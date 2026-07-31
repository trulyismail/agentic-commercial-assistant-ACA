"""
Comptes utilisateurs, mots de passe hachés et rôles (§15.1.6 de docs/ACAM_roadmap.md — première
vraie identité par personne du projet).

Avant ce module, les trois surfaces (Streamlit, dashboard, API) partageaient un unique secret
partagé (`ACA_UI_PASSWORD` / `DASHBOARD_PASSWORD` / `ACA_API_KEY`) : personne n'était identifié,
tout le monde pouvait tout faire, et le champ « Validé par » du journal d'audit était une simple
zone de texte libre que la personne remplissait elle-même (donc ni fiable ni opposable). Ce
registre remplace ce modèle par des comptes nominatifs :

- mot de passe **jamais stocké en clair** — PBKDF2-HMAC-SHA256, sel aléatoire par utilisateur,
  `PBKDF2_ITERATIONS` itérations, encodé dans un champ auto-descriptif
  (`pbkdf2_sha256$<itérations>$<sel>$<hash>`) pour qu'un durcissement futur du coût puisse
  cohabiter avec les hachages existants sans migration destructive ;
- comparaison à **temps constant** (`hmac.compare_digest`) et hachage factice quand le compte
  n'existe pas, pour que « utilisateur inconnu » et « mot de passe faux » prennent le même temps —
  sans quoi le temps de réponse énumère les comptes valides ;
- **rôles** (`admin` / `operator` / `viewer`) et permissions déclaratives (`ROLE_PERMISSIONS`) : un
  opérateur valide ou rejette des leads, un `viewer` ne fait que consulter (§18), un admin seul
  touche aux réglages, à la curation de la base de connaissances et aux comptes. C'est la séparation
  client/admin décrite en §12bis, jusqu'ici *conçue* mais jamais *appliquée* ;
- **second facteur TOTP** exigé des comptes `admin` (§18, cf. `TOTP_REQUIRED_ROLES` et
  [aca/core/totp.py](aca/core/totp.py)) : le mot de passe restait le facteur unique d'un compte
  capable de créer d'autres administrateurs et de rediriger les alertes commerciales.

Comme les quatre autres registres locaux, la table est cloisonnée par `org_id`
(cf. aca.core.tenant) et chaque fonction publique passe par `with_sqlite_retry` (deux process —
`ui.py` et `poller.py` — peuvent ouvrir le même fichier).

Dégradation gracieuse volontaire (même contrat que le reste du projet) : tant qu'**aucun** compte
n'existe, `ui.py` retombe sur l'ancien gate `ACA_UI_PASSWORD` en mode développeur. Créer le premier
compte bascule l'UI en mode authentifié :

    python -m aca.storage.user_store create <identifiant> --role admin
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime

from aca.core.tenant import current_org_id

from .sqlite_retry import with_sqlite_retry

DB_PATH = os.getenv("ACA_USERS_DB", "data/users.sqlite")

# ── Rôles et permissions ──────────────────────────────────────────────────────────────────────
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
# §18 — rôle de consultation. Il manquait un cas pourtant très courant : un directeur commercial, ou
# le client final, qui veut voir le tableau de bord et l'historique **sans** pouvoir écrire au CRM.
# Jusqu'ici il fallait lui attribuer `operator`, donc le droit de valider des leads — c'est-à-dire
# accorder l'écriture pour permettre la lecture. L'ajout tient en deux lignes : c'est exactement ce
# que la table déclarative `ROLE_PERMISSIONS` était censée rendre possible (§15.1.6).
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)

# Permissions déclaratives plutôt que des `if role == "admin"` disséminés dans `ui.py`/`api.py` :
# ajouter un rôle (ex. « lecteur seul ») devient une entrée de ce dict, pas une chasse aux
# conditions dans les surfaces.
PERM_VALIDATE_LEAD = "validate_lead"        # résoudre la pause humaine → écriture CRM
PERM_REJECT_LEAD = "reject_lead"
PERM_VIEW_DASHBOARD = "view_dashboard"
PERM_VIEW_HISTORY = "view_history"
PERM_CURATE_KNOWLEDGE = "curate_knowledge"  # ingestion de documents + validation des FAQ « à valider »
PERM_EDIT_SETTINGS = "edit_settings"        # onglet « Réglages » (routage, Calendly, relance…)
PERM_MANAGE_USERS = "manage_users"

ROLE_PERMISSIONS = {
    ROLE_ADMIN: {
        PERM_VALIDATE_LEAD, PERM_REJECT_LEAD, PERM_VIEW_DASHBOARD, PERM_VIEW_HISTORY,
        PERM_CURATE_KNOWLEDGE, PERM_EDIT_SETTINGS, PERM_MANAGE_USERS,
    },
    ROLE_OPERATOR: {
        PERM_VALIDATE_LEAD, PERM_REJECT_LEAD, PERM_VIEW_DASHBOARD, PERM_VIEW_HISTORY,
    },
    # Lecture seule stricte : aucune permission d'écriture, pas même le rejet d'un lead. Rejeter
    # paraît anodin mais retire l'analyse de la file de tout le monde — ce n'est pas de la
    # consultation.
    ROLE_VIEWER: {PERM_VIEW_DASHBOARD, PERM_VIEW_HISTORY},
}

# Rôles pour lesquels le second facteur est exigé (§18). Réservé à `admin` : un compte capable de
# créer d'autres administrateurs, de rediriger les alertes commerciales et de curer la base de
# connaissances que l'IA citera aux prospects justifie la contrainte. L'imposer à un opérateur qui
# valide vingt leads par jour se paierait en contournements (secret partagé, application
# d'authentification sur un unique téléphone de service) — cf. la docstring de `aca/core/totp.py`.
TOTP_REQUIRED_ROLES = frozenset({ROLE_ADMIN})


def can(role: str, permission: str) -> bool:
    """Le rôle `role` détient-il `permission` ? Un rôle inconnu n'a aucun droit (fail-closed)."""
    return permission in ROLE_PERMISSIONS.get(role, set())


# ── Hachage des mots de passe ─────────────────────────────────────────────────────────────────
HASH_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 240_000  # ~0,15 s par vérification sur une machine de bureau (2026)
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, salt: str = None, iterations: int = PBKDF2_ITERATIONS) -> str:
    """
    Encode `password` en `pbkdf2_sha256$<itérations>$<sel hex>$<hash hex>`.

    Le sel et le nombre d'itérations voyagent AVEC le hash : un futur relèvement du coût
    (matériel plus rapide) peut être appliqué aux nouveaux mots de passe sans invalider les
    anciens, `verify_password` relisant les paramètres de chaque enregistrement.
    """
    salt = salt or secrets.token_hex(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{HASH_ALGORITHM}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Compare à temps constant `password` au hash encodé. Format inconnu/corrompu ⇒ False."""
    try:
        algorithm, iterations, salt, _ = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if algorithm != HASH_ALGORITHM:
        return False
    try:
        candidate = hash_password(password, salt=salt, iterations=int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(candidate, encoded)


# Hash factice utilisé quand l'identifiant n'existe pas : sans lui, `verify_credentials` répondrait
# instantanément sur un compte inconnu et après ~0,15 s de PBKDF2 sur un compte réel — un attaquant
# distinguerait les identifiants valides au chronomètre, sans jamais deviner un mot de passe.
_DUMMY_HASH = hash_password("mot-de-passe-factice-jamais-valide")


class PasswordTooWeak(ValueError):
    """Levée par `create_user`/`set_password` sous `MIN_PASSWORD_LENGTH` caractères."""


class UserExists(ValueError):
    """Levée par `create_user` si l'identifiant est déjà pris pour ce tenant."""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "org_id TEXT NOT NULL, username TEXT NOT NULL, password_hash TEXT NOT NULL, "
        "role TEXT NOT NULL, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, "
        "totp_secret TEXT, PRIMARY KEY (org_id, username))"
    )
    # Migration idempotente pour les bases antérieures au §18 (même procédé que `org_id` puis
    # `prev_hash`/`row_hash` dans audit_log.py). Colonne nullable : un compte existant continue de
    # fonctionner sans second facteur, et l'activer reste une décision explicite.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "totp_secret" not in existing_cols:
        conn.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    conn.commit()
    return conn


def _row_to_user(row) -> dict:
    return {
        "username": row[0], "role": row[1], "created_at": row[2], "disabled": bool(row[3]),
        # Le secret lui-même ne sort JAMAIS d'ici : seul le fait qu'un second facteur soit actif est
        # exposé. `list_users()` alimente un tableau affiché à l'écran ; y faire transiter le secret
        # partagé serait exactement le genre de fuite discrète qu'un audit ne rattrape pas.
        "totp": bool(len(row) > 4 and row[4]),
    }


@with_sqlite_retry
def init_db() -> None:
    """Crée la table si nécessaire."""
    _connect().close()


@with_sqlite_retry
def create_user(username: str, password: str, role: str = ROLE_OPERATOR, org_id: str = None) -> dict:
    """Crée un compte pour le tenant courant. Lève `UserExists` / `PasswordTooWeak` / `ValueError`."""
    username = (username or "").strip()
    if not username:
        raise ValueError("Identifiant vide.")
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role!r} (attendu : {', '.join(ROLES)}).")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordTooWeak(f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum).")
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (org_id, username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (org_id or current_org_id(), username, hash_password(password), role,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
        except sqlite3.IntegrityError as exc:
            raise UserExists(f"L'identifiant {username!r} existe déjà.") from exc
        conn.commit()
    return {"username": username, "role": role, "disabled": False}


@with_sqlite_retry
def verify_credentials(username: str, password: str, org_id: str = None) -> dict:
    """
    Renvoie `{"username", "role"}` si le couple est valide et le compte actif, sinon `None`.

    Un compte inexistant ou désactivé fait quand même tourner PBKDF2 sur `_DUMMY_HASH` : le coût
    de la réponse ne doit pas révéler quels identifiants existent (cf. `_DUMMY_HASH`).
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT password_hash, role, disabled FROM users WHERE org_id = ? AND username = ?",
            (org_id or current_org_id(), (username or "").strip()),
        ).fetchone()
    if row is None or row[2]:
        verify_password(password or "", _DUMMY_HASH)
        return None
    if not verify_password(password or "", row[0]):
        return None
    return {"username": (username or "").strip(), "role": row[1]}


@with_sqlite_retry
def get_user(username: str, org_id: str = None) -> dict:
    """Compte du tenant courant (sans le hash), ou `None`."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT username, role, created_at, disabled, totp_secret FROM users "
            "WHERE org_id = ? AND username = ?",
            (org_id or current_org_id(), (username or "").strip()),
        ).fetchone()
    return _row_to_user(row) if row else None


@with_sqlite_retry
def list_users(org_id: str = None) -> list:
    """Tous les comptes du tenant courant (jamais le hash), par ordre alphabétique."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT username, role, created_at, disabled, totp_secret FROM users "
            "WHERE org_id = ? ORDER BY username",
            (org_id or current_org_id(),),
        ).fetchall()
    return [_row_to_user(row) for row in rows]


@with_sqlite_retry
def has_users(org_id: str = None) -> bool:
    """
    Un compte actif existe-t-il pour ce tenant ? `ui.py` s'en sert pour choisir son mode
    d'authentification : aucun compte ⇒ ancien gate `ACA_UI_PASSWORD` (développement), au moins un
    ⇒ connexion nominative obligatoire.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE org_id = ? AND disabled = 0 LIMIT 1",
            (org_id or current_org_id(),),
        ).fetchone()
    return row is not None


@with_sqlite_retry
def set_password(username: str, password: str, org_id: str = None) -> bool:
    """Change le mot de passe. `False` si le compte n'existe pas ; lève `PasswordTooWeak`."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise PasswordTooWeak(f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum).")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET password_hash = ? WHERE org_id = ? AND username = ?",
            (hash_password(password), org_id or current_org_id(), (username or "").strip()),
        )
        conn.commit()
    return cursor.rowcount > 0


@with_sqlite_retry
def set_role(username: str, role: str, org_id: str = None) -> bool:
    """Change le rôle. `False` si le compte n'existe pas ; lève `ValueError` sur un rôle inconnu."""
    if role not in ROLES:
        raise ValueError(f"Rôle inconnu : {role!r} (attendu : {', '.join(ROLES)}).")
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET role = ? WHERE org_id = ? AND username = ?",
            (role, org_id or current_org_id(), (username or "").strip()),
        )
        conn.commit()
    return cursor.rowcount > 0


@with_sqlite_retry
def set_disabled(username: str, disabled: bool, org_id: str = None) -> bool:
    """
    Désactive (ou réactive) un compte. Préféré à une suppression : le journal d'audit référence
    l'identifiant, l'effacer rendrait des validations passées non attribuables.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET disabled = ? WHERE org_id = ? AND username = ?",
            (1 if disabled else 0, org_id or current_org_id(), (username or "").strip()),
        )
        conn.commit()
    return cursor.rowcount > 0


# ── Second facteur (§18) ──────────────────────────────────────────────────────────────────────
def totp_required(role: str) -> bool:
    """Ce rôle doit-il présenter un second facteur ? (cf. `TOTP_REQUIRED_ROLES`)"""
    return role in TOTP_REQUIRED_ROLES


@with_sqlite_retry
def set_totp_secret(username: str, secret: str, org_id: str = None) -> bool:
    """
    Active (secret non vide) ou désactive (`None`/`""`) le second facteur d'un compte.

    Le secret est stocké **en clair**, et c'est inévitable : contrairement à un mot de passe, il doit
    être relu pour recalculer le code attendu à chaque connexion — un secret TOTP haché serait
    inutilisable. La protection réelle est donc celle du fichier `users.sqlite` lui-même (chiffrement
    disque de la machine, permissions du système), ce qui est énoncé dans
    `docs/DEPLOYMENT_HARDENING.md` plutôt que masqué derrière un faux sentiment de sécurité.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE users SET totp_secret = ? WHERE org_id = ? AND username = ?",
            (secret or None, org_id or current_org_id(), (username or "").strip()),
        )
        conn.commit()
    return cursor.rowcount > 0


@with_sqlite_retry
def get_totp_secret(username: str, org_id: str = None) -> str:
    """Secret TOTP du compte, ou `None`. Réservé au chemin de vérification — jamais affiché."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM users WHERE org_id = ? AND username = ?",
            (org_id or current_org_id(), (username or "").strip()),
        ).fetchone()
    return row[0] if row and row[0] else None


def has_totp(username: str, org_id: str = None) -> bool:
    """Un second facteur est-il configuré pour ce compte ?"""
    return get_totp_secret(username, org_id) is not None


def verify_totp(username: str, code: str, org_id: str = None, now: float = None) -> bool:
    """
    Vérifie un code à six chiffres pour ce compte.

    Import différé de `aca.core.totp` : ce module de stockage est importé par `prod_check.py` au
    démarrage, et n'a pas besoin d'entraîner la couche cryptographique tant que personne ne se
    connecte. Un compte sans second facteur configuré renvoie `False` — l'appelant doit vérifier
    `has_totp()` d'abord et décider si l'absence de facteur bloque (rôle `admin`) ou non.
    """
    from aca.core import totp as totp_module

    secret = get_totp_secret(username, org_id)
    if not secret:
        return False
    return totp_module.verify(secret, code, now=now)


def _cli() -> None:
    """`python -m aca.storage.user_store <create|list|passwd|role|disable|enable|totp-off> …`"""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Gestion des comptes ACA (§15.1.6).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    creator = subparsers.add_parser("create", help="créer un compte")
    creator.add_argument("username")
    creator.add_argument("--role", default=ROLE_OPERATOR, choices=list(ROLES))

    subparsers.add_parser("list", help="lister les comptes du tenant courant")

    passwd = subparsers.add_parser("passwd", help="changer un mot de passe")
    passwd.add_argument("username")

    role_parser = subparsers.add_parser("role", help="changer le rôle d'un compte")
    role_parser.add_argument("username")
    role_parser.add_argument("role", choices=list(ROLES))

    for name, flag in (("disable", True), ("enable", False)):
        sub = subparsers.add_parser(name, help=f"{'dés' if flag else 'ré'}activer un compte")
        sub.add_argument("username")

    # §18 — la seule voie de secours d'un second facteur : quelqu'un ayant accès à la machine
    # débloque un administrateur qui a perdu son téléphone. Volontairement hors interface : un
    # bouton « désactiver la double authentification » dans l'UI serait la porte dérobée que le
    # second facteur est censé fermer.
    totp_off = subparsers.add_parser(
        "totp-off", help="désactiver le second facteur d'un compte (perte de téléphone)")
    totp_off.add_argument("username")

    args = parser.parse_args()

    if args.command == "list":
        users = list_users()
        if not users:
            print("Aucun compte — l'UI utilise encore ACA_UI_PASSWORD (mode developpement).")
        for user in users:
            state = " (desactive)" if user["disabled"] else ""
            second = " [2FA]" if user["totp"] else ""
            print(f"{user['username']:<24} {user['role']:<10} cree le "
                  f"{user['created_at']}{state}{second}")
        return

    if args.command == "totp-off":
        print("Second facteur desactive." if set_totp_secret(args.username, None)
              else "Compte introuvable.")
        return

    if args.command in ("create", "passwd"):
        password = getpass.getpass("Mot de passe : ")
        if password != getpass.getpass("Confirmation : "):
            raise SystemExit("Les deux saisies different.")
        if args.command == "create":
            create_user(args.username, password, role=args.role)
            print(f"Compte '{args.username}' cree avec le role '{args.role}'.")
        else:
            print("Mot de passe mis a jour." if set_password(args.username, password)
                  else "Compte introuvable.")
        return

    if args.command == "role":
        print("Role mis a jour." if set_role(args.username, args.role) else "Compte introuvable.")
        return

    disabled = args.command == "disable"
    if set_disabled(args.username, disabled):
        print(f"Compte {'desactive' if disabled else 'reactive'}.")
    else:
        print("Compte introuvable.")


if __name__ == "__main__":
    _cli()

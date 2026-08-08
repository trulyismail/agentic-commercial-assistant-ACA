"""
Appareils autorisés à sauter le second facteur (§24) — `data/device_trust.sqlite`.

Registre distinct de ses voisins, et la distinction est sa raison d'être (même raisonnement qui a
séparé `task_store` de `followup_store`/`schedule_store` au §19) : `user_store` porte **une ligne par
personne** et rien qui expire ; `session.py` gouverne la durée d'une session **déjà ouverte** ; ici
on stocke **une ligne par navigateur**, datée, expirante, révocable individuellement, et dont la
question n'est ni « qui es-tu » ni « ta session vit-elle encore » mais « faut-il redemander le code
sur ce poste ». Trois cycles de vie différents, trois purges différentes.

Ce qui est écrit n'est jamais le jeton, seulement son empreinte : tout le raisonnement de sécurité
est dans l'en-tête de `aca/core/device_trust.py`. Ce fichier n'est que la persistance.

`sqlite_retry` comme les autres magasins : ces écritures ont lieu hors du graphe, donc hors du
`RETRY_POLICY` de `app.py`, et l'interface peut ouvrir le fichier pendant qu'un autre processus le
lit.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from aca.core import device_trust
from aca.core.tenant import current_org_id
from aca.storage.sqlite_retry import with_sqlite_retry

DB_PATH = os.getenv("ACA_DEVICE_TRUST_DB", "data/device_trust.sqlite")


def _connect() -> sqlite3.Connection:
    # Chemin relu à chaque connexion (et non figé à l'import) : c'est ce qui permet aux tests de
    # rediriger la base vers un dossier temporaire, comme pour les autres magasins.
    path = Path(os.getenv("ACA_DEVICE_TRUST_DB", DB_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trusted_devices ("
        "org_id TEXT NOT NULL, username TEXT NOT NULL, token_hash TEXT NOT NULL, "
        "auth_fingerprint TEXT NOT NULL, ua_hash TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', "
        # Horodatage stocké deux fois, exprès et pour la même raison que `schedule_store` :
        # l'epoch (REAL) pour l'arithmétique d'expiration, sans question de fuseau ni de format ;
        # le texte ISO pour qu'un humain qui ouvre le fichier comprenne ce qu'il regarde.
        "created_at TEXT NOT NULL, created_epoch REAL NOT NULL, expires_at REAL NOT NULL, "
        "last_used_at TEXT, "
        "PRIMARY KEY (org_id, token_hash))"
    )
    conn.commit()
    return conn


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


@with_sqlite_retry
def remember(
    username: str,
    token: str,
    auth_fingerprint: str,
    user_agent: str = "",
    label: str = "",
    now: float = None,
    org_id: str = None,
) -> float:
    """
    Mémorise ce navigateur pour `username` ; renvoie la date d'expiration (epoch).

    L'appelant transmet le jeton en clair parce qu'il doit le déposer dans le cookie ; ce magasin
    n'en conserve que l'empreinte.
    """
    moment = time.time() if now is None else float(now)
    expiry = device_trust.expires_at(moment)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trusted_devices (org_id, username, token_hash, "
            "auth_fingerprint, ua_hash, label, created_at, created_epoch, expires_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                org_id or current_org_id(),
                (username or "").strip(),
                device_trust.token_hash(token),
                auth_fingerprint or "",
                device_trust.user_agent_hash(user_agent),
                (label or "")[:120],
                _now_text(),
                moment,
                expiry,
                None,
            ),
        )
        conn.commit()
    return expiry


@with_sqlite_retry
def verify(
    username: str,
    token: str,
    auth_fingerprint: str,
    user_agent: str = "",
    now: float = None,
    org_id: str = None,
) -> bool:
    """
    Ce navigateur peut-il sauter le second facteur pour ce compte ?

    Quatre conditions, toutes nécessaires : l'empreinte du jeton existe **pour ce compte** (un
    jeton émis pour A ne vaut rien pour B), l'empreinte d'authentification correspond toujours
    (sinon le mot de passe ou le secret TOTP a changé depuis), le navigateur est le même, et la
    date n'est pas dépassée — expiration jugée ici, jamais d'après le cookie.

    Aucune de ces quatre n'échoue bruyamment : un refus se contente de **redemander le code**. Il
    n'y a donc rien à annoncer à l'utilisateur, et surtout aucun compteur d'échec à incrémenter —
    un cookie périmé n'est pas une tentative d'intrusion, et les confondre verrouillerait des gens
    parfaitement légitimes.
    """
    moment = time.time() if now is None else float(now)
    tenant = org_id or current_org_id()
    digest = device_trust.token_hash(token)
    with _connect() as conn:
        row = conn.execute(
            "SELECT auth_fingerprint, ua_hash, expires_at FROM trusted_devices "
            "WHERE org_id = ? AND username = ? AND token_hash = ?",
            (tenant, (username or "").strip(), digest),
        ).fetchone()
        if row is None:
            return False
        stored_auth, stored_ua, expiry = row
        if device_trust.is_expired(expiry, moment):
            # Ménage opportuniste : la table est minuscule (quelques postes par personne) et une
            # ligne périmée n'a plus de valeur, ni fonctionnelle ni de traçabilité — le journal
            # d'activité, lui, garde la trace des usages passés.
            conn.execute(
                "DELETE FROM trusted_devices WHERE org_id = ? AND token_hash = ?", (tenant, digest),
            )
            conn.commit()
            return False
        if stored_auth != (auth_fingerprint or ""):
            return False
        if stored_ua != device_trust.user_agent_hash(user_agent):
            return False
        conn.execute(
            "UPDATE trusted_devices SET last_used_at = ? WHERE org_id = ? AND token_hash = ?",
            (_now_text(), tenant, digest),
        )
        conn.commit()
    return True


@with_sqlite_retry
def list_devices(username: str, org_id: str = None) -> list:
    """
    Appareils mémorisés pour ce compte, du plus récent au plus ancien.

    Jamais l'empreinte complète : ce qui s'affiche doit aider à reconnaître un poste, pas fournir
    de quoi en rejouer un.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT label, created_at, expires_at, last_used_at, substr(token_hash, 1, 8) "
            "FROM trusted_devices WHERE org_id = ? AND username = ? ORDER BY created_epoch DESC",
            (org_id or current_org_id(), (username or "").strip()),
        ).fetchall()
    return [
        {"label": r[0], "created_at": r[1], "expires_at": r[2], "last_used_at": r[3], "ref": r[4]}
        for r in rows
    ]


@with_sqlite_retry
def revoke_token(token: str, org_id: str = None) -> bool:
    """Oublie CE navigateur (case « oublier cet appareil » à la déconnexion)."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM trusted_devices WHERE org_id = ? AND token_hash = ?",
            (org_id or current_org_id(), device_trust.token_hash(token)),
        )
        conn.commit()
    return cursor.rowcount > 0


@with_sqlite_retry
def revoke_all(username: str, org_id: str = None) -> int:
    """
    Oublie tous les appareils de ce compte ; renvoie leur nombre.

    C'est le geste à faire depuis un poste qu'on a encore, quand on en a perdu un autre. Changer de
    mot de passe produit le même effet **automatiquement** (cf. `device_trust.auth_fingerprint`) :
    ce bouton existe pour le cas où l'on ne veut pas, ou pas tout de suite, en changer.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM trusted_devices WHERE org_id = ? AND username = ?",
            (org_id or current_org_id(), (username or "").strip()),
        )
        conn.commit()
    return cursor.rowcount


@with_sqlite_retry
def purge_expired(now: float = None, org_id: str = None) -> int:
    """Supprime les autorisations périmées (branché sur la purge périodique de `retention.py`)."""
    moment = time.time() if now is None else float(now)
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM trusted_devices WHERE org_id = ? AND expires_at <= ?",
            (org_id or current_org_id(), moment),
        )
        conn.commit()
    return cursor.rowcount

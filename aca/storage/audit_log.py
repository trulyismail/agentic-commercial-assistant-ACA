"""
Journal d'audit (P1 §11.4 item 10, durci §15.2.7) : qui a validé quel lead, et quand.

Depuis §15.1.6, l'identité vient de la session authentifiée (`aca/storage/user_store.py`) et non
plus d'un nom saisi librement dans la sidebar — le champ libre ne subsiste qu'en mode
développement/secret partagé, où personne n'est identifié de toute façon.

**Chaînage par hachage (§15.2.7).** L'audit relevait : « aujourd'hui une simple table SQLite
modifiable ». Chaque ligne porte désormais l'empreinte de la précédente (`prev_hash`) dans le
calcul de la sienne (`row_hash`), par tenant. Modifier ou supprimer discrètement une ligne ancienne
casse toutes les empreintes suivantes, ce que `verify_chain()` détecte et localise.

Deux limites énoncées franchement, parce qu'elles déterminent ce que cette protection vaut :

1. C'est **tamper-evident**, pas tamper-proof. Qui peut écrire dans le fichier peut aussi
   recalculer toute la chaîne — *sauf* si `ACA_AUDIT_HMAC_KEY` est réglée : les empreintes sont
   alors des HMAC, et forger une chaîne cohérente exige la clé, qui vit hors de la base (variable
   d'environnement, idéalement un coffre — cf. docs/DEPLOYMENT_HARDENING.md). Sans clé, le chaînage
   ne protège que de la modification *négligente* ou partielle.
2. Une vraie inviolabilité demanderait un stockage append-only (WORM) ou un ancrage externe
   (empreinte publiée périodiquement chez un tiers). Hors périmètre à ce volume, noté comme tel.

Les lignes écrites avant cette migration n'ont pas d'empreinte : `verify_chain()` les compte comme
« héritées, non chaînées » et ne les signale jamais comme falsifiées — un journal antérieur au
mécanisme n'est pas une preuve de fraude.
"""
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_AUDIT_DB", "data/audit.sqlite")

# Séparateur d'unité ASCII (0x1F) : jamais présent dans une adresse, un nom ou une classification,
# donc deux lignes différentes ne peuvent pas produire la même chaîne canonique par concaténation
# ambiguë (« ab | c » vs « a | bc »).
_FIELD_SEPARATOR = "\x1f"


def _digest(payload: str) -> str:
    """
    Empreinte d'une ligne : HMAC-SHA256 si `ACA_AUDIT_HMAC_KEY` est réglée, SHA-256 sinon.

    Clé lue dynamiquement (jamais figée à l'import — même leçon que `DATABASE_URL`/`ACA_ORG_ID`
    ailleurs dans le projet) : elle peut être injectée par un `load_dotenv()` postérieur au premier
    import de ce module.
    """
    key = os.getenv("ACA_AUDIT_HMAC_KEY")
    if key:
        return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_hash(prev_hash: str, thread_id: str, validated_by: str, classification: str,
              sender: str, validated_at: str, org_id: str) -> str:
    """Empreinte chaînée d'une ligne : dépend de son contenu ET de l'empreinte de la précédente."""
    payload = _FIELD_SEPARATOR.join([
        prev_hash or "", thread_id or "", validated_by or "", classification or "",
        sender or "", validated_at or "", org_id or "",
    ])
    return _digest(payload)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, validated_by TEXT, "
        "classification TEXT, sender TEXT, validated_at TEXT NOT NULL, "
        "org_id TEXT NOT NULL DEFAULT 'default', prev_hash TEXT, row_hash TEXT)"
    )
    # Migrations idempotentes : `org_id` (fondation multi-tenant, §12 item 3 / §14.3), puis
    # `prev_hash`/`row_hash` (chaînage tamper-evident, §15.2.7). Colonnes de hachage nullables :
    # les lignes antérieures restent lisibles, simplement non chaînées.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit)").fetchall()}
    if "org_id" not in existing_cols:
        conn.execute("ALTER TABLE audit ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
    if "prev_hash" not in existing_cols:
        conn.execute("ALTER TABLE audit ADD COLUMN prev_hash TEXT")
    if "row_hash" not in existing_cols:
        conn.execute("ALTER TABLE audit ADD COLUMN row_hash TEXT")
    conn.commit()
    return conn


def _last_hash(conn: sqlite3.Connection, org_id: str) -> str:
    """Empreinte de la dernière ligne chaînée de ce tenant ("" si la chaîne démarre)."""
    row = conn.execute(
        "SELECT row_hash FROM audit WHERE org_id = ? AND row_hash IS NOT NULL "
        "ORDER BY id DESC LIMIT 1", (org_id,),
    ).fetchone()
    return row[0] if row else ""


@with_sqlite_retry
def log_validation(thread_id: str, validated_by: str, classification: str, sender: str, org_id: str = None) -> None:
    """Enregistre un événement de validation humaine, chaîné à la ligne précédente (§15.2.7)."""
    org = org_id or current_org_id()
    validated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    author = validated_by or "(non renseigné)"
    with _connect() as conn:
        prev_hash = _last_hash(conn, org)
        conn.execute(
            "INSERT INTO audit (thread_id, validated_by, classification, sender, validated_at, "
            "org_id, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, author, classification, sender, validated_at, org, prev_hash,
             _row_hash(prev_hash, thread_id, author, classification, sender, validated_at, org)),
        )
        conn.commit()


@with_sqlite_retry
def verify_chain(org_id: str = None) -> dict:
    """
    Recalcule la chaîne d'empreintes du tenant courant et signale la première rupture.

    Renvoie `{"ok", "checked", "legacy_unchained", "first_invalid_id", "detail"}`. `ok` est vrai si
    aucune ligne chaînée n'a été altérée ; les lignes héritées sans empreinte sont comptées à part
    (`legacy_unchained`) et n'invalident rien — elles précèdent le mécanisme.
    """
    org = org_id or current_org_id()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, thread_id, validated_by, classification, sender, validated_at, "
            "prev_hash, row_hash FROM audit WHERE org_id = ? ORDER BY id", (org,),
        ).fetchall()

    legacy = 0
    checked = 0
    expected_prev = ""
    for (row_id, thread_id, validated_by, classification, sender, validated_at,
         prev_hash, row_hash) in rows:
        if row_hash is None:
            legacy += 1
            continue
        expected = _row_hash(
            prev_hash or "", thread_id, validated_by, classification, sender, validated_at, org,
        )
        # Deux contrôles distincts : l'empreinte doit correspondre au contenu de SA ligne, et le
        # `prev_hash` déclaré doit correspondre à la ligne réellement précédente. Sans le second,
        # supprimer une ligne du milieu passerait inaperçu (chaque ligne restante resterait
        # individuellement cohérente).
        if row_hash != expected or (prev_hash or "") != expected_prev:
            return {
                "ok": False, "checked": checked, "legacy_unchained": legacy,
                "first_invalid_id": row_id,
                "detail": f"Rupture de chaîne à la ligne {row_id} : contenu modifié, ligne "
                          "supprimée avant celle-ci, ou empreintes recalculées sans la clé HMAC.",
            }
        expected_prev = row_hash
        checked += 1

    return {
        "ok": True, "checked": checked, "legacy_unchained": legacy, "first_invalid_id": None,
        "detail": f"{checked} ligne(s) vérifiée(s), {legacy} ligne(s) héritée(s) non chaînée(s).",
    }


@with_sqlite_retry
def list_recent(limit: int = 20, org_id: str = None) -> list:
    """Derniers événements de validation du tenant courant, les plus récents d'abord."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, validated_by, classification, sender, validated_at FROM audit "
            "WHERE org_id = ? ORDER BY validated_at DESC LIMIT ?", (org_id or current_org_id(), limit)
        ).fetchall()
    return [
        {"thread_id": r[0], "validated_by": r[1], "classification": r[2], "sender": r[3], "validated_at": r[4]}
        for r in rows
    ]


def _main() -> None:
    """`python -m aca.storage.audit_log` — vérifie l'intégrité du journal du tenant courant."""
    result = verify_chain()
    print(result["detail"])
    if not os.getenv("ACA_AUDIT_HMAC_KEY"):
        print("Note : ACA_AUDIT_HMAC_KEY non definie - chainage SHA-256 simple, recalculable par "
              "qui peut ecrire dans la base (cf. docstring du module).")
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()

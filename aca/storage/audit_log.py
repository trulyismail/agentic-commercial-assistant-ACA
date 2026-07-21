"""
Journal d'audit minimal (P1 §11.4 item 10) : qui a validé quel lead, et quand.

Ce n'est PAS un vrai système d'authentification multi-utilisateurs — juste un nom déclaré par la
personne qui valide (saisi une fois dans la sidebar Streamlit), enregistré localement à chaque clic
sur « Valider ». Le gate mot de passe optionnel (`ACA_UI_PASSWORD`, voir ui.py) protège l'accès à
l'UI ; ce module ne fait que tracer qui a pris la décision, pour un usage solo/petite équipe.
"""
import os
import sqlite3
from datetime import datetime
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_AUDIT_DB", "data/audit.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, validated_by TEXT, "
        "classification TEXT, sender TEXT, validated_at TEXT NOT NULL, "
        "org_id TEXT NOT NULL DEFAULT 'default')"
    )
    # Migration idempotente (fondation multi-tenant, §12 item 3 / §14.3).
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit)").fetchall()}
    if "org_id" not in existing_cols:
        conn.execute("ALTER TABLE audit ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
    conn.commit()
    return conn


@with_sqlite_retry
def log_validation(thread_id: str, validated_by: str, classification: str, sender: str, org_id: str = None) -> None:
    """Enregistre un événement de validation humaine (traçabilité minimale)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit (thread_id, validated_by, classification, sender, validated_at, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, validated_by or "(non renseigné)", classification, sender,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), org_id or current_org_id()),
        )
        conn.commit()


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

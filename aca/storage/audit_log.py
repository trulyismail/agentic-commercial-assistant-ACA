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

DB_PATH = os.getenv("ACA_AUDIT_DB", "data/audit.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, validated_by TEXT, "
        "classification TEXT, sender TEXT, validated_at TEXT NOT NULL)"
    )
    return conn


def log_validation(thread_id: str, validated_by: str, classification: str, sender: str) -> None:
    """Enregistre un événement de validation humaine (traçabilité minimale)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit (thread_id, validated_by, classification, sender, validated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, validated_by or "(non renseigné)", classification, sender,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def list_recent(limit: int = 20) -> list:
    """Derniers événements de validation, les plus récents d'abord."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, validated_by, classification, sender, validated_at FROM audit "
            "ORDER BY validated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"thread_id": r[0], "validated_by": r[1], "classification": r[2], "sender": r[3], "validated_at": r[4]}
        for r in rows
    ]

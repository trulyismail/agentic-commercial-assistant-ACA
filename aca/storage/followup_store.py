"""
Suivi des relances automatiques (P1 §11.4 item 7) : registre local (SQLite, `followup.sqlite`) des
leads validés provenant de Gmail, pour que `relance.py` puisse vérifier périodiquement si le
prospect a répondu et, sinon, préparer un brouillon de relance après `RELANCE_DAYS`.

Un lead n'est suivi ici que s'il a un `gmail_thread_id` connu (source Gmail) — les saisies
manuelles sans e-mail source n'ont pas de fil à relancer automatiquement.
"""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.getenv("ACA_FOLLOWUP_DB", "data/followup.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS followup ("
        "thread_id TEXT PRIMARY KEY, gmail_thread_id TEXT NOT NULL, sender TEXT, subject TEXT, "
        "validated_at TEXT NOT NULL, followup_sent INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def init_db() -> None:
    """Crée la table si nécessaire (appelé au démarrage de relance.py)."""
    _connect().close()


def track(thread_id: str, gmail_thread_id: str, sender: str, subject: str) -> None:
    """Enregistre un lead validé venant de Gmail pour suivi de relance. No-op si pas de fil Gmail."""
    if not gmail_thread_id:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO followup (thread_id, gmail_thread_id, sender, subject, validated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, gmail_thread_id, sender, subject, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def list_active() -> list:
    """Leads suivis n'ayant pas encore reçu de relance."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, gmail_thread_id, sender, subject FROM followup WHERE followup_sent = 0"
        ).fetchall()
    return [{"thread_id": r[0], "gmail_thread_id": r[1], "sender": r[2], "subject": r[3]} for r in rows]


def mark_followed_up(thread_id: str) -> None:
    """Marque un lead comme relancé (une seule relance par lead dans cette version — pas de cadence multi-round)."""
    with _connect() as conn:
        conn.execute("UPDATE followup SET followup_sent = 1 WHERE thread_id = ?", (thread_id,))
        conn.commit()

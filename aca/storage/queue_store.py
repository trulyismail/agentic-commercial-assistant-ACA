"""
Registre local (SQLite) de la file d'attente d'e-mails traités automatiquement par le poller
(poller.py) et en attente de validation humaine dans l'UI Streamlit.

Ne stocke que des métadonnées légères (expéditeur/objet/thread_id) — l'état complet de l'analyse
reste dans le checkpointer LangGraph (`checkpoints.sqlite`) ; ce fichier sert uniquement à :
- savoir QUELS e-mails ont déjà été mis en file, pour ne pas les retraiter à chaque cycle de polling
  (ils restent `UNREAD` côté Gmail jusqu'à la validation humaine, donc `list_unread_emails` les
  renverrait sans arrêt sinon) ;
- lister les analyses en attente dans la sidebar Streamlit.
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.getenv("ACA_QUEUE_DB", "data/queue.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS queue ("
        "message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, sender TEXT, subject TEXT, "
        "status TEXT NOT NULL DEFAULT 'en_attente', created_at TEXT NOT NULL)"
    )
    return conn


def init_db() -> None:
    """Crée la table si nécessaire (appelé au démarrage du poller)."""
    _connect().close()


def is_known(message_id: str) -> bool:
    """True si ce message Gmail a déjà été mis en file (quel que soit son statut)."""
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM queue WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None


def enqueue(message_id: str, thread_id: str, sender: str, subject: str) -> None:
    """
    Marque un e-mail comme « en_cours » — appelé AVANT `app.invoke()`, pas après, pour qu'un crash
    du poller pendant le traitement n'entraîne pas un retraitement en double au prochain cycle
    (`is_known()` renvoie déjà True dès cet instant). `mark_ready()` bascule vers `en_attente` une
    fois le graphe arrivé à la pause sans erreur ; `reset_stale()` récupère les entrées bloquées.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO queue (message_id, thread_id, sender, subject, status, created_at) "
            "VALUES (?, ?, ?, ?, 'en_cours', ?)",
            (message_id, thread_id, sender, subject, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def mark_ready(message_id: str) -> None:
    """Bascule une entrée de « en_cours » à « en_attente » : le graphe a atteint la pause sans erreur."""
    with _connect() as conn:
        conn.execute(
            "UPDATE queue SET status = 'en_attente' WHERE message_id = ? AND status = 'en_cours'",
            (message_id,),
        )
        conn.commit()


def reset_stale(older_than_minutes: int = 15) -> int:
    """
    Supprime les entrées bloquées en « en_cours » depuis plus de `older_than_minutes` (poller
    interrompu — process tué, panne — avant d'avoir atteint `mark_ready`) pour qu'elles soient
    retraitées au prochain cycle plutôt que perdues indéfiniment. Renvoie le nombre supprimé.
    """
    cutoff = (datetime.now() - timedelta(minutes=older_than_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute("DELETE FROM queue WHERE status = 'en_cours' AND created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount


def list_pending() -> list:
    """Analyses en attente de validation humaine, les plus anciennes d'abord."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, sender, subject, created_at FROM queue "
            "WHERE status = 'en_attente' ORDER BY created_at"
        ).fetchall()
    return [{"thread_id": r[0], "sender": r[1], "subject": r[2], "created_at": r[3]} for r in rows]


def mark_validated(thread_id: str) -> None:
    """Retire une entrée de la file après validation humaine (« Valider » dans l'UI)."""
    with _connect() as conn:
        conn.execute("UPDATE queue SET status = 'validé' WHERE thread_id = ?", (thread_id,))
        conn.commit()


def list_validated_older_than(days: int) -> list:
    """Thread IDs validés depuis plus de `days` jours (RGPD — cf. retention.py)."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id FROM queue WHERE status = 'validé' AND created_at < ?", (cutoff,)
        ).fetchall()
    return [r[0] for r in rows]


def purge_validated_older_than(days: int) -> int:
    """Supprime les entrées validées depuis plus de `days` jours (RGPD). Renvoie le nombre supprimé."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute("DELETE FROM queue WHERE status = 'validé' AND created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount

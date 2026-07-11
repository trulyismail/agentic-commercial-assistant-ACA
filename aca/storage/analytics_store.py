"""
Journal léger de TOUTES les classifications (P2 §11.4 item 16, volet tableau de bord).

Contrairement à l'onglet Sheets `Leads` (qui ne reçoit que les DEMANDE_DEMO/DEVIS validés) et à
`audit_log.py` (qui ne trace que les événements de validation), ce registre local capture CHAQUE
e-mail classé — y compris SPAM/AUTRE/SUPPORT, qui ne sont jamais validés — pour que le tableau de
bord affiche un volume par catégorie complet et un vrai temps de réponse (classification → clic
« Valider »), quelle que soit la source (saisie manuelle, import Gmail ponctuel, ou poller.py).
"""
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.getenv("ACA_ANALYTICS_DB", "data/analytics.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "thread_id TEXT PRIMARY KEY, classification TEXT NOT NULL, sender TEXT, source TEXT, "
        "has_draft INTEGER NOT NULL DEFAULT 0, classified_at TEXT NOT NULL, validated_at TEXT)"
    )
    return conn


def record_classification(thread_id: str, classification: str, sender: str, source: str) -> None:
    """
    Enregistre l'événement de classification, une seule fois par thread (`INSERT OR IGNORE`).
    Idempotent par design : appelé à chaque resynchronisation de l'état (y compris après une
    clarification résolue), donc rejouable sans créer de doublon. `source` ∈
    {"manuel", "gmail_import", "poller"}.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events (thread_id, classification, sender, source, classified_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, classification, sender, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def record_draft_ready(thread_id: str) -> None:
    """
    Marque qu'une proposition a été rédigée pour ce thread. Appel séparé de `record_classification`
    (plutôt qu'un seul INSERT) car la classification est connue AVANT la proposition (le Stratège
    peut tourner après une clarification qui a déjà mis la première ligne en base) — un simple
    `INSERT OR IGNORE` figerait `has_draft=0` pour toujours si on l'y intégrait directement.
    """
    with _connect() as conn:
        conn.execute("UPDATE events SET has_draft = 1 WHERE thread_id = ?", (thread_id,))
        conn.commit()


def record_validation(thread_id: str) -> None:
    """Renseigne `validated_at` pour un thread déjà classé (appelé au clic « Valider » dans l'UI)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE events SET validated_at = ? WHERE thread_id = ? AND validated_at IS NULL",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), thread_id),
        )
        conn.commit()


def _cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def volume_by_category(days: int = 30) -> list[dict]:
    """Nombre d'e-mails classés par catégorie sur les `days` derniers jours, décroissant."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT classification, COUNT(*) FROM events WHERE classified_at >= ? "
            "GROUP BY classification ORDER BY COUNT(*) DESC",
            (_cutoff(days),),
        ).fetchall()
    return [{"classification": r[0], "count": r[1]} for r in rows]


def daily_volume(days: int = 30) -> list[dict]:
    """Volume quotidien total (toutes catégories confondues), pour un graphe de tendance."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT substr(classified_at, 1, 10) AS jour, COUNT(*) FROM events "
            "WHERE classified_at >= ? GROUP BY jour ORDER BY jour",
            (_cutoff(days),),
        ).fetchall()
    return [{"jour": r[0], "count": r[1]} for r in rows]


def response_times(days: int = 30) -> list[dict]:
    """
    Durée (en minutes) entre classification et validation, pour chaque lead validé sur la période —
    matière première du graphe de latence (répondre < 1h vs > 24h, cf. ACAM_roadmap.md §11.4).
    Ignore les threads jamais validés (SPAM/AUTRE/SUPPORT routés, ou encore en attente).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, classification, classified_at, validated_at FROM events "
            "WHERE validated_at IS NOT NULL AND classified_at >= ?",
            (_cutoff(days),),
        ).fetchall()
    results = []
    for thread_id, classification, classified_at, validated_at in rows:
        delta = (
            datetime.strptime(validated_at, "%Y-%m-%d %H:%M:%S")
            - datetime.strptime(classified_at, "%Y-%m-%d %H:%M:%S")
        )
        results.append({
            "thread_id": thread_id,
            "classification": classification,
            "minutes": round(delta.total_seconds() / 60, 1),
        })
    return results


def funnel_counts(days: int = 30) -> dict:
    """Compte classé → proposition rédigée → validé, sur les `days` derniers jours."""
    with _connect() as conn:
        cutoff = _cutoff(days)
        classified = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ?", (cutoff,)
        ).fetchone()[0]
        drafted = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND has_draft = 1", (cutoff,)
        ).fetchone()[0]
        validated = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND validated_at IS NOT NULL", (cutoff,)
        ).fetchone()[0]
    return {"classifiés": classified, "proposition rédigée": drafted, "validés": validated}

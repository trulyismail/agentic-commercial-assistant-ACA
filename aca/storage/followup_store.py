"""
Suivi des relances automatiques (P1 §11.4 item 7, cadence multi-round ajoutée §11.6 item 5) :
registre local (SQLite, `followup.sqlite`) des leads validés provenant de Gmail, pour que
`relance.py` puisse vérifier périodiquement si le prospect a répondu et, sinon, préparer un
brouillon de relance après `RELANCE_DAYS`, jusqu'à `RELANCE_MAX_ROUNDS` relances (~80% des ventes
demandent 5+ contacts au total, cf. docs/ACAM_roadmap.md §11.6 item 5 — la cadence s'arrête dès que
le prospect répond, `relance.check_one` ne relance jamais si le dernier message du fil vient de lui).

Un lead n'est suivi ici que s'il a un `gmail_thread_id` connu (source Gmail) — les saisies
manuelles sans e-mail source n'ont pas de fil à relancer automatiquement.
"""
import os
import sqlite3
from datetime import datetime
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_FOLLOWUP_DB", "data/followup.sqlite")
RELANCE_MAX_ROUNDS = int(os.getenv("RELANCE_MAX_ROUNDS", "3"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS followup ("
        "thread_id TEXT PRIMARY KEY, gmail_thread_id TEXT NOT NULL, sender TEXT, subject TEXT, "
        "validated_at TEXT NOT NULL, followup_sent INTEGER NOT NULL DEFAULT 0, "
        "followup_count INTEGER NOT NULL DEFAULT 0, last_followup_at TEXT, "
        "org_id TEXT NOT NULL DEFAULT 'default')"
    )
    # Migration idempotente : les bases créées avant la cadence multi-round (§11.6 item 5) n'ont
    # que `followup_sent` (0/1) — on ajoute les nouvelles colonnes si absentes et on reporte l'état
    # déjà connu (`followup_sent=1` -> une relance déjà envoyée) pour ne perdre aucun historique.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(followup)").fetchall()}
    if "followup_count" not in existing_cols:
        conn.execute("ALTER TABLE followup ADD COLUMN followup_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE followup SET followup_count = followup_sent WHERE followup_sent = 1")
    if "last_followup_at" not in existing_cols:
        conn.execute("ALTER TABLE followup ADD COLUMN last_followup_at TEXT")
    if "org_id" not in existing_cols:  # fondation multi-tenant (§12 item 3 / §14.3)
        conn.execute("ALTER TABLE followup ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
    conn.commit()
    return conn


@with_sqlite_retry
def init_db() -> None:
    """Crée la table si nécessaire (appelé au démarrage de relance.py)."""
    _connect().close()


@with_sqlite_retry
def track(thread_id: str, gmail_thread_id: str, sender: str, subject: str, org_id: str = None) -> None:
    """Enregistre un lead validé venant de Gmail pour suivi de relance. No-op si pas de fil Gmail."""
    if not gmail_thread_id:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO followup (thread_id, gmail_thread_id, sender, subject, validated_at, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, gmail_thread_id, sender, subject, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             org_id or current_org_id()),
        )
        conn.commit()


def relance_max_rounds() -> int:
    """
    Plafond de relances effectif : réglage du panneau (config_store, prioritaire, §12 item 7) sinon
    `RELANCE_MAX_ROUNDS` (`.env`/défaut) — même principe que `app._calendly_url()`.
    """
    from . import config_store

    override = config_store.get_setting("RELANCE_MAX_ROUNDS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return RELANCE_MAX_ROUNDS


@with_sqlite_retry
def list_active(org_id: str = None) -> list:
    """Leads suivis du tenant courant n'ayant pas encore atteint le plafond de relances."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, gmail_thread_id, sender, subject, followup_count FROM followup "
            "WHERE followup_count < ? AND org_id = ?",
            (relance_max_rounds(), org_id or current_org_id()),
        ).fetchall()
    return [
        {"thread_id": r[0], "gmail_thread_id": r[1], "sender": r[2], "subject": r[3], "followup_count": r[4]}
        for r in rows
    ]


@with_sqlite_retry
def mark_followed_up(thread_id: str) -> None:
    """
    Incrémente le compteur de relances d'un lead (cadence multi-round, §11.6 item 5) — remplace
    l'ancien flag booléen `followup_sent` (conservé en base pour compatibilité, plus lu par
    `list_active`). `relance.check_one` ne rappelle cette fonction que lorsque le fil montre que
    NOUS avons parlé en dernier depuis au moins `RELANCE_DAYS` — la cadence s'arrête donc
    automatiquement dès que le prospect répond, sans logique supplémentaire ici.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE followup SET followup_count = followup_count + 1, followup_sent = 1, "
            "last_followup_at = ? WHERE thread_id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), thread_id),
        )
        conn.commit()

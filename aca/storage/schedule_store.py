"""
Mémoire des passages du planificateur (§16.0 de docs/ACAM_roadmap.md) : quand chaque travail
périodique (`relance`, `retention`, `maintenance`, `billing`) a été exécuté pour la dernière fois.

Sans cette persistance, [scheduler.py](../core/scheduler.py) rejouerait TOUS ses travaux à chaque
redémarrage du process — une purge de rétention et une passe de relance Gmail à chaque `docker
compose up`, ce qui est au mieux du gaspillage de quota et au pire une rafale de brouillons de
relance sur un simple redéploiement.

Même forme que [config_store.py](config_store.py) : registre SQLite local, scopé par tenant
(`org_id`, cf. aca.core.tenant) et enveloppé par `with_sqlite_retry` — le planificateur écrit hors
du graphe, donc hors du `RETRY_POLICY` de LangGraph, exactement comme les autres registres locaux.

À ne pas confondre avec `config_store.py`, qui stocke ce qu'un humain a **réglé** (« attendre 4
jours avant une relance ») ; ce module stocke ce que la machine a **fait** (« le travail de relance
est passé à telle heure »). Le premier s'édite depuis l'onglet « Réglages », le second jamais.

L'horodatage est stocké deux fois volontairement : `last_run_epoch` (REAL) sert au calcul d'échéance
— une soustraction, sans aucune question de fuseau horaire ni de format —, `last_run_at` (TEXT) sert
uniquement à la lecture humaine quand on inspecte la base pour comprendre pourquoi un travail ne
s'est pas déclenché.
"""
import os
import sqlite3
from datetime import datetime
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_SCHEDULE_DB", "data/schedule.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS job_runs ("
        "org_id TEXT NOT NULL, job TEXT NOT NULL, last_run_epoch REAL NOT NULL, "
        "last_run_at TEXT NOT NULL, last_status TEXT NOT NULL, "
        "PRIMARY KEY (org_id, job))"
    )
    return conn


@with_sqlite_retry
def init_db() -> None:
    """Crée la table si nécessaire (appelé au démarrage du planificateur)."""
    _connect().close()


@with_sqlite_retry
def get_last_run(job: str, org_id: str = None) -> float:
    """
    Horodatage (epoch) du dernier passage de `job`, ou `None` s'il n'a jamais tourné.

    `None` est traité comme « échu » par le planificateur : un travail jamais exécuté doit l'être
    au premier tick, sinon une purge de rétention n'aurait jamais lieu sur une installation neuve.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_run_epoch FROM job_runs WHERE org_id = ? AND job = ?",
            (org_id or current_org_id(), job),
        ).fetchone()
    return row[0] if row else None


@with_sqlite_retry
def record_run(job: str, epoch: float, status: str = "ok", org_id: str = None) -> None:
    """
    Enregistre un passage de `job`, **qu'il ait réussi ou échoué** (`status`).

    Enregistrer aussi les échecs est délibéré : sans cela, un travail durablement en erreur (Gmail
    injoignable, quota épuisé) serait retenté à chaque tick — soit toutes les 60 s par défaut —
    transformant une panne en martèlement. Le compromis assumé est qu'une erreur *transitoire*
    retarde le travail d'un intervalle complet ; c'est le bon compromis pour des travaux dont aucun
    n'est urgent à la minute près (purge, relance, facturation).
    """
    with _connect() as conn:
        conn.execute(
            "INSERT INTO job_runs (org_id, job, last_run_epoch, last_run_at, last_status) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (org_id, job) DO UPDATE SET last_run_epoch = excluded.last_run_epoch, "
            "last_run_at = excluded.last_run_at, last_status = excluded.last_status",
            (
                org_id or current_org_id(), job, epoch,
                datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S"), status,
            ),
        )
        conn.commit()


@with_sqlite_retry
def list_runs(org_id: str = None) -> list:
    """Dernier passage de chaque travail du tenant courant — alimente `scheduler --status`."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT job, last_run_epoch, last_run_at, last_status FROM job_runs WHERE org_id = ? "
            "ORDER BY job",
            (org_id or current_org_id(),),
        ).fetchall()
    return [
        {"job": job, "last_run_epoch": epoch, "last_run_at": at, "last_status": status}
        for job, epoch, at, status in rows
    ]

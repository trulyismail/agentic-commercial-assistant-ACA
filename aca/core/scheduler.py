"""
Planificateur autonome (§16.0 de docs/ACAM_roadmap.md) — la pièce qui rend le palier « Solo »
(sans n8n) réellement automatique 24/7.

Contexte du manque qu'il comble : `relance.py`, `retention.py` et `billing.py` étaient déjà écrits,
testés, et chacun documenté « à planifier périodiquement (ex. une fois par jour) » — mais **rien ne
les planifiait**. Aucune dépendance de planification n'existait dans `requirements.txt`, et la
machine de développement est sous Windows, qui n'a même pas de `cron`. Résultat concret : la purge
RGPD et les relances commerciales ne partaient que si un humain pensait à lancer la commande à la
main, c'est-à-dire en pratique jamais. C'était la seule des cinq capacités autonomes du §16.0
(ingestion, traitement passif, nettoyage, maintenance, relances) qui manquait vraiment : les deux
premières sont assurées depuis toujours par [poller.py](poller.py).

Ce module **ne réimplémente aucune logique métier** : il ne fait que cadencer des fonctions déjà
existantes. C'est aussi pourquoi il n'introduit aucune dépendance (ni APScheduler, ni Celery) —
une boucle `time.sleep` sur des horodatages persistés suffit très largement pour quatre travaux
dont le plus fréquent tourne à l'heure, et reste cohérent avec la contrainte 0 € et le peu de
dépendances du projet.

Correspondance n8n : au palier « Enterprise », ces mêmes travaux sont des nœuds **Schedule** n8n
appelant les mêmes fonctions. Les deux paliers exécutent le même code métier — c'est précisément
la démonstration que n8n est optionnel et non structurant.

Lancement :
    python -m aca.core.scheduler              # boucle permanente (mode service)
    python -m aca.core.scheduler --once       # un seul passage des travaux échus, puis sortie
    python -m aca.core.scheduler --job relance --force   # forcer un travail précis
    python -m aca.core.scheduler --status     # dernier passage de chaque travail
    python -m aca.core.scheduler --prime      # après un 1er déploiement : décale tout d'un
                                              # intervalle sans rien exécuter (cf. `prime()`)
"""
import argparse
import logging
import os
import time
from datetime import datetime
from dotenv import load_dotenv

from aca.storage import activity_log, schedule_store

load_dotenv()

logger = logging.getLogger("aca.scheduler")

# Cadence de la boucle : à quelle fréquence on REGARDE si un travail est échu (pas la fréquence des
# travaux eux-mêmes). 60 s suffit — le travail le plus fréquent tourne à l'heure.
TICK_SECONDS = int(os.getenv("ACA_SCHEDULE_TICK_SECONDS", "60") or "60")


def _job_relance() -> None:
    """Relances commerciales — brouillons Gmail dans le fil, jamais envoyés seuls (cf. relance.py)."""
    from aca.core import relance

    relance.run()


def _job_retention() -> None:
    """Purge RGPD par ancienneté — Leads, checkpoints, file d'attente (cf. retention.py)."""
    from aca.core import retention

    retention.run()


def _job_maintenance() -> None:
    """
    Maintenance de la file : récupère les entrées bloquées en « en_cours » par un poller interrompu.

    `poller.py` appelle déjà `reset_stale()` à chaque cycle, mais uniquement s'il tourne. Au palier
    Solo le poller tourne en permanence, donc c'est redondant ; au palier Enterprise, où n8n peut
    remplacer le poller, plus personne n'appellerait cette récupération — d'où sa présence ici.
    """
    from aca.storage import queue_store

    recovered = queue_store.reset_stale()
    if recovered:
        logger.info("Maintenance : %d entrée(s) bloquée(s) réinitialisée(s).", recovered)


def _job_billing() -> None:
    """
    Remontée de consommation (Stripe) — no-op gracieux sans `STRIPE_API_KEY` (cf. billing.py).

    Volontairement planifié malgré son caractère commercial : sans clé Stripe, `report_usage()`
    se contente de renvoyer les statistiques locales sans jamais appeler le réseau, donc l'inclure
    ici ne coûte rien et évite d'avoir à recâbler quoi que ce soit le jour où un client réel arrive.
    """
    from aca.integrations import billing

    billing.report_usage(days=30)


ARCHIVE_DIR = os.getenv("ACA_ARCHIVE_DIR", "data/archives")


def _last_completed_month(now: datetime = None) -> tuple:
    """
    Dernier mois civil ENTIÈREMENT écoulé par rapport à `now` (injectable, donc testable sans
    horloge) — jamais le mois en cours, qui n'a pas fini de recevoir des lignes.
    """
    now = now or datetime.now()
    return (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)


def _job_archive() -> None:
    """
    Archive mensuelle signée du journal d'activité (§18) — cf. `activity_log.archive_period()`.

    « Mensuelle » au sens métier, pas au sens de la cadence du planificateur : `default_hours`
    ci-dessous ne fait que régler la fréquence à laquelle on VÉRIFIE qu'une archive manque ;
    `archive_period()` est idempotent (une archive déjà présente n'est jamais réécrite), donc une
    vérification en trop — un `--force`, un redémarrage le même jour — n'écrit rien de plus.
    """
    from aca.storage import activity_log

    year, month = _last_completed_month()
    result = activity_log.archive_period(ARCHIVE_DIR, year, month)
    if result.get("skipped"):
        logger.info("Archive %04d-%02d déjà présente (%s).", year, month, result["path"])
    else:
        logger.info(
            "Archive %04d-%02d écrite : %s (%s ligne(s)).",
            year, month, result["path"], result["lines"],
        )


# Table déclarative des travaux — ajouter un travail périodique = une entrée ici, rien d'autre
# (même esprit que `ROUTING_DESTINATIONS` dans app.py). `env` permet de régler l'intervalle sans
# toucher au code ; `0` ou une valeur négative **désactive** le travail (dégradation gracieuse
# identique au reste du projet : absent/à zéro = fonctionnalité ignorée, jamais une erreur).
JOBS = {
    "maintenance": {
        "fn": _job_maintenance,
        "env": "ACA_SCHEDULE_MAINTENANCE_HOURS",
        "default_hours": 1,
        "label": "Maintenance de la file d'attente",
    },
    "relance": {
        "fn": _job_relance,
        "env": "ACA_SCHEDULE_RELANCE_HOURS",
        "default_hours": 24,
        "label": "Relances commerciales",
    },
    "retention": {
        "fn": _job_retention,
        "env": "ACA_SCHEDULE_RETENTION_HOURS",
        "default_hours": 168,  # hebdomadaire
        "label": "Purge RGPD (rétention)",
    },
    "billing": {
        "fn": _job_billing,
        "env": "ACA_SCHEDULE_BILLING_HOURS",
        "default_hours": 720,  # ~mensuel
        "label": "Remontée de consommation",
    },
    "archive": {
        "fn": _job_archive,
        "env": "ACA_SCHEDULE_ARCHIVE_HOURS",
        "default_hours": 720,  # ~mensuel — le mois archivé est déterminé par _last_completed_month,
                                # pas par cette cadence de vérification
        "label": "Archive mensuelle du journal d'activité",
    },
}


def interval_hours(job: str) -> float:
    """
    Intervalle effectif d'un travail : variable d'environnement si définie, défaut sinon.

    Lue DYNAMIQUEMENT à chaque appel, jamais figée à l'import — même leçon que `DATABASE_URL` dans
    `vector_store.py` et `ACA_RATE_LIMIT` dans `api.py` : une valeur figée à l'import est
    intestable et se gèle silencieusement selon l'ordre des imports.
    """
    spec = JOBS[job]
    raw = os.getenv(spec["env"])
    if raw is None or raw.strip() == "":
        return float(spec["default_hours"])
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "%s = %r n'est pas un nombre — repli sur le défaut (%s h).",
            spec["env"], raw, spec["default_hours"],
        )
        return float(spec["default_hours"])


def is_due(job: str, now: float) -> bool:
    """
    `job` doit-il tourner maintenant ? Fonction pure vis-à-vis du temps (`now` est injecté), ce qui
    la rend testable sans attendre ni manipuler l'horloge système.

    Un travail jamais exécuté est échu : sur une installation neuve, la purge de rétention doit
    partir au premier tick plutôt que d'attendre une semaine.
    """
    if interval_hours(job) <= 0:
        return False  # travail désactivé
    last = schedule_store.get_last_run(job)
    if last is None:
        return True
    return (now - last) >= interval_hours(job) * 3600


def run_job(job: str, now: float = None) -> bool:
    """
    Exécute un travail et enregistre son passage. Renvoie True si le travail a réussi.

    N'échoue JAMAIS vers l'appelant : une exception est journalisée et enregistrée avec le statut
    « error », pour que la boucle du planificateur survive à un service externe en panne — même
    contrat que la boucle `run_forever()` de [poller.py](poller.py). Le passage est enregistré même
    en cas d'échec ; cf. `schedule_store.record_run` pour le raisonnement (évite de marteler un
    service mort à chaque tick).
    """
    now = time.time() if now is None else now
    spec = JOBS[job]
    logger.info("Démarrage du travail « %s » (%s).", job, spec["label"])
    try:
        spec["fn"]()
        schedule_store.record_run(job, now, status="ok")
        logger.info("Travail « %s » terminé.", job)
        # §18 — trace machine du planificateur lui-même : distincte de ce que chaque travail
        # journalise en son propre nom (`ACTION_DATA_PURGED`/`ACTION_FOLLOWUP_DRAFTED`) — celle-ci
        # répond à « le planificateur a-t-il seulement tourné », visible même pour les travaux qui
        # n'ont rien à journaliser eux-mêmes (maintenance, billing).
        activity_log.log(
            activity_log.ACTION_JOB_RAN, actor="(scheduler)", target_type="job", target_id=job,
            source=activity_log.SOURCE_CLI, details={"label": spec["label"]},
        )
        return True
    except Exception as e:
        schedule_store.record_run(job, now, status="error")
        logger.exception("Échec du travail « %s » : %s", job, e)
        activity_log.log(
            activity_log.ACTION_JOB_RAN, actor="(scheduler)", target_type="job", target_id=job,
            source=activity_log.SOURCE_CLI, outcome=activity_log.OUTCOME_FAILURE,
            details={"label": spec["label"], "erreur": str(e)},
        )
        return False


def run_due_jobs(now: float = None) -> list:
    """Exécute tous les travaux échus. Renvoie la liste des noms exécutés (vide si aucun)."""
    now = time.time() if now is None else now
    executed = []
    for job in JOBS:
        if is_due(job, now):
            run_job(job, now)
            executed.append(job)
    return executed


def prime(now: float = None) -> list:
    """
    Marque tous les travaux jamais exécutés comme « venant de tourner », **sans les exécuter**.

    À utiliser juste après un premier déploiement. Un travail jamais exécuté étant considéré comme
    échu (cf. `is_due`), le tout premier démarrage déclenche sinon les quatre travaux d'un coup —
    dont `relance`, qui écrit de vrais brouillons dans Gmail. C'est défendable (ces relances sont
    réellement dues) mais surprenant le jour de la mise en service, et le même effet se reproduit
    si `schedule.sqlite` est perdu ou si l'on recopie un déploiement.

    `python -m aca.core.scheduler --prime` décale donc proprement chaque travail d'un intervalle.
    Renvoie la liste des travaux amorcés.
    """
    now = time.time() if now is None else now
    primed = []
    for job in JOBS:
        if schedule_store.get_last_run(job) is None:
            schedule_store.record_run(job, now, status="primed")
            primed.append(job)
    return primed


def run_forever() -> None:
    schedule_store.init_db()
    enabled = [j for j in JOBS if interval_hours(j) > 0]
    disabled = [j for j in JOBS if interval_hours(j) <= 0]
    logger.info(
        "Planificateur démarré — tick %ds. Actifs : %s.%s",
        TICK_SECONDS,
        ", ".join(f"{j} ({interval_hours(j):g}h)" for j in enabled) or "aucun",
        f" Désactivés : {', '.join(disabled)}." if disabled else "",
    )
    while True:
        try:
            run_due_jobs()
        except Exception as e:  # filet de sécurité : la boucle ne doit jamais mourir
            logger.exception("Erreur inattendue dans le cycle du planificateur : %s", e)
        time.sleep(TICK_SECONDS)


def _print_status() -> None:
    runs = {r["job"]: r for r in schedule_store.list_runs()}
    print(f"{'Travail':<14} {'Intervalle':<12} {'Dernier passage':<21} Statut")
    print("-" * 60)
    for job in JOBS:
        hours = interval_hours(job)
        cadence = "désactivé" if hours <= 0 else f"{hours:g} h"
        run = runs.get(job)
        last = run["last_run_at"] if run else "jamais"
        status = run["last_status"] if run else "-"
        print(f"{job:<14} {cadence:<12} {last:<21} {status}")


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(
        description="Planificateur des travaux périodiques ACA (§16.0) — relances, purge RGPD, "
                    "maintenance, consommation.",
    )
    parser.add_argument("--once", action="store_true",
                        help="exécute les travaux échus une seule fois puis quitte (usage cron/n8n)")
    parser.add_argument("--job", choices=sorted(JOBS),
                        help="n'exécuter que ce travail (s'il est échu, ou avec --force)")
    parser.add_argument("--force", action="store_true",
                        help="exécuter --job même s'il n'est pas encore échu")
    parser.add_argument("--status", action="store_true",
                        help="afficher le dernier passage de chaque travail et quitter")
    parser.add_argument("--prime", action="store_true",
                        help="marquer les travaux jamais exécutés comme déjà passés, SANS les "
                             "exécuter (à lancer après un premier déploiement)")
    args = parser.parse_args()

    schedule_store.init_db()

    if args.status:
        _print_status()
        return
    if args.prime:
        primed = prime()
        logger.info("Amorçage — %s.",
                    f"décalé(s) d'un intervalle : {', '.join(primed)}" if primed
                    else "aucun travail à amorcer (tous ont déjà tourné)")
        return
    if args.job:
        if args.force or is_due(args.job, time.time()):
            run_job(args.job)
        else:
            logger.info("Travail « %s » pas encore échu (utilisez --force pour l'imposer).", args.job)
        return
    if args.once:
        executed = run_due_jobs()
        logger.info("Passage unique terminé — %s.",
                    f"exécuté(s) : {', '.join(executed)}" if executed else "aucun travail échu")
        return
    run_forever()


if __name__ == "__main__":
    _main()

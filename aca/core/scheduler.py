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


def _job_tasks() -> None:
    """
    Exécute les tâches datées posées par un humain (§19) : envois programmés et rappels.

    Deux natures, deux effets, une seule boucle — cf. `storage/task_store.py` pour la raison d'une
    table unique. Chaque tâche est traitée isolément dans son propre `try` : une adresse Gmail
    injoignable sur un lead ne doit pas empêcher le rappel du lead suivant de partir (même principe
    que la boucle `for` de `poller.poll_once()`).

    Le service Gmail n'est construit **qu'en présence d'un envoi réellement dû** : l'immense
    majorité des passages ne trouve rien à faire, et ouvrir une session Gmail à chaque tick pour
    repartir aussitôt reviendrait à payer un appel réseau par minute pour rien.
    """
    from aca.storage import task_store

    due = task_store.list_due(time.time())
    if not due:
        return

    service = None
    for task in due:
        try:
            if task["kind"] == task_store.KIND_SEND:
                from aca.integrations import gmail_reader

                if not task["gmail_draft_id"]:
                    # Sans brouillon, il n'y a rien à envoyer et il n'y en aura jamais : marquer en
                    # échec plutôt que laisser la tâche « en attente » éternellement à retenter.
                    task_store.mark_failed(task["id"], "Aucun brouillon Gmail associé.")
                    continue
                if service is None:
                    service = gmail_reader.get_gmail_service()
                sent_id = gmail_reader.send_draft(service, task["gmail_draft_id"])
                if sent_id:
                    task_store.mark_done(task["id"], f"Message {sent_id} envoyé.")
                    logger.info("Envoi programmé effectué (tâche %s).", task["id"])
                else:
                    # `send_draft` ne lève pas : un brouillon supprimé dans Gmail est une annulation
                    # humaine légitime, pas un incident. On la consigne sans la crier.
                    task_store.mark_failed(
                        task["id"], "Brouillon introuvable ou envoi refusé par Gmail.",
                    )
            else:
                from aca.integrations import notify

                label = task["label"] or task["thread_id"] or "lead"
                # `send_all` et non `send` : un rappel personnel doit arriver sur TOUS les canaux
                # configurés. `send()` s'arrête au premier succès, donc dès que Slack fonctionnait
                # l'e-mail n'était jamais envoyé — silencieusement, alors que c'est justement le
                # canal qu'on retrouve le lendemain matin dans sa boîte.
                channels = notify.send_all(
                    f"⏰ Rappel — {label}\n\n{task['note']}",
                    subject=f"Rappel ACA — {label}",
                )
                # L'application est un canal à part entière : le rappel reste dans la barre
                # latérale jusqu'à ce qu'un humain clique « Vu » (`acknowledged_at`). Il n'y a donc
                # plus de cas « n'a atteint personne » — c'est ce qui distingue cette version de la
                # précédente, qui marquait un échec faute de Slack ou d'e-mail. Le fait qu'un
                # humain ait RÉELLEMENT vu le rappel n'est pas porté par `status` mais par
                # `acknowledged_at`, qui reste vide tant que personne ne l'a acquitté.
                task_store.mark_done(
                    task["id"],
                    "Rappel poussé vers " + (", ".join(channels + ["l'application"])),
                )

            activity_log.log(
                activity_log.ACTION_TASK_EXECUTED,
                actor=task["created_by"] or "(planificateur)", target_type=task["kind"],
                target_id=str(task["id"]), source=activity_log.SOURCE_CLI,
                details={"échéance": task["due_at"], "lead": task["thread_id"]},
            )
        except Exception as e:
            logger.warning("Tâche %s en échec : %s", task.get("id"), e)
            task_store.mark_failed(task.get("id"), str(e)[:200])


ARCHIVE_DIR = os.getenv("ACA_ARCHIVE_DIR", "data/archives")
REPORT_DIR = os.getenv("ACA_REPORT_DIR", "data/reports")


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


def _job_report() -> None:
    """
    Rapport mensuel d'activité en PDF (§20) — cf. `aca/core/reporting.py`.

    Même distinction que `_job_archive` ci-dessus : « mensuel » qualifie ce qui est produit (le
    dernier mois civil ENTIÈREMENT écoulé), pas la fréquence à laquelle on vérifie qu'il manque.
    `report_pdf.write_pdf` est idempotent, donc un `--force` ou un redémarrage le même jour ne
    réécrit rien.

    **La notification n'est pas un ornement.** Un rapport déposé dans un répertoire que personne
    n'ouvre est exactement le manque que le §16.0 avait trouvé pour la planification elle-même :
    du code écrit, testé, documenté... et sans effet réel parce que rien ne le déclenchait ni ne le
    signalait. `notify.send()` retombe en silence s'il n'y a ni Slack ni e-mail configuré (contrat
    inchangé), et le rapport reste de toute façon visible dans l'onglet « Rapports ».
    """
    from aca.core import reporting
    from aca.integrations import report_pdf

    year, month = reporting.last_completed_month()
    spec = reporting.monthly_spec(year, month)
    filename = reporting.report_filename(spec)

    payload = report_pdf.build_report_pdf(reporting.collect(spec))
    if not payload:
        # `build_report_pdf` ne lève jamais et a déjà journalisé la cause. On s'arrête ici plutôt
        # que d'écrire un fichier vide, qui serait ensuite considéré comme « déjà produit » par
        # l'idempotence de `write_pdf` et empêcherait toute nouvelle tentative.
        raise RuntimeError("Le rendu PDF du rapport mensuel a échoué (cf. journal ci-dessus).")

    result = report_pdf.write_pdf(REPORT_DIR, filename, payload)
    period = reporting.month_label(year, month)
    if result["skipped"]:
        logger.info("Rapport %s déjà présent (%s).", period, result["path"])
        return

    logger.info("Rapport %s écrit : %s (%d octets).", period, result["path"], result["bytes"])
    activity_log.log(
        activity_log.ACTION_REPORT_GENERATED, actor="(scheduler)", target_type="rapport",
        target_id=filename, source=activity_log.SOURCE_CLI,
        details={"période": period, "octets": result["bytes"], "chemin": result["path"]},
    )
    try:
        from aca.integrations import notify

        notify.send(
            f"📄 Rapport d'activité de {period} disponible : {result['path']}",
            subject=f"Rapport ACA — {period}",
        )
    except Exception as e:  # noqa: BLE001
        # Le rapport EXISTE : ne pas avoir su prévenir ne doit pas faire passer le travail pour
        # un échec, ce qui déclencherait une nouvelle tentative de génération à chaque tick.
        logger.warning("Rapport %s produit mais notification impossible : %s", period, e)


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
    "report": {
        "fn": _job_report,
        "env": "ACA_SCHEDULE_REPORT_HOURS",
        "default_hours": 720,  # ~mensuel — comme `archive`, le mois couvert est fixé par
                                # `reporting.last_completed_month()`, pas par cette cadence
        "label": "Rapport mensuel d'activité (PDF)",
    },
    "tasks": {
        "fn": _job_tasks,
        "env": "ACA_SCHEDULE_TASKS_HOURS",
        # 1/60 h = 60 s, soit un passage par tick. Seul travail de cette table dont la cadence est
        # sub-horaire, et c'est justifié : les autres traitent des échéances en jours ou en mois,
        # celui-ci exécute une heure choisie par un humain. Une vérification horaire ferait partir
        # à 15 h 00 un message demandé pour 14 h 15 — un décalage que personne n'accepterait d'un
        # envoi différé. Le vrai contrôle d'échéance est `task_store.list_due()` ; ce travail se
        # contente de la consulter souvent, et ne coûte rien quand elle est vide.
        "default_hours": 1 / 60,
        "label": "Envois programmés et rappels",
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

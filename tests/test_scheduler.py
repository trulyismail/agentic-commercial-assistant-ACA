"""
Tests du planificateur autonome (§16.0) — la pièce qui rend le palier « Solo » (sans n8n)
réellement automatique 24/7.

Ces tests ne dorment jamais et ne touchent jamais l'horloge système : `is_due`/`run_job`/
`run_due_jobs` acceptent `now` en paramètre précisément pour ça. Les travaux réels
(relance/retention/billing) sont remplacés par des doublures — aucun appel Gmail, Sheets ou Stripe.
"""
import time

import pytest

from aca.core import scheduler
from aca.storage import schedule_store


@pytest.fixture(autouse=True)
def _clean_schedule_db(tmp_path, monkeypatch):
    """Base de planification neuve pour chaque test (isolation entre cas)."""
    monkeypatch.setattr(schedule_store, "DB_PATH", str(tmp_path / "schedule.sqlite"))
    schedule_store.init_db()


# ── Intervalles : lecture dynamique de l'environnement ────────────────────────────────────────


def test_interval_falls_back_to_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("ACA_SCHEDULE_RETENTION_HOURS", raising=False)
    assert scheduler.interval_hours("retention") == 168


def test_interval_reads_env_dynamically(monkeypatch):
    """Jamais figé à l'import — un changement d'environnement doit être vu immédiatement."""
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "6")
    assert scheduler.interval_hours("relance") == 6
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "12")
    assert scheduler.interval_hours("relance") == 12


def test_invalid_interval_falls_back_to_default(monkeypatch):
    """Une valeur non numérique ne doit pas faire planter le service au démarrage."""
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "tous-les-jours")
    assert scheduler.interval_hours("relance") == 24


# ── Échéance ──────────────────────────────────────────────────────────────────────────────────


def test_never_run_job_is_due():
    """Sur une installation neuve, la purge RGPD doit partir au 1er tick, pas dans une semaine."""
    assert scheduler.is_due("retention", now=time.time()) is True


def test_job_not_due_before_interval(monkeypatch):
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "24")
    now = time.time()
    schedule_store.record_run("relance", now)
    assert scheduler.is_due("relance", now + 3600) is False        # 1 h plus tard
    assert scheduler.is_due("relance", now + 23 * 3600) is False   # 23 h plus tard


def test_job_due_once_interval_elapsed(monkeypatch):
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "24")
    now = time.time()
    schedule_store.record_run("relance", now)
    assert scheduler.is_due("relance", now + 24 * 3600) is True


def test_zero_hours_disables_job(monkeypatch):
    """`0` = désactivé — dégradation gracieuse, jamais une erreur (contrat du projet)."""
    monkeypatch.setenv("ACA_SCHEDULE_BILLING_HOURS", "0")
    assert scheduler.is_due("billing", time.time()) is False


def test_negative_hours_disables_job(monkeypatch):
    monkeypatch.setenv("ACA_SCHEDULE_RETENTION_HOURS", "-1")
    assert scheduler.is_due("retention", time.time()) is False


# ── Exécution ─────────────────────────────────────────────────────────────────────────────────


def test_run_job_records_success(monkeypatch):
    called = []
    monkeypatch.setitem(scheduler.JOBS["relance"], "fn", lambda: called.append(True))

    now = time.time()
    assert scheduler.run_job("relance", now) is True

    assert called == [True]
    assert schedule_store.get_last_run("relance") == pytest.approx(now)
    assert schedule_store.list_runs()[0]["last_status"] == "ok"


def test_failing_job_never_propagates_and_is_recorded(monkeypatch):
    """
    Un service externe en panne ne doit pas tuer le planificateur — même contrat que la boucle
    `run_forever()` de poller.py. Le passage est enregistré malgré l'échec, sinon un travail en
    erreur serait retenté à chaque tick (toutes les 60 s), transformant une panne en martèlement.
    """
    def _boom():
        raise RuntimeError("Gmail injoignable")

    monkeypatch.setitem(scheduler.JOBS["relance"], "fn", _boom)

    now = time.time()
    assert scheduler.run_job("relance", now) is False  # ne lève pas

    assert schedule_store.get_last_run("relance") == pytest.approx(now)
    assert schedule_store.list_runs()[0]["last_status"] == "error"


def test_failing_job_is_not_retried_next_tick(monkeypatch):
    """Corollaire du test précédent, vérifié bout en bout : pas de martèlement."""
    calls = []
    monkeypatch.setenv("ACA_SCHEDULE_RELANCE_HOURS", "24")
    for job in ("maintenance", "retention", "billing"):
        monkeypatch.setenv(scheduler.JOBS[job]["env"], "0")  # isoler « relance »

    def _boom():
        calls.append(1)
        raise RuntimeError("Gmail injoignable")

    monkeypatch.setitem(scheduler.JOBS["relance"], "fn", _boom)

    now = time.time()
    scheduler.run_due_jobs(now)
    scheduler.run_due_jobs(now + 60)   # tick suivant, 1 min plus tard
    assert len(calls) == 1

    scheduler.run_due_jobs(now + 24 * 3600)  # une fois l'intervalle écoulé, on réessaie
    assert len(calls) == 2


def test_run_due_jobs_runs_only_due_ones(monkeypatch):
    ran = []
    for job in scheduler.JOBS:
        monkeypatch.setitem(scheduler.JOBS[job], "fn", lambda j=job: ran.append(j))
        monkeypatch.setenv(scheduler.JOBS[job]["env"], "24")

    now = time.time()
    schedule_store.record_run("relance", now)  # « relance » vient de tourner

    executed = scheduler.run_due_jobs(now)

    assert "relance" not in executed
    assert set(executed) == {"maintenance", "retention", "billing"}
    assert "relance" not in ran


def test_disabled_jobs_never_run(monkeypatch):
    ran = []
    for job in scheduler.JOBS:
        monkeypatch.setitem(scheduler.JOBS[job], "fn", lambda j=job: ran.append(j))
        monkeypatch.setenv(scheduler.JOBS[job]["env"], "0")

    assert scheduler.run_due_jobs(time.time()) == []
    assert ran == []


def test_every_declared_job_is_callable():
    """Garde-fou de la table déclarative : une entrée mal branchée doit échouer au test, pas en prod."""
    for job, spec in scheduler.JOBS.items():
        assert callable(spec["fn"]), job
        assert spec["env"].startswith("ACA_SCHEDULE_"), job
        assert spec["default_hours"] > 0, job
        assert spec["label"], job


# ── Amorçage d'un premier déploiement (`--prime`) ─────────────────────────────────────────────


def test_prime_marks_jobs_without_running_them(monkeypatch):
    """
    Le point clé : `prime()` ne doit RIEN exécuter. Sans lui, le tout premier démarrage déclenche
    `relance`, qui écrit de vrais brouillons dans Gmail — surprenant le jour de la mise en service.
    """
    ran = []
    for job in scheduler.JOBS:
        monkeypatch.setitem(scheduler.JOBS[job], "fn", lambda j=job: ran.append(j))

    now = time.time()
    primed = scheduler.prime(now)

    assert set(primed) == set(scheduler.JOBS)
    assert ran == []  # aucun travail exécuté
    for job in scheduler.JOBS:
        assert scheduler.is_due(job, now) is False


def test_prime_leaves_already_run_jobs_untouched():
    """Amorcer deux fois ne doit pas réécrire l'historique d'un travail qui a réellement tourné."""
    earlier = time.time() - 999
    schedule_store.record_run("relance", earlier, status="ok")

    primed = scheduler.prime(time.time())

    assert "relance" not in primed
    assert schedule_store.get_last_run("relance") == pytest.approx(earlier)


# ── Persistance entre redémarrages ────────────────────────────────────────────────────────────


def test_last_run_survives_process_restart(monkeypatch):
    """
    La raison d'être de `schedule_store` : sans persistance, chaque `docker compose up` relancerait
    la purge RGPD et une passe de relance Gmail complète.
    """
    monkeypatch.setenv("ACA_SCHEDULE_RETENTION_HOURS", "168")
    now = time.time()
    schedule_store.record_run("retention", now)

    # Simule un redémarrage : nouvelle connexion à la même base, aucun état en mémoire.
    assert schedule_store.get_last_run("retention") == pytest.approx(now)
    assert scheduler.is_due("retention", now + 3600) is False


def test_runs_are_scoped_per_tenant():
    """Cohérent avec les 6 autres registres locaux (fondation multi-tenant §12 item 3)."""
    now = time.time()
    schedule_store.record_run("retention", now, org_id="client-a")

    assert schedule_store.get_last_run("retention", org_id="client-a") == pytest.approx(now)
    assert schedule_store.get_last_run("retention", org_id="client-b") is None

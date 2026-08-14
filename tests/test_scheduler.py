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
    """
    Bases neuves pour chaque test (isolation entre cas).

    `task_store` est réinitialisé au même titre que `schedule_store` : `conftest.py` ne redirige
    `ACA_TASK_DB` que pour la SESSION, si bien que les tâches créées par un test restaient visibles
    du suivant. Invisible tant que chaque cas n'affirmait que sur la tâche qu'il venait de créer,
    mais faux dès qu'un test porte sur le contenu global d'une liste (« quels rappels sont dus ? »).
    """
    from aca.storage import task_store

    monkeypatch.setattr(schedule_store, "DB_PATH", str(tmp_path / "schedule.sqlite"))
    monkeypatch.setattr(task_store, "DB_PATH", str(tmp_path / "tasks.sqlite"))
    schedule_store.init_db()
    task_store.init_db()


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
    # Dérivé de `JOBS` plutôt qu'écrit en dur : la liste en dur a cassé à chaque nouveau travail
    # (« archive » au §18, « tasks » au §19) alors que le comportement testé — « tout ce qui est
    # échu tourne, sauf ce qui vient de tourner » — n'avait pas changé. La version dérivée exprime
    # cette intention et échoue toujours si `run_due_jobs` en oublie un.
    assert set(executed) == set(scheduler.JOBS) - {"relance"}
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


# ── Archive mensuelle (§18) ───────────────────────────────────────────────────────────────────


def test_last_completed_month_is_previous_month():
    from datetime import datetime

    assert scheduler._last_completed_month(datetime(2026, 7, 15)) == (2026, 6)


def test_last_completed_month_wraps_year_boundary():
    """Le mois précédent en janvier est décembre de l'année d'AVANT, pas mois 0."""
    from datetime import datetime

    assert scheduler._last_completed_month(datetime(2026, 1, 5)) == (2025, 12)


def test_job_archive_writes_signed_csv_for_previous_month(monkeypatch, tmp_path):
    """
    `_job_archive` archive le mois précédent (jamais le mois en cours, pas terminé) et produit un
    CSV + son empreinte `.sha256`, via le même mécanisme que `activity_log.archive_period`.
    """
    from datetime import datetime

    from aca.storage import activity_log

    monkeypatch.setattr(activity_log, "DB_PATH", str(tmp_path / "activity.sqlite"))
    activity_log.init_db()
    monkeypatch.setattr(scheduler, "ARCHIVE_DIR", str(tmp_path / "archives"))

    year, month = scheduler._last_completed_month(datetime.now())
    activity_log.log(
        activity_log.ACTION_JOB_RAN, actor="(scheduler)", source=activity_log.SOURCE_CLI,
        details={"label": "test"},
    )
    # `log()` horodate avec `datetime.now()`, donc cette ligne tombe forcément dans le mois EN
    # COURS — on vérifie ici seulement que `_job_archive` ne plante pas et écrit une archive
    # (potentiellement vide) pour le mois précédent, jamais celui-ci.
    scheduler._job_archive()

    csv_path = tmp_path / "archives" / f"activite-{year:04d}-{month:02d}.csv"
    digest_path = tmp_path / "archives" / f"activite-{year:04d}-{month:02d}.csv.sha256"
    assert csv_path.exists()
    assert digest_path.exists()


def test_archive_job_declared_like_the_others():
    """Garde-fou : la nouvelle entrée suit le même contrat que `test_every_declared_job_is_callable`."""
    spec = scheduler.JOBS["archive"]
    assert callable(spec["fn"])
    assert spec["env"] == "ACA_SCHEDULE_ARCHIVE_HOURS"
    assert spec["default_hours"] > 0
    assert spec["label"]


# ── §19 — envois programmés et rappels : le travail qui ENVOIE réellement ─────────────────────
# Le registre (`task_store`) est testé à part ; ce qui suit vérifie le maillon manquant, celui qui
# transforme une tâche échue en e-mail effectivement expédié. Sans ces tests, « l'envoi
# automatique fonctionne » n'était qu'une intention exprimée dans du code jamais exécuté.
class _FakeDrafts:
    def __init__(self, store):
        self.store = store

    def send(self, userId=None, body=None):          # noqa: N803 (signature de l'API Gmail)
        self.store.append(body["id"])
        return type("R", (), {"execute": lambda _self: {"id": "msg-42"}})()


class _FakeGmail:
    """Doublure minimale du service Gmail : `service.users().drafts().send(...).execute()`."""

    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def drafts(self):
        return _FakeDrafts(self.sent)


def test_un_envoi_programme_echu_part_reellement(monkeypatch):
    from aca.integrations import gmail_reader
    from aca.storage import task_store

    fake = _FakeGmail()
    monkeypatch.setattr(gmail_reader, "get_gmail_service", lambda: fake)

    task_id = task_store.schedule_send(
        time.time() - 60, thread_id="t-send", gmail_draft_id="draft-1", created_by="operator",
    )
    scheduler._job_tasks()

    assert fake.sent == ["draft-1"]                     # le brouillon relu par l'humain, lui-même
    task = task_store.get_task(task_id)
    assert task["status"] == task_store.STATUS_DONE
    assert "msg-42" in task["detail"]


def test_une_tache_non_echue_reste_intacte(monkeypatch):
    from aca.integrations import gmail_reader
    from aca.storage import task_store

    fake = _FakeGmail()
    monkeypatch.setattr(gmail_reader, "get_gmail_service", lambda: fake)

    task_id = task_store.schedule_send(
        time.time() + 3600, thread_id="t-futur", gmail_draft_id="draft-futur",
    )
    scheduler._job_tasks()

    assert fake.sent == []
    assert task_store.get_task(task_id)["status"] == task_store.STATUS_PENDING


def test_une_tache_annulee_nest_jamais_envoyee(monkeypatch):
    """La propriété qui compte le plus : annuler doit empêcher le départ, pas seulement le masquer."""
    from aca.integrations import gmail_reader
    from aca.storage import task_store

    fake = _FakeGmail()
    monkeypatch.setattr(gmail_reader, "get_gmail_service", lambda: fake)

    task_id = task_store.schedule_send(
        time.time() - 60, thread_id="t-annule", gmail_draft_id="draft-annule",
    )
    task_store.cancel(task_id)
    scheduler._job_tasks()

    assert fake.sent == []
    assert task_store.get_task(task_id)["status"] == task_store.STATUS_CANCELLED


def test_un_brouillon_supprime_dans_gmail_echoue_proprement(monkeypatch):
    """
    Supprimer le brouillon dans Gmail est une annulation humaine légitime : `send_draft` renvoie
    `None` sans lever, et la tâche passe en échec plutôt que d'être retentée indéfiniment.
    """
    from aca.integrations import gmail_reader
    from aca.storage import task_store

    monkeypatch.setattr(gmail_reader, "get_gmail_service", lambda: _FakeGmail())
    monkeypatch.setattr(gmail_reader, "send_draft", lambda service, draft_id: None)

    task_id = task_store.schedule_send(
        time.time() - 60, thread_id="t-perdu", gmail_draft_id="draft-disparu",
    )
    scheduler._job_tasks()

    assert task_store.get_task(task_id)["status"] == task_store.STATUS_FAILED


def test_un_rappel_echu_notifie_lequipe_sans_toucher_a_gmail(monkeypatch):
    from aca.integrations import gmail_reader, notify
    from aca.storage import task_store

    envoyes = []
    monkeypatch.setattr(
        notify, "send_all", lambda message, **kw: envoyes.append(message) or ["Slack", "e-mail"],
    )

    def _interdit():
        raise AssertionError("un rappel ne doit jamais ouvrir de session Gmail")

    monkeypatch.setattr(gmail_reader, "get_gmail_service", _interdit)

    task_id = task_store.add_reminder(
        time.time() - 60, "Rappeler Dupont", thread_id="t-rappel", label="Entreprise Exemple",
    )
    scheduler._job_tasks()

    assert len(envoyes) == 1
    assert "Rappeler Dupont" in envoyes[0]
    assert task_store.get_task(task_id)["status"] == task_store.STATUS_DONE


def test_un_rappel_part_sur_tous_les_canaux_pas_seulement_le_premier(monkeypatch):
    """
    `send()` s'arrête au premier succès : dès que Slack marchait, l'e-mail n'était jamais envoyé.
    Pour un rappel personnel c'est le mauvais comportement — la personne veut le retrouver AUSSI
    dans sa boîte. Ce test verrouille l'usage de `send_all` plutôt que `send`.
    """
    from aca.integrations import notify
    from aca.storage import task_store

    canaux = []
    monkeypatch.setattr(notify, "_notify_slack",
                        lambda message, webhook_url=None, blocks=None: canaux.append("Slack") or True)
    monkeypatch.setattr(notify, "_notify_email",
                        lambda message, to=None, subject=None: canaux.append("e-mail") or True)

    task_id = task_store.add_reminder(time.time() - 60, "Deux canaux", thread_id="t-deux")
    scheduler._job_tasks()

    assert canaux == ["Slack", "e-mail"]                 # les DEUX, pas seulement Slack
    detail = task_store.get_task(task_id)["detail"]
    assert "Slack" in detail and "e-mail" in detail and "application" in detail


def test_un_rappel_sans_canal_externe_reste_visible_dans_lapplication(monkeypatch):
    """
    Sans Slack ni NOTIFY_EMAIL, le rappel n'est PAS un échec : il reste affiché dans la barre
    latérale jusqu'à ce qu'un humain clique « Vu ». C'est `acknowledged_at`, et non `status`, qui
    porte l'information « quelqu'un l'a réellement vu » — d'où un `status` « effectué » assorti
    d'un détail honnête sur le canal réellement emprunté.
    """
    from aca.integrations import notify
    from aca.storage import task_store

    monkeypatch.setattr(notify, "send_all", lambda message, **kw: [])

    task_id = task_store.add_reminder(time.time() - 60, "Visible en local", thread_id="t-x")
    scheduler._job_tasks()

    task = task_store.get_task(task_id)
    assert task["status"] == task_store.STATUS_DONE
    assert "application" in task["detail"]
    assert task["acknowledged_at"] is None               # personne ne l'a encore vu
    assert [r["id"] for r in task_store.list_due_reminders(time.time())] == [task_id]


def test_le_travail_tasks_tourne_a_chaque_tick():
    """
    Seule cadence sub-horaire de la table, et c'est justifié : une vérification horaire ferait
    partir à 15 h 00 un message demandé pour 14 h 15.
    """
    spec = scheduler.JOBS["tasks"]
    assert callable(spec["fn"])
    assert spec["env"] == "ACA_SCHEDULE_TASKS_HOURS"
    assert spec["default_hours"] * 3600 <= 60


# ── Rapport mensuel (§20) ─────────────────────────────────────────────────────────────────────
def test_le_rapport_mensuel_ecrit_un_pdf_du_mois_ecoule(tmp_path, monkeypatch):
    """
    Le travail produit réellement un fichier, nommé d'après le dernier mois ENTIÈREMENT écoulé —
    jamais le mois en cours, qui n'a pas fini de recevoir des lignes.
    """
    from aca.core import reporting

    monkeypatch.setattr(scheduler, "REPORT_DIR", str(tmp_path / "reports"))
    # Aucun canal configuré : `notify.send` retombe en silence, comme partout ailleurs.
    scheduler._job_report()

    annee, mois = reporting.last_completed_month()
    attendu = tmp_path / "reports" / f"rapport-{annee:04d}-{mois:02d}.pdf"
    assert attendu.exists()
    assert attendu.read_bytes()[:4] == b"%PDF"


def test_le_rapport_mensuel_ne_reecrit_pas_un_rapport_existant(tmp_path, monkeypatch):
    """
    Idempotence, même raison que `activity_log.archive_period` : si le travail repasse après une
    purge de rétention, réécrire remplacerait un rapport complet par un rapport amputé portant le
    même nom, sans que personne s'en aperçoive.
    """
    from aca.core import reporting

    dossier = tmp_path / "reports"
    monkeypatch.setattr(scheduler, "REPORT_DIR", str(dossier))
    annee, mois = reporting.last_completed_month()
    dossier.mkdir()
    existant = dossier / f"rapport-{annee:04d}-{mois:02d}.pdf"
    existant.write_bytes(b"%PDF-deja-la")

    scheduler._job_report()
    assert existant.read_bytes() == b"%PDF-deja-la"


def test_un_echec_de_rendu_est_signale_pas_avale(tmp_path, monkeypatch):
    """
    `build_report_pdf` ne lève jamais et renvoie `None`. Si le travail se contentait de sortir, le
    planificateur enregistrerait un passage « ok » et n'y reviendrait pas avant un mois : une
    absence de rapport passerait pour un rapport à jour. Il doit donc échouer bruyamment —
    `run_job` transforme cette exception en statut « error », sans faire tomber la boucle.
    """
    from aca.integrations import report_pdf

    monkeypatch.setattr(scheduler, "REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(report_pdf, "build_report_pdf", lambda *a, **kw: None)

    with pytest.raises(RuntimeError):
        scheduler._job_report()
    # Aucun fichier vide laissé derrière : sinon l'idempotence de `write_pdf` le prendrait pour un
    # rapport déjà produit et bloquerait définitivement les tentatives suivantes.
    assert not (tmp_path / "reports").exists() or not list((tmp_path / "reports").iterdir())

    # Et le planificateur, lui, ne meurt pas : il enregistre l'échec et repart.
    assert scheduler.run_job("report", time.time()) is False
    assert schedule_store.get_last_run("report") is not None


def test_une_notification_impossible_ne_transforme_pas_le_rapport_en_echec(tmp_path, monkeypatch):
    """
    Le rapport EXISTE : ne pas avoir su prévenir ne doit pas faire passer le travail pour un échec,
    ce qui déclencherait une nouvelle tentative de génération à chaque tick.
    """
    from aca.integrations import notify

    monkeypatch.setattr(scheduler, "REPORT_DIR", str(tmp_path / "reports"))

    def _explose(*_args, **_kwargs):
        raise RuntimeError("Slack injoignable")

    monkeypatch.setattr(notify, "send", _explose)
    scheduler._job_report()  # ne lève pas
    assert list((tmp_path / "reports").iterdir())

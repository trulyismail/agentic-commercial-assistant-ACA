"""
Tests du registre des tâches datées (§19, `aca/storage/task_store.py`).

Priorité, dans cet ordre — elle découle de ce que ces tâches font réellement, à savoir **envoyer
un e-mail à un prospect en l'absence de son auteur** :

1. **Une tâche annulée ne s'exécute JAMAIS.** C'est la propriété la plus importante du module : si
   une annulation humaine pouvait être écrasée par le planificateur, un message partirait après
   qu'une personne a explicitement dit non. La garde vit dans la clause SQL `status = 'pending'`,
   donc elle est atomique ; ces tests l'attaquent dans les deux sens.
2. **Seules les tâches échues sont rendues** par `list_due`, sinon un envoi programmé pour la
   semaine prochaine partirait au prochain tick.
3. **Le cloisonnement par tenant** tient, comme pour tous les autres registres locaux.
4. **La purge épargne les tâches en attente**, quelle que soit leur ancienneté : une échéance
   lointaine reste une intention valide, et l'effacer ferait disparaître un envoi qu'on croit
   programmé.
"""
from datetime import datetime

import pytest

from aca.storage import task_store

_T0 = datetime(2026, 8, 4, 9, 0).timestamp()   # mardi 4 août 2026, 09:00


@pytest.fixture(autouse=True)
def _base_neuve(tmp_path, monkeypatch):
    """
    Base neuve par test (même procédé que `_clean_schedule_db` dans `test_scheduler.py`).

    `conftest.py` redirige déjà `ACA_TASK_DB` vers un dossier temporaire, mais **pour la session
    entière** : tous les tests du fichier partageaient donc une seule base. Les cas d'origine ne
    s'en apercevaient pas — ils n'affirment que sur les lignes qu'ils viennent de créer — mais tout
    test portant sur le CONTENU GLOBAL d'une liste (« aucun rappel à afficher ») héritait des
    lignes des tests précédents. Isoler ici plutôt que contraindre chaque test à filtrer.
    """
    monkeypatch.setattr(task_store, "DB_PATH", str(tmp_path / "tasks.sqlite"))
    task_store.init_db()


# ── Création ─────────────────────────────────────────────────────────────────────────────────
def test_schedule_send_cree_une_tache_en_attente():
    task_id = task_store.schedule_send(
        _T0, thread_id="t-1", gmail_draft_id="draft-1", label="Entreprise Exemple",
        created_by="operator",
    )
    task = task_store.get_task(task_id)
    assert task["kind"] == task_store.KIND_SEND
    assert task["status"] == task_store.STATUS_PENDING
    assert task["gmail_draft_id"] == "draft-1"
    assert task["due_at"] == "2026-08-04 09:00:00"
    assert task["created_by"] == "operator"


def test_add_reminder_conserve_la_note():
    task_id = task_store.add_reminder(_T0, "Rappeler Dupont", thread_id="t-2")
    task = task_store.get_task(task_id)
    assert task["kind"] == task_store.KIND_REMINDER
    assert task["note"] == "Rappeler Dupont"


def test_une_nature_inconnue_est_refusee():
    # Silencieusement acceptée, elle produirait une tâche que le planificateur n'exécute jamais :
    # « en attente » pour toujours, sans que personne comprenne pourquoi.
    with pytest.raises(ValueError):
        task_store.add_task("carte_postale", _T0)


def test_les_libelles_lisibles_accompagnent_chaque_tache():
    task_id = task_store.schedule_send(_T0, thread_id="t-3", gmail_draft_id="d")
    task = task_store.get_task(task_id)
    assert task["kind_label"] == "Envoi programmé"
    assert task["status_label"] == "En attente"


# ── Échéance ─────────────────────────────────────────────────────────────────────────────────
def test_list_due_ne_rend_que_les_taches_echues():
    task_store.schedule_send(_T0, thread_id="passe", gmail_draft_id="d1")
    task_store.schedule_send(_T0 + 7 * 86400, thread_id="futur", gmail_draft_id="d2")

    threads = {t["thread_id"] for t in task_store.list_due(_T0 + 60)}
    assert "passe" in threads
    assert "futur" not in threads


def test_list_due_ignore_les_taches_deja_traitees():
    task_id = task_store.schedule_send(_T0, thread_id="t-done", gmail_draft_id="d")
    task_store.mark_done(task_id, "envoyé")
    assert all(t["id"] != task_id for t in task_store.list_due(_T0 + 60))


def test_list_pending_est_filtrable_par_nature_et_par_lead():
    task_store.schedule_send(_T0, thread_id="lead-a", gmail_draft_id="d")
    task_store.add_reminder(_T0, "note", thread_id="lead-a")
    task_store.add_reminder(_T0, "autre", thread_id="lead-b")

    assert len(task_store.list_pending(thread_id="lead-a")) == 2
    assert len(task_store.list_pending(thread_id="lead-a", kind=task_store.KIND_SEND)) == 1
    assert len(task_store.list_pending(kind=task_store.KIND_REMINDER, thread_id="lead-b")) == 1


# ── Annulation : la propriété qui compte le plus ─────────────────────────────────────────────
def test_une_tache_annulee_disparait_des_taches_a_executer():
    task_id = task_store.schedule_send(_T0, thread_id="t-cancel", gmail_draft_id="d")
    assert task_store.cancel(task_id) is True
    assert all(t["id"] != task_id for t in task_store.list_due(_T0 + 60))


def test_une_tache_annulee_ne_peut_plus_etre_marquee_effectuee():
    """
    Le scénario réel : l'humain annule pendant que le planificateur traite le lot. Sans la clause
    `status = 'pending'` de `_set_status`, la tâche repasserait à « effectué » — autrement dit
    l'e-mail partirait après une annulation explicite.
    """
    task_id = task_store.schedule_send(_T0, thread_id="t-race", gmail_draft_id="d")
    task_store.cancel(task_id)

    assert task_store.mark_done(task_id, "envoyé") is False
    assert task_store.get_task(task_id)["status"] == task_store.STATUS_CANCELLED


def test_annuler_deux_fois_ne_ment_pas():
    task_id = task_store.schedule_send(_T0, thread_id="t-twice", gmail_draft_id="d")
    assert task_store.cancel(task_id) is True
    assert task_store.cancel(task_id) is False   # déjà annulée : rien n'a changé


def test_cancel_for_thread_annule_tout_le_lead():
    task_store.schedule_send(_T0, thread_id="oubli", gmail_draft_id="d")
    task_store.add_reminder(_T0, "note", thread_id="oubli")
    task_store.add_reminder(_T0, "autre lead", thread_id="reste")

    assert task_store.cancel_for_thread("oubli", "Effacement RGPD") == 2
    assert task_store.list_pending(thread_id="oubli") == []
    assert len(task_store.list_pending(thread_id="reste")) == 1


# ── Échec ────────────────────────────────────────────────────────────────────────────────────
def test_mark_failed_conserve_la_raison():
    task_id = task_store.schedule_send(_T0, thread_id="t-fail", gmail_draft_id="d")
    task_store.mark_failed(task_id, "Brouillon introuvable")
    task = task_store.get_task(task_id)
    assert task["status"] == task_store.STATUS_FAILED
    assert "introuvable" in task["detail"]


# ── Cloisonnement par tenant ─────────────────────────────────────────────────────────────────
def test_les_taches_sont_cloisonnees_par_tenant():
    task_store.schedule_send(_T0, thread_id="t-acme", gmail_draft_id="d", org_id="acme")
    task_store.schedule_send(_T0, thread_id="t-globex", gmail_draft_id="d", org_id="globex")

    assert [t["thread_id"] for t in task_store.list_pending(org_id="acme")] == ["t-acme"]
    assert task_store.list_due(_T0 + 60, org_id="globex")[0]["thread_id"] == "t-globex"


# ── Rappels échus affichés dans l'application (§19) ──────────────────────────────────────────
def test_un_rappel_echu_reste_affiche_meme_une_fois_notifie():
    """
    La propriété qui fait exister `acknowledged_at` : le planificateur passe le rappel à
    « effectué » dès qu'il l'a poussé vers Slack, dans la minute suivant l'échéance. Si l'affichage
    se fiait à `status`, le rappel disparaîtrait de l'écran avant que quiconque l'ait lu.
    """
    passe = datetime.now().timestamp() - 60
    task_id = task_store.add_reminder(passe, "Rappeler Dupont", thread_id="t-1")

    task_store.mark_done(task_id, "Rappel notifié (Slack ou e-mail).")

    encore_affiches = task_store.list_due_reminders(datetime.now().timestamp())
    assert [r["id"] for r in encore_affiches] == [task_id]


def test_un_rappel_acquitte_disparait():
    passe = datetime.now().timestamp() - 60
    task_id = task_store.add_reminder(passe, "Rappeler Dupont", thread_id="t-1")

    assert task_store.acknowledge(task_id) is True
    assert task_store.list_due_reminders(datetime.now().timestamp()) == []
    # `status` n'est pas écrasé : savoir si la notification avait réellement abouti reste lisible.
    assert task_store.get_task(task_id)["status"] == task_store.STATUS_PENDING
    assert task_store.get_task(task_id)["acknowledged_at"]


def test_un_rappel_futur_ou_annule_nest_pas_affiche():
    futur = datetime.now().timestamp() + 3600
    passe = datetime.now().timestamp() - 60
    task_store.add_reminder(futur, "Plus tard", thread_id="t-futur")
    annule = task_store.add_reminder(passe, "Annulé", thread_id="t-annule")
    task_store.cancel(annule)

    assert task_store.list_due_reminders(datetime.now().timestamp()) == []


def test_un_envoi_programme_nest_jamais_pris_pour_un_rappel():
    passe = datetime.now().timestamp() - 60
    task_store.schedule_send(passe, thread_id="t-envoi", gmail_draft_id="d-1")

    assert task_store.list_due_reminders(datetime.now().timestamp()) == []


# ── Purge ────────────────────────────────────────────────────────────────────────────────────
def test_la_purge_epargne_les_taches_encore_en_attente():
    """
    Une échéance ancienne mais toujours en attente ne doit pas disparaître : c'est un envoi que
    quelqu'un croit programmé, et l'effacer le ferait échouer sans laisser de trace.
    """
    vieille_epoch = datetime.now().timestamp() - 400 * 86400
    en_attente = task_store.schedule_send(vieille_epoch, thread_id="t-old", gmail_draft_id="d")
    terminee = task_store.schedule_send(vieille_epoch, thread_id="t-old2", gmail_draft_id="d")
    task_store.mark_done(terminee, "envoyé")

    supprimees = task_store.purge_older_than(365)

    assert supprimees == 1
    assert task_store.get_task(en_attente) is not None
    assert task_store.get_task(terminee) is None

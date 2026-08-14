"""
Tests du registre des demandes de relecture (§20, `aca/storage/review_store.py`).

Priorité, dans cet ordre — elle découle de ce que ce registre porte réellement, à savoir **du
travail qu'une personne a transféré à une autre** :

1. **Une demande atteint son destinataire, et personne d'autre.** C'est la propriété qui fait
   exister la fonctionnalité : si `list_for` se trompait, soit l'administrateur ne verrait jamais
   ce qu'on lui adresse (l'opérateur attendrait indéfiniment une réponse), soit un opérateur
   verrait les demandes destinées à quelqu'un d'autre. Les deux erreurs sont graves, en sens
   opposés.
2. **Le lot est réellement un lot** : un geste, plusieurs e-mails, un `batch_id` commun — et une
   réponse groupée possible.
3. **Une demande déjà tranchée ne peut pas l'être une seconde fois.** La garde vit dans la clause
   SQL `status = 'pending'`, donc elle est atomique ; deux administrateurs ouvrant la même file au
   même moment ne doivent pas s'écraser l'un l'autre.
4. **« Vu » et « traité » restent distincts** : consulter une demande ne la fait pas disparaître de
   la file, sinon une demande ouverte puis oubliée serait perdue pour tout le monde.
5. **Le cloisonnement par tenant** tient, comme pour tous les autres registres locaux.
6. **La purge épargne les demandes en attente**, quelle que soit leur ancienneté : une relecture
   oubliée depuis un an reste une relecture due.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from aca.storage import review_store, user_store

_LEADS = [
    {"thread_id": "t-1", "subject": "Devis urgent", "sender": "p1@exemple.fr",
     "classification": "DEVIS"},
    {"thread_id": "t-2", "subject": "Demande de démo", "sender": "p2@exemple.fr",
     "classification": "DEMANDE_DEMO"},
    {"thread_id": "t-3", "subject": "Renouvellement", "sender": "p3@exemple.fr",
     "classification": "DEVIS"},
]


@pytest.fixture(autouse=True)
def _base_neuve(tmp_path, monkeypatch):
    """Base neuve par test — même procédé que `test_task_store.py`, et pour la même raison : les
    assertions portent ici sur le CONTENU d'une liste, pas seulement sur les lignes créées."""
    monkeypatch.setattr(review_store, "DB_PATH", str(tmp_path / "reviews.sqlite"))
    review_store.init_db()


# ── Création groupée ─────────────────────────────────────────────────────────────────────────
def test_un_geste_cree_une_ligne_par_email_avec_un_lot_commun():
    result = review_store.create_batch(_LEADS, requester="marie", note="Second avis SVP")

    assert result["count"] == 3
    rows = review_store.list_for("admin", user_store.ROLE_ADMIN)
    assert len(rows) == 3
    assert {row["batch_id"] for row in rows} == {result["batch_id"]}
    assert {row["thread_id"] for row in rows} == {"t-1", "t-2", "t-3"}
    # La note et la priorité appartiennent au geste, pas à chaque e-mail.
    assert {row["note"] for row in rows} == {"Second avis SVP"}


def test_les_metadonnees_de_lemail_sont_recopiees_pas_referencees():
    """
    L'objet et l'expéditeur sont copiés dans la demande exprès : le destinataire doit pouvoir lire
    de quoi il s'agit même si le lead a été purgé entre-temps par la rétention RGPD. Une demande
    dont l'intitulé s'évapore ne peut plus être ni comprise ni close.
    """
    review_store.create_batch(_LEADS[:1], requester="marie")
    row = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]
    assert row["subject"] == "Devis urgent"
    assert row["sender"] == "p1@exemple.fr"
    assert row["classification"] == "DEVIS"


def test_lot_vide_nest_pas_une_erreur_mais_necrit_rien():
    """Cliquer « Transmettre » sans avoir coché de ligne est une maladresse, pas une panne."""
    result = review_store.create_batch([], requester="marie")
    assert result["count"] == 0
    assert result["batch_id"] == ""
    assert review_store.list_for("admin", user_store.ROLE_ADMIN) == []


def test_demande_sans_auteur_est_refusee():
    """Une demande anonyme serait inexploitable : à qui répondre, qui relancer ?"""
    with pytest.raises(ValueError):
        review_store.create_batch(_LEADS, requester="   ")


def test_priorite_inconnue_retombe_sur_normale():
    """Valeur libre venue d'un formulaire : on normalise plutôt que d'écrire une priorité que le
    tri ne saurait pas ordonner."""
    review_store.create_batch(_LEADS[:1], requester="marie", priority="URGENTISSIME")
    assert (review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["priority"]
            == review_store.PRIORITY_NORMAL)


# ── Destination ──────────────────────────────────────────────────────────────────────────────
def test_une_demande_aux_administrateurs_atteint_tout_administrateur():
    """
    Le destinataire `@admins` désigne la FONCTION, pas la liste des comptes existant à l'instant de
    l'envoi : un administrateur recruté demain doit voir ce qui a été adressé à sa fonction hier.
    """
    review_store.create_batch(_LEADS[:1], requester="marie")
    assert len(review_store.list_for("admin", user_store.ROLE_ADMIN)) == 1
    assert len(review_store.list_for("un-autre-admin", user_store.ROLE_ADMIN)) == 1


def test_un_operateur_ne_voit_pas_les_demandes_adressees_aux_administrateurs():
    review_store.create_batch(_LEADS[:1], requester="marie")
    assert review_store.list_for("paul", user_store.ROLE_OPERATOR) == []


def test_une_demande_nominative_natteint_que_son_destinataire():
    review_store.create_batch(_LEADS[:1], requester="marie", recipient="chef")
    assert len(review_store.list_for("chef", user_store.ROLE_OPERATOR)) == 1
    # Même un administrateur ne récupère pas une demande nommément adressée à quelqu'un d'autre :
    # sans quoi « adresser à une personne » ne voudrait rien dire.
    assert review_store.list_for("admin", user_store.ROLE_ADMIN) == []


def test_les_demandes_prioritaires_remontent_en_tete():
    review_store.create_batch(_LEADS[:1], requester="marie")
    review_store.create_batch(_LEADS[1:2], requester="paul",
                              priority=review_store.PRIORITY_HIGH)
    rows = review_store.list_for("admin", user_store.ROLE_ADMIN)
    assert rows[0]["thread_id"] == "t-2"


# ── Clôture ──────────────────────────────────────────────────────────────────────────────────
def test_traiter_retire_de_la_file_et_conserve_la_reponse():
    review_store.create_batch(_LEADS[:1], requester="marie")
    request_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["id"]

    assert review_store.resolve(request_id, "admin", "Vu, allez-y") is True
    assert review_store.list_for("admin", user_store.ROLE_ADMIN) == []

    closed = review_store.get_request(request_id)
    assert closed["status"] == review_store.STATUS_RESOLVED
    assert closed["resolved_by"] == "admin"
    assert closed["resolution_note"] == "Vu, allez-y"
    # L'auteur doit retrouver la réponse : c'est la moitié utile de l'échange.
    assert review_store.list_sent_by("marie")[0]["resolution_note"] == "Vu, allez-y"


def test_une_demande_deja_tranchee_ne_peut_pas_letre_une_seconde_fois():
    """
    Deux administrateurs peuvent ouvrir la même file au même moment. Sans la clause
    `status = 'pending'` portée par le SQL, le second écraserait la réponse du premier.
    """
    review_store.create_batch(_LEADS[:1], requester="marie")
    request_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["id"]

    assert review_store.resolve(request_id, "admin", "D'accord") is True
    assert review_store.dismiss(request_id, "autre-admin", "Pas concerné") is False
    assert review_store.get_request(request_id)["resolved_by"] == "admin"
    assert review_store.get_request(request_id)["resolution_note"] == "D'accord"


def test_ecarter_est_trace_pas_supprime():
    """Écarter une demande est une décision : elle doit rester lisible après coup."""
    review_store.create_batch(_LEADS[:1], requester="marie")
    request_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["id"]
    review_store.dismiss(request_id, "admin", "Cas standard")
    assert review_store.get_request(request_id)["status"] == review_store.STATUS_DISMISSED
    assert review_store.list_sent_by("marie")[0]["status_label"] == "Écartée"


def test_reponse_groupee_clot_tout_le_lot_dun_coup():
    result = review_store.create_batch(_LEADS, requester="marie")
    closed = review_store.resolve_batch(result["batch_id"], "admin", "Allez-y sur les trois")
    assert closed == 3
    assert review_store.list_for("admin", user_store.ROLE_ADMIN) == []


def test_reponse_groupee_epargne_ce_qui_etait_deja_tranche():
    """Une réponse groupée ne doit pas réécrire l'avis déjà donné sur un e-mail du lot."""
    result = review_store.create_batch(_LEADS, requester="marie")
    first = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]
    review_store.dismiss(first["id"], "admin", "Celui-ci, non")

    assert review_store.resolve_batch(result["batch_id"], "admin", "Les autres, oui") == 2
    assert review_store.get_request(first["id"])["status"] == review_store.STATUS_DISMISSED
    assert review_store.get_request(first["id"])["resolution_note"] == "Celui-ci, non"


# ── « Vu » ≠ « traité » ──────────────────────────────────────────────────────────────────────
def test_consulter_une_demande_ne_la_retire_pas_de_la_file():
    """
    Même distinction que `task_store.acknowledged_at` : une demande ouverte puis laissée de côté
    reste à traiter. La confondre avec une clôture ferait disparaître du travail que personne n'a
    fait.
    """
    review_store.create_batch(_LEADS[:1], requester="marie")
    request_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["id"]

    assert review_store.mark_seen(request_id) is True
    assert len(review_store.list_for("admin", user_store.ROLE_ADMIN)) == 1
    assert review_store.get_request(request_id)["status"] == review_store.STATUS_PENDING


def test_seule_la_premiere_consultation_est_horodatee():
    """`seen_at` répond à « quand l'a-t-on découverte », pas à « quand l'a-t-on rouverte »."""
    review_store.create_batch(_LEADS[:1], requester="marie")
    request_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["id"]
    review_store.mark_seen(request_id)
    first_seen = review_store.get_request(request_id)["seen_at"]

    assert review_store.mark_seen(request_id) is False
    assert review_store.get_request(request_id)["seen_at"] == first_seen


# ── Regroupement (fonction pure) ─────────────────────────────────────────────────────────────
def test_group_by_batch_regroupe_en_conservant_lordre():
    review_store.create_batch(_LEADS[:2], requester="marie")
    review_store.create_batch(_LEADS[2:], requester="paul")
    batches = review_store.group_by_batch(
        review_store.list_for("admin", user_store.ROLE_ADMIN))

    assert [len(batch["items"]) for batch in batches] == [2, 1]
    assert [batch["requester"] for batch in batches] == ["marie", "paul"]


# ── Cloisonnement par tenant ─────────────────────────────────────────────────────────────────
def test_cloisonnement_par_tenant(monkeypatch):
    review_store.create_batch(_LEADS[:1], requester="marie", org_id="acme")
    review_store.create_batch(_LEADS[1:], requester="paul", org_id="globex")

    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert len(review_store.list_for("admin", user_store.ROLE_ADMIN)) == 1
    monkeypatch.setenv("ACA_ORG_ID", "globex")
    assert len(review_store.list_for("admin", user_store.ROLE_ADMIN)) == 2


# ── Effacement et rétention ──────────────────────────────────────────────────────────────────
def test_effacement_rgpd_ecarte_les_demandes_du_lead():
    """
    Une demande en attente sur un prospect effacé conserverait son nom exactement là où on vient de
    le retirer, et l'afficherait à un administrateur le lendemain.
    """
    review_store.create_batch(_LEADS, requester="marie")
    assert review_store.cancel_for_thread("t-1") == 1
    remaining = review_store.list_for("admin", user_store.ROLE_ADMIN)
    assert {row["thread_id"] for row in remaining} == {"t-2", "t-3"}


def test_la_purge_epargne_les_demandes_en_attente():
    """
    Une relecture jamais traitée est du travail en souffrance : l'effacer par ancienneté ferait
    disparaître la trace d'un lead que personne n'a tranché, en même temps que la donnée.
    """
    review_store.create_batch(_LEADS[:1], requester="marie")
    review_store.create_batch(_LEADS[1:2], requester="marie")
    closed_id = review_store.list_for("admin", user_store.ROLE_ADMIN)[-1]["id"]
    review_store.resolve(closed_id, "admin")

    # Antidater les deux lignes de deux ans, directement en base : la purge raisonne sur
    # `created_at`, et attendre deux ans n'est pas une option.
    old = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(review_store.DB_PATH) as conn:
        conn.execute("UPDATE review_requests SET created_at = ?", (old,))
        conn.commit()

    assert review_store.purge_older_than(365) == 1  # la close seulement
    assert len(review_store.list_for("admin", user_store.ROLE_ADMIN)) == 1
    assert review_store.get_request(closed_id) is None


# ── Fenêtre temporelle (alimente les rapports) ───────────────────────────────────────────────
def test_list_between_borne_haute_exclue():
    """Deux périodes consécutives ne doivent pas compter deux fois la même demande."""
    review_store.create_batch(_LEADS[:1], requester="marie")
    created = review_store.list_for("admin", user_store.ROLE_ADMIN)[0]["created_at"]

    assert len(review_store.list_between("2000-01-01 00:00:00", "2999-01-01 00:00:00")) == 1
    # Borne haute posée sur l'instant exact de création : la ligne doit en être EXCLUE.
    assert review_store.list_between("2000-01-01 00:00:00", created) == []

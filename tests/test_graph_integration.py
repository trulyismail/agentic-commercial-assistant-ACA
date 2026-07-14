"""
Tests d'intégration du graphe complet (app.invoke) avec tous les LLM et intégrations externes
remplacés par des faux : vérifie le câblage réel des nœuds/arêtes — supervision, boucle de
réflexion, pause de validation humaine (interrupt_before=["action"]), reprise après « Valider » —
sans aucun appel réseau. Le checkpointer est le SqliteSaver temporaire du conftest.
"""
import uuid

from conftest import FakeLLM

import aca.core.app as app_module
from aca.core.app import app

EXTRACTION_JSON = '{"entreprise": "Test SA", "contact": "Jean", "urgence": "haute", "besoin_principal": "10 licences"}'


def _install_fast_llm(monkeypatch, classification="DEVIS", supervisor_replies=None, reflection_replies=None):
    """
    fast_llm est partagé par plusieurs nœuds (classifier, superviseur, reflect, décontextualisation) :
    ce faux route la réponse selon le prompt système reçu, et consomme des files de réponses pour
    les appels successifs du superviseur / du relecteur. Une SEULE instance est partagée par tous
    les appels `fast_llm()` du test (les files doivent avancer d'un appel à l'autre).
    """
    supervisor_replies = list(supervisor_replies or [])
    reflection_replies = list(reflection_replies or ["OK"])

    def reply(messages):
        system = messages[0].content
        if "Classe l'e-mail" in system:
            return classification
        if "SUPERVISEUR" in system:
            return supervisor_replies.pop(0) if supervisor_replies else "stratege"
        if "relecteur qualité" in system:
            return reflection_replies.pop(0) if reflection_replies else "OK"
        if "Reformule la DEMANDE" in system:
            return "requête reformulée"
        raise AssertionError(f"Prompt fast_llm inattendu : {system[:80]}")

    shared = FakeLLM(reply)
    monkeypatch.setattr(app_module, "fast_llm", lambda: shared)
    return shared


def _mock_integrations(monkeypatch):
    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", lambda sender: [])
    monkeypatch.setattr(app_module.sheets, "search_knowledge_base_semantic", lambda q: "- Q: tarifs\n  R: 50€/mois")
    monkeypatch.setattr(app_module.enrichment, "research_company", lambda sender: "")
    monkeypatch.setattr(app_module.veille, "search_faq_online", lambda q: "")
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: False)
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(EXTRACTION_JSON))
    monkeypatch.setattr(app_module, "creative_llm", lambda: FakeLLM("Proposition commerciale test."))


def _invoke(email, config):
    return app.invoke({"email_raw": email}, config=config)


EMAIL = {"sender": "jean@testsa.fr", "subject": "Devis 10 licences", "body": "Bonjour, un devis SVP."}


def test_devis_flow_pauses_before_action(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    output = _invoke(EMAIL, config)

    assert output["classification"] == "DEVIS"
    assert output["extracted_info"]["besoin_principal"] == "10 licences"
    assert output["completed_agents"] == ["connaissance", "stratege"]
    assert output["draft_response"].startswith("Proposition commerciale")
    # La preuve du Human-in-the-loop : le graphe est en pause juste avant l'écriture CRM.
    snapshot = app.get_state(config)
    assert snapshot.next == ("action",)
    assert "action_status" not in output or not output.get("action_status")


def test_resume_after_validation_runs_action(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    crm_writes = []
    monkeypatch.setattr(app_module.sheets, "append_lead", lambda **kwargs: crm_writes.append(kwargs))
    monkeypatch.setattr(app_module.hubspot, "create_lead", lambda **kwargs: None)

    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    _invoke(EMAIL, config)
    assert crm_writes == []  # rien n'est écrit avant la validation humaine

    # « Valider » dans l'UI = invoke(None, config) : le graphe reprend et exécute action_node.
    output = app.invoke(None, config=config)
    assert len(crm_writes) == 1
    assert crm_writes[0]["email_classification"] == "DEVIS"
    assert "Lead ajouté au CRM." in output["action_status"]
    assert app.get_state(config).next == ()  # graphe terminé


def test_reflection_rewrite_loop_capped_at_one(monkeypatch):
    _mock_integrations(monkeypatch)
    creative = FakeLLM("Proposition commerciale test.")
    monkeypatch.setattr(app_module, "creative_llm", lambda: creative)
    _install_fast_llm(
        monkeypatch,
        supervisor_replies=["stratege"],
        reflection_replies=["REWRITE: affirmation non étayée", "REWRITE: encore"],  # 2e ignoré (garde-fou)
    )
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    output = _invoke(EMAIL, config)

    assert output["completed_agents"].count("stratege") == 2  # une seule réécriture, pas de boucle infinie
    assert creative.calls == 2
    assert any("réécriture demandée" in r.lower() for r in output["reasoning_log"])
    assert any("garde-fou" in r.lower() for r in output["reasoning_log"])
    assert app.get_state(config).next == ("action",)  # pause de validation atteinte malgré tout


def test_spam_flow_skips_workers_and_notification(monkeypatch):
    _mock_integrations(monkeypatch)
    notified = []
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: notified.append(a) or False)
    _install_fast_llm(monkeypatch, classification="SPAM")

    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    output = _invoke(EMAIL, config)

    assert output["classification"] == "SPAM"
    assert output.get("completed_agents", []) == []      # aucun worker pour du spam
    assert not output.get("draft_response")              # pas de proposition
    assert notified == []                                # ni alerte de validation ni routage
    assert app.get_state(config).next == ("action",)     # pause quand même (l'UI n'affiche pas de bouton)


def test_veille_triggered_when_faq_empty(monkeypatch):
    _mock_integrations(monkeypatch)
    monkeypatch.setattr(app_module.sheets, "search_knowledge_base_semantic", lambda q: "")  # FAQ vide
    veille_calls = []
    monkeypatch.setattr(
        app_module.veille, "search_faq_online",
        lambda q: veille_calls.append(q) or "- Q: q\n  R: réponse web",
    )
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    config = {"configurable": {"thread_id": f"test-{uuid.uuid4()}"}}
    output = _invoke(EMAIL, config)

    # Garde-fou déterministe : FAQ vide après connaissance → veille forcée avant le stratège.
    assert output["completed_agents"] == ["connaissance", "veille", "stratege"]
    assert veille_calls == ["10 licences"]  # requête décontextualisée (besoin extrait), pas l'e-mail brut
    assert output["faq_context"] == "- Q: q\n  R: réponse web"

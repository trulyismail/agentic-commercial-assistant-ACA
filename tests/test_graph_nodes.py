"""
Tests unitaires des nœuds du graphe (aca/core/app.py) — chaque nœud est une fonction pure
(state -> dict partiel), testable sans exécuter le graphe complet. Tous les LLM sont remplacés
par des faux (cf. conftest.FakeLLM) : aucun appel réseau.
"""
import json

import pytest
from conftest import ExplodingLLM, FakeLLM

import aca.core.app as app_module
from aca.core.app import (
    CATEGORIES_SANS_SUITE,
    ExtractedInfo,
    _build_rag_query,
    _retry_on,
    clarification_node,
    classifier_node,
    connaissance_node,
    extractor_node,
    memory_lookup_node,
    notification_node,
    reflection_node,
    routing_node,
    stratege_node,
    supervisor_node,
    veille_node,
)

EMAIL = {"sender": "jean@entreprise.fr", "subject": "Devis", "body": "Bonjour, un devis SVP pour 10 licences."}


def _state(**overrides):
    base = {"email_raw": EMAIL, "classification": "DEVIS", "extracted_info": {}, "completed_agents": []}
    base.update(overrides)
    return base


# ── classifier_node ───────────────────────────────────────────────────────────────────────────
def test_classifier_valid_label(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("devis \n"))
    assert classifier_node(_state())["classification"] == "DEVIS"


def test_classifier_unknown_label_falls_back_to_autre(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("BANANE"))
    assert classifier_node(_state())["classification"] == "AUTRE"


# ── extractor_node (sortie structurée Pydantic) ──────────────────────────────────────────────
def test_extractor_returns_structured_fields(monkeypatch):
    payload = json.dumps({
        "entreprise": "Entreprise SA", "contact": "Jean Dupont",
        "urgence": "haute", "besoin_principal": "10 licences Enterprise",
    })
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(payload))
    out = extractor_node(_state())
    assert out["extracted_info"] == {
        "entreprise": "Entreprise SA", "contact": "Jean Dupont",
        "urgence": "haute", "besoin_principal": "10 licences Enterprise",
    }


def test_extractor_missing_fields_become_none(monkeypatch):
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(json.dumps({})))
    out = extractor_node(_state())
    assert out["extracted_info"] == {
        "entreprise": None, "contact": None, "urgence": None, "besoin_principal": None,
    }


def test_extractor_graceful_fallback_on_structured_output_failure(monkeypatch):
    # Sortie non conforme au schéma (urgence hors énum "haute|moyenne|basse") -> ValidationError
    # côté Pydantic, absorbée : plus de plantage de app.invoke(), champs vides comme un e-mail vague.
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(json.dumps({"urgence": "extreme"})))
    out = extractor_node(_state())
    assert out["extracted_info"] == ExtractedInfo().model_dump()


# ── memory_lookup_node ───────────────────────────────────────────────────────────────────────
def test_memory_lookup_new_contact(monkeypatch):
    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", lambda sender: [])
    out = memory_lookup_node(_state())
    assert out == {"sender_history": "", "is_duplicate": False}


def test_memory_lookup_returning_customer(monkeypatch):
    previous = [{"Date": "2026-07-01", "Besoin": "50 licences"}]
    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", lambda sender: previous)
    out = memory_lookup_node(_state())
    assert out["is_duplicate"] is True
    assert "50 licences" in out["sender_history"]


# ── clarification_node (branches sans interrupt) ────────────────────────────────────────────
@pytest.mark.parametrize("categorie", sorted(CATEGORIES_SANS_SUITE))
def test_clarification_skipped_for_sans_suite(categorie):
    assert clarification_node(_state(classification=categorie)) == {}


def test_clarification_skipped_when_besoin_present():
    state = _state(extracted_info={"besoin_principal": "10 licences"})
    assert clarification_node(state) == {}


# ── supervisor_node (garde-fous déterministes) ───────────────────────────────────────────────
def test_supervisor_finish_for_sans_suite(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    out = supervisor_node(_state(classification="SPAM"))
    assert out["next_agent"] == "FINISH"


def test_supervisor_forces_veille_when_faq_empty(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    state = _state(completed_agents=["connaissance"], faq_context="")
    assert supervisor_node(state)["next_agent"] == "veille"


def test_supervisor_forces_stratege_when_helpers_done(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    state = _state(completed_agents=["enrichissement", "connaissance"], faq_context="- Q: x\n  R: y")
    assert supervisor_node(state)["next_agent"] == "stratege"


def test_supervisor_never_reoffers_completed_agent(monkeypatch):
    # Le LLM répond un agent déjà exécuté (non proposé) → repli déterministe sur la 1re option.
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("connaissance"))
    state = _state(completed_agents=["connaissance"], faq_context="- Q: x\n  R: y")
    assert supervisor_node(state)["next_agent"] == "enrichissement"


def test_supervisor_llm_choice_respected(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("Je choisis : connaissance"))
    out = supervisor_node(_state())
    assert out["next_agent"] == "connaissance"


# ── reflection_node (auto-critique + garde-fou anti-boucle) ──────────────────────────────────
def test_reflection_ok_continues(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("OK"))
    state = _state(completed_agents=["stratege"], draft_response="Bonjour.", faq_context="")
    out = reflection_node(state)
    assert out["next_agent"] == "ok"
    assert out["reflection_feedback"] == ""


def test_reflection_rewrite_sends_back_with_reason(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("REWRITE: prix non présent dans la FAQ"))
    state = _state(completed_agents=["stratege"], draft_response="C'est 10€.", faq_context="- Q: x\n  R: y")
    out = reflection_node(state)
    assert out["next_agent"] == "rewrite"
    assert out["reflection_feedback"] == "prix non présent dans la FAQ"


def test_reflection_anti_loop_guard_skips_llm(monkeypatch):
    # 2e passage du stratège : le brouillon passe tel quel, SANS appel LLM (ExplodingLLM le prouve).
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    state = _state(completed_agents=["stratege", "stratege"], draft_response="Bonjour.")
    out = reflection_node(state)
    assert out["next_agent"] == "ok"
    assert out["reflection_feedback"] == ""


# ── _build_rag_query (décontextualisation) ───────────────────────────────────────────────────
def test_rag_query_uses_besoin_without_llm(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    state = _state(extracted_info={"besoin_principal": "Tarifs pour 20 utilisateurs"}, sender_history="")
    assert _build_rag_query(state) == "Tarifs pour 20 utilisateurs"


@pytest.mark.parametrize("besoin", [None, "", "null", "None", "n/a"])
def test_rag_query_falls_back_to_raw_email(monkeypatch, besoin):
    monkeypatch.setattr(app_module, "fast_llm", lambda: ExplodingLLM())
    state = _state(extracted_info={"besoin_principal": besoin}, sender_history="")
    assert _build_rag_query(state) == f"{EMAIL['subject']} {EMAIL['body']}"


def test_rag_query_rewrites_for_returning_customer(monkeypatch):
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("Délais de livraison de l'option Enterprise"))
    state = _state(
        extracted_info={"besoin_principal": "et pour cette option-là ?"},
        sender_history="A déjà commandé l'option Enterprise.",
    )
    assert _build_rag_query(state) == "Délais de livraison de l'option Enterprise"


def test_rag_query_llm_failure_falls_back_to_besoin(monkeypatch):
    class _FailingLLM:
        def invoke(self, messages):
            raise RuntimeError("panne réseau simulée")

    monkeypatch.setattr(app_module, "fast_llm", lambda: _FailingLLM())
    state = _state(
        extracted_info={"besoin_principal": "et pour cette option-là ?"},
        sender_history="A déjà commandé l'option Enterprise.",
    )
    assert _build_rag_query(state) == "et pour cette option-là ?"


# ── connaissance_node (zone ambre) / veille_node ─────────────────────────────────────────────
def test_connaissance_strips_low_confidence_marker(monkeypatch):
    marked = f"{app_module.sheets.LOW_CONFIDENCE_MARKER}\n- Q: x\n  R: y"
    monkeypatch.setattr(app_module.sheets, "search_knowledge_base_semantic", lambda q: marked)
    state = _state(extracted_info={"besoin_principal": "tarifs"}, sender_history="")
    out = connaissance_node(state)
    assert app_module.sheets.LOW_CONFIDENCE_MARKER not in out["faq_context"]
    assert out["faq_context"].startswith("- Q: x")
    assert any("confiance modérée" in r for r in out["reasoning_log"])


def test_connaissance_normal_match(monkeypatch):
    monkeypatch.setattr(app_module.sheets, "search_knowledge_base_semantic", lambda q: "- Q: x\n  R: y")
    state = _state(extracted_info={"besoin_principal": "tarifs"}, sender_history="")
    out = connaissance_node(state)
    assert out["faq_context"] == "- Q: x\n  R: y"
    assert out["completed_agents"] == ["connaissance"]


def test_veille_uses_shared_rag_query(monkeypatch):
    captured = {}

    def fake_search(query):
        captured["query"] = query
        return "- Q: q\n  R: r"

    monkeypatch.setattr(app_module.veille, "search_faq_online", fake_search)
    state = _state(extracted_info={"besoin_principal": "intégration Salesforce"}, sender_history="")
    out = veille_node(state)
    assert captured["query"] == "intégration Salesforce"
    assert out["faq_context"] == "- Q: q\n  R: r"
    assert out["completed_agents"] == ["veille"]


# ── stratege_node (lien Calendly déterministe + feedback de réécriture) ──────────────────────
def test_stratege_appends_calendly_for_demo(monkeypatch):
    monkeypatch.setattr(app_module, "creative_llm", lambda: FakeLLM("Bonjour, avec plaisir."))
    monkeypatch.setattr(app_module, "CALENDLY_URL", "https://calendly.example/demo")
    out = stratege_node(_state(classification="DEMANDE_DEMO"))
    assert out["draft_response"].endswith("https://calendly.example/demo")


def test_stratege_no_calendly_for_devis(monkeypatch):
    monkeypatch.setattr(app_module, "creative_llm", lambda: FakeLLM("Bonjour, avec plaisir."))
    monkeypatch.setattr(app_module, "CALENDLY_URL", "https://calendly.example/demo")
    out = stratege_node(_state(classification="DEVIS"))
    assert "calendly" not in out["draft_response"].lower()


def test_stratege_injects_reflection_feedback_in_prompt(monkeypatch):
    fake = FakeLLM("Version corrigée.")
    monkeypatch.setattr(app_module, "creative_llm", lambda: fake)
    stratege_node(_state(reflection_feedback="retirer le prix non vérifié"))
    system_prompt = fake.last_messages[0].content
    assert "retirer le prix non vérifié" in system_prompt


def test_stratege_prompt_clean_without_feedback(monkeypatch):
    fake = FakeLLM("Proposition.")
    monkeypatch.setattr(app_module, "creative_llm", lambda: fake)
    stratege_node(_state())
    assert "CORRECTION DEMANDÉE" not in fake.last_messages[0].content


# ── routing_node / notification_node ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("categorie", ["DEVIS", "DEMANDE_DEMO", "SPAM"])
def test_routing_noop_for_unrouted_categories(categorie):
    assert routing_node(_state(classification=categorie)) == {}


def test_routing_support_logs_missing_channel(monkeypatch):
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: False)
    out = routing_node(_state(classification="SUPPORT"))
    assert any("aucun canal" in r.lower() for r in out["reasoning_log"])


def test_routing_support_alert_sent(monkeypatch):
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: True)
    out = routing_node(_state(classification="SUPPORT"))
    assert any("alerte envoyée" in r.lower() for r in out["reasoning_log"])


def test_notification_skipped_for_sans_suite(monkeypatch):
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert notification_node(_state(classification="SPAM")) == {}


def test_notification_sent_for_lead(monkeypatch):
    monkeypatch.setattr(app_module.notify, "send", lambda message: True)
    out = notification_node(_state(classification="DEVIS"))
    assert out["reasoning_log"] == ["Notification envoyée."]


# ── _retry_on (politique de retry) ───────────────────────────────────────────────────────────
def test_retry_on_429():
    class _Resp:
        status_code = 429

    exc = Exception("rate limited")
    exc.response = _Resp()
    assert _retry_on(exc) is True


def test_no_retry_on_programming_error():
    assert _retry_on(ValueError("bug")) is False

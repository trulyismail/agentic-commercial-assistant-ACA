"""
Tests du microservice FastAPI (§12 item 6 — port n8n) : aca/api.py. Mêmes faux LLM/intégrations que
test_graph_integration.py (le microservice n'est qu'une façade HTTP sur le graphe déjà testé) —
vérifie ici le câblage HTTP lui-même : démarrage d'un thread, clarification dynamique, pause de
validation, et que la validation reste le seul point d'entrée qui déclenche l'écriture CRM.
"""
import json
import uuid

from conftest import FakeLLM
from fastapi.testclient import TestClient

import aca.core.app as app_module
from aca.api import api

client = TestClient(api)

EXTRACTION_JSON = '{"entreprise": "Test SA", "contact": "Jean", "urgence": "haute", "besoin_principal": "10 licences"}'
EXTRACTION_JSON_NO_BESOIN = '{"entreprise": "Test SA", "contact": "Jean", "urgence": "haute", "besoin_principal": null}'


def _install_fast_llm(monkeypatch, classification="DEVIS", confidence=0.9, supervisor_replies=None):
    supervisor_replies = list(supervisor_replies or [])

    def reply(messages):
        system = messages[0].content
        if "Classe l'e-mail" in system:
            return json.dumps({"categorie": classification, "confiance": confidence})
        if "SUPERVISEUR" in system:
            return supervisor_replies.pop(0) if supervisor_replies else "stratege"
        if "relecteur qualité" in system:
            return "OK"
        if "Reformule la DEMANDE" in system:
            return "requête reformulée"
        raise AssertionError(f"Prompt fast_llm inattendu : {system[:80]}")

    shared = FakeLLM(reply)
    monkeypatch.setattr(app_module, "fast_llm", lambda: shared)
    return shared


def _mock_integrations(monkeypatch, extraction_json=EXTRACTION_JSON):
    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", lambda sender: [])
    monkeypatch.setattr(app_module.sheets, "search_knowledge_base_semantic", lambda q: "- Q: tarifs\n  R: 50€/mois")
    monkeypatch.setattr(app_module.enrichment, "research_company", lambda sender: "")
    monkeypatch.setattr(app_module.veille, "search_faq_online", lambda q: "")
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: False)
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(extraction_json))
    monkeypatch.setattr(app_module, "creative_llm", lambda: FakeLLM("Proposition commerciale test."))
    monkeypatch.setattr(app_module.sheets, "append_lead", lambda **k: None)
    monkeypatch.setattr(app_module.hubspot, "create_lead", lambda **k: None)


EMAIL_PAYLOAD = {"sender": "jean@testsa.fr", "subject": "Devis 10 licences", "body": "Bonjour, un devis SVP."}


def test_create_thread_pauses_before_validation(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])

    resp = client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": f"api-test-{uuid.uuid4()}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "DEVIS"
    assert body["draft_response"].startswith("Proposition commerciale")
    assert body["pending_clarification"] is None
    assert body["awaiting_validation"] is True
    assert body["done"] is False


def test_validate_thread_runs_action_and_completes(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    thread_id = f"api-test-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    resp = client.post(f"/threads/{thread_id}/valider", json={"validated_by": "Alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] is True
    assert "Lead ajouté au CRM" in body["action_status"]


def test_validate_thread_uses_edited_draft(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    thread_id = f"api-test-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    resp = client.post(f"/threads/{thread_id}/valider", json={"edited_draft": "Version corrigée par un humain."})
    assert resp.status_code == 200
    assert resp.json()["draft_response"] == "Version corrigée par un humain."


def test_validate_thread_rejects_when_not_awaiting(monkeypatch):
    thread_id = f"api-test-nonexistent-{uuid.uuid4()}"
    resp = client.post(f"/threads/{thread_id}/valider", json={})
    assert resp.status_code == 400


def test_clarification_flow_end_to_end(monkeypatch):
    _mock_integrations(monkeypatch, extraction_json=EXTRACTION_JSON_NO_BESOIN)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    thread_id = f"api-test-{uuid.uuid4()}"

    resp = client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})
    body = resp.json()
    assert body["pending_clarification"] is not None
    assert body["awaiting_validation"] is False

    resp = client.post(f"/threads/{thread_id}/clarifier", json={"answer": "Le client veut 20 licences Pro."})
    body = resp.json()
    assert body["pending_clarification"] is None
    assert body["awaiting_validation"] is True
    assert body["extracted_info"]["besoin_principal"] == "Le client veut 20 licences Pro."


def test_clarify_thread_rejects_when_nothing_pending(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    thread_id = f"api-test-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})  # pas de clarification (besoin présent)

    resp = client.post(f"/threads/{thread_id}/clarifier", json={"answer": "peu importe"})
    assert resp.status_code == 400


# ── /metrics (§12 item 9 : observabilité Prometheus) ─────────────────────────────────────────
def test_metrics_endpoint_exposes_prometheus_format(monkeypatch):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["connaissance", "stratege"])
    thread_id = f"api-test-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})
    client.post(f"/threads/{thread_id}/valider", json={})

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "aca_emails_classified_total" in body
    assert "aca_leads_validated_total" in body
    assert 'classification="DEVIS"' in body

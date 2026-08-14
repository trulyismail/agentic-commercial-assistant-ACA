"""
Tests des capacités API ajoutées pour rendre le port n8n réellement praticable (§16.1).

Ce que ces tests verrouillent, et pourquoi chacun existe :
- **Pièces jointes** (§16.1.1) — `POST /threads` codait `attachments_raw: []` en dur, rendant
  l'analyse multimodale (pilier d'innovation n°1) inatteignable depuis n8n.
- **`GET /health`** (§16.1.3) — requis par le healthcheck Docker et la branche d'erreur n8n ;
  doit rester booléen (jamais un secret) et ne joindre aucun service externe.
- **Idempotence** (§16.1.4) — le nœud HTTP de n8n réessaie par défaut ; sans garde, un réessai
  relançait une analyse complète et renotifiait l'équipe.
- **Mode asynchrone** (§16.1.4) — évite de retenir une requête HTTP 30 à 90 s.
- **Webhooks** (§16.1.2) — émis aux bons moments, avec la même forme de charge utile que l'API.

Volontairement séparé de `test_api.py`, qui reste la base de non-régression du §12/§15 (ordre des
routes, HMAC Slack, garde par clé, limite de débit). Mêmes faux LLM/intégrations : aucun appel
réseau, aucun LLM réel.
"""
import base64
import json
import uuid

import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient

import aca.core.app as app_module
from aca.api import api
from aca.integrations import webhook

client = TestClient(api)

EXTRACTION_JSON = (
    '{"entreprise": "Test SA", "contact": "Jean", "urgence": "haute", '
    '"besoin_principal": "10 licences"}'
)
EMAIL_PAYLOAD = {
    "sender": "jean@testsa.fr", "subject": "Devis 10 licences", "body": "Bonjour, un devis SVP.",
}


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


def _mock_integrations(monkeypatch):
    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", lambda sender: [])
    monkeypatch.setattr(
        app_module.sheets, "search_knowledge_base_semantic", lambda q: "- Q: tarifs\n  R: 50€/mois",
    )
    monkeypatch.setattr(app_module.enrichment, "research_company", lambda sender: "")
    monkeypatch.setattr(app_module.veille, "search_faq_online", lambda q: "")
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: False)
    monkeypatch.setattr(app_module.notify, "send_approval", lambda *a, **k: False)
    monkeypatch.setattr(app_module, "smart_llm", lambda: FakeLLM(EXTRACTION_JSON))
    monkeypatch.setattr(app_module, "creative_llm", lambda: FakeLLM("Proposition commerciale test."))
    monkeypatch.setattr(app_module.sheets, "append_lead", lambda **k: None)
    monkeypatch.setattr(app_module.hubspot, "create_lead", lambda **k: None)


@pytest.fixture
def emitted(monkeypatch):
    """Capture les webhooks émis sans jamais sortir sur le réseau."""
    events = []
    monkeypatch.setattr(
        webhook, "emit", lambda event, payload: events.append((event, payload)) or True,
    )
    # `app.py` et `api.py` importent le MODULE (pas la fonction) : un seul patch suffit.
    return events


def _ready(monkeypatch, **kwargs):
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["stratege"], **kwargs)


# ── §16.1.3 — GET /health ─────────────────────────────────────────────────────────────────────


def test_health_is_ok_and_unauthenticated():
    """Un orchestrateur doit pouvoir sonder sans détenir la clé qui écrit dans le CRM."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["environment"] == "development"
    assert body["checkpointer"] == "sqlite"   # DATABASE_URL vidée par conftest


def test_health_reports_only_booleans(monkeypatch):
    """
    Garde-fou de sécurité : `/health` n'étant pas authentifiée, elle ne doit jamais laisser fuir
    la moindre valeur de secret — seulement « configuré ou non ».
    """
    monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "pat-super-secret-value")

    body = client.get("/health").json()

    assert body["integrations"]["hubspot"] is True
    assert "pat-super-secret-value" not in json.dumps(body)
    assert all(isinstance(v, bool) for v in body["integrations"].values())


def test_health_makes_no_external_call(monkeypatch):
    """Sondée toutes les 10 s par Docker : elle ne doit consommer aucun quota ni dépendre d'un tiers."""
    def _boom(*args, **kwargs):
        raise AssertionError("/health ne doit joindre aucun service externe.")

    monkeypatch.setattr(app_module.sheets, "find_leads_by_sender", _boom)
    monkeypatch.setattr(app_module.enrichment, "research_company", _boom)
    assert client.get("/health").status_code == 200


# ── §16.1.1 — pièces jointes ──────────────────────────────────────────────────────────────────


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_attachments_reach_the_graph(monkeypatch):
    """Le test central du §16.1.1 : ce qui est envoyé en base64 arrive bien dans `attachments_raw`."""
    _ready(monkeypatch)
    seen = {}
    original = app_module.extract_text_from_attachments

    def _spy(attachments):
        seen["attachments"] = attachments
        return original(attachments)

    monkeypatch.setattr(app_module, "extract_text_from_attachments", _spy)

    resp = client.post("/threads", json={
        **EMAIL_PAYLOAD,
        "thread_id": f"n8n-att-{uuid.uuid4()}",
        "attachments": [{"filename": "cdc.pdf", "content_b64": _b64(b"%PDF-1.4 fake")}],
    })

    assert resp.status_code == 200
    assert seen["attachments"] == [("cdc.pdf", b"%PDF-1.4 fake")]


def test_attachments_are_optional(monkeypatch):
    """Rétrocompatibilité : les clients existants (dashboard) n'envoient pas ce champ."""
    _ready(monkeypatch)
    resp = client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": f"n8n-noatt-{uuid.uuid4()}"})
    assert resp.status_code == 200


def test_invalid_base64_is_rejected_before_the_llm(monkeypatch):
    """422 AVANT tout appel LLM — même principe que les bornes de `body` (§15.1.4)."""
    _mock_integrations(monkeypatch)
    monkeypatch.setattr(app_module, "fast_llm", lambda: FakeLLM("ne doit pas être appelé"))

    resp = client.post("/threads", json={
        **EMAIL_PAYLOAD, "attachments": [{"filename": "x.pdf", "content_b64": "pas du base64 !!"}],
    })
    assert resp.status_code == 422


def test_too_many_attachments_rejected():
    payload = {
        **EMAIL_PAYLOAD,
        "attachments": [{"filename": f"f{i}.pdf", "content_b64": _b64(b"x")} for i in range(11)],
    }
    assert client.post("/threads", json=payload).status_code == 422


def test_oversized_attachments_rejected():
    """Refuser AVANT de décoder : un corps énorme ne doit jamais atteindre le graphe."""
    from aca.api import MAX_ATTACHMENT_BYTES

    payload = {
        **EMAIL_PAYLOAD,
        "attachments": [{
            "filename": "gros.pdf", "content_b64": _b64(b"x" * (MAX_ATTACHMENT_BYTES + 1)),
        }],
    }
    assert client.post("/threads", json=payload).status_code == 422


# ── §16.1.4 — idempotence ─────────────────────────────────────────────────────────────────────


def test_repost_same_thread_id_does_not_rerun(monkeypatch):
    """
    Le nœud HTTP de n8n réessaie en cas d'échec réseau. Sans cette garde, un réessai relançait
    l'analyse complète (2 appels 70B, quota Tavily/Gemini) ET renotifiait l'équipe.
    """
    _ready(monkeypatch)
    thread_id = f"n8n-idem-{uuid.uuid4()}"

    first = client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})
    assert first.status_code == 200
    assert first.json()["already_exists"] is False

    creative = FakeLLM("NE DOIT PAS ÊTRE RÉGÉNÉRÉ")
    monkeypatch.setattr(app_module, "creative_llm", lambda: creative)

    second = client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    assert second.status_code == 200
    assert second.json()["already_exists"] is True
    assert creative.calls == 0  # le graphe n'a pas été rejoué
    assert second.json()["draft_response"] == first.json()["draft_response"]


def test_generated_thread_ids_stay_independent(monkeypatch):
    """Sans `thread_id` fourni, chaque appel est une analyse distincte (pas de faux positif)."""
    _install_fast_llm(monkeypatch, supervisor_replies=["stratege", "stratege"])
    _mock_integrations(monkeypatch)

    first = client.post("/threads", json=EMAIL_PAYLOAD).json()
    second = client.post("/threads", json=EMAIL_PAYLOAD).json()

    assert first["thread_id"] != second["thread_id"]
    assert second["already_exists"] is False


# ── §16.1.4 — mode asynchrone ─────────────────────────────────────────────────────────────────


def test_async_mode_returns_202_immediately(monkeypatch):
    _ready(monkeypatch)
    thread_id = f"n8n-async-{uuid.uuid4()}"

    resp = client.post("/threads?mode=async", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    assert resp.status_code == 202
    assert resp.json()["status"] == "running"
    assert resp.json()["thread_id"] == thread_id


def test_async_mode_still_runs_the_analysis(monkeypatch):
    """`TestClient` exécute les BackgroundTasks à la sortie du contexte : l'analyse doit aboutir."""
    _ready(monkeypatch)
    thread_id = f"n8n-async2-{uuid.uuid4()}"

    with TestClient(api) as ctx:
        assert ctx.post(
            "/threads?mode=async", json={**EMAIL_PAYLOAD, "thread_id": thread_id},
        ).status_code == 202

    state = client.get(f"/threads/{thread_id}").json()
    assert state["classification"] == "DEVIS"
    assert state["awaiting_validation"] is True


# ── §16.1.2 — émission des webhooks ───────────────────────────────────────────────────────────


def test_paused_event_emitted_before_the_validation_pause(monkeypatch, emitted):
    _ready(monkeypatch)

    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": f"n8n-wh-{uuid.uuid4()}"})

    events = [name for name, _ in emitted]
    assert webhook.EVENT_PAUSED in events
    payload = next(p for name, p in emitted if name == webhook.EVENT_PAUSED)
    assert payload["classification"] == "DEVIS"
    assert payload["sender"] == "jean@testsa.fr"
    assert payload["draft_response"].startswith("Proposition commerciale")


def test_validated_event_emitted_after_crm_write(monkeypatch, emitted):
    _ready(monkeypatch)
    thread_id = f"n8n-wh-val-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})
    emitted.clear()

    resp = client.post(f"/threads/{thread_id}/valider", json={"validated_by": "ismail"})

    assert resp.status_code == 200
    names = [name for name, _ in emitted]
    assert webhook.EVENT_VALIDATED in names
    payload = next(p for name, p in emitted if name == webhook.EVENT_VALIDATED)
    assert payload["validated_by"] == "ismail"


def test_rejected_event_emitted_without_crm_write(monkeypatch, emitted):
    _ready(monkeypatch)
    thread_id = f"n8n-wh-rej-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})
    emitted.clear()

    monkeypatch.setattr(
        app_module.sheets, "append_lead",
        lambda **k: (_ for _ in ()).throw(AssertionError("aucune écriture CRM sur un rejet")),
    )
    resp = client.post(f"/threads/{thread_id}/rejeter")

    assert resp.status_code == 200
    assert webhook.EVENT_REJECTED in [name for name, _ in emitted]


def test_clarification_event_emitted_when_the_graph_asks_a_question(monkeypatch, emitted):
    """
    §16.1.2 — la seule branche où le graphe s'arrête **sans** atteindre `notification_node`, donc
    sans émettre `analysis.paused`. Sans cet événement, un workflow lancé en `?mode=async` restait
    muet sur un e-mail ambigu : il avait reçu son 202 et attendait un `analysis.paused` qui n'arrive
    jamais tant que la question est sans réponse.
    """
    _mock_integrations(monkeypatch)
    _install_fast_llm(monkeypatch, supervisor_replies=["stratege"])
    # Besoin principal vide ⇒ `clarification_node` déclenche son `interrupt()` dynamique.
    monkeypatch.setattr(
        app_module, "smart_llm",
        lambda: FakeLLM('{"entreprise": "Test SA", "contact": "Jean", "urgence": "haute", '
                        '"besoin_principal": ""}'),
    )
    thread_id = f"n8n-wh-clar-{uuid.uuid4()}"

    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    names = [name for name, _ in emitted]
    assert webhook.EVENT_CLARIFICATION in names
    # Le lead n'est pas encore prêt : surtout PAS d'`analysis.paused` à ce stade.
    assert webhook.EVENT_PAUSED not in names
    payload = next(p for name, p in emitted if name == webhook.EVENT_CLARIFICATION)
    assert payload["pending_clarification"]["field"] == "besoin_principal"
    assert payload["thread_id"] == thread_id


def test_clarification_event_not_emitted_on_a_normal_lead(monkeypatch, emitted):
    """Symétrique du précédent : un besoin clair ne doit déclencher aucune question inutile."""
    _ready(monkeypatch)

    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": f"n8n-wh-noclar-{uuid.uuid4()}"})

    assert webhook.EVENT_CLARIFICATION not in [name for name, _ in emitted]


def test_webhook_payload_matches_api_snapshot_shape(monkeypatch, emitted):
    """
    Anti-dérive (§16.1.2) : l'abonné webhook et le client REST doivent voir le même lead. Les deux
    passent par `snapshot_from_state()`, ce test l'atteste plutôt que de l'espérer.
    """
    _ready(monkeypatch)
    thread_id = f"n8n-wh-shape-{uuid.uuid4()}"
    client.post("/threads", json={**EMAIL_PAYLOAD, "thread_id": thread_id})

    rest = client.get(f"/threads/{thread_id}").json()
    pushed = next(p for name, p in emitted if name == webhook.EVENT_PAUSED)

    # Le REST ajoute les 3 champs propres à la pause ; tout le reste doit être identique.
    assert set(rest) - set(pushed) == {"pending_clarification", "awaiting_validation", "done"}
    for key in pushed:
        assert pushed[key] == rest[key], f"champ divergent : {key}"

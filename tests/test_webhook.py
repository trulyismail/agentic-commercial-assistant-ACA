"""
Tests du webhook sortant (§16.1.2) — la pièce qui rend le port n8n événementiel plutôt que
fondé sur du sondage.

Deux propriétés comptent plus que le reste et sont testées en premier :
1. **Ne lève jamais.** `emit()` est appelé depuis des nœuds du graphe, tous sous `RETRY_POLICY` :
   une exception y provoquerait jusqu'à 3 réexécutions du nœud — et pour un nœud d'écriture, une
   double écriture CRM (le bug HubSpot réellement survenu le 2026-07-12).
2. **No-op sans configuration.** Contrat de dégradation gracieuse commun à tout le projet.

Aucun appel réseau : `requests.post` est remplacé par une doublure qui capture l'appel.
"""
import hashlib
import hmac
import json

import pytest

from aca.integrations import webhook


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def captured(monkeypatch):
    """Capture l'appel `requests.post` au lieu de sortir sur le réseau."""
    calls = []

    def _post(url, data=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr(webhook.requests, "post", _post)
    return calls


# ── Dégradation gracieuse ─────────────────────────────────────────────────────────────────────


def test_disabled_without_url(monkeypatch, captured):
    monkeypatch.delenv("ACA_WEBHOOK_URL", raising=False)
    assert webhook.is_enabled() is False
    assert webhook.emit(webhook.EVENT_PAUSED, {"thread_id": "t1"}) is False
    assert captured == []  # aucun appel réseau tenté


def test_enabled_reads_env_dynamically(monkeypatch):
    """Jamais figé à l'import — la leçon `DATABASE_URL`/pgvector du 2026-07-11."""
    monkeypatch.delenv("ACA_WEBHOOK_URL", raising=False)
    assert webhook.is_enabled() is False
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    assert webhook.is_enabled() is True


def test_never_raises_on_network_error(monkeypatch):
    """La propriété critique : une panne réseau ne doit jamais remonter dans un nœud du graphe."""
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")

    def _boom(*args, **kwargs):
        raise ConnectionError("n8n injoignable")

    monkeypatch.setattr(webhook.requests, "post", _boom)
    assert webhook.emit(webhook.EVENT_PAUSED, {"thread_id": "t1"}) is False


def test_never_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.setattr(webhook.requests, "post", lambda *a, **k: _FakeResponse(500))
    assert webhook.emit(webhook.EVENT_PAUSED, {"thread_id": "t1"}) is False


def test_never_raises_on_unserializable_payload(monkeypatch, captured):
    """Un objet exotique dans l'état du graphe ne doit pas casser l'analyse (`default=str`)."""
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")

    class _Exotic:
        def __str__(self):
            return "exotique"

    assert webhook.emit(webhook.EVENT_PAUSED, {"obj": _Exotic()}) is True


# ── Enveloppe ─────────────────────────────────────────────────────────────────────────────────


def test_envelope_shape(monkeypatch, captured):
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.setenv("ACA_ORG_ID", "client-a")

    assert webhook.emit(webhook.EVENT_VALIDATED, {"thread_id": "t1"}) is True

    envelope = json.loads(captured[0]["data"].decode("utf-8"))
    assert envelope["event"] == "lead.validated"
    assert envelope["org_id"] == "client-a"          # multi-tenant : un seul endpoint n8n suffit
    assert isinstance(envelope["timestamp"], int)    # epoch entier (fenêtre anti-rejeu)
    assert envelope["data"] == {"thread_id": "t1"}
    assert captured[0]["url"] == "https://n8n.example/webhook/aca"
    assert captured[0]["timeout"] == webhook.TIMEOUT_SECONDS


def test_accents_are_not_escaped(monkeypatch, captured):
    """`ensure_ascii=False` : un devis français doit rester lisible côté n8n."""
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    webhook.emit(webhook.EVENT_PAUSED, {"subject": "Demande de démonstration"})
    assert "démonstration" in captured[0]["data"].decode("utf-8")


def test_event_names_are_stable():
    """Ces chaînes sont un contrat public : un workflow n8n filtre dessus."""
    assert webhook.EVENT_PAUSED == "analysis.paused"
    assert webhook.EVENT_CLARIFICATION == "analysis.clarification"
    assert webhook.EVENT_ROUTED == "analysis.routed"
    assert webhook.EVENT_VALIDATED == "lead.validated"
    assert webhook.EVENT_REJECTED == "lead.rejected"


# ── Signature HMAC ────────────────────────────────────────────────────────────────────────────


def test_no_signature_without_secret(monkeypatch, captured):
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.delenv("ACA_WEBHOOK_SECRET", raising=False)

    webhook.emit(webhook.EVENT_PAUSED, {"thread_id": "t1"})

    assert "X-ACA-Signature" not in captured[0]["headers"]
    # Contrairement à /slack/interactions, l'absence de secret n'empêche PAS l'envoi : un webhook
    # sortant ne déclenche aucune écriture CRM chez nous, le risque n'est pas symétrique.


def test_signature_is_verifiable_by_receiver(monkeypatch, captured):
    """Le destinataire (n8n) doit pouvoir refaire le calcul — sinon la signature ne sert à rien."""
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.setenv("ACA_WEBHOOK_SECRET", "s3cret")

    webhook.emit(webhook.EVENT_PAUSED, {"thread_id": "t1"})

    headers, body = captured[0]["headers"], captured[0]["data"]
    timestamp = headers["X-ACA-Timestamp"]
    expected = hmac.new(
        b"s3cret", f"{timestamp}.".encode() + body, hashlib.sha256,
    ).hexdigest()
    assert headers["X-ACA-Signature"] == f"sha256={expected}"


def test_signature_covers_the_exact_bytes_sent(monkeypatch, captured):
    """
    Régression : signer un ré-encodage plutôt que le corps réellement transmis produirait une
    signature que le destinataire ne peut pas revalider (ordre des clés potentiellement différent).
    """
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.setenv("ACA_WEBHOOK_SECRET", "s3cret")

    webhook.emit(webhook.EVENT_PAUSED, {"z": 1, "a": 2, "accent": "é"})

    headers, body = captured[0]["headers"], captured[0]["data"]
    recomputed = hmac.new(
        b"s3cret", f"{headers['X-ACA-Timestamp']}.".encode() + body, hashlib.sha256,
    ).hexdigest()
    assert headers["X-ACA-Signature"] == f"sha256={recomputed}"


def test_tampered_body_breaks_the_signature(monkeypatch, captured):
    monkeypatch.setenv("ACA_WEBHOOK_URL", "https://n8n.example/webhook/aca")
    monkeypatch.setenv("ACA_WEBHOOK_SECRET", "s3cret")

    webhook.emit(webhook.EVENT_VALIDATED, {"thread_id": "t1"})

    headers, body = captured[0]["headers"], captured[0]["data"]
    tampered = body.replace(b'"t1"', b'"t2"')
    forged = hmac.new(
        b"s3cret", f"{headers['X-ACA-Timestamp']}.".encode() + tampered, hashlib.sha256,
    ).hexdigest()
    assert headers["X-ACA-Signature"] != f"sha256={forged}"

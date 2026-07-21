"""
Tests du suivi de facturation par organisation (§12 item 4, audité §14) : aca/integrations/billing.py.
Le chemin "Stripe désactivé" est couvert dans test_degradation.py (contrat de dégradation
gracieuse commun) ; ce fichier couvre le chemin "Stripe activé" via un faux client Stripe — jamais
d'appel réseau réel, aucun compte Stripe de test n'existe pour ce projet.
"""
from aca.integrations import billing
from aca.storage import analytics_store, config_store


def test_report_usage_calls_stripe_when_configured_and_subscribed(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    config_store.set_setting("STRIPE_SUBSCRIPTION_ITEM_ID", "si_fake123")

    analytics_store.record_tokens("t-1", input_tokens=100, output_tokens=50)

    calls = {}

    class FakeSubscriptionItem:
        @staticmethod
        def create_usage_record(subscription_item_id, quantity, action):
            calls["subscription_item_id"] = subscription_item_id
            calls["quantity"] = quantity
            calls["action"] = action

    monkeypatch.setattr(billing.stripe, "SubscriptionItem", FakeSubscriptionItem)

    stats = billing.report_usage()
    assert calls == {"subscription_item_id": "si_fake123", "quantity": 150, "action": "set"}
    assert stats["total_entree"] == 100 and stats["total_sortie"] == 50


def test_report_usage_skips_stripe_call_without_subscription_item(monkeypatch, tmp_path):
    """Stripe activé mais aucun `STRIPE_SUBSCRIPTION_ITEM_ID` réglé pour ce tenant → pas d'appel."""
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    analytics_store.record_tokens("t-1", input_tokens=10, output_tokens=5)

    monkeypatch.setattr(
        billing.stripe, "SubscriptionItem",
        type("Exploding", (), {"create_usage_record": staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("Stripe ne doit pas être appelé"))
        )}),
    )
    stats = billing.report_usage()
    assert stats["total_entree"] == 10


def test_report_usage_stripe_failure_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    config_store.set_setting("STRIPE_SUBSCRIPTION_ITEM_ID", "si_fake123")
    analytics_store.record_tokens("t-1", input_tokens=10, output_tokens=5)

    def _raise(*a, **k):
        raise RuntimeError("réseau indisponible")

    monkeypatch.setattr(
        billing.stripe, "SubscriptionItem",
        type("Failing", (), {"create_usage_record": staticmethod(_raise)}),
    )
    stats = billing.report_usage()  # ne doit PAS lever
    assert stats["total_entree"] == 10

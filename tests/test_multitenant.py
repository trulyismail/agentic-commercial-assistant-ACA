"""
Tests de la fondation multi-tenant (§12 item 3, audité §14.3 de docs/ACAM_roadmap.md) : chaque
registre SQLite local tague désormais ses lignes par `org_id` (défaut : tenant du process courant,
cf. aca.core.tenant) et scope ses lectures dessus. Ces tests vérifient l'isolation entre deux
tenants distincts sur le MÊME fichier .sqlite — le scénario que RLS reproduit côté Supabase.
"""
from aca.core.tenant import DEFAULT_ORG_ID, current_org_id
from aca.storage import analytics_store, audit_log, followup_store, queue_store


def test_current_org_id_defaults_and_reads_env(monkeypatch):
    monkeypatch.delenv("ACA_ORG_ID", raising=False)
    assert current_org_id() == DEFAULT_ORG_ID
    monkeypatch.setenv("ACA_ORG_ID", "acme-corp")
    assert current_org_id() == "acme-corp"


def test_queue_store_isolates_pending_by_org(monkeypatch, tmp_path):
    monkeypatch.setattr(queue_store, "DB_PATH", str(tmp_path / "queue.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    queue_store.enqueue("msg-acme", "thread-acme", "a@acme.fr", "Objet Acme")
    queue_store.mark_ready("msg-acme")

    monkeypatch.setenv("ACA_ORG_ID", "globex")
    queue_store.enqueue("msg-globex", "thread-globex", "b@globex.fr", "Objet Globex")
    queue_store.mark_ready("msg-globex")

    assert [e["thread_id"] for e in queue_store.list_pending()] == ["thread-globex"]
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert [e["thread_id"] for e in queue_store.list_pending()] == ["thread-acme"]


def test_analytics_store_isolates_volume_by_org(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    analytics_store.record_classification("t-acme", "DEVIS", "a@acme.fr", "manuel")

    monkeypatch.setenv("ACA_ORG_ID", "globex")
    analytics_store.record_classification("t-globex-1", "SPAM", "b@globex.fr", "manuel")
    analytics_store.record_classification("t-globex-2", "SPAM", "c@globex.fr", "manuel")

    assert analytics_store.funnel_counts()["classifiés"] == 2  # tenant courant = globex
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert analytics_store.funnel_counts()["classifiés"] == 1


def test_audit_log_isolates_by_org(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    audit_log.log_validation("t-acme", "Alice", "DEVIS", "a@acme.fr")

    monkeypatch.setenv("ACA_ORG_ID", "globex")
    audit_log.log_validation("t-globex", "Bob", "DEMANDE_DEMO", "b@globex.fr")

    assert [r["thread_id"] for r in audit_log.list_recent()] == ["t-globex"]
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert [r["thread_id"] for r in audit_log.list_recent()] == ["t-acme"]


def test_followup_store_isolates_active_by_org(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    followup_store.track("t-acme", "gmail-acme", "a@acme.fr", "Objet Acme")

    monkeypatch.setenv("ACA_ORG_ID", "globex")
    followup_store.track("t-globex", "gmail-globex", "b@globex.fr", "Objet Globex")

    assert [r["thread_id"] for r in followup_store.list_active()] == ["t-globex"]
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert [r["thread_id"] for r in followup_store.list_active()] == ["t-acme"]

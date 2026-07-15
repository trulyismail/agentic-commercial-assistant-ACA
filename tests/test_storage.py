"""
Tests des registres SQLite locaux (aca/storage/) : file d'attente du poller, journal analytique,
journal d'audit et suivi de relance. Chaque test redirige `DB_PATH` (figé à l'import du module)
vers un fichier temporaire pytest — les vraies bases `data/*.sqlite` ne sont jamais touchées.
Teste aussi le retry local sur conflit de verrou SQLite (§11.6 item 3, `sqlite_retry.py`).
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from aca.storage import analytics_store, audit_log, followup_store, queue_store, sqlite_retry
from aca.storage.sqlite_retry import with_sqlite_retry

FMT = "%Y-%m-%d %H:%M:%S"


# ── queue_store (file du poller : idempotence, staging en_cours/en_attente, récupération) ────
def _fresh_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(queue_store, "DB_PATH", str(tmp_path / "queue.sqlite"))


def test_enqueue_marks_known_immediately(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    assert not queue_store.is_known("msg-1")
    queue_store.enqueue("msg-1", "t-1", "a@b.fr", "Objet")
    # Connu dès l'enqueue (statut en_cours) : un crash du poller ne cause pas de double traitement.
    assert queue_store.is_known("msg-1")
    assert queue_store.list_pending() == []  # pas encore en_attente


def test_enqueue_is_idempotent(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    queue_store.enqueue("msg-1", "t-1", "a@b.fr", "Objet")
    queue_store.enqueue("msg-1", "t-1", "a@b.fr", "Objet")  # INSERT OR IGNORE
    queue_store.mark_ready("msg-1")
    assert len(queue_store.list_pending()) == 1


def test_mark_ready_then_validated(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    queue_store.enqueue("msg-1", "t-1", "a@b.fr", "Objet")
    queue_store.mark_ready("msg-1")
    pending = queue_store.list_pending()
    assert pending[0]["thread_id"] == "t-1"
    queue_store.mark_validated("t-1")
    assert queue_store.list_pending() == []


def test_reset_stale_recovers_stuck_entries(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    old = (datetime.now() - timedelta(minutes=30)).strftime(FMT)
    with queue_store._connect() as conn:
        conn.execute(
            "INSERT INTO queue (message_id, thread_id, sender, subject, status, created_at) "
            "VALUES ('msg-stale', 't-stale', 'a@b.fr', 'Objet', 'en_cours', ?)", (old,)
        )
        conn.commit()
    assert queue_store.reset_stale(older_than_minutes=15) == 1
    assert not queue_store.is_known("msg-stale")  # retraitable au prochain cycle


def test_reset_stale_keeps_fresh_entries(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    queue_store.enqueue("msg-fresh", "t-fresh", "a@b.fr", "Objet")
    assert queue_store.reset_stale(older_than_minutes=15) == 0
    assert queue_store.is_known("msg-fresh")


def test_purge_validated_older_than(monkeypatch, tmp_path):
    _fresh_queue(monkeypatch, tmp_path)
    old = (datetime.now() - timedelta(days=400)).strftime(FMT)
    with queue_store._connect() as conn:
        conn.execute(
            "INSERT INTO queue (message_id, thread_id, sender, subject, status, created_at) "
            "VALUES ('msg-old', 't-old', 'a@b.fr', 'Objet', 'validé', ?)", (old,)
        )
        conn.commit()
    assert queue_store.list_validated_older_than(365) == ["t-old"]
    assert queue_store.purge_validated_older_than(365) == 1
    assert queue_store.list_validated_older_than(365) == []


# ── analytics_store (journal du tableau de bord) ─────────────────────────────────────────────
def _fresh_analytics(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))


def test_record_classification_idempotent(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "poller")
    # Rejouable (resynchronisation UI d'un thread déjà loggé par le poller) : pas de doublon,
    # la première classification est conservée.
    analytics_store.record_classification("t-1", "SPAM", "a@b.fr", "manuel")
    volumes = analytics_store.volume_by_category(days=1)
    assert volumes == [{"classification": "DEVIS", "count": 1}]


def test_funnel_and_response_time(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "manuel")
    analytics_store.record_classification("t-2", "SPAM", "spam@x.io", "poller")
    analytics_store.record_draft_ready("t-1")
    analytics_store.record_validation("t-1")
    funnel = analytics_store.funnel_counts(days=1)
    assert funnel == {"classifiés": 2, "proposition rédigée": 1, "validés": 1}
    times = analytics_store.response_times(days=1)
    assert len(times) == 1 and times[0]["thread_id"] == "t-1"
    assert times[0]["minutes"] >= 0


def test_daily_volume(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "manuel")
    daily = analytics_store.daily_volume(days=1)
    assert daily == [{"jour": datetime.now().strftime("%Y-%m-%d"), "count": 1}]


# ── audit_log (traçabilité des validations) ──────────────────────────────────────────────────
def test_audit_log_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "audit.sqlite"))
    audit_log.log_validation("t-1", "Ismail", "DEVIS", "a@b.fr")
    audit_log.log_validation("t-2", "", "DEMANDE_DEMO", "c@d.fr")  # nom vide → placeholder
    recent = audit_log.list_recent(limit=5)
    assert len(recent) == 2
    by_thread = {r["thread_id"]: r for r in recent}
    assert by_thread["t-1"]["validated_by"] == "Ismail"
    assert by_thread["t-2"]["validated_by"] == "(non renseigné)"


# ── followup_store (suivi de relance) ────────────────────────────────────────────────────────
def test_track_noop_without_gmail_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    followup_store.track("t-1", "", "a@b.fr", "Objet")  # saisie manuelle : pas de fil Gmail
    assert followup_store.list_active() == []


def test_track_and_mark_followed_up(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    followup_store.track("t-1", "gmail-123", "a@b.fr", "Objet")
    active = followup_store.list_active()
    assert active == [{"thread_id": "t-1", "gmail_thread_id": "gmail-123", "sender": "a@b.fr", "subject": "Objet"}]
    followup_store.mark_followed_up("t-1")
    assert followup_store.list_active() == []  # une seule relance par lead dans cette version


# ── sqlite_retry (§11.6 item 3 : retry local hors du graphe LangGraph) ───────────────────────
def test_with_sqlite_retry_succeeds_after_transient_lock(monkeypatch):
    monkeypatch.setattr(sqlite_retry, "BASE_DELAY_SECONDS", 0)  # tests instantanés, pas de vrai sleep
    calls = {"n": 0}

    @with_sqlite_retry
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3  # 2 échecs absorbés, succès à la 3e tentative


def test_with_sqlite_retry_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(sqlite_retry, "BASE_DELAY_SECONDS", 0)
    calls = {"n": 0}

    @with_sqlite_retry
    def always_locked():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        always_locked()
    assert calls["n"] == sqlite_retry.MAX_ATTEMPTS


def test_with_sqlite_retry_does_not_retry_other_exceptions():
    calls = {"n": 0}

    @with_sqlite_retry
    def programming_error():
        calls["n"] += 1
        raise ValueError("bug, pas un conflit de verrou")

    with pytest.raises(ValueError):
        programming_error()
    assert calls["n"] == 1  # aucune tentative supplémentaire : rejouer ne corrigerait pas un bug


def test_queue_store_functions_are_retry_wrapped():
    # Vérifie le câblage réel (pas juste le décorateur en isolation) : chaque fonction publique de
    # queue_store doit être décorée, pas seulement une partie d'entre elles.
    for name in ("enqueue", "mark_ready", "reset_stale", "list_pending", "mark_validated"):
        assert getattr(queue_store, name).__wrapped__ is not None


def test_audit_log_functions_are_retry_wrapped():
    for name in ("log_validation", "list_recent"):
        assert getattr(audit_log, name).__wrapped__ is not None

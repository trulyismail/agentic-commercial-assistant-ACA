"""
Tests des registres SQLite locaux (aca/storage/) : file d'attente du poller, journal analytique,
journal d'audit et suivi de relance. Chaque test redirige `DB_PATH` (figé à l'import du module)
vers un fichier temporaire pytest — les vraies bases `data/*.sqlite` ne sont jamais touchées.
Teste aussi le retry local sur conflit de verrou SQLite (§11.6 item 3, `sqlite_retry.py`).
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from aca.storage import analytics_store, audit_log, config_store, followup_store, queue_store, sqlite_retry
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


def test_mark_ready_then_rejected(monkeypatch, tmp_path):
    # Rejet humain (bouton "Rejeter" de ui.py / `_do_reject` de l'API) : le lead quitte la file
    # sans écriture CRM, exactement comme une validation retire l'entrée — mais sans action_node.
    _fresh_queue(monkeypatch, tmp_path)
    queue_store.enqueue("msg-1", "t-1", "a@b.fr", "Objet")
    queue_store.mark_ready("msg-1")
    assert queue_store.list_pending()[0]["thread_id"] == "t-1"
    queue_store.mark_rejected("t-1")
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


# ── analytics_store : brouillons édités + tokens (§13 items 3/4, audit des PDF "ACAM v2 Blueprint") ──
def test_record_edit_noop_when_unchanged(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "manuel")
    analytics_store.record_validation("t-1")
    analytics_store.record_edit("t-1", "Bonjour.", "Bonjour.")  # identique -> no-op
    assert analytics_store.edit_rate(days=1) == {"validés": 1, "édités": 0, "taux_pct": 0.0}


def test_record_edit_and_rate(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "manuel")
    analytics_store.record_classification("t-2", "DEMANDE_DEMO", "c@d.fr", "poller")
    analytics_store.record_validation("t-1")
    analytics_store.record_validation("t-2")
    analytics_store.record_edit("t-1", "Bonjour.", "Bonjour, le prix est de 490€/mois.")
    rate = analytics_store.edit_rate(days=1)
    assert rate == {"validés": 2, "édités": 1, "taux_pct": 50.0}


def test_edit_rate_zero_validated_no_division_error(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    assert analytics_store.edit_rate(days=1) == {"validés": 0, "édités": 0, "taux_pct": 0.0}


# ── analytics_store.get_draft_edit (§18, recap #5/§4 item 2 — différentiel avant/après) ────────
def test_get_draft_edit_absent_renvoie_none(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    assert analytics_store.get_draft_edit("jamais-edite") is None


def test_get_draft_edit_renvoie_le_texte_complet(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_edit("t-1", "Bonjour, prix : 500€.", "Bonjour, prix : 450€.")
    edit = analytics_store.get_draft_edit("t-1")
    assert edit["original"] == "Bonjour, prix : 500€."
    assert edit["edited"] == "Bonjour, prix : 450€."
    assert edit["edited_at"]


def test_get_draft_edit_renvoie_la_plus_recente_modification(monkeypatch, tmp_path):
    """`record_edit` n'a pas de contrainte d'unicité sur `thread_id` — la lecture doit servir la
    version qui correspond à la validation effectivement envoyée, pas la première."""
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_edit("t-1", "V0", "V1")
    analytics_store.record_edit("t-1", "V1", "V2")
    edit = analytics_store.get_draft_edit("t-1")
    assert edit == {"original": "V1", "edited": "V2", "edited_at": edit["edited_at"]}


def test_get_draft_edit_ne_melange_pas_les_threads(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_edit("t-1", "A", "B")
    analytics_store.record_edit("t-2", "C", "D")
    assert analytics_store.get_draft_edit("t-1")["edited"] == "B"
    assert analytics_store.get_draft_edit("t-2")["edited"] == "D"


def test_record_tokens_noop_when_zero(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_tokens("t-1", 0, 0)
    assert analytics_store.token_stats(days=1) == {
        "analyses": 0, "total_entree": 0, "total_sortie": 0, "moyenne_par_analyse": 0,
    }


def test_record_tokens_and_stats(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_tokens("t-1", 800, 200)
    analytics_store.record_tokens("t-2", 400, 100)
    stats = analytics_store.token_stats(days=1)
    assert stats["analyses"] == 2
    assert stats["total_entree"] == 1200
    assert stats["total_sortie"] == 300
    assert stats["moyenne_par_analyse"] == 750.0  # (1200+300)/2


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


# ── audit_log : chaînage tamper-evident (§15.2.7) ────────────────────────────────────────────
def test_audit_chain_verifies_on_an_untouched_log(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "audit.sqlite"))
    for i in range(3):
        audit_log.log_validation(f"t-{i}", "alice", "DEVIS", f"{i}@exemple.fr")

    result = audit_log.verify_chain()
    assert result["ok"] is True
    assert result["checked"] == 3
    assert result["legacy_unchained"] == 0
    assert result["first_invalid_id"] is None


def test_audit_chain_detects_an_edited_row(monkeypatch, tmp_path):
    """Le scénario que §15.2.7 visait : quelqu'un réécrit discrètement QUI a validé un lead."""
    db = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(audit_log, "DB_PATH", db)
    for i in range(3):
        audit_log.log_validation(f"t-{i}", "alice", "DEVIS", f"{i}@exemple.fr")
    assert audit_log.verify_chain()["ok"] is True

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE audit SET validated_by = 'bob' WHERE id = 2")
        conn.commit()

    result = audit_log.verify_chain()
    assert result["ok"] is False
    assert result["first_invalid_id"] == 2


def test_audit_chain_detects_a_deleted_row(monkeypatch, tmp_path):
    """Supprimer une ligne du milieu laisse chaque ligne restante individuellement cohérente —
    c'est le contrôle du `prev_hash` attendu qui le rattrape."""
    db = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(audit_log, "DB_PATH", db)
    for i in range(4):
        audit_log.log_validation(f"t-{i}", "alice", "DEVIS", f"{i}@exemple.fr")

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM audit WHERE id = 2")
        conn.commit()

    result = audit_log.verify_chain()
    assert result["ok"] is False
    assert result["first_invalid_id"] == 3  # la ligne suivante pointe vers une empreinte disparue


def test_audit_chain_tolerates_pre_migration_rows(monkeypatch, tmp_path):
    """Un journal antérieur au chaînage n'est pas une preuve de fraude : compté à part, pas rejeté."""
    db = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(audit_log, "DB_PATH", db)
    audit_log.log_validation("t-0", "alice", "DEVIS", "0@exemple.fr")
    with sqlite3.connect(db) as conn:  # simule une ligne écrite avant la migration
        conn.execute(
            "INSERT INTO audit (thread_id, validated_by, classification, sender, validated_at, "
            "org_id) VALUES ('t-legacy', 'ancien', 'DEVIS', 'x@exemple.fr', "
            "'2026-01-01 00:00:00', 'default')"
        )
        conn.commit()

    result = audit_log.verify_chain()
    assert result["ok"] is True
    assert result["legacy_unchained"] == 1
    assert result["checked"] == 1


def test_audit_chain_is_hmac_keyed_when_configured(monkeypatch, tmp_path):
    """Avec une clé, refabriquer une empreinte valide sans la connaître devient impossible."""
    db = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(audit_log, "DB_PATH", db)
    monkeypatch.setenv("ACA_AUDIT_HMAC_KEY", "cle-secrete-hors-base")
    audit_log.log_validation("t-0", "alice", "DEVIS", "0@exemple.fr")
    assert audit_log.verify_chain()["ok"] is True

    # Un attaquant sans la clé recalcule l'empreinte en SHA-256 simple : la vérification échoue.
    monkeypatch.delenv("ACA_AUDIT_HMAC_KEY")
    assert audit_log.verify_chain()["ok"] is False


def test_audit_chains_are_independent_per_tenant(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_log, "DB_PATH", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    audit_log.log_validation("t-acme", "alice", "DEVIS", "a@acme.fr")
    monkeypatch.setenv("ACA_ORG_ID", "globex")
    audit_log.log_validation("t-globex", "bob", "DEVIS", "b@globex.fr")

    # L'écriture d'un tenant ne s'intercale pas dans la chaîne de l'autre.
    assert audit_log.verify_chain()["ok"] is True
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert audit_log.verify_chain()["ok"] is True


# ── Droit à l'effacement RGPD (§15.2.4) ──────────────────────────────────────────────────────
def test_queue_store_purges_and_lists_by_sender(monkeypatch, tmp_path):
    monkeypatch.setattr(queue_store, "DB_PATH", str(tmp_path / "queue.sqlite"))
    queue_store.enqueue("m-1", "t-1", "cible@exemple.fr", "Objet 1")
    queue_store.enqueue("m-2", "t-2", "cible@exemple.fr", "Objet 2")
    queue_store.enqueue("m-3", "t-3", "autre@exemple.fr", "Objet 3")

    assert sorted(queue_store.list_threads_by_sender("cible@exemple.fr")) == ["t-1", "t-2"]
    assert queue_store.purge_sender("cible@exemple.fr") == 2
    assert queue_store.list_threads_by_sender("cible@exemple.fr") == []
    # Les données des autres personnes sont intactes.
    assert queue_store.list_threads_by_sender("autre@exemple.fr") == ["t-3"]


def test_erasure_is_scoped_to_the_tenant(monkeypatch, tmp_path):
    """Une demande d'effacement chez un client ne doit pas toucher les données d'un autre."""
    monkeypatch.setattr(queue_store, "DB_PATH", str(tmp_path / "queue.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    queue_store.enqueue("m-1", "t-acme", "meme@exemple.fr", "Objet")
    monkeypatch.setenv("ACA_ORG_ID", "globex")
    queue_store.enqueue("m-2", "t-globex", "meme@exemple.fr", "Objet")

    assert queue_store.purge_sender("meme@exemple.fr") == 1
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert queue_store.list_threads_by_sender("meme@exemple.fr") == ["t-acme"]


def test_followup_store_purges_by_sender(monkeypatch, tmp_path):
    """Effet de bord voulu : la personne effacée ne sera plus relancée par relance.py."""
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    followup_store.track("t-1", "gmail-1", "cible@exemple.fr", "Objet 1")
    followup_store.track("t-2", "gmail-2", "autre@exemple.fr", "Objet 2")

    assert followup_store.purge_sender("cible@exemple.fr") == 1
    remaining = [row["sender"] for row in followup_store.list_active()]
    assert remaining == ["autre@exemple.fr"]


# ── followup_store (suivi de relance) ────────────────────────────────────────────────────────
def test_track_noop_without_gmail_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    followup_store.track("t-1", "", "a@b.fr", "Objet")  # saisie manuelle : pas de fil Gmail
    assert followup_store.list_active() == []


def test_track_and_mark_followed_up(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    followup_store.track("t-1", "gmail-123", "a@b.fr", "Objet")
    active = followup_store.list_active()
    assert active == [{
        "thread_id": "t-1", "gmail_thread_id": "gmail-123", "sender": "a@b.fr", "subject": "Objet",
        "followup_count": 0,
    }]
    followup_store.mark_followed_up("t-1")
    active = followup_store.list_active()
    assert len(active) == 1 and active[0]["followup_count"] == 1  # toujours actif, cadence multi-round


def test_followup_stops_after_max_rounds(monkeypatch, tmp_path):
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    monkeypatch.setattr(followup_store, "RELANCE_MAX_ROUNDS", 2)
    followup_store.track("t-1", "gmail-123", "a@b.fr", "Objet")
    followup_store.mark_followed_up("t-1")
    assert len(followup_store.list_active()) == 1  # 1 relance envoyée, plafond à 2
    followup_store.mark_followed_up("t-1")
    assert followup_store.list_active() == []  # plafond atteint, plus jamais relancé


def test_followup_max_rounds_overridable_via_config_store(monkeypatch, tmp_path):
    """§12 item 7 : un réglage édité via l'UI prend le pas sur `RELANCE_MAX_ROUNDS` (.env/défaut)."""
    monkeypatch.setattr(followup_store, "DB_PATH", str(tmp_path / "followup.sqlite"))
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    monkeypatch.setattr(followup_store, "RELANCE_MAX_ROUNDS", 3)  # défaut .env
    followup_store.track("t-1", "gmail-123", "a@b.fr", "Objet")
    followup_store.mark_followed_up("t-1")  # 1 relance envoyée

    assert len(followup_store.list_active()) == 1  # sous le défaut .env (3)
    config_store.set_setting("RELANCE_MAX_ROUNDS", "1")  # réglage panneau : plafond abaissé à 1
    assert followup_store.list_active() == []  # le réglage panneau l'emporte immédiatement


def test_followup_schema_migrates_legacy_followup_sent_column(monkeypatch, tmp_path):
    """Une base créée avant la cadence multi-round n'a que `followup_sent` (0/1) — vérifie que la
    migration idempotente reporte correctement l'état déjà connu au lieu de le perdre."""
    import sqlite3

    db_path = tmp_path / "legacy_followup.sqlite"
    monkeypatch.setattr(followup_store, "DB_PATH", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE followup (thread_id TEXT PRIMARY KEY, gmail_thread_id TEXT NOT NULL, "
        "sender TEXT, subject TEXT, validated_at TEXT NOT NULL, "
        "followup_sent INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO followup (thread_id, gmail_thread_id, sender, subject, validated_at, followup_sent) "
        "VALUES ('t-legacy', 'gmail-999', 'old@b.fr', 'Ancien objet', '2026-01-01 00:00:00', 1)"
    )
    conn.commit()
    conn.close()

    active = followup_store.list_active()
    assert active == [{
        "thread_id": "t-legacy", "gmail_thread_id": "gmail-999", "sender": "old@b.fr",
        "subject": "Ancien objet", "followup_count": 1,
    }]


# ── config_store (panneau de réglages, §12 item 7) ───────────────────────────────────────────
def test_get_setting_returns_none_when_never_set(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    assert config_store.get_setting("CALENDLY_URL") is None


def test_set_and_get_setting_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    config_store.set_setting("CALENDLY_URL", "https://calendly.example/demo")
    assert config_store.get_setting("CALENDLY_URL") == "https://calendly.example/demo"


def test_set_setting_overwrites_previous_value(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    config_store.set_setting("RELANCE_DAYS", "4")
    config_store.set_setting("RELANCE_DAYS", "7")
    assert config_store.get_setting("RELANCE_DAYS") == "7"


def test_get_all_settings_scoped_by_org(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "DB_PATH", str(tmp_path / "config.sqlite"))
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    config_store.set_setting("CALENDLY_URL", "https://acme.example/demo")
    monkeypatch.setenv("ACA_ORG_ID", "globex")
    config_store.set_setting("CALENDLY_URL", "https://globex.example/demo")

    assert config_store.get_all_settings() == {"CALENDLY_URL": "https://globex.example/demo"}
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert config_store.get_all_settings() == {"CALENDLY_URL": "https://acme.example/demo"}


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


# ── §22 — Répartitions du tableau de bord ─────────────────────────────────────────────────────
# Ces quatre lectures n'ajoutent aucune donnée : elles exposent des colonnes déjà écrites depuis
# l'origine. Les tests portent donc sur ce qui pourrait silencieusement mentir — une tranche
# absente, une heure creuse escamotée, un classement instable.

def test_repartition_par_origine(monkeypatch, tmp_path):
    """`source` était enregistrée depuis toujours et affichée nulle part : c'est la mesure
    d'adoption (réception automatique contre ressaisie à la main)."""
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "poller")
    analytics_store.record_classification("t-2", "DEVIS", "c@d.fr", "poller")
    analytics_store.record_classification("t-3", "SPAM", "e@f.fr", "manuel")
    assert analytics_store.by_source(days=1) == [
        {"source": "poller", "count": 2},
        {"source": "manuel", "count": 1},
    ]


def test_les_tranches_de_delai_vides_sont_conservees():
    """
    La propriété qui compte le plus ici. Une tranche « plus de 24 h » absente du graphe se lit
    comme « pas de données » ; elle veut dire « aucun retard », c'est-à-dire exactement l'inverse
    — et la meilleure nouvelle du tableau de bord.
    """
    buckets = analytics_store.bucket_response_times([{"minutes": 5}, {"minutes": 30}])
    assert [b["tranche"] for b in buckets] == [label for label, _, _ in
                                               analytics_store.RESPONSE_BUCKETS]
    assert buckets[0]["count"] == 2
    assert all(b["count"] == 0 for b in buckets[1:])


def test_les_bornes_de_tranches_ne_se_chevauchent_pas():
    """Une durée pile sur une borne ne doit être comptée qu'une fois, et du bon côté."""
    rows = [{"minutes": 59.9}, {"minutes": 60}, {"minutes": 240}, {"minutes": 1440},
            {"minutes": 99999}]
    counts = {b["tranche"]: b["count"] for b in analytics_store.bucket_response_times(rows)}
    assert counts == {"moins d'1 h": 1, "1 à 4 h": 1, "4 à 24 h": 1, "plus de 24 h": 2}
    assert sum(counts.values()) == len(rows)


def test_bucket_ignore_les_durees_absentes():
    """Ne lève pas sur une ligne sans durée : la fonction est appelée sur des lectures réelles."""
    assert sum(b["count"] for b in
               analytics_store.bucket_response_times([{"minutes": None}, {}])) == 0


def test_histogramme_horaire_couvre_les_24_heures(monkeypatch, tmp_path):
    """Les heures creuses sont l'information recherchée (à comparer avec la fenêtre de réception
    réglée au §19) : un histogramme troué se lirait comme une courbe continue."""
    _fresh_analytics(monkeypatch, tmp_path)
    analytics_store.record_classification("t-1", "DEVIS", "a@b.fr", "manuel")
    hours = analytics_store.hourly_volume(days=1)
    assert len(hours) == 24
    assert [h["heure"] for h in hours][:3] == ["00 h", "01 h", "02 h"]
    assert sum(h["count"] for h in hours) == 1


def test_correspondants_classes_et_bornes(monkeypatch, tmp_path):
    _fresh_analytics(monkeypatch, tmp_path)
    for i in range(3):
        analytics_store.record_classification(f"a-{i}", "DEVIS", "gros@client.fr", "manuel")
    analytics_store.record_classification("b-1", "DEVIS", "petit@client.fr", "manuel")
    analytics_store.record_validation("a-0")

    top = analytics_store.top_senders(days=1)
    assert top[0] == {"expéditeur": "gros@client.fr", "e-mails": 3, "validés": 1}
    assert top[1]["validés"] == 0          # jamais None : la colonne alimente une barre
    assert len(analytics_store.top_senders(days=1, limit=1)) == 1

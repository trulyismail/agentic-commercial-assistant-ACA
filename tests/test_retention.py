"""
Tests de la rétention à deux vitesses du journal d'activité (§18, `aca/core/retention.py`).

Portée volontairement étroite : uniquement le raccordement ajouté au §18
(`ACTIVITY_SENSITIVE_RETENTION_DAYS` → `activity_log.purge_older_than(sensitive_days=…)`), pas une
suite complète de `retention.py` (les autres purges — Sheets, checkpoints, file d'attente — restent
vérifiées en direct, cf. CLAUDE.md Known gaps, faute de pouvoir simuler Google Sheets hors ligne).

Sans ce raccordement, la rétention à deux vitesses construite dans `activity_log.py` existerait mais
ne serait jamais appelée par le travail planifié réel — exactement le genre d'écart entre « construit »
et « branché » que ce projet corrige systématiquement (cf. §16.0, le planificateur lui-même).
"""
import sqlite3

from aca.core import retention
from aca.storage import activity_log


def _seed_two_rows():
    """Une action ordinaire (validation) et une action sensible (échec de connexion), toutes deux
    vieilles de plusieurs années."""
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin")
    activity_log.log(activity_log.ACTION_LOGIN_FAILED, "bob", "")
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2020-01-01 00:00:00'")
        conn.commit()


def test_purge_old_activity_uses_the_two_speed_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_log, "DB_PATH", str(tmp_path / "activity.sqlite"))
    activity_log.init_db()
    monkeypatch.setattr(retention, "ACTIVITY_RETENTION_DAYS", 30)
    monkeypatch.setattr(retention, "ACTIVITY_SENSITIVE_RETENTION_DAYS", 3650)

    _seed_two_rows()
    deleted = retention.purge_old_activity()

    assert deleted == 1  # seule la validation (bruit d'usage courant) est purgée
    remaining = activity_log.list_recent()
    assert len(remaining) == 1
    assert remaining[0]["action"] == activity_log.ACTION_LOGIN_FAILED


def test_purge_old_activity_explicit_override_wins_over_module_defaults(monkeypatch, tmp_path):
    """Un appelant qui précise `sensitive_days` explicitement doit être respecté, pas silencieusement
    remplacé par la constante du module."""
    monkeypatch.setattr(activity_log, "DB_PATH", str(tmp_path / "activity.sqlite"))
    activity_log.init_db()

    _seed_two_rows()
    deleted = retention.purge_old_activity(days=30, sensitive_days=1)

    assert deleted == 2  # les deux dépassent 1 jour de rétention sensible
    assert activity_log.list_recent() == []


def test_activity_sensitive_retention_defaults_longer_than_ordinary(monkeypatch):
    """La rétention sensible ne doit jamais être plus courte que la rétention ordinaire — ce serait
    l'inverse de l'intention du §4 item 4 des suggestions."""
    monkeypatch.delenv("ACTIVITY_SENSITIVE_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("ACTIVITY_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    import importlib

    reloaded = importlib.reload(retention)
    assert reloaded.ACTIVITY_SENSITIVE_RETENTION_DAYS >= reloaded.ACTIVITY_RETENTION_DAYS
    importlib.reload(retention)  # restaure l'état module pour les tests suivants

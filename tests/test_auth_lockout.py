"""
Tests unitaires du verrou progressif anti-brute-force (§14 item US-41) :
`aca/core/auth_lockout.py`. Fonctions pures, aucune dépendance Streamlit/réseau.
"""
from aca.core.auth_lockout import (
    LOCKOUT_MAX_SECONDS,
    MAX_ATTEMPTS_BEFORE_LOCKOUT,
    lockout_remaining_seconds,
    next_lockout_seconds,
)


# ── next_lockout_seconds ──────────────────────────────────────────────────────────────────────
def test_no_lockout_below_threshold():
    for attempts in range(MAX_ATTEMPTS_BEFORE_LOCKOUT):
        assert next_lockout_seconds(attempts) == 0.0


def test_lockout_starts_at_threshold():
    assert next_lockout_seconds(MAX_ATTEMPTS_BEFORE_LOCKOUT) == 30.0


def test_lockout_backs_off_exponentially():
    first = next_lockout_seconds(MAX_ATTEMPTS_BEFORE_LOCKOUT)
    second = next_lockout_seconds(MAX_ATTEMPTS_BEFORE_LOCKOUT + 1)
    third = next_lockout_seconds(MAX_ATTEMPTS_BEFORE_LOCKOUT + 2)
    assert second == first * 2
    assert third == first * 4


def test_lockout_capped_at_max():
    # Un nombre d'échecs très élevé ne doit jamais dépasser le plafond.
    assert next_lockout_seconds(MAX_ATTEMPTS_BEFORE_LOCKOUT + 20) == LOCKOUT_MAX_SECONDS


# ── lockout_remaining_seconds ─────────────────────────────────────────────────────────────────
def test_no_remaining_lockout_when_not_locked():
    assert lockout_remaining_seconds(locked_until=0.0, now=1000.0) == 0.0


def test_remaining_lockout_while_active():
    assert lockout_remaining_seconds(locked_until=1030.0, now=1000.0) == 30.0


def test_remaining_lockout_after_expiry():
    assert lockout_remaining_seconds(locked_until=1000.0, now=1030.0) == 0.0

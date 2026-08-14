"""
§24 — appareils de confiance : ce qui doit être vrai pour que sauter le second facteur reste sûr.

Ordonné par gravité décroissante, comme les autres fichiers de sécurité de cette suite. Les huit
premiers tests décrivent des façons dont ce contournement deviendrait un trou : jeton d'un autre
compte accepté, expiration ignorée, mot de passe changé sans effet, cookie rejoué depuis un autre
navigateur, jeton stocké en clair, fuite entre tenants. Les suivants vérifient le confort promis —
c'est-à-dire que la fonction sert effectivement à quelque chose.
"""
import os
import sqlite3
import time

import pytest

from aca.core import device_trust
from aca.storage import device_trust_store, user_store

FINGERPRINT = "empreinte-auth-de-test"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/203.0.0.0"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """
    Base d'appareils neuve à chaque test : ces lignes autorisent à sauter un facteur
    d'authentification, aucune ne doit fuir d'un test au suivant.

    `ACA_USERS_DB` n'est volontairement PAS redirigé ici : `user_store.DB_PATH` est figé à
    l'import (comportement préexistant dont dépend le reste de la suite), donc le redéfinir par
    test ne ferait rien tout en laissant croire à une isolation. Les tests qui créent un compte
    utilisent donc un identifiant qui leur est propre, dans la base temporaire de `conftest.py`.
    """
    monkeypatch.setenv("ACA_DEVICE_TRUST_DB", str(tmp_path / "device_trust.sqlite"))
    monkeypatch.delenv("ACA_TOTP_TRUST_DAYS", raising=False)


# ── Ce qui rendrait le mécanisme dangereux ──────────────────────────────────────────────────────

def test_token_issued_for_one_account_is_worthless_for_another():
    """Le pire cas : un jeton valide qui ouvrirait la porte d'un autre compte."""
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA)
    assert device_trust_store.verify("alice", token, FINGERPRINT, user_agent=UA) is True
    assert device_trust_store.verify("bob", token, FINGERPRINT, user_agent=UA) is False


def test_expired_authorisation_is_refused_and_deleted():
    """L'expiration est jugée côté serveur : le `max-age` du cookie est modifiable par qui détient
    le poste, il ne peut donc pas faire autorité."""
    token = device_trust.new_token()
    issued = time.time()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA, now=issued)
    later = issued + device_trust.trust_seconds() + 1
    assert device_trust_store.verify("alice", token, FINGERPRINT, user_agent=UA, now=later) is False
    assert device_trust_store.list_devices("alice") == []


def test_changing_the_password_revokes_every_remembered_device():
    """Révocation automatique par `auth_state_fingerprint`, sans qu'aucun appelant n'ait à y
    penser — la seule forme de révocation qu'on n'oublie pas de brancher."""
    user_store.create_user("pwd-user", "mot-de-passe-initial", role=user_store.ROLE_ADMIN)
    before = user_store.auth_state_fingerprint("pwd-user")
    token = device_trust.new_token()
    device_trust_store.remember("pwd-user", token, before, user_agent=UA)
    assert device_trust_store.verify("pwd-user", token, before, user_agent=UA) is True

    user_store.set_password("pwd-user", "un-tout-autre-mot-de-passe")
    after = user_store.auth_state_fingerprint("pwd-user")
    assert after != before
    assert device_trust_store.verify("pwd-user", token, after, user_agent=UA) is False


def test_resetting_the_totp_secret_also_revokes():
    """Même mécanisme : un `totp-off` suivi d'une réinscription ne doit laisser aucun appareil
    autorisé derrière lui."""
    user_store.create_user("totp-user", "mot-de-passe-initial", role=user_store.ROLE_ADMIN)
    user_store.set_totp_secret("totp-user", "JBSWY3DPEHPK3PXP")
    before = user_store.auth_state_fingerprint("totp-user")
    token = device_trust.new_token()
    device_trust_store.remember("totp-user", token, before, user_agent=UA)

    user_store.set_totp_secret("totp-user", "MFRGGZDFMZTWQ2LK")
    assert device_trust_store.verify(
        "totp-user", token, user_store.auth_state_fingerprint("totp-user"), user_agent=UA,
    ) is False


def test_cookie_replayed_from_another_browser_is_refused():
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA)
    assert device_trust_store.verify("alice", token, FINGERPRINT, user_agent="curl/8.4.0") is False


def test_the_token_itself_is_never_stored():
    """Une fuite de la base ne doit rendre aucun cookie rejouable."""
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA)
    with sqlite3.connect(os.environ["ACA_DEVICE_TRUST_DB"]) as conn:
        blob = " ".join(
            str(v) for row in conn.execute("SELECT * FROM trusted_devices") for v in row
        )
    assert token not in blob
    assert device_trust.token_hash(token) in blob


def test_devices_do_not_cross_tenants():
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA, org_id="acme")
    assert device_trust_store.verify(
        "alice", token, FINGERPRINT, user_agent=UA, org_id="acme",
    ) is True
    assert device_trust_store.verify(
        "alice", token, FINGERPRINT, user_agent=UA, org_id="autre",
    ) is False


def test_unknown_account_has_no_fingerprint_hence_no_trust():
    """Empreinte vide sur un compte inexistant : elle ne correspondra à aucun enregistrement, donc
    « pas de confiance » — le bon défaut, plutôt qu'une exception."""
    assert user_store.auth_state_fingerprint("personne") == ""


# ── Ce qui rendrait le mécanisme inutile ────────────────────────────────────────────────────────

def test_a_remembered_browser_is_accepted_within_the_window():
    token = device_trust.new_token()
    issued = time.time()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA, now=issued)
    almost = issued + device_trust.trust_seconds() - 60
    assert device_trust_store.verify(
        "alice", token, FINGERPRINT, user_agent=UA, now=almost,
    ) is True


def test_default_window_is_three_days():
    assert device_trust.trust_days() == 3
    assert device_trust.trust_seconds() == 3 * 86_400


def test_zero_days_disables_the_feature(monkeypatch):
    """`ACA_TOTP_TRUST_DAYS=0` est la seule façon, pour un déploiement strict, d'interdire ce
    compromis sans toucher au code (même convention que les travaux du planificateur)."""
    monkeypatch.setenv("ACA_TOTP_TRUST_DAYS", "0")
    assert device_trust.is_enabled() is False


def test_malformed_window_falls_back_to_the_default(monkeypatch):
    """Une variable illisible ne doit décider ni d'une désactivation muette ni d'un plantage à la
    connexion."""
    monkeypatch.setenv("ACA_TOTP_TRUST_DAYS", "trois")
    assert device_trust.trust_days() == device_trust.DEFAULT_TRUST_DAYS
    assert device_trust.is_enabled() is True


def test_revoke_all_forgets_every_browser_of_that_account_only():
    tokens = [device_trust.new_token() for _ in range(3)]
    for tok in tokens:
        device_trust_store.remember("alice", tok, FINGERPRINT, user_agent=UA)
    other = device_trust.new_token()
    device_trust_store.remember("bob", other, FINGERPRINT, user_agent=UA)

    assert device_trust_store.revoke_all("alice") == 3
    assert all(
        device_trust_store.verify("alice", tok, FINGERPRINT, user_agent=UA) is False
        for tok in tokens
    )
    assert device_trust_store.verify("bob", other, FINGERPRINT, user_agent=UA) is True


def test_revoke_token_forgets_only_that_browser():
    keep, drop = device_trust.new_token(), device_trust.new_token()
    for tok in (keep, drop):
        device_trust_store.remember("alice", tok, FINGERPRINT, user_agent=UA)
    assert device_trust_store.revoke_token(drop) is True
    assert device_trust_store.verify("alice", drop, FINGERPRINT, user_agent=UA) is False
    assert device_trust_store.verify("alice", keep, FINGERPRINT, user_agent=UA) is True


def test_purge_expired_spares_live_authorisations():
    live, dead = device_trust.new_token(), device_trust.new_token()
    now = time.time()
    device_trust_store.remember("alice", live, FINGERPRINT, user_agent=UA, now=now)
    device_trust_store.remember(
        "alice", dead, FINGERPRINT, user_agent=UA, now=now - device_trust.trust_seconds() - 10,
    )
    assert device_trust_store.purge_expired(now=now) == 1
    assert device_trust_store.verify("alice", live, FINGERPRINT, user_agent=UA, now=now) is True


def test_listing_never_exposes_a_usable_reference():
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA, label="Poste bureau")
    row = device_trust_store.list_devices("alice")[0]
    assert row["label"] == "Poste bureau"
    assert len(row["ref"]) == 8                        # extrait, jamais l'empreinte entière
    assert row["ref"] != device_trust.token_hash(token)
    assert token not in str(row)


def test_last_used_is_recorded_on_acceptance():
    """Sans cette date, la liste « Appareils de confiance » ne permettrait pas de repérer une
    autorisation dont quelqu'un d'autre se sert."""
    token = device_trust.new_token()
    device_trust_store.remember("alice", token, FINGERPRINT, user_agent=UA)
    assert device_trust_store.list_devices("alice")[0]["last_used_at"] is None
    device_trust_store.verify("alice", token, FINGERPRINT, user_agent=UA)
    assert device_trust_store.list_devices("alice")[0]["last_used_at"] is not None


# ── Le fragment de cookie ───────────────────────────────────────────────────────────────────────

def test_cookie_script_adds_secure_only_over_https():
    """Poser `Secure` en HTTP local ferait refuser le cookie par le navigateur, et la case à
    cocher n'aurait alors aucun effet visible — une panne parfaitement muette."""
    script = device_trust.cookie_script("jeton-de-test", 3600)
    assert "https:" in script                          # décidé à l'exécution, côté navigateur
    assert "; Secure" not in script.split("var secure")[0]   # jamais en dur dans la chaîne de base


def test_cookie_script_carries_the_token_and_the_window():
    script = device_trust.cookie_script("jeton-de-test", 4242)
    assert "jeton-de-test" in script
    assert "max-age=4242" in script
    assert device_trust.COOKIE_NAME in script


def test_forget_script_expires_the_cookie():
    assert "max-age=0" in device_trust.forget_script()
    assert device_trust.COOKIE_NAME in device_trust.forget_script()


def test_is_expired_treats_unreadable_records_as_expired():
    """En cas de doute sur une donnée de sécurité, on redemande le code."""
    assert device_trust.is_expired(None, time.time()) is True
    assert device_trust.is_expired("pas une date", time.time()) is True


def test_tokens_are_unique_and_long_enough():
    tokens = {device_trust.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(tok) >= 40 for tok in tokens)

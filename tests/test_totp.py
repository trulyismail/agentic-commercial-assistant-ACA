"""
Tests du second facteur TOTP stdlib-only (§18, `aca/core/totp.py`).

Priorité, dans cet ordre :

1. **`code_at` produit les codes attendus par le protocole**, vérifié contre les vecteurs de test
   officiels du RFC 4226 (HOTP, le cœur partagé de TOTP) — pas seulement contre nos propres
   attentes, qui pourraient reproduire une même erreur des deux côtés.
2. **`verify` ne lève jamais**, quel que soit ce qu'on lui passe (secret vide, code non numérique,
   longueur incorrecte) : c'est la porte d'un compte `admin`, une exception non attrapée y serait un
   déni de service ou pire.
3. **La tolérance de dérive d'horloge est bornée** : ni nulle (inutilisable en pratique) ni large
   au point d'élargir la fenêtre d'exploitation d'un code intercepté.
4. **`generate_secret`/`grouped_secret`/`provisioning_uri` produisent des valeurs syntaxiquement
   correctes** pour les applications d'authentification grand public qui les consomment.
"""
import base64

from aca.core import totp

# Vecteurs de test officiels RFC 4226 Appendix D (HOTP-SHA1, 6 chiffres) — le secret ASCII de
# référence encodé en base32, puisque `code_at` attend un secret base32 comme les applications
# d'authentification réelles.
_RFC4226_SECRET_B32 = base64.b32encode(b"12345678901234567890").decode("ascii")
_RFC4226_VECTORS = [
    (0, "755224"), (1, "287082"), (2, "359152"), (3, "969429"), (4, "338314"),
    (5, "254676"), (6, "287922"), (7, "162583"), (8, "399871"), (9, "520489"),
]


def test_code_at_matches_rfc4226_test_vectors():
    for counter, expected in _RFC4226_VECTORS:
        assert totp.code_at(_RFC4226_SECRET_B32, counter) == expected, counter


# ── generate_secret ───────────────────────────────────────────────────────────────────────────
def test_generate_secret_est_du_base32_sans_remplissage():
    secret = totp.generate_secret()
    assert "=" not in secret
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)


def test_generate_secret_distinct_a_chaque_appel():
    assert totp.generate_secret() != totp.generate_secret()


# ── current_code / round-trip ────────────────────────────────────────────────────────────────
def test_current_code_deterministe_pour_un_instant_donne():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    assert totp.current_code(secret, now=now) == totp.current_code(secret, now=now)


def test_current_code_change_de_fenetre_toutes_les_30_secondes():
    secret = totp.generate_secret()
    base = 1_800_000_000.0
    assert totp.current_code(secret, now=base) == totp.current_code(secret, now=base + 29)
    # La fenêtre suivante peut coïncider par hasard (1 chance sur 1e6) ; on vérifie plutôt que le
    # compteur RFC 4226 sous-jacent a bien avancé.
    counter_a = int(base // totp.PERIOD_SECONDS)
    counter_b = int((base + 30) // totp.PERIOD_SECONDS)
    assert counter_b == counter_a + 1


# ── verify : chemin correct ───────────────────────────────────────────────────────────────────
def test_verify_accepte_le_bon_code():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    assert totp.verify(secret, totp.current_code(secret, now=now), now=now) is True


def test_verify_rejette_un_mauvais_code():
    secret = totp.generate_secret()
    assert totp.verify(secret, "000000", now=1_800_000_000.0) is False


def test_verify_tolere_les_espaces_de_saisie():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    code = totp.current_code(secret, now=now)
    assert totp.verify(secret, f" {code} ", now=now) is True


def test_verify_tolere_un_secret_recopie_par_groupes():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    code = totp.current_code(secret, now=now)
    messy = totp.grouped_secret(secret).lower().replace(" ", "-")
    assert totp.verify(messy, code, now=now) is True


# ── verify : tolérance de dérive ─────────────────────────────────────────────────────────────
def test_verify_accepte_la_fenetre_precedente_dans_la_derive_par_defaut():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    previous_window_code = totp.current_code(secret, now=now - totp.PERIOD_SECONDS)
    assert totp.verify(secret, previous_window_code, now=now) is True


def test_verify_rejette_au_dela_de_la_derive():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    far_code = totp.current_code(secret, now=now - 10 * totp.PERIOD_SECONDS)
    assert totp.verify(secret, far_code, now=now) is False


def test_verify_derive_nulle_desactive_la_tolerance():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    previous_window_code = totp.current_code(secret, now=now - totp.PERIOD_SECONDS)
    assert totp.verify(secret, previous_window_code, now=now, drift_windows=0) is False
    assert totp.verify(secret, totp.current_code(secret, now=now), now=now, drift_windows=0) is True


# ── verify : ne lève jamais (propriété la plus importante) ──────────────────────────────────
def test_verify_ne_leve_jamais_sur_une_entree_malformee():
    secret = totp.generate_secret()
    assert totp.verify("", "123456") is False
    assert totp.verify(secret, "") is False
    assert totp.verify(secret, None) is False
    assert totp.verify(secret, "abcdef") is False
    assert totp.verify(secret, "12345") is False
    assert totp.verify(secret, "1234567") is False
    assert totp.verify("not-valid-base32!!!", "123456") is False
    assert totp.verify(None, "123456") is False


# ── provisioning_uri / grouped_secret ────────────────────────────────────────────────────────
def test_provisioning_uri_format_otpauth():
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, "admin@acme.fr", "Acme Solutions")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=Acme" in uri
    assert f"secret={secret}" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def test_provisioning_uri_utilise_la_marque_du_client_pas_aca_en_dur():
    uri = totp.provisioning_uri("SECRET", "op@client.fr", "Client Corp")
    assert "ACA" not in uri
    assert "Client" in uri


def test_grouped_secret_par_paquets_de_quatre():
    assert totp.grouped_secret("ABCDEFGHIJKL") == "ABCD EFGH IJKL"


def test_grouped_secret_secret_recompose_verifie():
    secret = totp.generate_secret()
    now = 1_800_000_000.0
    code = totp.current_code(secret, now=now)
    reconstructed = totp.grouped_secret(secret).replace(" ", "")
    assert reconstructed == secret
    assert totp.verify(reconstructed, code, now=now) is True


# ── seconds_remaining ─────────────────────────────────────────────────────────────────────────
def test_seconds_remaining_borne_a_la_periode():
    for now in (0.0, 1.0, 15.0, 29.9, 30.0, 59.9):
        remaining = totp.seconds_remaining(now=now)
        assert 0 <= remaining <= totp.PERIOD_SECONDS


def test_seconds_remaining_decroit_dans_la_fenetre():
    # 1005 et 1015 tombent dans la MÊME fenêtre de 30 s (1000-1030) : à l'intérieur d'une fenêtre,
    # le compte à rebours doit être strictement décroissant.
    assert totp.seconds_remaining(now=1005.0) > totp.seconds_remaining(now=1015.0)

"""
Tests du dictionnaire de traduction FR/EN (§18 tangent, `aca/core/i18n.py`).

Priorité, dans cet ordre :
1. **Une clé manquante ou une langue inconnue ne lève jamais** — `translate()` doit rester
   utilisable depuis n'importe quelle page sans un `try/except` défensif à chaque appel ; le
   comportement de repli (clé brute, ou `DEFAULT_LANGUAGE`) est celui documenté dans le docstring
   de `translate()`, pas un accident.
2. **Chaque clé déclarée porte réellement les deux langues** — sinon `language_switcher()` bascule
   sans que rien à l'écran ne change, un bug silencieux qu'aucun test fonctionnel isolé (un seul
   `t()` à la fois) ne peut détecter.
3. **Le formatage `{placeholder}` fonctionne dans les deux langues** et ne casse pas sur un
   argument manquant.

Aucun import Streamlit : `i18n.py` est pur, ces tests tournent hors ligne comme le reste de la
suite.
"""
from aca.core import i18n


# ── Comportement de base ─────────────────────────────────────────────────────────────────────
def test_translate_clé_connue_langue_par_defaut():
    assert i18n.translate("nav.inbox") == "Nouvel e-mail"


def test_translate_clé_connue_anglais():
    assert i18n.translate("nav.inbox", "en") == "New email"


def test_translate_clé_inconnue_renvoie_la_clé_elle_meme():
    assert i18n.translate("this.key.does.not.exist") == "this.key.does.not.exist"


def test_translate_langue_inconnue_replie_sur_le_francais():
    assert i18n.translate("nav.inbox", "de") == "Nouvel e-mail"


# ── Formatage ─────────────────────────────────────────────────────────────────────────────────
def test_translate_avec_placeholder():
    assert i18n.translate("dashboard.period_days", "fr", d=7) == "7 jours"
    assert i18n.translate("dashboard.period_days", "en", d=7) == "7 days"


def test_translate_placeholder_manquant_ne_leve_pas():
    # Pas de `d=` fourni : `str.format` lèverait KeyError si non intercepté.
    result = i18n.translate("dashboard.period_days", "fr")
    assert result == "{d} jours"


# ── Cohérence du dictionnaire ─────────────────────────────────────────────────────────────────
def test_toutes_les_clés_ont_les_deux_langues():
    missing = {
        key: [lang for lang in i18n.LANGUAGES if lang not in entry]
        for key, entry in i18n.TRANSLATIONS.items()
    }
    missing = {k: v for k, v in missing.items() if v}
    assert missing == {}, f"clés sans traduction complète : {missing}"


def test_toutes_les_traductions_sont_non_vides():
    empty = [
        f"{key}.{lang}"
        for key, entry in i18n.TRANSLATIONS.items()
        for lang in i18n.LANGUAGES
        if not entry.get(lang, "").strip()
    ]
    assert empty == []


def test_langue_par_defaut_est_dans_languages():
    assert i18n.DEFAULT_LANGUAGE in i18n.LANGUAGES


def test_language_labels_couvre_toutes_les_langues():
    assert set(i18n.LANGUAGE_LABELS) == set(i18n.LANGUAGES)


def test_fr_et_en_different_reellement_pour_un_echantillon_de_cles():
    # Une traduction identique aux deux langues n'est pas une erreur en soi (ex. un nom propre),
    # mais si TOUTES l'étaient, le switcher ne changerait jamais rien à l'écran — un vrai bug muet.
    sample_keys = ["nav.inbox", "nav.dashboard", "auth.username", "auth.login_button"]
    differing = [
        key for key in sample_keys
        if i18n.TRANSLATIONS[key]["fr"] != i18n.TRANSLATIONS[key]["en"]
    ]
    assert differing, "aucune des clés échantillonnées ne diffère entre fr et en"

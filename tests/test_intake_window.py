"""
Tests de la fenêtre de réception automatique (§19, `aca/core/intake_window.py`).

Priorité, dans cet ordre :

1. **Un réglage absent ne change rien au comportement existant.** C'est le contrat de tout le
   projet (« absent = fonctionnalité passée ») et il est critique ici : une installation qui
   tournait 24/7 avant ce module doit continuer, sinon la mise à jour arrête silencieusement la
   réception des e-mails de quelqu'un.
2. **Une saisie invalide ne fait jamais tomber le poller.** Ces valeurs viennent d'un formulaire ;
   si une heure mal saisie levait, la boucle mourrait et plus AUCUN e-mail ne serait relevé — une
   panne bien pire que le réglage raté qui l'a causée.
3. **La fenêtre de nuit (22:00 → 06:00) fonctionne**, car c'est le cas que la comparaison naïve
   `start <= t <= end` casse en silence, en produisant une plage vide.

`now` est toujours injecté : aucun test ne dépend de l'heure réelle de la machine.
"""
from datetime import datetime, time as dt_time

from aca.core import intake_window
from aca.storage import config_store


# ── Valeurs par défaut : le comportement d'avant §19 ─────────────────────────────────────────
def test_sans_reglage_la_reception_est_ouverte_en_permanence():
    cfg = intake_window.current_config()
    assert cfg["enabled"] is True
    assert cfg["days"] == intake_window.DEFAULT_DAYS
    # Un mardi à 3 h du matin : ouvert, exactement comme la boucle nue d'origine.
    assert intake_window.is_open(datetime(2026, 8, 4, 3, 0), cfg) is True


def test_sans_reglage_lintervalle_est_celui_dorigine():
    assert intake_window.current_config()["interval_seconds"] == 60


# ── Tolérance aux saisies ────────────────────────────────────────────────────────────────────
def test_parse_days_accepte_les_formats_courants():
    assert intake_window.parse_days("0,1,2,3,4") == (0, 1, 2, 3, 4)
    assert intake_window.parse_days("1;3") == (1, 3)
    assert intake_window.parse_days([2, 2, 0]) == (0, 2)


def test_parse_days_illisible_retombe_sur_tous_les_jours():
    # Ne jamais renvoyer un tuple vide : « aucun jour coché » voudrait dire « ne relève jamais ».
    assert intake_window.parse_days("") == intake_window.DEFAULT_DAYS
    assert intake_window.parse_days("lundi, mardi") == intake_window.DEFAULT_DAYS
    assert intake_window.parse_days(None) == intake_window.DEFAULT_DAYS


def test_parse_days_ignore_les_valeurs_hors_bornes():
    assert intake_window.parse_days("0,9,-2,3") == (0, 3)


def test_parse_time_accepte_plusieurs_ecritures():
    assert intake_window.parse_time("08:30", "00:00") == dt_time(8, 30)
    assert intake_window.parse_time("8h15", "00:00") == dt_time(8, 15)
    assert intake_window.parse_time("7", "00:00") == dt_time(7, 0)


def test_parse_time_illisible_retombe_sur_le_defaut():
    assert intake_window.parse_time("midi", "09:00") == dt_time(9, 0)
    assert intake_window.parse_time(None, "09:00") == dt_time(9, 0)


def test_parse_interval_borne_les_valeurs():
    assert intake_window.parse_interval("1") == intake_window.MIN_INTERVAL_SECONDS
    assert intake_window.parse_interval("999999") == intake_window.MAX_INTERVAL_SECONDS
    assert intake_window.parse_interval("abc") == intake_window.DEFAULT_INTERVAL_SECONDS
    assert intake_window.parse_interval("300") == 300


# ── Fenêtre horaire ──────────────────────────────────────────────────────────────────────────
def _cfg(**kwargs) -> dict:
    base = {
        "enabled": True, "days": (0, 1, 2, 3, 4),
        "start": dt_time(8, 0), "end": dt_time(19, 0), "interval_seconds": 300,
    }
    base.update(kwargs)
    return base


def test_dans_la_plage_un_jour_ouvre():
    assert intake_window.is_open(datetime(2026, 8, 4, 10, 0), _cfg()) is True   # mardi


def test_hors_de_la_plage_le_meme_jour():
    assert intake_window.is_open(datetime(2026, 8, 4, 7, 59), _cfg()) is False
    assert intake_window.is_open(datetime(2026, 8, 4, 19, 1), _cfg()) is False


def test_un_jour_non_coche_reste_ferme_meme_dans_la_plage():
    assert intake_window.is_open(datetime(2026, 8, 8, 10, 0), _cfg()) is False  # samedi


def test_desactivee_ferme_toujours():
    assert intake_window.is_open(datetime(2026, 8, 4, 10, 0), _cfg(enabled=False)) is False


def test_fenetre_de_nuit_a_cheval_sur_minuit():
    """
    22:00 → 06:00 : le cas que `start <= t <= end` rend vide. Le jour autorisé est celui où la
    fenêtre S'OUVRE, sinon une astreinte du lundi soir s'arrêterait net à minuit.
    """
    nuit = _cfg(days=(0,), start=dt_time(22, 0), end=dt_time(6, 0))  # lundi soir
    assert intake_window.is_open(datetime(2026, 8, 3, 23, 30), nuit) is True   # lundi 23h30
    assert intake_window.is_open(datetime(2026, 8, 4, 2, 0), nuit) is True     # mardi 2h (suite)
    assert intake_window.is_open(datetime(2026, 8, 4, 7, 0), nuit) is False    # mardi 7h : fini
    assert intake_window.is_open(datetime(2026, 8, 4, 23, 0), nuit) is False   # mardi soir : non


# ── Prochaine ouverture ──────────────────────────────────────────────────────────────────────
def test_next_opening_renvoie_maintenant_si_deja_ouvert():
    now = datetime(2026, 8, 4, 10, 0)
    assert intake_window.next_opening(now, _cfg()) == now


def test_next_opening_saute_au_lendemain_matin():
    nxt = intake_window.next_opening(datetime(2026, 8, 4, 20, 0), _cfg())  # mardi soir
    assert nxt == datetime(2026, 8, 5, 8, 0)                               # mercredi 8 h


def test_next_opening_saute_le_week_end():
    nxt = intake_window.next_opening(datetime(2026, 8, 7, 20, 0), _cfg())  # vendredi soir
    assert nxt == datetime(2026, 8, 10, 8, 0)                              # lundi 8 h


def test_next_opening_none_si_desactivee():
    assert intake_window.next_opening(datetime(2026, 8, 4, 10, 0), _cfg(enabled=False)) is None


# ── Phrase lisible ───────────────────────────────────────────────────────────────────────────
def test_describe_semaine_ouvrable():
    texte = intake_window.describe(_cfg())
    assert "Du lundi au vendredi" in texte
    assert "08:00" in texte and "19:00" in texte
    assert "5 min" in texte


def test_describe_24h_sur_24():
    texte = intake_window.describe(
        _cfg(days=(0, 1, 2, 3, 4, 5, 6), start=dt_time(0, 0), end=dt_time(23, 59))
    )
    assert "Tous les jours" in texte and "24 h/24" in texte


def test_describe_desactivee_le_dit():
    assert "désactivée" in intake_window.describe(_cfg(enabled=False))


# ── Réglages venant de l'interface ───────────────────────────────────────────────────────────
def test_les_reglages_de_linterface_priment_sur_les_defauts():
    config_store.set_setting(intake_window.SETTING_ENABLED, "0")
    try:
        assert intake_window.current_config()["enabled"] is False
    finally:
        config_store.set_setting(intake_window.SETTING_ENABLED, "")


def test_un_intervalle_regle_depuis_linterface_est_relu():
    config_store.set_setting(intake_window.SETTING_INTERVAL, "600")
    try:
        assert intake_window.current_config()["interval_seconds"] == 600
    finally:
        config_store.set_setting(intake_window.SETTING_INTERVAL, "")

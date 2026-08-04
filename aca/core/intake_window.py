"""
Quand la réception automatique doit-elle tourner ? (§19)

`poller.py` tournait jusqu'ici en boucle nue : un `time.sleep(POLL_INTERVAL_SECONDS)` et rien
d'autre. Deux conséquences que l'usage réel rend visibles :

1. **Aucun cadrage horaire.** Un e-mail arrivé à 3 h du matin était analysé à 3 h du matin, le
   Stratège consommait du quota, une alerte Slack partait — pour une équipe commerciale qui ne le
   verrait qu'à 9 h. Pire, une analyse en attente depuis six heures paraît « ancienne » alors que
   personne n'aurait pu la traiter plus tôt.
2. **Aucun réglage sans redémarrage.** L'intervalle vivait dans une variable d'environnement lue
   **à l'import** (`poller.POLL_INTERVAL_SECONDS`) ; le changer imposait d'éditer `.env` puis de
   relancer le processus.

Ce module ne répond qu'à une question, et le fait de façon **pure** : « à l'instant `now`,
la réception doit-elle tourner, et sinon, à quand la prochaine ouverture ? ». Aucun accès disque,
aucun import Streamlit, `now` toujours injecté (même posture que `auth_lockout.py`, `session.py` et
`scheduler.is_due`) — donc testable sans toucher à l'horloge ni à Gmail.

**Heures locales naïves, volontairement.** Une équipe commerciale énonce ses horaires en heure de
bureau (« de 8 h à 19 h »), pas en UTC ; convertir introduirait un décalage que personne n'a
demandé. Le processus tourne sur la machine de l'équipe (palier Solo, cf. `run_solo.py`), son
horloge locale *est* la bonne référence.

Les réglages sont lus via `config_store` (donc modifiables depuis « Réglages » sans redémarrage),
avec repli sur l'environnement puis sur les valeurs par défaut — même chaîne que
`app._calendly_url()`.
"""
import os
from datetime import datetime, time as dt_time, timedelta

from aca.storage import config_store

# Réglages (clés `config_store` = noms des variables d'environnement, comme partout ailleurs).
SETTING_ENABLED = "INTAKE_ENABLED"
SETTING_DAYS = "INTAKE_DAYS"
SETTING_START = "INTAKE_START"
SETTING_END = "INTAKE_END"
SETTING_INTERVAL = "INTAKE_INTERVAL_SECONDS"

# Par défaut : ouvert en permanence, toute la semaine, toutes les 60 s — c'est-à-dire exactement le
# comportement d'avant ce module. Un réglage absent ne doit jamais changer ce qu'une installation
# existante faisait déjà (même contrat que le reste du projet : « absent = fonctionnalité passée »).
DEFAULT_ENABLED = True
DEFAULT_DAYS = (0, 1, 2, 3, 4, 5, 6)
DEFAULT_START = "00:00"
DEFAULT_END = "23:59"
DEFAULT_INTERVAL_SECONDS = 60

MIN_INTERVAL_SECONDS = 15      # en deçà, on martèle l'API Gmail sans rien y gagner
MAX_INTERVAL_SECONDS = 6 * 3600

DAY_LABELS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")


def _setting(key: str, env_default: str) -> str:
    """`config_store` (réglé depuis l'UI) → variable d'environnement → défaut."""
    stored = config_store.get_setting(key)
    if stored is not None and str(stored).strip():
        return str(stored).strip()
    return os.getenv(key, env_default)


def parse_days(raw) -> tuple:
    """
    "0,1,2,3,4" -> (0, 1, 2, 3, 4). Lundi = 0, comme `datetime.weekday()`.

    Tolérant par construction : ce champ vient d'un formulaire, et une valeur illisible ne doit pas
    faire tomber le poller (il tournerait alors *jamais*, une panne silencieuse pire que le réglage
    raté). Une entrée vide ou entièrement invalide retombe sur « tous les jours ».
    """
    if isinstance(raw, (list, tuple, set)):
        candidates = list(raw)
    else:
        candidates = str(raw or "").replace(";", ",").split(",")
    days = []
    for item in candidates:
        try:
            day = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    return tuple(sorted(days)) or DEFAULT_DAYS


def parse_time(raw, fallback: str) -> dt_time:
    """"HH:MM" -> time. Même tolérance que `parse_days`, et pour la même raison."""
    for value in (raw, fallback):
        text = str(value or "").strip()
        if not text:
            continue
        for fmt in ("%H:%M", "%H:%M:%S", "%Hh%M", "%H"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            return dt_time(parsed.hour, parsed.minute)
    return dt_time(0, 0)


def parse_interval(raw) -> int:
    """Intervalle borné. Hors bornes ou illisible = valeur par défaut, jamais une exception."""
    try:
        seconds = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


def current_config() -> dict:
    """Les réglages effectifs, résolus. Le seul point qui touche `config_store`/l'environnement."""
    enabled = _setting(SETTING_ENABLED, "1" if DEFAULT_ENABLED else "0")
    return {
        "enabled": str(enabled).strip().lower() in ("1", "true", "yes", "oui", "on"),
        "days": parse_days(_setting(SETTING_DAYS, ",".join(str(d) for d in DEFAULT_DAYS))),
        "start": parse_time(_setting(SETTING_START, DEFAULT_START), DEFAULT_START),
        "end": parse_time(_setting(SETTING_END, DEFAULT_END), DEFAULT_END),
        "interval_seconds": parse_interval(
            _setting(SETTING_INTERVAL, os.getenv("POLL_INTERVAL_SECONDS",
                                                 str(DEFAULT_INTERVAL_SECONDS)))
        ),
    }


def _within_daily_window(moment: dt_time, start: dt_time, end: dt_time) -> bool:
    """
    Fenêtre journalière, y compris **à cheval sur minuit** (22:00 → 06:00).

    Sans ce cas, une équipe d'astreinte réglant 20 h → 4 h obtiendrait une fenêtre vide : `start <=
    t <= end` est faux partout dès que `start > end`. Le traiter ici plutôt que d'interdire la
    saisie évite une règle arbitraire dans le formulaire.
    """
    if start <= end:
        return start <= moment <= end
    return moment >= start or moment <= end


def is_open(now: datetime, config: dict = None) -> bool:
    """La réception automatique doit-elle tourner à l'instant `now` ?"""
    cfg = config or current_config()
    if not cfg["enabled"]:
        return False
    start, end = cfg["start"], cfg["end"]
    if start > end:
        # Fenêtre nocturne : le jour autorisé est celui où la fenêtre s'OUVRE. Sinon un réglage
        # « lundi 22:00 → 06:00 » s'arrêterait net à minuit, au milieu de la plage demandée.
        day = now.weekday() if now.time() >= start else (now.weekday() - 1) % 7
    else:
        day = now.weekday()
    if day not in cfg["days"]:
        return False
    return _within_daily_window(now.time(), start, end)


def next_opening(now: datetime, config: dict = None):
    """
    Prochaine ouverture de la fenêtre, pour afficher « prochaine relève à … » plutôt que de laisser
    la personne deviner. Renvoie `now` si la fenêtre est déjà ouverte, `None` si la réception est
    désactivée ou si aucun jour n'est coché. Cherche sur 8 jours (le jour courant + une semaine
    complète) : au-delà, il n'y a par construction pas de prochaine ouverture.
    """
    cfg = config or current_config()
    if not cfg["enabled"]:
        return None
    if is_open(now, cfg):
        return now
    for offset in range(0, 9):
        opening = (now + timedelta(days=offset)).replace(
            hour=cfg["start"].hour, minute=cfg["start"].minute, second=0, microsecond=0,
        )
        if opening < now:
            continue
        if is_open(opening, cfg):
            return opening
    return None


def describe(config: dict = None) -> str:
    """
    Phrase lisible pour l'interface — « Du lundi au vendredi, de 08:00 à 19:00 — vérification
    toutes les 5 min ».

    Écrite ici plutôt que dans la page : c'est la même phrase dans la barre latérale et dans les
    réglages, et deux formulations divergentes pour un même réglage sèment le doute sur lequel des
    deux écrans dit vrai.
    """
    cfg = config or current_config()
    if not cfg["enabled"]:
        return "Réception automatique désactivée."

    days = tuple(cfg["days"])
    if len(days) == 7:
        days_text = "Tous les jours"
    elif days == (0, 1, 2, 3, 4):
        days_text = "Du lundi au vendredi"
    elif days == (5, 6):
        days_text = "Le week-end"
    else:
        days_text = ", ".join(DAY_LABELS[d] for d in days)

    start, end = cfg["start"], cfg["end"]
    if (start.hour, start.minute) == (0, 0) and (end.hour, end.minute) >= (23, 59):
        hours_text = "24 h/24"
    else:
        hours_text = f"de {start.strftime('%H:%M')} à {end.strftime('%H:%M')}"

    seconds = cfg["interval_seconds"]
    if seconds % 3600 == 0:
        every = f"{seconds // 3600} h"
    elif seconds % 60 == 0:
        every = f"{seconds // 60} min"
    else:
        every = f"{seconds} s"
    return f"{days_text}, {hours_text} — vérification toutes les {every}."

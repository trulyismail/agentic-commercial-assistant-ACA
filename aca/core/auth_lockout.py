"""
Verrou progressif anti-brute-force sur le gate mot de passe optionnel de l'UI (§14 item US-41,
audit de sécurité du 2026-07-21 : `ui.py._check_auth()` comparait le mot de passe sans aucune
limite de tentatives). Logique pure et testable sans Streamlit — `ui.py` stocke les compteurs dans
`st.session_state` (qui survit aux reruns d'une même session navigateur) et appelle ces fonctions
pour décider si une tentative est autorisée.
"""

MAX_ATTEMPTS_BEFORE_LOCKOUT = 5
LOCKOUT_BASE_SECONDS = 30
LOCKOUT_MAX_SECONDS = 900  # 15 min — plafond pour ne jamais bloquer indéfiniment un utilisateur légitime


def lockout_remaining_seconds(locked_until: float, now: float) -> float:
    """Secondes restantes avant la prochaine tentative autorisée (0 si aucun verrou actif)."""
    return max(0.0, locked_until - now)


def next_lockout_seconds(failed_attempts: int) -> float:
    """
    Délai de verrouillage après `failed_attempts` échecs consécutifs : 0 en dessous du seuil, puis
    un backoff exponentiel (30s, 60s, 120s, ...) plafonné à `LOCKOUT_MAX_SECONDS` — un bot ne peut
    plus tester 10 000 mots de passe d'affilée, sans pour autant verrouiller un humain distrait
    plus de 15 minutes.
    """
    if failed_attempts < MAX_ATTEMPTS_BEFORE_LOCKOUT:
        return 0.0
    exponent = failed_attempts - MAX_ATTEMPTS_BEFORE_LOCKOUT
    return min(LOCKOUT_BASE_SECONDS * (2**exponent), LOCKOUT_MAX_SECONDS)

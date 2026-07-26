"""
Durée de vie et expiration des sessions authentifiées (§15.1.7 de docs/ACAM_roadmap.md).

Avant ce module, une session Streamlit validée (`st.session_state.authed = True`) restait valable
**indéfiniment** tant que l'onglet du navigateur vivait : un poste laissé déverrouillé restait
utilisable des jours durant, et aucun mécanisme ne permettait d'invalider une session en cours.
C'est le trou que l'audit §15.1.7 signalait (« cookie HMAC dashboard existe ; à ajouter :
TTL/expiration explicite + invalidation »).

Deux bornes complémentaires, la plus stricte des deux l'emportant :

- **TTL absolu** (`ACA_SESSION_TTL_SECONDS`, défaut 8 h) : au bout de N secondes il faut se
  reconnecter, quelle que soit l'activité — borne le vol de session dans le temps.
- **Délai d'inactivité** (`ACA_SESSION_IDLE_SECONDS`, défaut 30 min) : une session inutilisée
  expire même si le TTL absolu court encore — c'est ce qui couvre le poste non verrouillé.

Logique pure et testable sans Streamlit (même parti pris que `auth_lockout.py`) : `ui.py` range le
dictionnaire renvoyé par `new_session()` dans `st.session_state` et rappelle `is_valid()` à chaque
rerun. Les deux durées sont lues DYNAMIQUEMENT (jamais figées à l'import — même leçon que
`DATABASE_URL`/`ACA_ORG_ID` ailleurs dans le projet), donc réglables sans redémarrage et testables
par monkeypatch.
"""
import os

DEFAULT_TTL_SECONDS = 8 * 3600      # 8 h — une journée de travail
DEFAULT_IDLE_SECONDS = 30 * 60      # 30 min sans interaction


def session_ttl_seconds() -> int:
    """TTL absolu configuré (`ACA_SESSION_TTL_SECONDS`), ou le défaut. ≤ 0 ⇒ pas de borne absolue."""
    try:
        return int(os.getenv("ACA_SESSION_TTL_SECONDS") or DEFAULT_TTL_SECONDS)
    except ValueError:
        return DEFAULT_TTL_SECONDS


def session_idle_seconds() -> int:
    """Délai d'inactivité configuré (`ACA_SESSION_IDLE_SECONDS`). ≤ 0 ⇒ pas de borne d'inactivité."""
    try:
        return int(os.getenv("ACA_SESSION_IDLE_SECONDS") or DEFAULT_IDLE_SECONDS)
    except ValueError:
        return DEFAULT_IDLE_SECONDS


def new_session(username: str, role: str, now: float) -> dict:
    """Ouvre une session pour `username` : identité, rôle, instant d'ouverture et dernier accès."""
    return {"username": username, "role": role, "started_at": now, "last_seen": now}


def expiry_reason(session: dict, now: float) -> str:
    """
    Motif d'expiration (`"absolute"` / `"idle"`), ou `None` si la session est encore valable.

    Renvoyer le motif plutôt qu'un simple booléen permet à `ui.py` d'afficher un message honnête
    (« session expirée » vs. « déconnecté par inactivité ») au lieu d'un refus opaque qui
    ressemblerait à un bug.
    """
    if not session:
        return "absolute"
    ttl = session_ttl_seconds()
    if ttl > 0 and now - session.get("started_at", 0) >= ttl:
        return "absolute"
    idle = session_idle_seconds()
    if idle > 0 and now - session.get("last_seen", 0) >= idle:
        return "idle"
    return None


def is_valid(session: dict, now: float) -> bool:
    """La session est-elle encore utilisable à l'instant `now` ?"""
    return expiry_reason(session, now) is None


def touch(session: dict, now: float) -> dict:
    """
    Repousse le compteur d'inactivité (à appeler à chaque interaction). Ne touche JAMAIS
    `started_at` : le TTL absolu ne doit pas pouvoir être prolongé indéfiniment par de l'activité,
    sinon une session volée mais maintenue active ne meurt jamais.
    """
    if session:
        session["last_seen"] = now
    return session

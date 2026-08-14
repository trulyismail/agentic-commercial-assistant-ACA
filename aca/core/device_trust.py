"""
« Se souvenir de cet appareil » — logique pure du contournement borné du second facteur (§24).

LE PROBLÈME. TOTP est exigé des comptes `admin` (`user_store.TOTP_REQUIRED_ROLES`), à chaque
connexion, indéfiniment. Sur un poste personnel utilisé plusieurs fois par jour, cela produit une
friction quotidienne dont l'issue habituelle n'est pas « plus de sécurité » mais un contournement
maison : secret partagé entre collègues, application d'authentification unique pour toute l'équipe,
ou désactivation pure et simple. Une protection qu'on abandonne ne protège rien.

CE QUI EST RÉELLEMENT AFFAIBLI, DIT FRANCHEMENT. Un appareil mémorisé saute **le second facteur, et
lui seul**. Le mot de passe reste exigé à chaque connexion — ce module n'ouvre jamais de session, il
répond à une seule question : « faut-il redemander le code ? ». On passe donc, pour la fenêtre
choisie et sur ce navigateur précis, de « mot de passe + TOTP » à « mot de passe + possession d'un
secret déposé dans ce navigateur ». C'est un facteur de moins que l'idéal, et c'est le sens même de
la case à cocher : le compromis est choisi, pas subi.

POURQUOI UN VRAI SECRET ALÉATOIRE, ET NON L'EMPREINTE (IP, user-agent) DÉJÀ PRÉSENTE. Le projet
calcule déjà un `device_id` dans `activity_log.device_fingerprint()`, et s'en servir ici aurait été
tentant. Ce serait une faute : cette empreinte n'est **pas un secret**. Deux personnes derrière la
même IP publique (le NAT d'un bureau) avec le même navigateur produisent la même empreinte —
quiconque connaît le mot de passe depuis le même réseau sauterait alors le second facteur. Vérifié
plutôt que supposé (cf. `docs/PROJECT_JOURNAL.md`) : un composant Streamlit **peut** déposer un
cookie que `st.context.cookies` relit ensuite, donc un jeton aléatoire de 256 bits est réalisable
ici — le mécanisme qu'emploient les vraies implémentations de « remember this device ».

CE QUI EST STOCKÉ CÔTÉ SERVEUR N'EST PAS LE JETON, mais son empreinte SHA-256. Le jeton fait 32
octets d'aléa cryptographique : contrairement à un mot de passe, il n'a ni sel ni étirement à
recevoir, parce qu'il n'a aucune faiblesse d'entropie à compenser — un SHA-256 simple ne se force
pas plus vite qu'un espace de 2^256. Une fuite de la base ne rend donc aucun cookie rejouable.

RÉVOCATION SANS COUPLAGE : `auth_fingerprint`. Chaque enregistrement porte l'empreinte du mot de
passe haché ET du secret TOTP au moment de l'émission. Changer le mot de passe, ou réinitialiser le
second facteur, change cette empreinte et invalide **d'un coup tous les appareils mémorisés**, sans
que `user_store` ait à connaître ce module (technique du `session_auth_hash` de Django). Une
révocation qui repose sur un appel explicite est une révocation qu'on oublie de brancher le jour où
un troisième chemin de changement de mot de passe apparaît.

Pur, sans Streamlit ni SQLite (même posture que `session.py`, `auth_lockout.py`, `totp.py`) : `now`
est toujours injecté, donc tout est testable sans toucher à l'horloge.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets

# Nom du cookie. Préfixé `aca_` pour ne pas entrer en collision avec `_streamlit_xsrf`, déjà posé
# par le serveur sur la même origine.
COOKIE_NAME = "aca_device"

# Durée par défaut, en jours. 3 : la valeur demandée. `0` (ou négatif) DÉSACTIVE complètement la
# fonction — même convention que les travaux du planificateur (`ACA_SCHEDULE_*_HOURS`), et seule
# façon pour un déploiement strict d'interdire ce compromis sans modifier le code.
DEFAULT_TRUST_DAYS = 3

TOKEN_BYTES = 32  # 256 bits


def trust_days() -> int:
    """Fenêtre de confiance en jours (`ACA_TOTP_TRUST_DAYS`). Valeur illisible ⇒ défaut."""
    raw = os.getenv("ACA_TOTP_TRUST_DAYS")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_TRUST_DAYS
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        # Une variable malformée ne doit pas décider en silence d'une durée de sécurité : on
        # retombe sur la valeur documentée, plutôt que sur 0 (qui désactiverait la fonction sans
        # le dire) ou sur une exception (qui empêcherait toute connexion).
        return DEFAULT_TRUST_DAYS


def is_enabled() -> bool:
    """La mémorisation d'appareil est-elle proposée ? (`ACA_TOTP_TRUST_DAYS=0` ⇒ non.)"""
    return trust_days() > 0


def trust_seconds() -> int:
    return max(0, trust_days()) * 86_400


def new_token() -> str:
    """Jeton d'appareil : 32 octets d'aléa cryptographique, sûrs pour une URL et un cookie."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """Empreinte stockée côté serveur. Voir l'en-tête : pas de sel, et c'est volontaire."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def auth_fingerprint(password_hash: str, totp_secret: str = None) -> str:
    """
    Empreinte de l'état d'authentification du compte au moment où l'appareil est mémorisé.

    Recomparée à chaque reconnexion : si le mot de passe a changé, ou si le second facteur a été
    réinitialisé, elle ne correspond plus et l'appareil cesse d'être reconnu — révocation
    automatique, sans qu'aucun appelant n'ait à y penser.
    """
    raw = f"{password_hash or ''}|{totp_secret or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def expires_at(now: float) -> float:
    return float(now) + trust_seconds()


def is_expired(expiry: float, now: float) -> bool:
    """
    Expiration jugée CÔTÉ SERVEUR.

    Le `max-age` du cookie est une politesse envers le navigateur, jamais une garantie : il est
    modifiable par qui détient le poste. Un enregistrement illisible est traité comme expiré —
    en cas de doute sur une donnée de sécurité, on redemande le code.
    """
    try:
        return float(now) >= float(expiry)
    except (TypeError, ValueError):
        return True


def user_agent_hash(user_agent: str) -> str:
    """
    Empreinte du navigateur qui a demandé la mémorisation.

    Vérification secondaire : un cookie rejoué depuis un autre navigateur ne correspond plus et
    l'écran redemande simplement le code. Le coût du faux positif est connu et accepté — une mise à
    jour du navigateur change sa chaîne de version, donc au pire un code de plus à saisir. Ce n'est
    PAS une authentification : c'est un cran supplémentaire, gratuit, contre le rejeu.
    """
    return hashlib.sha256((user_agent or "").encode("utf-8")).hexdigest()[:32]


def cookie_script(token: str, max_age_seconds: int) -> str:
    """
    Fragment HTML/JS déposant le cookie d'appareil sur l'origine parente.

    Streamlit n'expose aucune écriture de cookie côté Python (`st.context.cookies` est en lecture
    seule) ; un composant `components.html` s'exécute en revanche dans une iframe `srcdoc` qui
    hérite de l'origine de la page — vérifié en conditions réelles avant d'écrire ce module, pas
    déduit de la documentation.

    `Secure` n'est ajouté QUE si la page est servie en HTTPS : le poser en HTTP local ferait
    refuser le cookie par le navigateur, et la case à cocher n'aurait alors aucun effet visible —
    une panne muette, la pire espèce pour une fonction de confort. `HttpOnly` est hors de portée
    par construction (un cookie posé en JavaScript est lisible en JavaScript) : limite assumée de
    ce mécanisme, notée ici plutôt que passée sous silence.
    """
    base = f"{COOKIE_NAME}={token}; path=/; max-age={int(max_age_seconds)}; SameSite=Lax"
    return (
        "<script>(function(){try{"
        f"var base={json.dumps(base)};"
        "var secure=(window.parent&&window.parent.location.protocol==='https:')?'; Secure':'';"
        "window.parent.document.cookie=base+secure;"
        "}catch(e){}})();</script>"
    )


def forget_script() -> str:
    """Efface le cookie d'appareil côté navigateur (révocation, déconnexion)."""
    return (
        "<script>(function(){try{"
        f"window.parent.document.cookie={json.dumps(COOKIE_NAME + '=; path=/; max-age=0; SameSite=Lax')};"
        "}catch(e){}})();</script>"
    )

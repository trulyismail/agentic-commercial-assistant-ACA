"""
Second facteur TOTP (§18) — RFC 6238, bibliothèque standard uniquement.

**Le manque comblé.** `user_store.py` fait déjà correctement le difficile : PBKDF2 avec sel par
utilisateur, coût d'itérations stocké dans le hachage, comparaison à temps constant, hachage factice
pour que « compte inconnu » et « mot de passe faux » coûtent le même temps, verrou progressif (§14).
Il restait un facteur unique — pour un compte `admin` capable de créer d'autres administrateurs, de
rediriger les alertes commerciales vers une autre adresse et de curer la base de connaissances que
l'IA citera aux prospects. C'était le dernier maillon faible d'une authentification par ailleurs
solide.

**Pourquoi la bibliothèque standard, et pas `pyotp` ni `cryptography`.** `cryptography` est présent
dans l'environnement, mais **en dépendance transitive** (il arrive via `google-auth`) : l'importer
directement rejouerait exactement le piège relevé au §15.3.8 — « requirements.txt est épinglé » était
une fausse assurance, puisque les dépendances indirectes n'y figuraient pas. Un algorithme de
quarante lignes tenant dans `hmac`/`hashlib`/`struct`/`base64` ne justifie ni une dépendance
nouvelle, ni l'usage détourné d'une dépendance qui n'est pas la nôtre. Même posture que
[slack_verify.py](aca/core/slack_verify.py), volontairement « pur/stdlib ».

**Portée assumée.** Le TOTP est réservé aux comptes `admin` (cf. `user_store.totp_required`).
L'imposer à un opérateur qui valide vingt leads par jour se paierait en contournements — secret
partagé entre collègues, application d'authentification installée sur le seul téléphone du service.
Une protection qu'on contourne protège moins qu'une protection qu'on n'impose pas.

**Ce qui n'est pas fait, et pourquoi.** Pas de codes de secours ni de récupération en libre-service :
sur ce déploiement, un administrateur qui perd son téléphone est débloqué en ligne de commande
(`python -m aca.storage.user_store totp-off <compte>`) par quelqu'un ayant accès à la machine. Un
mécanisme de récupération est précisément l'endroit où l'on affaiblit un second facteur, et il n'a de
sens qu'avec un canal de confiance (adresse vérifiée, support identifié) qui n'existe pas ici.
"""
import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

# Paramètres RFC 6238 « par défaut », ceux que lisent Google Authenticator, Aegis, 1Password, etc.
# sans configuration particulière. Les changer casserait la compatibilité avec ces applications, donc
# rendrait la fonctionnalité inutilisable en pratique.
DIGITS = 6
PERIOD_SECONDS = 30
ALGORITHM = "SHA1"  # exigé par la quasi-totalité des applications d'authentification grand public

# Tolérance : ±1 fenêtre, soit une minute au total. Le décalage d'horloge d'un téléphone et le temps
# de recopie d'un code à six chiffres rendent une tolérance nulle inutilisable ; une tolérance large
# élargirait d'autant la fenêtre d'exploitation d'un code intercepté.
DEFAULT_DRIFT_WINDOWS = 1

SECRET_BYTES = 20  # 160 bits, la taille recommandée pour HMAC-SHA1


def generate_secret() -> str:
    """Nouveau secret partagé, en base32 sans remplissage — le format attendu par les applications."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    """
    Décode un secret base32, en tolérant les espaces, les tirets et l'absence de remplissage.

    Les applications d'authentification affichent le secret par groupes de quatre caractères
    (`JBSW Y3DP EHPK`) et les gens le recopient tel quel : refuser ces espaces produirait un « code
    invalide » incompréhensible alors que la saisie est correcte.
    """
    cleaned = (secret or "").replace(" ", "").replace("-", "").upper()
    if not cleaned:
        raise ValueError("Secret TOTP vide.")
    padding = "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned + padding, casefold=True)


def code_at(secret: str, counter: int) -> str:
    """Code HOTP pour un compteur donné (RFC 4226) — le cœur partagé de HOTP et TOTP."""
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    # Troncature dynamique : les 4 bits de poids faible du dernier octet désignent où lire.
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** DIGITS)).zfill(DIGITS)


def current_code(secret: str, now: float = None) -> str:
    """Code attendu à l'instant `now` (injectable, donc testable sans toucher à l'horloge)."""
    return code_at(secret, int((now if now is not None else time.time()) // PERIOD_SECONDS))


def verify(secret: str, code: str, now: float = None,
           drift_windows: int = DEFAULT_DRIFT_WINDOWS) -> bool:
    """
    Vérifie un code à six chiffres, avec tolérance de dérive d'horloge.

    Comparaison à **temps constant** (`hmac.compare_digest`), comme partout ailleurs dans ce projet :
    un `==` sur un code court fuit sa progression caractère par caractère au chronomètre. La boucle
    parcourt **toutes** les fenêtres même après une correspondance, pour la même raison — s'arrêter
    tôt rendrait le temps de réponse dépendant de la fenêtre qui a réussi, donc du décalage
    d'horloge de la victime.

    Un secret vide ou illisible renvoie `False` sans lever : le gate d'un compte dont le secret a été
    corrompu doit refuser l'accès, pas afficher une trace d'exception.
    """
    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    try:
        counter = int((now if now is not None else time.time()) // PERIOD_SECONDS)
    except (TypeError, ValueError):
        return False
    matched = False
    for offset in range(-abs(drift_windows), abs(drift_windows) + 1):
        try:
            candidate = code_at(secret, counter + offset)
        except (ValueError, TypeError, binascii.Error):
            return False
        if hmac.compare_digest(candidate, cleaned):
            matched = True
    return matched


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """
    URI `otpauth://` à encoder en QR code, ou à saisir à la main.

    `issuer` est la marque du client (jeton `BRAND_NAME`), pas « ACA » en dur : dans l'application
    d'authentification de la personne, l'entrée doit porter le nom qu'elle reconnaît, aux côtés de ses
    autres comptes professionnels. C'est le seul endroit où la marque blanche du §17 touche la
    sécurité, et l'oublier produirait une ligne « ACA » énigmatique sur le téléphone d'un client.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={(secret or '').replace(' ', '')}"
        f"&issuer={quote(issuer, safe='')}&algorithm={ALGORITHM}"
        f"&digits={DIGITS}&period={PERIOD_SECONDS}"
    )


def grouped_secret(secret: str) -> str:
    """Secret présenté par groupes de quatre, pour une recopie manuelle sans erreur."""
    clean = (secret or "").replace(" ", "")
    return " ".join(clean[i:i + 4] for i in range(0, len(clean), 4))


def seconds_remaining(now: float = None) -> int:
    """
    Secondes restantes avant expiration du code courant.

    Affiché à côté du champ de saisie : sans ce repère, une personne tape un code qui expire pendant
    la frappe et croit s'être trompée. Petit détail, mais c'est la principale cause d'échec ressenti
    d'un second facteur.
    """
    reference = now if now is not None else time.time()
    return int(PERIOD_SECONDS - (reference % PERIOD_SECONDS))

"""
Chaînage par hachage, tamper-evident — mécanisme partagé par les journaux inviolables du projet.

Extrait de [audit_log.py](aca/storage/audit_log.py) (§15.2.7) au moment où un second journal a eu
besoin exactement de la même garantie : le **journal d'activité** (§17,
[activity_log.py](aca/storage/activity_log.py)), qui trace toutes les actions et pas seulement les
validations. Recopier une quinzaine de lignes de cryptographie dans un deuxième module, c'est
garantir qu'un jour l'un des deux sera durci et pas l'autre — la clé HMAC lue dynamiquement d'un
côté et figée à l'import de l'autre, un séparateur de champ changé ici mais pas là. Une seule
implémentation, deux appelants.

Principe : l'empreinte d'une ligne dépend de son contenu **et** de l'empreinte de la ligne
précédente. Modifier ou supprimer discrètement une ligne ancienne casse toutes les empreintes
suivantes, ce qu'une vérification séquentielle détecte et localise.

Les deux limites énoncées dans `audit_log.py` valent identiquement ici et ne sont pas répétées à
chaque appel : c'est **tamper-evident**, pas tamper-proof (sans `ACA_AUDIT_HMAC_KEY`, qui peut
écrire dans le fichier peut recalculer toute la chaîne), et une vraie inviolabilité demanderait un
stockage append-only (WORM) ou un ancrage externe.
"""
import hashlib
import hmac
import os

# Séparateur d'unité ASCII (0x1F) : jamais présent dans une adresse, un nom, une classification ou
# un libellé d'action, donc deux lignes différentes ne peuvent pas produire la même chaîne canonique
# par concaténation ambiguë (« ab | c » vs « a | bc »).
FIELD_SEPARATOR = "\x1f"


def digest(payload: str) -> str:
    """
    Empreinte d'une charge utile : HMAC-SHA256 si `ACA_AUDIT_HMAC_KEY` est réglée, SHA-256 sinon.

    Clé lue **dynamiquement**, jamais figée à l'import — même leçon que `DATABASE_URL` dans
    `vector_store.py` et `ACA_ORG_ID` dans `tenant.py` : elle peut être injectée par un
    `load_dotenv()` postérieur au premier import de ce module, et un module de sécurité qui gèlerait
    sa clé à « absente » dégraderait silencieusement en SHA-256 simple sans que rien n'échoue.
    """
    key = os.getenv("ACA_AUDIT_HMAC_KEY")
    if key:
        return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chain_hash(prev_hash: str, fields) -> str:
    """
    Empreinte chaînée d'une ligne : `digest(prev_hash ␟ champ1 ␟ champ2 ␟ …)`.

    `fields` est une séquence ORDONNÉE — l'ordre fait partie du contrat : deux appelants qui
    listeraient les mêmes champs dans un ordre différent produiraient des chaînes incomparables.
    Chaque champ est normalisé en chaîne, `None` devenant `""`, pour qu'une colonne nullable
    (fréquent : une adresse IP absente en usage local) ne casse pas le calcul.
    """
    payload = FIELD_SEPARATOR.join([prev_hash or ""] + ["" if f is None else str(f) for f in fields])
    return digest(payload)

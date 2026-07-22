"""
Vérification de signature des requêtes entrantes Slack (interactivité — boutons « Valider »/
« Rejeter » sur l'alerte de notification, cf. `notify.send_approval` et `POST /slack/interactions`
dans `aca/api.py`). Pure/déterministe, **stdlib uniquement** (`hmac`/`hashlib`/`time`) — aucune
dépendance ajoutée, entièrement testable hors ligne.

Slack signe chaque requête d'interactivité : deux en-têtes `X-Slack-Request-Timestamp` et
`X-Slack-Signature` (= `"v0="` + HMAC-SHA256), calculés sur la chaîne `v0:{timestamp}:{corps brut}`
avec le *signing secret* de l'app Slack (`SLACK_SIGNING_SECRET`, DISTINCT du webhook entrant
`SLACK_WEBHOOK_URL` qui, lui, ne sert qu'à POSTER des messages).

⚠️ Sécurité : `/slack/interactions` est le seul endpoint de l'API où un clic déclenche une écriture
CRM sans passer par `require_api_key` (Slack n'envoie pas notre en-tête `X-API-Key`). La signature
est donc la SEULE barrière — sans elle, quiconque atteint le port pourrait forger une « validation ».
Cet endpoint échoue donc FERMÉ (rejet) si `SLACK_SIGNING_SECRET` est absent, à l'inverse du reste de
l'API dont la garde est optionnelle (clé absente = ouvert, mode dev).
"""
import hashlib
import hmac
import time


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
    max_age_seconds: int = 300,
) -> bool:
    """
    True si `signature` correspond au HMAC-SHA256 attendu pour `body` et `timestamp`, calculé avec
    `signing_secret`. Rejette aussi les requêtes trop anciennes (`max_age_seconds`, défaut 5 min) —
    protection anti-rejeu recommandée par Slack. Comparaison en temps constant (`hmac.compare_digest`)
    pour ne pas fuiter d'information par timing. Toute entrée manquante/malformée renvoie False
    (échec fermé), jamais d'exception.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > max_age_seconds:
            return False
    except (TypeError, ValueError):
        return False
    base = f"v0:{timestamp}:{body}".encode()
    digest = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)

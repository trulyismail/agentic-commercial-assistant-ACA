"""
Notification humaine quand une analyse arrive en attente de validation (P0-2 de la §11.4 du
roadmap) : sans signal, l'outil devient une page qu'on oublie d'ouvrir et les leads pourrissent.

Deux canaux possibles, tentés dans l'ordre, chacun optionnel — dégradation gracieuse identique à
`enrichment.py`/`veille.py` (aucune exception si rien n'est configuré) :
1. Slack (`SLACK_WEBHOOK_URL`) — webhook entrant, gratuit, aucune carte bancaire.
2. E-mail à soi-même via l'API Gmail déjà authentifiée (`NOTIFY_EMAIL`) — pas de nouveau service.
Un envoi Gmail réel (pas un brouillon) est acceptable ici : c'est une alerte interne adressée au
commercial lui-même, pas une réponse au prospect — ça ne contourne pas la validation humaine avant
CRM/envoi client, c'est justement ce qui la déclenche plus vite.
"""
import base64
import os
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv

load_dotenv()


def _notify_slack(message: str, webhook_url: str | None = None, blocks: list | None = None) -> bool:
    webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False
    # `text` reste toujours présent même avec des `blocks` : Slack l'utilise pour la notification
    # push/mobile et les lecteurs d'écran (accessibilité), le rendu visuel venant des blocks.
    payload = {"text": message, "blocks": blocks} if blocks else {"text": message}
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"⚠️ Notification Slack échouée : {e}")
        return False


def _notify_email(message: str, to: str | None = None, subject: str | None = None) -> bool:
    to = to or os.getenv("NOTIFY_EMAIL")
    if not to:
        return False
    try:
        from aca.integrations import gmail_reader
        service = gmail_reader.get_gmail_service()
        mime = MIMEText(message)
        mime["To"] = to
        mime["Subject"] = subject or "ACA — nouveau lead en attente de validation"
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True
    except Exception as e:
        print(f"⚠️ Notification e-mail échouée : {e}")
        return False


def send(
    message: str,
    webhook_url: str | None = None,
    email_to: str | None = None,
    subject: str | None = None,
) -> bool:
    """
    Tente Slack puis e-mail. Renvoie True si un canal a réussi, False sinon (silencieux).
    `webhook_url`/`email_to`/`subject` permettent à un appelant de cibler une destination différente
    du canal générique (`SLACK_WEBHOOK_URL`/`NOTIFY_EMAIL`) — ex. router SUPPORT/AUTRE vers une
    boîte support ou RH (cf. `routing_node` dans app.py) sans dupliquer cette logique d'envoi.
    """
    if _notify_slack(message, webhook_url):
        return True
    if _notify_email(message, email_to, subject):
        return True
    return False


def send_all(
    message: str,
    webhook_url: str | None = None,
    email_to: str | None = None,
    subject: str | None = None,
) -> list:
    """
    Envoie sur **tous** les canaux configurés et renvoie la liste de ceux qui ont abouti
    (`["Slack", "e-mail"]`).

    Différent de `send()`, qui s'arrête au premier succès. La distinction n'est pas cosmétique :
    une alerte de routage n'a besoin d'atteindre l'équipe qu'une fois, peu importe par où — la
    chaîne de repli est alors le bon comportement. Un **rappel personnel**, lui, est posé par
    quelqu'un pour ne pas oublier : le recevoir dans Slack *et* dans sa boîte e-mail est ce que la
    personne attend, et la chaîne de repli le lui refusait silencieusement dès que Slack
    fonctionnait.

    Ne lève jamais, comme `send()` : chaque canal échoue pour son propre compte.
    """
    delivered = []
    if _notify_slack(message, webhook_url):
        delivered.append("Slack")
    if _notify_email(message, email_to, subject):
        delivered.append("e-mail")
    return delivered


def _approval_blocks(message: str, thread_id: str) -> list:
    """
    Message Block Kit avec deux boutons « Valider »/« Rejeter » portant le `thread_id` dans leur
    `value` — le clic déclenche un POST signé de Slack vers `/slack/interactions` (cf. aca/api.py),
    qui rejoue le graphe jusqu'à l'écriture CRM (Valider) ou retire le lead de la file (Rejeter),
    exactement comme le bouton « Valider » de l'UI. `action_id` distingue les deux côté serveur.
    """
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "✅ Valider"},
             "style": "primary", "action_id": "aca_approve", "value": thread_id},
            {"type": "button", "text": {"type": "plain_text", "text": "✕ Rejeter"},
             "style": "danger", "action_id": "aca_reject", "value": thread_id},
        ]},
    ]


def send_approval(
    message: str,
    thread_id: str,
    webhook_url: str | None = None,
    email_to: str | None = None,
    subject: str | None = None,
) -> bool:
    """
    Comme `send()`, mais l'alerte Slack porte des boutons cliquables « Valider »/« Rejeter » —
    la validation humaine se fait alors DANS Slack, sans ouvrir aucune UI (le plus grand gain de
    commodité pour une équipe commerciale qui vit déjà dans Slack). Nécessite, côté Slack, une app
    avec l'interactivité activée pointant vers `/slack/interactions` (cf. slack_verify.py) — le
    webhook entrant seul poste le message mais ne reçoit pas les clics. Repli gracieux identique à
    `send()` : e-mail sans boutons si Slack absent, silencieux si rien n'est configuré.
    """
    if _notify_slack(message, webhook_url, blocks=_approval_blocks(message, thread_id)):
        return True
    if _notify_email(message, email_to, subject):
        return True
    return False


if __name__ == "__main__":
    ok = send("Test de notification ACA — si tu vois ceci, le canal fonctionne.")
    print("Notification envoyée." if ok else "Aucun canal configuré (SLACK_WEBHOOK_URL / NOTIFY_EMAIL absents).")

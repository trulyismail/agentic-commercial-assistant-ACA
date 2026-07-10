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


def _notify_slack(message: str, webhook_url: str | None = None) -> bool:
    webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=5)
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
        import gmail_reader
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


if __name__ == "__main__":
    ok = send("Test de notification ACA — si tu vois ceci, le canal fonctionne.")
    print("Notification envoyée." if ok else "Aucun canal configuré (SLACK_WEBHOOK_URL / NOTIFY_EMAIL absents).")

"""
Intégration Gmail : lecture des e-mails non lus et marquage comme traités.

Au tout premier lancement, `get_gmail_service()` ouvre le navigateur pour
l'autorisation OAuth (compte "installed app", via GMAIL_CREDENTIALS_FILE).
Le token obtenu est ensuite mis en cache dans GMAIL_TOKEN_FILE et réutilisé
automatiquement — plus besoin d'interaction navigateur par la suite, sauf
expiration du refresh token.
"""
import base64
import os
import re
from html import unescape
from typing import TypedDict

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
PROCESSED_LABEL_NAME = "ACA-Traite"

CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials/gmail_credentials.json")
TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "credentials/gmail_token.json")


class EmailPayload(TypedDict):
    id: str
    sender: str
    subject: str
    body: str
    attachment_pdf: bytes | None


def get_gmail_service():
    """Authentifie l'accès Gmail et retourne le client API `gmail v1`."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _b64url_decode(data: str) -> bytes:
    """Décode du base64url Gmail, qui arrive sans le padding '=' attendu par le module `base64`."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode_body(payload: dict) -> str:
    """Extrait le texte d'un message Gmail (text/plain, ou à défaut text/html nettoyé), parties imbriquées comprises."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _b64url_decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text

    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = _b64url_decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        return unescape(re.sub(r"<[^>]+>", " ", html)).strip()

    return ""


def _extract_pdf_attachment(service, message_id: str, payload: dict) -> bytes | None:
    """Récupère le premier PDF joint au message (parcours récursif des parties MIME), s'il existe."""
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        attachment_id = part.get("body", {}).get("attachmentId")
        if filename.lower().endswith(".pdf") and attachment_id:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
            return _b64url_decode(attachment["data"])

        nested = _extract_pdf_attachment(service, message_id, part)
        if nested:
            return nested
    return None


def list_unread_emails(service, max_results: int = 10) -> list[dict]:
    """Liste les e-mails non lus de la boîte de réception (métadonnées légères : id/expéditeur/objet)."""
    results = service.users().messages().list(
        userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results
    ).execute()
    messages = results.get("messages", [])

    summaries = []
    for msg in messages:
        detail = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata", metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        summaries.append({
            "id": msg["id"],
            "sender": headers.get("From", ""),
            "subject": headers.get("Subject", "(sans objet)"),
        })
    return summaries


def get_email(service, message_id: str) -> EmailPayload:
    """Récupère le contenu complet d'un e-mail : expéditeur, objet, corps, et éventuelle pièce jointe PDF."""
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = message["payload"]
    headers = {h["name"]: h["value"] for h in payload["headers"]}

    return {
        "id": message_id,
        "sender": headers.get("From", ""),
        "subject": headers.get("Subject", "(sans objet)"),
        "body": _decode_body(payload),
        "attachment_pdf": _extract_pdf_attachment(service, message_id, payload),
    }


def _get_or_create_label(service, label_name: str) -> str:
    """Retourne l'ID du label `label_name`, en le créant s'il n'existe pas encore."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]

    created = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    return created["id"]


def mark_as_processed(service, message_id: str) -> None:
    """Retire le libellé UNREAD et ajoute `ACA-Traite`, pour ne pas retraiter le même e-mail."""
    processed_label_id = _get_or_create_label(service, PROCESSED_LABEL_NAME)
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"], "addLabelIds": [processed_label_id]},
    ).execute()


if __name__ == "__main__":
    # Test manuel : authentifie (ouvre le navigateur si besoin) et liste les e-mails non lus.
    gmail = get_gmail_service()
    unread = list_unread_emails(gmail, max_results=5)
    if not unread:
        print("Aucun e-mail non lu.")
    for summary in unread:
        print(f"- {summary['subject']} ({summary['sender']})")

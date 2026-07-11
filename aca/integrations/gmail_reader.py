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
from email.mime.text import MIMEText
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
    attachments: list[tuple[str, bytes]]  # (nom de fichier, contenu) — PDF/Word/Excel, cf. attachment_reader.py
    gmail_thread_id: str  # vrai threadId Gmail (distinct du thread_id LangGraph) — pour les relances


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


_SUPPORTED_ATTACHMENT_EXTENSIONS = (".pdf", ".docx", ".xlsx")


def _extract_attachments(service, message_id: str, payload: dict) -> list[tuple[str, bytes]]:
    """
    Récupère TOUTES les pièces jointes PDF/Word/Excel du message (parcours récursif des parties
    MIME) — un vrai appel d'offres arrive souvent avec plusieurs documents, pas un seul PDF
    (P2 §11.4 item 16). Chaque résultat = (nom de fichier, contenu binaire décodé).
    """
    found: list[tuple[str, bytes]] = []
    for part in payload.get("parts", []):
        filename = part.get("filename", "")
        attachment_id = part.get("body", {}).get("attachmentId")
        if filename.lower().endswith(_SUPPORTED_ATTACHMENT_EXTENSIONS) and attachment_id:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
            found.append((filename, _b64url_decode(attachment["data"])))

        found.extend(_extract_attachments(service, message_id, part))
    return found


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
        "attachments": _extract_attachments(service, message_id, payload),
        "gmail_thread_id": message["threadId"],
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


def create_draft_reply(service, message_id: str, to: str, subject: str, body: str) -> str:
    """
    Crée un brouillon de réponse dans le MÊME fil que le message d'origine (threadId + en-têtes
    In-Reply-To/References corrects), pour que le commercial n'ait qu'à relire dans Gmail et
    cliquer Envoyer après avoir validé la proposition dans l'UI. Renvoie l'ID du brouillon créé ;
    lève une exception en cas d'échec (gérée par l'appelant, cf. `action_node`).
    """
    original = service.users().messages().get(
        userId="me", id=message_id, format="metadata", metadataHeaders=["Message-Id", "Subject"],
    ).execute()
    headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}
    original_message_id = headers.get("Message-Id", "")
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    message = MIMEText(body)
    message["To"] = to
    message["Subject"] = reply_subject
    if original_message_id:
        message["In-Reply-To"] = original_message_id
        message["References"] = original_message_id

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": original["threadId"]}},
    ).execute()
    return draft["id"]


def create_forward_draft(
    service, message_id: str, to: str,
    original_sender: str, original_subject: str, original_body: str,
) -> str:
    """
    Crée un brouillon de TRANSFERT (jamais auto-envoyé) dans le même fil que le message d'origine,
    prérempli avec l'expéditeur/objet/corps original — pour router SUPPORT/AUTRE vers l'équipe
    compétente (support ou RH, cf. `routing_node` dans app.py) sans jamais envoyer quoi que ce soit
    sans relecture humaine. Même pattern que `create_draft_reply`, mais adressé à un tiers plutôt
    qu'à l'expéditeur d'origine.
    """
    original = service.users().messages().get(
        userId="me", id=message_id, format="metadata", metadataHeaders=["Subject"],
    ).execute()
    fwd_subject = original_subject if original_subject.lower().startswith("fwd:") else f"Fwd: {original_subject}"

    body_text = (
        "---------- Message transféré ---------\n"
        f"De : {original_sender}\n"
        f"Objet : {original_subject}\n\n"
        f"{original_body}"
    )

    message = MIMEText(body_text)
    message["To"] = to
    message["Subject"] = fwd_subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw, "threadId": original["threadId"]}},
    ).execute()
    return draft["id"]


if __name__ == "__main__":
    # Test manuel : authentifie (ouvre le navigateur si besoin) et liste les e-mails non lus.
    gmail = get_gmail_service()
    unread = list_unread_emails(gmail, max_results=5)
    if not unread:
        print("Aucun e-mail non lu.")
    for summary in unread:
        print(f"- {summary['subject']} ({summary['sender']})")

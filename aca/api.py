"""
Microservice FastAPI exposant le graphe LangGraph (§12 item 6 — port n8n « Option A », décision
d'architecture actée dans docs/ACAM_roadmap.md) : garder LangGraph/Python intact comme « cerveau »,
l'exposer via HTTP ; un futur workflow **n8n self-hosted** (gratuit — n8n Cloud est payant, cf.
§11.5) devient l'enveloppe d'infrastructure (trigger Gmail natif remplaçant `poller.py`,
notifications, file d'attente visuelle, reprise après « Valider ») en appelant ces endpoints via un
nœud HTTP Request. L'alternative « tout réécrire en nœuds n8n » est explicitement rejetée (perdrait
`attachment_reader.py`, le RAG hybride, les garde-fous déterministes du superviseur...).

Ce module N'EXÉCUTE PAS n8n lui-même — il expose seulement le graphe existant. Lancement :
`uvicorn aca.api:api --port 8000` (ou tout serveur ASGI).

Human-in-the-loop inchangé, c'est le cœur non négociable du projet : `POST /threads` met le graphe
en pause (clarification dynamique OU validation finale), jamais n'écrit dans le CRM tout seul —
seul `POST /threads/{id}/valider` déclenche `action_node`. Même contrat que `ui.py`/`poller.py`,
juste exposé en HTTP plutôt qu'en Streamlit.

Observabilité (§12 item 9, audité §14) : un `GET /metrics` au format Prometheus standard —
gratuit, open source, pas de compte tiers requis. La roadmap la marque explicitement « utile
uniquement sous vraie charge multi-clients » ; LangSmith (déjà branché, cf. app.py) reste
suffisant au volume prototype. Cet endpoint est donc prêt pour ce cas futur (scrape Prometheus/
Grafana) sans imposer d'infrastructure supplémentaire tant que personne ne le scrape.
"""
import json
import os
import uuid
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from aca.core import app as aca_graph
from aca.core.slack_verify import verify_slack_signature
from aca.core.tenant import current_org_id
from aca.storage import analytics_store, audit_log, config_store, queue_store

api = FastAPI(
    title="ACA — API du graphe LangGraph",
    description="Expose le cerveau ACAM v2 en HTTP pour un futur port n8n (self-hosted, §12 item 6) "
    "et pour le dashboard Next.js (§12 item 8).",
    version="1.1",
)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Garde optionnelle (même contrat que `ACA_UI_PASSWORD` sur `ui.py`) : `ACA_API_KEY` absente =
    pas de garde, mode développement, comportement inchangé pour tout usage existant (n8n local,
    tests). Réglée = toute requête doit porter le header `X-API-Key` correspondant, sans quoi cette
    API — qui expose le SEUL point d'entrée d'écriture CRM (`/valider`) — serait accessible sans
    authentification à qui peut atteindre le port. `/metrics` reste volontairement hors de cette
    garde (format Prometheus standard, pensé pour être scrapé sans en-tête custom).
    """
    required = os.getenv("ACA_API_KEY")
    if required and x_api_key != required:
        raise HTTPException(401, "Clé API manquante ou invalide (en-tête X-API-Key).")


EMAILS_CLASSIFIED = Counter(
    "aca_emails_classified_total", "E-mails classés, par catégorie et par tenant",
    ["classification", "org_id"],
)
LEADS_VALIDATED = Counter(
    "aca_leads_validated_total", "Leads validés (écriture CRM effective), par tenant", ["org_id"],
)
TOKENS_PER_ANALYSIS = Histogram(
    "aca_tokens_per_analysis", "Tokens Groq (entrée+sortie) consommés par analyse via l'API",
)


class EmailIn(BaseModel):
    sender: str
    subject: str
    body: str
    thread_id: Optional[str] = None


class ClarificationIn(BaseModel):
    answer: str


class ValidationIn(BaseModel):
    validated_by: Optional[str] = None
    edited_draft: Optional[str] = None


class SettingsIn(BaseModel):
    """Sous-ensemble de `config_store.SETTINGS_SCHEMA` — une clé absente/vide n'est pas modifiée
    (même comportement que le formulaire « Réglages » de `ui.py` : un champ vide retombe sur la
    valeur `.env` existante plutôt que de l'effacer)."""
    values: dict[str, str]


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _snapshot(thread_id: str) -> dict:
    """
    Sérialise l'état courant du thread + ce qui reste en attente — même distinction que `ui.py`
    (`state.interrupts` non vide = clarification dynamique en attente, sinon pause de validation
    si `snapshot.next` n'est pas vide).
    """
    snapshot = aca_graph.app.get_state(_config(thread_id))
    state = snapshot.values
    pending_clarification = snapshot.interrupts[0].value if snapshot.interrupts else None
    return {
        "thread_id": thread_id,
        "classification": state.get("classification"),
        "classification_confidence": state.get("classification_confidence"),
        "extracted_info": state.get("extracted_info"),
        "company_profile": state.get("company_profile"),
        "risk_flags": state.get("risk_flags"),
        "knowledge_gap": state.get("knowledge_gap"),
        "draft_response": state.get("draft_response"),
        "reasoning_log": state.get("reasoning_log"),
        "completed_agents": state.get("completed_agents"),
        "action_status": state.get("action_status"),
        "sender": state.get("email_raw", {}).get("sender"),
        "subject": state.get("email_raw", {}).get("subject"),
        "pending_clarification": pending_clarification,
        "awaiting_validation": not pending_clarification and bool(snapshot.next),
        "done": not snapshot.next,
    }


def _invoke_with_metrics(graph_input, config: dict) -> None:
    """
    Enveloppe `app.invoke()` avec le même callback de comptage de tokens que `ui.py`/`poller.py`
    (`UsageMetadataCallbackHandler` + `sum_usage`), observé dans l'histogramme Prometheus plutôt
    que journalisé en base — ce endpoint expose une vue temps réel, `analytics_store.token_stats`
    reste la source de vérité historique par tenant.
    """
    usage_handler = UsageMetadataCallbackHandler()
    config_with_callback = {**config, "callbacks": [usage_handler]}
    aca_graph.app.invoke(graph_input, config=config_with_callback)
    tokens_in, tokens_out = aca_graph.sum_usage(usage_handler.usage_metadata)
    if tokens_in or tokens_out:
        TOKENS_PER_ANALYSIS.observe(tokens_in + tokens_out)


@api.post("/threads", dependencies=[Depends(require_api_key)])
def create_thread(payload: EmailIn) -> dict:
    """
    Démarre une nouvelle analyse — équivalent HTTP du formulaire manuel/import Gmail de `ui.py`.
    Pas de pièces jointes dans cette première version de l'API (`attachments_raw` vide) : un futur
    nœud n8n Gmail Trigger les transmettrait en base64, décodées avant cet appel.
    """
    thread_id = payload.thread_id or str(uuid.uuid4())
    _invoke_with_metrics(
        {
            "email_raw": {"sender": payload.sender, "subject": payload.subject, "body": payload.body},
            "attachments_raw": [],
        },
        config=_config(thread_id),
    )
    snapshot = _snapshot(thread_id)
    if snapshot["classification"]:
        EMAILS_CLASSIFIED.labels(classification=snapshot["classification"], org_id=current_org_id()).inc()
    return snapshot


@api.get("/threads/pending", dependencies=[Depends(require_api_key)])
def list_pending_threads() -> list:
    """
    Analyses en attente de validation humaine du tenant courant (`queue_store`, alimenté par
    `poller.py`). Déclaré AVANT `/threads/{thread_id}` ci-dessous : FastAPI résout les routes dans
    l'ordre d'enregistrement pour deux chemins de même forme (`/threads/pending` vs.
    `/threads/{thread_id}`) — sans cet ordre, `GET /threads/pending` serait intercepté par la route
    dynamique avec `thread_id="pending"`.
    """
    return queue_store.list_pending()


@api.get("/threads/history", dependencies=[Depends(require_api_key)])
def list_thread_history(limit: int = 100) -> list:
    """Derniers événements de validation du tenant courant (`audit_log.list_recent`). Même remarque
    d'ordre de déclaration que `list_pending_threads` ci-dessus."""
    return audit_log.list_recent(limit=limit)


@api.get("/threads/{thread_id}", dependencies=[Depends(require_api_key)])
def get_thread(thread_id: str) -> dict:
    return _snapshot(thread_id)


@api.post("/threads/{thread_id}/clarifier", dependencies=[Depends(require_api_key)])
def clarify_thread(thread_id: str, payload: ClarificationIn) -> dict:
    """Répond à la question de clarification en attente (`interrupt()` dynamique)."""
    snapshot = aca_graph.app.get_state(_config(thread_id))
    if not snapshot.interrupts:
        raise HTTPException(400, "Aucune clarification en attente pour ce thread.")
    _invoke_with_metrics(Command(resume=payload.answer), config=_config(thread_id))
    return _snapshot(thread_id)


def _do_validate(thread_id: str, validated_by: Optional[str] = None,
                 edited_draft: Optional[str] = None) -> dict:
    """
    Logique partagée de validation — le SEUL chemin qui déclenche l'écriture CRM (`action_node`).
    Appelé par `POST /threads/{id}/valider` (dashboard/n8n) ET par `POST /slack/interactions`
    (bouton « Valider » dans Slack). Résout la pause `interrupt_before=["action"]`, édite le
    brouillon au préalable si fourni, puis rejoue le graphe et effectue la comptabilité post-
    validation (file d'attente, journal d'audit, analytics) — une seule source de vérité pour ces
    trois surfaces.
    """
    config = _config(thread_id)
    snapshot = aca_graph.app.get_state(config)
    if snapshot.interrupts or not snapshot.next:
        raise HTTPException(400, "Ce thread n'est pas en attente de validation.")
    if edited_draft:
        aca_graph.app.update_state(config, {"draft_response": edited_draft})
    aca_graph.app.invoke(None, config=config)
    LEADS_VALIDATED.labels(org_id=current_org_id()).inc()
    state_snapshot = _snapshot(thread_id)
    queue_store.mark_validated(thread_id)
    audit_log.log_validation(
        thread_id, validated_by, state_snapshot["classification"], state_snapshot["sender"],
    )
    analytics_store.record_validation(thread_id)
    return state_snapshot


def _do_reject(thread_id: str) -> dict:
    """
    Logique partagée de rejet (sans écriture CRM) — appelée par `POST /threads/{id}/rejeter` et par
    le bouton « Rejeter » de Slack. `action_node` n'est jamais invoqué ; le thread est simplement
    retiré de la file (`queue_store`) pour ne pas réapparaître indéfiniment.
    """
    config = _config(thread_id)
    snapshot = aca_graph.app.get_state(config)
    if snapshot.interrupts or not snapshot.next:
        raise HTTPException(400, "Ce thread n'est pas en attente de validation.")
    queue_store.mark_rejected(thread_id)
    return {**_snapshot(thread_id), "rejected": True}


@api.post("/threads/{thread_id}/valider", dependencies=[Depends(require_api_key)])
def validate_thread(thread_id: str, payload: ValidationIn) -> dict:
    """
    Résout la pause de validation humaine (`interrupt_before=["action"]`) — SEUL point d'entrée de
    cette API qui déclenche l'écriture CRM (`action_node`). Édite le brouillon avant reprise si
    `edited_draft` est fourni, même logique que le bouton « Valider » de `ui.py`.
    """
    return _do_validate(thread_id, payload.validated_by, payload.edited_draft)


@api.post("/threads/{thread_id}/rejeter", dependencies=[Depends(require_api_key)])
def reject_thread(thread_id: str) -> dict:
    """
    Rejette une analyse en attente sans jamais écrire au CRM — `action_node` n'est jamais invoqué.
    Contrepartie de `/valider` pour le dashboard (§12 item 8) : ce chemin n'existait pas avant (ni
    dans `ui.py`, ni dans le graphe), un « rejet » y équivalait jusqu'ici à simplement ne pas
    cliquer « Valider ».
    """
    return _do_reject(thread_id)


@api.post("/slack/interactions")
async def slack_interactions(request: Request) -> dict:
    """
    Reçoit les clics sur les boutons « Valider »/« Rejeter » d'une alerte Slack (cf.
    `notify.send_approval`) — la validation humaine se fait alors DANS Slack, sans ouvrir aucune UI.

    Sécurité (cf. slack_verify.py) : cet endpoint n'est PAS derrière `require_api_key` (Slack
    n'envoie pas notre en-tête), il est protégé par la signature HMAC de Slack. Il échoue FERMÉ si
    `SLACK_SIGNING_SECRET` est absent — sans quoi n'importe qui pourrait forger une validation et
    déclencher une écriture CRM.

    Slack attend une réponse < 3 s ; `action_node` (Sheets + HubSpot) tient généralement dans ce
    budget au volume prototype. Une version production « à charge » accuserait réception
    immédiatement puis mettrait à jour le message via `response_url` en tâche de fond — noté comme
    limite connue, non nécessaire ici.
    """
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(503, "Interactions Slack non configurées (SLACK_SIGNING_SECRET absent).")
    raw = await request.body()
    body = raw.decode("utf-8")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        raise HTTPException(401, "Signature Slack invalide.")

    payload = json.loads(parse_qs(body).get("payload", ["{}"])[0])
    actions = payload.get("actions", [])
    if not actions:
        return {"text": "Aucune action reçue."}
    action_id = actions[0].get("action_id")
    thread_id = actions[0].get("value")
    user = payload.get("user", {}).get("username") or payload.get("user", {}).get("name") or "Slack"

    try:
        if action_id == "aca_approve":
            _do_validate(thread_id, validated_by=user)
            return {"replace_original": True,
                    "text": f":white_check_mark: Lead validé par {user} — écrit au CRM."}
        if action_id == "aca_reject":
            _do_reject(thread_id)
            return {"replace_original": True,
                    "text": f":x: Lead rejeté par {user} — non écrit au CRM."}
    except HTTPException as e:
        # Ne pas remplacer le message (garder les boutons) : l'action a échoué (ex. thread déjà
        # validé ailleurs), l'humain voit pourquoi et peut réessayer.
        return {"replace_original": False, "text": f":warning: {e.detail}"}
    return {"text": "Action inconnue."}


@api.get("/stats", dependencies=[Depends(require_api_key)])
def get_stats(days: int = 30) -> dict:
    """
    Regroupe les agrégats déjà exposés séparément dans l'onglet « Tableau de bord » de `ui.py`
    (`analytics_store.py`) en une seule réponse, pour le dashboard Next.js (§12 item 8).
    """
    return {
        "volume_by_category": analytics_store.volume_by_category(days=days),
        "daily_volume": analytics_store.daily_volume(days=days),
        "response_times": analytics_store.response_times(days=days),
        "funnel_counts": analytics_store.funnel_counts(days=days),
        "edit_rate": analytics_store.edit_rate(days=days),
        "token_stats": analytics_store.token_stats(days=days),
    }


@api.get("/settings", dependencies=[Depends(require_api_key)])
def get_settings() -> dict:
    """
    Réglages édités du tenant courant + le schéma (clé -> libellé humain) que `ui.py`'s onglet
    « Réglages » utilise déjà — le dashboard peut ainsi construire le même formulaire sans dupliquer
    `config_store.SETTINGS_SCHEMA` côté frontend.
    """
    return {"schema": config_store.SETTINGS_SCHEMA, "values": config_store.get_all_settings()}


@api.post("/settings", dependencies=[Depends(require_api_key)])
def update_settings(payload: SettingsIn) -> dict:
    """Enregistre chaque réglage non vide — même contrat que le formulaire « Réglages » de `ui.py`."""
    for key, value in payload.values.items():
        if value and value.strip():
            config_store.set_setting(key, value.strip())
    return {"schema": config_store.SETTINGS_SCHEMA, "values": config_store.get_all_settings()}


@api.get("/metrics")
def metrics() -> Response:
    """Exposition Prometheus standard (§12 item 9) — `scrape_config` pointe simplement ici."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

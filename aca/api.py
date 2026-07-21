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
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from aca.core import app as aca_graph
from aca.core.tenant import current_org_id

api = FastAPI(
    title="ACA — API du graphe LangGraph",
    description="Expose le cerveau ACAM v2 en HTTP pour un futur port n8n (self-hosted, §12 item 6).",
    version="1.0",
)

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
        "action_status": state.get("action_status"),
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


@api.post("/threads")
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


@api.get("/threads/{thread_id}")
def get_thread(thread_id: str) -> dict:
    return _snapshot(thread_id)


@api.post("/threads/{thread_id}/clarifier")
def clarify_thread(thread_id: str, payload: ClarificationIn) -> dict:
    """Répond à la question de clarification en attente (`interrupt()` dynamique)."""
    snapshot = aca_graph.app.get_state(_config(thread_id))
    if not snapshot.interrupts:
        raise HTTPException(400, "Aucune clarification en attente pour ce thread.")
    _invoke_with_metrics(Command(resume=payload.answer), config=_config(thread_id))
    return _snapshot(thread_id)


@api.post("/threads/{thread_id}/valider")
def validate_thread(thread_id: str, payload: ValidationIn) -> dict:
    """
    Résout la pause de validation humaine (`interrupt_before=["action"]`) — SEUL point d'entrée de
    cette API qui déclenche l'écriture CRM (`action_node`). Édite le brouillon avant reprise si
    `edited_draft` est fourni, même logique que le bouton « Valider » de `ui.py`.
    """
    config = _config(thread_id)
    snapshot = aca_graph.app.get_state(config)
    if snapshot.interrupts or not snapshot.next:
        raise HTTPException(400, "Ce thread n'est pas en attente de validation.")
    if payload.edited_draft:
        aca_graph.app.update_state(config, {"draft_response": payload.edited_draft})
    aca_graph.app.invoke(None, config=config)
    LEADS_VALIDATED.labels(org_id=current_org_id()).inc()
    return _snapshot(thread_id)


@api.get("/metrics")
def metrics() -> Response:
    """Exposition Prometheus standard (§12 item 9) — `scrape_config` pointe simplement ici."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

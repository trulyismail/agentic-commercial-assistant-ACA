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
import base64
import binascii
import hmac
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Optional
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.types import Command
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field, field_validator

from aca.core import app as aca_graph, prod_check
from aca.core.slack_verify import verify_slack_signature
from aca.core.tenant import current_org_id
from aca.integrations import webhook
from aca.storage import activity_log, analytics_store, audit_log, config_store, queue_store

# §15.1.5 : en `ACA_ENV=production`, refuse de démarrer si une protection requise manque (clé API,
# limite de débit, /metrics ouvert…). En développement — le défaut — no-op total.
prod_check.enforce()

# §15.3.3 : Swagger (`/docs`, `/redoc`) et `/openapi.json` publient la surface complète de l'API,
# routes d'écriture CRM comprises, et sont servis par défaut par FastAPI. Ils restent activés en
# développement (c'est là qu'ils servent) mais sont coupés dès que `ACA_ENV=production`, sauf
# `ACA_ENABLE_DOCS=1` explicite — l'inverse du défaut FastAPI, qui expose sans rien demander.
_DOCS_ENABLED = os.getenv("ACA_ENABLE_DOCS") == "1" or not prod_check.is_production()

api = FastAPI(
    title="ACA — API du graphe LangGraph",
    description="Expose le cerveau ACAM v2 en HTTP pour un futur port n8n (self-hosted, §12 item 6) "
    "et pour le dashboard Next.js (§12 item 8).",
    version="1.1",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Garde par clé partagée sur toutes les routes sauf `/metrics` (garde propre, cf. `metrics()`) et
    `/slack/interactions` (signature HMAC de Slack).

    Contrat historique conservé en développement (même dégradation gracieuse que `ACA_UI_PASSWORD`
    sur `ui.py`) : `ACA_API_KEY` absente = pas de garde, comportement inchangé pour n8n local et la
    suite de tests. **§15.1.5** : en `ACA_ENV=production`, cette garde n'est plus optionnelle —
    l'absence de clé y est un défaut de configuration, pas une autorisation. Le cas est déjà refusé
    au démarrage par `prod_check.enforce()` ; le contrôle est répété ici pour que la protection ne
    dépende pas d'un seul point (une variable vidée à chaud ne doit pas rouvrir l'API).

    La comparaison est à temps constant : un `!=` sur un secret fuit la longueur de son préfixe
    correct par chronométrage (même raisonnement que `ui.py._check_auth()`).
    """
    required = os.getenv("ACA_API_KEY")
    if not required:
        if prod_check.is_production():
            raise HTTPException(503, "API non configurée : ACA_API_KEY est requise en production.")
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, required):
        raise HTTPException(401, "Clé API manquante ou invalide (en-tête X-API-Key).")


# ── Limitation de débit (rate limiting) ───────────────────────────────────────────────────────
# `require_api_key` empêche l'accès NON authentifié, mais pas l'abus par un client authentifié (ou
# non, si la garde est en mode dev) : rafales de requêtes, brute-force du gate, déni de service. On
# ajoute une fenêtre glissante en mémoire par client (clé API si présente, sinon IP source). Même
# contrat gracieux que le reste du projet : `ACA_RATE_LIMIT` absente ou ≤ 0 = désactivé (défaut —
# usage local/n8n et suite de tests inchangés) ; réglée à N = au plus N requêtes par
# `ACA_RATE_WINDOW_SECONDS` (défaut 60 s), au-delà → HTTP 429 + en-tête `Retry-After`. Les deux
# variables sont lues DYNAMIQUEMENT à chaque requête (jamais gelées à l'import — même leçon que
# `DATABASE_URL`/`ACA_ORG_ID` ailleurs dans le projet), donc testables via monkeypatch/setenv.
# In-memory volontairement : à l'échelle prototype/mono-process c'est exact et sans dépendance ;
# une bascule multi-process (plusieurs workers uvicorn) demanderait un backend partagé (Redis) —
# noté comme dette de phase commerciale, pas un besoin actuel.
_rate_buckets: "defaultdict[str, deque]" = defaultdict(deque)
_rate_lock = threading.Lock()


def _rate_identity(request: Request) -> str:
    """Identité du client pour le compteur : clé API si fournie, sinon IP source."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"key:{api_key}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


@api.middleware("http")
async def _rate_limit_middleware(request: Request, call_next):
    limit = int(os.getenv("ACA_RATE_LIMIT", "0") or "0")
    # `/metrics` est exempté (scrape Prometheus fréquent et légitime, jamais un vecteur d'abus CRM).
    if limit > 0 and request.url.path != "/metrics":
        window = int(os.getenv("ACA_RATE_WINDOW_SECONDS", "60") or "60")
        now = time.monotonic()
        key = _rate_identity(request)
        with _rate_lock:
            bucket = _rate_buckets[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(window - (now - bucket[0])) + 1
                return JSONResponse(
                    {"detail": "Trop de requêtes — réessayez plus tard."},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
    return await call_next(request)


_logger = logging.getLogger("aca.api")


@api.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Réponse générique sur toute exception non gérée (§15.3.2).

    Une exception qui remonte jusqu'ici porte souvent, dans son texte, l'URL appelée, un en-tête ou
    un fragment de clé (erreurs Groq/Sheets/HubSpot notamment). Le client ne reçoit donc qu'un
    identifiant d'incident ; la trace complète part dans les journaux serveur, où l'identifiant
    permet de la retrouver. Sans ce handler, le comportement dépendait de la configuration du
    serveur ASGI — acceptable en local, jamais garanti en production.
    """
    incident = uuid.uuid4().hex[:12]
    _logger.exception(
        "Erreur non gérée [%s] sur %s %s", incident, request.method, request.url.path,
    )
    return JSONResponse(
        {"detail": "Erreur interne. Communiquez l'identifiant d'incident au support.",
         "incident": incident},
        status_code=500,
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


# ── Validation stricte des entrées (§15.1.4) ──────────────────────────────────────────────────
# Les modèles ci-dessous ne déclaraient que des types (`str`), ce que l'audit §15.1.4 relevait :
# Pydantic garantissait « c'est bien une chaîne », pas « c'est une chaîne plausible ». Un corps
# d'e-mail de 50 Mo était donc accepté et parti droit vers le LLM (coût, latence, saturation
# mémoire), et une chaîne vide déclenchait une analyse sur du vide. Les bornes ci-dessous sont
# larges — elles écartent l'absurde, pas le légitime : `MAX_BODY` dépasse déjà de loin le plafond
# de `pdf_reader.MAX_CHARS` (15 000) qui borne le texte réellement envoyé au modèle.
MAX_SENDER = 320        # longueur maximale d'une adresse e-mail (RFC 5321 : 64 + @ + 255)
MAX_SUBJECT = 998       # RFC 5322, longueur maximale d'une ligne d'en-tête
MAX_BODY = 200_000
MAX_DRAFT = 100_000
MAX_ANSWER = 10_000
MAX_NAME = 200
# Un `thread_id` est fabriqué par nous (`uuid4()`) ou repris d'un appelant : le restreindre à des
# caractères sûrs empêche qu'il serve de véhicule à des séparateurs ou à des caractères de contrôle
# vers les couches en aval (clés de checkpoint, libellés Slack, lignes de journal).
THREAD_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"

# ── Pièces jointes (§16.1.1) ──────────────────────────────────────────────────────────────────
# Bornes larges mais fermes, dans le même esprit que celles ci-dessus : elles écartent l'absurde,
# pas le légitime. Un vrai appel d'offres arrive avec 3 à 5 documents, pas 200 ; et le texte
# réellement transmis au LLM est de toute façon plafonné à `pdf_reader.MAX_CHARS` (15 000) par
# `attachment_reader`. Refuser ici, c'est refuser AVANT de décoder 200 Mo en mémoire.
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024   # 20 Mo décodés, tous fichiers confondus
MAX_FILENAME = 255


class AttachmentIn(BaseModel):
    """
    Une pièce jointe transmise en base64 — la forme que produit nativement un nœud n8n
    (Gmail Trigger → données binaires) comme n'importe quel client HTTP.
    """
    filename: str = Field(min_length=1, max_length=MAX_FILENAME)
    content_b64: str = Field(min_length=1)


class EmailIn(BaseModel):
    sender: str = Field(min_length=1, max_length=MAX_SENDER)
    subject: str = Field(max_length=MAX_SUBJECT)
    body: str = Field(max_length=MAX_BODY)
    thread_id: Optional[str] = Field(default=None, pattern=THREAD_ID_PATTERN)
    # §16.1.1 : jusqu'ici `POST /threads` codait en dur `attachments_raw: []`, ce qui rendait
    # l'analyse multimodale — le pilier d'innovation n°1 du projet — **inatteignable depuis
    # l'API**, donc depuis n8n. Le graphe, lui, savait déjà les traiter (`ingestion_node` →
    # `attachment_reader.extract_text_from_attachments`) : il ne manquait que ce champ.
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)

    @field_validator("attachments")
    @classmethod
    def _decodable_and_bounded(cls, value: list) -> list:
        """
        Refuse en 422 un base64 invalide ou un volume total déraisonnable, **avant** que quoi que
        ce soit n'atteigne le graphe ou le LLM (même principe que les bornes de `body`, §15.1.4).
        """
        total = 0
        for attachment in value:
            try:
                raw = base64.b64decode(attachment.content_b64, validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError(f"Pièce jointe « {attachment.filename} » : base64 invalide.") from e
            total += len(raw)
            if total > MAX_ATTACHMENT_BYTES:
                raise ValueError(
                    f"Pièces jointes trop volumineuses (> {MAX_ATTACHMENT_BYTES // (1024 * 1024)} Mo "
                    "au total une fois décodées)."
                )
        return value


def _decoded_attachments(payload: EmailIn) -> list:
    """
    `[(nom_de_fichier, octets), ...]` — exactement la forme attendue par `attachments_raw` dans
    l'état du graphe, donc par `ingestion_node`. La validité du base64 est déjà garantie par le
    validateur ci-dessus, ce décodage-ci ne peut donc plus échouer.
    """
    return [
        (attachment.filename, base64.b64decode(attachment.content_b64, validate=True))
        for attachment in payload.attachments
    ]


class ClarificationIn(BaseModel):
    answer: str = Field(min_length=1, max_length=MAX_ANSWER)


class ValidationIn(BaseModel):
    validated_by: Optional[str] = Field(default=None, max_length=MAX_NAME)
    edited_draft: Optional[str] = Field(default=None, max_length=MAX_DRAFT)


class SettingsIn(BaseModel):
    """Sous-ensemble de `config_store.SETTINGS_SCHEMA` — une clé absente/vide n'est pas modifiée
    (même comportement que le formulaire « Réglages » de `ui.py` : un champ vide retombe sur la
    valeur `.env` existante plutôt que de l'effacer). §15.1.4 : seules les clés du schéma connu
    sont acceptées — sans cette liste blanche, `POST /settings` était un magasin clé/valeur
    arbitraire alimentable par l'appelant."""
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
    pending_clarification = snapshot.interrupts[0].value if snapshot.interrupts else None
    # §16.1.2 : les champs communs viennent de `aca_graph.snapshot_from_state()`, partagé avec les
    # webhooks sortants — un client REST et un abonné webhook voient ainsi exactement le même lead.
    # (Y compris `injection_flags`, §15.1.4 : sans lui, la personne qui valide depuis n8n, Slack ou
    # le dashboard ignorerait que l'e-mail entrant tente de piloter le modèle.) Seuls les trois
    # champs ci-dessous sont propres à cette API : ils décrivent la PAUSE, information que seul le
    # `StateSnapshot` de LangGraph détient et qui n'existe pas dans l'état du graphe lui-même.
    return {
        **aca_graph.snapshot_from_state(snapshot.values, thread_id),
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


def _thread_exists(thread_id: str) -> bool:
    """Un état de graphe existe-t-il déjà pour ce `thread_id` ? (idempotence, cf. `create_thread`)"""
    return bool(aca_graph.app.get_state(_config(thread_id)).created_at)


def _emit_if_clarification(snapshot: dict) -> None:
    """
    §16.1.2 — émet `analysis.clarification` quand le graphe s'arrête sur une question à l'humain.

    Émis ici et non dans `clarification_node`, contrairement à `analysis.paused` (émis, lui, depuis
    `notification_node`) : `interrupt()` fait **rejouer le nœud depuis son début** à la reprise, si
    bien qu'un envoi placé à l'intérieur du nœud partirait deux fois pour une seule question. Ce
    point-ci est traversé exactement une fois par exécution du graphe.

    Sans cet événement, un workflow n8n lancé en `?mode=async` restait muet sur un e-mail ambigu :
    il avait reçu son 202 et n'attendait plus qu'`analysis.paused`, lequel n'arrive jamais tant que
    la clarification est sans réponse — la seule branche où le graphe s'arrête sans rien émettre.
    """
    if snapshot.get("pending_clarification"):
        webhook.emit(webhook.EVENT_CLARIFICATION, snapshot)


def _run_analysis(payload: EmailIn, thread_id: str) -> None:
    """Exécution du graphe jusqu'à la pause — partagée par le mode synchrone et le mode asynchrone."""
    _invoke_with_metrics(
        {
            "email_raw": {"sender": payload.sender, "subject": payload.subject, "body": payload.body},
            "attachments_raw": _decoded_attachments(payload),
        },
        config=_config(thread_id),
    )
    snapshot = _snapshot(thread_id)
    if snapshot["classification"]:
        EMAILS_CLASSIFIED.labels(
            classification=snapshot["classification"], org_id=current_org_id(),
        ).inc()
    _emit_if_clarification(snapshot)


@api.post("/threads", dependencies=[Depends(require_api_key)])
def create_thread(payload: EmailIn, background: BackgroundTasks, mode: str = "sync") -> dict:
    """
    Démarre une nouvelle analyse — équivalent HTTP du formulaire manuel/import Gmail de `ui.py`.

    §16.1.1 : les pièces jointes (PDF/Word/Excel, transmises en base64) sont désormais acceptées et
    transmises brutes au graphe, où `ingestion_node` en extrait le texte. Auparavant ce champ était
    codé en dur à `[]`, ce qui privait tout client de l'API — donc n8n — de l'analyse multimodale,
    pourtant le pilier d'innovation n°1 du projet.

    §16.1.4 — **idempotence** : si un `thread_id` fourni possède déjà un état, l'analyse n'est PAS
    rejouée et l'instantané existant est renvoyé avec `already_exists: true`. Le nœud HTTP de n8n
    réessaie par défaut en cas d'échec réseau ; sans cette garde, un simple réessai relançait une
    analyse complète (deux appels 70B, du quota Tavily/Gemini) **et renotifiait** l'équipe pour le
    même e-mail. C'est le pendant, côté API, de l'idempotence que `poller.py` obtient déjà en
    marquant « en_cours » avant `invoke()`.

    §16.1.4 — **mode asynchrone** (`?mode=async`) : renvoie `202` immédiatement et exécute le
    graphe en tâche de fond ; la fin est signalée par le webhook `analysis.paused` (§16.1.2). Le
    mode synchrone reste le défaut (aucun client existant n'est affecté), mais il retient la requête
    30 à 90 s — deux appels 70B, Tavily, embeddings, réflexion — ce qui devient fragile dès que les
    réessais de n8n se combinent au backoff 429 du palier gratuit de Groq.
    """
    thread_id = payload.thread_id or str(uuid.uuid4())

    if payload.thread_id and _thread_exists(thread_id):
        return {**_snapshot(thread_id), "already_exists": True}

    if mode == "async":
        background.add_task(_run_analysis, payload, thread_id)
        return JSONResponse(
            {"thread_id": thread_id, "status": "running", "already_exists": False},
            status_code=202,
        )

    _run_analysis(payload, thread_id)
    return {**_snapshot(thread_id), "already_exists": False}


@api.get("/health")
def health() -> dict:
    """
    Sonde de disponibilité (§16.1.3) — healthcheck Docker, branche d'erreur n8n, supervision.

    **N'appelle aucun service externe** : elle rapporte ce qui est *configuré*, pas ce qui répond.
    Une sonde qui interrogerait Groq, Sheets et Supabase serait lente, consommerait du quota à
    chaque passage (toutes les 10 s pour Docker) et tomberait en panne pour cause de panne d'un
    tiers optionnel — alors que tout le projet est bâti sur « service absent = fonctionnalité
    ignorée ».

    Volontairement hors de `require_api_key` (un orchestrateur doit pouvoir sonder sans détenir la
    clé qui écrit dans le CRM) et volontairement **strictement booléenne** : jamais une valeur de
    secret, seulement « configuré ou non ».
    """
    return {
        "status": "ok",
        "version": api.version,
        "org_id": current_org_id(),
        "environment": "production" if prod_check.is_production() else "development",
        "checkpointer": "postgres" if os.getenv("DATABASE_URL") else "sqlite",
        "integrations": {
            "groq": bool(os.getenv("GROQ_API_KEY")),
            "gemini": bool(os.getenv("GOOGLE_API_KEY")),
            "sheets": bool(os.getenv("GOOGLE_SHEETS_ID")),
            "tavily": bool(os.getenv("TAVILY_API_KEY")),
            "hubspot": bool(os.getenv("HUBSPOT_ACCESS_TOKEN")),
            "slack": bool(os.getenv("SLACK_WEBHOOK_URL")),
            "slack_interactivity": bool(os.getenv("SLACK_SIGNING_SECRET")),
            "outbound_webhook": webhook.is_enabled(),
            "stripe": bool(os.getenv("STRIPE_API_KEY")),
        },
    }


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
                 edited_draft: Optional[str] = None,
                 source: str = activity_log.SOURCE_API) -> dict:
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
    # §17 — même validation dans le journal d'activité, avec `source` distinguant l'API du clic
    # Slack. Sans ça, une validation déclenchée depuis Slack ou n8n restait invisible du journal
    # que l'administrateur consulte dans Streamlit, alors qu'elle écrit tout autant dans le CRM.
    # Aucun contexte de poste ici : un client HTTP n'a ni navigateur ni adresse à consigner
    # honnêtement (celle du proxy n'apprendrait rien), et inventer une empreinte machine dans un
    # journal d'audit serait pire que de laisser la colonne vide.
    activity_log.log(
        activity_log.ACTION_LEAD_VALIDATED, actor=validated_by, source=source,
        target_type="thread", target_id=thread_id,
        details={"classification": state_snapshot["classification"],
                 "expéditeur": state_snapshot["sender"],
                 "brouillon_modifié": bool(edited_draft)},
    )
    # §16.1.2 : émis APRÈS l'écriture CRM et la comptabilité — un abonné n8n qui déclenche une
    # suite (facture, tâche, message d'équipe) doit pouvoir considérer le lead comme réellement
    # enregistré. `validated_by` est joint : c'est l'information qu'un workflow d'entreprise veut.
    webhook.emit(
        webhook.EVENT_VALIDATED, {**state_snapshot, "validated_by": validated_by},
    )
    return state_snapshot


def _do_reject(thread_id: str, rejected_by: Optional[str] = None,
               source: str = activity_log.SOURCE_API) -> dict:
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
    rejected = {**_snapshot(thread_id), "rejected": True}
    # §17 — le rejet n'était consigné nulle part : `audit_log` ne connaît que les validations, donc
    # un lead écarté depuis Slack était indistinguable d'un lead jamais traité.
    activity_log.log(
        activity_log.ACTION_LEAD_REJECTED, actor=rejected_by, source=source,
        target_type="thread", target_id=thread_id,
        details={"classification": rejected["classification"], "expéditeur": rejected["sender"]},
    )
    webhook.emit(webhook.EVENT_REJECTED, rejected)
    return rejected


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
            _do_validate(thread_id, validated_by=user, source=activity_log.SOURCE_SLACK)
            return {"replace_original": True,
                    "text": f":white_check_mark: Lead validé par {user} — écrit au CRM."}
        if action_id == "aca_reject":
            # `user` vient du champ `user` de la charge utile Slack, dont la signature HMAC a déjà
            # été vérifiée ci-dessus : l'attribution est donc au moins aussi fiable que le secret
            # de signature, ce qui est le maximum atteignable pour un clic hors de notre UI.
            _do_reject(thread_id, rejected_by=user, source=activity_log.SOURCE_SLACK)
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
    """
    Enregistre chaque réglage non vide — même contrat que le formulaire « Réglages » de `ui.py`.

    §15.1.4 : les clés inconnues sont refusées (422) au lieu d'être écrites. `config_store`
    accepte volontairement n'importe quelle clé texte (c'est un magasin générique), donc la liste
    blanche appartient à cette frontière-ci, la seule exposée au réseau.
    """
    unknown = sorted(set(payload.values) - set(config_store.SETTINGS_SCHEMA))
    if unknown:
        raise HTTPException(422, f"Réglage(s) inconnu(s) : {', '.join(unknown)}.")
    before = config_store.get_all_settings()
    changed = {}
    for key, value in payload.values.items():
        if value and value.strip():
            config_store.set_setting(key, value.strip())
            changed[key] = {"avant": before.get(key) or "(non réglé)", "après": value.strip()}
    if changed:
        # §17 — cette route peut rediriger les alertes commerciales vers une autre adresse ; elle
        # doit laisser la même trace que le formulaire équivalent de `ui.py`, sinon il suffit de
        # passer par l'API pour modifier la configuration sans apparaître au journal.
        activity_log.log(
            activity_log.ACTION_SETTINGS_CHANGED, source=activity_log.SOURCE_API,
            target_type="réglages", target_id=", ".join(sorted(changed)), details=changed,
        )
    return {"schema": config_store.SETTINGS_SCHEMA, "values": config_store.get_all_settings()}


@api.get("/metrics")
def metrics(x_metrics_token: Optional[str] = Header(default=None)) -> Response:
    """
    Exposition Prometheus standard (§12 item 9) — `scrape_config` pointe simplement ici.

    §15.3.3 : `/metrics` est resté délibérément hors de `require_api_key` (un scrapeur Prometheus
    n'envoie pas d'en-tête applicatif), mais « hors de la garde » ne doit pas vouloir dire
    « public » : ces compteurs révèlent la volumétrie, le nombre de leads validés et la liste des
    tenants. `ACA_METRICS_TOKEN` ajoute donc une garde dédiée, à renseigner côté Prometheus via
    `authorization`-like `headers: {X-Metrics-Token: …}` dans le `scrape_config`. Absent = ouvert,
    comme avant (mode développement) — mais `prod_check` le signale et le refuse en production.
    """
    required = os.getenv("ACA_METRICS_TOKEN")
    if required and (not x_metrics_token or not hmac.compare_digest(x_metrics_token, required)):
        raise HTTPException(401, "Jeton de métriques manquant ou invalide (en-tête X-Metrics-Token).")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

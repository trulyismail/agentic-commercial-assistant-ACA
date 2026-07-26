"""
Webhook sortant (§16.1.2 de docs/ACAM_roadmap.md) — ce qui rend le port n8n idiomatique.

Le problème qu'il résout : l'API n'exposait que des routes à interroger (`GET /threads/pending`).
Un workflow n8n aurait donc dû tourner sur un nœud **Schedule** interrogeant l'API en boucle —
c'est-à-dire réimplémenter `poller.py` à l'intérieur de n8n, exactement ce que le port n8n est
censé remplacer. Avec ce module, ACA **pousse** ses événements : n8n utilise son nœud **Webhook**
(un déclencheur), le workflow devient événementiel, et la latence tombe de « au pire un intervalle
de sondage » à « immédiat ».

Contrat de dégradation identique à [notify.py](notify.py) : `ACA_WEBHOOK_URL` absente = no-op
silencieux, et **aucune exception ne remonte jamais** — un webhook injoignable ne doit pas faire
échouer une analyse. C'est d'autant plus important que `emit()` est appelé depuis des nœuds du
graphe, tous enveloppés par `RETRY_POLICY` : une exception y provoquerait jusqu'à 3 réexécutions du
nœud (même piège que les `print()` corrigés par `aca/core/console.py`, et que le bug HubSpot de
2026-07-12 qui a dupliqué des leads).

À ne pas confondre avec `notify.py`, qui s'adresse à un **humain** (message Slack en prose, boutons
Valider/Rejeter, e-mail) ; ce module s'adresse à une **machine** (enveloppe JSON structurée,
signée). Les deux coexistent et sont complémentaires.

Signature HMAC : si `ACA_WEBHOOK_SECRET` est défini, chaque envoi porte un en-tête
`X-ACA-Signature: sha256=<hexdigest>` calculé sur le corps brut, plus `X-ACA-Timestamp`. C'est le
miroir sortant de [slack_verify.py](../core/slack_verify.py) : le destinataire (n8n) peut vérifier
que l'appel vient bien d'ACA. Contrairement à `/slack/interactions`, l'absence de secret ne fait
pas échouer l'envoi — un webhook sortant ne déclenche aucune écriture CRM chez nous, le risque
n'est pas symétrique.
"""
import hashlib
import hmac
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# Événements émis. Nommés `objet.action` (convention Stripe/GitHub) pour qu'un filtre n8n puisse
# router dessus sans deviner.
EVENT_PAUSED = "analysis.paused"                # analyse en attente de validation humaine
EVENT_CLARIFICATION = "analysis.clarification"  # le graphe pose une question à l'humain
EVENT_ROUTED = "analysis.routed"                # SUPPORT/AUTRE routé vers l'équipe compétente
EVENT_VALIDATED = "lead.validated"              # écriture CRM effectuée
EVENT_REJECTED = "lead.rejected"                # rejeté, aucune écriture CRM

TIMEOUT_SECONDS = 5


def is_enabled() -> bool:
    """
    `ACA_WEBHOOK_URL` lue DYNAMIQUEMENT (jamais figée à l'import) — même leçon que `DATABASE_URL`
    dans `vector_store.py`, où un gel à l'import avait silencieusement désactivé pgvector pendant
    des semaines.
    """
    return bool(os.getenv("ACA_WEBHOOK_URL"))


def _signature_headers(body: bytes) -> dict:
    """En-têtes de signature HMAC-SHA256, ou `{}` si aucun secret n'est configuré."""
    secret = os.getenv("ACA_WEBHOOK_SECRET")
    if not secret:
        return {}
    timestamp = str(int(time.time()))
    # L'horodatage entre dans la signature : sans lui, un appel intercepté serait rejouable
    # indéfiniment (même raisonnement que la fenêtre de 5 min de `slack_verify.py`).
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {"X-ACA-Timestamp": timestamp, "X-ACA-Signature": f"sha256={digest}"}


def emit(event: str, payload: dict) -> bool:
    """
    Envoie `{event, org_id, timestamp, data}` à `ACA_WEBHOOK_URL`. Renvoie True si le destinataire
    a répondu 2xx, False dans tous les autres cas — **ne lève jamais**.

    L'`org_id` est inclus pour qu'un même point d'entrée n8n puisse servir plusieurs tenants
    (fondation multi-tenant §12 item 3) sans URL distincte par client.
    """
    url = os.getenv("ACA_WEBHOOK_URL")
    if not url:
        return False

    try:
        from aca.core.tenant import current_org_id

        envelope = {
            "event": event,
            "org_id": current_org_id(),
            "timestamp": int(time.time()),
            "data": payload,
        }
        # Corps sérialisé une seule fois : la signature doit porter sur les octets RÉELLEMENT
        # envoyés — re-sérialiser derrière `json=` produirait un ordre de clés potentiellement
        # différent, donc une signature invalide côté destinataire.
        body = json.dumps(envelope, ensure_ascii=False, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", **_signature_headers(body)}
        response = requests.post(url, data=body, headers=headers, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except Exception as e:
        # Volontairement large : ce module est appelé depuis des nœuds du graphe sous
        # `RETRY_POLICY`. Laisser filer une exception réseau y déclencherait une réexécution du
        # nœud — et pour `action_node`, une double écriture CRM.
        print(f"⚠️ Webhook « {event} » échoué : {e}")
        return False


if __name__ == "__main__":
    # `python -m aca.integrations.webhook` — envoi de test, même esprit que `notify.py`.
    if not is_enabled():
        print("ACA_WEBHOOK_URL non configurée — rien à envoyer (dégradation gracieuse).")
    else:
        ok = emit(EVENT_PAUSED, {"thread_id": "test-webhook", "classification": "DEVIS"})
        print("✅ Webhook de test livré." if ok else "❌ Échec de l'envoi (voir le message ci-dessus).")
        if os.getenv("ACA_WEBHOOK_SECRET"):
            print("   Signé (X-ACA-Signature) — le destinataire peut vérifier l'origine.")

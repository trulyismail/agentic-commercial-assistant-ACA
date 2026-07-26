# Palier « Enterprise » — piloter ACA depuis n8n

> **n8n est optionnel.** Le palier « Solo » (`docker compose --profile solo up`, ou
> `python scripts/run_solo.py`) est **déjà entièrement automatisé** : `poller.py` ingère Gmail et
> exécute le graphe 24/7, `scheduler.py` passe les relances et la purge RGPD. n8n n'apporte pas
> l'automatisation — il apporte **l'orchestration avec d'autres systèmes** (votre CRM, votre ERP,
> vos outils internes) et un workflow visuel. Voir §16.0 de [../docs/ACAM_roadmap.md](../docs/ACAM_roadmap.md).

## Ce que le workflow fait

[`aca_workflow.json`](aca_workflow.json) contient **deux flux indépendants** :

```
1. INGESTION       Gmail Trigger ─→ Préparer la charge utile ─→ POST /threads?mode=async
   (remplace poller.py)              (pièces jointes → base64)

2. RÉACTION        Webhook ACA ─→ analysis.paused ? ─→ Mettre en forme l'alerte ─→ (votre canal)
   (événementiel)                                       (risques, injection, lacune de connaissance)
```

Aucune écriture CRM n'est déclenchée par ce workflow. Seul `POST /threads/{id}/valider` écrit dans
le CRM, et il reste derrière une action humaine — c'est le cœur non négociable du projet.

## Mise en place

### 1. Démarrer les deux paliers

```bash
docker compose --profile enterprise up      # API + interface + poller + planificateur + n8n
```

n8n est sur <http://localhost:5678>, l'API sur <http://localhost:8000>, Streamlit sur <http://localhost:8501>.

> Si vous laissez tourner le service `poller`, **désactivez le Gmail Trigger** du workflow (ou
> arrêtez le poller : `docker compose stop poller`) — sinon les deux ingèrent la même boîte. Ils ne
> créeront pas de doublon (`POST /threads` est idempotent et `queue_store` déduplique), mais c'est
> du travail fait deux fois.

### 2. Importer le workflow

Dans n8n : **Workflows → Import from File** → `/workflows/aca_workflow.json`
(le dossier `n8n/` du dépôt est monté en lecture seule dans le conteneur).

### 3. Renseigner les identifiants

| Élément | Où | Valeur |
|---|---|---|
| Compte Gmail | nœud *Gmail Trigger* → Credentials | OAuth2 Google (même compte que `credentials/gmail_credentials.json`) |
| `ACA_API_KEY` | variable d'environnement du conteneur n8n | la même que celle de l'API ACA |
| `ACA_API_URL` | déjà réglée par docker-compose | `http://api:8000` (nom de service du réseau compose) |

### 4. Brancher le webhook sortant — l'étape qui rend le tout événementiel

Copier l'URL de production du nœud *Webhook ACA*, puis côté ACA, dans `.env` :

```bash
ACA_WEBHOOK_URL=http://n8n:5678/webhook/aca-events
ACA_WEBHOOK_SECRET=une-chaine-longue-et-aleatoire   # optionnel mais recommandé
```

Redémarrer l'API (`docker compose restart api poller`).

**Pourquoi cette étape compte.** Sans elle, n8n devrait interroger `GET /threads/pending` en boucle
avec un nœud *Schedule* — c'est-à-dire réimplémenter `poller.py` à l'intérieur de n8n, exactement ce
que le port n8n est censé remplacer. Avec elle, ACA **pousse** ses événements et le workflow se
déclenche immédiatement.

## Événements émis par ACA

Tous arrivent sur la même URL, avec la même enveloppe. Filtrez sur `event` (le nœud *IF* du workflow
le fait déjà pour `analysis.paused`).

| Événement | Quand | Utilisation typique |
|---|---|---|
| `analysis.paused` | une analyse attend une validation humaine | alerter l'équipe, créer une tâche |
| `analysis.clarification` | le graphe pose une question à l'humain | demander l'info manquante |
| `analysis.routed` | SUPPORT/AUTRE routé vers l'équipe compétente | créer un ticket |
| `lead.validated` | **après** l'écriture CRM | facturer, notifier, déclencher un onboarding |
| `lead.rejected` | rejeté, aucune écriture CRM | statistiques, suivi qualité |

Enveloppe :

```json
{
  "event": "analysis.paused",
  "org_id": "default",
  "timestamp": 1785456000,
  "data": {
    "thread_id": "n8n-19a2f...",
    "classification": "DEVIS",
    "classification_confidence": 0.93,
    "extracted_info": { "entreprise": "Example SA", "urgence": "haute" },
    "sender": "contact@example.com",
    "risk_flags": [], "injection_flags": [], "knowledge_gap": false,
    "draft_response": "Bonjour, ..."
  }
}
```

`timestamp` est un entier Unix (epoch), pas une date formatée : il sert aussi de fenêtre anti-rejeu
pour la signature ci-dessous.

`data` a **exactement la même forme** que la réponse de `GET /threads/{id}` (un test le vérifie :
`test_webhook_payload_matches_api_snapshot_shape`), aux trois champs de pause près
(`pending_clarification`, `awaiting_validation`, `done`).

### Vérifier la signature (optionnel)

Si `ACA_WEBHOOK_SECRET` est défini, chaque appel porte `X-ACA-Timestamp` et
`X-ACA-Signature: sha256=<hexdigest>`, calculé en HMAC-SHA256 sur `"<timestamp>." + corps_brut`.
Un nœud *Code* peut le revérifier avant de traiter l'événement.

## Endpoints utiles

Schéma complet : [`../docs/openapi.json`](../docs/openapi.json) — commité **parce que**
`/openapi.json` est coupé en production (§15.3.3), donc inaccessible depuis un déploiement réel.

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/threads` | lancer une analyse (`?mode=async` recommandé ; idempotent sur `thread_id`) |
| `GET` | `/threads/{id}` | état courant |
| `POST` | `/threads/{id}/clarifier` | répondre à une question du graphe |
| `POST` | `/threads/{id}/valider` | **seule route qui écrit dans le CRM** |
| `POST` | `/threads/{id}/rejeter` | rejeter sans écriture CRM |
| `GET` | `/threads/pending` | file d'attente (utile en repli si le webhook n'est pas branché) |
| `GET` | `/health` | sonde — ne joint aucun service externe, appelable librement |

## Limites connues (dites honnêtement)

- **Ce workflow n'a jamais été exécuté contre une vraie instance n8n.** Aucune n'existe pour ce
  projet. Le JSON est structurellement valide (nœuds, connexions et références vérifiés
  automatiquement) et l'API qu'il appelle est couverte par 16 tests dédiés
  (`tests/test_api_n8n.py`), mais les versions de nœuds (`typeVersion`) peuvent demander un
  ajustement selon votre version de n8n.
- Le nœud *Gmail Trigger* et `poller.py` font le même travail : n'en activer qu'un.
- n8n **Cloud est payant** — ce montage utilise l'édition *community* auto-hébergée, gratuite,
  conformément à la contrainte 0 € du projet (§11.5).
- Exposer n8n ou l'API publiquement demande TLS et une clé d'API obligatoire :
  voir [../docs/DEPLOYMENT_HARDENING.md](../docs/DEPLOYMENT_HARDENING.md).

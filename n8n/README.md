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
   (remplace poller.py)              (pièces jointes → base64)         │
                                                    (sortie d'erreur)  └─→ GET /health
                                                                           (ACA est-il tombé,
                                                                            ou juste cet e-mail ?)

2. RÉACTION        Webhook ACA ─→ analysis.paused ? ─→ Mettre en forme l'alerte ─┬─→ Alerter (Slack)
   (événementiel)                                       (risques, injection,     │
                                                         lacune de connaissance) │
3. VALIDATION                                                                    ▼
   (humaine)       Envoyer le lien d'approbation (e-mail) ─→ ATTENDRE la décision humaine
                          (contient $execution.resumeUrl)           (formulaire web)
                                                                             │
                                             ┌───────────────────────────────┴──────────┐
                                       Valider                                     Rejeter
                              POST /threads/{id}/valider               POST /threads/{id}/rejeter
                                 ⚠️ ÉCRITURE CRM                          (aucune écriture)
```

La mise en forme et l'envoi sont **deux nœuds séparés** : pour passer de Slack à Teams, à un e-mail
ou à un ticket, on remplace le seul dernier nœud, sans toucher à la logique d'alerte.

**Le workflow va désormais jusqu'à l'écriture CRM** — mais jamais sans un humain. Le nœud
*Attendre la décision humaine* met l'exécution réellement en pause : tant que le formulaire n'est
pas soumis, `action_node` n'est pas atteint et rien n'est écrit nulle part. C'est le pendant exact
du bouton « Valider » de `ui.py`, champ de correction du brouillon compris. Un seul nœud de tout le
workflow écrit dans le CRM — *ACA — valider (écriture CRM)* — et il est, par construction,
inatteignable sans une soumission humaine.

> Avant le 2026-07-29, ce workflow s'arrêtait à l'alerte : **aucun nœud n'appelait
> `POST /threads/{id}/valider`**, donc il ne pouvait structurellement pas écrire dans le CRM. La
> validation était déléguée à Streamlit ou aux boutons Slack. La moitié « validation » ci-dessus a
> été ajoutée pour que n8n couvre la boucle complète, comme l'interface.

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
| Compte Gmail | nœuds *Gmail Trigger* **et** *Envoyer le lien d'approbation* → Credentials | OAuth2 Google (même compte que `credentials/gmail_credentials.json`) |
| `ACA_API_KEY` | variable d'environnement du conteneur n8n | la même que celle de l'API ACA |
| `ACA_API_URL` | déjà réglée par docker-compose | `http://api:8000` (nom de service du réseau compose) |
| `SLACK_WEBHOOK_URL` | variable d'environnement du conteneur n8n | le **même** webhook entrant que `notify.py` — aucun identifiant n8n à créer |
| `NOTIFY_EMAIL` | variable d'environnement du conteneur n8n | destinataire du lien d'approbation — la même adresse que celle utilisée par `notify.py` |

Les trois variables sont transmises depuis le `.env` par `docker-compose.yml` ; il n'y a donc rien à
saisir dans n8n hormis le compte Gmail. Si `SLACK_WEBHOOK_URL` est absente, le nœud d'alerte échoue
seul et le workflow continue (`onError: continueRegularOutput`) : l'événement reste consultable dans
le journal d'exécution.

> ⚠️ **Sur n8n Cloud, ce fichier ne fonctionne pas tel quel.** Cloud **interdit** l'accès aux
> variables d'environnement : toute expression `$env.…` échoue avec « access to env vars denied », et
> le réglage `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` posé par `docker-compose.yml` est un réglage
> *self-hosted*, inapplicable sur Cloud. Vérifié le 2026-07-28 sur une instance Cloud réelle : `$env`
> lève, `$vars` répond (objet, vide tant qu'aucune variable n'est créée).
>
> Sur Cloud, remplacer les trois expressions par `$vars` et créer les variables correspondantes dans
> **Settings → Variables** :
>
> **Six** nœuds sont concernés — la moitié « validation » en a ajouté trois :
>
> | Nœud | Expression self-hosted (ce fichier) | Expression Cloud |
> |---|---|---|
> | *ACA — lancer l'analyse* (URL) | `{{ $env.ACA_API_URL \|\| 'http://api:8000' }}` | `{{ $vars.ACA_API_URL \|\| 'http://localhost:8000' }}` |
> | *ACA — lancer l'analyse* (en-tête) | `{{ $env.ACA_API_KEY }}` | `{{ $vars.ACA_API_KEY \|\| '' }}` |
> | *ACA — sonde de santé* (URL) | `{{ $env.ACA_API_URL \|\| 'http://api:8000' }}` | `{{ $vars.ACA_API_URL \|\| 'http://localhost:8000' }}` |
> | *Alerter l'équipe (Slack)* (URL) | `{{ $env.SLACK_WEBHOOK_URL }}` | `{{ $vars.SLACK_WEBHOOK_URL }}` |
> | *Envoyer le lien d'approbation* (destinataire) | `{{ $env.NOTIFY_EMAIL }}` | `{{ $vars.NOTIFY_EMAIL }}` |
> | *ACA — valider* et *ACA — rejeter* (URL + en-tête) | `{{ $env.ACA_API_URL }}` · `{{ $env.ACA_API_KEY }}` | `{{ $vars.ACA_API_URL \|\| 'http://localhost:8000' }}` · `{{ $vars.ACA_API_KEY \|\| '' }}` |
>
> Le fichier versionné garde `$env` : le palier Enterprise documenté ici est **auto-hébergé**
> (contrainte 0 €, §11.5), et `$env` y est le mécanisme correct — les secrets restent dans le `.env`,
> jamais dans le workflow versionné. Sur Cloud, `ACA_API_URL` doit de toute façon pointer vers une
> **URL publique** de l'API : une instance Cloud ne peut pas joindre `localhost`.

### 4. Brancher le webhook sortant — l'étape qui rend le tout événementiel

Copier l'URL de production affichée par le nœud *Webhook ACA* (panneau du nœud, onglet
*Production URL*), puis côté ACA, dans `.env` :

```bash
ACA_WEBHOOK_URL=http://n8n:5678/webhook/aca-events
ACA_WEBHOOK_SECRET=une-chaine-longue-et-aleatoire   # optionnel mais recommandé
```

Le chemin est celui du paramètre **`path`** du nœud (`aca-events`), et non son `webhookId`. Ce
dernier ne sert qu'à *engendrer* un chemin lorsqu'aucun n'est renseigné ; ici `path` est explicite,
donc c'est lui qui l'emporte. **Vérifié en conditions réelles le 2026-07-28** contre une instance
n8n Cloud active : `/webhook/aca-events` répond `200 {"message":"Workflow was started"}`, tandis que
la forme `/webhook/<webhookId>/<path>` renvoie `404 … is not registered`.

⚠️ Le champ « Production URL » remonté par l'**API MCP** de n8n affiche pourtant cette seconde
forme, qui ne fonctionne pas. C'est l'URL affichée dans le **panneau du nœud, dans l'éditeur**, qui
fait foi. Une URL erronée se manifeste par un 404 silencieux côté ACA — `webhook.emit()` ne lève
jamais — et jamais par une erreur au démarrage.

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

- **Ce workflow a été importé dans une vraie instance n8n, mais jamais exécuté de bout en bout.**
  L'import (2026-07-28) a d'ailleurs révélé trois défauts que la seule relecture n'avait pas vus, et
  qui sont corrigés ici : le nœud d'alerte n'était relié à rien (le workflow mettait l'alerte en
  forme puis ne l'envoyait à personne) ; la sonde `/health` n'avait aucune entrée et ne pouvait donc
  jamais s'exécuter ; et `getBinaryDataBuffer(item.index ?? 0, …)` lisait toujours l'index 0, parce
  qu'un item n8n n'a pas de propriété `.index` — avec deux e-mails dans un même cycle, le second
  lead recevait les pièces jointes du premier. Le JSON est par ailleurs structurellement valide
  (nœuds, connexions et références vérifiés automatiquement) et l'API qu'il appelle est couverte par
  18 tests dédiés (`tests/test_api_n8n.py`), mais les versions de nœuds (`typeVersion`) peuvent
  demander un ajustement selon votre version de n8n.
- **La moitié « validation » a été poussée sur l'instance Cloud et publiée le 2026-07-29**
  (13 nœuds actifs). La relire *contre la définition réelle des nœuds* — et non de mémoire — a
  révélé deux défauts qu'une simple relecture avait laissés passer, tous deux corrigés ici :
  (1) l'e-mail d'approbation transportait `$execution.resumeUrl` alors que le nœud *Wait* est en
  mode `form`. Les deux variables pointent vers des chemins **différents** (`/webhook-waiting/`
  contre `/form-waiting/`) et la définition du nœud ne rattache `resumeFormUrl` qu'à
  `resume: ["form"]` : le lien envoyé était donc mort, ou reprenait l'exécution sans réponses de
  formulaire — auquel cas `Décision` est vide et l'aiguillage sûr retombe sur « Rejeter ». La
  boucle d'approbation était cassée dans les deux cas. (2) `formSubmittedText` était placé
  directement sous `options`, où il n'existe pas dans le schéma du nœud : il était donc
  silencieusement ignoré, au profit du texte de confirmation par défaut de n8n. Le vrai chemin est
  `options.respondWithOptions.values.formSubmittedText`.
- **La moitié « réaction » a été exécutée pour de vrai le 2026-07-28** contre l'instance Cloud
  active : une analyse réelle s'est arrêtée à la pause, a émis `analysis.paused`, et le workflow l'a
  reçue, filtrée et mise en forme (exécution n°8, `mode: webhook`). L'alerte portait le bon
  `thread_id`, l'entreprise déduite du domaine et les drapeaux de risque contractuel.
- **La moitié « ingestion » n'a pas été exécutée**, et bute sur deux contraintes d'environnement, pas
  de code : le déclencheur Gmail exige un consentement OAuth via navigateur, et une instance n8n
  **Cloud** ne peut pas joindre une API ACA locale — elle refuse les adresses de bouclage et privées
  au titre de la protection anti-SSRF (`The request was blocked because it resolves to a restricted
  IP address`). Le sens compte : ACA → n8n fonctionne (sortant), n8n → ACA non. Pour l'éprouver,
  soit héberger n8n localement, soit exposer l'API par un tunnel ou un hébergement public.
- Le nœud *Gmail Trigger* et `poller.py` font le même travail : n'en activer qu'un.
- n8n **Cloud est payant** — ce montage utilise l'édition *community* auto-hébergée, gratuite,
  conformément à la contrainte 0 € du projet (§11.5).
- Exposer n8n ou l'API publiquement demande TLS et une clé d'API obligatoire :
  voir [../docs/DEPLOYMENT_HARDENING.md](../docs/DEPLOYMENT_HARDENING.md).

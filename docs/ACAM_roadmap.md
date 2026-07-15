# ACAM — Assistant Commercial Agentique Multimodal
### Feuille de route & architecture (projet de stage, 8 semaines)

> Ce document reformule le prototype **ACA** en **ACAM** et cartographie ce qui est **fait** ✅
> et ce qui **reste** 🔨. Il sert de référence pour le rapport de stage et la soutenance.

---

## 1. Concept & problématique

Les prospects envoient des e-mails accompagnés de documents (PDF, cahiers des charges). Leur
traitement manuel crée des goulots d'étranglement. **Problématique :** *comment automatiser
l'ingestion multimodale, l'enrichissement contextuel (RAG) et la qualification des leads tout en
gardant un contrôle humain rigoureux ?*

Le choix de **LangGraph** (vs CrewAI / LangChain Agents) se justifie par son architecture en
**graphe cyclique d'états** : contrôle déterministe et **interruption native** pour la validation
humaine — indispensable avant toute écriture dans un CRM.

## 2. Les 3 piliers d'innovation

1. **Analyse multimodale (e-mail + document)** — l'agent traite conjointement le corps du texte et
   la pièce jointe PDF pour produire une qualification structurée (JSON).
2. **RAG « database-less » (Google Sheets), sémantique** — enrichissement dynamique par embeddings
   (Gemini, gratuit) + similarité cosinus sur une base de connaissances vivant dans Google Sheets,
   sans base vectorielle dédiée à héberger.
3. **Mémoire hybride + Human-in-the-loop** — l'agent fige son exécution, **conserve sa mémoire** et
   attend une validation sur Streamlit avant d'agir.

## 3. Architecture du graphe agentique (ACAM v2 — superviseur + équipe)

```
START
 → classifier_node      (Llama-8B)   Classe l'e-mail : DEMANDE_DEMO | DEVIS | SUPPORT | AUTRE | SPAM
 → memory_lookup_node   (CRM read)   Cherche l'expéditeur dans 'Leads' → historique + doublon
 → extractor_node       (Llama-70B)  Extraction JSON {entreprise, contact, urgence, besoin}
 → clarification_node   (interrupt)  ❓ Si besoin flou → pose UNE question à l'humain, puis reprend
 → supervisor_node      (Llama-8B)   Oriente dynamiquement l'équipe (garde-fous déterministes) ⇄ workers
        ├─ enrichissement_node       Profil entreprise (Tavily + cache Sheets long terme)
        ├─ connaissance_node         RAG hybride (dense Gemini + mots-clés, fusion RRF) → contexte FAQ
        ├─ veille_node               Repli web (Tavily) si la FAQ est vide → enrichit la FAQ (staging)
        └─ stratege_node   (Llama-70B) Proposition + devis (profil + FAQ + historique)
              → reflection_node (8B)  Auto-critique du brouillon ──rewrite (1x max)──▶ stratege
 → routing_node         (SUPPORT/AUTRE → équipe compétente : alerte + brouillon de transfert Gmail)
 → notification_node    (Slack/e-mail : « une analyse attend votre validation »)
 ── interrupt_before ──              ⏸️  PAUSE : validation humaine (Streamlit « Valider »)
 → action_node          (write)      Écrit dans 'Leads' + HubSpot + marque l'e-mail Gmail traité + brouillon de réponse
 → END

Ingestion (hors graphe) :  doc/PDF/Markdown ──(Groq → Q/R)──▶ onglet Knowledge_Base (Sheets)
```

Compilation : `app = workflow.compile(checkpointer=..., interrupt_before=["action"])` — le
checkpointer est `PostgresSaver` (Supabase) si `DATABASE_URL` est configurée, sinon `SqliteSaver`
(fichier local) ; le `MemorySaver` volatile des débuts a été remplacé (§11.4 item 4, puis §11.2).
Le superviseur boucle avec les workers via `add_conditional_edges`. Deux interruptions humaines :
**clarification** en cours de route (`interrupt()` dynamique, repris par `Command(resume=...)`) et
**validation** finale (`interrupt_before`).

## 4. Le système de mémoire hybride

| Type | Outil | Rôle |
|---|---|---|
| **Court terme** (working memory) | `PostgresSaver` (Supabase) ou `SqliteSaver` (checkpointer LangGraph — `MemorySaver` remplacé, cf. §11.4 item 4 et §11.2) | Conserve l'état du lead pendant la **pause de validation**, y compris à travers un redémarrage de l'app et entre processus (`ui.py`/`poller.py`). L'agent peut « dormir » puis reprendre exactement où il en était (`invoke(None, config)`), sans réinterroger le LLM. |
| **Long terme — CRM** | Google Sheets, onglet **`Leads`** | Historique commercial. Lu **avant** traitement (`find_leads_by_sender`) → détecte les clients récurrents et les doublons ; écrit **après** validation. |
| **Long terme — Connaissances** | Google Sheets, onglet **`FAQ`** (Knowledge_Base) | Tarifs, délais, règles. Interrogé par `search_knowledge_base_semantic` (embeddings Gemini + similarité cosinus, avec repli sur la recherche par mots-clés si `GOOGLE_API_KEY` est absente) ; alimenté par l'ingestion doc/PDF (`ingest.py`). |
| **Long terme — Enrichissement** | Google Sheets, onglet **`Enrichissement_Cache`** | Profils d'entreprise déjà recherchés (par domaine). L'agent Enrichissement y lit **avant** tout appel Tavily et y écrit après → évite les recherches web répétées. |

Chaque analyse reçoit un `thread_id` (UUID) stocké dans `st.session_state`, qui relie l'interruption
à sa reprise.

> **Argumentaire soutenance :** « L'agent possède une mémoire à court terme gérée par les checkpoints
> de LangGraph pour gérer les pauses de validation, et une mémoire à long terme déportée sur Google
> Sheets pour l'historique commercial et les connaissances de l'entreprise. »

## 5. Planning de réalisation (8 semaines)

| Semaine | Objectif | Statut |
|---|---|---|
| 1–2 | Init LangGraph + LLM + Google Sheets (lecture/écriture), variables de test en dur | ✅ Fait |
| 3–4 | Prompt engineering (few-shot) + analyse multimodale (lecture des PDF joints) | ✅ Fait |
| 5–6 | RAG « database-less » → **nœud dédié `rag_retrieval` + recherche par mots-clés** | ✅ Fait |
| 7 | Intégration de la **vraie API Gmail** (ingestion des e-mails non lus + marquage) | ✅ Fait |
| 7–8 | **Mémoire hybride** (`MemorySaver` + interrupt), **mémoire long terme** (client récurrent + anti-doublon), **fix taxonomie** (catégorie `AUTRE`) | ✅ Fait |
| 8 | Tests de robustesse (validate-loop réel de bout en bout) | ✅ Fait (cf. §8 — E2E headless `AppTest` vérifié sur le vrai Sheet) |
| 8 | Rapport final (livrable académique) + backlog Scrum/user stories | 🔨 Restant (matière première : `PROJECT_JOURNAL.md`) |

## 6. Stack technique

- **Orchestration :** Python / LangGraph (`StateGraph`, checkpointer `PostgresSaver`/`SqliteSaver`,
  `interrupt_before`, `app.stream` pour la progression en direct côté UI).
- **Intelligence (texte) :** Groq — Llama-3.1-8B (routage) & Llama-3.3-70B (extraction/rédaction).
  *Gratuit.* JSON structuré obtenu par prompting strict (équivalent « Structured Outputs »).
- **Intelligence (embeddings/RAG) :** Google Gemini (`gemini-embedding-001`, `google-genai`). *Gratuit*
  (10M tokens/min). Groq n'expose pas d'API d'embeddings ; Gemini comble uniquement ce trou, tout le
  reste (classification/extraction/rédaction) reste sur Groq.
- **Données (CRM & RAG) :** Google Sheets API (`gspread` + service account).
- **Ingestion :** Gmail API (OAuth « installed app », `gmail.modify`).
- **Documents :** PyMuPDF. **Interface :** Streamlit (thème Fluent, progression du pipeline en direct
  via `st.status`).

## 7. Justification métier

- **Coût :** modèles Groq gratuits + Google Sheets → zéro infrastructure de base de données.
- **Maintenabilité :** commerciaux et managers mettent à jour tarifs / FAQ directement dans le Sheet,
  sans toucher au code.
- **Sécurité / conformité :** aucune écriture CRM autonome — chaque lead passe par une validation
  humaine (human-in-the-loop) avant d'être enregistré.

## 8. Reste à faire (Semaine 8)

- ✅ Test de bout en bout dans l'UI réelle (headless, via `streamlit.testing.v1.AppTest`) : e-mail
  **DEVIS** non-SPAM → classification, bandeau « client connu » + bandeau doublon corrects →
  « Valider » → nouvelle ligne confirmée dans `Leads` (7 lignes, avant/après vérifié en direct sur
  le Google Sheet réel). Reste à faire : le même test avec un e-mail **importé de Gmail** (pour
  vérifier le marquage `ACA-Traite` / retrait `UNREAD`), non exécuté faute d'e-mail commercial
  réel non lu dans la boîte de test.
- Rédaction du rapport de stage (livrable académique).

## 9. Capacités LangGraph non exploitées

État réconcilié après ACAM v2 : le graphe n'est plus un pipeline linéaire mais un **superviseur +
équipe d'agents** avec deux types d'interruption. Plusieurs capacités listées ici comme « non
utilisées » sont désormais implémentées (marquées ✅) ; les autres restent des pistes :

| Capacité LangGraph | Ce qu'elle apporterait à ACAM | État actuel |
|---|---|---|
| **`add_conditional_edges`** (routage dynamique) | Le classifieur pourrait router directement vers des chemins spécialisés (ex : un nœud d'escalade pour `SUPPORT` urgent) au lieu d'un `if` statique dans chaque nœud (`CATEGORIES_SANS_SUITE`) | ✅ **Fait (ACAM v2)** — le superviseur route dynamiquement vers `enrichissement`/`connaissance`/`veille`/`stratege`/`action` via `add_conditional_edges` |
| **`interrupt()` dynamique** (au lieu de `interrupt_before` statique) | Permettrait à l'agent de demander une clarification humaine *au milieu* d'un nœud (ex : « urgence ambiguë, confirmez SVP ») plutôt qu'un seul point de pause fixe avant `action` | ✅ **Fait (ACAM v2)** — `clarification_node` pose une question via `interrupt()` quand `besoin_principal` est vide, reprise par `Command(resume=...)` |
| **Store API** (`langgraph.store`, mémoire long terme native, cross-thread) | Remplacerait/compléterait la lecture manuelle de Google Sheets par une mémoire sémantique interrogeable (embeddings) partagée entre threads, gérée par le framework | Non utilisé — la mémoire long terme passe entièrement par des appels `gspread` custom dans `sheets.py` |
| **Checkpointer persistant** (`SqliteSaver` / `PostgresSaver` au lieu de `MemorySaver`) | Les pauses de validation survivraient à un redémarrage de l'app (actuellement perdues en mémoire RAM) | ✅ **Fait** — `SqliteSaver` (§11.4 item 4), puis `PostgresSaver` sur Supabase quand `DATABASE_URL` est configurée (§11.2), vérifié à travers un redémarrage simulé et entre deux processus distincts |
| **Streaming** (`astream_events` / `stream_mode="messages"`) | L'UI pourrait afficher la progression nœud par nœud (« classification en cours... », « rédaction... ») au lieu d'un `st.spinner` bloquant unique | ✅ **Fait** — `ui.py` utilise `app.stream()` et affiche chaque nœud en direct dans un `st.status` |
| **Exécution parallèle (fan-out/fan-in)** | `memory_lookup_node` et `rag_retrieval_node` sont indépendants (l'un lit `Leads`, l'autre `FAQ`) — ils pourraient s'exécuter en parallèle au lieu de séquentiellement | Non utilisé — chaîne strictement séquentielle |
| **`RetryPolicy` par nœud** | Retry automatique en cas d'erreur API Groq/Sheets transitoire, sans faire échouer tout le graphe | ✅ **Fait** (§11.4 item 9) — `RETRY_POLICY` (3 tentatives, backoff, couvre aussi les 429) sur tous les nœuds appelant une API externe, vérifié avec un 429 simulé |
| **Sous-graphes (`subgraphs`)** | Un sous-graphe `ingestion` dédié (PDF + e-mail) réutilisable indépendamment du pipeline de qualification, conforme à l'architecture cible du document de vision | Non utilisé — l'extraction PDF vit dans `ui.py`, hors du graphe |
| **Time travel / `get_state_history()`** | Permettrait de rejouer ou d'auditer une décision passée de l'agent (utile pour justifier une classification en cas de litige) | Non utilisé — seul `get_state()` (état courant) est appelé, dans le `__main__` de démo |
| **Agents à outils (`bind_tools` / ReAct)** | Le `draft_writer_node` pourrait appeler des outils (vérifier un agenda, calculer un tarif exact) plutôt que produire un texte figé en un seul appel LLM | Non utilisé — aucun tool-calling, uniquement des prompts système/humain fixes |
| **Orchestration multi-agents (superviseur)** | Un agent superviseur pourrait déléguer `DEVIS` vs `SUPPORT` vs `DEMANDE_DEMO` à des sous-agents spécialisés avec leurs propres prompts/outils | ✅ **Fait (ACAM v2)** — `supervisor_node` (Llama-8B + garde-fous) orchestre l'équipe `enrichissement`/`connaissance`/`veille`/`stratege` |

## 10. Pistes d'amélioration de l'agent

Classées par effort estimé (croissant) :

- **Few-shot prompting** — les prompts de `classifier_node` / `extractor_node` sont zéro-shot ;
  ajouter 2-3 exemples annotés par catégorie réduirait les erreurs de classification en bordure
  (ex : SUPPORT vs DEVIS ambigus).
- ✅ **Fait (2026-07-12) — `with_structured_output()` / Pydantic** au lieu de `json.loads()` manuel
  dans `extractor_node` : nouveau modèle `ExtractedInfo` (Pydantic), extraction forcée par
  tool-calling côté Groq — plus de JSON malformé à parser, plus de fallback `{"raw": ...}` fantôme
  (rien en aval ne le lisait). Repli gracieux si l'extraction structurée échoue malgré tout (réseau,
  sortie hors schéma) : `ExtractedInfo()` vide plutôt qu'un plantage de `app.invoke()` — traité
  ensuite comme un e-mail vague par `clarification_node`. Vérifié : 3 nouveaux tests unitaires +
  3 appels réels contre Groq (champs complets, champs manquants, repli sur schéma vide simulé).
- **Score de confiance de classification** — faire retourner un score (0-1) par `classifier_node`
  et router vers une relecture humaine systématique en dessous d'un seuil, au lieu d'un
  tout-ou-rien SPAM/AUTRE/valide.
- ✅ **`RetryPolicy` + gestion d'erreur réseau** — fait (§11.4 item 9) : `RETRY_POLICY` sur tous les
  nœuds à appel externe, prédicat étendu aux 429, vérifié avec une erreur simulée.
- **Nœud `Ingestion`** explicite en tête de graphe (PDF + e-mail) — actuellement l'extraction PDF
  se fait dans `ui.py` avant l'appel à `app.invoke()`, ce qui casse l'encapsulation du graphe et
  empêche de rejouer un test avec pièce jointe depuis le seul `app.py`.
- ✅ **Traitement par lot des e-mails non lus** — fait (§11.4 item 3) : `poller.py` traite
  automatiquement chaque e-mail non lu en série jusqu'à la pause de validation, avec file
  d'attente visible dans la sidebar (« File d'attente », `queue_store.py`).
- ✅ **UI asynchrone / streaming** — fait : `ui.py` remplace le `st.spinner` bloquant par
  `app.stream()` + un `st.status` affichant chaque nœud en direct.
- ✅ **Nœud « Reflect » (auto-critique)** — fait (2026-07-12) : `reflection_node` (Llama-8B) relit
  le brouillon du Stratège face au contexte FAQ réellement utilisé ; « REWRITE : raison » renvoie au
  Stratège (une seule réécriture max, garde-fou anti-boucle), « OK » continue vers la validation.
  Vérifié en direct : une redondance réelle détectée, réécrite une fois, puis passage.
- ✅ **RAG sémantique (embeddings)** — fait : `search_knowledge_base_semantic` (embeddings Gemini +
  similarité cosinus) remplace le recouvrement de mots-clés comme chemin principal, avec repli
  automatique sur `search_knowledge_base` si `GOOGLE_API_KEY` est absente ou l'appel échoue. Vérifié en
  direct : une reformulation ne partageant aucun mot-clé avec la FAQ ("Combien coûte votre abonnement
  mensuel pour une petite équipe ?" vs. "Quels sont vos tarifs professionnels ?") est correctement
  retrouvée par la recherche sémantique, là où la recherche par mots-clés ne renvoyait rien.
- ✅ **Notification Slack/e-mail pour les leads urgents** — fait sous une forme généralisée
  (§11.4 item 2) : `notification_node` alerte pour **chaque** lead en attente de validation (pas
  seulement `urgence == "haute"`), juste avant la pause. Le filtrage par urgence reste un
  raffinement optionnel si le volume rend les alertes trop bruyantes.
- ✅ **Persistance `SqliteSaver`** — fait (§11.4 item 4), puis dépassé : `PostgresSaver` (Supabase)
  quand `DATABASE_URL` est configurée (§11.2), partagé entre `ui.py` et `poller.py`.

## 11. Production — usage réel en entreprise (contrainte : 0 €)

Analyse de ce qu'il faudrait pour qu'une vraie entreprise utilise ce workflow au quotidien.
Tout ce qui suit respecte la contrainte du projet : **aucun coût** (free tiers uniquement).

### 11.1 Vector DB : verdict initial — NON nécessaire par la seule volumétrie (migré quand même, à la demande explicite de l'utilisateur)

Le premier mur n'aurait **pas été la taille de la FAQ mais les quotas de l'API Sheets** (~300
lectures/min par projet, 60/min par utilisateur) : c'est un polling automatique ou plusieurs
instances de l'app qui auraient provoqué des erreurs 429, bien avant que la base soit « trop
grande ». La similarité cosinus en mémoire tenait sans problème mesurable jusqu'à **~1 000–2 000
lignes** de FAQ.

Limites réelles de l'ancien design (acceptables en prototype, corrigées par la migration ci-dessous) :
- cache d'embeddings **par processus** : non partagé entre instances, perdu au redémarrage ;
- `get_all_records()` relit tout l'onglet à chaque vérification de signature ;
- aucune écriture concurrente sûre (course possible entre `ingest.py` et l'agent `veille`).

**Déclencheurs de migration initialement prévus** (un seul aurait suffi) : FAQ > ~1 000 lignes ·
plus d'une instance de l'app · p95 de récupération > ~2 s · besoin de filtres métadonnées /
recherche hybride. **Aucun n'était atteint** au moment de la migration (2026-07-11) — l'utilisateur
a choisi d'avancer la migration malgré tout, pour la valeur structurelle du vector DB partagé
(cache non perdu au redémarrage, partagé entre `ui.py`/`poller.py`) plutôt que d'attendre un
déclencheur de volume. Assumé consciemment : c'est en avance sur le besoin strict, mais le repli
gracieux (`DATABASE_URL` absente) garantit qu'aucune régression n'est possible pour quiconque n'a
pas encore configuré Supabase.

Exemples réels (toujours valables comme repère) : la FAQ produit de 200 lignes d'une PME → Sheets
aurait suffi indéfiniment ; importer les 50 000 articles d'un helpdesk Zendesk → vector DB obligatoire.

### 11.2 Quel vector DB — décision : Supabase (pgvector), ✅ implémenté

| Option | Coût | Points forts | Limites | Nœud n8n natif ? |
|---|---|---|---|---|
| **Supabase (pgvector)** ⭐ **décision actée et implémentée** | Free tier (500 Mo, sans carte) | UN service gratuit couvre TOUT : vector store + Postgres (`PostgresSaver`) | Projet gratuit en pause après 7 j d'inactivité (réveil en 1 clic) | ✅ (les templates « Agentic RAG » n8n l'utilisent) |
| Qdrant Cloud free | 1 Go gratuit à vie, sans carte | Dédié vecteur, jamais en pause | Ne résout QUE le vecteur (il aurait fallu un Postgres séparé pour le checkpointer) | ✅ |
| Chroma (embarqué) | 0 € (pip install) | Zéro serveur, migration la plus simple | Mono-instance — ne règle pas le partage entre `ui.py`/`poller.py`, l'un des problèmes que cette migration visait à résoudre | ❌ |
| Pinecone free | Gratuit mais propriétaire | — | Lock-in, limites floues | ✅ |

**Décision (2026-07-10, mise en œuvre le 2026-07-11) : Supabase (pgvector).** Un seul free tier
sert à la fois de vector store ([vector_store.py](aca/integrations/vector_store.py)) et de checkpointer LangGraph
(`PostgresSaver` dans `app.py`, remplaçant `SqliteSaver`) — et c'est ce que les workflows
« Agentic RAG » n8n (l'inspiration d'origine du projet) utilisent nativement. L'onglet Leads reste
sur Google Sheets pour l'instant (cf. item 15, « vrai CRM » — un choix distinct, pas résolu par un
déplacement vers une table Postgres brute). Qdrant restait le plan B si la pause de 7 jours gênait ;
non retenu car il n'aurait résolu que la moitié du problème (le vecteur, pas le checkpointer).

### 11.3 Quelles données dans le vector DB, d'où viennent-elles, est-ce automatique ?

Trois familles — c'est l'onglet FAQ d'aujourd'hui, généralisé. **Rien à acheter, aucun corpus
externe à trouver : le workflow produit et ingère déjà ces données**, la migration ne change que
le lieu de stockage.

1. **Connaissance produit** (paires Q/R) — *source : documents fournis par l'entreprise* (docs
   produit, grille tarifaire, CGV, fiches techniques). *Alimentation : semi-automatique* —
   l'humain fournit le document, `ingest.py` / l'uploader Streamlit fait le découpage Q/R et
   l'écriture. **Déjà construit.**
2. **Connaissance auto-apprise** — *sources :* l'agent `veille` (web) et, à ajouter, **chaque
   proposition validée** : au clic « Valider », la paire (besoin du prospect → proposition
   approuvée) peut être archivée automatiquement comme exemple pour le `stratege` (« retrouve
   3 devis passés similaires » = few-shot récupéré, 0 appel web, 0 coût). *Alimentation :
   automatique* — MAIS la partie web (veille) exige le staging humain (§11.4, item 6) ;
   l'auto-apprentissage sur les validations est sûr, car déjà validé par l'humain.
3. **Historique relationnel** — *source : les leads/échanges passés* (aujourd'hui cherchés par
   simple égalité d'expéditeur dans `find_leads_by_sender`) ; vectorisés, ils permettraient « ce
   prospect ressemble à tel client gagné ». *Alimentation : automatique* (chaque lead validé).

### 11.4 Ce qui manque pour un usage réel — la boucle n'est pas bouclée

Le constat central : **le brouillon validé ne part jamais** — après « Valider », la proposition
reste affichée dans Streamlit et le commercial doit la copier-coller. Priorités :

**P0 — Boucler la boucle (sans ça, aucune entreprise ne l'utilise)**

1. ✅ **Fait — Créer la réponse dans Gmail** après « Valider » : `action_node` appelle
   `gmail_reader.create_draft_reply()`, qui crée un **brouillon Gmail dans le fil d'origine**
   (`threadId` + en-têtes `In-Reply-To`/`References` corrects, scope `gmail.modify` déjà accordé) —
   le commercial relit dans Gmail et clique Envoyer, rien n'est jamais auto-envoyé. Vérifié : compile
   + run CLI mock sans régression (le brouillon n'est créé que si l'e-mail vient de Gmail, via
   `gmail_message_id`). Modèle Fyxer/Superhuman : l'IA drafte dans la boîte, l'humain envoie.
2. ✅ **Fait — Notification humaine** : [notify.py](aca/integrations/notify.py) tente Slack (`SLACK_WEBHOOK_URL`,
   webhook entrant gratuit) puis un e-mail à soi-même via l'API Gmail déjà authentifiée
   (`NOTIFY_EMAIL`, zéro nouveau service) — chaîne de repli gracieux, comme Tavily/Gemini. Appelé
   par un nouveau nœud `notification_node` juste avant la pause de validation (sauf SPAM/AUTRE).
   Vérifié : run CLI complet sans les deux variables (repli gracieux, log "aucun canal configuré",
   pas de crash) + webhook volontairement invalide (404 absorbé, `send()` renvoie `False`). Pas
   encore testé contre un vrai canal Slack/e-mail (aucune destination configurée pour l'instant).
   Sans signal, l'outil devient une page qu'on oublie d'ouvrir — or la latence de réponse est LE
   facteur de conversion inbound (répondre < 1 h vs > 24 h change radicalement le taux de contact).
3. ✅ **Fait — Intake automatique + traitement par lot** : [poller.py](aca/core/poller.py), un process
   séparé (`python -m aca.core.poller`, indépendant de Streamlit) qui interroge `list_unread_emails` toutes
   les `POLL_INTERVAL_SECONDS` (défaut 60s), fait avancer chaque nouvel e-mail dans le graphe
   jusqu'à la même pause de validation que le flux manuel (jamais au-delà — un humain valide
   toujours), et l'enregistre dans [queue_store.py](aca/storage/queue_store.py) (registre SQLite local,
   `data/queue.sqlite`) pour ne pas le retraiter à chaque cycle (l'e-mail reste `UNREAD` côté Gmail
   jusqu'à validation). L'UI affiche une section « File d'attente » en haut de la sidebar ; un
   clic sur « Ouvrir » charge l'état déjà calculé (pas de re-calcul) via `load_queued_thread()`.
   Vérifié en direct (sans écrire sur le vrai CRM) : mise en file → visible dans l'UI → chargement
   de l'état en pause → retrait de la file après validation simulée. Rôle naturel du futur shell
   n8n (trigger Gmail natif remplacerait le poller maison).
4. ✅ **Fait — Persistance du checkpointer** : `MemorySaver` → `SqliteSaver`
   (`langgraph-checkpoint-sqlite`, fichier local `data/checkpoints.sqlite`, 0 €). Vérifié en direct :
   une analyse lancée dans un process Python, puis relue dans un second process totalement
   indépendant (nouvelle connexion SQLite = redémarrage simulé), récupère l'état intact (pause,
   classification, proposition). Migration future vers `PostgresSaver` (Supabase) inchangée au
   §11.2 si le volume l'exige.
5. ✅ **Fait — Router TOUTES les catégories** : `SUPPORT` et `AUTRE` rejoignent désormais
   `CATEGORIES_SANS_SUITE` (plus de Stratège/CRM pour elles — une proposition commerciale ne
   correspond pas à un ticket technique ou une candidature) et sont prises en charge par un nouveau
   nœud `routing_node`, inséré entre le superviseur (FINISH) et `notification`. Table déclarative
   `ROUTING_DESTINATIONS` (catégorie → libellé/e-mail/webhook), pour qu'ajouter une future catégorie
   routée ne demande qu'une entrée + une paire de variables d'environnement. Deux actions par
   catégorie routée, chacune dégradée gracieusement (repli silencieux si rien n'est configuré, même
   principe que Tavily/Slack/Calendly) : (a) une alerte immédiate via `notify.send()` — généralisé
   pour accepter un webhook/e-mail/sujet différents du canal générique des leads — vers
   `SUPPORT_EMAIL`/`SUPPORT_SLACK_WEBHOOK_URL` ou `HR_EMAIL`/`HR_SLACK_WEBHOOK_URL` ; (b) un
   brouillon de **transfert** Gmail (`gmail_reader.create_forward_draft`, jamais auto-envoyé — même
   pattern que `create_draft_reply`) prérempli avec le message d'origine, si l'e-mail vient de Gmail
   et qu'une adresse est configurée. L'UI affiche SUPPORT comme AUTRE (encart + détail du routage
   dans un panneau dépliant), sans fiche CRM ni bouton « Valider ». Vérifié : run CLI mock (cas
   SUPPORT ajouté) sans crash, `reasoning_log` journalise correctement l'absence de canal configuré
   (aucune destination réelle dans `.env` pour l'instant — voir note ci-dessous).
6. ✅ **Fait — Staging pour l'agent `veille`** (défaut de conception corrigé) : la FAQ a désormais
   une colonne `Statut` (`Question | Réponse | Statut`, en-tête legacy 2 colonnes étendu
   automatiquement) ; `veille` écrit avec `statut="à valider"`, invisible du RAG
   (`sheets._get_knowledge_records()` filtre `à valider`/`rejeté`) jusqu'à validation humaine via le
   panneau « FAQ en attente » de la sidebar Streamlit (`get_pending_knowledge_rows` +
   boutons Valider/Rejeter → `approve_knowledge_row`/`reject_knowledge_row`). Scénario réel évité :
   un prospect demande « intégrez-vous Salesforce ? », Tavily trouve la page d'un AUTRE éditeur,
   l'agent écrit « oui » — cette ligne reste invisible du RAG jusqu'à ce qu'un humain la valide.
   Vérifié en direct sur la vraie feuille : écriture en attente → invisible du RAG → validée →
   visible du RAG → ligne de test supprimée (round-trip complet, aucune trace laissée).

**P1 — Fiabilité (dès la première semaine d'usage réel)**

7. ✅ **Fait — Relances automatiques** : [followup_store.py](aca/storage/followup_store.py) (registre local des
   leads validés venant de Gmail) + [relance.py](aca/core/relance.py) — pour chaque lead suivi, lit le
   dernier message du VRAI fil Gmail (`threads().get()`, distinct du `thread_id` LangGraph) ; si
   c'est nous qui avons parlé en dernier et que `RELANCE_DAYS` (défaut 4) sont passés, crée un
   brouillon de relance dans le fil (`create_draft_reply`, jamais un envoi automatique) ; si c'est
   le prospect, rien à faire. Une seule relance par lead dans cette version (pas de cadence
   multi-round — ~80 % des ventes demandent 5+ contacts, donc une vraie cadence reste une piste
   d'amélioration). Vérifié avec un service Gmail factice couvrant les 3 cas : prospect a répondu
   (rien), trop tôt (rien), seuil atteint (brouillon créé + lead retiré du suivi). Trivial à
   porter en n8n (Wait node) plus tard.
8. ✅ **Fait — Idempotence + conscience du fil** : [queue_store.py](aca/storage/queue_store.py) marque un
   e-mail `en_cours` **avant** `app.invoke()` (plus après) — un crash du poller en cours d'analyse
   n'entraîne plus de retraitement en double (`is_known()` est déjà vrai). `mark_ready()` bascule
   vers `en_attente` une fois la pause atteinte sans erreur ; `reset_stale()` récupère les entrées
   bloquées en `en_cours` après un délai (défaut 15 min), pour qu'un vrai crash ne perde pas
   l'e-mail indéfiniment. Vérifié en direct (entrée délibérément vieillie → récupérée par
   `reset_stale`, cycle complet enqueue→mark_ready→visible dans la file). La partie « réponse du
   prospect enrichit le lead existant » reste non traitée (nécessiterait de suivre les réponses
   dans le fil Gmail, cf. item 7).
9. ✅ **Fait — Retries / limites de débit** : `RetryPolicy` LangGraph (`app.RETRY_POLICY`) sur tous
   les nœuds qui appellent une API externe (Groq/Sheets/Gemini/Tavily/Gmail), avec un prédicat
   `_retry_on` qui étend le comportement par défaut pour couvrir aussi les 429 (le cas Groq free
   tier ≈ 30 req/min le plus probable), en plus des 5xx/erreurs réseau déjà couverts. Jamais de
   retry sur une erreur de programmation (`ValueError`/`TypeError`...). Vérifié en direct : une
   erreur 429 simulée sur le premier appel d'un nœud est absorbée (2 appels, résultat correct,
   pas de crash de `app.invoke()`).
10. ✅ **Fait — Auth + traçabilité minimale** : gate mot de passe optionnel
    (`ACA_UI_PASSWORD`, [ui.py](ui.py) `_check_auth()`) devant toute l'UI ; absent = pas de gate
    (mode développement, dégradation gracieuse comme les autres options). Traçabilité :
    [audit_log.py](aca/storage/audit_log.py) enregistre qui (champ « Validé par » dans la sidebar), quoi,
    quand à chaque clic sur « Valider ». Pas un vrai système multi-utilisateurs — suffisant pour
    un usage solo/petite équipe. Vérifié : `AppTest` headless (gate bloque sans bon mot de passe,
    débloque avec) + appel direct de `log_validation`/`list_recent`.
11. ✅ **Fait — Observabilité + évaluation** : traçage LangSmith activé (`LANGCHAIN_TRACING_V2`
    + `LANGCHAIN_API_KEY` + `LANGCHAIN_PROJECT=ACA` dans `.env`) — aucun changement de code requis,
    `langchain`/`langgraph` s'auto-instrumentent. Vérifié en direct : connexion au client confirmée
    et 5 traces retrouvées dans le projet "ACA" (détail par nœud : `classifier`, `supervisor`,
    `notification`...) après un run mock. [eval_dataset.json](aca/eval/eval_dataset.json) (50 e-mails
    synthétiques, 10/catégorie, dont quelques cas volontairement ambigus) +
    [eval_classifier.py](aca/eval/eval_classifier.py) mesurent la précision réelle du classifieur — **résultat
    mesuré : 96 % (48/50)**, DEMANDE_DEMO/DEVIS/SPAM à 100 %, AUTRE et SUPPORT à 90 % chacun. Les 2
    erreurs sont sur des cas ambigus délibérés (« compte suspendu par erreur » classé AUTRE au lieu
    de SUPPORT ; un message très vague classé SPAM au lieu d'AUTRE) — cohérent avec le comportement
    attendu, pas un signal d'alarme. À refaire périodiquement avec de vrais e-mails une fois
    disponibles, pour suivre la précision en conditions réelles.
12. ✅ **Fait — Créneaux réels pour DEMANDE_DEMO** : lien Calendly réel (`CALENDLY_URL`,
    Google Meet, gratuit) ajouté **déterministiquement** par le code à la fin du brouillon quand
    `classification == "DEMANDE_DEMO"` — jamais généré par le LLM, pour ne pas risquer une URL
    déformée. Absent = repli gracieux (brouillon inchangé, promesse vague comme avant). Vérifié en
    direct : le lien apparaît uniquement sur le cas `DEMANDE_DEMO` du mock, absent sur `DEVIS`.
13. ✅ **Fait — RGPD / PII** : [retention.py](aca/core/retention.py) purge les leads (onglet `Leads`),
    leurs threads `data/checkpoints.sqlite` correspondants (`checkpointer.delete_thread` — retire le
    corps brut de l'e-mail de l'état du graphe) et les entrées `data/queue.sqlite` validées, tous plus
    anciens que `RETENTION_DAYS` (défaut 365 jours). Ne touche jamais `Enrichissement_Cache`
    (données d'entreprise, pas personnelles) ni la `FAQ`. Vérifié en direct : ligne de test datée
    de l'an 2000 insérée dans le vrai onglet Leads → supprimée par `purge_old_leads(365)`, les 6
    vrais leads (tous de 2026) intacts. Audit des logs console confirmé : aucun ne journalise le
    corps brut de l'e-mail, seul l'extrait déjà destiné au CRM. Bon point déjà existant : seul le
    domaine (jamais le contenu de l'e-mail) part vers Tavily.

**P2 — Échelle / produit**

14. ✅ **Fait (par anticipation) — Migration Supabase/pgvector**, avancée avant que les
    déclencheurs du §11.1 ne soient atteints, à la demande explicite de l'utilisateur (voir §11.1
    pour le raisonnement complet). [vector_store.py](aca/integrations/vector_store.py) remplace le cache
    d'embeddings en mémoire de `sheets.py` par une table Postgres pgvector (`faq_embeddings`),
    partagée entre `ui.py` et `poller.py` ; `app.py` utilise `PostgresSaver` au lieu de
    `SqliteSaver` pour le checkpointer quand `DATABASE_URL` est configurée. Repli gracieux complet
    si absente (comportement identique à avant cette migration). **Vérifié en direct contre le
    vrai projet Supabase de l'utilisateur** : `vector_store.search()` renvoie exactement les mêmes
    résultats que l'ancien chemin en mémoire pour la même requête ; un checkpoint écrit par un
    process (`PostgresSaver`) est relu correctement depuis un process totalement séparé (le
    problème que cette migration visait à résoudre) ; suite complète `python -m aca.core.app` (5 cas) sans
    régression. Deux vrais bugs trouvés et corrigés pendant la vérification :
    (1) l'hôte de connexion directe de Supabase (`db.<ref>.supabase.co`) est IPv6 uniquement et ne
    résolvait pas sur ce réseau — corrigé en utilisant le **Session pooler** de Supabase
    (`postgres.<ref>@aws-0-<région>.pooler.supabase.com:5432`, compatible IPv4, toujours gratuit) ;
    (2) l'adaptateur psycopg de `pgvector` ne convertit automatiquement que `numpy.ndarray` ou sa
    propre classe `Vector`, pas une liste Python brute (ce que renvoie l'API d'embeddings Gemini) —
    une liste brute produisait un tableau Postgres `double precision[]` au lieu d'un `vector`, et
    l'opérateur `<=>` n'a pas de surcharge pour `vector <=> double precision[]` ; corrigé en
    enveloppant les vecteurs dans `pgvector.Vector(...)` avant de les lier aux requêtes.
15. ✅ **Fait — Vrai CRM (HubSpot)** : [hubspot.py](../aca/integrations/hubspot.py), appelé depuis
    `action_node` **en parallèle** de Sheets (pas en remplacement — Sheets reste la mémoire lue par
    `find_leads_by_sender` et le tableau de bord, décision actée pour ne pas porter la détection de
    doublons et les vues du dashboard dans le même changement). Upsert du Contact par e-mail, création
    du Deal (`HUBSPOT_PIPELINE`/`HUBSPOT_DEALSTAGE`), association v4, Note avec urgence/besoin/brouillon
    — API REST directe, sans SDK. Repli gracieux complet si `HUBSPOT_ACCESS_TOKEN` absent. Vérifié en
    direct contre le vrai portail (2026-07-12) : contact + deal + note créés puis supprimés ; un vrai
    bug attrapé pendant la vérification (un `print` Unicode plantait après l'écriture réussie, ce qui
    aurait fait rejouer tout `action_node` par le `RETRY_POLICY` → lead dupliqué — corrigé).
16. ✅ **Fait — Pièces jointes multiples (PDF + Word + Excel)** : `gmail_reader._extract_attachments`
    parcourt maintenant récursivement TOUTES les parties MIME et collecte chaque PDF/Word(.docx)/
    Excel(.xlsx), au lieu de s'arrêter au premier PDF trouvé. Nouveau module
    [attachment_reader.py](aca/ingestion/attachment_reader.py) : dispatch par extension (`python-docx` pour
    `.docx`, `openpyxl` pour `.xlsx`, réutilise `pdf_reader.extract_raw_text_from_pdf` pour `.pdf`),
    concatène chaque pièce jointe préfixée par son nom de fichier, puis tronque l'ENSEMBLE à
    `MAX_CHARS` (un seul budget token global par e-mail, pas un budget par fichier — sinon 5 pièces
    jointes exploseraient le contexte LLM). `pdf_reader.py` reste inchangé pour ses usages existants
    (ingestion Knowledge_Base, upload PDF unique) — refactorisé en interne pour exposer
    `extract_raw_text_from_pdf` (sans troncature) + la constante `MAX_CHARS`, réutilisées par
    `attachment_reader.py`. L'UI accepte désormais plusieurs fichiers (`accept_multiple_files=True`,
    types `pdf`/`docx`/`xlsx`) dans le formulaire manuel. Vérifié : script de test avec un PDF + un
    `.docx` + un `.xlsx` synthétiques → les trois textes extraits et concaténés correctement, une
    extension non supportée (`.png`) silencieusement ignorée ; suite `python -m aca.core.app` sans
    régression.
17. ✅ **Fait — Tableau de bord** (volume par catégorie, temps de réponse, conversion) : nouveau
    module [analytics_store.py](aca/storage/analytics_store.py) — un registre SQLite léger qui capture
    TOUTES les classifications (contrairement à l'onglet Sheets `Leads`, qui ne reçoit que les
    DEMANDE_DEMO/DEVIS validés, et à `audit_log.py`, qui ne trace que les validations). Trois points
    d'enregistrement : `poller.py` (source `poller`, dès que le graphe atteint la pause),
    `ui.py._sync_result()` (source `manuel`/`gmail_import`, à chaque resynchronisation d'état —
    idempotent, donc rejouable après une clarification résolue), et le clic « Valider » (ferme la
    mesure de temps de réponse). Nouvel onglet « Tableau de bord » dans `ui.py` (via `st.tabs`, pas
    de refonte multipage — l'app reste un script unique) : KPI (e-mails classés, taux de
    validation, temps de réponse médian), volume par catégorie (`st.bar_chart`), tendance
    quotidienne (`st.line_chart`), entonnoir classé→rédigé→validé, détail des temps de réponse en
    expander ; filtre de période (7/30/90 jours) via `st.segmented_control`. **Multi-boîtes /
    multi-tenant** reste à faire (hors scope d'un prototype solo).

### 11.5 Garantie 0 € + correspondance n8n

Toute la stack actuelle ET recommandée reste gratuite : Groq (free tier — la seule vraie limite
est ~30 req/min), Gemini embeddings (free tier), Tavily (1 000 req/mois), Google Sheets/Gmail API
(gratuits), Streamlit (open source ; Community Cloud gratuit), `SqliteSaver` (fichier local),
Supabase/Qdrant (free tiers sans carte bancaire), Calendly (plan gratuit).

⚠️ **Point de vigilance : n8n Cloud est PAYANT** — pour le port n8n, utiliser **n8n self-hosted**
(Docker, community edition gratuite).

Correspondance n8n : les items P0-1/2/3 et P1-7 sont des nœuds n8n **natifs** (Gmail trigger,
Slack, Wait) — le port n8n prévu résout donc naturellement l'intake, les notifications et les
relances, ce qui renforce le choix de cette cible.

### 11.6 Dette technique restante — le « cœur » à finir AVANT toute commercialisation

Audit du 2026-07-12 (roadmap vs. code réel). Ces items sont le préalable au §12 : tant qu'ils ne
sont pas traités, la phase commercialisation ne démarre pas. Classés par levier décroissant :

1. ✅ **Fait (2026-07-12) — Suite de tests automatisée (pytest)** : `tests/` — 84 tests, ~2 s,
   entièrement hors-ligne (le `conftest.py` vide toutes les clés d'API avant tout import `aca.*` et
   redirige les SQLite vers un répertoire temporaire — aucun test ne touche Supabase/Sheets/Gmail/
   Groq ni les vraies bases locales). Couvre : les nœuds du graphe en unitaire (LLM factices),
   les fonctions pures du RAG hybride (RRF, mots-clés, cosinus), les 4 registres SQLite, les
   contrats de dégradation gracieuse (notify/hubspot/enrichment/veille/pièces jointes), et 5 tests
   d'intégration du graphe compilé (pause avant `action`, reprise après « Valider », boucle de
   réflexion plafonnée, SPAM court-circuité, garde-fou veille). `python -m pytest tests/`.
   *Pourquoi c'était le levier n°1 :* chaque nouvel ajout obligeait à revérifier manuellement tout
   le reste ; ces vérifications sont maintenant figées et rejouables en 2 secondes.
2. 🟡 **Largement fait (2026-07-12) — Exercer les chemins « codés mais jamais joués en réel »** :
   ✅ `TAVILY_API_KEY` configurée et vérifiée en direct — enrichissement (profil réel obtenu pour
   doctolib.fr via Tavily, mis en cache dans `Enrichissement_Cache`, relu depuis le cache au 2e
   appel sans appel Tavily) et veille (réponse web réelle → paire Q/R formatée par Groq → ligne
   FAQ en staging « à valider », invisible du RAG, supprimée après vérification — aller-retour
   propre). ✅ Webhook Slack réel configuré (`#nouveau-canal`, workspace « acam ») et vérifié —
   `notify.py` a livré un vrai message, et `routing_node` a livré une vraie alerte SUPPORT
   (`SUPPORT_SLACK_WEBHOOK_URL`, même canal pour l'instant — à séparer plus tard). **Reste** :
   de vraies adresses `SUPPORT_EMAIL`/`HR_EMAIL` (elles conditionnent aussi la branche brouillon
   de transfert Gmail du routage), `relance.py` sur un vrai fil Gmail avec une vraie réponse, et
   le tableau de bord sur plusieurs jours réels de données.
3. ✅ **Fait (2026-07-12) — Retry sur les écritures SQLite hors graphe** :
   [sqlite_retry.py](../aca/storage/sqlite_retry.py) — décorateur `with_sqlite_retry` (3 tentatives,
   backoff linéaire, ne rejoue que `sqlite3.OperationalError`) appliqué à TOUTES les fonctions
   publiques des 4 registres locaux (`queue_store.py`, `analytics_store.py`, `audit_log.py`,
   `followup_store.py`), qui s'exécutent hors `app.invoke()` et n'étaient donc pas couverts par
   `RETRY_POLICY`. Vérifié par 5 tests (`tests/test_storage.py`) : succès après un verrou
   transitoire, échec propre après épuisement des tentatives sur un verrou persistant, et aucune
   tentative supplémentaire sur une exception qui n'est pas un conflit de verrou.
4. 🟡 **Partiel — Améliorations de robustesse LLM ouvertes au §10** :
   ✅ `with_structured_output()`/Pydantic pour l'extracteur (fait 2026-07-12, voir §10). **Reste** :
   few-shot prompting (classifier/extractor, encore zéro-shot), score de confiance de
   classification (relecture humaine sous un seuil), nœud `ingestion` explicite dans le graphe
   (l'extraction des pièces jointes vit encore dans `ui.py`/`poller.py`/`gmail_reader.py`).
5. **Cadence de relance multi-tours** — une seule relance par lead aujourd'hui (`relance.py`) ;
   ~80 % des ventes demandent 5+ contacts. Extension : plusieurs relances espacées, arrêt dès que
   le prospect répond.
6. **Rapport de stage + backlog Scrum / user stories** — ❌ volontairement en attente (démarre quand
   le projet est déclaré terminé). Matière première déjà accumulée dans `PROJECT_JOURNAL.md` ;
   les user stories se dériveront des items P0/P1/P2 de ce document.

## 12. P3 — Commercialisation / SaaS (à commencer UNIQUEMENT après le §11.6)

Issu d'un **second document de conseils externe** (généré par IA, sans accès au code), audité
item par item contre le code réel le 2026-07-12 avant d'être intégré ici. Verdict global : sur 9
suggestions, **1 était déjà entièrement construite, 2 partiellement, 6 sont réellement nouvelles**.
Les suggestions sont retranscrites fidèlement, avec leur statut vérifié et un ⚠️ « point de
vigilance » quand elles entrent en conflit avec la contrainte 0 € du projet ou dupliquent de
l'existant (même esprit que l'avertissement n8n Cloud du §11.5).

1. ✅ **Déjà fait — Checkpoints Human-in-the-Loop (HITL)** (« Approve / Reject / Edit avant toute
   action impactante ») : c'est le cœur du projet depuis le début — `interrupt_before=["action"]` +
   `interrupt()` dynamique de clarification, bouton « Valider » Streamlit, rien n'est jamais
   auto-envoyé. ⚠️ Ne pas reconstruire : la variante « webhook → frontend → retour n8n » décrite par
   le document est la *forme* que prendra ce mécanisme existant lors du port n8n (item 6 ci-dessous),
   pas une fonctionnalité nouvelle.
2. 🟡 **Partiel — Journal d'audit consultable & transparence** (« retrouver pour chaque exécution
   passée les sorties brutes, prompts et récupérations de chaque étape ») : les données existent déjà
   — `audit_log.py` (qui/quoi/quand par validation), traces LangSmith (détail par nœud),
   `reasoning_log` affiché dans l'UI. **Manque** : un onglet « Historique » dans l'UI qui consomme
   `audit_log.list_recent()` (déjà codé, jamais branché) et permette de rechercher les exécutions
   passées. ⚠️ Effort modeste : c'est surtout du câblage d'existant, pas une nouvelle infrastructure.
3. ❌ **Multi-tenant (isolation par client)** : Supabase Auth + table `organizations` + colonne
   `org_id` sur chaque donnée (Leads, FAQ, analytics, config) + **Row-Level Security** pour un
   cloisonnement au niveau base. Vérifié : aucun `org_id` nulle part, un seul `.env`, un seul mot de
   passe UI. C'est LA frontière prototype → produit vendable à plusieurs clients.
4. ❌ **Suivi de consommation & facturation** : capturer les métadonnées d'usage LLM (tokens
   entrée/sortie par exécution) dans `analytics_store.py` (vérifié : aucune trace de tokens
   aujourd'hui), les agréger par `org_id`, et brancher Stripe Billing. ⚠️ Conflit 0 € : Stripe et les
   modèles payants (Claude/GPT — les limites du free tier Groq ne tiendront pas un trafic commercial)
   n'ont de sens qu'en phase commerciale ; en attendant, la *première marche* gratuite est de logger
   les tokens Groq (déjà présents dans les réponses API) pour connaître le coût théorique par client.
5. 🟡 **Partiel — Trace d'observabilité & graphe d'état visuel** (« graphe LangGraph affiché, nœud
   actif surligné, dropdown "Thought Trace" par worker ») : le « Thought Trace » existe déjà
   (expander « Raisonnement de l'équipe d'agents » + progression nœud par nœud en direct via
   `app.stream()`/`st.status`). **Manque** : le rendu *visuel* du graphe avec surlignage du nœud
   actif. ⚠️ Valeur = confiance client en démo ; à faire dans le futur dashboard (item 8) plutôt
   qu'en Streamlit jetable.
6. ❌ **Stratégie n8n « Option A » (décision d'architecture actée)** : garder LangGraph/Python
   intact comme « cerveau », l'exposer en microservice via FastAPI ; n8n devient l'enveloppe
   d'infrastructure (trigger Gmail natif remplaçant `poller.py`, notifications, file d'attente
   visuelle, reprise après « Valider »). Vérifié : aucun FastAPI aujourd'hui. ⚠️ Cohérent avec la
   conception « n8n-ready » déjà actée (§11.5) et son avertissement : n8n **self-hosted** (gratuit),
   pas n8n Cloud (payant). L'alternative « tout réécrire en nœuds n8n » est explicitement rejetée
   (perdrait `attachment_reader.py`, le RAG hybride, les garde-fous déterministes...).
7. ❌ **Panneau de configuration dynamique** : un onglet « Réglages » où un manager (pas un
   développeur) édite son lien Calendly, ses adresses de routage SUPPORT/RH, ses webhooks, et sa
   base de connaissances via une grille de données — aujourd'hui tout est dans `.env` (vérifié) et
   la FAQ ne s'édite que par ingestion/staging. Pré-requis pratique du multi-tenant (item 3) : la
   config par client doit vivre en base (Supabase), plus dans un fichier local.
8. ❌ **Dashboard client dédié** (le document suggère Next.js/Shadcn/Tailwind : login client,
   timeline d'exécution, boutons HITL, réglages, facturation). ⚠️ Décision de phase commerciale,
   pas un manque du prototype : Streamlit reste l'UI assumée du stage ; le choix Next.js vs.
   Streamlit multi-pages durci se prendra au moment du port, pas avant.
9. ❌ **Observabilité d'infrastructure (Grafana/Prometheus sur les métriques Supabase)**. ⚠️ Utile
   uniquement sous vraie charge multi-clients ; LangSmith (gratuit, déjà branché) couvre le besoin
   d'observabilité au volume prototype. À reconsidérer quand l'item 3 existe et que plusieurs
   clients tournent.

**Ordre de dépendance suggéré** (si cette phase démarre un jour) : 3 (multi-tenant) → 7 (config par
client) → 4 (usage/billing) → 8 (dashboard) → 2/5 (audit + graphe visuel dans ce dashboard) →
6 (port n8n) → 9 (Grafana). Les items 2 et 5 peuvent aussi être prototypés avant, en Streamlit, à
faible coût.

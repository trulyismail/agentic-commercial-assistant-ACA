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

- ✅ **Fait (2026-07-12) — Few-shot prompting** : `classifier_node` a 3 exemples de cas limites
  (SUPPORT vs DEVIS, AUTRE vs partenariat, SPAM déguisé en urgence — précisément l'exemple cité
  ici) ; `extractor_node` a un calibrage explicite des 3 niveaux d'urgence + le format attendu de
  `besoin_principal`. Vérifié en direct : un e-mail piège (« question urgente » dans l'objet, mais
  aucun incident réel) classait d'abord l'urgence en "haute" à cause du seul mot "urgent" malgré la
  règle générale donnée au modèle — corrigé en remplaçant la règle abstraite par un exemple
  contrastif explicite (« Question urgente : compatible Salesforce ? → basse »), qui a résolu le
  cas piège sans casser les deux autres cas de calibrage testés. Le classifieur étant déjà à 100 %
  sur `eval_dataset.json` grâce à la sortie structurée (item précédent), ces exemples ajoutent une
  marge de robustesse hors de ce jeu d'évaluation plutôt que de corriger un problème mesuré —
  re-testé après coup : toujours 50/50, aucune régression.
- ✅ **Fait (2026-07-12) — `with_structured_output()` / Pydantic** au lieu de `json.loads()` manuel
  dans `extractor_node` : nouveau modèle `ExtractedInfo` (Pydantic), extraction forcée par
  tool-calling côté Groq — plus de JSON malformé à parser, plus de fallback `{"raw": ...}` fantôme
  (rien en aval ne le lisait). Repli gracieux si l'extraction structurée échoue malgré tout (réseau,
  sortie hors schéma) : `ExtractedInfo()` vide plutôt qu'un plantage de `app.invoke()` — traité
  ensuite comme un e-mail vague par `clarification_node`. Vérifié : 3 nouveaux tests unitaires +
  3 appels réels contre Groq (champs complets, champs manquants, repli sur schéma vide simulé).
- ✅ **Fait (2026-07-12) — Score de confiance de classification** : `classifier_node` renvoie
  maintenant un score (0-1, `ClassificationResult.confiance` via `with_structured_output()`) en plus
  de la catégorie. Sous `CLASSIFICATION_CONFIDENCE_THRESHOLD` (0.6), `notification_node` alerte un
  humain même pour SPAM/AUTRE/SUPPORT (qui court-circuitent normalement toute validation) — c'est le
  "router vers une relecture humaine systématique en dessous d'un seuil" visé ici, implémenté en
  réutilisant le canal Slack/e-mail déjà existant plutôt qu'en construisant un nouveau mécanisme de
  pause. Effet de bord positif mesuré : précision du classifieur passée de 96 % à **100 %** (50/50)
  sur `eval_dataset.json`, probablement parce que le tool-calling structuré contraint mieux le
  modèle qu'un simple mot en texte libre.
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
    [eval_classifier.py](aca/eval/eval_classifier.py) mesurent la précision réelle du classifieur —
    **résultat mesuré initialement : 96 % (48/50)**, DEMANDE_DEMO/DEVIS/SPAM à 100 %, AUTRE et
    SUPPORT à 90 % chacun (les 2 erreurs sur des cas ambigus délibérés — « compte suspendu par
    erreur » classé AUTRE au lieu de SUPPORT ; un message très vague classé SPAM au lieu d'AUTRE —
    cohérent avec le comportement attendu, pas un signal d'alarme). **Remesuré le 2026-07-12 après
    le passage à la sortie structurée (§10/§11.6 item 4) : 100 % (50/50)**, les deux cas ambigus
    ci-dessus désormais classés correctement — probablement parce que le tool-calling structuré
    contraint mieux le modèle qu'un simple mot en texte libre. À refaire périodiquement avec de
    vrais e-mails une fois disponibles, pour suivre la précision en conditions réelles.
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
4. ✅ **Soldé (2026-07-21) — Améliorations de robustesse LLM ouvertes au §10** :
   ✅ `with_structured_output()`/Pydantic pour l'extracteur ET le classifieur (le classifieur a
   gagné un score de confiance au passage) ; ✅ few-shot prompting sur les deux nœuds (fait
   2026-07-12, voir §10) ; ✅ nœud `ingestion` explicite dans le graphe — l'extraction des pièces
   jointes (`attachment_reader.extract_text_from_attachments`) vivait hors du graphe, dupliquée
   dans `ui.py`/`poller.py`, chacun devant l'appeler avant `app.invoke()` et passer le résultat déjà
   calculé ; `ingestion_node` (nouveau, tout début du graphe : `START → ingestion → classifier →
   ...`) centralise cette extraction une seule fois, à partir d'un nouveau champ d'état brut
   `attachments_raw`, et hérite gratuitement du `RETRY_POLICY` du graphe. Vérifié par 2 nouveaux
   tests unitaires + la suite d'intégration complète rejouée sans régression.
5. ✅ **Fait (2026-07-21) — Cadence de relance multi-tours** : `followup_store.py` remplace l'ancien
   flag booléen `followup_sent` (une seule relance, conservé en base pour compatibilité) par un
   compteur `followup_count`, plafonné à `RELANCE_MAX_ROUNDS` (défaut 3, réglable via le panneau
   « Réglages », §12 item 7). La cadence s'arrête d'elle-même dès que le prospect répond (le
   dernier message du fil n'est alors plus de nous) — aucune logique d'arrêt supplémentaire
   nécessaire, chaque relance envoyée devient à son tour le dernier message et repousse
   naturellement la suivante de `RELANCE_DAYS`. Ton légèrement différencié après la première
   relance. Migration idempotente de schéma (bases existantes non perdues), vérifiée par 3 tests
   dédiés + les tests de dégradation existants.
6. 🟡 **Largement fait — Rapport de stage + backlog Scrum / user stories** : la matière première a
   été transformée en un document de présentation structuré complet
   ([docs/ACA_presentation_source.md](ACA_presentation_source.md), même session que ce document,
   2026-07-21) — contexte projet, étude de l'existant, exigences fonctionnelles/techniques, valeur
   ajoutée, diagramme de cas d'utilisation UML (acteurs corrigés après relecture), et un backlog
   Scrum complet (8 epics, 40+ user stories, 4 sprints réels + backlog de commercialisation).
   **Reste** : le document de rapport de stage académique final à proprement dit (mise en forme
   institution/école, soutenance) — matière première désormais prête, mise en forme restante hors
   du périmètre d'un audit technique.

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
2. ✅ **Fait (2026-07-21, suite) — Journal d'audit consultable & transparence** (« retrouver pour
   chaque exécution passée les sorties brutes, prompts et récupérations de chaque étape ») : nouvel
   onglet « Historique » dans `ui.py`, consommant `audit_log.list_recent()` (déjà codé, jusqu'ici
   jamais branché) avec une recherche texte libre (expéditeur, classification, validé par, ID).
   Complète le « Thought Trace » déjà existant (`reasoning_log`) et les traces LangSmith détaillées.
3. 🟡 **Fondation faite (2026-07-21) — Multi-tenant (isolation par client)**. Ajouté : `org_id`
   (`aca.core.tenant.current_org_id()`, défaut `"default"`, un déploiement ACA = un tenant — pas de
   routage multi-org au sein d'un même process, aucun login/session ajouté) tague désormais chaque
   ligne des 4 registres SQLite locaux (`queue_store`, `analytics_store`, `audit_log`,
   `followup_store` — migration idempotente, historique préservé) ET de `faq_embeddings`
   (Supabase) ; chaque lecture est scopée dessus par défaut. **Row-Level Security** activée sur
   `faq_embeddings` (`ENABLE`+`FORCE ROW LEVEL SECURITY` + politique `tenant_isolation`) — ce
   projet ne passant jamais par PostgREST/une clé anon (uniquement `psycopg` via `DATABASE_URL`),
   la politique s'appuie sur une variable de session Postgres (`app.current_org_id`, positionnée
   via `set_config()` à chaque emprunt de connexion au pool) plutôt que sur `auth.uid()`. **Reste
   non fait** : `Supabase Auth`, table `organizations`, tout écran de login/inscription — c'est la
   fondation de données, pas un vrai système multi-tenant utilisateur. **Vérifié en direct contre un
   vrai Supabase le 2026-07-21** (suite) : la policy s'est révélée inopérante au premier passage
   (rôle `postgres` = `rolbypassrls=true` par défaut chez Supabase, rendant `FORCE` sans effet),
   corrigée via un rôle applicatif restreint `aca_app` — voir §14.3 et `docs/PROJECT_JOURNAL.md`
   pour le détail complet. La logique de scoping `org_id` reste aussi couverte par 5 tests unitaires
   (`tests/test_multitenant.py`).
4. ✅ **Fait (2026-07-21) — Suivi de consommation, facturation Stripe en scaffold**. Le suivi de
   tokens (`analytics_store.token_stats`) existait déjà et est maintenant scopé par `org_id` (item
   3 ci-dessus). Ajouté : [billing.py](../aca/integrations/billing.py) — `report_usage(org_id, days)`
   reporte la consommation comme un enregistrement d'usage Stripe (`action="set"`) **uniquement**
   si `STRIPE_API_KEY` est configurée ET qu'un `STRIPE_SUBSCRIPTION_ITEM_ID` est réglé pour ce
   tenant (via le panneau de réglages, item 7) — sinon renvoie juste les statistiques, sans jamais
   lever. ⚠️ Conflit 0 € assumé et documenté dans le code lui-même : ce module n'a de sens qu'en
   phase commerciale, et n'a **jamais été exercé contre un vrai compte Stripe** (aucun compte de
   test disponible pour ce projet) — seule sa dégradation gracieuse et la forme de son appel (via
   un faux client Stripe) sont couvertes par les tests (`tests/test_billing.py` +
   `tests/test_degradation.py`).
5. ✅ **Fait (2026-07-21, suite) — Trace d'observabilité & graphe d'état visuel** (« graphe LangGraph
   affiché, nœud actif surligné, dropdown "Thought Trace" par worker ») : le rendu visuel du graphe
   (`st.graphviz_chart`, viz.js côté navigateur — aucune dépendance système Graphviz) surligne
   désormais le nœud actif en direct pendant `app.stream()`, et une version statique du même graphe
   apparaît dans les expanders « Raisonnement »/« Détail du routage » pour les analyses déjà
   terminées. Prototypé directement en Streamlit plutôt que d'attendre le futur dashboard (item 8),
   comme anticipé dans l'ordre de dépendance ci-dessous.
6. ✅ **Fait (2026-07-21) — Stratégie n8n « Option A »**. [api.py](../aca/api.py) : microservice
   FastAPI exposant le graphe compilé (`POST /threads`, `GET /threads/{id}`,
   `POST /threads/{id}/clarifier`, `POST /threads/{id}/valider`) — même contrat human-in-the-loop
   que `ui.py`, `valider` restant le seul point d'entrée qui écrit dans le CRM. Cohérent avec la
   conception « n8n-ready » déjà actée (§11.5) : n8n resterait **self-hosted** (gratuit), pas n8n
   Cloud (payant) ; l'alternative « tout réécrire en nœuds n8n » reste explicitement rejetée.
   Couvert par 7 tests (`tests/test_api.py`, via `fastapi.testclient.TestClient` + les mêmes faux
   LLM que `test_graph_integration.py`). **Non exercé contre un vrai workflow n8n** — aucune
   instance n8n n'existe pour ce projet ; n8n ne ferait qu'appeler cette API en HTTP, il n'y a rien
   côté API elle-même qu'un n8n réel changerait à vérifier.
7. ✅ **Fait (2026-07-21) — Panneau de configuration dynamique**. Nouvel onglet « Réglages » dans
   `ui.py`, backé par [config_store.py](../aca/storage/config_store.py) (SQLite local, par tenant) :
   un manager édite le lien Calendly, les adresses/webhooks de routage SUPPORT/RH, et la cadence de
   relance (`RELANCE_DAYS`/`RELANCE_MAX_ROUNDS`) sans toucher `.env` — pris en compte dès la
   prochaine analyse (`app._calendly_url()`/`_routing_destinations()`) ou le prochain cycle
   planifié (`relance._relance_days()`/`followup_store.relance_max_rounds()`), aucun redémarrage
   requis. Un réglage jamais édité retombe sur `.env` (surcouche, pas un remplacement). **Non
   inclus** : édition de la base de connaissances via grille de données (l'ingestion/staging
   existants restent le chemin d'édition de la FAQ) — hors du périmètre étroit de cet item.
8. ✅ **Fait (2026-07-21, plus tard le même jour) — Dashboard client dédié**. Délibérément non
   construit plus tôt dans la session (voir ci-dessous) malgré la demande de « finir tout ce qui
   reste » — les questions de framework/hébergement/authentification/périmètre n'étaient pas des
   choix qu'un passage de code autonome devait trancher à la place d'une vraie décision produit.
   Construit ensuite le même jour une fois l'utilisateur explicitement revenu dessus et ces
   questions posées et tranchées avec lui (Next.js en local pour l'instant, pas de décision
   d'hébergement ; mot de passe partagé + clé API plutôt qu'une vraie authentification
   multi-utilisateur ; tourne à côté de Streamlit, ne le remplace pas). Voir
   [dashboard/README.md](../dashboard/README.md) pour le détail — login animé, vue d'ensemble
   (roster de l'équipe d'agents + file d'attente + historique), tiroir HITL (Valider/Rejeter/
   Éditer/répondre à une clarification), Réglages, Facturation/usage. Signature visuelle : un rendu
   SVG animé de la topologie réelle du `StateGraph` (nœud actif en direct, mêmes données que le
   graphe Streamlit — §12 item 5 — mais dans un langage visuel propre au dashboard). A nécessité
   d'étendre `aca/api.py` : garde optionnelle par clé API (`ACA_API_KEY`), `GET /threads/pending`,
   `GET /threads/history`, `POST /threads/{id}/rejeter` (fonctionnalité réellement nouvelle — aucun
   chemin de « rejet » n'existait avant, y compris dans `ui.py`), `GET /stats`, `GET`/`POST
   /settings` — et a révélé un vrai bug de routage FastAPI (routes statiques déclarées après la
   route dynamique `/threads/{thread_id}`, donc jamais atteintes), corrigé et couvert par les 17
   tests de `test_api.py`. Ancien texte de cadrage, conservé pour l'historique : « ⚠️ Décision de
   phase commerciale, pas un manque du prototype : Streamlit reste l'UI assumée du stage ; le choix
   Next.js vs. Streamlit multi-pages durci se prendra au moment du port, pas avant. »
9. ✅ **Fait (2026-07-21) — Observabilité (Prometheus)**. `GET /metrics` sur
   [api.py](../aca/api.py) (`prometheus-client`, gratuit, open source) :
   `aca_emails_classified_total{classification, org_id}`, `aca_leads_validated_total{org_id}`,
   `aca_tokens_per_analysis` (histogramme). ⚠️ Le point de vigilance d'origine reste vrai —
   « utile uniquement sous vraie charge multi-clients » — mais l'endpoint est maintenant prêt pour
   ce cas futur (scrape Prometheus/Grafana) sans imposer d'infrastructure tant que rien ne le
   scrape ; LangSmith couvre toujours le besoin d'observabilité au volume prototype actuel.

**Ordre de dépendance suggéré** (si cette phase démarre un jour) : ~~3 (multi-tenant)~~ (fondation
faite, RLS vérifiée en direct) → ~~7 (config par client)~~ (fait) → ~~4 (usage/billing)~~ (scaffold
fait) → ~~2 (onglet Historique)~~ (fait) → ~~5 (graphe visuel)~~ (fait, prototypé en Streamlit) →
~~8 (dashboard)~~ (fait) → ~~6 (port n8n)~~ (fait) → ~~9 (Grafana)~~ (endpoint prêt). **Les 9 items
de cette section sont maintenant tous faits.**

### 12bis. Positionnement des surfaces après le dashboard (décidé 2026-07-22)

Une fois le dashboard construit, trois surfaces de contrôle coexistaient (Streamlit, dashboard
Next.js, port n8n) et se chevauchaient. Décision de cadrage, prise avec l'utilisateur, pour éviter
le « impressionnant mais incohérent » :

- **Le dashboard Next.js est la colonne vertébrale UI à long terme** (le « cockpit » client :
  file d'attente, graphe d'agents animé, HITL, réglages, usage). Streamlit (`ui.py`) est
  reclassé en **outil d'administration/curation interne** (ingestion de connaissances, validation
  FAQ, config back-office avancée). Les deux tournent côte à côte aujourd'hui ; le jour où Streamlit
  est retiré, ses pièces *client* migrent dans les vues principales du dashboard et ses pièces
  *curation* dans un futur groupe `(admin)` protégé par rôle — l'intérêt de migrer, c'est cette
  **séparation** par audience, pas le simple changement de framework.
- **La boucle d'approbation Slack (Valider/Rejeter directement dans Slack)** a été ajoutée comme la
  vraie « commodité pour l'entreprise » : un commercial valide un lead depuis le Slack qu'il a déjà
  ouvert, sans se connecter à aucune UI. Réutilise `aca/api.py` (`POST /slack/interactions`, signé
  HMAC, cf. `slack_verify.py`) et `notify.send_approval` (boutons Block Kit). Testée hors ligne
  (requêtes réellement signées), **non vérifiée contre une vraie app Slack** (nécessite une app
  Slack avec Interactivité activée + une URL publique/tunnel — étape de config manuelle de
  l'utilisateur, comme l'OAuth Gmail ou le webhook Slack déjà en place).
- **n8n est repositionné en couche d'intégration OPTIONNELLE, pas une 3e UI** : « si une entreprise
  fait déjà tourner n8n, elle peut piloter ACA depuis n8n via `aca/api.py` ». Il cesse ainsi de
  concurrencer le dashboard pour le rôle « vue du workflow ». Reste, comme toujours dans ce projet,
  **la dernière chose à réellement câbler** (le port API existe déjà et suffit ; aucune instance n8n
  n'est montée), même s'il restera une fonctionnalité principale offerte à terme.

**Mise à jour (2026-07-24) — le dashboard est PARKÉ ; Streamlit est la colonne vertébrale
opérationnelle.** Un inventaire des trois surfaces contre le code réel a tranché la question « le
dashboard est-il vraiment nécessaire ? » : Streamlit (`ui.py`) est la console opérateur *complète*
(intake formulaire/Gmail + lancement de l'analyse, HITL valider/éditer/**rejeter**, clarification,
**ingestion de connaissances**, **validation FAQ de la veille**, KPIs, historique, réglages) ; le
dashboard Next.js n'est qu'un *sous-ensemble en lecture seule* — **il n'a ni l'intake, ni
l'ingestion, ni la curation FAQ** (confirmé absent du code : aucun `POST /threads`, aucun uploader,
aucun endpoint FAQ), donc il **ne peut pas tourner seul** (il dépend du poller/Streamlit pour être
alimenté) et n'est pas déployé. Décision de l'utilisateur : **garder le dashboard dans le dépôt
comme vitrine construite, mais faire de Streamlit la surface opérationnelle unique** pour la
démo/soutenance ; aucun investissement dashboard supplémentaire pour l'instant. La direction
« cockpit client » du dashboard devient une piste future **différée**, pas active ; Slack
(Valider/Rejeter) couvre déjà la commodité d'approbation ; n8n reste une plomberie future
orthogonale (voir ci-dessus). Concrètement, la reformulation « dashboard = colonne vertébrale UI à
long terme » du bloc ci-dessus est remplacée par « Streamlit = colonne vertébrale opérationnelle
aujourd'hui ; dashboard = vitrine parquée ; cockpit client = piste future différée ».

**Deux incohérences de surface relevées pendant l'inventaire (documentées, différées) :**
- *Topologie du graphe recopiée à la main en 3 endroits* — `aca/core/app.py` (le vrai graphe),
  `GRAPH_EDGES` de `ui.py`, et `dashboard/lib/graph-topology.ts` : risque de dérive connu (changer
  le graphe oblige à re-synchroniser 2 copies UI ou elles divergent en silence). **Risque réduit par
  le parking du dashboard** (sa copie est désormais gelée) ; s'il est repris, mono-sourcer la liste
  d'arêtes `app.py ↔ ui.py` en un seul module importé par les deux. Différé.
- *Trois secrets partagés d'authentification* — `ACA_UI_PASSWORD` (Streamlit), `DASHBOARD_PASSWORD`
  (dashboard), `ACA_API_KEY` (API) : trois secrets à gérer pour un même opérateur. À consolider dans
  la phase identité/sécurité déjà planifiée en **§15.1.6** (vraie identité par utilisateur). Différé.
- *Corrigé le 2026-07-24* — Streamlit n'avait **pas de bouton « Rejeter »** (le rejet explicite
  n'existait que côté API/Slack/dashboard) : un lead ne pouvait qu'être validé ou abandonné
  silencieusement, jamais rejeté de façon traçable. Ajouté à `ui.py`, miroir de `_do_reject`
  (`aca/api.py`) — retire le lead de la file (`queue_store.mark_rejected`), **aucune écriture CRM**,
  le graphe n'est pas repris. Couvert par un test (`test_mark_ready_then_rejected`).

## 13. Audit d'un 3e document externe — blueprints « Bid Governance » (2026-07-16)

Issu de deux PDF générés par IA reçus le 2026-07-16 ("ACAM v2 Complete Engineering Blueprint" et
une version condensée "Unified Master Blueprint" — même contenu, la seconde résume la première)
proposant de repositionner ACAM en moteur de gouvernance d'appels d'offres pour Teamwill (conseil
bancaire) : score de probabilité de gagner un appel d'offre, disponibilité du personnel, marge de
rentabilité, seuil de confiance RAG fixe à 0.85, schéma pgvector `vector(1536)`. Même méthode que
le §12 : chaque idée auditée contre le code réel, pas ajoutée telle quelle. **Décision de cadrage
de l'utilisateur avant l'audit** : abandonner le cadrage Teamwill/appel d'offres — l'objectif du
projet est une solution générique pour plusieurs entreprises clientes, pas un pivot sectoriel.

**Déjà construit, souvent en mieux que décrit dans les PDF (ne pas reconstruire) :**
- *Intake événementiel / « retirer l'humain de l'initiation »* : `poller.py` fait déjà tourner
  chaque e-mail non lu jusqu'à la pause de validation automatiquement ; la variante webhook n8n est
  le port déjà prévu (§12 item 6). ⚠️ La rhétorique des PDF (« supprimer la phase HITL
  d'initiation ») ne doit surtout pas éroder la contrainte fondatrice « rédige et attend » — la
  pause de validation humaine reste non négociable.
- *Pré-traitement déterministe (JSON structuré avant tout raisonnement)* : `extractor_node` +
  `with_structured_output(ExtractedInfo)` (§10, fait).
- *Interface à deux vues (technique/exécutive)* : en grande partie déjà là — `app.stream()` +
  `st.status` en direct (trace technique) et l'écran de validation (vue exécutive). Les animations
  LottieFiles des PDF sont cosmétiques, non retenues.
- *Optimisation dynamique du contexte* : la troncature globale (`MAX_CHARS`) + l'ingestion
  granulaire en paires Q/R bornent déjà l'usage de tokens à cette échelle.

**Deux erreurs factuelles dans les PDF, à ne surtout pas copier :**
- *« Anti-Hallucination Gate » à seuil cosinus fixe 0.85* : la mesure empirique déjà faite sur ce
  projet (§"Known gaps" de CLAUDE.md, calibrage du 2026-07-11) montre que de vraies reformulations
  pertinentes obtiennent **0.73–0.80** avec l'embedding réellement utilisé (Gemini) — un seuil à
  0.85 bloquerait la quasi-totalité des bonnes réponses. Le design existant (double seuil zone
  ambre 0.72/0.62 + fusion RRF dense/sparse + repli veille + reflection + porte humaine finale) est
  strictement supérieur. Les seuils de similarité ne se recopient pas d'un modèle d'embedding à
  l'autre — ils se mesurent. Conservé tel quel ; seule l'idée du drapeau de lacune explicite est
  retenue (item 2 ci-dessous).
- *Schéma Supabase pgvector `vector(1536)`, OpenAI `text-embedding-ada-002`, index HNSW* : la
  stack réelle utilise `gemini-embedding-001` (3072 dimensions, gratuit — `ada-002` est payant,
  violerait la contrainte 0 €). L'index HNSW avait déjà été sciemment repoussé (scan séquentiel
  exact et sub-milliseconde au volume actuel de la FAQ).

**Idées jugées bonnes mais non réalisables avec les données actuelles — parquées :**
- *« Golden Diff » Win Predictor* (score de similarité contre un historique de propositions
  gagnantes) : nécessite un corpus par client de propositions passées qui n'existe pas. → P3 (§12),
  reformulé génériquement (« historique de propositions par client »), à ne construire que quand
  cette donnée existera réellement.
- *Bench Readiness + Profitability Margin Index* : nécessitent des données de disponibilité
  consultant + coûts/revenus qui n'existent pas ; la formule elle-même est une simple arithmétique,
  la vraie difficulté est la donnée. → P3 (§12), après le multi-tenant (item 3).
- *SME Matchmaker complet* (base de compétences + cartes interactives Teams/Slack) : nécessite une
  base de compétences + un backend de bot qui n'existent pas — candidat naturel pour le port n8n
  (§12 item 6). La version légère (question sans réponse poussée dans l'alerte Slack existante) est
  retenue (item 2 ci-dessous).
- *Fan-out parallèle des workers* (enrichissement/connaissance en parallèle plutôt que
  séquentiellement) : LangGraph le permettrait, mais la limite Groq (~30 req/min free tier) et la
  lisibilité du `reasoning_log` séquentiel rendent le gain marginal. Piste optionnelle future, pas
  retenue maintenant.

**Idées jugées bonnes ET réalisables — implémentées le 2026-07-16 :**

1. ✅ **Fait — Scanner de risques contractuels déterministe** (inspiré du « Trapdoor Risk Engine »
   des PDF) : nouveau module [risk_scan.py](../aca/core/risk_scan.py) (expressions régulières
   bilingues FR/EN, insensibles aux accents/majuscules — responsabilité illimitée, pénalités de
   retard, clause de non-concurrence, garantie bancaire, astreinte, résiliation
   unilatérale/immédiate, exclusivité contractuelle) + nouveau `risk_scan_node` dans
   [app.py](../aca/core/app.py) (placé `memory_lookup → risk_scan → extractor`, sans `RetryPolicy`
   — aucun appel externe à réessayer). `risk_flags` prévient explicitement `stratege_node` (ne
   s'engage sur aucune clause détectée, renvoie vers le juridique) et `notification_node` (en tête
   de l'alerte Slack/e-mail). Vérifié en direct (`python -m aca.core.app`, 6e e-mail de démo
   ajouté) : « responsabilité illimitée » + « pénalités de retard » détectées, et le brouillon final
   a correctement refusé tout engagement dessus.
2. ✅ **Fait — Lacune de connaissance signalée explicitement (`knowledge_gap`)** : avant ce
   changement, quand `connaissance` ET `veille` ne trouvaient rien, `stratege_node` rédigeait quand
   même sans aucun contexte factuel, en silence. Version réalisable du « [UNANSWERED GAP] » des
   PDF (sans le blocage strict à 0.85, voir plus haut) : `veille_node` pose `knowledge_gap=True`
   dans ce cas précis ; le Stratège est prévenu de rester honnête (jamais de prix/délai/
   fonctionnalité inventés) et `notification_node` pousse la question sans réponse dans l'alerte —
   version légère du SME Matchmaker, réutilisant le canal d'alerte existant plutôt qu'un nouveau
   webhook dédié.
3. ✅ **Fait — Brouillon éditable avant validation, capture (original, édité)** : le brouillon du
   Stratège est maintenant modifiable dans un `st.text_area` avant de cliquer « Valider » (au lieu
   d'un affichage lecture seule) ; c'est la version corrigée qui part vers Sheets/HubSpot/le
   brouillon Gmail. Chaque édition réelle est journalisée
   (`analytics_store.record_edit`/`edit_rate`, nouvelle table `draft_edits`). Version réalisable du
   « Continuous Training Loop » des PDF : **pas de réentraînement automatique** (stack 100 %
   gratuite, aucun fine-tuning) — un corpus brut pour enrichir manuellement, plus tard, le
   few-shot prompting (§10) ou `eval_dataset.json`.
4. ✅ **Fait — Suivi de consommation de tokens (« Quota Usage Tracker »)** : `sum_usage()` dans
   `app.py` + `UsageMetadataCallbackHandler` (langchain_core, standard) branché dans
   `ui.py`/`poller.py` sur chaque exécution du graphe ; journalisé via
   `analytics_store.record_tokens`/`token_stats` (nouvelle table `token_usage`) et affiché comme
   KPI du tableau de bord. C'est la première marche déjà identifiée en §12 item 4, avant même la
   lecture de ces PDF — purement informatif tant que Groq reste gratuit.

**Vérification** : 23 nouveaux tests unitaires (détection de chaque motif de risque,
insensibilité accents/majuscules, absence de faux positif sur texte propre, `knowledge_gap` posé
seulement quand connaissance ET veille échouent, injection correcte des deux avertissements dans le
prompt du Stratège, présence des deux signaux dans le message d'alerte, agrégation multi-modèle de
`sum_usage`, taux d'édition et statistiques de tokens sur bases temporaires) — suite complète
repassée sans régression : **125 tests, ~5 s**. Vérifié en direct contre la vraie API Groq (6e
e-mail de démonstration dans `python -m aca.core.app`, voir `docs/PROJECT_JOURNAL.md` entrée
2026-07-16 pour le détail complet).

## 14. Audit d'une checklist générique « 5 erreurs de sécurité des projets IA » (2026-07-21)

Source : une série de slides génériques (non spécifiques à ACA) listant des erreurs de sécurité
fréquentes sur les projets « vibe-codés » avec de l'IA — mistake n°01 (clés API exposées), n°02
(pas de rate limiting), n°03 (Supabase grand ouvert / RLS désactivée), n°05 (pas de politique de
confidentialité). *Note : la slide n°04 n'a pas été fournie par l'utilisateur — non auditée ici.*
Même méthode que les §12/§13 : chaque point vérifié contre le code réel avant d'être ajouté au
backlog, pas recopié tel quel.

1. ✅ **Non applicable — clés API exposées côté client.** Vérifié par grep sur tout le
   dépôt : `GROQ_API_KEY`/`GOOGLE_API_KEY`/`TAVILY_API_KEY`/`HUBSPOT_ACCESS_TOKEN`/
   `SLACK_WEBHOOK_URL`/`DATABASE_URL` sont lus exclusivement via `os.getenv()` depuis `.env`
   (gitignoré), jamais codés en dur, jamais affichés dans l'UI (`ui.py` ne fait aucun `st.write`/
   `st.text`/`print` sur une valeur contenant *key*/*token*/*secret*). Le point de la slide («AI
   tools hardcode your secrets into frontend code, one inspect element and you're done») suppose un
   bundle JS livré au navigateur avec des secrets embarqués — **Streamlit n'a pas cette surface** :
   c'est un serveur Python qui rend du HTML, il n'y a pas de « code frontend » exposant quoi que ce
   soit d'inspectable. Rien à corriger ; à surveiller uniquement si une future UI (item 8 du §12,
   dashboard Next.js) introduit un vrai frontend — à ce moment-là, revérifier qu'aucune clé ne migre
   côté client.
2. ❌ **Réel — absence de rate limiting sur le gate mot de passe UI.** `ui.py:19-36`
   (`_check_auth`) compare `pwd == required` sans aucun compteur de tentatives, verrou temporaire,
   ni délai — un bot peut soumettre `ACA_UI_PASSWORD` en boucle aussi vite que Streamlit rejoue le
   script (`st.rerun()`), sans throttle process ni stockage de tentatives échouées. Le
   commentaire du code assume déjà « usage solo/petite équipe, pas un vrai système
   multi-utilisateurs » — donc le risque réel est faible en usage interne actuel, mais reste un vrai
   trou si l'UI est un jour exposée publiquement (ex. démo commerciale). Ajouté au backlog
   ci-dessous (item nouveau, faible effort : compteur + verrou progressif en session_state, pas de
   dépendance externe).
3. 🟡 **Partiellement applicable — pas de RLS sur la table pgvector Supabase.**
   [vector_store.py](../aca/integrations/vector_store.py) crée `faq_embeddings` (`CREATE TABLE IF
   NOT EXISTS ...`) sans jamais activer `ROW LEVEL SECURITY` — confirmé par grep, aucune occurrence
   de RLS/`ENABLE ROW LEVEL SECURITY` dans tout le projet. **Nuance par rapport à la slide** : le
   scénario qu'elle décrit (« every user can read every other user's data ») suppose une exposition
   via l'API REST PostgREST de Supabase avec une clé publique *anon* — recherché explicitement
   (`SUPABASE_ANON_KEY`/PostgREST/`supabase-js`) : **aucune occurrence**. Ce projet n'accède à
   Postgres que via `psycopg`/`DATABASE_URL` (chaîne de connexion directe, côté serveur uniquement,
   jamais envoyée à un navigateur) — la surface d'attaque « anon key volée dans le JS » n'existe
   donc pas aujourd'hui. Cela dit, l'absence de RLS reste une vraie dette : (a) c'est déjà un
   prérequis identifié du multi-tenant (§12 item 3 — « aucun `org_id` nulle part » — RLS est
   littéralement la frontière prototype → produit vendable à plusieurs clients), et (b) c'est une
   défense en profondeur peu coûteuse si `DATABASE_URL` fuitait un jour. Statut : pas une faille
   activement exploitable dans l'architecture actuelle (mono-tenant, pas de PostgREST exposé), mais
   à corriger avant tout accès multi-utilisateur/multi-client à Supabase — regroupé avec le
   multi-tenant plutôt que traité isolément (RLS sans `org_id` n'aurait rien à cloisonner).
4. ❌ **Réel — absence de politique de confidentialité.** `retention.py` (purge RGPD à
   `RETENTION_DAYS`) est un **mécanisme technique**, pas une politique publiée : rien dans le dépôt
   n'informe un prospect de ce qui est collecté (e-mail, pièces jointes, historique), pourquoi, pour
   combien de temps, ni de ses droits (accès/rectification/effacement/portabilité RGPD), ni qui est
   le responsable de traitement. Une entreprise qui collecte des e-mails de prospects sans document
   de ce type est en infraction RGPD (amendes jusqu'à 20M€ ou 4% du CA mondial) — confirmé par grep,
   aucun fichier « politique de confidentialité »/`privacy policy` n'existe. Ajouté au backlog.

**Backlog consolidé de cette section :**

| # | Tâche | Statut | Priorité | Effort |
|---|---|---|---|---|
| 14.1 | Clés API jamais exposées côté client | ✅ Déjà acquis (architecture serveur, rien à faire) | — | — |
| 14.2 | Rate limiting / verrou progressif sur `_check_auth()` (ui.py) | ✅ **Fait (2026-07-21)** — [auth_lockout.py](../aca/core/auth_lockout.py) (backoff exponentiel, 5 tentatives puis verrou 30s→15min), 7 tests | — | — |
| 14.3 | RLS sur `faq_embeddings` (et toute table future) | ✅ **Fait ET vérifié en direct (2026-07-21)**, avec le multi-tenant `org_id` (§12 item 3) — `ENABLE`+`FORCE ROW LEVEL SECURITY` + politique par variable de session Postgres (pas de PostgREST/anon key ici). La vérification en direct a trouvé la protection réellement inopérante (rôle `postgres` = `rolbypassrls=true` par défaut chez Supabase, ce qui rend `FORCE` sans effet) ; corrigé via un rôle applicatif restreint `aca_app`, re-vérifié en direct (bogus tenant/session non positionnée → 0 ligne, tenant réel → 74). Détail complet : `docs/PROJECT_JOURNAL.md` (entrée 2026-07-21, suite) | — | — |
| 14.4 | Rédiger et publier une politique de confidentialité (RGPD) | ✅ **Fait (2026-07-21)** — [docs/PRIVACY_POLICY.md](PRIVACY_POLICY.md), lié depuis un expander de `ui.py`. Champs raison sociale/contact DPO marqués `[À COMPLÉTER]` (décision propre à l'entreprise utilisatrice, pas devinable par le code) | — | — |

**Ce qui n'a volontairement pas été ajouté** : rien d'autre de cette checklist ne s'applique sans
modification — voir le détail item par item ci-dessus pour la justification de chaque « non
applicable ». Point de vigilance méthodologique (même qu'aux §12/§13) : une checklist générique
écrite sans accès au code réel peut décrire des scénarios d'attaque qui ne correspondent pas à
l'architecture effective (ici : Streamlit n'est pas un frontend JS, et Postgres n'est jamais
exposé via PostgREST/anon key) — l'auditer contre le code évite d'ajouter une tâche qui ne
protégerait contre rien, ou d'en manquer une vraie.

---

## 15. Checklist « production-ready » complète — durcissement final avant déploiement commercial (demandée 2026-07-23)

Source : une checklist de mise en production fournie par l'utilisateur (sécurité, tests, résilience,
conformité). **Décision de séquencement explicite de l'utilisateur : cette phase est la DERNIÈRE.**
Elle ne démarre qu'**après** la dette technique cœur (§11.6) *et* les items de commercialisation
retenus (§12). Rien ici n'est implémenté à ce stade — c'est le plan.

⚠️ **Statuts = auto-audit de premier passage (2026-07-23), à re-vérifier contre le code au moment de
l'implémentation** (comme demandé : « you can check all these after you finish what's in the plan
first »). Légende : ✅ acquis · 🟡 partiel (base présente, à compléter/durcir) · ❌ à faire.
Beaucoup de ✅ ci-dessous ne sont pas de nouvelles tâches — ils recensent ce qui existe déjà, pour
que la checklist soit honnête et qu'on ne « re-fasse » pas l'existant.

### 15.1 Accès, authentification & injection

| # | Item | Statut | Note (état réel / reste à faire) |
|---|---|---|---|
| 15.1.1 | Clés API & secrets côté serveur uniquement | ✅ | `os.getenv()` partout, aucun secret dans un bundle client (cf. §14.1) |
| 15.1.2 | `.env` jamais commité | ✅ | gitignoré, vérifié §14 ; idem `credentials/` |
| 15.1.3 | Rate limiting & prévention d'abus | ✅ | `ACA_RATE_LIMIT` (middleware fenêtre glissante, 429+Retry-After, 2026-07-22) + verrou progressif du gate UI (US-41) |
| 15.1.4 | Validation & sanitisation des entrées, prévention d'injection | ✅ **Fait (2026-07-26)** | Sortie structurée Pydantic ; échappement anti-formule Sheets (2026-07-22). **Ajouté** : bornes strictes sur tous les payloads de `aca/api.py` (`Field(min_length/max_length)`, `thread_id` restreint par motif — un corps de 200 ko+ est refusé en 422 *avant* d'atteindre le LLM), liste blanche des clés sur `POST /settings` (c'était un magasin clé/valeur ouvert), et [prompt_guard.py](../aca/core/prompt_guard.py) : détection déterministe bilingue des injections de prompt, câblée dans `risk_scan_node` → `injection_flags` → prompt du Stratège + alerte + bandeau UI. **Signale, ne bloque pas** : le gate humain reste la protection, ce drapeau le rend éclairé (une consigne cachée page 14 d'un cahier des charges ressortait auparavant comme une phrase plausible de plus) |
| 15.1.5 | Authentification sur les routes protégées | ✅ **Fait (2026-07-26)** | `ACA_API_KEY` sur toutes les routes API (sauf `/metrics`, qui a désormais sa propre garde `ACA_METRICS_TOKEN`, et `/slack`, couvert par HMAC). **Ajouté** : comparaison à temps constant (`hmac.compare_digest` — un `!=` fuyait le préfixe correct par chronométrage) et surtout `ACA_ENV=production`, qui rend la garde **obligatoire** : clé absente ⇒ 503 et refus de démarrage ([prod_check.py](../aca/core/prod_check.py)), au lieu de « absente = ouverte » |
| 15.1.6 | Autorisation, rôles & permissions | ✅ **Fait (2026-07-26)** | [user_store.py](../aca/storage/user_store.py) : comptes nominatifs, mots de passe PBKDF2-HMAC-SHA256 (sel + coût par enregistrement, jamais en clair), rôles `admin`/`operator` et permissions déclaratives (`ROLE_PERMISSIONS`, fail-closed sur rôle inconnu). Appliqué dans `ui.py` : réglages, curation de la base de connaissances et gestion des comptes réservés à `admin`. Effet collatéral important : « Validé par » du journal d'audit vient désormais de la session authentifiée, plus d'un champ libre auto-déclaré. Dégradation gracieuse conservée : aucun compte créé ⇒ ancien gate `ACA_UI_PASSWORD` |
| 15.1.7 | Gestion de session & expiration des tokens | ✅ **Fait (2026-07-26)** | [session.py](../aca/core/session.py) : TTL absolu (`ACA_SESSION_TTL_SECONDS`, 8 h) **et** délai d'inactivité (`ACA_SESSION_IDLE_SECONDS`, 30 min), la borne la plus stricte l'emportant ; l'activité repousse l'inactivité mais **jamais** le TTL absolu (sinon une session volée maintenue active ne meurt jamais). Côté dashboard, le jeton portait jusqu'ici une valeur constante, donc valable à vie — il embarque désormais son expiration **signée** (le `maxAge` du cookie ne prouvait rien, il n'est appliqué que par le navigateur). Invalidation globale = rotation de `DASHBOARD_SESSION_SECRET` |
| 15.1.8 | Gestion des secrets (coffre, rotation) | ✅ **Fait (2026-07-26)**, hors coffre lui-même | Règles, tableau de rotation par secret et procédure documentés dans [DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md) §3. Le coffre (Vault/Doppler/Secrets Manager) reste une décision d'hébergement, mais **ne demandera aucun changement de code** : tous les modules lisent `os.getenv()` dynamiquement, un agent injectant les variables suffit. Piège documenté : `ACA_AUDIT_HMAC_KEY` est le seul secret dont la rotation casse une vérification (§15.2.7) |
| 15.1.9 | HTTPS / TLS & rotation de certificats | ✅ **Procédure prête (2026-07-26)**, à appliquer au déploiement | [DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md) §2 : configurations Caddy (renouvellement automatique) et Nginx complètes, en-têtes HSTS/nosniff/DENY, écoute sur la boucle locale uniquement, et le piège WebSocket de Streamlit derrière proxy (UI figée sur « Connecting… » sans `Upgrade`/`Connection`). Conséquence applicative documentée : sans `--proxy-headers`, tous les clients partagent un seul quota de débit. Rien n'étant hébergé, l'application effective reste au jour J |

### 15.2 Données, multi-tenant & conformité

| # | Item | Statut | Note |
|---|---|---|---|
| 15.2.1 | RLS sur *chaque* table | 🟡 | `faq_embeddings` : RLS Postgres vérifiée en direct (§14.3, revérifiée 2026-07-26 via `scripts/verify_rls.py`). Tables checkpoint LangGraph : politique permissive volontaire (isolation par `thread_id` interne). **Six** stores SQLite locaux (le nouveau `user_store.py` compris, lui aussi cloisonné par `org_id` et couvert par un test d'isolation) : cloisonnement `org_id` *applicatif*, pas au niveau base. **Reste** : décider si les stores locaux passent à un backend supportant la RLS en multi-client réel — arbitrage produit, pas dette technique |
| 15.2.2 | Aucune table laissée totalement publique | ✅ **Vérifié en direct (2026-07-26)** | [scripts/verify_rls.py](../scripts/verify_rls.py) — balayage rejouable de tout le schéma `public`. Résultat réel : **5 tables, 0 sans politique** (`faq_embeddings` RLS activée + forcée ; les 4 tables LangGraph avec leur politique permissive assumée). Le script contrôle **aussi le rôle de connexion** (`aca_app`, ni `SUPERUSER` ni `BYPASSRLS`) — sans ce contrôle, un rapport « tout est vert » serait trompeur, exactement le piège du 2026-07-21. Nuance apprise en écrivant le script : `FORCE` ne lie que le *propriétaire* de la table, donc son absence sur les tables LangGraph n'est pas un défaut — les signaler à chaque exécution aurait appris à ignorer le rapport |
| 15.2.3 | Multi-tenant & isolation des données | 🟡 | Fondation `org_id` (5 stores + pgvector) posée (§12 item 3). **Reste** : onboarding/provisioning tenant, aujourd'hui « 1 déploiement = 1 tenant » |
| 15.2.4 | Gestion des données personnelles (PII) | ✅ **Fait (2026-07-26)** | Purge `retention.py` + politique de confidentialité, **plus le droit à l'effacement (art. 17)** qui manquait : `retention.purge_subject(sender)` / `python -m aca.core.retention --oublier <adresse>` efface Leads + checkpoints LangGraph (corps brut de l'e-mail) + file d'attente + suivi de relance, et renvoie le décompte par emplacement pour pouvoir répondre précisément à la personne. C'était le vrai manque : seul l'effacement *par ancienneté* était automatisé — la partie facile —, alors qu'une demande explicite imposait de retrouver à la main des lignes dans un Sheet, des threads dans un fichier de checkpoints et deux registres SQLite, donc en pratique ne se faisait pas. Effet de bord voulu : la personne n'est plus relancée par `relance.py`. Le journal d'audit est **volontairement** conservé (intérêt légitime art. 17.3(e), et le supprimer romprait la chaîne §15.2.7 — décision documentée, pas un oubli). **Reste** : chiffrement au niveau champ si un client l'exige |
| 15.2.5 | Politique de rétention & suppression | ✅ | `retention.py` (RETENTION_DAYS, purge Leads+checkpoints+queue) + [PRIVACY_POLICY.md](PRIVACY_POLICY.md) (§14.4) |
| 15.2.6 | Conformité réglementaire (RGPD) | 🟡 | Forme RGPD : rétention, politique, audit. **Reste** : champs `[À COMPLÉTER]` (responsable de traitement), DPA client, DPIA formelle si volume réel |
| 15.2.7 | Journaux d'audit & logs infalsifiables (tamper-evident) | ✅ **Fait (2026-07-26)** | Chaînage par hachage dans [audit_log.py](../aca/storage/audit_log.py) : chaque ligne intègre l'empreinte de la précédente (par tenant), `verify_chain()` recalcule et **localise** la première rupture ; `python -m aca.storage.audit_log` en fait un contrôle planifiable. Deux contrôles distincts, car l'empreinte seule ne suffit pas : le contenu de la ligne **et** le `prev_hash` attendu — sans le second, supprimer une ligne du milieu passerait inaperçu, chaque ligne restante étant individuellement cohérente. Avec `ACA_AUDIT_HMAC_KEY`, les empreintes deviennent des HMAC : forger une chaîne exige la clé, qui vit hors de la base. **Vérifié en direct** : falsification d'un `validated_by` détectée, ligne 2 désignée. Limites énoncées : c'est *tamper-evident*, pas tamper-proof (sans clé, qui écrit peut tout recalculer) ; le WORM/ancrage externe reste hors périmètre. Les lignes antérieures à la migration sont comptées « héritées, non chaînées », jamais signalées comme falsifiées |

### 15.3 Observabilité & robustesse d'exécution

| # | Item | Statut | Note |
|---|---|---|---|
| 15.3.1 | Logging | 🟡 | `print` + `analytics_store` + `audit_log` + tracing LangSmith. **Reste** : logging structuré/centralisé (niveaux, JSON, corrélation par `thread_id`) |
| 15.3.2 | Messages d'erreur ne fuitant pas de stack trace à l'utilisateur | ✅ **Fait (2026-07-26)** | Vérification faite, et elle a trouvé un vrai défaut : `ui.py` affichait `st.error(f"... : {e}")` à **quatre** endroits (Gmail, chargement d'e-mail, ingestion, validation). Le texte brut d'une exception d'API contient régulièrement l'URL appelée, des en-têtes, voire un fragment de clé. Remplacé par `_safe_error()` : message actionnable à l'écran, détail complet en console serveur. Côté API, un handler d'exception global renvoie un 500 générique + un **identifiant d'incident** corrélable aux journaux, au lieu de dépendre de la configuration du serveur ASGI |
| 15.3.3 | Endpoints admin/debug coupés ou verrouillés | ✅ **Fait (2026-07-26)** | `/docs`, `/redoc` et `/openapi.json` sont désormais **coupés dès `ACA_ENV=production`** (sauf `ACA_ENABLE_DOCS=1` explicite) — l'inverse du défaut FastAPI, qui publie la surface complète, routes d'écriture CRM comprises, sans rien demander. `/metrics` reste hors de `require_api_key` (un scrapeur Prometheus n'envoie pas d'en-tête applicatif) mais n'est plus public pour autant : garde dédiée `ACA_METRICS_TOKEN` (en-tête `X-Metrics-Token`), indépendante de la clé d'écriture CRM — Prometheus scrape sans jamais détenir la clé qui écrit dans le CRM |
| 15.3.4 | Gestion d'erreurs | ✅ | Dégradation gracieuse partout (chaque service optionnel absent = feature ignorée, jamais un crash) |
| 15.3.5 | Dégradation gracieuse | ✅ | Motif cœur du projet (notify/enrichment/veille/hubspot/billing…) |
| 15.3.6 | Retry avec back-off & idempotence | ✅ | `RETRY_POLICY` (3 essais, +429) sur chaque nœud à appel externe ; `sqlite_retry` hors graphe ; idempotence poller (`en_cours` avant `invoke`) |
| 15.3.7 | Circuit breakers & comportement de repli | 🟡 | Replis présents (Gemini→mots-clés, Tavily→"", Postgres→SQLite/mémoire). **Reste** : vrai disjoncteur (ouverture après N échecs pour cesser d'appeler un service mort) — non nécessaire au volume actuel |
| 15.3.8 | Scan de dépendances & patch de vulnérabilités | ✅ **Fait et exécuté (2026-07-26)** | `pip-audit` ajouté à `requirements.txt` et **réellement lancé** : 17 vulnérabilités connues dans 3 paquets, toutes corrigées (`gitpython` 3.1.50 → 8 avis, `pyasn1` 0.6.3 → 3, `pip` 25.2 → 6), scan final « No known vulnerabilities found ». Enseignement : **deux des trois paquets sont transitifs** (`gitpython` via Streamlit, `pyasn1` via `google-auth`) — le projet ne les importe pas, personne ne les aurait surveillés, et « `requirements.txt` épinglé » donnait une fausse impression de maîtrise puisque les dépendances indirectes n'y figuraient pas. D'où des planchers `>=` explicites, sans quoi une installation neuve réintroduisait les versions vulnérables. **Reste** : l'automatisation en CI dépend de 15.4.7 (pas de pipeline aujourd'hui) — la procédure manuelle est dans [DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md) §5 |

### 15.4 Tests & qualité

| # | Item | Statut | Note |
|---|---|---|---|
| 15.4.1 | Tests unitaires | ✅ | **261 tests** hors ligne (~12 s) — +69 lors de la passe sécurité du 2026-07-26 (hachage/rôles/sessions/injection/`prod_check` dans [test_security.py](../tests/test_security.py), chaînage d'audit et effacement RGPD dans `test_storage.py`, validation stricte et gardes d'API dans `test_api.py`, `injection_flags` dans `test_graph_nodes.py`) |
| 15.4.2 | Tests d'intégration | ✅ | Tests graphe complet (`test_graph_integration.py`, `test_api.py` via TestClient) |
| 15.4.3 | Tests end-to-end | 🟡 | Intégrations vérifiées *manuellement* en direct (Gmail/Sheets/Slack/Tavily/HubSpot/Supabase). **Reste** : E2E navigateur automatisé (UI Streamlit + dashboard) |
| 15.4.4 | Tests de régression | ✅ | La suite hors ligne + le jeu d'éval 50 e-mails jouent ce rôle |
| 15.4.5 | Tests de charge & stress | ❌ | Aucun. À ajouter (locust/k6) avant volume commercial — les quotas gratuits Groq/Gemini seront la 1re limite |
| 15.4.6 | Chaos engineering & tests de résilience | 🟡 | `RETRY_POLICY` vérifiée avec un 429 simulé ; pas d'injection de fautes systématique |
| 15.4.7 | Couverture, seuils & CI gates | ❌ | Pas de pipeline CI, pas de seuil de couverture bloquant. **À ajouter** : GitHub Actions (pytest + couverture + `pip-audit`) sur chaque push |
| 15.4.8 | Processus de revue de code & standards | ❌ | Projet solo, pas de revue par PR. À formaliser si l'équipe grandit (CODEOWNERS, PR obligatoire, linter en CI) |

### 15.5 Séquencement & priorisation

Ordre d'attaque proposé quand cette phase démarrera (après §11.6 et §12), du plus au moins
bloquant pour un vrai déploiement multi-client :

1. **Bloc « exposition publique »** (indispensable dès le 1er hébergement) : 15.1.9 HTTPS/TLS,
   15.3.2 pas de stack trace, 15.3.3 verrouiller `/docs`+`/metrics`, rendre 15.1.5 l'auth API
   *obligatoire* (pas optionnelle) en prod.
2. **Bloc « multi-client »** (dépend du multi-tenant §12) : 15.1.6 rôles/permissions, 15.1.7
   expiration de session, 15.2.1/15.2.2/15.2.3 RLS complète + isolation, 15.2.7 audit infalsifiable.
3. **Bloc « exploitation »** : 15.3.1 logging structuré, 15.1.8 coffre de secrets, 15.3.7 disjoncteurs,
   15.3.8 scan de dépendances.
4. **Bloc « qualité industrielle »** : 15.4.7 CI + couverture, 15.4.5 charge, 15.4.3 E2E, 15.4.8 revue.

**Déjà acquis avant même de démarrer cette phase** (à ne pas re-faire) : 15.1.1/15.1.2/15.1.3,
15.2.5, 15.3.4/15.3.5/15.3.6, 15.4.1/15.4.2/15.4.4. Autrement dit, une partie notable d'une
checklist « production-ready » standard est déjà couverte par l'architecture défensive existante —
le reste est un travail de durcissement ciblé, pas une reconstruction.

---

### 15.6 Passe de durcissement du 2026-07-26 — ce qui a été fait, et ce qui reste

Périmètre demandé : **la sécurité proprement dite** — §15.1, §15.2, et les items de §15.3 de nature
sécuritaire (15.3.2, 15.3.3, 15.3.8). Explicitement hors périmètre pour cette passe : 15.3.1
(logging structuré), 15.3.7 (disjoncteurs) et tout §15.4 (CI, charge, E2E, revue) — qualité et
exploitation, pas sécurité.

**Livré (12 items) :** 15.1.4, 15.1.5, 15.1.6, 15.1.7, 15.1.8, 15.1.9, 15.2.2, 15.2.4, 15.2.7,
15.3.2, 15.3.3, 15.3.8 — détail dans les tableaux ci-dessus. Nouveaux modules :
[user_store.py](../aca/storage/user_store.py), [session.py](../aca/core/session.py),
[prod_check.py](../aca/core/prod_check.py), [prompt_guard.py](../aca/core/prompt_guard.py),
[scripts/verify_rls.py](../scripts/verify_rls.py),
[DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md). Suite de tests : 192 → **261**.

**Ce que la passe a réellement trouvé** (au-delà de la mise en œuvre du plan) :

1. **Quatre fuites d'exception dans `ui.py`** (§15.3.2). L'audit disait « à vérifier » ; la
   vérification a trouvé quatre `st.error(f"… : {e}")` recopiant à l'écran le texte brut
   d'exceptions Gmail/Sheets/Groq.
2. **17 vulnérabilités de dépendances**, dont 11 dans deux paquets **transitifs** que le projet
   n'importe pas (§15.3.8). « `requirements.txt` épinglé » donnait une fausse assurance : les
   dépendances indirectes n'y figuraient pas.
3. **Le cookie du dashboard n'expirait jamais côté serveur** (§15.1.7). Le jeton HMAC était
   constant ; le `maxAge` du cookie, seul garde-fou apparent, n'est appliqué que par le navigateur
   et ne survit pas à une recopie du cookie.
4. **`POST /settings` acceptait n'importe quelle clé** (§15.1.4) — `config_store` est un magasin
   générique par conception, mais rien ne filtrait à la frontière réseau.
5. **Une nuance Postgres qui aurait rendu le rapport RLS trompeur** (§15.2.2) : `FORCE ROW LEVEL
   SECURITY` ne lie que le *propriétaire* de la table. Un script naïf aurait signalé les quatre
   tables LangGraph en permanence — et un rapport bruyant finit par ne plus être lu.

**Ce qui reste ouvert, et pourquoi** (aucun de ces points n'est un oubli) :

| Item | État | Raison |
|---|---|---|
| 15.1.9 TLS | Procédure prête, non appliquée | Rien n'est hébergé — l'appliquer demande un serveur et un domaine réels |
| 15.1.8 coffre | Règles + rotation documentées | Le coffre lui-même est une décision d'hébergement ; aucun changement de code ne sera requis |
| 15.2.1 RLS des stores locaux | 🟡 assumé | Cloisonnement `org_id` applicatif ; passer à un backend RLS est un arbitrage produit (multi-client réel), pas de la dette |
| 15.2.3 provisioning tenant | 🟡 | « 1 déploiement = 1 tenant » reste le modèle ; l'onboarding multi-client est un sujet produit |
| 15.2.6 DPA/DPIA | 🟡 | Documents juridiques propres à l'entreprise utilisatrice, non devinables depuis le code |
| 15.3.1, 15.3.7, tout §15.4 | Inchangés | Hors du périmètre « sécurité » demandé pour cette passe |

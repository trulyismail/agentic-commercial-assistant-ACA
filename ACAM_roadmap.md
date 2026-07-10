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
        ├─ connaissance_node         RAG sémantique (embeddings Gemini) → contexte FAQ
        └─ stratege_node   (Llama-70B) Proposition + devis (profil + FAQ + historique)
 ── interrupt_before ──              ⏸️  PAUSE : validation humaine (Streamlit « Valider »)
 → action_node          (write)      Écrit dans 'Leads' + marque l'e-mail Gmail comme traité
 → END

Ingestion (hors graphe) :  doc/PDF/Markdown ──(Groq → Q/R)──▶ onglet Knowledge_Base (Sheets)
```

Compilation : `app = workflow.compile(checkpointer=MemorySaver(), interrupt_before=["action"])`.
Le superviseur boucle avec les workers via `add_conditional_edges`. Deux interruptions humaines :
**clarification** en cours de route (`interrupt()` dynamique, repris par `Command(resume=...)`) et
**validation** finale (`interrupt_before`).

## 4. Le système de mémoire hybride

| Type | Outil | Rôle |
|---|---|---|
| **Court terme** (working memory) | `MemorySaver` (checkpointer LangGraph) | Conserve l'état du lead pendant la **pause de validation**. L'agent peut « dormir » puis reprendre exactement où il en était (`invoke(None, config)`), sans réinterroger le LLM. |
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
| 8 | Tests de robustesse (validate-loop réel de bout en bout) + rapport final | 🔨 Restant |

## 6. Stack technique

- **Orchestration :** Python / LangGraph (`StateGraph`, `MemorySaver`, `interrupt_before`, `app.stream`
  pour la progression en direct côté UI).
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

ACAM n'utilise aujourd'hui qu'une fraction de ce que LangGraph permet. Le graphe est un
**pipeline linéaire à 6 nœuds** avec un seul point de pause fixe ; les capacités ci-dessous sont
disponibles dans la même librairie mais pas encore mobilisées :

| Capacité LangGraph | Ce qu'elle apporterait à ACAM | État actuel |
|---|---|---|
| **`add_conditional_edges`** (routage dynamique) | Le classifieur pourrait router directement vers des chemins spécialisés (ex : un nœud d'escalade pour `SUPPORT` urgent) au lieu d'un `if` statique dans chaque nœud (`CATEGORIES_SANS_SUITE`) | Non utilisé — le graphe n'a que des `add_edge` fixes |
| **`interrupt()` dynamique** (au lieu de `interrupt_before` statique) | Permettrait à l'agent de demander une clarification humaine *au milieu* d'un nœud (ex : « urgence ambiguë, confirmez SVP ») plutôt qu'un seul point de pause fixe avant `action` | Non utilisé — un seul `interrupt_before=["action"]` |
| **Store API** (`langgraph.store`, mémoire long terme native, cross-thread) | Remplacerait/compléterait la lecture manuelle de Google Sheets par une mémoire sémantique interrogeable (embeddings) partagée entre threads, gérée par le framework | Non utilisé — la mémoire long terme passe entièrement par des appels `gspread` custom dans `sheets.py` |
| **Checkpointer persistant** (`SqliteSaver` / `PostgresSaver` au lieu de `MemorySaver`) | Les pauses de validation survivraient à un redémarrage de l'app (actuellement perdues en mémoire RAM) | Non utilisé — `MemorySaver()` est volatile |
| **Streaming** (`astream_events` / `stream_mode="messages"`) | L'UI pourrait afficher la progression nœud par nœud (« classification en cours... », « rédaction... ») au lieu d'un `st.spinner` bloquant unique | Non utilisé — `app.invoke()` synchrone, tout ou rien |
| **Exécution parallèle (fan-out/fan-in)** | `memory_lookup_node` et `rag_retrieval_node` sont indépendants (l'un lit `Leads`, l'autre `FAQ`) — ils pourraient s'exécuter en parallèle au lieu de séquentiellement | Non utilisé — chaîne strictement séquentielle |
| **`RetryPolicy` par nœud** | Retry automatique en cas d'erreur API Groq/Sheets transitoire, sans faire échouer tout le graphe | Non utilisé — aucun `try/except` autour des appels LLM dans les nœuds |
| **Sous-graphes (`subgraphs`)** | Un sous-graphe `ingestion` dédié (PDF + e-mail) réutilisable indépendamment du pipeline de qualification, conforme à l'architecture cible du document de vision | Non utilisé — l'extraction PDF vit dans `ui.py`, hors du graphe |
| **Time travel / `get_state_history()`** | Permettrait de rejouer ou d'auditer une décision passée de l'agent (utile pour justifier une classification en cas de litige) | Non utilisé — seul `get_state()` (état courant) est appelé, dans le `__main__` de démo |
| **Agents à outils (`bind_tools` / ReAct)** | Le `draft_writer_node` pourrait appeler des outils (vérifier un agenda, calculer un tarif exact) plutôt que produire un texte figé en un seul appel LLM | Non utilisé — aucun tool-calling, uniquement des prompts système/humain fixes |
| **Orchestration multi-agents (superviseur)** | Un agent superviseur pourrait déléguer `DEVIS` vs `SUPPORT` vs `DEMANDE_DEMO` à des sous-agents spécialisés avec leurs propres prompts/outils | Non utilisé — un seul graphe monolithique traite toutes les catégories |

## 10. Pistes d'amélioration de l'agent

Classées par effort estimé (croissant) :

- **Few-shot prompting** — les prompts de `classifier_node` / `extractor_node` sont zéro-shot ;
  ajouter 2-3 exemples annotés par catégorie réduirait les erreurs de classification en bordure
  (ex : SUPPORT vs DEVIS ambigus).
- **`with_structured_output()` / Pydantic** au lieu de `json.loads()` manuel dans `extractor_node` —
  élimine le fallback `{"raw": ...}` et garantit un schéma strict côté LangChain plutôt qu'un
  parsing défensif côté application.
- **Score de confiance de classification** — faire retourner un score (0-1) par `classifier_node`
  et router vers une relecture humaine systématique en dessous d'un seuil, au lieu d'un
  tout-ou-rien SPAM/AUTRE/valide.
- **`RetryPolicy` + gestion d'erreur réseau** — aujourd'hui une erreur Groq ou Sheets fait
  planter tout `app.invoke()` sans retry ni message utilisateur clair côté Streamlit.
- **Nœud `Ingestion`** explicite en tête de graphe (PDF + e-mail) — actuellement l'extraction PDF
  se fait dans `ui.py` avant l'appel à `app.invoke()`, ce qui casse l'encapsulation du graphe et
  empêche de rejouer un test avec pièce jointe depuis le seul `app.py`.
- **Traitement par lot des e-mails non lus** — `list_unread_emails` remonte déjà jusqu'à 10
  e-mails ; l'UI ne permet d'en traiter qu'un à la fois manuellement, sans file d'attente ni
  traitement automatique en série.
- **UI asynchrone / streaming** — remplacer le `st.spinner` bloquant par un affichage progressif
  nœud par nœud (cf. capacité `astream_events` ci-dessus), pour une meilleure perception de
  latence sur les cas avec pièce jointe volumineuse.
- **Nœud « Reflect » (auto-critique)** — avant de proposer le brouillon à validation, un second
  passage LLM qui relit sa propre réponse (ton, exactitude vis-à-vis de la FAQ, absence
  d'engagement commercial non autorisé) et la corrige si besoin.
- ✅ **RAG sémantique (embeddings)** — fait : `search_knowledge_base_semantic` (embeddings Gemini +
  similarité cosinus) remplace le recouvrement de mots-clés comme chemin principal, avec repli
  automatique sur `search_knowledge_base` si `GOOGLE_API_KEY` est absente ou l'appel échoue. Vérifié en
  direct : une reformulation ne partageant aucun mot-clé avec la FAQ ("Combien coûte votre abonnement
  mensuel pour une petite équipe ?" vs. "Quels sont vos tarifs professionnels ?") est correctement
  retrouvée par la recherche sémantique, là où la recherche par mots-clés ne renvoyait rien.
- **Notification Slack/e-mail pour les leads urgents** — déclenchée en sortie de `action_node`
  quand `extracted_info.urgence == "haute"`, pour réduire le délai de prise en charge humaine.
- **Persistance `SqliteSaver`** — pour que les analyses en attente de validation survivent à un
  redémarrage de l'app Streamlit (actuellement perdues si le process redémarre entre l'analyse et
  le clic « Valider »).

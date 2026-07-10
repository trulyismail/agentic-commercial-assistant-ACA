# Assistant Commercial Agentique (ACA)

Prototype de stage (8 semaines) qui **pré-lit les e-mails commerciaux entrants** (et leurs pièces
jointes PDF), en **extrait les informations de lead** via un LLM, puis **enregistre les leads qualifiés
dans Google Sheets** — mais uniquement après qu'un humain a cliqué sur « Valider » dans une interface
Streamlit. L'agent n'agit jamais seul sur le CRM : il rédige, puis attend la validation humaine.

> Construit sur **LangGraph** (graphe d'états à interruption native pour le *human-in-the-loop*),
> **Groq** (Llama, gratuit) pour le texte, et **Google Sheets** comme CRM + base de connaissances.

---

## Fonctionnalités

- **Classification** de l'e-mail en 5 catégories (`DEMANDE_DEMO`, `DEVIS`, `SUPPORT`, `AUTRE`, `SPAM`).
- **Analyse multimodale** : corps de l'e-mail + texte extrait des pièces jointes PDF.
- **Mémoire hybride** :
  - *court terme* — checkpointer LangGraph (`MemorySaver`) qui conserve l'état pendant la pause de
    validation ;
  - *long terme* — Google Sheets : historique client (détection de client récurrent / doublon) et base
    de connaissances (FAQ).
- **Architecture multi-agents à superviseur** : un superviseur oriente une équipe d'agents spécialisés
  (Enrichissement, Connaissance, Stratège) avec garde-fous déterministes, et expose une trace de
  raisonnement.
- **RAG sémantique « database-less »** : embeddings (Gemini, gratuit) + similarité cosinus sur l'onglet
  Knowledge_Base, avec repli automatique sur une recherche par mots-clés.
- **Ingestion de connaissances** : un doc/PDF/Markdown est découpé en Q/R (Groq) et écrit dans Google
  Sheets — le remplacement « sans base de données » d'un Vector DB (script `ingest.py` ou upload UI).
- **Enrichissement web** : profil de l'entreprise de l'expéditeur via Tavily (gratuit), avec mémoire
  long terme (cache Google Sheets par domaine).
- **Raisonnement + clarification** : quand une info clé manque, l'agent met le graphe en pause et pose
  une question à l'humain (interrupt dynamique), puis reprend.
- **Validation humaine (human-in-the-loop)** : aucune écriture CRM avant le clic « Valider ».
- **Ingestion Gmail réelle** : import des e-mails non lus, et marquage `ACA-Traite` après traitement.

## Architecture (ACAM v2 — superviseur + équipe d'agents)

Graphe LangGraph multi-agents, compilé avec `MemorySaver` + `interrupt_before=["action"]` :

```
START → classifier → memory_lookup → extractor → clarification (❓question à l'humain si besoin flou)
      → SUPERVISEUR ⇄ workers ──FINISH── interrupt ── action → END
                        ├─ enrichissement (Tavily + cache Sheets → profil entreprise)
                        ├─ connaissance   (RAG sémantique → contexte FAQ)
                        └─ stratege       (proposition + devis)

  Ingestion (hors graphe) :  doc/PDF/Markdown ──(Groq → Q/R)──▶ onglet Knowledge_Base (Sheets)
```

Le superviseur (Llama-8B) oriente dynamiquement l'équipe ; deux interruptions humaines : *clarification*
en cours de route (interrupt dynamique) et *validation* finale avant écriture CRM. Voir
[ACAM_roadmap.md](ACAM_roadmap.md) pour les piliers d'innovation, la mémoire par agent et la conception
« n8n-ready ».

## Stack

Python 3.14 · LangGraph · `langchain-groq` (Llama 3.1-8B / 3.3-70B, gratuit) · `google-genai` (embeddings
Gemini, gratuit) · Streamlit · `gspread` + `google-auth` (Google Sheets) · `google-api-python-client` +
`google-auth-oauthlib` (Gmail) · PyMuPDF · `python-dotenv`. Versions figées dans
[requirements.txt](requirements.txt).

## Prérequis

- Python 3.11+ (développé sous 3.14).
- Un tableur Google Sheets avec deux onglets : **`Leads`** (CRM) et **`FAQ`** (base de connaissances).
- Un **compte de service** Google (accès Sheets) et un **client OAuth « installed app »** Google (accès
  Gmail).
- Des clés API gratuites : **Groq** (LLM), **Google AI / Gemini** (embeddings), et **Tavily**
  (enrichissement web ; optionnelle — l'agent Enrichissement est ignoré sans elle).

## Installation

```bash
python -m venv venv
venv\Scripts\activate            # Windows (PowerShell/CMD)
pip install -r requirements.txt
```

Créer un fichier `.env` à la racine (gitignoré) :

```env
GROQ_API_KEY=...                 # LLM (Groq, gratuit)
GOOGLE_API_KEY=...               # Embeddings Gemini (RAG sémantique ; repli mots-clés si absent)
GOOGLE_SHEETS_ID=...             # ID du tableur Google
GOOGLE_SERVICE_ACCOUNT_FILE=credentials/service_account.json
TAVILY_API_KEY=...               # Agent Enrichissement (Tavily, gratuit ; enrichissement ignoré si absent)
# Optionnel (valeurs par défaut ci-dessous) :
# GMAIL_CREDENTIALS_FILE=credentials/gmail_credentials.json
# GMAIL_TOKEN_FILE=credentials/gmail_token.json
```

Placer les secrets dans `credentials/` (gitignoré) :

- `service_account.json` — compte de service (Google Sheets) ;
- `gmail_credentials.json` — client OAuth « installed app » (Gmail).

> Au premier accès Gmail, un navigateur s'ouvre pour l'autorisation (scope `gmail.modify`) ; le token est
> ensuite mis en cache dans `credentials/gmail_token.json`. À faire une fois en local (non headless).

## Initialisation des onglets (une seule fois)

```bash
python setup_sheets.py           # crée/formate l'en-tête de l'onglet Leads
python setup_faq.py              # insère des Q/R d'exemple dans l'onglet FAQ
```

## Alimenter la base de connaissances depuis un document (optionnel)

```bash
python ingest.py chemin/vers/doc.pdf           # ajoute des Q/R extraites du doc à l'onglet Knowledge_Base
python ingest.py chemin/vers/doc.md replace     # ou remplace tout le contenu existant
```

(Également possible via l'uploader dans la barre latérale de l'interface Streamlit.)

## Lancement

```bash
streamlit run ui.py              # interface web (import Gmail ou saisie manuelle → analyse → Valider)
```

Test rapide en ligne de commande (exécute le graphe sur 4 faux e-mails, **s'arrête à l'interruption sans
écrire au CRM**) :

```bash
python app.py
```

## Structure du projet

| Fichier | Rôle |
|---|---|
| [app.py](app.py) | Graphe LangGraph multi-agents : `AgentState`, classifier/mémoire/extraction/clarification, superviseur + 3 agents workers, action, checkpointer + interruptions |
| [ui.py](ui.py) | Interface Streamlit (thème Fluent) : formulaire/import Gmail, progression en direct, clarification interactive, raisonnement, validation, uploader base de connaissances |
| [sheets.py](sheets.py) | Google Sheets : CRM (`Leads`), base de connaissances (`FAQ`), cache d'enrichissement, RAG sémantique, écriture d'ingestion |
| [ingest.py](ingest.py) | Ingestion doc/PDF/Markdown → Q/R (Groq) → onglet Knowledge_Base (remplace un Vector DB) |
| [enrichment.py](enrichment.py) | Agent Enrichissement : profil entreprise via Tavily + cache Sheets (mémoire long terme) |
| [gmail_reader.py](gmail_reader.py) | API Gmail : lecture des non-lus, extraction PDF, marquage `ACA-Traite` |
| [pdf_reader.py](pdf_reader.py) | Extraction de texte PDF (PyMuPDF) |
| [setup_sheets.py](setup_sheets.py) / [setup_faq.py](setup_faq.py) | Scripts d'initialisation (one-off) des onglets |
| [CLAUDE.md](CLAUDE.md) · [ACAM_roadmap.md](ACAM_roadmap.md) · [ACA project description.md](ACA%20project%20description.md) | Documentation projet |

Onglets Google Sheets — **`Leads`** : `Date · Expéditeur · Entreprise · Contact · Urgence · Besoin ·
Catégorie · Brouillon` · **`FAQ`** (Knowledge_Base) : `Question · Réponse` · **`Enrichissement_Cache`**
(créé à la volée) : `Domaine · Profil · Date`.

## Statut

**ACAM v2** (architecture multi-agents à superviseur, ingestion database-less, raisonnement +
clarification interactive, mémoire hybride par agent) est implémenté et vérifié par phases. Détails,
mémoire par agent et conception « n8n-ready » dans [ACAM_roadmap.md](ACAM_roadmap.md).

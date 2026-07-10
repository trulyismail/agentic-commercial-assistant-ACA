# Assistant Commercial Agentique (ACA)

Internal internship prototype (8-week scope, see `ACA project description.md`) that pre-reads incoming
sales emails and PDF attachments, extracts lead info with an LLM, and writes qualified leads to Google
Sheets — but only after a human clicks "Valider" in a Streamlit UI. It does not act autonomously on the
CRM; it drafts and waits.

## Architecture (ACAM v2 — supervisor + agent team)

Multi-agent LangGraph graph in [app.py](app.py), compiled with a `MemorySaver` checkpointer and
`interrupt_before=["action"]` (see `ACAM_roadmap.md`):

```
START → classifier (8B) → memory_lookup → extractor (70B) → clarification (❓dynamic interrupt)
      → SUPERVISOR (8B) ⇄ workers ──FINISH── interrupt ── action → END
                        ├─ enrichissement (Tavily + Sheets cache → company_profile)
                        ├─ connaissance   (semantic RAG → faq_context)
                        └─ stratege       (70B, temp 0.3 → proposition/devis)
```

- `classifier_node` — labels the email `DEMANDE_DEMO | DEVIS | SUPPORT | AUTRE | SPAM`. `AUTRE` =
  legitimate but out-of-scope; unknown output falls back to `AUTRE`. Valid/no-suite sets are
  `CATEGORIES_VALIDES` / `CATEGORIES_SANS_SUITE`.
- `memory_lookup_node` — **long-term memory read**: `sheets.find_leads_by_sender()` fills
  `sender_history` + `is_duplicate` from the "Leads" tab. No LLM.
- `extractor_node` — extracts `{entreprise, contact, urgence, besoin_principal}` as JSON (falls back to
  `{"raw": ...}`).
- `clarification_node` — **interactive reasoning**: if `besoin_principal` is missing/ambiguous (and not
  SPAM/AUTRE), calls LangGraph's dynamic `interrupt()` to ask the human one question; the answer is
  merged into `extracted_info` on resume (`Command(resume=...)`). Otherwise passes through.
- `supervisor_node` — **orchestrator** (Llama-8B): picks the next worker
  (`enrichissement | connaissance | stratege | FINISH`) from `completed_agents`, with deterministic
  guardrails (SPAM/AUTRE→FINISH; never repeat an agent; `stratege` last; FINISH after it). Appends to
  `reasoning_log`. Workers each return to the supervisor (`add_conditional_edges`).
- `enrichissement_node` — **hybrid-memory agent**: `enrichment.research_company()` reads the
  `Enrichissement_Cache` tab first, else calls Tavily (free tier) and caches. Graceful fallback (`""`)
  if the domain is generic / `TAVILY_API_KEY` absent / error.
- `connaissance_node` — **semantic RAG "database-less"**: `sheets.search_knowledge_base_semantic()`
  embeds the FAQ/Knowledge_Base tab + query with Gemini (`gemini-embedding-001`, free) and ranks by
  cosine similarity into `faq_context`. Falls back to keyword `search_knowledge_base()` if
  `GOOGLE_API_KEY` missing / Gemini fails. (Groq has no embeddings endpoint — Gemini only for this
  piece; Groq still does classification/extraction/supervision/drafting.)
- `stratege_node` — **Llama-70B** proposal writer: personalized reply + indicative quote + next action,
  using `company_profile` + `faq_context` + `sender_history` + `extracted_info`. Always the last worker.
- `action_node` — runs **only after human validation**: the UI resumes with `app.invoke(None, config)`
  on "Valider" → `sheets.append_lead()` + (if Gmail-sourced) `mark_as_processed`.
- **Two interrupts:** dynamic `interrupt()` for mid-graph clarification (resumed with
  `Command(resume=answer)`); static `interrupt_before=["action"]` for the final validation. The UI
  distinguishes them: `get_state().interrupts` non-empty ⇒ clarification pending; empty + `next==action`
  ⇒ validation pause.
- **Memory:** short-term = shared graph state via `MemorySaver` (survives the pauses, `thread_id` per
  analysis); long-term = Google Sheets (`Leads` CRM, `FAQ` Knowledge_Base, `Enrichissement_Cache`).
- **Knowledge ingestion (out of graph):** [ingest.py](ingest.py) turns a doc/PDF/Markdown into Q/R rows
  (Groq) written to the Knowledge_Base tab — the "database-less" replacement for a vector DB. Run via
  `python ingest.py <path>` or the Streamlit sidebar uploader.
- Email intake: manual form entry, or "Rechercher les e-mails non lus" (real Gmail via
  [gmail_reader.py](gmail_reader.py)).
- **n8n-ready design:** each capability is an isolated node/module (see `ACAM_roadmap.md` §"Conception
  n8n-ready") so the graph can later be ported to an n8n workflow node-for-node.

## Files

- [app.py](app.py) — LangGraph definition, `AgentState` (TypedDict; adds `company_profile`, `next_agent`,
  and reducer lists `completed_agents`/`reasoning_log`), the classifier/memory/extractor/clarification
  nodes, the `supervisor_node` + three worker agents (`enrichissement`/`connaissance`/`stratege`),
  `action_node`, the `MemorySaver`/`interrupt_before` compile, and a `__main__` block with 4 mock emails
  (incl. `AUTRE`) that run through the interrupt without a CRM write (`python app.py`).
- [ingest.py](ingest.py) — knowledge ingestion: `ingest_document(source, mode)` extracts text (PDF via
  `pdf_reader`, or `.md`/`.txt`), asks Groq to split it into Q/R pairs, and writes them to the
  Knowledge_Base tab via `sheets.write_knowledge_rows`. CLI (`python ingest.py <path> [append|replace]`)
  and Streamlit uploader both call it. The "database-less" replacement for a vector DB.
- [enrichment.py](enrichment.py) — `research_company(sender)`: company profile from the sender's domain.
  Reads the `Enrichissement_Cache` Sheets tab first (long-term memory), else Tavily (free tier) then
  caches. Graceful `""` fallback for generic domains / missing `TAVILY_API_KEY` / errors.
- [ui.py](ui.py) — Streamlit front-end, styled with a light "Fluent" theme
  ([.streamlit/config.toml](.streamlit/config.toml)). Sidebar Gmail import (fetch unread → pick one →
  load into form) or manual form entry (sender/subject/body + PDF upload) → generates a `thread_id` and
  runs the graph via an `advance_graph()` helper that streams each node live in an `st.status` block,
  then reads `get_state(config)`. If a clarification `interrupt` is pending, it renders the agent's
  question + a reply box and resumes with `Command(resume=...)` (looping until the validation pause);
  otherwise it shows a colored category badge / returning-customer + duplicate banners / a "Fiche
  prospect" card (metrics + urgency + company profile) / a "Raisonnement de l'équipe" expander
  (`reasoning_log`) / the proposition → "Valider" resumes with `app.invoke(None, config)` →
  `action_node`. The sidebar also has a **knowledge-base uploader** (calls `ingest.ingest_document`).
  `SPAM`/`AUTRE` show an info/error box and no validation button.
- [gmail_reader.py](gmail_reader.py) — Gmail API integration (OAuth "installed app" flow):
  `get_gmail_service()` (auths, caches token in `credentials/gmail_token.json`), `list_unread_emails()`,
  `get_email()` (body + first PDF attachment, decoded), `mark_as_processed()` (removes `UNREAD`, adds
  `ACA-Traite` label, creating it if needed). First run requires an interactive browser consent — see
  Setup notes below.
- [sheets.py](sheets.py) — Google Sheets integration via `gspread` + service account:
  `get_sheet()` (opens the "Leads" tab), `search_knowledge_base_semantic(query)` (Gemini embeddings +
  cosine similarity, top-N; the `connaissance_node` entry point), `search_knowledge_base(query)` (older
  keyword/token-overlap search, the fallback when Gemini is unavailable), `write_knowledge_rows(pairs,
  mode)` (ingestion write path — append/replace on the Knowledge_Base tab, invalidates the embedding
  cache), `find_leads_by_sender(sender)` (returning-customer / duplicate lookup), `append_lead()`
  (appends a row: Date | Expéditeur | Entreprise | Contact | Urgence | Besoin | Catégorie | Brouillon),
  and `get_cached_profile(domain)`/`cache_profile(domain, profile)` (the enrichment agent's long-term
  memory on the auto-created `Enrichissement_Cache` tab: Domaine | Profil | Date). Knowledge reads/writes
  share `_get_knowledge_worksheet()`. The knowledge tab name is the `KNOWLEDGE_TAB` constant (currently
  `"FAQ"`). FAQ embeddings are cached in an in-memory dict (`_faq_embedding_cache`), recomputed only
  when the FAQ tab's content changes — so a normal run costs one Gemini call for the query, not one per
  FAQ row.
- [pdf_reader.py](pdf_reader.py) — `extract_text_from_pdf()` using PyMuPDF (`fitz`); accepts bytes or a
  path; truncates output to 15,000 chars to bound LLM token usage.
- [setup_sheets.py](setup_sheets.py) — one-off script to insert/bold-format the "Leads" header row.
- [setup_faq.py](setup_faq.py) — one-off script to seed sample Q&A into the "FAQ" tab.

## Stack

LangGraph (supervisor graph, `MemorySaver`, static + dynamic `interrupt`) · `langchain_groq`
(Groq-hosted Llama models, free tier, chat only — Groq has no embeddings endpoint) · `google-genai`
(Gemini embeddings, free tier, semantic RAG only) · `tavily-python` (web enrichment, free tier) ·
Streamlit (Fluent theme via [.streamlit/config.toml](.streamlit/config.toml)) · `gspread` + `google-auth`
(Google Sheets as CRM + knowledge base) · `google-api-python-client` + `google-auth-oauthlib` (Gmail) ·
PyMuPDF · `python-dotenv`. Pinned in [requirements.txt](requirements.txt).

Required env vars (`.env`, gitignored): `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_ID`, a Groq API
key for `langchain_groq`, `GOOGLE_API_KEY` (Gemini, for `search_knowledge_base_semantic`; RAG silently
falls back to keyword search if absent), `TAVILY_API_KEY` (enrichment agent; silently skips enrichment
if absent), and optionally `GMAIL_CREDENTIALS_FILE` / `GMAIL_TOKEN_FILE` (default to
`credentials/gmail_credentials.json` / `credentials/gmail_token.json`).

`credentials/` (gitignored) holds `service_account.json` (Sheets) and `gmail_credentials.json` (Gmail
OAuth client secret, "installed app" type). `gmail_token.json` is created there on first Gmail auth.

### Gmail setup notes

`gmail_credentials.json` is an OAuth "installed app" client secret, not a service account — the first
call to `get_gmail_service()` opens a real browser window for the account owner to grant consent
(scope: `gmail.modify`). This can't be done headlessly; run `python gmail_reader.py` or click "Rechercher
les e-mails non lus" in the UI once locally to complete it. The resulting token is cached and reused
afterward.

## Known gaps

- No automated test suite; verification is `app.py`'s `__main__` mock run + headless Streamlit `AppTest`
  scripts (run ad hoc during development). The CLI run stops at the interrupt (no CRM write).
- `TAVILY_API_KEY` is not set in the current `.env`, so the enrichment agent always hits its graceful
  fallback (`company_profile = ""`); the Tavily + `Enrichissement_Cache` path is coded and unit-safe but
  not yet exercised against the live API.
- The clarification trigger is "empty `besoin_principal`"; the 70B extractor usually fills it, so
  clarification fires only on genuinely vague emails (by design).

## Status vs. the 8-week roadmap

The linear ACAM v1 (hybrid memory, semantic RAG, `AUTRE` taxonomy, live validate-loop) is done and was
verified end-to-end. **ACAM v2** (this multi-agent supervisor + team) is implemented and verified per
phase: document ingestion → Sheets, supervisor + enrichissement/connaissance/stratège, reasoning trace,
and interactive clarification (dynamic `interrupt`). See `ACAM_roadmap.md`. Remaining: exercise the
enrichment agent against a live `TAVILY_API_KEY`, and the eventual n8n port (design already n8n-ready).

# Assistant Commercial Agentique (ACA)

Internal internship prototype (8-week scope, see `ACA project description.md`) that pre-reads incoming
sales emails and PDF attachments, extracts lead info with an LLM, and writes qualified leads to Google
Sheets — but only after a human clicks "Valider" in a Streamlit UI. It does not act autonomously on the
CRM; it drafts and waits.

## Architecture (ACAM v2 — supervisor + agent team)

Multi-agent LangGraph graph in [app.py](app.py), compiled with a `SqliteSaver` checkpointer
(`checkpoints.sqlite`, local file — survives app restarts, unlike the earlier `MemorySaver`),
`interrupt_before=["action"]`, and a `RetryPolicy` (`RETRY_POLICY`, custom `_retry_on` extending
LangGraph's default to also retry HTTP 429) on every node that calls an external API — absorbs
transient Groq/Sheets/Gemini/Tavily/Gmail errors instead of crashing `app.invoke()` (see
`ACAM_roadmap.md`):

```
START → classifier (8B) → memory_lookup → extractor (70B) → clarification (❓dynamic interrupt)
      → SUPERVISOR (8B) ⇄ workers ──FINISH── notification ── interrupt ── action → END
                        ├─ enrichissement (Tavily + Sheets cache → company_profile)
                        ├─ connaissance   (semantic RAG → faq_context)
                        ├─ veille         (Tavily fallback if FAQ empty → enriches FAQ tab)
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
  guardrails (SPAM/AUTRE→FINISH; never repeat an agent; `stratege` last; FINISH after it; `veille`
  forced right after `connaissance` if `faq_context` is still empty — not offered to the LLM as a free
  choice, purely a deterministic guardrail). Appends to `reasoning_log`. Workers each return to the
  supervisor (`add_conditional_edges`).
- `enrichissement_node` — **hybrid-memory agent**: `enrichment.research_company()` reads the
  `Enrichissement_Cache` tab first, else calls Tavily (free tier) and caches. Graceful fallback (`""`)
  if the domain is generic / `TAVILY_API_KEY` absent / error.
- `connaissance_node` — **semantic RAG "database-less"**: `sheets.search_knowledge_base_semantic()`
  embeds the FAQ/Knowledge_Base tab + query with Gemini (`gemini-embedding-001`, free) and ranks by
  cosine similarity into `faq_context`. Falls back to keyword `search_knowledge_base()` if
  `GOOGLE_API_KEY` missing / Gemini fails. (Groq has no embeddings endpoint — Gemini only for this
  piece; Groq still does classification/extraction/supervision/drafting.)
- `veille_node` — **web-research agent, FAQ fallback**: only invoked by the deterministic guardrail
  above. `veille.search_faq_online()` queries Tavily (free tier) with `besoin_principal` (or the raw
  email), reformats the answer into a clean Q/R pair (Groq 8B), and **stages it into the FAQ tab**
  (`sheets.write_knowledge_rows(..., statut="à valider")`) — invisible to the RAG until a human
  approves it from the Streamlit sidebar (`sheets.get_pending_knowledge_rows` /
  `approve_knowledge_row` / `reject_knowledge_row`); the answer is still used for *this* proposal
  (reviewed by the human at the "Valider" gate either way). Same hybrid-memory pattern as
  `enrichissement_node`. Graceful `""` fallback if `TAVILY_API_KEY` absent / search fails / no answer.
- `stratege_node` — **Llama-70B** proposal writer: personalized reply + indicative quote + next action,
  using `company_profile` + `faq_context` + `sender_history` + `extracted_info`. Always the last
  worker. For `DEMANDE_DEMO`, appends the real Calendly link (`CALENDLY_URL`) to the draft
  **deterministically in code** (not LLM-generated, to avoid a mangled URL) — absent = graceful
  no-op, draft unchanged (vague promise, as before).
- `notification_node` — alerts a human that an analysis is waiting to be validated, right before the
  pause (skipped for `SPAM`/`AUTRE` — nothing to validate there). `notify.send()` tries Slack
  (`SLACK_WEBHOOK_URL`) then a real Gmail send-to-self (`NOTIFY_EMAIL` — an internal alert, not a
  customer-facing action, so auto-sending doesn't violate the "drafts and waits" rule); no-ops if
  neither is configured.
- `action_node` — runs **only after human validation**: the UI resumes with `app.invoke(None, config)`
  on "Valider" → `sheets.append_lead()` + (if Gmail-sourced) `mark_as_processed` +
  `gmail_reader.create_draft_reply` (creates a Gmail draft in the original thread with the proposition,
  so the human only has to reread and click Send — never auto-sent).
- **Two interrupts:** dynamic `interrupt()` for mid-graph clarification (resumed with
  `Command(resume=answer)`); static `interrupt_before=["action"]` for the final validation. The UI
  distinguishes them: `get_state().interrupts` non-empty ⇒ clarification pending; empty + `next==action`
  ⇒ validation pause.
- **Memory:** short-term = shared graph state via `MemorySaver` (survives the pauses, `thread_id` per
  analysis); long-term = Google Sheets (`Leads` CRM, `FAQ` Knowledge_Base, `Enrichissement_Cache`).
- **Knowledge ingestion (out of graph):** [ingest.py](ingest.py) turns a doc/PDF/Markdown into Q/R rows
  (Groq) written to the Knowledge_Base tab — the "database-less" replacement for a vector DB. Run via
  `python ingest.py <path>` or the Streamlit sidebar uploader.
- Email intake: manual form entry, "Rechercher les e-mails non lus" (real Gmail via
  [gmail_reader.py](gmail_reader.py), one at a time), or automatic in the background via
  [poller.py](poller.py) (see below) — all three share the same graph and checkpointer.
- **n8n-ready design:** each capability is an isolated node/module (see `ACAM_roadmap.md` §"Conception
  n8n-ready") so the graph can later be ported to an n8n workflow node-for-node.

## Files

- [app.py](app.py) — LangGraph definition, `AgentState` (TypedDict; adds `company_profile`, `next_agent`,
  `gmail_thread_id` (real Gmail thread, read by `ui.py` after validation for `relance.py` — no node
  touches it, it just rides along in the state), and reducer lists `completed_agents`/`reasoning_log`), the classifier/memory/extractor/clarification
  nodes, the `supervisor_node` + four worker agents (`enrichissement`/`connaissance`/`veille`/`stratege`),
  `action_node`, the `SqliteSaver`/`interrupt_before` compile, and a `__main__` block with 4 mock emails
  (incl. `AUTRE`) that run through the interrupt without a CRM write (`python app.py`).
- [poller.py](poller.py) — standalone background intake: run separately (`python poller.py`, own
  process/terminal — not started by Streamlit), polls `gmail_reader.list_unread_emails()` every
  `POLL_INTERVAL_SECONDS` (default 60), and for each email not already in `queue_store` runs it
  through `aca_graph.app.invoke()` up to the same validation pause as the manual flow (never past
  it — a human still has to click "Valider" in the UI), then records it via `queue_store.enqueue()`.
- [queue_store.py](queue_store.py) — tiny local SQLite registry (`queue.sqlite`, not the Google
  Sheet) tracking which Gmail messages `poller.py` has already queued (emails stay `UNREAD` until
  validated, so without this they'd be reprocessed every poll cycle) and which are still pending
  human review. `enqueue()` marks a message `en_cours` **before** `app.invoke()` (idempotence: a
  poller crash mid-analysis won't cause a duplicate reprocessing — `is_known()` is already `True`),
  `mark_ready()` flips it to `en_attente` once the graph reaches the pause, `reset_stale()` recovers
  entries stuck in `en_cours` past a timeout (default 15 min). `list_pending()` feeds the UI's "File
  d'attente" sidebar panel; `mark_validated(thread_id)` is called after "Valider".
  `list_validated_older_than()`/`purge_validated_older_than()` support `retention.py`.
- [followup_store.py](followup_store.py) — local SQLite registry (`followup.sqlite`) of validated
  leads sourced from Gmail (`track()`, no-op if no `gmail_thread_id` — manual entries can't be
  followed up automatically), consumed by `relance.py`. `mark_followed_up()` after a follow-up
  draft is created (one follow-up per lead in this version, no multi-round cadence).
- [relance.py](relance.py) — automatic follow-ups (P1 §11.4 item 7): for each tracked lead, reads
  the last message of the real Gmail thread (`threads().get()`); if it's from **us** (the sales
  rep) and at least `RELANCE_DAYS` old (default 4), drafts a follow-up in-thread via
  `gmail_reader.create_draft_reply()` — never auto-sent. If the last message is from the prospect,
  does nothing (they replied, or we haven't sent our first reply yet). Run via `python relance.py`
  (standalone, meant to be scheduled — e.g. daily — independent of `poller.py`).
- [audit_log.py](audit_log.py) — minimal traceability (`audit.sqlite`, local, not the Google Sheet):
  `log_validation(thread_id, validated_by, classification, sender)` called from `ui.py`'s "Valider"
  handler; `validated_by` comes from the sidebar's "Validé par" free-text field (no real
  multi-user auth — see `ACA_UI_PASSWORD` below). `list_recent()` for a future audit screen.
- [retention.py](retention.py) — GDPR/PII retention sweep (`RETENTION_DAYS`, default 365): purges
  `Leads` rows, their corresponding `checkpoints.sqlite` threads (`checkpointer.delete_thread`,
  removes the raw email body from graph state), and old validated `queue.sqlite` entries older
  than the retention window. Never touches `Enrichissement_Cache` (company data, not personal) or
  `FAQ`. Run via `python retention.py`, meant to be scheduled (e.g. weekly).
- [notify.py](notify.py) — `send(message)`: Slack webhook (`SLACK_WEBHOOK_URL`) then Gmail
  send-to-self (`NOTIFY_EMAIL`) as a graceful-degradation chain, same pattern as
  `enrichment.py`/`veille.py`. Called by `notification_node`. `python notify.py` sends a one-off
  test message on whichever channel is configured.
- [ingest.py](ingest.py) — knowledge ingestion: `ingest_document(source, mode)` extracts text (PDF via
  `pdf_reader`, or `.md`/`.txt`), asks Groq to split it into Q/R pairs, and writes them to the
  Knowledge_Base tab via `sheets.write_knowledge_rows`. CLI (`python ingest.py <path> [append|replace]`)
  and Streamlit uploader both call it. The "database-less" replacement for a vector DB.
- [enrichment.py](enrichment.py) — `research_company(sender)`: company profile from the sender's domain.
  Reads the `Enrichissement_Cache` Sheets tab first (long-term memory), else Tavily (free tier) then
  caches. Graceful `""` fallback for generic domains / missing `TAVILY_API_KEY` / errors.
- [veille.py](veille.py) — `search_faq_online(query)`: Tavily search (free tier) for a question the FAQ
  couldn't answer, reformats the result into a `(question, réponse)` pair via Groq 8B
  (`_format_qr`), and stages it into the FAQ tab (`sheets.write_knowledge_rows(..., statut="à
  valider")`) — invisible to the RAG until approved via the Streamlit sidebar. Graceful `""` fallback
  (same pattern as `enrichment.py`) if `TAVILY_API_KEY` absent / search fails / no answer.
- [ui.py](ui.py) — Streamlit front-end, styled with a light "Fluent" theme
  ([.streamlit/config.toml](.streamlit/config.toml)). `_check_auth()` gates the whole app behind an
  optional password (`ACA_UI_PASSWORD`; absent = no gate, dev mode) before anything else renders.
  Top of the sidebar has a "Validé par" free-text field (session-scoped, used for `audit_log`) and
  the **"File d'attente"** panel (`queue_store.list_pending()`) — analyses queued by `poller.py`; clicking "Ouvrir" on an entry
  calls `load_queued_thread()` to load its already-paused state (no re-run — the graph already ran in
  the poller process) via the shared `_sync_result()` helper. Below that: Gmail import (fetch unread →
  pick one → load into form, the manual one-at-a-time path) or manual form entry (sender/subject/body +
  PDF upload) → generates a `thread_id` and runs the graph via an `advance_graph()` helper that streams
  each node live in an `st.status` block, then reads `get_state(config)`. If a clarification `interrupt` is pending, it renders the agent's
  question + a reply box and resumes with `Command(resume=...)` (looping until the validation pause);
  otherwise it shows a colored category badge / returning-customer + duplicate banners / a "Fiche
  prospect" card (metrics + urgency + company profile) / a "Raisonnement de l'équipe" expander
  (`reasoning_log`) / the proposition → "Valider" resumes with `app.invoke(None, config)` →
  `action_node`, then `queue_store.mark_validated()` (no-op if the thread wasn't queue-sourced) and
  `audit_log.log_validation()`. The sidebar also has a **knowledge-base uploader** (calls `ingest.ingest_document`)
  and a **"FAQ en attente" review panel** (`sheets.get_pending_knowledge_rows`) with Valider/Rejeter
  buttons per row (`approve_knowledge_row`/`reject_knowledge_row`) for content staged by `veille`.
  `SPAM`/`AUTRE` show an info/error box and no validation button.
- [gmail_reader.py](gmail_reader.py) — Gmail API integration (OAuth "installed app" flow):
  `get_gmail_service()` (auths, caches token in `credentials/gmail_token.json`), `list_unread_emails()`,
  `get_email()` (body + first PDF attachment, decoded, plus the real Gmail `gmail_thread_id` — used
  by `relance.py`, distinct from the LangGraph `thread_id` used everywhere else),
  `mark_as_processed()` (removes `UNREAD`, adds
  `ACA-Traite` label, creating it if needed), `create_draft_reply()` (creates a Gmail draft in the
  original thread — correct `threadId` + `In-Reply-To`/`References` headers — so the drafted proposal
  is ready to reread and send, never auto-sent). First run requires an interactive browser consent —
  see Setup notes below.
- [sheets.py](sheets.py) — Google Sheets integration via `gspread` + service account:
  `get_sheet()` (opens the "Leads" tab), `search_knowledge_base_semantic(query)` (Gemini embeddings +
  cosine similarity, top-N; the `connaissance_node` entry point), `search_knowledge_base(query)` (older
  keyword/token-overlap search, the fallback when Gemini is unavailable), `write_knowledge_rows(pairs,
  mode, statut)` (ingestion write path — append/replace on the Knowledge_Base tab; `statut` defaults to
  `"validé"` for human-provided ingestion, `"à valider"` for `veille`'s web content; invalidates the
  embedding cache), `get_pending_knowledge_rows()` / `approve_knowledge_row(row_index)` /
  `reject_knowledge_row(row_index)` (human review of staged `veille` rows), `find_leads_by_sender(sender)`
  (returning-customer / duplicate lookup), `append_lead()` (appends a row: Date | Expéditeur |
  Entreprise | Contact | Urgence | Besoin | Catégorie | Brouillon), and
  `get_cached_profile(domain)`/`cache_profile(domain, profile)` (the enrichment agent's long-term
  memory on the auto-created `Enrichissement_Cache` tab: Domaine | Profil | Date). Knowledge reads/writes
  share `_get_knowledge_worksheet()`. The knowledge tab name is the `KNOWLEDGE_TAB` constant (currently
  `"FAQ"`, schema `Question | Réponse | Statut` — the `Statut` column was added this session; rows
  without it, i.e. pre-existing content, are treated as `"validé"` for backward compatibility).
  `_get_knowledge_records()` (shared by both search functions) filters out `"à valider"`/`"rejeté"`
  rows, so staged `veille` content is invisible to the RAG until approved. FAQ embeddings are cached in
  an in-memory dict (`_faq_embedding_cache`), recomputed only when the FAQ tab's visible content
  changes — so a normal run costs one Gemini call for the query, not one per FAQ row.
- [pdf_reader.py](pdf_reader.py) — `extract_text_from_pdf()` using PyMuPDF (`fitz`); accepts bytes or a
  path; truncates output to 15,000 chars to bound LLM token usage.
- [setup_sheets.py](setup_sheets.py) — one-off script to insert/bold-format the "Leads" header row.
- [setup_faq.py](setup_faq.py) — one-off script to seed sample Q&A into the "FAQ" tab.
- [format_sheets.py](format_sheets.py) — one-off, idempotent visual polish for all 3 tabs: frozen +
  bold/gray header row, wrapped/widened long-text columns, and conditional cell coloring (Leads:
  `Urgence`/`Catégorie` — same palette as the UI's category badges; FAQ: `Statut`). Touches formatting
  only, never cell values. Run via `python format_sheets.py`.
- [eval_dataset.json](eval_dataset.json) — 50 synthetic labeled emails (10 per category, a few
  deliberately ambiguous) for [eval_classifier.py](eval_classifier.py), which runs each through
  `classifier_node` and reports overall/per-category accuracy + misclassifications. Last measured:
  96% (48/50). Run via `python eval_classifier.py`; re-run once real emails are available to track
  accuracy under real conditions instead of the synthetic set.

## Stack

LangGraph (supervisor graph, `SqliteSaver`, static + dynamic `interrupt`) · `langchain_groq`
(Groq-hosted Llama models, free tier, chat only — Groq has no embeddings endpoint) · `google-genai`
(Gemini embeddings, free tier, semantic RAG only) · `tavily-python` (web enrichment, free tier) ·
Streamlit (Fluent theme via [.streamlit/config.toml](.streamlit/config.toml)) · `gspread` + `google-auth`
(Google Sheets as CRM + knowledge base) · `google-api-python-client` + `google-auth-oauthlib` (Gmail) ·
PyMuPDF · `python-dotenv`. Pinned in [requirements.txt](requirements.txt).

Required env vars (`.env`, gitignored): `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_ID`, a Groq API
key for `langchain_groq`, `GOOGLE_API_KEY` (Gemini, for `search_knowledge_base_semantic`; RAG silently
falls back to keyword search if absent), `TAVILY_API_KEY` (enrichment agent; silently skips enrichment
if absent), and optionally `GMAIL_CREDENTIALS_FILE` / `GMAIL_TOKEN_FILE` (default to
`credentials/gmail_credentials.json` / `credentials/gmail_token.json`), `ACA_CHECKPOINT_DB` (default
`checkpoints.sqlite`), `ACA_QUEUE_DB` (default `queue.sqlite`), `POLL_INTERVAL_SECONDS` for
[poller.py](poller.py) (default `60`), `SLACK_WEBHOOK_URL` / `NOTIFY_EMAIL` for
[notify.py](notify.py) (both optional; no-ops if absent — see Gmail setup notes for the incoming-
webhook steps, no new account needed for `NOTIFY_EMAIL` since it reuses the existing Gmail auth),
`ACA_UI_PASSWORD` (optional password gate for [ui.py](ui.py); absent = no gate), `ACA_AUDIT_DB`
(default `audit.sqlite`), `RETENTION_DAYS` for [retention.py](retention.py) (default `365`),
`ACA_FOLLOWUP_DB` (default `followup.sqlite`) / `RELANCE_DAYS` (default `4`) for
[relance.py](relance.py), optionally `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` +
`LANGCHAIN_PROJECT` for LangSmith tracing (free tier, 5k traces/mo — no code change needed,
`langchain`/`langgraph` auto-instrument from these env vars alone), and optionally `CALENDLY_URL`
(real booking link appended to `DEMANDE_DEMO` proposals by `stratege_node`; absent = vague promise
as before).

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
- `poller.py` and `ui.py` are separate processes that can open `checkpoints.sqlite`/`queue.sqlite`
  concurrently. `RETRY_POLICY` (✅ done, item 9) now retries transient errors inside every graph
  node — including checkpointer reads/writes during `app.invoke()` — but the standalone SQLite
  writes in `queue_store.py`/`audit_log.py` (`enqueue`, `mark_ready`, `mark_validated`,
  `log_validation`) run outside the graph and are NOT wrapped by any retry; a lock conflict there
  would still raise. Low risk at prototype volume (one poll cycle, occasional UI clicks), but a
  real fix would wrap those calls in their own small retry loop before real concurrent load.
  `PROCESSED_LABEL_NAME`/`gmail.modify` mean the poller never deletes anything.
- ✅ **Fixed**: P0 item 2 (Slack/e-mail notification) is now coded (`notify.py` + `notification_node`,
  graceful no-op without `SLACK_WEBHOOK_URL`/`NOTIFY_EMAIL`) — not yet exercised against a real
  webhook/inbox (no destination configured in `.env` yet, only the graceful-fallback path verified).
- P0 item 5 (routing `SUPPORT`/`AUTRE` to real destinations) from `ACAM_roadmap.md` §11.4 remains
  explicitly deferred — no support/HR destination address configured yet.
- `TAVILY_API_KEY` is not set in the current `.env`, so the enrichment agent always hits its graceful
  fallback (`company_profile = ""`); the Tavily + `Enrichissement_Cache` path is coded and unit-safe but
  not yet exercised against the live API.
- The clarification trigger is "empty `besoin_principal`"; the 70B extractor usually fills it, so
  clarification fires only on genuinely vague emails (by design).
- `search_knowledge_base_semantic`'s cosine-similarity cutoff (`score > 0.5`, [sheets.py:207](sheets.py#L207))
  is too permissive against the current 2-row FAQ: even unrelated queries (e.g. "recette de tarte aux
  pommes") score above it and "match", so `faq_context` is effectively never empty and the new `veille`
  guardrail rarely fires in practice with this seed data. Not yet fixed — raising the threshold or
  growing the FAQ (which should naturally spread out similarity scores) would need to be verified
  against real data before relying on `veille` triggering as designed.
- ✅ **Fixed**: `veille` used to write unverified web content straight into the FAQ tab. It now stages
  it (`statut="à valider"`), invisible to the RAG until approved from the Streamlit sidebar. Gmail
  drafting after "Valider" (`create_draft_reply`) was added the same session — see `ACAM_roadmap.md`
  §11.4 items 1 and 6, both now done.

## Status vs. the 8-week roadmap

The linear ACAM v1 (hybrid memory, semantic RAG, `AUTRE` taxonomy, live validate-loop) is done and was
verified end-to-end. **ACAM v2** (this multi-agent supervisor + team) is implemented and verified per
phase: document ingestion → Sheets, supervisor + enrichissement/connaissance/stratège, reasoning trace,
and interactive clarification (dynamic `interrupt`). See `ACAM_roadmap.md`. A fourth worker, `veille`
(web search that self-enriches the FAQ tab when `connaissance` finds nothing), was added afterward —
wired into the graph and verified not to loop/crash, though its trigger condition rarely fires against
the current 2-row FAQ seed data (see Known gaps). Five of the six §11.4 P0 production gaps are now
closed: Gmail draft-reply after "Valider" (item 1, live-verified), background intake via
`poller.py` + the "File d'attente" sidebar (item 3, live-verified without touching the real CRM),
`SqliteSaver` persistence surviving a simulated restart (item 4, live-verified), human-facing
notification via `notify.py` (item 2, coded + graceful-fallback path verified, not yet exercised
against a real Slack/e-mail destination), and human staging/approval for `veille`'s web content
(item 6, live-verified). Item 5 (routing `SUPPORT`/`AUTRE` to real destinations) remains explicitly
deferred pending a support/HR address. Also done this session: `format_sheets.py` visual polish
(frozen/bold headers, conditional coloring, wrapped columns) on all 3 Sheets tabs, and 4 of the 7
§11.4 P1 items — `RetryPolicy` on every external-call node (item 9, verified with a simulated 429),
idempotent `poller.py` intake via `en_cours`/`en_attente` staging + `reset_stale()` (item 8, verified
with a backdated stale entry), a minimal password gate + "Validé par" audit trail (item 10,
verified headless via `AppTest` for the gate, direct calls for the log), a GDPR retention sweep
(item 13, `retention.py`, verified live against a synthetic old row — real leads untouched, all
within the 365-day default), and automatic follow-ups (item 7, `relance.py` +
`followup_store.py` — drafts a follow-up in-thread if we were last to speak and `RELANCE_DAYS` have
passed; verified with a mocked Gmail service across all 3 branches: prospect replied, too-soon,
follow-up triggered), LangSmith tracing (item 11 — connection + 5 traces live-verified in the "ACA"
project, plus a 50-email labeled eval set (`eval_dataset.json` + `eval_classifier.py`) that measured
**96% classifier accuracy** (48/50), with the 2 errors both on deliberately ambiguous cases), and a
real Calendly link appended deterministically to `DEMANDE_DEMO` drafts (item 12, verified: link
present on the `DEMANDE_DEMO` mock case, absent on `DEVIS`). **All 7 §11.4 P1 items are now done.**
Remaining: item 5, exercise `notify.py`/the enrichment agent/`veille`/`relance.py` against real
Slack+`TAVILY_API_KEY`+Gmail-thread-with-a-reply credentials, tune the RAG similarity threshold,
§11.4 P2, and the eventual n8n port (design already n8n-ready).

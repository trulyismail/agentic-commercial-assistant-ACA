# Assistant Commercial Agentique (ACA)

Internal internship prototype (8-week scope, see `docs/ACA project description.md`) that pre-reads incoming
sales emails and PDF attachments, extracts lead info with an LLM, and writes qualified leads to Google
Sheets — but only after a human clicks "Valider" in a Streamlit UI. It does not act autonomously on the
CRM; it drafts and waits.

## Architecture (ACAM v2 — supervisor + agent team)

Multi-agent LangGraph graph in [app.py](aca/core/app.py), compiled with a checkpointer —
`PostgresSaver` (Supabase, shared across `ui.py`/`poller.py` processes) if `DATABASE_URL` is
configured, else the original `SqliteSaver` (`data/checkpoints.sqlite`, local file — survives app
restarts, unlike the earlier `MemorySaver`) — `interrupt_before=["action"]`, and a `RetryPolicy`
(`RETRY_POLICY`, custom `_retry_on` extending LangGraph's default to also retry HTTP 429) on every
node that calls an external API — absorbs transient Groq/Sheets/Gemini/Tavily/Gmail errors instead
of crashing `app.invoke()` (see `docs/ACAM_roadmap.md`):

```
START → classifier (8B) → memory_lookup → extractor (70B) → clarification (❓dynamic interrupt)
      → SUPERVISOR (8B) ⇄ workers ──FINISH── routing ── notification ── interrupt ── action → END
                        ├─ enrichissement (Tavily + Sheets cache → company_profile)
                        ├─ connaissance   (hybrid RAG, dense+sparse RRF fusion → faq_context)
                        ├─ veille         (Tavily fallback if FAQ empty → enriches FAQ tab)
                        └─ stratege       (70B, temp 0.3 → proposition/devis)
                              ↓
                          reflection (8B self-critique) ──rewrite (1x max)──→ back to stratege
                              └──ok──────────────────────────────────────────→ routing
```

- `classifier_node` — labels the email `DEMANDE_DEMO | DEVIS | SUPPORT | AUTRE | SPAM`. `AUTRE` =
  legitimate but out-of-scope; unknown output falls back to `AUTRE`. Valid/no-suite sets are
  `CATEGORIES_VALIDES` / `CATEGORIES_SANS_SUITE` (`{SPAM, AUTRE, SUPPORT}` — none of these three are
  sales leads, so none reach `stratege`/the CRM; `SUPPORT`/`AUTRE` are instead handled by
  `routing_node` below).
- `memory_lookup_node` — **long-term memory read**: `sheets.find_leads_by_sender()` fills
  `sender_history` + `is_duplicate` from the "Leads" tab. No LLM.
- `extractor_node` — extracts `{entreprise, contact, urgence, besoin_principal}` via `with_structured_output(ExtractedInfo)`
  (Pydantic model, tool-calling under the hood — no manual `json.loads()`, no malformed-JSON risk).
  Graceful fallback to an all-`None` `ExtractedInfo()` if structured extraction fails outright
  (network error surviving `RETRY_POLICY`'s 3 attempts, or the model producing something the schema
  rejects) — never crashes `app.invoke()`, and `clarification_node` asks the human exactly as it
  would for a genuinely vague email.
- `clarification_node` — **interactive reasoning**: if `besoin_principal` is missing/ambiguous (and not
  SPAM/AUTRE/SUPPORT), calls LangGraph's dynamic `interrupt()` to ask the human one question; the
  answer is merged into `extracted_info` on resume (`Command(resume=...)`). Otherwise passes through.
- `supervisor_node` — **orchestrator** (Llama-8B): picks the next worker
  (`enrichissement | connaissance | stratege | FINISH`) from `completed_agents`, with deterministic
  guardrails (SPAM/AUTRE/SUPPORT→FINISH; never repeat an agent; `stratege` last;
  `veille` forced right after `connaissance` if `faq_context` is still empty — not offered to the LLM
  as a free choice, purely a deterministic guardrail). Appends to `reasoning_log`. Workers each return
  to the supervisor (`add_conditional_edges`), **except** `stratege`, which goes straight to
  `reflection` instead (see below) — once the supervisor picks `stratege` it is never called again
  for that analysis.
- `_build_rag_query()` — **query de-contextualization**, shared by `connaissance_node`/`veille_node`:
  builds the RAG query from `besoin_principal` (already extracted by the 70B, possibly refined by the
  human via `clarification_node`) instead of the raw email — the raw subject/body carries greetings
  and pleasantries that dilute the embedding. If the sender is a returning customer
  (`sender_history` non-empty), the besoin may reference a prior exchange implicitly ("that option",
  "like last time"); a Llama-8B call resolves it into a standalone, explicit query using
  `sender_history` as context before it hits the embedding. Falls back to raw subject+body if no
  `besoin_principal` was extracted.
- `enrichissement_node` — **hybrid-memory agent**: `enrichment.research_company()` reads the
  `Enrichissement_Cache` tab first, else calls Tavily (free tier) and caches. Graceful fallback (`""`)
  if the domain is generic / `TAVILY_API_KEY` absent / error.
- `connaissance_node` — **hybrid RAG "database-less"**: `sheets.search_knowledge_base_semantic()`
  fuses a DENSE search (Gemini embeddings, `gemini-embedding-001`, free — cosine similarity, catches
  paraphrases) and a SPARSE search (keyword/token overlap — catches exact alphanumeric matches dense
  embeddings miss, e.g. a product reference or "99.9%" SLA) via Reciprocal Rank Fusion, into
  `faq_context`. A dual confidence threshold on the top dense score (calibrated empirically on the
  74-row FAQ, see Known gaps) creates three zones: **≥0.72** trust the match outright; **0.62–0.71**
  ("amber zone") still inject the match — better than a hard rejection on a genuine borderline case —
  but prefix it with `LOW_CONFIDENCE_MARKER`, which `connaissance_node` strips before it reaches the
  Stratège's prompt while logging a "confiance modérée" note in `reasoning_log` for the human to see;
  **<0.62 with no sparse hits either** returns `""` (triggers `veille`). Falls back entirely to
  keyword `search_knowledge_base()` if `GOOGLE_API_KEY` missing / Gemini fails. (Groq has no
  embeddings endpoint — Gemini only for this piece; Groq still does
  classification/extraction/supervision/drafting.)
- `veille_node` — **web-research agent, FAQ fallback**: only invoked by the deterministic guardrail
  above. `veille.search_faq_online()` queries Tavily (free tier) with the same de-contextualized query
  as `connaissance_node` (`_build_rag_query()`), reformats the answer into a clean Q/R pair (Groq 8B),
  and **stages it into the FAQ tab**
  (`sheets.write_knowledge_rows(..., statut="à valider")`) — invisible to the RAG until a human
  approves it from the Streamlit sidebar (`sheets.get_pending_knowledge_rows` /
  `approve_knowledge_row` / `reject_knowledge_row`); the answer is still used for *this* proposal
  (reviewed by the human at the "Valider" gate either way). Same hybrid-memory pattern as
  `enrichissement_node`. Graceful `""` fallback if `TAVILY_API_KEY` absent / search fails / no answer.
- `stratege_node` — **Llama-70B** proposal writer: personalized reply + indicative quote + next action,
  using `company_profile` + `faq_context` + `sender_history` + `extracted_info` (+ `reflection_feedback`
  when `reflection_node` sent it back for a rewrite). Always the last worker. For `DEMANDE_DEMO`,
  appends the real Calendly link (`CALENDLY_URL`) to the draft **deterministically in code** (not
  LLM-generated, to avoid a mangled URL) — absent = graceful no-op, draft unchanged (vague promise,
  as before).
- `reflection_node` — **self-critique loop** (Llama-8B, a check not a generation): reads `draft_response`
  back against the `faq_context` actually used and looks for an unsupported claim (price/deadline/
  feature not in the FAQ) or an inappropriate tone. `REWRITE: <reason>` sends the draft back to
  `stratege_node` with the reason in `reflection_feedback`; `OK` continues to `routing`. Capped at
  **one** rewrite (guarded by counting `stratege` occurrences in `completed_agents`) so a stubborn
  disagreement between the two LLM calls can't loop forever — past that, the draft passes through as-is
  and the human validation gate (`interrupt_before=["action"]`) remains the final backstop either way.
- `routing_node` — routes `SUPPORT`/`AUTRE` to the right team instead of dropping them after
  classification (P0 §11.4 item 5; no-op for `DEMANDE_DEMO`/`DEVIS`/`SPAM`). Declarative
  `ROUTING_DESTINATIONS` dict (category → label/email/webhook) so adding a future routed category is
  one dict entry + one env-var pair. Two independent, gracefully-degrading actions per routed
  category: (1) an immediate alert via `notify.send()` (generalized to accept a webhook/email/subject
  override) to `SUPPORT_EMAIL`/`SUPPORT_SLACK_WEBHOOK_URL` or `HR_EMAIL`/`HR_SLACK_WEBHOOK_URL`; (2) a
  Gmail **forward** draft (`gmail_reader.create_forward_draft`, never auto-sent — same pattern as
  `create_draft_reply`) prefilled with the original message, only if Gmail-sourced and a destination
  address is configured. Sits between the supervisor's FINISH and `notification` in the graph.
- `notification_node` — alerts a human that an analysis is waiting to be validated, right before the
  pause (skipped for `SPAM`/`AUTRE`/`SUPPORT` — nothing to validate there). `notify.send()` tries Slack
  (`SLACK_WEBHOOK_URL`) then a real Gmail send-to-self (`NOTIFY_EMAIL` — an internal alert, not a
  customer-facing action, so auto-sending doesn't violate the "drafts and waits" rule); no-ops if
  neither is configured.
- `action_node` — runs **only after human validation**: the UI resumes with `app.invoke(None, config)`
  on "Valider" → `sheets.append_lead()` + `hubspot.create_lead()` (real CRM, runs **alongside** Sheets
  during the transition period — see `hubspot.py` below) + (if Gmail-sourced) `mark_as_processed` +
  `gmail_reader.create_draft_reply` (creates a Gmail draft in the original thread with the proposition,
  so the human only has to reread and click Send — never auto-sent).
- **Two interrupts:** dynamic `interrupt()` for mid-graph clarification (resumed with
  `Command(resume=answer)`); static `interrupt_before=["action"]` for the final validation. The UI
  distinguishes them: `get_state().interrupts` non-empty ⇒ clarification pending; empty + `next==action`
  ⇒ validation pause.
- **Memory:** short-term = shared graph state via `MemorySaver` (survives the pauses, `thread_id` per
  analysis); long-term = Google Sheets (`Leads` CRM, `FAQ` Knowledge_Base, `Enrichissement_Cache`).
- **Knowledge ingestion (out of graph):** [ingest.py](aca/ingestion/ingest.py) turns a doc/PDF/Markdown into Q/R rows
  (Groq) written to the Knowledge_Base tab — the "database-less" replacement for a vector DB. Run via
  `python -m aca.ingestion.ingest <path>` or the Streamlit sidebar uploader.
- Email intake: manual form entry, "Rechercher les e-mails non lus" (real Gmail via
  [gmail_reader.py](aca/integrations/gmail_reader.py), one at a time), or automatic in the background via
  [poller.py](aca/core/poller.py) (see below) — all three share the same graph and checkpointer.
- **n8n-ready design:** each capability is an isolated node/module (see `docs/ACAM_roadmap.md` §"Conception
  n8n-ready") so the graph can later be ported to an n8n workflow node-for-node.

## Files

- [app.py](aca/core/app.py) — LangGraph definition, `AgentState` (TypedDict; adds `company_profile`, `next_agent`,
  `reflection_feedback` (critique text from `reflection_node`, consumed by `stratege_node` on rewrite),
  `gmail_thread_id` (real Gmail thread, read by `ui.py` after validation for `relance.py` — no node
  touches it, it just rides along in the state), and reducer lists `completed_agents`/`reasoning_log`), the classifier/memory/extractor/clarification
  nodes, the `supervisor_node` + four worker agents (`enrichissement`/`connaissance`/`veille`/`stratege`),
  `reflection_node` (self-critique after `stratege`), `action_node`, the `SqliteSaver`/`interrupt_before`
  compile, and a `__main__` block with 5 mock emails
  (incl. `AUTRE` and `SUPPORT`) that run through the interrupt without a CRM write (`python -m aca.core.app`).
- [poller.py](aca/core/poller.py) — standalone background intake: run separately (`python -m aca.core.poller`, own
  process/terminal — not started by Streamlit), polls `gmail_reader.list_unread_emails()` every
  `POLL_INTERVAL_SECONDS` (default 60), and for each email not already in `queue_store` runs it
  through `aca_graph.app.invoke()` up to the same validation pause as the manual flow (never past
  it — a human still has to click "Valider" in the UI), then records it via `queue_store.enqueue()`
  and logs the classification event via `analytics_store.record_classification()` (dashboard data —
  captured as soon as the graph pauses, independent of whether a human ever opens it in the UI).
- [queue_store.py](aca/storage/queue_store.py) — tiny local SQLite registry (`data/queue.sqlite`, not the Google
  Sheet) tracking which Gmail messages `poller.py` has already queued (emails stay `UNREAD` until
  validated, so without this they'd be reprocessed every poll cycle) and which are still pending
  human review. `enqueue()` marks a message `en_cours` **before** `app.invoke()` (idempotence: a
  poller crash mid-analysis won't cause a duplicate reprocessing — `is_known()` is already `True`),
  `mark_ready()` flips it to `en_attente` once the graph reaches the pause, `reset_stale()` recovers
  entries stuck in `en_cours` past a timeout (default 15 min). `list_pending()` feeds the UI's "File
  d'attente" sidebar panel; `mark_validated(thread_id)` is called after "Valider".
  `list_validated_older_than()`/`purge_validated_older_than()` support `retention.py`. Every public
  function is wrapped with `sqlite_retry.with_sqlite_retry()` (below).
- [sqlite_retry.py](aca/storage/sqlite_retry.py) — `with_sqlite_retry()` decorator (3 attempts, linear
  backoff) applied to every public function of `queue_store.py`/`analytics_store.py`/`audit_log.py`/
  `followup_store.py` — the standalone SQLite writes these four modules make **outside** the graph
  (`app.RETRY_POLICY` only covers nodes during `app.invoke()`), so a lock conflict between `poller.py`
  and `ui.py` opening the same file concurrently no longer raises immediately. Retries only
  `sqlite3.OperationalError`; any other exception propagates on the first attempt (same "don't retry
  a programming error" principle as `app._retry_on`).
- [followup_store.py](aca/storage/followup_store.py) — local SQLite registry (`data/followup.sqlite`) of validated
  leads sourced from Gmail (`track()`, no-op if no `gmail_thread_id` — manual entries can't be
  followed up automatically), consumed by `relance.py`. `mark_followed_up()` after a follow-up
  draft is created (one follow-up per lead in this version, no multi-round cadence).
- [relance.py](aca/core/relance.py) — automatic follow-ups (P1 §11.4 item 7): for each tracked lead, reads
  the last message of the real Gmail thread (`threads().get()`); if it's from **us** (the sales
  rep) and at least `RELANCE_DAYS` old (default 4), drafts a follow-up in-thread via
  `gmail_reader.create_draft_reply()` — never auto-sent. If the last message is from the prospect,
  does nothing (they replied, or we haven't sent our first reply yet). Run via `python -m aca.core.relance`
  (standalone, meant to be scheduled — e.g. daily — independent of `poller.py`).
- [audit_log.py](aca/storage/audit_log.py) — minimal traceability (`data/audit.sqlite`, local, not the Google Sheet):
  `log_validation(thread_id, validated_by, classification, sender)` called from `ui.py`'s "Valider"
  handler; `validated_by` comes from the sidebar's "Validé par" free-text field (no real
  multi-user auth — see `ACA_UI_PASSWORD` below). `list_recent()` for a future audit screen.
- [analytics_store.py](aca/storage/analytics_store.py) — local SQLite event log (`data/analytics.sqlite`, P2 §11.4
  item 17) powering the "Tableau de bord" tab in `ui.py`. Unlike the Sheets `Leads` tab (only
  validated `DEMANDE_DEMO`/`DEVIS`) or `audit_log.py` (only validation events), this captures
  **every** classification — including `SPAM`/`AUTRE`/`SUPPORT`, which never get validated.
  `record_classification(thread_id, classification, sender, source)` (idempotent `INSERT OR
  IGNORE`, called from `poller.py` and `ui.py._sync_result()`), `record_draft_ready(thread_id)`
  (separate call, since a clarification pause can log the classification before Stratège has
  written a draft), `record_validation(thread_id)` (closes the response-time measurement, called
  from the "Valider" handler). Read side: `volume_by_category()`, `daily_volume()`,
  `response_times()`, `funnel_counts()` — all `days`-windowed.
- [retention.py](aca/core/retention.py) — GDPR/PII retention sweep (`RETENTION_DAYS`, default 365): purges
  `Leads` rows, their corresponding `data/checkpoints.sqlite` threads (`checkpointer.delete_thread`,
  removes the raw email body from graph state), and old validated `data/queue.sqlite` entries older
  than the retention window. Never touches `Enrichissement_Cache` (company data, not personal) or
  `FAQ`. Run via `python -m aca.core.retention`, meant to be scheduled (e.g. weekly).
- [notify.py](aca/integrations/notify.py) — `send(message, webhook_url=None, email_to=None, subject=None)`: Slack
  webhook (`SLACK_WEBHOOK_URL`, or `webhook_url` override) then Gmail send-to-self (`NOTIFY_EMAIL`,
  or `email_to` override) as a graceful-degradation chain, same pattern as `enrichment.py`/`veille.py`.
  Called by `notification_node` (generic leads channel) and `routing_node` (per-category
  `SUPPORT_EMAIL`/`HR_EMAIL` overrides). `python -m aca.integrations.notify` sends a one-off test message on whichever
  channel is configured.
- [ingest.py](aca/ingestion/ingest.py) — knowledge ingestion: `ingest_document(source, mode)` extracts text (PDF via
  `pdf_reader`, or `.md`/`.txt`), asks Groq to split it into Q/R pairs, and writes them to the
  Knowledge_Base tab via `sheets.write_knowledge_rows`. CLI (`python -m aca.ingestion.ingest <path> [append|replace]`)
  and Streamlit uploader both call it. The "database-less" replacement for a vector DB.
- [enrichment.py](aca/agents/enrichment.py) — `research_company(sender)`: company profile from the sender's domain.
  Reads the `Enrichissement_Cache` Sheets tab first (long-term memory), else Tavily (free tier) then
  caches. Graceful `""` fallback for generic domains / missing `TAVILY_API_KEY` / errors.
- [veille.py](aca/agents/veille.py) — `search_faq_online(query)`: Tavily search (free tier) for a question the FAQ
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
  the poller process) via the shared `_sync_result()` helper (which also logs the classification event
  to `analytics_store.py` — idempotent, so opening an already-poller-logged thread is a no-op). Below
  that: Gmail import (fetch unread → pick one → load into form, the manual one-at-a-time path) or
  manual form entry (sender/subject/body + multi-file PDF/Word/Excel upload via `attachment_reader`)
  → generates a `thread_id` and runs the graph via an `advance_graph()` helper that streams
  each node live in an `st.status` block, then reads `get_state(config)`. Main area is two
  `st.tabs`: **"Nouvel e-mail"** (the flow above) and **"Tableau de bord"** (KPIs + charts from
  `analytics_store.py`, period filter via `st.segmented_control`). If a clarification `interrupt` is
  pending, it renders the agent's question + a reply box and resumes with `Command(resume=...)`
  (looping until the validation pause); otherwise it shows a colored category badge / returning-customer
  + duplicate banners / a "Fiche prospect" card (metrics + urgency + company profile) / a "Raisonnement
  de l'équipe" expander (`reasoning_log`) / the proposition → "Valider" resumes with
  `app.invoke(None, config)` → `action_node`, then `queue_store.mark_validated()` (no-op if the thread
  wasn't queue-sourced), `audit_log.log_validation()`, and `analytics_store.record_validation()`.
  `SUPPORT` renders like `AUTRE` (info box + routing-detail expander, no CRM card/validation button —
  both are routed by `routing_node` instead). The sidebar also has a **knowledge-base uploader** (calls
  `ingest.ingest_document`) and a **"FAQ en attente" review panel** (`sheets.get_pending_knowledge_rows`)
  with Valider/Rejeter buttons per row (`approve_knowledge_row`/`reject_knowledge_row`) for content
  staged by `veille`. `SPAM` shows a plain error box, no validation button.
- [gmail_reader.py](aca/integrations/gmail_reader.py) — Gmail API integration (OAuth "installed app" flow):
  `get_gmail_service()` (auths, caches token in `credentials/gmail_token.json`), `list_unread_emails()`,
  `get_email()` (body + **all** PDF/Word/Excel attachments — `_extract_attachments()` walks every
  MIME part recursively instead of stopping at the first PDF, P2 §11.4 item 16 — plus the real
  Gmail `gmail_thread_id` — used by `relance.py`, distinct from the LangGraph `thread_id` used
  everywhere else), `mark_as_processed()` (removes `UNREAD`, adds
  `ACA-Traite` label, creating it if needed), `create_draft_reply()` (creates a Gmail draft in the
  original thread — correct `threadId` + `In-Reply-To`/`References` headers — so the drafted proposal
  is ready to reread and send, never auto-sent), `create_forward_draft()` (same never-auto-sent draft
  pattern, but addressed to a third party — support/HR — instead of replying to the original sender;
  used by `routing_node` for `SUPPORT`/`AUTRE`). First run requires an interactive browser consent —
  see Setup notes below.
- [sheets.py](aca/integrations/sheets.py) — Google Sheets integration via `gspread` + service account:
  `get_sheet()` (opens the "Leads" tab), `search_knowledge_base_semantic(query)` (Gemini embeddings +
  vector search, top-N; the `connaissance_node` entry point), `search_knowledge_base(query)` (older
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
  rows, so staged `veille` content is invisible to the RAG until approved. FAQ authoring stays here
  unchanged (`ingest.py`, `veille` staging, sidebar approve/reject); vector storage/search itself
  delegates to [vector_store.py](aca/integrations/vector_store.py) when `DATABASE_URL` (Supabase) is configured — else
  falls back to the original in-memory dict (`_faq_embedding_cache`), recomputed only when the FAQ
  tab's visible content changes, so a normal run costs one Gemini call for the query, not one per row.
- [vector_store.py](aca/integrations/vector_store.py) — pgvector-backed semantic search on Supabase Postgres (P2 §11.1
  vector-DB migration, brought forward ahead of the volume triggers at the user's request — see
  docs/ACAM_roadmap.md §11.1/§11.2 for the original trigger-based plan). `is_enabled()` gates everything on
  `DATABASE_URL`; `sync_embeddings(pairs, embed_documents)` fully replaces the `faq_embeddings` table
  (`question | reponse | embedding VECTOR(3072) | updated_at`, no ANN index yet — a sequential scan
  with pgvector's `<=>` operator is exact and sub-millisecond at FAQ-sized volumes; add
  `hnsw`/`ivfflat` later if the FAQ grows into the thousands) whenever `sheets.py` detects the FAQ's
  visible content changed; `search(query_vector, top_n, max_distance)` returns the nearest rows by
  cosine distance (note: pgvector gives a *distance*, not a similarity — the old `score > 0.5`
  threshold is `distance < 0.5` here). Absent `DATABASE_URL` = fully inert, `sheets.py` uses its
  original in-memory path unchanged.
- [hubspot.py](aca/integrations/hubspot.py) — real CRM (P2), mirrors `sheets.append_lead()`: called from
  `action_node` **alongside** Sheets (not replacing it — Sheets stays the memory `find_leads_by_sender()`/
  the dashboard read from). `is_enabled()` gates everything on `HUBSPOT_ACCESS_TOKEN` (private-app token).
  `create_lead(email_classification, extracted_info, sender, draft)` upserts a Contact by e-mail (search →
  patch or create), creates a Deal (`HUBSPOT_PIPELINE`/`HUBSPOT_DEALSTAGE`, default `"default"`/
  `"appointmentscheduled"` — every fresh HubSpot portal ships these), associates it to the contact via the
  v4 default-association endpoint, and attaches a Note (Deals have no free-text property by default) with
  urgence/besoin/draft — all via `requests` against the CRM v3/v4 REST API directly (no SDK dependency).
  Same graceful-degradation contract as `notify.py`/`enrichment.py`: never raises, returns `None` on any
  failure or absent token. Live-verified end-to-end against the real portal (contact/deal/note/associations
  all created correctly, then deleted as test cleanup) — and that verification caught a real bug: printing
  `→`/`⚠️` crashed with `UnicodeEncodeError` under this Windows shell's cp1252 console *after* the HubSpot
  write had already succeeded, and because `action_node` is `RETRY_POLICY`-wrapped, an uncaught exception
  there would have retried the whole node — duplicating the lead in both Sheets and HubSpot. Fixed by
  guaranteeing `return deal_id` never depends on a print succeeding (prints are now try/excepted with an
  ASCII fallback). `python -m aca.integrations.hubspot` runs a one-off live test (creates + reports a real
  test deal — clean up manually afterward, this module has no dry-run mode).
- [pdf_reader.py](aca/ingestion/pdf_reader.py) — `extract_text_from_pdf()` using PyMuPDF (`fitz`); accepts bytes or a
  path; truncates output to `MAX_CHARS` (15,000) to bound LLM token usage. Used as-is by
  `ingest.py`/the Knowledge_Base uploader (single PDF, unchanged). Internally also exposes
  `extract_raw_text_from_pdf()` (no truncation) + the `MAX_CHARS` constant, reused by
  `attachment_reader.py` below so multi-file truncation happens once, on the combined text.
- [attachment_reader.py](aca/ingestion/attachment_reader.py) — `extract_text_from_attachments(attachments)`: the
  multi-format email-attachment path (P2 §11.4 item 16) — a real RFP arrives with several PDF/Word/
  Excel documents, not one PDF. Dispatches by extension (`.pdf` → `pdf_reader.extract_raw_text_from_pdf`,
  `.docx` → `python-docx`, `.xlsx` → `openpyxl`, reading all sheets), concatenates each file prefixed
  by its filename, then truncates the **combined** text to `MAX_CHARS` once (not per file, so N
  attachments can't blow up the LLM context). Unsupported extensions / failed extraction are
  silently skipped (graceful degradation, same pattern as the rest of the project). Consumed by
  `gmail_reader.get_email()`'s `attachments` list, `poller.py`, and `ui.py`'s manual multi-file
  uploader.
- [setup_sheets.py](scripts/setup_sheets.py) — one-off script to insert/bold-format the "Leads" header row.
- [setup_faq.py](scripts/setup_faq.py) — one-off script to seed sample Q&A into the "FAQ" tab.
- [format_sheets.py](scripts/format_sheets.py) — one-off, idempotent visual polish for all 3 tabs: frozen +
  bold/gray header row, wrapped/widened long-text columns, and conditional cell coloring (Leads:
  `Urgence`/`Catégorie` — same palette as the UI's category badges; FAQ: `Statut`). Touches formatting
  only, never cell values. Run via `python scripts/format_sheets.py`.
- [eval_dataset.json](aca/eval/eval_dataset.json) — 50 synthetic labeled emails (10 per category, a few
  deliberately ambiguous) for [eval_classifier.py](aca/eval/eval_classifier.py), which runs each through
  `classifier_node` and reports overall/per-category accuracy + misclassifications. Last measured:
  96% (48/50). Run via `python -m aca.eval.eval_classifier`; re-run once real emails are available to track
  accuracy under real conditions instead of the synthetic set.
- [tests/](tests/) — automated pytest suite (92 tests, offline, ~2s — see Known gaps for full
  coverage list): [conftest.py](tests/conftest.py) (env isolation + `FakeLLM`/`ExplodingLLM`),
  [test_graph_nodes.py](tests/test_graph_nodes.py), [test_sheets_helpers.py](tests/test_sheets_helpers.py),
  [test_storage.py](tests/test_storage.py) (incl. `sqlite_retry.py` coverage),
  [test_degradation.py](tests/test_degradation.py),
  [test_graph_integration.py](tests/test_graph_integration.py). Run via `python -m pytest tests/`
  (pytest pinned in requirements.txt).

## Stack

LangGraph (supervisor graph, `SqliteSaver`/`PostgresSaver`, static + dynamic `interrupt`) ·
`langchain_groq` (Groq-hosted Llama models, free tier, chat only — Groq has no embeddings endpoint) ·
`google-genai` (Gemini embeddings, free tier, semantic RAG only) · `psycopg`/`psycopg-pool` +
`pgvector` + `langgraph-checkpoint-postgres` (Supabase Postgres — vector store + checkpointer,
optional, see `DATABASE_URL` below) · `tavily-python` (web enrichment, free tier) ·
Streamlit (Fluent theme via [.streamlit/config.toml](.streamlit/config.toml)) · `gspread` + `google-auth`
(Google Sheets as CRM + knowledge base) · `google-api-python-client` + `google-auth-oauthlib` (Gmail) ·
PyMuPDF (PDF) · `python-docx` (Word) · `openpyxl` (Excel) · `python-dotenv`. Pinned in
[requirements.txt](requirements.txt).

Required env vars (`.env`, gitignored): `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_ID`, a Groq API
key for `langchain_groq`, `GOOGLE_API_KEY` (Gemini, for `search_knowledge_base_semantic`; RAG silently
falls back to keyword search if absent), `TAVILY_API_KEY` (enrichment agent; silently skips enrichment
if absent), and optionally `GMAIL_CREDENTIALS_FILE` / `GMAIL_TOKEN_FILE` (default to
`credentials/gmail_credentials.json` / `credentials/gmail_token.json`), `ACA_CHECKPOINT_DB` (default
`data/checkpoints.sqlite`), `ACA_QUEUE_DB` (default `data/queue.sqlite`), `POLL_INTERVAL_SECONDS` for
[poller.py](aca/core/poller.py) (default `60`), `SLACK_WEBHOOK_URL` / `NOTIFY_EMAIL` for
[notify.py](aca/integrations/notify.py) (both optional; no-ops if absent — see Gmail setup notes for the incoming-
webhook steps, no new account needed for `NOTIFY_EMAIL` since it reuses the existing Gmail auth),
`ACA_UI_PASSWORD` (optional password gate for [ui.py](ui.py); absent = no gate), `ACA_ANALYTICS_DB`
(default `data/analytics.sqlite`, dashboard event log), `ACA_AUDIT_DB`
(default `data/audit.sqlite`), `RETENTION_DAYS` for [retention.py](aca/core/retention.py) (default `365`),
`ACA_FOLLOWUP_DB` (default `data/followup.sqlite`) / `RELANCE_DAYS` (default `4`) for
[relance.py](aca/core/relance.py), optionally `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` +
`LANGCHAIN_PROJECT` for LangSmith tracing (free tier, 5k traces/mo — no code change needed,
`langchain`/`langgraph` auto-instrument from these env vars alone), optionally `CALENDLY_URL`
(real booking link appended to `DEMANDE_DEMO` proposals by `stratege_node`; absent = vague promise
as before), optionally `SUPPORT_EMAIL` / `SUPPORT_SLACK_WEBHOOK_URL` / `HR_EMAIL` /
`HR_SLACK_WEBHOOK_URL` for `routing_node` (each independently optional; absent = graceful no-op,
same pattern as everything else — see `docs/ACAM_roadmap.md` §11.4 item 5), and optionally
`DATABASE_URL` (Supabase Postgres connection string — enables `PostgresSaver` for the checkpointer
and `vector_store.py`'s pgvector-backed RAG; absent = `SqliteSaver` + in-memory embedding cache,
exactly as before this migration — see `docs/ACAM_roadmap.md` §11.1/§11.2), and optionally
`HUBSPOT_ACCESS_TOKEN` (private-app token for [hubspot.py](aca/integrations/hubspot.py); absent =
`action_node` writes to Sheets only, graceful no-op, same pattern as everything else) with optional
`HUBSPOT_PIPELINE` / `HUBSPOT_DEALSTAGE` overrides (default `"default"` / `"appointmentscheduled"`,
present in every fresh HubSpot portal).

`credentials/` (gitignored) holds `service_account.json` (Sheets) and `gmail_credentials.json` (Gmail
OAuth client secret, "installed app" type). `gmail_token.json` is created there on first Gmail auth.

### Gmail setup notes

`gmail_credentials.json` is an OAuth "installed app" client secret, not a service account — the first
call to `get_gmail_service()` opens a real browser window for the account owner to grant consent
(scope: `gmail.modify`). This can't be done headlessly; run `python -m aca.integrations.gmail_reader` or click "Rechercher
les e-mails non lus" in the UI once locally to complete it. The resulting token is cached and reused
afterward.

## Known gaps

- ✅ **Fixed (2026-07-12)**: automated test suite now exists — `tests/` (92 tests, pytest, run via
  `python -m pytest tests/`, ~2s, fully offline). `tests/conftest.py` blanks every external-service
  env var *before* any `aca.*` import (`load_dotenv` never overrides pre-set vars, so the real
  `.env` stays inert) and redirects all SQLite paths to a temp dir — no test ever touches Supabase,
  Sheets, Gmail, Groq, or the real `data/*.sqlite`. Coverage: graph nodes unit-tested with fake
  LLMs (classifier fallback, supervisor guardrails, reflection + anti-loop cap, `_build_rag_query`
  branches, Calendly append, routing/notification no-ops, `_retry_on`), sheets pure helpers
  (`_rrf_fuse`, `_keyword_candidates`, `_tokenize`, `_row_qr`, `_cosine_similarity`), all four
  storage modules on tmp DBs, graceful-degradation contracts (notify/hubspot/enrichment/veille/
  attachment_reader incl. synthetic PDF+docx+xlsx and global truncation), and 5 full-graph
  integration tests through the compiled `app` (pause before `action`, resume-after-validation CRM
  write, reflection rewrite capped at one, SPAM skips workers/notification, veille guardrail with
  the de-contextualized query). The `__main__` mock run + ad-hoc `AppTest` scripts remain as
  complementary live checks.
- `poller.py` and `ui.py` are separate processes that can open `data/checkpoints.sqlite`/`data/queue.sqlite`
  concurrently. `RETRY_POLICY` (✅ done, item 9) retries transient errors inside every graph node —
  including checkpointer reads/writes during `app.invoke()`. ✅ **Fixed (2026-07-12)**: the standalone
  SQLite writes outside the graph (`queue_store.py`, `analytics_store.py`, `audit_log.py`,
  `followup_store.py`) are now wrapped too — [sqlite_retry.py](aca/storage/sqlite_retry.py)'s
  `with_sqlite_retry` decorator (3 attempts, linear backoff, retries only `sqlite3.OperationalError`
  — a lock conflict — never a programming error) is applied to every public function of all four
  storage modules. Verified with tests that simulate a transient lock (succeeds on the 3rd try),
  a persistent lock (raises after `MAX_ATTEMPTS`), and confirm non-lock exceptions propagate
  immediately without retrying.
  `PROCESSED_LABEL_NAME`/`gmail.modify` mean the poller never deletes anything.
- ✅ **Fixed (2026-07-12, live-verified)**: P0 item 2 (Slack/e-mail notification) — `SLACK_WEBHOOK_URL`
  is now set in `.env` (incoming webhook to `#nouveau-canal`, "acam" workspace) and
  `python -m aca.integrations.notify` delivered a real message to the channel.
- ✅ **Fixed (2026-07-12, live-verified)**: P0 item 5 (routing `SUPPORT`/`AUTRE`) — the Slack-alert
  path was exercised live: `SUPPORT_SLACK_WEBHOOK_URL` is set (currently the **same** webhook/channel
  as the generic one — split into dedicated support/HR channels later) and a `routing_node` call with
  a SUPPORT state delivered a real alert. Still pending: real `SUPPORT_EMAIL`/`HR_EMAIL` addresses
  (commented out in `.env`), which also gate the Gmail forward-draft branch.
- ✅ **Fixed (2026-07-12, live-verified)**: `TAVILY_API_KEY` is now set in `.env`. Enrichment agent
  exercised live end-to-end: first call for a real corporate domain (doctolib.fr) hit Tavily and
  cached the profile in `Enrichissement_Cache`; second call was served from the cache without a
  Tavily call. `veille` exercised live too: Tavily answer → Groq Q/R formatting → staged FAQ row
  (`à valider`, invisible to the RAG) → test row deleted after verification (clean round-trip).
- The clarification trigger is "empty `besoin_principal`"; the 70B extractor usually fills it, so
  clarification fires only on genuinely vague emails (by design).
- ✅ **Fixed**: `search_knowledge_base_semantic`'s similarity cutoff was too permissive against the
  original 2-row FAQ seed (unrelated queries like "recette de tarte aux pommes" scored above the old
  `0.5` threshold and "matched", so `faq_context` was effectively never empty and `veille` almost never
  fired). Fixed by (1) growing the FAQ to 74 realistic Q/R pairs across 10 business categories
  (pricing, features, security/GDPR, support/SLA, integrations, onboarding, demo/trial, accounts,
  contracts, platform) — a 2-row FAQ can't reveal a real score distribution — and (2) empirically
  measuring real Gemini embedding similarity on this larger set: paraphrased-but-relevant queries
  scored 0.73–0.80, genuinely irrelevant queries scored 0.56–0.61, a clean gap. New threshold: `0.65`
  similarity (in-memory path, `sheets.py`) / `0.35` distance (pgvector path, `vector_store.search()`,
  `distance = 1 - similarity`). Live-verified via `search_knowledge_base_semantic()`: relevant
  paraphrases still return good matches, irrelevant queries now correctly return `""` (which triggers
  `veille` as designed). `scripts/setup_faq.py` updated to seed the full 74-pair set instead of the
  old 2 rows. See `docs/PROJECT_JOURNAL.md` (2026-07-11 entry) for the full calibration numbers.
- ✅ **Fixed**: `veille` used to write unverified web content straight into the FAQ tab. It now stages
  it (`statut="à valider"`), invisible to the RAG until approved from the Streamlit sidebar. Gmail
  drafting after "Valider" (`create_draft_reply`) was added the same session — see `docs/ACAM_roadmap.md`
  §11.4 items 1 and 6, both now done.
- ✅ **Fixed**: `DATABASE_URL` (Supabase) is now set and live-verified. Note: the direct connection
  host (`db.<ref>.supabase.co`) is IPv6-only and failed to resolve on this network — fixed by using
  Supabase's **Session pooler** connection string instead (`postgres.<ref>@aws-0-<region>.pooler.
  supabase.com:5432`, IPv4-compatible, still free). Also fixed during verification: `pgvector`'s
  psycopg adapter only auto-converts `numpy.ndarray`/its own `Vector` class, not plain Python lists
  (what Gemini's embedding API returns) — passing a raw list produced a Postgres `double precision[]`
  array instead of `vector`, and the `<=>` operator has no overload for `vector <=> double
  precision[]`. Fixed by wrapping vectors in `pgvector.Vector(...)` before binding as query
  parameters in both `sync_embeddings()` and `search()`. Verified live: `vector_store.search()`
  returns byte-identical results to the old in-memory path for the same query; a checkpoint written
  by one process (`PostgresSaver`) was read back correctly from a completely separate process
  (the actual problem this migration solves, vs. the old per-process SQLite/in-memory cache); full
  `python -m aca.core.app` mock suite (5 cases) ran clean against the Supabase-backed checkpointer + RAG.
- ✅ **Fixed (2026-07-11)**: despite the above, real runs (`app.py`/`poller.py`/`ui.py`) were
  **silently never using pgvector for the RAG** since the migration — only the checkpointer
  (`PostgresSaver`) was genuinely Postgres-backed in production. Root cause: `sheets.py` did
  `from . import vector_store` *before* calling its own `load_dotenv()`; `vector_store.py` read
  `DATABASE_URL` into a module-level constant at import time, so it froze to `""` for the rest of
  the process the moment `sheets.py` (hence `vector_store.py`) was imported anywhere before any
  `load_dotenv()` call had run — which is every real entry point (`aca/core/app.py` also calls
  `load_dotenv()` only *after* its own `from aca.integrations import sheets`). `vector_store.is_enabled()`
  silently returned `False`, so `search_knowledge_base_semantic()` fell through to the in-memory
  per-process embedding cache — functionally correct (real Gemini embeddings, real cosine similarity)
  but not shared across processes, exactly the problem the migration was meant to solve. No exception
  was ever raised, so nothing looked broken. Confirmed with a direct query: `faq_embeddings` in
  Supabase held only 2 stale rows (leftover from the original isolated verification script above,
  which happened to call `load_dotenv()` before importing `sheets`) while Sheets already had 74.
  Fixed two ways: (1) reordered `sheets.py` to call `load_dotenv()` before importing `vector_store`;
  (2) made `vector_store.py` read `os.getenv("DATABASE_URL")` dynamically in `is_enabled()`/
  `_get_pool()` instead of freezing it at import time, so the bug class can't recur regardless of
  caller import order. Re-verified live: `vector_store.is_enabled()` now `True` on a fresh
  `aca.core.app` import, `faq_embeddings` now holds all 74 real rows, full mock suite + classifier
  eval re-run clean with no `⚠️ Échec pgvector` fallback warnings. See `docs/PROJECT_JOURNAL.md`
  (2026-07-11 entry) for the full investigation.
- ✅ **Fixed (2026-07-12)**: `hubspot.py`'s live write-path test (contact + deal + note + associations
  against the real portal) crashed with `UnicodeEncodeError` on `print(f"→ ...")` under this Windows
  shell's cp1252 console — **after** the HubSpot write had already succeeded. Because the crash
  happened before `return deal_id`, it propagated out of `create_lead()` as an uncaught exception; had
  this happened inside `action_node` (which is `RETRY_POLICY`-wrapped, `max_attempts=3`), LangGraph
  would have retried the whole node, re-running `sheets.append_lead()` **and** `hubspot.create_lead()`
  — duplicating the lead in both systems. Two duplicate test deals were in fact created this way
  during verification (confirmed via `deals/search`, then deleted via `DELETE
  /crm/v3/objects/deals/{id}`, recoverable from HubSpot's recycle bin for 90 days). Fixed by
  restructuring `create_lead()` so `return deal_id` never depends on a print succeeding — the
  success/failure print statements are now individually try/excepted with an ASCII-only fallback.
  Re-verified live: a fresh test lead was created cleanly (exit code 0, no exception) under the same
  shell that crashed before the fix, then deleted as cleanup.

## Status vs. the 8-week roadmap

The linear ACAM v1 (hybrid memory, semantic RAG, `AUTRE` taxonomy, live validate-loop) is done and was
verified end-to-end. **ACAM v2** (this multi-agent supervisor + team) is implemented and verified per
phase: document ingestion → Sheets, supervisor + enrichissement/connaissance/stratège, reasoning trace,
and interactive clarification (dynamic `interrupt`). See `docs/ACAM_roadmap.md`. A fourth worker, `veille`
(web search that self-enriches the FAQ tab when `connaissance` finds nothing), was added afterward —
wired into the graph and verified not to loop/crash, though its trigger condition rarely fires against
the current 2-row FAQ seed data (see Known gaps). **All six §11.4 P0 production gaps are now
closed**: Gmail draft-reply after "Valider" (item 1, live-verified), background intake via
`poller.py` + the "File d'attente" sidebar (item 3, live-verified without touching the real CRM),
`SqliteSaver` persistence surviving a simulated restart (item 4, live-verified), human-facing
notification via `notify.py` (item 2, coded + graceful-fallback path verified, not yet exercised
against a real Slack/e-mail destination), human staging/approval for `veille`'s web content
(item 6, live-verified), and routing `SUPPORT`/`AUTRE` via `routing_node` (item 5, coded +
graceful-fallback path verified — `SUPPORT` now bypasses Stratège/CRM like SPAM/AUTRE, alert +
Gmail forward draft ready as soon as real `SUPPORT_EMAIL`/`HR_EMAIL` destinations are configured).
Also done this session: `format_sheets.py` visual polish
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
present on the `DEMANDE_DEMO` mock case, absent on `DEVIS`). **All 7 §11.4 P1 items are now done,
and so are all 6 §11.4 P0 items.** Also done from P2: item 16 (multi-attachment) —
`gmail_reader.py` now collects every PDF/Word/Excel attachment instead of just the first PDF, and
the new [attachment_reader.py](aca/ingestion/attachment_reader.py) extracts/concatenates all of them (capped once
at `MAX_CHARS`, not per file); verified with a synthetic PDF+docx+xlsx test (all three extracted,
an unsupported extension silently skipped) and a full `python -m aca.core.app` regression run — and item 17
(dashboard) — new [analytics_store.py](aca/storage/analytics_store.py) event log + a "Tableau de bord" tab in
`ui.py` (KPIs, volume by category, daily trend, conversion funnel, response-time detail). Remaining:
verify the dashboard against a real multi-day run (only synthetic/manual data exercised so far).
Also done from P2, ahead of schedule at the user's explicit request rather than waiting for the
§11.1 volume triggers: item 14 (Supabase/pgvector) — [vector_store.py](aca/integrations/vector_store.py) +
`PostgresSaver` in `app.py`, gated entirely behind `DATABASE_URL` — **live-verified end-to-end**:
`vector_store.search()` matches the old in-memory RAG path exactly, a checkpoint written by one
process is correctly read back from a separate process, and the full `python -m aca.core.app` mock suite
passes against the Supabase-backed checkpointer + RAG (see Known gaps for the two real bugs found
and fixed during verification: the IPv6-only direct-connection host, and pgvector's psycopg adapter
not handling plain Python lists). Also done: the RAG went from a single similarity cutoff to a hybrid
dense+sparse RRF fusion with a dual "amber zone" confidence threshold (0.72/0.62, superseding the
earlier single 0.65 cutoff — see `connaissance_node` above and Known gaps). `notify.py`,
`routing_node` (Slack branch), the enrichment agent, and `veille` are now **live-verified** against
real Tavily + Slack credentials (2026-07-12 — see Known gaps). Remaining: real
`SUPPORT_EMAIL`/`HR_EMAIL` addresses (gate the Gmail forward-draft branch of `routing_node`),
`relance.py` against a real Gmail thread with a reply, the dashboard on real multi-day data,
multi-tenant, and the eventual n8n port (design already n8n-ready). Also done from P2, ahead of schedule at the user's
explicit request: item — real CRM (§11.1 mentions HubSpot as the eventual target) —
[hubspot.py](aca/integrations/hubspot.py), wired into `action_node` **alongside** Sheets (Sheets stays
the memory read by `find_leads_by_sender`/the dashboard; decided over fully replacing it, to avoid
porting duplicate-detection and the dashboard's lead-based views in the same change — see Known gaps
for a bug the live verification caught and fixed). Also done, from an external AI-generated
architecture review the user asked to be checked against the real codebase: a `reflection_node`
self-critique loop after `stratege` (Llama-8B re-reads the draft against the FAQ context it actually
used, capped at one rewrite to avoid an infinite loop — live-verified: caught a real redundant claim
on a test email, rewrote once, then stopped), and query de-contextualization (`_build_rag_query()`,
shared by `connaissance_node`/`veille_node`) — the RAG query now comes from the already-extracted
`besoin_principal` instead of the raw email, with an LLM rewrite step for returning customers whose
question implicitly references a prior exchange (live-verified: "et pour cette option-là ?" against
a synthetic prior-order history correctly resolved into a standalone query). The review's other
suggestions (row-hash Sheets↔Supabase sync, an Apps Script webhook, category metadata pre-filtering,
a few-shot "approved interactions" table) were evaluated and intentionally not built: the row-hash/
sync idea is already covered by the existing content-signature cache invalidation, and the rest are
net-new features rather than refinements, out of scope unless separately requested. The forward-
looking backlog now lives in `docs/ACAM_roadmap.md` §11.6 (remaining core technical debt — test
suite, live-credential exercises, out-of-graph SQLite retries, §10 leftovers) and §12 (P3
commercialization/SaaS items from a second external AI review, each audited against the code with a
verified done/partial/todo status — to be started only after §11.6).

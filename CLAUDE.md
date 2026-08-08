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
START → classifier (8B) → memory_lookup → risk_scan (RegEx) → extractor (70B) → clarification (❓dynamic interrupt)
      → SUPERVISOR (8B) ⇄ workers ──FINISH── routing ── notification ── interrupt ── action → END
                        ├─ enrichissement (Tavily + Sheets cache → company_profile)
                        ├─ connaissance   (hybrid RAG, dense+sparse RRF fusion → faq_context)
                        ├─ veille         (Tavily fallback if FAQ empty → enriches FAQ tab)
                        └─ stratege       (70B, temp 0.3 → proposition/devis)
                              ↓
                          reflection (8B self-critique) ──rewrite (1x max)──→ back to stratege
                              └──ok──────────────────────────────────────────→ routing
```

- `classifier_node` — labels the email `DEMANDE_DEMO | DEVIS | SUPPORT | AUTRE | SPAM` **and** a
  0-1 confidence score via `with_structured_output(ClassificationResult)` (Pydantic `Literal` +
  `Field(ge=0, le=1)` — tool-calling under the hood, so an out-of-enum category is rejected by the
  schema itself rather than checked manually after the fact). Below
  `CLASSIFICATION_CONFIDENCE_THRESHOLD` (0.6), `notification_node` alerts a human **even for**
  `SPAM`/`AUTRE`/`SUPPORT` (see below) — those categories normally skip validation entirely, so an
  unreliable classification there is the riskiest case to let slide silently. `CATEGORIES_SANS_SUITE`
  (`{SPAM, AUTRE, SUPPORT}`) — none of these three are sales leads, so none reach `stratege`/the CRM;
  `SUPPORT`/`AUTRE` are instead handled by `routing_node` below. No local try/except around the LLM
  call — a schema/API failure propagates to the graph's `RETRY_POLICY` (3 attempts) rather than
  being swallowed on the first try, which would silently defeat the retry (see extractor_node below
  for the same reasoning, discovered together via live testing).
- `memory_lookup_node` — **long-term memory read**: `sheets.find_leads_by_sender()` fills
  `sender_history` + `is_duplicate` from the "Leads" tab. No LLM.
- `risk_scan_node` — **deterministic risk scan** (§13, audit of the "ACAM v2 Blueprint" PDFs):
  [risk_scan.py](aca/core/risk_scan.py)'s `scan_risks()` (bilingual FR/EN, accent/case-insensitive
  regexes — unlimited liability, late penalties, non-compete clause, bank guarantee, etc.) scans
  the subject + body + `attachment_text` for contractual red flags into `risk_flags`. No LLM/API
  call, so **no** `RETRY_POLICY` on this node (nothing external to retry). `risk_flags` feeds
  `stratege_node` (refuses to commit on flagged clauses, defers to legal/management) and
  `notification_node` (prepended to the alert).
- `extractor_node` — extracts `{entreprise, contact, urgence, besoin_principal}` via `with_structured_output(ExtractedInfo)`
  (Pydantic model, tool-calling under the hood — no manual `json.loads()`, no malformed-JSON risk,
  no more `{"raw": ...}` fallback that nothing downstream read). Deliberately **no** local
  try/except around the `.invoke()` call: an earlier version caught every exception there to
  return an empty `ExtractedInfo()` on failure, which live testing caught as a real regression — it
  swallowed genuinely transient errors (429/5xx/network) *before* the graph's `RETRY_POLICY` ever
  got a chance to retry them, since the node "succeeded" (with a degraded result) on the very first
  attempt. Fixed by removing the catch entirely: `with_structured_output()` already eliminates the
  malformed-JSON failure mode the old fallback existed for, so any remaining failure is either
  transient (→ let `RETRY_POLICY` retry the whole node) or a genuine schema rejection at
  temperature 0 (→ retrying won't change the outcome anyway, so there's nothing a local catch would
  usefully add). If `RETRY_POLICY` exhausts all 3 attempts, `app.invoke()` raises — the same
  resilience boundary every other LLM-calling node in this graph already has.
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
  If Tavily also comes back empty (neither `connaissance` nor `veille` found anything), sets
  `knowledge_gap=True` (§13, audit of the "ACAM v2 Blueprint" PDFs — a lighter, human-gated version
  of their "[UNANSWERED GAP]" concept, deliberately without a hard similarity-threshold block —
  see `connaissance_node` above for why a fixed 0.85 gate would be wrong on this stack's embedding
  model) — consumed by `stratege_node` (answers honestly instead of inventing specifics) and
  `notification_node` (surfaces the unanswered question to a human).
- `stratege_node` — **Llama-70B** proposal writer: personalized reply + indicative quote + next action,
  using `company_profile` + `faq_context` + `sender_history` + `extracted_info` (+ `reflection_feedback`
  when `reflection_node` sent it back for a rewrite). Always the last worker. For `DEMANDE_DEMO`,
  appends the real Calendly link (`CALENDLY_URL`) to the draft **deterministically in code** (not
  LLM-generated, to avoid a mangled URL) — absent = graceful no-op, draft unchanged (vague promise,
  as before). If `risk_flags` is non-empty (§13, `risk_scan_node`), the prompt is told to refuse
  committing on any flagged clause and defer to legal/management instead. If `knowledge_gap` is
  set (§13, `veille_node`), the prompt is told to answer honestly and never invent a price/deadline/
  feature that isn't backed by `faq_context`.
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
  neither is configured. When present, `risk_flags` (§13) is prepended to the alert and
  `knowledge_gap` (§13) appends the unanswered `besoin_principal` — both surfaced in the alert
  itself, not just the `reasoning_log` shown later in the UI.
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
  `classification_confidence` (0-1 score from `classifier_node`, read by `notification_node`),
  `risk_flags` (§13, list of contractual red-flag labels from `risk_scan_node`), `knowledge_gap`
  (§13, bool set by `veille_node` when no answer was found anywhere), `gmail_thread_id` (real Gmail
  thread, read by `ui.py` after validation for `relance.py` — no node touches it, it just rides
  along in the state), `attachments_raw` (§11.6, raw `[(filename, bytes), ...]` pairs consumed by
  the new `ingestion_node` — see below), and reducer lists `completed_agents`/`reasoning_log`), the
  `ingestion_node` (§11.6 item 4 — extracts `attachment_text` from `attachments_raw` at the very
  start of the graph via `attachment_reader.extract_text_from_attachments()`; this used to live
  outside the graph, duplicated in `ui.py`/`poller.py`, each of which had to call it before
  `app.invoke()` — a real graph node centralizes it once and inherits `RETRY_POLICY` for free), the
  classifier/memory/risk_scan/extractor/clarification nodes, the `supervisor_node` + four worker
  agents (`enrichissement`/`connaissance`/`veille`/`stratege`), `reflection_node` (self-critique
  after `stratege`), `action_node`, `sum_usage()` (§13, aggregates a `UsageMetadataCallbackHandler`'s
  per-model token counts — consumed by `ui.py`/`poller.py`/`aca/api.py`), `_calendly_url()`/
  `_routing_destinations()` (§12 item 7 — read `config_store` first, falling back to the
  `CALENDLY_URL`/`SUPPORT_EMAIL`/etc. `.env` defaults, so the Streamlit "Réglages" panel takes
  effect without a restart), `snapshot_from_state(state, thread_id)` (§16.1.2 — the single "client
  view" of a graph state, shared by `api._snapshot()` and the outbound webhooks so a REST client and
  a webhook subscriber can never see two different shapes of the same lead; pure, so it is callable
  from inside a node, and it deliberately includes `risk_flags`/`injection_flags` — precisely what a
  human must see before validating, including when validating from n8n or Slack), the demo-mode
  substitution in the three LLM factories (§16.3) and `demo.guard_write()` in `action_node`, and a
  `__main__` block with 6 mock emails (incl. `AUTRE`, `SUPPORT`, and one with a contractual risk
  clause + an out-of-FAQ question to exercise `risk_flags`/`knowledge_gap`) that run through the
  interrupt without a CRM write (`python -m aca.core.app`).
- [risk_scan.py](aca/core/risk_scan.py) — §13: `scan_risks(text) -> list[str]`, pure/deterministic
  (bilingual FR/EN regexes, accent/case-insensitive via `unicodedata` normalization) for
  `risk_scan_node`. No LLM, no external call, no `RETRY_POLICY` needed.
- [device_trust.py](aca/core/device_trust.py) — §24: **"remember this device"**, the bounded TOTP
  bypass. TOTP was required of every `admin` at *every* login, forever; on a personal machine used
  several times a day that friction's usual outcome is not more security but a homemade workaround
  (a shared secret, one authenticator app for the whole team, or switching it off) — a protection
  people abandon protects nothing. **What is actually traded, stated plainly**: a remembered device
  skips **the code, and only the code**. The password is still required every time and no session is
  extended (`session.py` answers a different question), so for that browser and that window the
  account moves from "password + TOTP" to "password + possession of a token held by this browser".
  That is one factor fewer than ideal, and it is what the checkbox *means* — the trade is chosen,
  not imposed. **Deliberately not the `(IP, user-agent)` fingerprint** already computed by
  `activity_log.device_fingerprint()`: it is not a secret — two people behind one office NAT on the
  same browser produce the same value, so anyone with the password on that network would skip the
  second factor. A real 256-bit random token was reachable instead, established **by experiment
  rather than from the docs**: a scratch page proved a `components.html` iframe (`srcdoc`, so it
  inherits the page origin) *can* write the parent's `document.cookie`, and `st.context.cookies`
  reads it back next pass. Only the token's SHA-256 is stored — no salt and no stretching, on
  purpose: 256 bits of entropy have no weakness to compensate, so a database leak replays nothing.
  **Revocation without coupling**: each record carries `auth_fingerprint(password_hash, totp_secret)`
  as of issue time, so changing the password *or* resetting TOTP invalidates every remembered device
  automatically, with `user_store` never needing to know this module exists (Django's
  `session_auth_hash` technique) — a revocation wired by an explicit call is one you forget to wire
  the day a third password-change path appears. Expiry is judged **server-side**; the cookie's
  `max-age` is a courtesy to the browser, editable by whoever holds the machine. `user_agent_hash()`
  is a free extra notch against replay (a cookie presented by another browser simply re-asks for the
  code; the known, accepted false positive is a browser update costing one extra code).
  `cookie_script()` adds `Secure` **only over HTTPS** — setting it on local HTTP would make the
  browser silently drop the cookie and the checkbox would have no visible effect, the worst kind of
  failure for a convenience feature; `HttpOnly` is unreachable by construction (a cookie written in
  JS is readable in JS), a stated limit rather than a silent one. Pure/stdlib with `now` injected,
  no Streamlit (same posture as `session.py`/`auth_lockout.py`/`totp.py`). `ACA_TOTP_TRUST_DAYS`
  (default 3, `0` disables and removes the checkbox — the only way a strict deployment refuses this
  trade without code changes; a malformed value falls back to 3 rather than disabling silently or
  breaking login).
- [auth_lockout.py](aca/core/auth_lockout.py) — §14 item US-41 (security audit, 2026-07-21):
  `lockout_remaining_seconds()`/`next_lockout_seconds()`, pure functions backing a progressive
  lockout on `ui.py`'s optional password gate (`_check_auth()`) — exponential backoff (30s, 60s,
  120s..., capped at 15 min) after 5 failed attempts, stored in `st.session_state`. Fixes a real
  gap found during the audit: the gate previously compared the password with no attempt limit at
  all, so a bot could brute-force `ACA_UI_PASSWORD` without any throttle.
- [session.py](aca/core/session.py) — §15.1.7: session lifetime. Pure, Streamlit-free logic (same
  stance as `auth_lockout.py`) enforcing an **absolute TTL** (`ACA_SESSION_TTL_SECONDS`, 8h) *and*
  an **idle timeout** (`ACA_SESSION_IDLE_SECONDS`, 30min), strictest bound winning. `touch()`
  pushes back idleness but deliberately never `started_at` — otherwise a stolen-but-kept-active
  session never dies. Before this, `st.session_state.authed = True` stayed valid for as long as the
  browser tab lived. §18 (recap #6) adds `seconds_until_expiry(session, now)` — the stricter of the
  two bounds' remaining time, or `None` if both are disabled — consumed by
  `aca/ui/shared.py::_warn_before_expiry()` to `st.toast()` a warning in the last 5 minutes (a
  brouillon lost to a silent expiry is the small frustration that discredits a tool). Computed
  **before** `touch()`, not after — `touch()` always resets the idle clock to its full window, so
  checking afterward would make the idle-based warning permanently unreachable (the absolute-TTL
  bound is unaffected by `touch()` either way).
- [prod_check.py](aca/core/prod_check.py) — §15.1.5/§15.3.3: startup security-posture check. The
  whole project is built on graceful degradation ("absent = feature skipped"), the right default
  locally and exactly wrong on a public host, where it becomes "absent = exposed". `ACA_ENV` is the
  explicit switch: unset/`development` ⇒ no checks at all (unchanged behaviour, tests included);
  `production` ⇒ `enforce()` **refuses to start** when `ACA_API_KEY`, a UI gate, `ACA_RATE_LIMIT`
  or `ACA_METRICS_TOKEN` is missing. `check()` never raises — it backs `ui.py`'s admin-only banner
  and `python -m aca.core.prod_check`.
- [prompt_guard.py](aca/core/prompt_guard.py) — §15.1.4: deterministic bilingual detection of
  prompt-injection attempts (`scan_injection()`), called by `risk_scan_node` over the same
  subject+body+attachment text as `risk_scan`, into a **separate** `injection_flags` list. Kept
  separate on purpose: a contractual clause means "have legal review this", an injection means
  "distrust this draft" — merging them would hand "ignore previous instructions" to `stratege_node`
  as a clause to escalate to management. **Flags, never blocks**: the human gate
  (`interrupt_before=["action"]`) remains the actual protection; this only makes it informed, since
  an instruction buried on page 14 of an RFP previously surfaced in the draft as one more plausible
  sentence. No LLM (asking a model to detect model-manipulation exposes it to that manipulation).
- [demo.py](aca/core/demo.py) — §16.3: `ACA_DEMO_MODE=1` runs the whole project **with no API key at
  all**. Trying ACA used to require five external accounts (Groq, Gemini, Tavily, a Google service
  account, a Gmail OAuth client), so an evaluating company could read the code but never run it.
  `DemoLLM` (deterministic, same `invoke`/`with_structured_output` interface as `ChatGroq`) is
  substituted in `app.py`'s three factories — one switch point, so a node added later inherits it
  for free — and **the graph stays the real one**: same nodes, same supervisor, same self-critique,
  same validation pause. Only the billable call is simulated. `guard_write()` is the deliberate
  exception to the project's graceful degradation: it **raises** in `action_node` rather than
  no-op'ing, because "absent = skipped" is the right default for an optional feature and the wrong
  one for a safety barrier — writing a fake lead into a prospect's CRM during a demo is an
  incident, a loud failure isn't. Also ships `DEMO_EMAILS` (the 6 cases from `app.py`'s `__main__`,
  now reachable from the UI) and `DEMO_FAQ_CONTEXT`. Distinct from `tests/conftest.py`'s `FakeLLM`,
  which lives in the test tree and is excluded from the Docker image.
- [scheduler.py](aca/core/scheduler.py) — §16.0: the one **real** gap the Solo-tier audit found.
  `relance.py`, `retention.py` and `billing.py` were each written, tested and documented "schedule
  this periodically" — but **nothing scheduled them**: no scheduling dependency in
  `requirements.txt`, and a Windows dev machine with no `cron`. In practice the GDPR purge and the
  sales follow-ups only ran if a human remembered the command, i.e. never. Declarative `JOBS` table
  (adding a periodic job = one entry, same spirit as `ROUTING_DESTINATIONS`), per-job interval env
  var where `0` **disables** the job, and no new dependency — a `time.sleep` loop over persisted
  timestamps is ample for four jobs whose most frequent runs hourly. `run_job()` never raises
  toward the loop (same contract as `poller.run_forever()`). `is_due(job, now)` takes `now`
  injected, so it is pure w.r.t. time and testable without touching the clock. CLI: bare (service
  loop), `--once` (cron/n8n), `--job … --force`, `--status`, and `--prime` — which marks never-run
  jobs as just-run **without executing them**, because a never-run job is "due" by construction
  (else a fresh install would never purge), so a first boot would otherwise fire all four at once
  including `relance`, which writes real Gmail drafts. §18 adds a fifth job, `archive` — monthly
  signed export of the activity journal (`activity_log.archive_period()`, `ACA_ARCHIVE_DIR`, default
  `data/archives`), via the pure helper `_last_completed_month(now)` (the **previous** calendar
  month, never the current one, which isn't finished receiving rows yet) — "monthly" describes what
  gets archived, not how often the scheduler checks; `ACA_SCHEDULE_ARCHIVE_HOURS` (default 720, like
  `billing`) only controls the check cadence, and `archive_period()`'s own idempotence absorbs an
  over-eager check. Also logs `ACTION_JOB_RAN` to `activity_log` for **every** job on every tick
  (success or failure) — the piece that answers "did the scheduler even run today", visible even for
  jobs (`maintenance`, `billing`) that have nothing of their own to log.
- [reporting.py](aca/core/reporting.py) — §20: **what happened over a period, and how it differs
  from the one before**. Every figure already existed — `analytics_store` counts emails, `audit_log`
  the validations, `activity_log` each person's gestures, `task_store` the scheduled sends,
  `review_store` the reviews — but only **on screen, in "last N days", and never compared**. Nobody
  could answer "what did this tool bring us in July versus June", which is the only question a
  manager actually asks and the only one that justifies renewing the tool: a dashboard shows a
  state, a report tells a change. **One engine for both features**: the automatic monthly report
  (`scheduler`'s `report` job) and the UI's customisable report are the same `collect(spec)` with
  two specs — `monthly_spec()` is simply frozen (last completed calendar month, `MONTHLY_SECTIONS`,
  comparison on). Two code paths would have guaranteed that one day one of them shows a number the
  other doesn't know. **Collection is strictly separated from rendering**: this module doesn't know
  what a PDF is, it emits typed blocks (`kpis`/`bars`/`line`/`table`/`text`) that
  `report_pdf.py` draws — so content is testable without opening a document, and an HTML or CSV
  export would need no query rewritten. `previous_period()` returns the **same-length** window
  immediately before, not "the previous month": comparing 31 days to 28 would show February down
  10% every year with nothing having happened. `last_completed_month()` never returns the current
  month (same rule and reason as `scheduler._last_completed_month`). Every KPI declares which
  direction is *favourable* (`better`) — a rising median response time is a degradation, and
  painting every increase green would produce a flattering, false report; a 0→3 move reports the
  absolute delta with **no** percentage, since "+100%" would claim growth from no base. `SECTIONS`
  is a declarative registry (adding a section = one entry, same spirit as `ROUTING_DESTINATIONS`
  and scheduler `JOBS`), grouped into four families matching four different readers; each block
  carries a `context` sentence, because a report circulates away from its screen and a number
  without its method of calculation is at best useless and at worst misleading. A section that
  raises is replaced by a text block saying so rather than taking the report down — and rather than
  staying silent, which would read as "nothing happened". `MONTHLY_SECTIONS` excludes the
  email-by-email detail: it can run to dozens of pages and copies prospect addresses into a file
  that will circulate, so it stays a conscious choice in the customisable report. Presets
  (`save_preset`/`list_presets`/`delete_preset`) live in `config_store` under `REPORT_PRESET_<name>`
  like §18's brand profiles, and deliberately store everything **except the dates** — a preset named
  "Monthly management review" describes a *content*, not a month.
- [intake_window.py](aca/core/intake_window.py) — §19: **when automatic intake is allowed to run**.
  `poller.py` looped bare — `time.sleep(POLL_INTERVAL_SECONDS)` and nothing else — with two
  consequences: an email arriving at 3am was analysed at 3am (burning quota and firing an alert for
  a team that would only see it at 9am, and making the analysis *look* stale by the time anyone
  could act), and the interval was read **at import**, so changing it meant editing `.env` and
  restarting. Pure, with `now` always injected (same posture as `auth_lockout.py`/`session.py`/
  `scheduler.is_due`), so it is testable without touching the clock or Gmail. **Naive local time,
  deliberately**: a sales team states its hours as office hours, not UTC, and the process runs on
  that team's machine (Solo tier, `run_solo.py`), so the local clock *is* the right reference.
  Handles the **overnight window** (22:00 → 06:00), which naive `start <= t <= end` silently turns
  into an empty range — the allowed day is the one the window *opens* on, else a Monday-night
  on-call setting would stop dead at midnight. Every parser is deliberately forgiving
  (`parse_days`/`parse_time`/`parse_interval` never raise, falling back to "every day"/default):
  these values come from a form, and a malformed entry that killed the loop would mean **no** email
  ever gets collected — far worse than the bad setting that caused it. Defaults reproduce the exact
  pre-§19 behaviour (always open, 60s), honouring the project's "absent = feature skipped"
  contract. Settings resolve `config_store` → env → default, so the "Réglages → Réception
  automatique" panel takes effect on the next cycle **without a restart**. `describe()` lives here
  rather than in the page because the same sentence appears in the sidebar and in settings, and two
  divergent wordings for one setting make a person doubt which screen tells the truth.
- [task_store.py](aca/storage/task_store.py) — §19: **dated tasks a human placed on a lead** —
  scheduled sends and reminders. Distinct from the two neighbouring registries, and the distinction
  is why it exists: `schedule_store.py` records when a *system* job last ran (no per-lead rows, no
  chosen deadline, nothing to cancel); `followup_store.py` tracks leads eligible for the
  *automatic* `relance.py` cadence (the machine picks the moment; no free text, no chosen hour).
  This stores what a **person decided**, for **one lead**, at **a moment they chose** — a different
  business object with its own lifecycle (cancellable, traceable, named author). Two kinds in one
  table on purpose: a scheduled send and a reminder have the same shape and lifecycle, and two
  tables would mean two purges, two lists, and a second "what's overdue" query that eventually
  diverges from the first. **The scheduled send does not weaken the product's central guarantee**:
  the person read the draft, corrected it if needed, then explicitly said "leave at 9am" — the
  human authorisation exists, it is merely *prior* to execution, exactly like any mail client's
  send-later. What is scheduled is the **already-created Gmail draft** (`gmail_draft_id`), so if
  they edit or delete it in Gmail before the deadline, their version goes out or nothing does; the
  human keeps the last word (see `gmail_reader.send_draft`). `_set_status` carries
  `status = 'pending'` **inside the SQL clause**, so it is atomic: without it a slow scheduler and
  a simultaneous human cancellation could cross, and a cancelled task would flip to "done" — an
  email going out after someone explicitly stopped it. `purge_older_than` deliberately spares
  *pending* tasks regardless of age, since a distant deadline is still a valid intention.
- [review_store.py](aca/storage/review_store.py) — §20: **review requests** — an operator hands an
  admin specific emails they don't want to decide alone, several in one gesture
  (`data/reviews.sqlite`, `ACA_REVIEW_DB`). The gap it closes: an operator facing a lead beyond
  their authority had exactly three options — validate (writes to the CRM), reject (removes the lead
  from *everyone's* queue), or do nothing and tell their manager through some other channel, where
  the information leaves the product and stops being traceable. The third gesture — "I'm not
  deciding this, someone must look" — did not exist. **Deliberately a separate registry from
  `task_store.py`**, and the distinction is the module's reason to exist (the same reasoning that
  separated `task_store` from `followup_store`/`schedule_store` at §19): a task is **dated** (the
  scheduler fires it, `list_due()` queries a clock) and ends by itself; a review request is
  **addressed** (the recipient's login surfaces it) and ends with a *human decision* that can carry
  a written answer (`resolution_note`). The query is not "what's overdue" but "what's waiting for
  me". Merging them would force `list_due` to exclude this kind, the scheduler to skip it and the
  purge to treat it apart — three exceptions at the heart of a table whose docstring praises its
  uniformity. **A batch, not one-at-a-time**: one row per email (each is resolved and commented
  individually) but all rows of one gesture share a `batch_id`, note and priority, so the recipient
  reads "Marie sent you 4 emails" and can answer the lot with `resolve_batch()` without losing
  per-email granularity. **`RECIPIENT_ADMINS` (`@admins`) is resolved at read time, not write
  time** — an admin hired tomorrow must see what was addressed to the *role* yesterday; expanding it
  into one row per existing admin at send time would have frozen that list. `mark_seen()` never
  touches `status`: "seen" and "resolved" are distinct events (same reasoning as
  `task_store.acknowledged_at`), otherwise a request opened then set aside would vanish from the
  queue with nobody having handled it. `_close()` carries `status = 'pending'` **inside the SQL**,
  so two admins opening the same queue can't overwrite each other's answer. Email metadata
  (subject/sender/classification) is **copied** into the request rather than referenced, so it stays
  readable after the lead is purged by GDPR retention — a request whose subject evaporates can
  neither be understood nor closed. `cancel_for_thread()` (wired into `retention.purge_subject`) and
  `purge_older_than()` (spares *pending* requests at any age — an un-triaged review is outstanding
  work, and erasing it would delete the trace along with the data).
- [schedule_store.py](aca/storage/schedule_store.py) — §16.0: when each periodic job last ran
  (`data/schedule.sqlite`, `ACA_SCHEDULE_DB`). Without it `scheduler.py` would replay every job on
  each process restart — a retention purge and a burst of Gmail follow-up drafts on every
  `docker compose up`. Same shape as `config_store.py`: org-scoped, `sqlite_retry`-wrapped (the
  scheduler writes outside the graph, hence outside `RETRY_POLICY`). Not to be confused with
  `config_store.py`, which stores what a human **set**; this stores what the machine **did**. The
  timestamp is stored twice on purpose: `last_run_epoch` (REAL) for the due-date arithmetic, no
  timezone or format question; `last_run_at` (TEXT) purely so a human inspecting the DB can see
  why a job didn't fire. `record_run()` records failures too — otherwise a durably broken service
  (Gmail unreachable, quota exhausted) would be retried every tick, turning an outage into a
  hammering.
- [graph_topology.py](aca/core/graph_topology.py) — §16.1.6: **single source** for the agent graph's
  topology, derived from the compiled graph itself (`app.get_graph()`). §12bis had flagged the
  hand-copied edge lists in `app.py`/`ui.py`/`dashboard/lib/graph-topology.ts` as a drift risk —
  and **the drift had already happened**: `ui.py` was missing the `supervisor → routing` edge (the
  supervisor's FINISH path), so the diagram shown to the user depicted a supervisor with no exit to
  the rest of the pipeline, and nothing could catch it because nothing compared the two lists.
  Adding a node in `app.py` now surfaces it everywhere; only its French label in `NODE_LABELS` is
  hand-maintained (and a test fails if it's forgotten). Exposes `nodes()`/`edges()`/`to_dot()`
  (consumed by `ui.py`'s live `st.graphviz_chart`) and `to_dict()` (consumed by
  `scripts/export_graph.py`). No Streamlit import in `aca/core/`.
- [console.py](aca/core/console.py) — UTF-8-tolerant console output (2026-07-26). The project logs
  heavily with emoji (68 `print()` calls across 13 modules); on this Windows box `sys.stdout` is
  **cp1252** as soon as output is redirected (service, log file, backgrounded `uvicorn`), so a bare
  `print("⚡ …")` raises `UnicodeEncodeError`. Not cosmetic: those prints happen **inside graph
  nodes**, all wrapped by `RETRY_POLICY`, so a decorative log line could trigger up to 3 node
  re-executions and — for a writing node — a double CRM write. Exactly the `hubspot.py` incident of
  2026-07-12, whose local two-`print()` try/except fix doesn't scale to 68 calls. Fixed at the
  **process boundary** instead: `enable_utf8_console()` reconfigures `stdout`/`stderr` once with
  `errors="replace"` (no character can ever make a `print()` raise again — worst case it renders
  `?`), is idempotent, and never raises if the stream can't be reconfigured (pytest capture,
  `StringIO`). Called from `aca/__init__.py`, so every entry point inherits it.
- [branding.py](aca/core/branding.py) — §17: **white-label theming, parametrable at runtime**. The
  look used to live entirely in a static, version-controlled `.streamlit/config.toml`, so shipping
  ACA to a company whose spec mandates its logo and colours meant *modifying the product* per
  client. Appearance is now **data**: 22 `BRAND_*` tokens (name, tagline, company, logo, 10 colours,
  font, radius, density, light/dark, animation level, hero style) resolved on every call —
  `config_store` (per tenant, editable from the UI) → same-named env var (so a Docker image can ship
  already in the client's colours) → selected preset (7 of them) → mode defaults → token default. A
  colour explicitly chosen is never overridden by a preset or by dark mode. **Two layers on
  purpose**: `css()` is injected every rerun (instant effect, carries all animations and the brand
  hero banner) and `config_toml()` rewrites `.streamlit/config.toml` on explicit admin action — the
  only layer that reaches *inside* Streamlit's React components (open dropdown, `st.dataframe`
  header, Vega palette), effective on page reload. This deliberately contradicts the project's
  default doctrine ("never CSS, always config.toml", per the `developing-with-streamlit` skill),
  which remains right for a *fixed* theme: a theme that changes at runtime, per tenant, cannot be a
  static file read at server start. Stated cost: the `data-testid`/`data-baseweb` selectors are
  Streamlit 1.59 implementation details, so a version bump can make the page **less pretty, never
  broken** — every rule is decorative, none gates a feature. Also ships `readable_text_on()`
  (auto black/white button text, so a client picking a vivid yellow doesn't get unreadable buttons —
  that would be *our* defect, not their taste), `accessibility_report()` (WCAG contrast checks that
  **warn but never refuse** — it's the client's brand), `merge_config_toml()` (pure, idempotent,
  preserves every non-`[theme]` section, since clobbering a `[server]` block would break the
  deployment rather than the looks), and `encode_logo()` (512 KB cap — the logo is re-injected on
  *every* rerun, so a 5 MB PNG would slow down each interaction). No Streamlit import (same stance
  as `risk_scan.py`/`session.py`/`graph_topology.py`), so it is fully unit-testable offline. §18
  extends `PRESETS` from 7 to 18 sectoral profiles (industry, healthcare, finance, tech/SaaS, luxury,
  education, food, real estate, energy, logistics, accessibility — each varying font/radius/density/
  animation level, not just colour, on the "a sector has a look, not just a colour" principle), adds
  `saved_profiles()`/`all_profiles()`/`profile_payload()` (an admin can save a custom-tuned palette as
  a named, reusable profile stored in `config_store` under `BRAND_PROFILE_<name>`, logo deliberately
  excluded from the payload to avoid bloating the settings table), and `favicon_for_streamlit()` (the
  client logo as the browser-tab icon via `st.set_page_config(page_icon=...)`; SVG excluded since
  `st.image`/favicon rendering of SVG is unreliable — it remains fine as the sidebar logo via
  `st.logo`). **§18 tangent** (visual-polish follow-up request): the old `.stTabs [data-baseweb=…]`
  rules were dead — `st.tabs()` is no longer called anywhere since the `st.navigation` restructure
  above — replaced with real selectors for Streamlit's **top-nav** internals, found by grepping the
  compiled JS bundle rather than guessing (`data-testid="stTopNavLinkContainer"`/`stTopNavLink`,
  undocumented and version-fragile, same "decorative, never gates a feature" stance as the rest of
  this module): a gradient card around the nav row, a hover lift/glow per link, and staggered
  entrance animations at the `_ANIMATIONS_FULL` level (a lighter `aca-fade` at `_ANIMATIONS_SUBTLE`,
  none at `_ANIMATIONS_OFF` — an OS-level `prefers-reduced-motion` still wins regardless, per the
  existing contract). **This first attempt didn't actually work** — caught only when the user
  reported the bar was still neither visible nor centered and a live Playwright inspection of the
  real rendered DOM (not just the minified bundle) was run against it: `*:has(> […])` matches the
  nearest ancestor with `stTopNavLinkContainer` as a *direct* child, which is a private, per-item
  Streamlit wrapper div (one per page, shrink-to-content, no flex role) — never the actual flex row
  that lays all four links out side by side. Every rule aimed at "the bar" (background, border,
  `margin-inline: auto`/`width: max-content` for centering) was silently landing on four separate,
  nearly-invisible per-item boxes instead of one shared bar, and the stagger animation's
  `:nth-child(N)` was dead too, for the identical reason — a lone child is always its parent's
  `:nth-child(1)`, so all four links always matched the same rule. Fixed by targeting `.rc-overflow`/
  `.rc-overflow-item` instead — the actual flex container and repeated item, both stable classes
  from the third-party `rc-overflow` list library Streamlit uses here (not Streamlit's own hashed
  per-version classes) — confirmed via the real computed `justify-content`/`background`/`box-shadow`
  on the live element before and after. The admin-only
  security-posture banner (`ui.py`, `key="security_banner"`) gets a warm gradient plus a slow
  `aca-warn-glow` pulse — deliberately only at the animated levels, so a client who disabled motion
  doesn't get a wandering hint that something needs attention when nothing changed.
  **§21 (design pass, measured against the live DOM rather than the source)** — five rules were
  written but never rendered, which is why the pass was mostly *making the existing design exist*:
  (1) `h1,h2,h3 { font-family: var(--aca-display) }` is specificity (0,0,1) and lost to Streamlit's
  own `.st-emotion-cache-XXXX h1..h6` (0,1,1), so **no real heading was ever in the display serif** —
  the hero title only worked because `.aca-hero__title` is a class; now anchored on
  `stMarkdownContainer`/`stHeadingWithActionElements`, no `!important`; (2) a stale
  `[data-testid="stHeader"] { background: transparent }` sat 130 lines below §19's opaque-header fix
  and silently undid it (measured `rgba(0,0,0,0)`); (3) `--aca-header-h` was `3.5rem` = 49px under
  `config.toml`'s `baseFontSize = 14`, describing a bar that actually measures 52.5px — now px;
  (4) the **active** nav item was Streamlit's neutral grey while *hover* carried the brand colour,
  lift and shadow, i.e. the hierarchy was inverted — now `aria-current` takes the brand fill and
  hover is deliberately subordinate; (5) `chart_colors()` concatenated the six semantic tokens, but
  nothing makes them distinct (default: `BRAND_INFO == BRAND_PRIMARY`, `BRAND_WARNING ==
  BRAND_ACCENT`), so two dashboard categories drew in the same colour — now de-duplicated and
  topped up with derived tints to 8. Also: `--aca-muted` 0.42 → **0.34** (was 4.2:1, below AA, and
  it is the colour of *small* text — calibrated across all 18 presets after a hand-calibrated 0.38
  passed 4 palettes and failed `Industrie & BTP`); a permanent low card shadow so cards separate
  from the page even when a client picks near-identical surface/background (one shipped palette
  measures 1.01:1); `--aca-ease-out`/`--aca-ease-in-out`/`--aca-t-*` tokens replacing five inline
  curves; every UI animation cut under 300 ms; `:active { scale(.97) }` press feedback; hover gated
  behind `@media (hover: hover) and (pointer: fine)`; `:focus-visible` rings on buttons/links/nav.
  One animation **removed**: the empty-state icon's infinite float — the only perpetual purely
  decorative motion, which competed for attention with the two loops that do signal something.
  One animation **added** on request, and the tension with that removal is the point: the
  **ambient background** (`[data-testid="stAppViewContainer"]::before`, two very soft radial veils
  drifting on a 48s loop). Admissible only because it clears three constraints inherited from
  decisions already made — (1) it borrows `--aca-primary` and **never** `--aca-accent`, since amber
  means "a human must decide" and using it decoratively would empty the signal exactly as the four
  broken palettes did; (2) it is below the attention threshold — 7% alpha, huge radii, no edge, and
  48s is what separates a *material* from a *moving object*; (3) it covers the work surface only —
  the sidebar and header have
  opaque backgrounds and paint over it, so the chrome stays still. Animated with `transform` alone
  so it is GPU-composited (no layout, no repaint) — this is the one loop that never stops, and
  animating `background-position` instead would have repainted the whole page every frame. The
  gradient lives in the **static** block and only the `animation` line is level-gated, so
  "animations: sobre/aucune" keeps the depth and loses only the movement, and
  `prefers-reduced-motion` freezes the loop without erasing the background. The companion rule
  `[data-testid="stMain"], [stSidebar], [stHeader] { position: relative; z-index: 1 }` is
  structural, not decorative: the veil is `position: fixed` in the same stacking context, so without
  it the wash would paint **over** the page. **Corrected after the user reported not seeing it, and
  they were right** — the radii were in `rem`, hence frozen at 588px (14px root from `config.toml`),
  so on a wide screen two small islands floated in a ~2900px layer with one falling outside the
  viewport entirely: measured at 1892px, **6 of 7 background points were untouched** and only the
  bottom-right corner carried colour. The instructive part is *why it passed verification*: the
  earlier "varies by only 5/255" measurement was taken at 1440px on a strip that happened to sit
  near the one visible blob — a true number, sampled at the only flattering place, supporting the
  opposite of the right conclusion. Fixed by sizing in `vmax` (a background decoration scales with
  the **window**, never with the text size), cutting the overflow 25% → 10% (a 2.5% drift needs no
  more, and the excess was pushing the blobs out of frame), and raising the alpha 7% → 14% — a
  judgement fix as much as a code one, since "below the attention threshold" was my criterion while
  the request was *a background animation you can see*, and an effect nobody can perceive is not
  subtle, it is absent. `--aca-veil-1`/`--aca-veil-2` are lower in dark mode (9%/6%), because on a
  light page the veil darkens and thus *raises* text contrast, while on a dark page it lightens and
  lowers it. After the fix, same viewport: 6 of 7 points carry the wash, 22 luminance levels corner
  to corner. A test locks the lesson (radii must stay in viewport units, never `rem`).
  **§26 — the ambient background now carries the landing page's motif** (`_ambient_texture()`,
  `_dither_tile()`, `_scatter_cells()`). The app and `static/landing.html` were selling the same
  product in two unrelated visual languages — typographic block fields there, smooth radial veils
  here — and someone moving from the sales page to the tool should recognise the same product. A
  second `::after` layer on `stAppViewContainer` tiles a **generated SVG data-URI of scattered
  blocks** in `--aca-primary`, masked by the *same* radial positions/radii as the §21 veils so the
  pixels exist only where colour already is, and driven by the *same* `aca-ambient` keyframe so the
  two layers drift together (two background layers sliding relative to each other stop reading as
  one material). Deliberately **not** the landing page's engine: there the blocks are real text
  regenerated per frame by a canvas, and `branding.py` emits CSS only — a constraint not worth
  lifting for decoration. The stated cost of that: the mask makes blocks **fade** toward the edges
  where a true dither would **thin** them; at 5% alpha on a 27%-covered tile the difference is
  imperceptible, but it is an approximation, not the same thing. Three details that are decisions:
  the tile is chosen by a **coordinate hash, not a Bayer matrix** — Bayer thresholded at one level
  has no varying density to break it up, so its rows come out 4/2/4/0 and the result is a weave
  with visible tile seams (tried, rendered, rejected), while a static texture has no reason to be
  ordered since there is no frame-to-frame sparkle to avoid; **every row and column is guaranteed
  ≥2 cells**, because a 30% draw leaves a row of 16 empty about once in 300 and an empty row in a
  tile repeating every 96px is a horizontal seam the eye follows; and the **same** URI is tiled
  twice at 96px/138px with coprime offsets, so the combined period is far past any screen. `#` is
  URL-encoded — left raw in a `url()` it reads as a fragment and the whole tile vanishes with no
  error, no failing test and nothing to see on re-reading. §21's three constraints are inherited
  unchanged (primary never accent; below the attention threshold; work surface only), and as with
  the gradient the **texture is static and only the drift is level-gated**, so "animations: aucune"
  keeps the material. Covered by 7 tests, one of which is general rather than about this feature:
  `test_les_commentaires_css_sont_tous_refermes` counts and balances comment delimiters across the
  whole emitted sheet, after a stray `*/` closed a comment early **twice in one session** — once
  silently reverting the footer rules, once silently killing the ambient drift on *both* layers.
  Motion note worth keeping: entrance animations were *suspected* of replaying on every Streamlit
  rerun (which would have meant deleting them outright); measuring `getAnimations()` before and
  after a widget-triggered rerun showed them unchanged at `finished` — React reconciles the nodes,
  so they play once per mount. The hypothesis was wrong and the animations were kept.
- `signal_separation(primary, accent)` (§21) — the guard behind the pass's one genuine *design*
  defect rather than a rendering one. Since §19 the accent has exactly one job — *a human must
  decide here* (`.aca-signoff`, the alert pill, the decision rail's terminus) — but all 18 `PRESETS`
  predate that rule and still treat accent as "the second brand colour": 4 of them gave it the same
  hue family as the primary, erasing the product's central affordance (the test instance ran an
  all-blue palette with **not one pixel** of the reserved colour). Deliberately **not**
  `contrast_ratio`: WCAG measures luminance, so it ranks these backwards — the default petrol/amber
  pair scores a poor 1.66:1 while dark-blue/light-blue scores well and still reads as "blue". It is
  a distance in the **a\*b\* chroma plane of CIELAB with L\* ignored on purpose** (identical → 0,
  dark/light blue → 0.08, petrol/amber → 0.74); an earlier hue+saturation version was wrong in both
  directions and the 18-preset ranking exposed it immediately. Wired into `accessibility_report()`
  (warns, never refuses — it is the client's brand) and locked by a test parametrized over every
  preset. `Accessibilité renforcée` was fixed **despite passing** the metric: it paired blue with
  violet, the classic deuteranopia confusion, and a metric that models normal colour vision is the
  wrong authority on a palette whose name promises accessibility. Stated invariant, corrected from
  §19's wording: what matters is that the accent is **reserved and unmistakable**, not that it is
  warm — hence `Corail`, whose brand colour is already warm, gets a *cool* reserved colour.
- [i18n.py](aca/core/i18n.py) — §18 tangent, on explicit user request ("ajouter l'option de
  switcher si français ou anglais la langue"): a **FR/EN language switcher for the UI**, scoped
  deliberately to primary chrome only (navigation, page headers/captions, main buttons/labels, key
  messages) — the user's own choice when asked, over translating every string in the project.
  Admin-only curation screens (knowledge-base rows, account management fields, branding token
  labels), the activity journal's detail columns, PDF export text and console logs stay French-only
  by design, not oversight. **A hand-written dict, not a library** (Babel/gettext): the translated
  surface is a few dozen static entries, no plurals, no localized dates — pulling in a whole i18n
  dependency for that would repeat the exact mistake this project avoids elsewhere (`totp.py`/
  `slack_verify.py`, stdlib-only by principle). Keys are named by screen zone (`nav.*`/`auth.*`/
  `dashboard.*`…) rather than by the French text itself, since the text is not a stable identifier —
  correcting a French label must never break the English lookup. `translate(key, lang, **kwargs)`
  never raises: an unknown key returns itself (a visible-but-harmless `"missing.key"` on screen,
  never a crashed page) and an unknown language falls back to `DEFAULT_LANGUAGE` ("fr"); `**kwargs`
  feeds `str.format()` for the handful of parameterized strings (`"{d} jours"`), with a malformed/
  missing argument caught rather than propagated. No Streamlit import (same posture as `session.py`/
  `branding.py`/`ui_kit.py`) — the session-scoped current-language read/write lives in
  `aca/ui/shared.py`'s `current_language()`/`t()`/`language_switcher()` instead (below), so `i18n.py`
  itself is fully unit-testable offline. Language choice is **session-scoped**
  (`st.session_state["_lang"]`), not persisted per-user or per-tenant — a deliberately small scope
  for a feature added as a tangent to a different request, matching how much was actually asked for.
  Covered by [test_i18n.py](tests/test_i18n.py) (11 tests: never-raises on an unknown key/language,
  every declared key genuinely carries both languages non-empty, placeholder formatting in both
  languages, and a sample-key check that FR and EN actually differ — so a future key added with the
  same string copy-pasted into both languages doesn't silently defeat the switcher).
- [ui_kit.py](aca/core/ui_kit.py) — §18: **the design system's vocabulary**, the piece §17's
  white-label theming didn't provide — every screen built its own section titles, cards and empty
  states ad hoc, so two blocks playing the same role never looked alike. Pure functions (`section()`,
  `stat()`/`stat_row()`, `chip()`/`chip_row()`, `empty_state()`, `key_hints()`) rendering HTML strings,
  no Streamlit import (same posture as `branding.py`/`session.py`), every interpolated value passed
  through `html.escape` — an email body or LLM output flowing through these is by definition
  untrusted content (cf. `prompt_guard.py`). **Signature component: `decision_rail()`** — a numbered
  vertical timeline of received→classified→enriched→drafted→**your decision**, where the numbering is
  honest (it's a real sequence) and the human decision is visually the terminus of the machine's work,
  not a bolted-on checkbox; a `STEP_ALERT` state exists for steps that completed fine but demand a
  wary read (a flagged clause, a detected injection attempt) — conflating it with failure would read
  as a crash, conflating it with success would bury the warning. A second, equally deliberate
  component: `timeline()` for a lead's chronological history (`{"when", "who", "what", "detail",
  "tone"}`) and `diff()` (stdlib `difflib`, bounded at `max_lines` — a three-hundred-line diff is
  scrolled past, not read) for the before/after of a human-edited draft; both consumed by
  `app_pages/1_inbox.py`'s "Historique de ce lead" expander. Styles live in `branding.py`'s `_UI_KIT`
  CSS block, so the application has exactly one place its stylesheet comes from.
- [tenant.py](aca/core/tenant.py) — `current_org_id()`: the single source of tenant identity for the
  multi-tenant foundation (§12 item 3, audited §14.3) — reads `ACA_ORG_ID` (default `"default"`)
  dynamically (never frozen at import, same reasoning as `DATABASE_URL` in `vector_store.py`). One
  ACA deployment = one tenant (like `DATABASE_URL`/`GOOGLE_SHEETS_ID` already are), not per-request
  multi-org routing within a single process — there is deliberately no login/session system here.
- [slack_verify.py](aca/core/slack_verify.py) — `verify_slack_signature(signing_secret, timestamp,
  body, signature)`: pure/stdlib-only (`hmac`/`hashlib`/`time`) HMAC-SHA256 verification of Slack's
  signed interactivity requests (the "✅ Valider"/"✕ Rejeter" buttons — see `notify.send_approval`
  and `api.py`'s `POST /slack/interactions`). Uses `SLACK_SIGNING_SECRET` (distinct from the
  posting `SLACK_WEBHOOK_URL`), constant-time compare, and a 5-min replay window. `/slack/interactions`
  is the one endpoint where a click triggers a CRM write **without** `require_api_key` (Slack won't
  send our header), so this signature is its only gate — it **fails closed** (rejects) if
  `SLACK_SIGNING_SECRET` is unset, unlike the rest of the API whose key is optional.
- [totp.py](aca/core/totp.py) — §18: second-factor TOTP (RFC 6238), the last weak link in an
  otherwise solid authentication chain — `user_store.py` already did the hard part (salted PBKDF2,
  constant-time compare, a dummy hash on unknown accounts, progressive lockout) for a single factor,
  guarding an `admin` account capable of creating other admins, redirecting sales alerts, and curating
  the knowledge base the AI will cite to prospects. **Stdlib-only, deliberately**: `cryptography` is
  present in the venv but only as a *transitive* dependency (via `google-auth`) — importing it directly
  would replay the exact §15.3.8 mistake ("`requirements.txt` is pinned" was false assurance, since
  indirect dependencies weren't in it). ~140 lines of `hmac`/`hashlib`/`struct`/`base64` is cheap
  enough not to justify a new dependency. Same posture as `slack_verify.py`. `generate_secret()`,
  `code_at()`/`current_code()` (RFC 4226 HOTP core, verified against the official RFC 4226 Appendix D
  test vectors in `tests/test_totp.py`), `verify()` (constant-time `hmac.compare_digest`, ±1 window
  drift tolerance, loops through **all** windows even after a match — stopping early would leak which
  window matched, hence the verifier's clock offset, by timing), `provisioning_uri()` (`otpauth://`,
  rendered as an actual scannable QR by `aca/ui/shared.py::_totp_qr_png()` — see that entry — rather
  than shown as raw text, `issuer` = the client's `BRAND_NAME`, never "ACA" hard-coded — the one
  place §17's white label touches security), `grouped_secret()` (groups of 4, the manual-entry
  fallback for a phone that can't scan), `seconds_remaining()`
  (shown next to the code field — the leading cause of a second factor's *felt* failure is a code that
  expired mid-typing). TOTP is scoped to `admin` only (`user_store.TOTP_REQUIRED_ROLES`) — imposing it
  on an operator validating twenty leads a day would be paid back in workarounds (a shared secret, one
  authenticator app for the whole team) that weaken security rather than strengthen it. No self-service
  recovery — only a CLI escape hatch (`user_store`'s `totp-off`) — since a recovery flow is precisely
  where a second factor gets weakened, and doing it properly needs a trusted channel (verified email,
  identified support) this deployment shape doesn't have.
- [poller.py](aca/core/poller.py) — standalone background intake: run separately (`python -m aca.core.poller`, own
  process/terminal — not started by Streamlit), polls `gmail_reader.list_unread_emails()` every
  `POLL_INTERVAL_SECONDS` (default 60), and for each email not already in `queue_store` runs it
  through `aca_graph.app.invoke()` up to the same validation pause as the manual flow (never past
  it — a human still has to click "Valider" in the UI), then records it via `queue_store.enqueue()`
  and logs the classification event via `analytics_store.record_classification()` (dashboard data —
  captured as soon as the graph pauses, independent of whether a human ever opens it in the UI).
  Also attaches a `UsageMetadataCallbackHandler` to the `invoke()` config and logs the aggregated
  token count via `analytics_store.record_tokens()` (§13, same pattern as `ui.py`). §18: also logs
  `ACTION_ANALYSIS_STARTED` to `activity_log` with `source=SOURCE_POLLER` — the same gesture as the
  manual form in `app_pages/1_inbox.py`, only the source differs, so `activity_log`'s "who did what"
  is complete for auto-intake too, not just human-triggered analyses.
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
  `followup_store.py`/`config_store.py` — the standalone SQLite writes these modules make **outside**
  the graph (`app.RETRY_POLICY` only covers nodes during `app.invoke()`), so a lock conflict between
  `poller.py` and `ui.py` opening the same file concurrently no longer raises immediately. Retries
  only `sqlite3.OperationalError`; any other exception propagates on the first attempt (same "don't
  retry a programming error" principle as `app._retry_on`).
- [followup_store.py](aca/storage/followup_store.py) — local SQLite registry (`data/followup.sqlite`) of validated
  leads sourced from Gmail (`track()`, no-op if no `gmail_thread_id` — manual entries can't be
  followed up automatically), consumed by `relance.py`. `mark_followed_up()` increments
  `followup_count` (§11.6 item 5, multi-round cadence — replaces the old one-shot `followup_sent`
  flag, kept in the schema for backward compatibility) up to `relance_max_rounds()` per lead
  (`RELANCE_MAX_ROUNDS`, default 3, overridable via the "Réglages" panel/`config_store` — ~80% of
  sales need 5+ touches total). The cadence stops on its own once the prospect replies (the thread's
  last message is then no longer ours) — no extra bookkeeping needed here for that. `list_active()`
  is scoped to the current tenant (`org_id`, fondation multi-tenant §12 item 3).
- [config_store.py](aca/storage/config_store.py) — settings panel backing store (§12 item 7, audited
  §14): local SQLite registry (`data/config.sqlite`) of per-tenant overrides (`CALENDLY_URL`,
  `SUPPORT_EMAIL`/`SUPPORT_SLACK_WEBHOOK_URL`, `HR_EMAIL`/`HR_SLACK_WEBHOOK_URL`, `RELANCE_DAYS`,
  `RELANCE_MAX_ROUNDS`), editable from `ui.py`'s "Réglages" tab without touching `.env`. A key never
  edited via the UI returns `None` from `get_setting()` — callers (`app.py`, `followup_store.py`,
  `relance.py`) then fall back to the existing `.env`/default value, so this is a surcouche, not a
  replacement for `.env`.
- [relance.py](aca/core/relance.py) — automatic follow-ups (P1 §11.4 item 7): for each tracked lead, reads
  the last message of the real Gmail thread (`threads().get()`); if it's from **us** (the sales
  rep) and at least `RELANCE_DAYS` old (default 4), drafts a follow-up in-thread via
  `gmail_reader.create_draft_reply()` — never auto-sent. If the last message is from the prospect,
  does nothing (they replied, or we haven't sent our first reply yet). Up to `RELANCE_MAX_ROUNDS`
  rounds per lead (§11.6 item 5, default 3), the wording varying slightly after the first round;
  both thresholds read the "Réglages" panel override first, `.env`/default otherwise (same pattern
  as `app._calendly_url()`). Run via `python -m aca.core.relance` (standalone, meant to be
  scheduled — e.g. daily — independent of `poller.py`). §18: each follow-up drafted logs
  `ACTION_FOLLOWUP_DRAFTED` to `activity_log` (`source=SOURCE_CLI`) — before this, a relance ran
  invisibly to the activity journal, indistinguishable from nothing happening at all.
- [user_store.py](aca/storage/user_store.py) — §15.1.6: named accounts, hashed passwords and roles
  (`data/users.sqlite`, `ACA_USERS_DB`). Replaces the "one shared password, nobody identified"
  model: PBKDF2-HMAC-SHA256 with a per-user salt and the cost stored **inside** the encoded hash
  (`pbkdf2_sha256$240000$<salt>$<hash>`), so raising the cost later doesn't invalidate existing
  records; constant-time compare plus a dummy hash on unknown accounts, so response time can't
  enumerate valid usernames. Roles `admin`/`operator` with declarative `ROLE_PERMISSIONS`
  (fail-closed on an unknown role) — an operator validates/rejects leads, only an admin edits
  settings, curates the knowledge base or manages accounts. Org-scoped and `sqlite_retry`-wrapped
  like the other stores. Deliberate graceful degradation: **no account created ⇒ `ui.py` falls back
  to the old `ACA_UI_PASSWORD` gate**, so existing deployments don't break. Accounts are *disabled*,
  never deleted (the audit log references the username). CLI:
  `python -m aca.storage.user_store create <name> --role admin|operator` (also `list`, `passwd`,
  `role`, `disable`, `enable`). §18 adds two things: **`ROLE_VIEWER`** (`view_dashboard`/
  `view_history` only — deliberately excludes `reject_lead`, since rejecting removes an analysis
  from everyone's queue, a real mutation, not consultation; fills a real gap — a director or the
  client themself wanting to see the dashboard/history without CRM-write rights used to have to be
  handed `operator`), and the **TOTP columns/functions** (`totp_secret` nullable column via an
  idempotent `ALTER TABLE`, `totp_required()`/`set_totp_secret()`/`get_totp_secret()`/`has_totp()`/
  `verify_totp()`, `totp` field on `list_users()` — never the secret itself; see `totp.py` above and
  `aca/ui/shared.py::_handle_totp_step` for the login-time wiring). CLI gains `totp-off <username>`
  (deliberately CLI-only, no UI button — "a `disable 2FA` button in the UI would be exactly the
  backdoor 2FA exists to close"). §24 adds `auth_state_fingerprint(username)` — a truncated hash of
  (`password_hash`, `totp_secret`) consumed by `device_trust_store` so that changing either one
  revokes every remembered browser automatically; it returns neither value, only their digest, since
  it travels to another store, and `""` for an unknown account (matching no record, hence "no
  trust" — the right default).
- [device_trust_store.py](aca/storage/device_trust_store.py) — §24: browsers allowed to skip the
  second factor (`data/device_trust.sqlite`, `ACA_DEVICE_TRUST_DB`). A separate registry from its
  neighbours, and the distinction is the reason it exists (the same reasoning that separated
  `task_store` from `followup_store`/`schedule_store` at §19): `user_store` holds **one row per
  person** and nothing that expires; `session.py` governs how long an **already-open** session
  lives; this holds **one row per browser** — dated, expiring, individually revocable — answering
  neither "who are you" nor "is your session still alive" but "must this machine be asked for the
  code". Three lifecycles, three purges. `verify()` requires **four** conditions, all necessary: the
  token hash exists **for that account** (a token issued to A is worthless for B), the auth
  fingerprint still matches, the browser is the same, and the deadline has not passed — judged here,
  never from the cookie. None of the four fails loudly: a refusal merely re-asks for the code, and
  **no failure counter is touched**, because an expired cookie is not an intrusion attempt and
  conflating the two would lock out legitimate people. Expired rows are dropped opportunistically on
  read (the table is tiny and a stale row has no value — the *usage* trace lives in `activity_log`),
  and `purge_expired()` is wired into `retention.py`. `list_devices()` returns only an 8-character
  extract of the hash: the list must help someone recognise their own machine, not hand over
  anything replayable. Timestamps stored twice for the same reason as `schedule_store` — epoch REAL
  for the expiry arithmetic, ISO text so a human opening the file can see why a device stopped being
  trusted.
- [audit_log.py](aca/storage/audit_log.py) — traceability (`data/audit.sqlite`, local, not the Google Sheet):
  `log_validation(thread_id, validated_by, classification, sender)` called from `ui.py`'s "Valider"
  handler and `aca/api.py`'s `_do_validate`. Since §15.1.6, `validated_by` comes from the
  **authenticated session**, not the old self-declared free-text field (which survives only in
  dev/shared-secret mode, where nobody is identified anyway). §15.2.7 — **hash-chained**: each row
  folds the previous row's digest into its own (`prev_hash`/`row_hash`, per tenant), so quietly
  editing or deleting an old row breaks every digest after it. `verify_chain()` recomputes and
  *locates* the first break — checking both the row's own content **and** that its `prev_hash`
  matches the actually-preceding row, since without the second check deleting a middle row would
  pass (each surviving row stays individually consistent). With `ACA_AUDIT_HMAC_KEY` set the
  digests become HMACs, so forging a coherent chain needs a key that lives outside the database.
  Stated plainly: this is **tamper-evident, not tamper-proof** — without that key, whoever can
  write to the file can recompute everything; real immutability would need append-only storage or
  external anchoring. Pre-migration rows have no digest and are counted as "legacy, unchained",
  never reported as tampering. `python -m aca.storage.audit_log` runs the check (exit 1 on a break).
- [tamper_chain.py](aca/storage/tamper_chain.py) — §17: `digest()` / `chain_hash()`, the hash-chaining
  mechanism extracted from `audit_log.py` once a **second** journal needed the identical guarantee.
  Copying fifteen lines of crypto into another module guarantees that one day one of the two gets
  hardened and the other doesn't — the HMAC key read dynamically here and frozen at import there, a
  field separator changed on one side only. One implementation, two callers. Pure/stdlib, and the
  field ordering is the callers' contract (changing it would make every already-written digest look
  forged).
- [activity_log.py](aca/storage/activity_log.py) — §17: **the audit trail that answers "who did what,
  when, from which machine"** (`data/activity.sqlite`, `ACA_ACTIVITY_DB`). `audit_log.py` records
  exactly *one* event type — a lead validation — so the `operator` role introduced in §15.1.6 existed
  with **none of its actions traced**: not logins, not *failed* logins (the §14 progressive lockout
  blocked brute force **silently**, so an attack could never be noticed, dated or attributed), not
  rejections (indistinguishable from a lead never handled), not settings changes (which redirect
  where sales alerts go), not knowledge curation (which decides what the Stratège will assert to
  prospects), not account management. 25 declared actions named `object.action` like `webhook.py`'s
  events, `ACTION_LABELS` (French, because a journal a manager can't read isn't consulted, hence
  isn't a control — a test fails if a new action lacks its label), `SENSITIVE_ACTIONS`,
  `outcome` (success/denied/failure) and `source` (streamlit/api/slack/cli/poller). Read side:
  `list_recent()` (SQL-side filters), `actors_summary()` (per-person rollup), `actor_profile()` (one
  operator's actions **and the machines they used** — the literal answer to "from what PC"),
  `verify_chain()`, `purge_older_than()` (wired into `retention.py`; an IP is personal data, so this
  journal cannot grow forever — the purge deliberately breaks the chain at the cut point, which is a
  legitimate deletion, not tampering, and is documented as such so a post-purge integrity warning
  isn't mistaken for an incident). **"From which machine", honestly**: a web app cannot identify a
  machine, and claiming otherwise in an audit log would be worse than writing nothing — what is
  stored is the server-seen IP (the proxy's unless `X-Forwarded-For` is propagated, and validated via
  `normalise_ip()` since that header is client-controlled hence forgeable), the user-agent
  (declarative, so forgeable, capped at 400 chars), a `device_id` = truncated hash of (IP, UA) that
  *groups* one workstation's actions without planting a tracking cookie, and `server_host` — which in
  the Solo deployment (`run_solo.py` on a rep's laptop) **is** the PC. **`log()` never raises** (same
  contract as `webhook.emit()`): it sits in the path of a CRM write, so a locked journal must not fail
  a legitimate validation — it returns `ok=False` and prints instead. Two real defects that
  verification caught rather than review: serialisation of `details` originally happened *outside* the
  `try`, so `log()` **could** raise in violation of its own contract; and `st.context.ip_address` is
  not guaranteed to be a string, so the value reached SQLite (`Error binding parameter 13`), `log()`
  swallowed the exception per that same contract, and the audit line **vanished silently** — a
  security journal you believe is complete and isn't is more dangerous than none, so values are now
  normalised at the store boundary and a regression test locks it. **§18 additions**:
  `lead_timeline(thread_id)` — chronological (ascending) view of one lead's history, reusing the
  existing `target_id` column, consumed by `app_pages/1_inbox.py`'s "Historique de ce lead" (the
  best value/effort ratio of the whole suggestions doc — the data already existed, only the view
  was missing); `known_devices()`/`is_new_device()` — unusual-device detection as a **set
  comparison**, deliberately not machine learning ("a model that 'learns' three salespeople's habits
  would mostly produce inexplicable false positives"), `before_id` answers "was this device known
  **at the time** of this action" (without it, the action being examined would count as its own
  history), an empty `device_id` (API/machine calls) is never flagged as new; two-speed
  `purge_older_than(days, sensitive_days=None)` — ordinary activity purges at `days`,
  `SENSITIVE_ACTIONS` at `sensitive_days` if given, as **two separate `DELETE` statements** (not one
  `CASE`) specifically so a SQL parenthesisation bug can't swap which duration applies to which
  action class — wired into `retention.py` via the new `ACTIVITY_SENSITIVE_RETENTION_DAYS`
  (default: twice `ACTIVITY_RETENTION_DAYS`); `csv_export(rows)`; `rows_for_period(year, month)` +
  `archive_period(directory, year, month)` — writes a monthly CSV **and its `.sha256` fingerprint**
  (via `tamper_chain.digest()`, HMAC'd if `ACA_AUDIT_HMAC_KEY` is set), **idempotent** (an existing
  archive is never overwritten, so a re-run of the scheduled job can't silently replace a genuine
  archive with an amputated one after a retention purge already ran) — answers the actual question a
  SOC 2 audit asks ("show me week 28"), which a purge-only retention policy can't. New action
  constants `ACTION_DATA_PURGED`/`ACTION_FOLLOWUP_DRAFTED`/`ACTION_JOB_RAN` back the machine-action
  logging now wired into `poller.py`/`relance.py`/`retention.py`/`scheduler.py` (see their entries).
- [analytics_store.py](aca/storage/analytics_store.py) — local SQLite event log (`data/analytics.sqlite`, P2 §11.4
  item 17) powering the "Tableau de bord" tab in `ui.py`. Unlike the Sheets `Leads` tab (only
  validated `DEMANDE_DEMO`/`DEVIS`) or `audit_log.py` (only validation events), this captures
  **every** classification — including `SPAM`/`AUTRE`/`SUPPORT`, which never get validated.
  `record_classification(thread_id, classification, sender, source)` (idempotent `INSERT OR
  IGNORE`, called from `poller.py` and `ui.py._sync_result()`), `record_draft_ready(thread_id)`
  (separate call, since a clarification pause can log the classification before Stratège has
  written a draft), `record_validation(thread_id)` (closes the response-time measurement, called
  from the "Valider" handler). Read side: `volume_by_category()`, `daily_volume()`,
  `response_times()`, `funnel_counts()` — all `days`-windowed **and now tenant-scoped** (`org_id`,
  fondation multi-tenant §12 item 3 — each read defaults to the current tenant via
  `aca.core.tenant.current_org_id()`). Also (§13, audit of the "ACAM v2 Blueprint" PDFs):
  `record_edit(thread_id, original, edited)` (no-op if unchanged; new `draft_edits` table — a raw
  corpus for future manual few-shot/eval enrichment, **not** fine-tuning) / `edit_rate(days)`, and
  `record_tokens(thread_id, input_tokens, output_tokens)` (no-op if both zero; new `token_usage`
  table, fed by `sum_usage()` in `app.py`/`aca/api.py`) / `token_stats(days)` — the free "first
  step" of usage tracking anticipated in §12 item 4, now consumed by `aca/integrations/billing.py`
  (below) for the paid step that follows it. §18 adds `get_draft_edit(thread_id)` — the read side
  §22 adds four read-side aggregations, none of which needs new data — every column had been
  written since the beginning and simply never displayed: `bucket_response_times(rows)` (pure, so
  the one business judgement here — "past 24 h you have lost the prospect" — is testable without a
  DB; **empty buckets are kept**, since a missing "over 24 h" bar reads as *no data* when it
  actually means *no delay*, the opposite and the best news on the page — `RESPONSE_BUCKETS` is
  ordinal, hence `sort=False` and a single hue rather than a categorical palette);
  `by_source()` — the **adoption** measure, and the starkest built-but-never-shown gap: `source` is
  recorded on every analysis and appeared nowhere, so nothing in the product could tell you whether
  the automatic intake was working or whether people were still retyping emails by hand;
  `hourly_volume()` — always returns all 24 hours *including zeros*, because the troughs are the
  information, and it exists to make §19's intake-window setting a measured choice instead of a
  guess; `top_senders()` — deliberately small `limit`, since exporting an address book is the
  parametrable report's job (§20), not this block's.
  `get_draft_edit()` is the read side
  `record_edit()` never had: the most recent (original, edited) pair for a thread (no uniqueness
  constraint on `thread_id`, so "most recent" is deliberately what validation actually sent), feeding
  `ui_kit.diff()` in the lead timeline — §17 only ever logged character-count deltas ("340 → 412"),
  which say nothing about *what* changed; the full text already lived in this table, only the read
  was missing.
- [retention.py](aca/core/retention.py) — GDPR/PII retention sweep (`RETENTION_DAYS`, default 365): purges
  `Leads` rows, their corresponding `data/checkpoints.sqlite` threads (`checkpointer.delete_thread`,
  removes the raw email body from graph state), and old validated `data/queue.sqlite` entries older
  than the retention window. Never touches `Enrichissement_Cache` (company data, not personal) or
  `FAQ`. Run via `python -m aca.core.retention`, meant to be scheduled (e.g. weekly). §15.2.4 adds
  the **right to erasure** (GDPR art. 17) that was missing: `purge_subject(sender)` /
  `python -m aca.core.retention --oublier <address>` erases one person's data immediately —
  Leads rows, their LangGraph checkpoints (which hold the raw email body), queue entries and
  follow-up tracking — and returns a per-location count so you can answer the person precisely.
  That was the real gap: only *age-based* erasure was automated (the easy half), while an explicit
  request meant hand-hunting rows across a Google Sheet, a checkpoint file and two SQLite
  registries — so in practice it didn't happen. Wanted side effect: `relance.py` stops chasing
  someone who just asked to be forgotten. The **audit log is deliberately kept** (legitimate
  interest, art. 17.3(e); deleting a row would also break the §15.2.7 chain and look like
  tampering) — a documented decision, not an oversight. §18: `purge_old_activity()` now passes
  `sensitive_days=ACTIVITY_SENSITIVE_RETENTION_DAYS` (default: twice `ACTIVITY_RETENTION_DAYS`) into
  `activity_log.purge_older_than()` — the two-speed retention capability existed in `activity_log.py`
  but was never actually invoked by the real scheduled job, exactly the "built but not wired" gap
  this project's own §16.0 pass had already found once for scheduling itself; also logs
  `ACTION_DATA_PURGED` to `activity_log` for both the periodic sweep and `purge_subject()`
  (`SOURCE_CLI`), so a purge is now itself a traced event, not an invisible one.
- [notify.py](aca/integrations/notify.py) — `send(message, webhook_url=None, email_to=None, subject=None)`: Slack
  webhook (`SLACK_WEBHOOK_URL`, or `webhook_url` override) then Gmail send-to-self (`NOTIFY_EMAIL`,
  or `email_to` override) as a graceful-degradation chain, same pattern as `enrichment.py`/`veille.py`.
  Called by `notification_node` (generic leads channel) and `routing_node` (per-category
  `SUPPORT_EMAIL`/`HR_EMAIL` overrides). `python -m aca.integrations.notify` sends a one-off test message on whichever
  channel is configured. Also `send_approval(message, thread_id, ...)` — same chain, but the Slack
  alert carries **interactive "✅ Valider"/"✕ Rejeter" buttons** (Block Kit) whose `value` holds the
  `thread_id`; a click POSTs to `POST /slack/interactions` (see `api.py`) so the human-in-the-loop
  validation can happen **inside Slack, without opening any UI** — the highest-convenience surface
  for a sales team that already lives in Slack. `notification_node` uses `send_approval` for a real
  lead (has a `thread_id`, will pause for validation) and plain `send` for the informational
  low-confidence alert (auto-routed, no pause to act on). Graceful-degradation unchanged: falls back
  to a buttonless e-mail if Slack is absent. (Live approval requires a Slack **app** with
  interactivity enabled pointing at `/slack/interactions` — the incoming webhook alone posts the
  message but can't receive clicks; see `SLACK_SIGNING_SECRET` below.)
- [webhook.py](aca/integrations/webhook.py) — §16.1.2: **outbound** events, the piece that makes the
  n8n port idiomatic. Before it, the API only offered routes to *poll* (`GET /threads/pending`), so
  an n8n workflow would have run a **Schedule** node polling in a loop — i.e. reimplementing
  `poller.py` inside n8n, exactly what the port is meant to replace. ACA now **pushes** 5 events
  (`analysis.paused`, `analysis.clarification`, `analysis.routed`, `lead.validated`,
  `lead.rejected`, named `object.action` so an n8n filter can route on them), the workflow becomes
  event-driven, and latency drops from "up to one poll interval" to immediate. Envelope is
  `{event, org_id, timestamp, data}` — `org_id` included so one n8n endpoint serves several tenants
  without a per-client URL, and `data` comes from `app.snapshot_from_state()`, **shared with**
  `api._snapshot()` so a REST client and a webhook subscriber see the identical lead (asserted by
  `test_webhook_payload_matches_api_snapshot_shape`, not merely hoped for). Optional HMAC-SHA256
  signature (`ACA_WEBHOOK_SECRET` → `X-ACA-Signature`/`X-ACA-Timestamp`, timestamp folded *into* the
  signature as an anti-replay window — the outbound mirror of `slack_verify.py`); unlike
  `/slack/interactions` a missing secret does **not** fail the send, since an outbound webhook
  triggers no CRM write on our side. Same graceful-degradation contract as `notify.py`
  (`ACA_WEBHOOK_URL` absent = silent no-op) and — critically — **never raises**: `emit()` is called
  from graph nodes under `RETRY_POLICY`, where an exception would cause up to 3 node re-executions
  and, for `action_node`, a double CRM write (the real 2026-07-12 HubSpot bug). Not to be confused
  with `notify.py`, which addresses a **human** (Slack prose, Valider/Rejeter buttons); this
  addresses a **machine**. `python -m aca.integrations.webhook` sends a test event.
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
- [ui.py](ui.py) — §18: **thin `st.navigation` router**, styled with a light "Fluent" theme
  ([.streamlit/config.toml](.streamlit/config.toml)). Before §18 this was a single ~1700-line file
  mixing session/audit logic with every tab's body; nothing enforced that two tabs stayed consistent
  about "who is signed in" since each read `st.session_state` directly. Now `ui.py` only does what
  genuinely needs to run on *every* page: resolve `BRAND`, `st.set_page_config()` (must stay the
  script's first Streamlit call), inject `branding.css()`, call `prod_check.enforce()` then
  `aca.ui.shared.check_auth(BRAND)` (`st.stop()` if it returns `False`), render the admin-only
  security-posture expander and the brand hero banner (status pills: signed-in user/role,
  pending-queue count, **new-since-login count** — see below, demo-mode warning), the session
  defaults for the manual-entry form, the **sidebar** (signed-in user/logout, "File d'attente" with
  its "Ouvrir" button, Gmail import, §23's "Présentation" block linking to
  [static/landing.html](static/landing.html) — placed *before* the admin-only curation block on
  purpose, since an ungated item rendered after a gated one lands in a visibly different place for
  an operator than for an admin — the admin-only knowledge-base uploader + "FAQ en attente" review
  panel, the "Confidentialité des données" expander), builds the page list — `1_inbox.py`
  (default), `2_dashboard.py`, `3_history.py`, `4_activity.py` (only appended if
  `_can(PERM_MANAGE_USERS)` — an operator's `st.navigation()` call never even declares this route, so
  it isn't just hidden from the menu, it isn't reachable at all, same effective gate as the old
  `if tab_activity is not None:`), `5_settings.py` — via `st.Page(...)`/`st.navigation(pages,
  position="top")` ("top" rather than "sidebar": with only 3-5 pages and an already-busy custom
  sidebar, a horizontal bar avoids the two competing for the same rail), then `pg.run()`, then the
  brand footer. Two sidebar buttons ("Ouvrir" on a queued item, "Charger cet e-mail" from Gmail) call
  `st.switch_page("app_pages/1_inbox.py")` instead of a plain `st.rerun()` — under the old
  `st.tabs()`, *every* tab's body executed on *every* rerun, so a loaded result was visible
  regardless of which tab was visually active; `st.navigation` only executes the **selected** page's
  script, so without the explicit switch a result loaded from the sidebar would stay invisible until
  the person clicked "Nouvel e-mail" themselves. Also §18 (recap #5.5 "in-app notifications"): tracks
  `_seen_pending_count` (`setdefault`, so it's fixed once per authenticated session — "new since you
  signed in") against the live pending count, shows a "N nouvelle(s) depuis votre connexion" alert
  pill plus a sidebar "Marquer comme vu" button that resets the baseline. **§18 tangent**: renders
  `aca.ui.shared.language_switcher()` in the sidebar **before** `prod_check.enforce()`/`check_auth()`
  — an earlier draft placed it after the auth gate, which is exactly the reachability bug found while
  verifying: `check_auth()` returns `False` on the login screen and `ui.py` calls `st.stop()`
  immediately after, so anything placed later in the script never renders pre-login, leaving a person
  stuck on a French login screen with no way to switch before signing in.
- [aca/ui/shared.py](aca/ui/shared.py) — §18: the extraction that made the page split possible.
  `st.session_state` is shared by every page of one `st.navigation` session, so these functions need
  no adaptation to be called from `ui.py` **and** any `app_pages/*.py`; every page imports the ones
  it needs aliased back to their original underscore names (`audit as _audit`, `can as _can`, …) so
  no call site anywhere had to change during the split — the mechanical, low-risk way to move code
  that already worked. `client_context()`/`audit()`/`audit_denied()`/`safe_error()` are the §17
  activity-logging helpers, unchanged in behaviour. `check_auth(brand)` is `ui.py`'s old
  `_check_auth()`, now taking `brand` as a parameter (resolved once in `ui.py`, not a second time
  here) and, new in §18, routing a password-verified `admin` account through
  `_handle_totp_step(username, role, brand, now)` before opening the session when
  `user_store.totp_required(role)` — a pending username/role lives in
  `st.session_state["_totp_pending_username"/"_totp_pending_role"]` across reruns (Streamlit has no
  other way to "remember" being mid-flow between two form submissions). Two distinct branches, never
  conflated: an already-enrolled account (`has_totp()`) sees a plain 6-digit field, `verify_totp()`
  failure re-uses `_register_failure()` — the **same** progressive-lockout counter as a wrong
  password, since a second factor with unlimited guesses isn't one; an account required to have TOTP
  but not yet enrolled is forced through enrollment first — `generate_secret()` lives only in
  `st.session_state["_totp_enroll_secret"]` until a *first correct code* confirms it, then
  `user_store.set_totp_secret()` persists it — a secret generated but never confirmed must not guard
  the account, in case the browser tab is closed mid-setup. Enrollment renders the secret as an
  **actual scannable QR** (`_totp_qr_png()`, via `segno` — pinned, pure Python, zero transitive
  dependencies of its own, encodes and rasterises PNG bytes without needing Pillow — present in the
  venv only as a transitive dependency of Streamlit itself, the same trap already flagged once for
  `cryptography`) rather than raw `otpauth://` text, with the grouped secret tucked into a collapsed
  "Impossible de scanner ?" fallback — retyping a 32-character base32 string by hand is exactly the
  friction that makes people abandon a second factor. Deliberately kept out of `totp.py`, whose whole
  point is staying stdlib-only for the actual crypto; rendering an image is the UI layer's concern.
  `_warn_before_expiry()` (§18 recap #6, see `session.py`). **§18 tangent**: `current_language()`
  (reads `st.session_state["_lang"]`, default `i18n.DEFAULT_LANGUAGE`), `t(key, **kwargs)` (thin
  wrapper over `i18n.translate()` bound to the current language — the only i18n entry point every
  page/`ui.py` actually calls), and `language_switcher()` (`st.segmented_control` over
  `i18n.LANGUAGES`, labelled via `i18n.LANGUAGE_LABELS`; a changed selection sets `_lang` and
  `st.rerun()`s — Streamlit has no other way to make every already-rendered widget on the page pick
  up the new language mid-run). `_warn_before_expiry()`/TOTP/auth screens above are themselves
  translated via `t()`, so the login flow — the one screen every role sees before anything else —
  isn't French-only for an English-speaking evaluator. `advance_graph()`/`sync_result()`/
  `load_queued_thread()`/`build_graph_dot()`/
  `completed_graph_nodes()`/`NODE_STEPS`/`CATEGORY_STYLE`/`GRAPH_FIXED_NODES` are the old
  graph-interaction helpers, needed both by `app_pages/1_inbox.py` (launching/resuming the graph) and
  by `ui.py`'s own sidebar "Ouvrir" button (`load_queued_thread`) — living here rather than in the
  inbox page specifically so the router doesn't have to import from a page.
- [app_pages/1_inbox.py](app_pages/1_inbox.py) — §18: the "Nouvel e-mail" flow, the largest and
  riskiest single piece of the page split (re-verified end-to-end via `AppTest` — full demo-mode
  analysis through classification/extraction/agents/draft, then both validate and reject paths —
  before and after the extraction, output compared). Demo-mode example picker (§16.3); the manual
  form (sender/subject/body + multi-file upload, `attachments_raw` carried raw into the graph's
  `ingestion_node`, §11.6); `advance_graph()` on launch, streaming each node live; on a pending
  clarification `interrupt`, the question + reply box, looping via `Command(resume=...)` until the
  validation pause; the classification-specific rendering (`SPAM` plain error, `SUPPORT`/`AUTRE`
  routing-detail expander, else the full "Fiche prospect" card, risk-flags/knowledge-gap/
  injection-flags banners, the "Raisonnement de l'équipe d'agents" expander with the live graph
  render); §18 additions inside the qualifying-lead branch: an **"Historique de ce lead"** expander
  (recap #5) — `activity_log.lead_timeline(thread_id)` mapped into `ui_kit.timeline()` events (tone
  by action/outcome, an unusual-device note via `is_new_device(actor, device_id, before_id=row["id"])`
  on each row) plus, if `analytics_store.get_draft_edit()` returns one, `ui_kit.diff()` of the
  before/after draft; a **"Télécharger en PDF"** button (recap #8) — `pdf_export.build_proposal_pdf()`
  called on every rerun while a result is shown (cheap, no LLM/network call), the click itself logged
  as `ACTION_DATA_EXPORTED` (a PDF carries the prospect's name/contact, the same "export = personal
  data leaving the app" reasoning as the activity journal's CSV button). The editable proposition
  `st.text_area`, the validation/rejection handlers (`app.update_state`/`app.invoke(None, …)`,
  `audit_log.log_validation()`, `analytics_store.record_validation()`, `followup_store.track()`) are
  unchanged from §17, moved verbatim.
- [app_pages/2_dashboard.py](app_pages/2_dashboard.py) — §18: KPIs/charts from `analytics_store.py`
  (period filter via `st.segmented_control`, brand-derived `color=` on all three charts), moved
  verbatim from the old "Tableau de bord" tab. Adds an empty-state demo link (recap #5.6): when
  `total == 0` and `demo.is_enabled()`, a "Charger un exemple de démonstration" button
  `st.switch_page`s to the inbox — an empty dashboard used to just explain *why* it was empty; in
  demo mode it now also says what to do about it. **§22 — it showed a *state* and never a *change*.**
  Five counters and three charts over "the last N days" answered none of the three questions the
  page is actually opened for (*is it better than before?*, *are we replying fast enough?*, *is the
  automatic intake doing anything?*) — while the data for all three was already being recorded, the
  same built-but-never-read gap as `get_draft_edit` (§18) and `list_events` (§20). Now: **deltas on
  every KPI**, reusing `reporting.previous_period()` rather than reimplementing it (the screen and
  the monthly PDF must never disagree on the same period) and `reporting._kpi`'s `better` direction
  via a declared `_DELTA_COLOR` map — a rising median response time reads as a *degradation*, and
  the draft-edit rate stays **grey**, because a high rate means improvable drafts while a zero rate
  may mean validating without reading, so the data cannot justify a colour. A missing previous value
  renders **nothing**, never "+0", which would claim stability where there is no basis for comparison.
  Four new distributions (see `analytics_store` §22 below), an `st.fragment` so changing the period
  reruns only the dashboard rather than the router's whole sidebar, and horizontal bars wherever
  labels are long (`DEMANDE_DEMO`, `proposition rédigée`) since Vega rotates them 90° otherwise.
  Three alignment fixes: the token counter **leaves the hero row** (an ops metric, and at five items
  the row fell to "4 + 1"); the KPI sparkline is **removed** (it existed for one metric of five, made
  that card taller than its neighbours, and plotted the same series as the chart directly below);
  and the row itself is de-raggedised in CSS — see `branding.py`. **Deliberately no global category
  filter**: it could not apply to the funnel, the delays or the tokens without rewriting five
  queries, so half the screen would not move and the reader would no longer know what they are
  looking at. A filter that lies about its scope is worse than none.
- [app_pages/3_history.py](app_pages/3_history.py) — §18: the searchable `audit_log.list_recent()`
  table (§12 item 2), moved verbatim from the old "Historique" tab.
- [app_pages/4_activity.py](app_pages/4_activity.py) — §18: the admin-only activity journal (§17),
  moved verbatim from the old fifth tab — same gate as before (not reachable at all for a non-admin,
  see `ui.py` above), same reasoning for keeping it admin-only (exposing failed logins, IPs and
  colleagues' workstations to an operator would make mutual surveillance an open facility).
- [app_pages/5_settings.py](app_pages/5_settings.py) — §18: the "Réglages" form
  ([config_store.py](aca/storage/config_store.py)), the "Apparence et identité visuelle" branding
  panel, and the "Comptes et rôles" account-management panel, moved verbatim from the old fourth tab
  — unlike Journal d'activité, this page is **always** in the navigation (an operator sees a
  read-only settings view; `_can()` checks gate what's editable within the page itself, same as
  before the split).
- [app_pages/6_reviews.py](app_pages/6_reviews.py) — §20: the review-request surface, serving **two
  roles on one page** in the order that matches reality — what you *received* first (it is a queue,
  and it blocks a colleague), then what you send, then what you already sent. Two separate pages
  would have forced an admin — who both receives and sends — to switch screens for one subject. The
  picker is an `st.dataframe(selection_mode="multi-row")` over the pending queue (or the last 30
  days, so "I have a doubt about what I did yesterday" stays possible), so "several at once" is a
  real multi-select rather than a repeated single action. Sending is gated on
  `PERM_VALIDATE_LEAD`/`PERM_REJECT_LEAD`: a `viewer` consults, and letting a read-only role hand
  work to an admin would exceed what "read-only" announces. Choosing `@admins` when **no** admin
  account exists is refused loudly rather than written to a table nobody would ever read (the real
  shared-secret-mode case).
- [app_pages/7_reports.py](app_pages/7_reports.py) — §20: the report builder — period (last month /
  this month / 7-30-90 days / exact dates), sections grouped by family with each one's description
  shown as help, the email table's **columns** and filters (that is where "category and sender
  only" comes from), a free title and context note printed on the cover, comparison on/off, and
  reusable presets. The "to" date has **one day added** before it reaches the engine: the engine's
  upper bound is exclusive, but a person choosing "up to the 15th" means the 15th included — without
  this, that whole day would silently vanish from the report. An on-screen preview renders every
  block before download, so an empty section is discovered here (and the period widened) rather than
  inside the PDF. **Two access gates for one reason**: the `activity`/`actors` sections reproduce the
  Journal d'activité, admin-only since §17 — and so does the monthly archive list, which contains
  them. A report serving that content without the same gate would be a plain bypass of the rule.
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
  `DATABASE_URL`; `sync_embeddings(pairs, embed_documents)` fully replaces the *current tenant's*
  rows in the `faq_embeddings` table (`question | reponse | embedding VECTOR(3072) | updated_at |
  org_id` — `org_id` added for the multi-tenant foundation, §12 item 3, audited §14.3; no ANN index
  yet — a sequential scan with pgvector's `<=>` operator is exact and sub-millisecond at FAQ-sized
  volumes; add `hnsw`/`ivfflat` later if the FAQ grows into the thousands) whenever `sheets.py`
  detects the FAQ's visible content changed; `search(query_vector, top_n, max_distance)` returns
  the nearest rows of the current tenant by cosine distance (note: pgvector gives a *distance*, not
  a similarity — the old `score > 0.5` threshold is `distance < 0.5` here). **Row-Level Security**
  (§12 item 3 / §14.3): `faq_embeddings` has `ENABLE`+`FORCE ROW LEVEL SECURITY` and a
  `tenant_isolation` policy comparing `org_id` to the Postgres session variable
  `app.current_org_id` — this project never goes through PostgREST/an anon key (only a direct
  `psycopg` connection via `DATABASE_URL`), so the policy can't rely on `auth.uid()`; `_scope_to_tenant()`
  sets that session variable via `set_config()` at the start of every borrowed pooled connection,
  before any query, so a connection reused later by a different tenant can never inherit the
  previous one's scope. **Live-verified against real Supabase (2026-07-21)** — and found broken on
  the first pass: the `postgres` role Supabase's standard `DATABASE_URL` connects as has
  `rolbypassrls=true` by default, which makes `FORCE ROW LEVEL SECURITY` a no-op regardless (that
  clause only binds the table *owner*, never a role with `BYPASSRLS`/`SUPERUSER`). Fixed by creating
  a restricted `aca_app` role (no superuser, no `BYPASSRLS`) that the app now connects as instead;
  `postgres` remains available for admin/migrations only. Two more real bugs surfaced while fixing
  this, both now fixed: (1) Supabase auto-enables RLS with zero policies on any new `public` table
  — including LangGraph's own `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/
  `checkpoint_migrations`, which have no `org_id` column and were never meant to be tenant-scoped
  (isolation there is by `thread_id`, inside LangGraph's own queries) — so they needed a one-time
  permissive policy, added manually via the Supabase SQL editor; (2) `_get_pool()` used to
  unconditionally re-run the owner-only `ENABLE`/`FORCE ROW LEVEL SECURITY` + `CREATE POLICY` setup
  on every process start, which crashed under the new non-owner role — now wrapped in a try/except
  that treats "must be owner" as "already configured by an admin" and continues. Re-verified after
  each fix: a bogus tenant and a connection with no session variable set both correctly see 0 rows
  of the real 74; the real tenant sees all 74; `search_knowledge_base_semantic()` returns genuine
  pgvector results end-to-end with no silent fallback to keyword search. See
  `docs/PROJECT_JOURNAL.md` (2026-07-21 entry) for the full investigation. Absent `DATABASE_URL` =
  fully inert, `sheets.py` uses its original in-memory path unchanged.
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
- [billing.py](aca/integrations/billing.py) — usage-based billing (§12 item 4, audited §14): `report_usage(org_id,
  days)` reads the current tenant's `analytics_store.token_stats()` (already-free token logging)
  and, only if `STRIPE_API_KEY` is set **and** a `STRIPE_SUBSCRIPTION_ITEM_ID` is configured for
  that tenant (via `config_store`), reports the total as a Stripe usage record
  (`action="set"`, so re-running the same day doesn't double-count). Same graceful-degradation
  contract as `notify.py`/`hubspot.py`: absent config or a Stripe API failure both return the
  stats without raising, never blocking the caller. ⚠️ Explicitly the *paid* step the roadmap
  says makes sense "only in the commercial phase" — deliberately **not** live-verified against a
  real Stripe account (none exists for this project); only its graceful-degradation contract and
  its call shape (via a fake Stripe client) are covered by tests, unlike `hubspot.py`'s live-tested
  portal integration above.
- [pdf_reader.py](aca/ingestion/pdf_reader.py) — `extract_text_from_pdf()` using PyMuPDF (`fitz`); accepts bytes or a
  path; truncates output to `MAX_CHARS` (15,000) to bound LLM token usage. Used as-is by
  `ingest.py`/the Knowledge_Base uploader (single PDF, unchanged). Internally also exposes
  `extract_raw_text_from_pdf()` (no truncation) + the `MAX_CHARS` constant, reused by
  `attachment_reader.py` below so multi-file truncation happens once, on the combined text.
- [pdf_export.py](aca/integrations/pdf_export.py) — §18 (recap #8): PyMuPDF's *write* side — the
  content (`draft_response`) and the brand (§17) both already existed, but a commercial who wanted to
  send the proposal some way other than the Gmail draft had to copy-paste it into Word and reformat
  by hand; it's also the most direct demo argument ("here's the document your client receives, in
  your colours"). **No new dependency** — `pymupdf` is already pinned for *reading* attachments
  (`pdf_reader.py`); this reuses its `insert_htmlbox()` (a bounded HTML/CSS subset, so line-wrapping
  isn't computed by hand — the source of truncated text the moment a company name runs long).
  `build_proposal_pdf(draft, extracted_info, classification, sender, tokens=None)` renders a coloured
  header band in `BRAND_PRIMARY` with the logo (bitmap only — `insert_image` can't place a vector
  image, so SVG is silently skipped, same "omit, don't fail" choice as elsewhere), a fiche-prospect
  table, the draft (newlines converted to `<p>` tags, since `insert_htmlbox` ignores raw `\n`), and a
  footer stating the proposal was reviewed by a human before sending — **not decorative**: the
  product's whole premise is a human validation gate, and an exported document that doesn't say so
  would misrepresent that to whoever reads it later. **Never raises** (same contract as
  `notify.py`/`hubspot.py`) — a broken download button must not take down the validation screen, the
  application's vital function. `proposal_filename()` sanitises the LLM-extracted company name
  (Unicode-normalised, non-alphanumeric stripped) before it lands in a `Content-Disposition` header —
  the name comes from an inbound e-mail, hence an untrusted source. Verified via a real round-trip in
  `tests/test_pdf_export.py`: build → reopen with `fitz` → extract text → assert the company name,
  contact, draft content and the human-review footer are genuinely present (not a blank/broken
  document), plus header-injection/path-traversal characters (`\r`, `\n`, `/`, `"`) neutralised in the
  filename.
- [report_pdf.py](aca/integrations/report_pdf.py) — §20: the **themed, multi-page renderer** for
  `reporting.collect()`'s output. **No new dependency, deliberately**: charts are drawn with
  PyMuPDF primitives (already pinned for reading attachments and writing proposals). Pulling in
  matplotlib for four bar charts would have added tens of megabytes, a font to embed and a second
  rendering engine to the Docker image — to produce raster images that would have needed
  re-theming by hand anyway, since the palette comes from the client; vector rectangles are
  crisper in print, weigh kilobytes and take the brand colour for free. **Always on-brand, never
  illegible**: a client's dark theme is right on screen and disastrous on a document meant to be
  printed and forwarded, so the paper is forced light and the client's ink colour is kept only if
  its contrast holds (WCAG AA 4.5); the accent colour is always honoured. Same never-raises
  contract as `pdf_export.py`/`notify.py` (returns `None`) — it is called by a nightly job as well
  as by a button. `write_pdf()` is **idempotent by default** (an existing report is never
  overwritten, exactly like `activity_log.archive_period`: a re-run after a retention purge would
  otherwise replace a complete report with an amputated one under the same name). Two real defects
  found by *looking at the rendered document*, not by re-reading the code: (1) every French
  paragraph overran the right margin, because `fitz.get_text_length()` badly under-measures
  accented characters ("ééééééééée" measured 22.8pt against 45.6pt for "eeeeeeeeee") — fixed by
  measuring through `fitz.Font(...).text_length()`; (2) `…` and `—` rendered as stray dots in every
  truncated cell and every empty value, because a PDF's Base-14 fonts are written with a Latin-1
  encoding — the glyphs exist in the font but the document cannot address them, while accented
  letters *are* in Latin-1 and rendered fine, which is what made it easy to miss. Fixed at the
  **drawing boundary** (a single `_put`) rather than at the twenty-three call sites, the same
  reasoning as `console.py` for console encoding; the trailing
  `encode('latin-1', 'replace')` is not belt-and-braces, since an inbound email subject can contain
  any Unicode at all. A third defect was found by a test rather than review: a typo in a `BRAND_*`
  hex made `_Palette` raise, so `build_report_pdf` returned `None` per its own contract and **the
  nightly report silently stopped being produced** — a colour is decoration and must not decide
  whether the document exists (now falls back per token).
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
- [verify_rls.py](scripts/verify_rls.py) — §15.2.2: read-only Supabase RLS coverage sweep. Lists
  every `public` table with its `relrowsecurity` / `relforcerowsecurity` flags and policy count,
  **and checks the connecting role** — because `FORCE` only binds the table *owner* and doesn't
  constrain a `BYPASSRLS`/`SUPERUSER` role at all, so an all-green report from the default
  `postgres` role would be meaningless (the exact trap hit on 2026-07-21). Tables in
  `EXPECTED_PERMISSIVE` (LangGraph's checkpoint tables, which have no `org_id` and are isolated by
  `thread_id` inside LangGraph's own queries) are reported as expected rather than flagged — a
  noisy report stops being read. Run live 2026-07-26: **5 tables, 0 without a policy, connecting as
  the restricted `aca_app`**. Absent `DATABASE_URL` = says so and exits 0.
- [run_solo.py](scripts/run_solo.py) — §16.0: one-command launcher for the **Solo** tier (no n8n) —
  starts `uvicorn aca.api:api`, `streamlit run ui.py`, `python -m aca.core.poller` and
  `python -m aca.core.scheduler` together, streaming each child's output with a prefix, and stops
  them all cleanly on Ctrl+C. Without it you needed four terminals and four commands to remember,
  which in practice is enough for the poller and scheduler to **never** be started — hence for the
  product to *look* manual when it isn't. Deliberately imports no `aca.*` and no third-party
  package: it lives outside the package and runs as a direct script (which doesn't put the repo
  root on `sys.path`, same constraint as `setup_faq.py`); children are spawned with `cwd` = repo
  root so their own `aca.*` imports resolve. Flags: `--only api,ui`, `--without ui`, `--api-port`.
- [export_graph.py](scripts/export_graph.py) / [export_openapi.py](scripts/export_openapi.py) —
  §16.1.5/§16.1.6: regenerate `docs/assets/graph.dot` + `graph.json` (from the **compiled** graph
  via `graph_topology`, plus `architecture.svg` only if a `dot` binary exists — Graphviz is not a
  project dependency, the UI renders DOT browser-side via viz.js) and `docs/openapi.json`. Both are
  idempotent and are re-run in CI to prove the committed artifacts haven't drifted from the code.
  `openapi.json` is committed on purpose: a schema generated on demand can't be imported by someone
  who hasn't yet got the project running.
- [Dockerfile](Dockerfile) / [docker-compose.yml](docker-compose.yml) — §16.1.5: one image for all
  four services, and two compose profiles — `solo` (api + ui + poller + scheduler, 4 services) and
  `enterprise` (the same **plus n8n**, 5). Validated by counting the services each profile resolves
  to; the image itself has never been built (Docker isn't installed on this machine).
- [n8n/](n8n/) — §16.1.5: `aca_workflow.json` (importable workflow) + `README.md` (setup, the 5
  outbound events, envelope shape, HMAC verification, useful endpoints). Never exercised against a
  real n8n instance — none exists for this project; n8n would simply be an HTTP client of `api.py`.
- [.github/workflows/ci.yml](.github/workflows/ci.yml) — §16.4, closes §15.4.7 (there was no
  `.github/` at all). Three jobs: the test suite on Python 3.11 (the README's stated floor) and 3.14
  (the actual dev version), `pip-audit`, and a check that the derived artifacts above haven't
  drifted. Possible at all only because the suite is **fully offline** — `conftest.py` blanks every
  key before any `aca.*` import and redirects all 8 SQLite paths to a temp dir — so it runs on a
  public runner with **no secrets**. Won't execute until the first push to a remote.
- [static/landing.html](static/landing.html) — §16.5, rebuilt §23, turned into an **English
  marketing surface at §23.1**, made commercially operable at **§23.2**. Carries the same "verified
  live vs. not" section as the README — putting that on the sales page rather than burying it is a
  choice. Printable to PDF via a `@media print` block. Not hosted anywhere (same reason as
  §15.1.9's TLS).
  - **§23.2 — `CONFIG`, the one seam.** A single object at the top of the `<script>` holds every
    commercial URL (`calendly`, `contact`, `demo`, `pay.discovery|build|care`). Nothing here is read
    from the environment and nothing can be: this is a static file served as-is, so `.env.example`'s
    §7 block is an operator's *aide-mémoire*, explicitly not configuration. Each field empty
    degrades to something honest rather than to a dead control — the project's "absent = feature
    skipped" contract, restated as "never absent = a link to nowhere".
  - **§23.2 — the calendar was fiction, and could not have been anything else.** It had no
    availability data, no `fetch`, no backend, and offered every future weekday including full ones;
    its own footnote admitted it booked nothing. "A booked day turns grey" is therefore not a
    rendering problem but a *source of truth* problem, and the fix is to cede the card to **Calendly**,
    which already knows the diary. Two branches: `CONFIG.calendly` set ⇒ a themed `<iframe>`
    (`hide_event_type_details`/`background_color`/`primary_color`, plus `utm_content=<tier>` and
    name/email prefill) injected by `IntersectionObserver` at `rootMargin:400px`, so a visitor who
    never reaches the section never contacts Calendly; empty ⇒ the designed picker stays, untouched.
    Deliberately a **plain iframe, never `assets.calendly.com/…/widget.js`** — the page loads zero
    remote scripts and that property outweighs auto-resizing (the price is a fixed frame height, set
    in CSS at two breakpoints). The offline picker is **kept, not deleted**: it is what lets the file
    still work as a self-contained document, which was the entire reason it has no dependencies. The
    head comment declaring "no remote script, no remote image" was **amended rather than left
    standing** — a file that documents its own trade-offs must not silently acquire a new one.
  - **§23.2 — three tiers that finally do three things.** All three CTAs were `<a href="#book">`, so
    a $1,000 click and an $18,500 click were indistinguishable at runtime and the chosen tier reached
    nothing. Now `data-tier`/`data-cta`, three distinct labels, and a `.tier__when` line stating when
    money actually moves: discovery **paid online up front** (Stripe's own post-payment redirect
    pointed at `CONFIG.calendly` makes pay → book one chain with no server on our side), build
    **quoted after the scoping call** (`pay.build` left empty on purpose), care **monthly, starting
    after handover**. The choice survives the jump to `#book` in three places — a visible chip, the
    Calendly `utm_content`, and the mail body — because a distinction the reader cannot see the page
    register is decorative. `.tier__when` is deliberately **not** in `--signal`: amber means "a human
    must decide" and appears three times on this page; spending it on a caption would empty it.
    Stripe itself is a **seam, not an implementation** — no Checkout Session endpoint, no
    `POST /stripe/webhook`, no order store; when built, mirror `/slack/interactions` (outside
    `require_api_key`, signature-verified over the raw body, failing closed without the secret).
    Not to be confused with [billing.py](aca/integrations/billing.py), which meters tokens and
    collects nothing.
  - **§23.2 — security: 12 flat rows → 5 collapsible groups**, reusing the FAQ's mechanics wholesale
    (`data-open`, `grid-template-rows:0fr→1fr`, the `.faq__sign` glyph), so its reduced-motion
    escape hatch and print override are inherited rather than reimplemented. Five groups and not the
    planned four because the grouping is **contiguous** on the existing 01→12 order: no item had to
    move, hence none of the twelve long bilingual strings had to be retyped and silently corrupted.
    Panels are **independent**, unlike the FAQ's one-at-a-time — someone comparing two controls has a
    real reason to hold both open. All twelve stay in the DOM when collapsed, so `Ctrl+F`, indexing
    and print still find them, and the print block forces the groups open.
  - **§23.2 — the section the page was missing, and the FAQ it contradicted.** Three tiers were sold
    and nothing said what arrives after paying, so a new "05 — How you get access" (FAQ → 06,
    Contact → 07) walks demo → pay-or-scope → provisioning **on the client's infrastructure** →
    branding. Delivery model settled explicitly as **hybrid**: the demo is hosted by us and holds no
    real data, production lives with the client. That made the FAQ's "nothing is hosted on our side,
    because there is no 'our side'" half-false, so both affected answers were rewritten instead of
    left contradicting what the page now sells. Two ducked questions added (what am I paying for and
    when; who owns the keys and pays for the tokens), plus `og:`/`twitter:` share meta and an
    `Organization` JSON-LD — deliberately **no** `FAQPage` block (its answers must match the rendered
    text word for word, i.e. a second copy of eight bilingual answers in the same file, the exact
    divergence risk that made French an inline attribute here) and **no** `Offer`/price block (the
    three figures are still placeholders, and a placeholder in a search index outlives the page).
  - **§23.2 — four defects found by rendering, none visible on re-reading.** (1) **The contact form
    went nowhere**: `mailto:?subject=…` carried *no recipient*, so every submission opened a mail
    client with an empty `To:` — the page's only conversion path, broken from the start and silent,
    because nothing raises when you do that and the form appeared to work. (2) **`[hidden]` hid
    nothing**: `.chip{display:inline-block}` and `.btn{display:inline-flex}` are author rules of the
    same specificity as the UA's `[hidden]{display:none}`, and author wins — so an empty pill sat on
    the page permanently and *both demo buttons showed with no demo configured*, precisely what the
    attribute was there to prevent; fixed globally with `[hidden]{display:none !important}`. (3) **An
    open accordion read "|"**: rotating the glyph 90° swaps which bar is horizontal, so the *wrong*
    one was being faded — a pre-existing FAQ defect, inherited by the new groups and fixed for both.
    (4) A chosen time survived a date change, focus was destroyed on every calendar selection, and
    month nav had no lower bound. Verified on **both** branches in a real browser (the `mailto:`
    string captured via a probe copy, since Chromium refuses to let a test redefine
    `window.location.href`). **Not** verified, and stated as such: no full day has been *seen* to
    grey out — that is Calendly's rendering and no account exists for this project; our half (ceding
    the card, injecting at the right moment with the right parameters) is what was checked. No Stripe
    link has been created or tested, the three prices remain placeholders, and nothing was opened on
    a real phone.
  - **§26 — every illustration rebuilt in typographic blocks, and one real photograph.** The
    reference page's art direction was **measured in a browser, not inferred from a screenshot**,
    and the finding is that all of its illustrations are a single technique with no canvas, no SVG
    and no image behind any of them: a `<div>` of monospace text using **only three Unicode shade
    glyphs** (`░ ▒ ▓`, U+2591-93) at 10px / `line-height:1em` / `letter-spacing:0` /
    `rgb(176,176,176)`, **drawn twice** — a sharp copy over a `blur(11px)` copy. The blur is not a
    finish, it *is* the effect. Six instances confirmed one by one (mass 47×170; icons
    39×64/51×83/63×104/60×98 in `#00a2ff`/`#ff4ffc`/`#47e6c3`/`#ffbd2e`; footer hand 69×170); the
    only image the whole site downloads is the hand photo. This page's renderer therefore moved off
    the punctuation ramp `" .:-=+*#%@"` — honest ASCII art and the wrong medium, since punctuation
    has internal shape so the field reads as *writing*, whereas shade blocks fill their em box and
    tile into continuous tone. `--font-block` is a **separate stack from `--font-mono`**: Fragment
    Mono has no shade glyphs, so they would resolve through per-glyph fallback at a different advance
    width and every row would shear (a check asserts one distinct row width per field).
    **The two ASCII hands are gone, and could not have been fixed.** Three successive redraws all
    resolved to ovals for a structural reason, not a drawing one: at this resolution the gap between
    two fingers is under one cell and the dither merges it. The section now opposes a mass
    **synthesised every frame** to an **actual photograph** — the contrast runs through the media
    rather than only through the caption. Three defects only rendering showed: (1) three of the four
    icons came out as featureless slabs, because a sprite pixel was 5.3 screen px on the old
    upscaled canvas and is ~1 cell through a 27-row grid, which the 0.9px pre-blur then closed —
    hence the stated rule *no feature or gap under 2 sprite pixels, outline rather than fill*, a
    40-row grid, and a staggered pipeline (three equal nodes joined at one height make a bar however
    wide the notches). **Resizing these safely means scaling width and type size together** — the
    column count is width ÷ (font-size × advance) and the row count follows from it, so shrinking
    the width alone halves the rows and destroys the icons all over again; holding the ratio at
    40px of width per 1px of type keeps the ~73×40 grid at any size, which is how they went from
    280px/7px to **200px/5px** on the follow-up "make them smaller" without losing a feature;
    (2) the mass rendered as a solid silhouette until the dither amplitude became
    a per-field `grain` (0.22 for icons, **0.55** for the mass) — same code, same shape, and that one
    number is the difference between a slab and a cloud; (3) a stray `*/` closed a CSS comment six
    lines early and left prose in the stylesheet, which the browser silently dropped — the same class
    of failure as §17's swallowed audit line and §20's vanished monthly report, now covered by a
    computed-style assertion. **`static/aca-hand.png`** (1040×585, 163 KB) is **local, not remote**,
    and the header comment's older "no image at all" claim was amended rather than left standing;
    stored **greyscale + alpha** because the page renders it desaturated anyway (colour was 487 KB,
    and palette-quantising to 69 KB banded the forearm visibly, while an 8-bit luminance ramp cannot
    band), and desaturated *in the file* so it survives where CSS filters do not. Its **provenance is
    stated**: a stock cut-out from the Framer template this page's art direction follows — fine for a
    prototype, to be replaced or licensed before any commercial publication. The footer hand is
    **precomputed and shipped as literal text**, because reading an image back off a canvas is
    blocked under `file://` and would have silently deleted that artwork in exactly the offline
    self-contained case this page exists to survive. Deliberately **not** reused: the reference's
    three stock portraits — this page's "testimonials" section says outright there are no customers
    yet and cites §16/§17/§21 findings instead, and stock model photos there would turn an honest
    section into a fabricated one. Also fixed in passing, found by looking at an anchor-jumped
    section on mobile: no `scroll-margin-top` existed, so every in-page link (including "Why us?",
    which targets this very section) put its heading under the 70px sticky nav.
  - **§23.1 — one file, two languages.** English is the default and lives in the markup; French
    rides along per element in `data-fr`, and the toggle stashes the English original in `data-en`
    on first switch so it stays reversible indefinitely (verification drove two full round trips,
    since a toggle that destroys the original on the first switch looks fine exactly once).
    Deliberately **not** a second translated file — that copy would have diverged at the first
    content edit, the same reasoning that moved this page out of `docs/`. With JS off the page is
    complete English, never blank.
  - **§23.1 — every illustration is drawn at runtime, so the page still fetches no image.** Two
    ASCII fields (a four-pointed AI sparkle in the hero; a diffuse machine mass, the glyph, and a
    human hand reaching toward it in "The Human-AI Intersection") are rasterised on an offscreen
    canvas and mapped to characters through a *fixed* 4×4 ordered-dither matrix — fixed, not
    random, so texture drifts with the shape instead of sparkling frame to frame. The four
    capability icons are 28×28 canvases upscaled with `image-rendering: pixelated`, which is what
    yields the chunky sprite look; a vector would render smooth and a bitmap would be an asset to
    download. One shared 16 fps loop drives both fields and **stops** off-screen and on tab-hide.
  - **§23.1 — the aspect trap, and three defects only rendering revealed.** A monospace cell is
    ~1.5× taller than wide and the field is far wider than tall, so mapping a unit square onto the
    grid stretches a circle ~3×; the character box is therefore *measured on the live page* (the
    fallback mono font has a different advance width from Fragment Mono) and shapes are drawn
    through a helper that pre-divides x by that ratio. Then, all found by looking rather than
    re-reading: the star's Bézier control points at 0.17 made the sides nearly straight, so it
    rendered as a **lozenge with no points**; the glow behind it was *larger* than the star and its
    falloff filled the concave notches, so the shape was drawn correctly and could not be seen; and
    a four-fingered open hand collapsed into an amorphous blob because the gaps between fingers
    fall below one character cell — replaced by a pointing hand (fist, one thick index, thumb) on a
    taller field, which survives the downsampling and says "reaching toward" more plainly anyway.
    A fourth, in the pixel set: the pipeline icon joined four corner nodes all the way round and
    read as a *frame*, not a flow; it is now a left-to-right chain with one branch.
  - **§23.1 — pricing, calendar and contact, each honest about what it does.** Three service tiers
    carry the reference template's structure; **the figures are placeholders**, flagged in the file
    header and marked `data-placeholder-price`, because they come from no costing exercise for this
    project. The dark month calendar is real (Monday-first, locale-aware, past days and weekends
    unselectable — offering a slot that cannot exist is the kind of small lie that costs trust on a
    page whose whole argument is that it does not overclaim), and the contact form builds a
    `mailto:` carrying the chosen slot rather than POSTing to a backend that does not exist.
  - Four things about the §23 base are decisions rather than details:
  - **It lives in `static/`, not `docs/`** — Streamlit exposes that folder on `/app/static/`
    (`server.enableStaticServing`, added to `.streamlit/config.toml` and preserved by
    `branding.merge_config_toml()`), which is what lets `ui.py`'s sidebar "Page de présentation"
    button reach it. One file, one URL: a copy in `docs/` **and** one in `static/` would have
    diverged at the first content edit. `ui.py` checks the file exists **and** that static serving
    is on, saying which is missing rather than rendering a link that 404s.
  - **§16.5's "no remote resource" rule is deliberately relaxed.** That rule was right (a pitch page
    depending on a CDN doesn't open on a train or behind a corporate proxy — exactly where it gets
    shown), but the requested art direction rests on three specific typefaces (Instrument Serif /
    Figtree / Fragment Mono). The trade is bounded and stated in the file header: the `<link>` is an
    enhancement, never a dependency — every family is backed by a full system stack and the page
    stays fully readable and correctly set offline; no remote script or stylesheet at all, all
    CSS/JS inline.
  - **Structure and motion copy the requested template; the accent colour does not.** Near-white
    `#fdfdfd` canvas, two vertical rules framing a 1176px column, bracketed `[nav links]`,
    dashed-border cards, black schematic tags on leader lines, printer crop marks, and a blur+rise
    reveal on scroll — all measured off the live reference page (computed styles, not eyeballed
    from a screenshot); the rebuilt frame lands at 1176px/x=132 against the original's 1177px/x=131.
    The accent is ACA's own petrol/amber, and amber keeps its §19 meaning ("a human must decide
    here"), so it appears exactly three times on the whole page. Hero motion is a deterministic
    ASCII field (no `Math.random`, which would make characters flicker between frames instead of
    drift) whose disordered inflow converges to a single mark; it is the page's only continuous
    animation and it **stops** off-screen and on tab-hide.
  - **Four defects found by rendering it rather than re-reading it**, none of which raises an error:
    the convergence mark fell *between* rows (even row count) **and** inside the right edge-fade, so
    the one meaningful part of the animation was never drawn; an SVG `fill=` presentation attribute
    lost to a CSS rule, painting the "ACA" label black-on-black; the mobile grid override dropped
    `minmax(0,1fr)` to a bare `1fr`, letting the terminal block force 472px of content into a 390px
    viewport; and the footer's relative `../README.md` links would have 404'd once served (now
    rendered as paths, not links). A fifth was a placement bug in `ui.py` rather than the page: the
    button sat after the admin-only knowledge-base block, so it appeared in a different place
    depending on the signed-in role.
- [DEPLOYMENT_HARDENING.md](docs/DEPLOYMENT_HARDENING.md) — §15.1.8/§15.1.9: the operator runbook
  for a first public deployment — Caddy/Nginx TLS configs (incl. the Streamlit WebSocket headers
  whose absence leaves the UI stuck on "Connecting…"), security headers, loopback-only binding,
  secret-handling rules and a per-secret rotation table, plus the honest limits section. Exists
  because those two items aren't code: nothing is hosted, so they're configured on deploy day.
- [api.py](aca/api.py) — FastAPI microservice (§12 item 6 — n8n port "Option A", audited §14): exposes the
  compiled graph over HTTP for a future **self-hosted** n8n workflow (n8n Cloud is paid, cf. §11.5)
  to drive instead of `poller.py`/`ui.py` — `POST /threads` starts an analysis, `GET /threads/{id}`
  reads its state, `POST /threads/{id}/clarifier` answers a pending dynamic clarification, and
  `POST /threads/{id}/valider` is the **only** endpoint that resumes past `interrupt_before=["action"]`
  (same human-in-the-loop contract as `ui.py`'s "Valider" button — optionally with an
  `edited_draft`; now also mirrors `ui.py`'s post-validation bookkeeping —
  `queue_store.mark_validated()`, `audit_log.log_validation()`, `analytics_store.record_validation()`
  — which the endpoint didn't do before the `dashboard/` client existed, §12 item 8). `GET /metrics`
  exposes Prometheus-format counters/histogram (§12 item 9, audited
  §14 — `aca_emails_classified_total`, `aca_leads_validated_total`, `aca_tokens_per_analysis`);
  the roadmap marks this "useful only once §12 item 3 [multi-tenant] exists and several clients
  run" — it exists now that the org_id foundation does, but is inert until something actually
  scrapes it. Launch: `uvicorn aca.api:api --port 8000`. **Extended for the dashboard (§12 item 8,
  2026-07-21)**: an optional `require_api_key` dependency (`ACA_API_KEY` — same graceful-degradation
  contract as `ACA_UI_PASSWORD`, absent = no gate) on every route except `/metrics`; `GET
  /threads/pending` (`queue_store.list_pending`) and `GET /threads/history`
  (`audit_log.list_recent`) — declared *before* `GET /threads/{thread_id}` in the file, since
  FastAPI/Starlette resolves same-shaped routes in registration order, a real bug caught by the new
  tests; `POST /threads/{thread_id}/rejeter` — a genuinely new capability (`queue_store.
  mark_rejected`), since no "reject a lead without writing to CRM" path existed anywhere before,
  not even in `ui.py` (a rejection was just never clicking "Valider"); `GET /stats` (bundles
  `analytics_store`'s aggregates); `GET`/`POST /settings` (wraps `config_store`). **Slack approval
  loop (2026-07-22)**: `POST /slack/interactions` receives clicks on the "✅ Valider"/"✕ Rejeter"
  buttons from `notify.send_approval`'s alert, so validation can happen inside Slack with no UI. It
  is **not** behind `require_api_key` (Slack won't send the header) — instead it verifies Slack's
  HMAC signature (`slack_verify.py`, `SLACK_SIGNING_SECRET`) and **fails closed** (503) if that
  secret is unset. The shared `_do_validate`/`_do_reject` helpers back both the REST endpoints and
  this Slack path (one source of truth for the CRM-write + post-validation bookkeeping). Slack's
  3-second response budget is usually met by `action_node` at prototype volume; a production
  high-load version would ack immediately then update via `response_url` (noted as a known limit,
  not built). **n8n enablement (§16.1, 2026-07-26)** — the API existed but n8n could not have driven
  it properly; five obstacles, two of them blocking: (1) **§16.1.1 attachments** — `POST /threads`
  hard-coded `attachments_raw: []`, which made multimodal analysis (the project's *first* innovation
  pillar) unreachable from the API, hence from n8n, even though `ingestion_node` had handled it all
  along; base64 `attachments` are now accepted and bounded (10 files, 20 MB decoded total), rejected
  with a 422 **before** anything is decoded into memory and before the LLM, same principle as
  §15.1.4's payload bounds. (2) **§16.1.2 outbound webhooks** — see
  [webhook.py](aca/integrations/webhook.py); `_emit_if_clarification()` sends
  `analysis.clarification` from `_run_analysis` rather than from `clarification_node`, because
  `interrupt()` **replays the node from its start** on resume, so an in-node send would fire twice
  for one question; it is the only branch where the graph stops without reaching
  `notification_node`, so without it an `?mode=async` caller went silent on an ambiguous email.
  (3) **§16.1.3 `GET /health`** — deliberately outside `require_api_key` (an orchestrator must be
  able to probe without holding the key that writes to the CRM), strictly boolean (never a secret
  value — a test locks this) and making **no external call** (probed every 10s by Docker, it must
  neither burn quota nor fail because an optional third party is down). (4) **§16.1.4 idempotence +
  async** — n8n's HTTP node retries by default, and without a guard a mere network retry re-ran a
  full analysis (two 70B calls, Tavily/Gemini quota) **and re-notified** the team; a known
  `thread_id` now returns the existing snapshot with `already_exists: true` (the API-side counterpart
  of the idempotence `poller.py` already gets by marking "en_cours" before `invoke()`), and
  `?mode=async` returns 202 immediately, signalling completion via the webhook — sync stays the
  default so no existing client changes. (5) **§16.1.5** — `docs/openapi.json` is exported and
  committed. Covered by [test_api.py](tests/test_api.py) (36 tests) and
  [test_api_n8n.py](tests/test_api_n8n.py) (§16.1) via `fastapi.testclient.TestClient` with the same
  fake-LLM pattern as `test_graph_integration.py` — the Slack tests build genuinely HMAC-signed
  requests and cover approve/reject/bad-signature/unconfigured. Not exercised against a real n8n
  instance or a real Slack app (neither exists for this project; both would simply be HTTP clients
  of this API).
- [dashboard/](dashboard/) — the dedicated Next.js client dashboard (§12 item 8), **built
  2026-07-21 at the user's explicit request** — previously deliberately deferred as a product/
  hosting decision, not a code gap. Next.js 16 (App Router, TypeScript, Tailwind v4). **Product
  positioning (updated 2026-07-24 — the dashboard is PARKED):** a code-grounded inventory of the
  three surfaces found the dashboard is a *review-only subset* of Streamlit — it lacks intake
  (`POST /threads`), knowledge ingestion, and FAQ curation entirely, so it **cannot run standalone**
  (it depends on the poller/Streamlit to feed its queue) and is not deployed. Decision: **Streamlit
  (`ui.py`) is the single operational spine today**; the dashboard stays in the repo as a built
  *showcase* but gets no further investment for now, and the "client cockpit" direction (queue,
  visual agent graph, HITL approve/reject/edit, settings, usage — with Streamlit's curator pieces
  eventually moving to a role-gated `(admin)` group) becomes a **deferred future path**, not the
  active plan. Slack (Valider/Rejeter) already covers approval convenience; n8n stays orthogonal
  future plumbing. (An earlier 2026-07-22 framing had positioned the dashboard as the long-term UI
  spine — **superseded** by this parking decision; see roadmap §12bis.) Talks only to
  `aca/api.py`, never to the database directly — `ACA_API_KEY` is attached server-side
  (`lib/aca.ts`, marked `server-only`) and never reaches the browser. Own password gate
  (`DASHBOARD_PASSWORD`, HMAC-signed session cookie via `lib/session.ts`, checked in `proxy.ts` —
  renamed from `middleware.ts`, the Next 16 convention) — same shared-secret pattern as
  `ACA_UI_PASSWORD`, not a real multi-user auth system. Signature element:
  `components/pulse-graph.tsx`, an animated SVG rendering of the real `StateGraph` topology
  (`lib/graph-topology.ts`, kept in sync with `aca/core/app.py` the same way `ui.py`'s `GRAPH_EDGES`
  already is) — an ambient looping animation on the login background, and a live progress view
  (active/done nodes) inside the HITL drawer. Dependencies install via **pnpm** (`pnpm install` in
  `dashboard/`) — npm's `ERR_SSL_CIPHER_OPERATION_FAILED` (a Node/OpenSSL TLS-1.3 bug on this
  Windows box) blocked the plain `npm` path; `.npmrc` also raises `fetch-timeout` for the large
  `next`/`swc` binaries. See [dashboard/README.md](dashboard/README.md) for setup and the full
  file-by-file breakdown. Runs locally via `npm run dev`/`pnpm dev` (verified: login page renders
  with the animated graph; backend on `uvicorn aca.api:api --port 8000`) — not deployed anywhere yet
  (hosting still a deferred decision).
- [eval_dataset.json](aca/eval/eval_dataset.json) — 50 synthetic labeled emails (10 per category, a few
  deliberately ambiguous) for [eval_classifier.py](aca/eval/eval_classifier.py), which runs each through
  `classifier_node` and reports overall/per-category accuracy + misclassifications. Last measured
  (2026-07-12, after the switch to structured output + confidence score): **100% (50/50)**, up from
  96% (48/50) pre-migration — both prior errors were on deliberately ambiguous cases. Run via
  `python -m aca.eval.eval_classifier`; re-run once real emails are available to track accuracy
  under real conditions instead of the synthetic set.
- [tests/](tests/) — automated pytest suite (**773 tests**, offline, ~40s — see Known gaps for full
  coverage list): [conftest.py](tests/conftest.py) (env isolation + `FakeLLM`/`ExplodingLLM`, now
  also blanking `ACA_ORG_ID`/`STRIPE_API_KEY`, redirecting `ACA_CONFIG_DB`/`ACA_USERS_DB`, and
  neutralising every §15 security switch — `ACA_ENV=development`, empty `ACA_API_KEY`/
  `ACA_METRICS_TOKEN`/`ACA_UI_PASSWORD`/`ACA_AUDIT_HMAC_KEY`, `ACA_RATE_LIMIT=0` — so each test
  turns on only what it verifies),
  [test_graph_nodes.py](tests/test_graph_nodes.py) (incl. §13: `scan_risks()`, `risk_scan_node`,
  `knowledge_gap` propagation, `sum_usage()`, §11.6's `ingestion_node`, and §12 item 7's
  `config_store` overrides for Calendly/routing), [test_sheets_helpers.py](tests/test_sheets_helpers.py),
  [test_storage.py](tests/test_storage.py) (incl. `sqlite_retry.py` coverage, §13's
  `record_edit`/`edit_rate`/`record_tokens`/`token_stats`, §11.6's multi-round `followup_store`
  cadence + legacy-schema migration, and §12 item 7's `config_store` get/set/org-scoping),
  [test_degradation.py](tests/test_degradation.py) (incl. `billing.py`'s disabled-state contract),
  [test_graph_integration.py](tests/test_graph_integration.py),
  [test_multitenant.py](tests/test_multitenant.py) (§12 item 3 — org_id isolation across all four
  local stores, same tenant scenario RLS reproduces on Supabase),
  [test_auth_lockout.py](tests/test_auth_lockout.py) (§14 item US-41),
  [test_billing.py](tests/test_billing.py) (§12 item 4, via a fake Stripe client), and
  [test_api.py](tests/test_api.py) (§12 item 6 + item 8 — 36 tests incl. the Slack approval loop:
  genuinely HMAC-signed `/slack/interactions` requests covering approve/reject/bad-signature/
  unconfigured, via `fastapi.testclient.TestClient`; plus §15's strict payload validation — an
  oversized body must 422 *without* the LLM being called — the `/settings` key whitelist, the
  constant-time API-key compare, production fail-closed, and the `/metrics` token), and
  [test_security.py](tests/test_security.py) (§15 — 43 tests: password hashing/salting/cost
  migration, credential verification, disabled accounts, per-tenant user isolation, the
  role/permission matrix incl. fail-closed on an unknown role, session absolute-TTL vs. idle
  expiry and the guarantee that activity never extends the absolute TTL, prompt-injection
  detection with a zero-false-positive set of realistic sales emails, and `prod_check`'s
  development-vs-production behaviour). `test_storage.py` also covers §15.2.7's audit chain
  (detecting an edited row, a *deleted* row, HMAC keying, per-tenant chains, legacy rows) and
  §15.2.4's per-subject erasure. **§16 adds five files**:
  [test_scheduler.py](tests/test_scheduler.py) (18 tests — due-date arithmetic on an injected
  `now`, `0` disabling a job, a **failed** run still being recorded so a dead service isn't
  hammered every tick, `--prime` idempotence),
  [test_demo_mode.py](tests/test_demo_mode.py) (30 tests — incl. the whole graph running end-to-end
  with **no API key at all**, and `guard_write()` raising rather than degrading),
  [test_api_n8n.py](tests/test_api_n8n.py) (§16.1 — attachments incl. invalid base64 and the size
  cap rejected *before* the LLM, `/health` leaking no secret and making no external call, retry
  idempotence, `?mode=async`, and each of the 5 webhook events fired at the right moment),
  [test_webhook.py](tests/test_webhook.py) (the two properties that matter most first: **never
  raises** — network error, HTTP error, unserialisable payload — and no-op without config; plus
  HMAC verifiability by the receiver and signature-over-the-exact-bytes-sent), and
  [test_graph_topology.py](tests/test_graph_topology.py) (topology genuinely derived from the
  compiled graph, every node labelled). **§17 adds two files**:
  [test_branding.py](tests/test_branding.py) (46 tests — resolution priority, so a client's chosen
  colour is never silently overridden by a preset or by dark mode; an invalid hex never reaching the
  injected stylesheet; `prefers-reduced-motion` present at *every* animation level, since an OS
  accessibility setting must never be contradicted by an app setting; `merge_config_toml` preserving
  `[server]`/`[browser]` and being **idempotent** — that last one found a real bug where re-applying
  left an empty `[theme]` section, and the test's own first version was wrong too, counting the
  string "[theme]" that also appears in the generated header comment) and
  [test_activity_log.py](tests/test_activity_log.py) (now 58 tests — the two properties that matter
  most first: `log()` **never raises** even with an unreachable DB or unserialisable details, and the
  chain detects an edited row, a *deleted* middle row and a tampered `details`; plus IP normalisation
  against a forged `X-Forwarded-For`, per-tenant chains, the device/user-agent parsing, the
  regression test for the silently-lost audit line, and — added §18 — `lead_timeline` ordering,
  `is_new_device`/`known_devices`, the two-speed `purge_older_than`, `csv_export`, and
  `rows_for_period`/`archive_period` incl. its idempotence). **§18 adds four files and extends
  three**: [test_ui_kit.py](tests/test_ui_kit.py) (24 tests — HTML escaping first, since email
  bodies/LLM output flow through every one of these builders; the `tone`/`state` → CSS-class mapping;
  defined behaviour on the empty cases), [test_totp.py](tests/test_totp.py) (19 tests — `code_at`
  checked against the **official RFC 4226 Appendix D test vectors**, not just the module's own
  assumptions; `verify()` never raising on malformed input, the one property that matters most for
  code guarding an admin account; the drift window bounded on both sides),
  [test_pdf_export.py](tests/test_pdf_export.py) (14 tests — a genuine round-trip: build a PDF,
  reopen it with `fitz`, extract the text, assert the company/contact/draft/human-review-footer are
  actually present, not merely "no exception"; filename sanitisation against path-traversal and
  header-injection payloads), and [test_retention.py](tests/test_retention.py) (3 tests, deliberately
  narrow — only the §18 two-speed wiring `retention.py` → `activity_log.purge_older_than`, since the
  rest of `retention.py` has no offline path — Google Sheets can't be simulated — and stays verified
  live, per the note in Known gaps). `test_security.py` gains `ROLE_VIEWER`'s permission boundary
  (read-only, explicitly excluding `reject_lead`), the full TOTP round trip (`totp_required`,
  set/get/has/`verify_totp`, tenant isolation, `list_users()` reporting the flag without ever leaking
  the secret), and `session.seconds_until_expiry` (the stricter of the two bounds, `None` when both
  are disabled, the absolute-TTL bound unaffected by `touch()`). `test_storage.py` gains
  `analytics_store.get_draft_edit` (most-recent-edit semantics, no cross-thread bleed).
  [test_ui_shared.py](tests/test_ui_shared.py) (2 tests — `_totp_qr_png()` returns real PNG bytes,
  distinct secrets produce distinct images) is the one addition after the pass's own initial count:
  the enrollment secret ended up rendered as an actual scannable QR rather than raw `otpauth://`
  text, on user follow-up request. **550 tests total**, offline, ~19s. **§18 tangent adds one more
  file**: [test_i18n.py](tests/test_i18n.py) (11 tests — `translate()` never raising on an unknown
  key/language, every declared key genuinely non-empty in both languages, `{placeholder}` formatting,
  and a sample-key check that FR and EN actually differ, so a future key copy-pasted identically into
  both languages can't silently defeat the switcher). **§20 adds three files**:
  [test_review_store.py](tests/test_review_store.py) (21 tests — ordered by what the registry
  actually carries, namely *work one person handed to another*: a request reaches its recipient and
  nobody else, in both failing directions; a batch is genuinely a batch; an already-decided request
  can't be decided twice, since two admins can open the same queue at once; "seen" never silently
  becomes "resolved"; tenant isolation; and the purge spares *pending* requests at any age),
  [test_reporting.py](tests/test_reporting.py) (27 tests — a report is read by someone without
  access to the raw data who will make decisions on it, so **a wrong figure is worse than a missing
  section**: exclusive upper bounds proved by an event sitting exactly on a month boundary being
  counted once and only once; the comparison window being same-length rather than "previous month";
  a failing section degrading to a text block instead of taking the report down *and* instead of
  staying silent; every section carrying its context; and no deltas at all when comparison is off,
  rather than "0 %" everywhere), and
  [test_report_pdf.py](tests/test_report_pdf.py) (25 tests — a genuine round trip: build, reopen
  with `fitz`, extract the text and assert the content is really there, since "no exception" is
  also true of a blank page; never-raises on absurd input; the Latin-1 substitutions incl. an
  unmanageable Unicode subject; accent-aware measurement; and idempotent disk writes).
  `test_scheduler.py` gains 4 tests for the monthly `report` job (a file is really produced, an
  existing one is never overwritten, a render failure is raised rather than swallowed — otherwise
  the scheduler would record "ok" and not retry for a month — and a failed notification does not
  turn a produced report into a failure). **§21 adds 65 tests to
  [test_branding.py](tests/test_branding.py)** (90 → 120 there, incl. 4 parametrized over all 18
  shipped presets), deliberately asserting the **emitted stylesheet** rather than the intent — every
  defect that pass found had survived several reviews precisely because a dead CSS rule raises no
  exception, breaks no test and is invisible on re-reading: the display serif never reaching a real
  heading, the header silently reverted to transparent, `--aca-header-h` stated in `rem` under a
  14 px root, the accent collapsing into the primary on 4 palettes, and duplicate colours in the
  categorical chart palette. **773 tests total**, offline, ~40s. Run via
  `python -m pytest tests/` (pytest pinned in requirements.txt).

## Stack

LangGraph (supervisor graph, `SqliteSaver`/`PostgresSaver`, static + dynamic `interrupt`) ·
`langchain_groq` (Groq-hosted Llama models, free tier, chat only — Groq has no embeddings endpoint) ·
`google-genai` (Gemini embeddings, free tier, semantic RAG only) · `psycopg`/`psycopg-pool` +
`pgvector` + `langgraph-checkpoint-postgres` (Supabase Postgres — vector store + checkpointer,
optional, see `DATABASE_URL` below) · `tavily-python` (web enrichment, free tier) ·
Streamlit (Fluent theme via [.streamlit/config.toml](.streamlit/config.toml)) · `gspread` + `google-auth`
(Google Sheets as CRM + knowledge base) · `google-api-python-client` + `google-auth-oauthlib` (Gmail) ·
PyMuPDF (PDF) · `python-docx` (Word) · `openpyxl` (Excel) · `python-dotenv` · `fastapi` + `uvicorn`
([api.py](aca/api.py), n8n port §12 item 6) · `stripe` ([billing.py](aca/integrations/billing.py),
§12 item 4, inert without `STRIPE_API_KEY`) · `prometheus-client` (`/metrics` on `api.py`, §12 item
9) · `segno` (§18, TOTP enrollment QR code — pure Python, zero transitive dependencies of its own,
[aca/ui/shared.py](aca/ui/shared.py)). Pinned in [requirements.txt](requirements.txt).

Required env vars (`.env`, gitignored): `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_ID`, a Groq API
key for `langchain_groq`, `GOOGLE_API_KEY` (Gemini, for `search_knowledge_base_semantic`; RAG silently
falls back to keyword search if absent), `TAVILY_API_KEY` (enrichment agent; silently skips enrichment
if absent), and optionally `GMAIL_CREDENTIALS_FILE` / `GMAIL_TOKEN_FILE` (default to
`credentials/gmail_credentials.json` / `credentials/gmail_token.json`), `ACA_CHECKPOINT_DB` (default
`data/checkpoints.sqlite`), `ACA_QUEUE_DB` (default `data/queue.sqlite`), `POLL_INTERVAL_SECONDS` for
[poller.py](aca/core/poller.py) (default `60`), `SLACK_WEBHOOK_URL` / `NOTIFY_EMAIL` for
[notify.py](aca/integrations/notify.py) (both optional; no-ops if absent — see Gmail setup notes for the incoming-
webhook steps, no new account needed for `NOTIFY_EMAIL` since it reuses the existing Gmail auth),
`SLACK_SIGNING_SECRET` (optional — enables the Slack "Valider"/"Rejeter" approval buttons'
callback via `POST /slack/interactions`; absent = the buttons still post but clicks 503, i.e. the
loop fails closed rather than open — see `slack_verify.py`. This is the Slack **app** signing
secret, distinct from the `SLACK_WEBHOOK_URL` used only to post; enabling live approval also
requires turning on Interactivity in the Slack app and pointing its Request URL at
`/slack/interactions`, which needs the API reachable — a public host or a tunnel like ngrok in
local dev), `ACA_API_KEY` (optional shared key gating every `aca/api.py` route except `/metrics`
and `/slack/interactions`; absent = no gate, dev mode — must match the dashboard's server-side
`ACA_API_KEY`), optionally `ACA_RATE_LIMIT` (per-client rate limit on every `aca/api.py` route
except `/metrics` — a sliding window keyed by `X-API-Key` or source IP; absent/≤0 = disabled, dev
mode, same graceful contract; over-limit ⇒ HTTP 429 + `Retry-After`) with `ACA_RATE_WINDOW_SECONDS`
(default `60`) — both read dynamically per request, not frozen at import; in-memory (single-process,
exact at prototype scale — a multi-worker deploy would need a shared Redis backend), `ACA_UI_PASSWORD` (optional password gate for [ui.py](ui.py); absent = no gate — **superseded in
practice by named accounts**, see `ACA_USERS_DB` below: it is only used when no account exists),
and the §15 hardening switches: `ACA_ENV` (`development` by default — set to `production` and
[prod_check.py](aca/core/prod_check.py) makes every protection below **mandatory**, refusing to
start otherwise, instead of the usual "absent = feature skipped"), `ACA_USERS_DB` (default
`data/users.sqlite`, named accounts + roles — [user_store.py](aca/storage/user_store.py)),
`ACA_SESSION_TTL_SECONDS` / `ACA_SESSION_IDLE_SECONDS` (defaults 8h / 30min, session expiry —
[session.py](aca/core/session.py)), `ACA_METRICS_TOKEN` (optional `X-Metrics-Token` guard on
`/metrics`, the one route outside `require_api_key` since a Prometheus scraper sends no application
header; absent = open as before, but `prod_check` refuses it in production), `ACA_ENABLE_DOCS`
(set to `1` to re-expose `/docs`+`/openapi.json`, which are otherwise **off** under
`ACA_ENV=production` — the inverse of FastAPI's default), `ACA_AUDIT_HMAC_KEY` (optional — turns
the audit log's hash chain into HMACs so forging it needs a key held outside the database; ⚠️ the
one secret whose rotation invalidates existing verification, see
[DEPLOYMENT_HARDENING.md](docs/DEPLOYMENT_HARDENING.md)), `ACA_ANALYTICS_DB`
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
present in every fresh HubSpot portal), and optionally (multi-tenant foundation + commercialization
scaffolding, §12/§14) `ACA_ORG_ID` (default `"default"` — one ACA deployment/process = one tenant;
tags every row in the four local stores below plus `faq_embeddings`, and scopes every read to it —
see [tenant.py](aca/core/tenant.py)), `ACA_CONFIG_DB` (default `data/config.sqlite`, backs the
"Réglages" settings panel — [config_store.py](aca/storage/config_store.py)), `STRIPE_API_KEY` /
`STRIPE_SUBSCRIPTION_ITEM_ID` (per-tenant, set via `config_store` not `.env` — usage-based billing,
[billing.py](aca/integrations/billing.py); absent = token stats still computed locally, no Stripe
call, graceful no-op like everything else — ⚠️ not live-verified, no Stripe test account exists for
this project), and `RELANCE_MAX_ROUNDS` (default `3`, overridable via the settings panel — cf.
[followup_store.py](aca/storage/followup_store.py)'s multi-round cadence, §11.6 item 5). §16 adds:
`ACA_DEMO_MODE` (§16.3 — set to `1`/`true`/`yes`/`oui` to run the whole graph with **no API key**,
simulated LLM and CRM writes hard-blocked; absent = normal operation, and it is read dynamically so
it can never freeze on at import), `ACA_SCHEDULE_DB` (default `data/schedule.sqlite`) plus the
per-job cadences `ACA_SCHEDULE_MAINTENANCE_HOURS` / `ACA_SCHEDULE_RELANCE_HOURS` /
`ACA_SCHEDULE_RETENTION_HOURS` / `ACA_SCHEDULE_BILLING_HOURS` (defaults `1` / `24` / `168` / `720`;
`0` or negative **disables** that job — same "absent/zero = feature skipped" contract as everything
else) and `ACA_SCHEDULE_TICK_SECONDS` (default `60` — how often the loop *looks* for a due job, not
how often jobs run), and `ACA_WEBHOOK_URL` / `ACA_WEBHOOK_SECRET` (§16.1.2 — outbound events to
n8n; URL absent = silent no-op, secret absent = unsigned but still sent, unlike
`SLACK_SIGNING_SECRET` which fails closed, since an outbound webhook triggers no CRM write on our
side). §17 adds: `ACA_ACTIVITY_DB` (default `data/activity.sqlite` — the activity journal,
[activity_log.py](aca/storage/activity_log.py)), `ACTIVITY_RETENTION_DAYS` (defaults to
`RETENTION_DAYS`; separate because the two answer opposite needs — a lead is kept for the commercial
relationship, an access trace is kept to investigate afterwards — and because this journal holds IP
addresses, i.e. personal data, whereas `audit.sqlite` is deliberately never purged), and the
optional `BRAND_*` white-label tokens (`BRAND_NAME`, `BRAND_TAGLINE`, `BRAND_COMPANY`,
`BRAND_PRESET`, `BRAND_PRIMARY`, `BRAND_ACCENT`, `BRAND_BACKGROUND`, `BRAND_SURFACE`,
`BRAND_SIDEBAR`, `BRAND_TEXT`, `BRAND_BORDER`, `BRAND_SUCCESS`, `BRAND_WARNING`, `BRAND_DANGER`,
`BRAND_INFO`, `BRAND_FONT`, `BRAND_RADIUS`, `BRAND_DENSITY`, `BRAND_MODE`, `BRAND_ANIMATIONS`,
`BRAND_HERO` — all absent = the default ACA palette, unchanged appearance). Those exist only to ship
an instance *already* in a client's colours without touching the UI; a value set from the "Réglages →
Apparence" panel is stored per tenant and **wins** over the env var. `BRAND_LOGO` is deliberately
**not** settable from `.env`: it is uploaded through the UI and stored encoded, because a file path
breaks the moment the app moves machine or runs in a container with an ephemeral filesystem. §18
adds: `ACTIVITY_SENSITIVE_RETENTION_DAYS` (default: twice `ACTIVITY_RETENTION_DAYS` — the two-speed
retention wired into `retention.py`, keeping `activity_log.SENSITIVE_ACTIONS` — failed logins,
lockouts, role/settings changes — longer than routine usage noise), `ACA_ARCHIVE_DIR` (default
`data/archives` — where `scheduler.py`'s monthly `archive` job writes the signed CSV exports of the
activity journal), and `ACA_SCHEDULE_ARCHIVE_HOURS` (default `720`, same convention as the other four
`ACA_SCHEDULE_*_HOURS` — controls how often the scheduler *checks* for a due archive, not which month
gets archived, which `_last_completed_month()` always computes as the previous calendar month).
§20 adds: `ACA_REVIEW_DB` (default `data/reviews.sqlite` — review requests,
[review_store.py](aca/storage/review_store.py); distinct from `ACA_TASK_DB` because a task is
*dated* and self-executing while a review request is *addressed* and waits on a person),
`ACA_REPORT_DIR` (default `data/reports` — where the scheduler writes the monthly PDF reports) and
`ACA_SCHEDULE_REPORT_HOURS` (default `720`, same convention as `ACA_SCHEDULE_ARCHIVE_HOURS`:
it sets how often the scheduler *checks*, never which month is covered — that is always the last
completed calendar month via `reporting.last_completed_month()`; `0` disables the automatic report
while the "Rapports" page stays usable on demand).
All 83 variables are documented one by one in [.env.example](.env.example) (§16.2.2), which
is the file to read first — this section is the reference, that one is the checklist.

`credentials/` (gitignored) holds `service_account.json` (Sheets) and `gmail_credentials.json` (Gmail
OAuth client secret, "installed app" type). `gmail_token.json` is created there on first Gmail auth.

### Gmail setup notes

`gmail_credentials.json` is an OAuth "installed app" client secret, not a service account — the first
call to `get_gmail_service()` opens a real browser window for the account owner to grant consent
(scope: `gmail.modify`). This can't be done headlessly; run `python -m aca.integrations.gmail_reader` or click "Rechercher
les e-mails non lus" in the UI once locally to complete it. The resulting token is cached and reused
afterward.

## Known gaps

- ✅ **Fixed (2026-07-12)**: automated test suite now exists — `tests/` (95 tests, pytest, run via
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
- ✅ **Fixed (2026-07-21)** — §14 security audit + full §11.6/§12 build-out, at the user's explicit
  request to "finish what's rest in the plan" (confirmed to include §12 P3 commercialization
  items, normally deliberately deferred until the project is declared finished): (1) US-41,
  progressive lockout on `ui.py`'s password gate — previously no attempt limit at all, a real
  brute-force gap; (2) US-42, a GDPR privacy policy document — `retention.py` was a technical purge
  mechanism only, no policy ever existed; (3) §11.6's last unaddressed item, an explicit
  `ingestion_node` — attachment extraction used to live outside the graph, duplicated in
  `ui.py`/`poller.py`; (4) §11.6 item 5, multi-round relance cadence (up to `RELANCE_MAX_ROUNDS`,
  default 3, was capped at exactly one before); (5) a multi-tenant `org_id` foundation across all
  four local stores + `faq_embeddings`, with Postgres RLS (session-variable-based, since this
  project never uses PostgREST/an anon key); (6) a "Réglages" settings panel
  (`config_store.py`) making Calendly/routing/relance settings editable without `.env`; (7) usage
  aggregation already existed (`token_stats`, now org-scoped) — added `billing.py` as the
  Stripe-gated next step, **not live-verified** (no Stripe test account available); (8) a FastAPI
  microservice (`api.py`) exposing the graph for the planned n8n port, **not exercised against a
  real n8n instance** (n8n would just be an HTTP client of this API); (9) a Prometheus `/metrics`
  endpoint. **Deliberately not built in this pass**: a dedicated Next.js/Shadcn client dashboard
  (§12 item 8) — that requires a real framework/hosting decision the audit flagged as inappropriate
  to make unilaterally, unlike the items above which were pure code additions. (Built later the
  same day, once the user explicitly asked for it and answered the framework/auth/scope questions
  directly — see [dashboard/](dashboard/) above.) 160 tests total (up from
  125), all offline; see `docs/ACAM_roadmap.md` §14 for the full item-by-item audit reasoning
  (including two checklist items from the original security audit that were found to be
  non-issues by architecture and correctly *not* built: exposed API keys — no client-side
  frontend exists to expose anything from — and "Supabase wide open" in the PostgREST/anon-key
  sense, which doesn't apply since this project only ever connects via a direct `psycopg`
  connection string).
- ✅ **Fixed (2026-07-26)** — §15 security hardening pass (§15.1, §15.2, and the security-shaped
  §15.3 items; `docs/ACAM_roadmap.md` §15.6 records it item by item). Delivered: named accounts +
  roles (15.1.6), session TTL/idle expiry on both surfaces (15.1.7), strict API payload validation
  and prompt-injection flagging (15.1.4), auth made mandatory in production with constant-time
  compares (15.1.5), secret-rotation and TLS runbooks (15.1.8/15.1.9), a live-verified RLS sweep
  (15.2.2), GDPR right-to-erasure (15.2.4), a hash-chained audit log (15.2.7), no stack-trace
  leakage (15.3.2), locked `/docs`+`/metrics` (15.3.3), and dependency scanning (15.3.8). Suite:
  192 → **261 tests**. Five things the pass *found* rather than merely built — the reason a
  "verify" item is worth more than it looks: (1) four sites in `ui.py` printed raw exception text
  to the screen; (2) `pip-audit` reported 17 known vulnerabilities, 11 of them in two **transitive**
  packages the project never imports (`gitpython` via Streamlit, `pyasn1` via `google-auth`) — so
  "requirements.txt is pinned" was false assurance, since indirect dependencies weren't in it;
  (3) the dashboard's session cookie never expired server-side (the HMAC was a constant; the
  cookie's `maxAge` is browser-enforced and doesn't survive copying the cookie); (4) `POST
  /settings` accepted arbitrary keys; (5) a Postgres subtlety that would have made the RLS report
  misleading — `FORCE ROW LEVEL SECURITY` binds only the table *owner*, so a naive script would
  have flagged LangGraph's four checkpoint tables forever, and a noisy report stops being read.
  Deliberately **not** in this pass (out of the requested "security" scope, unchanged in the
  roadmap): structured logging (15.3.1), circuit breakers (15.3.7) and all of §15.4 (CI, load
  tests, browser E2E, review process). Still genuinely open, with reasons: TLS is *documented but
  not applied* (nothing is hosted), the secrets vault is an hosting decision (no code change will
  be needed — every module reads `os.getenv()` dynamically), local-store RLS is a product call, and
  DPA/DPIA documents belong to the using company.
- ✅ **Fixed (2026-07-26, same day)** — §16 "Solo tier, workable n8n port, first impression". Started
  from a narrow user question ("can I use pgvector alongside Google Sheets, and which flow suits my
  n8n workflow?") which was answered by auditing the code rather than from memory — and the audit
  found something else, which is the real result of the pass: **the product was already autonomous
  but didn't look it, and n8n could not have driven it properly.** Two problems long conflated into
  one ("you need n8n for this to be automatic"), both false for opposite reasons. Delivered: a
  scheduler + its store (§16.0 — the one genuine gap), `run_solo.py`, API attachments/webhooks/
  `/health`/idempotence/async (§16.1), `graph_topology.py` (§16.1.6), Docker + compose profiles +
  n8n workflow + committed `openapi.json` (§16.1.5), README/`.env.example`/`.gitignore` (§16.2),
  `ACA_DEMO_MODE` (§16.3), CI (§16.4, closing §15.4.7), and the one-pager + these docs (§16.5).
  Suite: 261 → **352 tests**. Six things the pass *found* rather than merely built: (1) **nothing
  scheduled `relance.py`/`retention.py`** — both documented "schedule this periodically", no
  scheduling mechanism anywhere, on a machine without `cron`, so the GDPR purge in practice never
  ran; (2) **the graph diagram shown in the UI was wrong** — `ui.py`'s hand-copied edge list was
  missing `supervisor → routing`, the drift §12bis had predicted, invisible because nothing compared
  the two lists; (3) **multimodal analysis was unreachable from the API** (`attachments_raw` wired to
  `[]`) — the project's first innovation pillar, invisible from its own HTTP interface; (4) the
  webhook payload **lagged the REST snapshot by one `reasoning_log` line**, since
  `notification_node` emitted before LangGraph merged the entry the node was about to return —
  fixed at source, not in the test; (5) **`analysis.clarification` was declared, documented in
  `n8n/README.md`, and never emitted** — the only branch where the graph stops without signalling
  anything, so an `?mode=async` caller stayed silent forever on an ambiguous email; (6) a
  `.gitignore` trap where the new `.env.*` rule would have excluded the `.env.example` written the
  same day. Still open after this pass, none of it missing code: the n8n workflow was never imported
  (no instance exists), the webhooks never received by a real n8n, the Docker image never built
  (Docker isn't installed here — only `compose config` was validated per profile), CI won't run
  until the first push to a remote, and the one-pager isn't hosted (same reason as TLS). A
  correction to my own plan, recorded for honesty: it claimed `ui.py` had a duplicated `st.title`;
  on inspection those are two mutually exclusive screens (auth gate vs. app), so nothing was touched.
- ✅ **Fixed (2026-07-30)** — §17 "white-label branding, animations, attributable activity journal".
  Started from a three-part request (make Streamlit prettier with lots of animations; make colours
  and the logo **parametrable** so the receiving company can match its own spec; build an audit
  profile for the `operator` role so an admin sees who changed what, from which PC, when). As in
  previous passes, auditing the code first found more than the request implied. Delivered:
  [tamper_chain.py](aca/storage/tamper_chain.py), [activity_log.py](aca/storage/activity_log.py),
  [branding.py](aca/core/branding.py), the admin "Journal d'activité" tab and "Apparence" panel,
  activity logging on 18 UI handlers plus `api._do_validate`/`_do_reject`/`update_settings` (so a
  Slack or n8n validation is no longer invisible to the journal an admin reads in Streamlit), GDPR
  purge wiring, and `docs/AMELIORATIONS_SUGGEREES.md`. Suite: 352 → **451 tests**. Four things the
  pass *found* rather than merely built: (1) **the `operator` role existed since §15.1.6 with none of
  its actions traced** — `audit_log.py` records one event type, so logins, rejections, settings
  changes, knowledge curation and account management left no trace at all, and "what did this person
  do this week" had no answer in the product; (2) **the anti-brute-force lockout blocked silently** —
  `auth_lockout.py` (§14) stopped a bot without recording the attempt, so an attack could never be
  noticed, dated or attributed; a security control that leaves no trace protects the moment and
  teaches nothing; (3) **an audit entry was lost in silence** — the first real end-to-end UI run
  showed zero journal rows because `st.context.ip_address` isn't guaranteed to be a string, the value
  reached SQLite (`Error binding parameter 13`), and `log()`'s deliberate "never raises" contract
  swallowed it: the protection mechanism was precisely what hid the loss, and a security journal you
  believe is complete and isn't is more dangerous than none; (4) `log()` **could** raise in violation
  of that same contract, because `details` serialisation sat outside the `try`. Also caught by tests:
  `merge_config_toml` wasn't idempotent (re-applying left an empty `[theme]` section) — and the
  first version of that test was itself wrong, counting a `[theme]` string that also appears in the
  generated header comment, so comparing the whole file is the correct check. Verified live: full
  suite offline; headless `AppTest` render of the whole app (no exception, 5 tabs, 11 colour pickers,
  branding panel and integrity check present); an end-to-end analysis then rejection through the UI,
  both appearing in the journal with workstation/server/outcome and an intact hash chain; and a
  client palette (violet/coral, Poppins, 0px radius, compact, animations off) applied from
  `config_store` and re-read in the rendered HTML — all tokens present, `@keyframes` absent,
  `prefers-reduced-motion` still emitted. Still open, none of it missing code: the journal has never
  run on a real multi-workstation deployment (none exists — the observed IP is the loopback), nothing
  alerts in real time on a logged incident (recommended as the top item of
  `docs/AMELIORATIONS_SUGGEREES.md`), and SSO/SCIM plus a custom domain remain documented
  recommendations rather than built features.
- ✅ **Fixed (2026-07-30, same day)** — §18 "everything in `docs/AMELIORATIONS_SUGGEREES.md` except
  hosting and Slack incident alerts", plus a deliberate frontend-design pass
  (`/example-skills:frontend-design`) requested alongside it. The recap table's 8 buildable items
  (viewer role and favicon were already done — see the §17 entry above) are now all delivered:
  machine-action logging (`poller.py`/`relance.py`/`retention.py`/`scheduler.py` now write to
  `activity_log` with `SOURCE_POLLER`/`SOURCE_CLI`, closing the exact gap §17's own audit had flagged
  — constants declared, used by nobody), a monthly signed archive export (`scheduler.py`'s new
  `archive` job → `activity_log.archive_period()`), a per-lead chronological timeline + readable diff
  (`ui_kit.timeline()`/`diff()`, `activity_log.lead_timeline()`, the new
  `analytics_store.get_draft_edit()`), a session-expiry warning toast, a branded PDF export of the
  proposal (`pdf_export.py`, no new dependency), and TOTP on `admin` accounts (`totp.py`,
  stdlib-only, plus the enrollment/verification UI in `aca/ui/shared.py`) — the security audit's own
  "last weak link" finally closed. Also delivered from §5 of the doc: an in-app "N new since your
  login" indicator and demo-mode empty-state links. **The single largest piece of the pass, not
  separately numbered in the doc**: `ui.py` (previously a single ~1700-line file) split into a thin
  `st.navigation` router (`ui.py`) + shared session/auth/graph helpers (`aca/ui/shared.py`) + five
  page scripts (`app_pages/1_inbox.py` … `5_settings.py`) — undertaken because every new §18 surface
  (the lead timeline, the PDF button, the TOTP UI) needed *somewhere sane to live*, and a single
  monolith was already the file this session's own plan flagged as needing the most care not to
  regress. Two things the split's own verification found and fixed before they became live bugs: (1)
  `st.navigation` executes only the **selected** page per rerun, unlike the old `st.tabs()` where
  *every* tab's body ran on *every* rerun — the sidebar's "Ouvrir"/"Charger cet e-mail" buttons had to
  gain an explicit `st.switch_page("app_pages/1_inbox.py")`, or a result loaded from the sidebar would
  stay invisible until the person clicked the inbox tab themselves; (2) the session-expiry toast's
  first draft checked remaining time **after** `session.touch()`, which resets the idle clock to full
  on every call — the idle-based warning would have been permanently unreachable in practice, caught
  by writing the toast test before assuming the ordering was fine. A third, unplanned fix while
  finishing the pass: `activity_log.py`'s two-speed retention (`purge_older_than(sensitive_days=…)`)
  already existed from §17 but was never actually invoked by `retention.py`'s real periodic job —
  built, tested, never wired, the identical shape of gap §16.0 found for scheduling itself; now closed
  via `ACTIVITY_SENSITIVE_RETENTION_DAYS`. Suite: 451 → **550 tests** (`test_ui_kit.py`,
  `test_totp.py` — checked against the official RFC 4226 test vectors, `test_pdf_export.py` — a real
  build→reopen→extract round trip, `test_retention.py`, and extensions to `test_security.py`/
  `test_activity_log.py`/`test_storage.py`). Verified live: the full offline suite; a headless
  `AppTest` sweep across all three roles (`admin` through TOTP enrollment, `operator`, `viewer`) and
  every page each can reach, with zero exceptions; a full demo-mode analysis through
  classification/extraction/agents/draft/validate/reject on the restructured inbox page, byte-for-byte
  behaviourally equivalent to the pre-split flow. A discovery specific to this session's tooling, kept
  here since it will recur: Streamlit 1.59's `AppTest` classifies **any** `st.expander(..., icon=...)`
  as its internal `Status` wrapper rather than `Expander` (the dispatch keys off `proto.icon` being
  non-empty), which affects every icon-bearing expander already in this codebase, not just the new
  ones — `at.expander` silently returns none of them; `at.status` is the correct accessor. Not done,
  by explicit exclusion: hosting/TLS/custom domain and real-time Slack incident alerting (both need
  real infrastructure this project doesn't have, same reasoning as everything else marked
  "not live-verified"). Deliberately deferred from §5 of the doc as higher-effort/higher-risk relative
  to the rest of the pass, and explicitly flagged as such rather than silently dropped: keyboard
  shortcuts (needs `st.components.v2`), batch validation (the doc itself cautions it needs care not to
  dilute the human-validation guarantee), a real mobile-device pass (nothing to test against here),
  and a global cross-page search.
- ✅ **Fixed (2026-07-31, same day)** — a §18 tangent, three follow-up requests on the just-finished
  restructure rather than a new roadmap item: (1) "how is this used exactly" → explained the TOTP
  enrollment screen; (2) "make it a QR code" → the enrollment secret, previously shown as raw
  `otpauth://` text, now renders as an actual scannable image (`_totp_qr_png()`, see
  `aca/ui/shared.py` above); (3) "fix this bar, make it more visible and animated" +
  "center the tabs and add a French/English language switcher" → the top-nav/security-banner CSS
  polish and [i18n.py](aca/core/i18n.py) described above. Asked to scope the translation effort
  (hundreds of strings across ~15 files vs. primary chrome only), the user chose **primary chrome
  only** — the smaller, explicitly-scoped option — which is why admin-only screens and the activity
  journal's detail columns stay French-only by design. One real bug the pass's own verification
  caught before it shipped: the language switcher was first placed in `ui.py`'s sidebar *after* the
  `check_auth()`/`st.stop()` gate, so it silently never rendered on the login screen — moved to
  before `prod_check.enforce()` (see `ui.py` above). Verified live: the full offline suite (550 →
  **561 tests**, `test_i18n.py`); a headless `AppTest` sweep logging in as a fresh TOTP-enrolling
  admin, switching to English mid-session, and confirming each of `2_dashboard.py`/`3_history.py`/
  `4_activity.py`'s own translated caption text (not just the absence of an exception) actually
  appears in the rendered page — the check that would have caught a switcher that flips the
  selector without the page underneath actually changing language.
- ✅ **Fixed (2026-08-03)** — §19 "header overlap, distinctive design, scheduled sends, reminders,
  parametrable intake". Four requests in one pass. **(1) The overlap was real and measurable**:
  Streamlit's header is `position: absolute`, `z-index: 999990`, transparent, **52.5px** tall, and
  the branding CSS had replaced `.block-container`'s top padding with a flat `var(--aca-top)` =
  **30.8px** — so the first ~22px of *every* page sat underneath it. Found by measuring the live
  DOM with Playwright rather than eyeballing; fixed with `padding-top: max(var(--aca-top),
  calc(var(--aca-header-h) + 1rem))` (a floor, so the "aérée" density can still add air but never
  drop below the clearance) plus a real opaque background on the header, which also stops it
  reading as a pill floating over nothing. **(2) Design.** The diagnosis wasn't the hue, it was
  that *gradient was applied to everything* — hero, primary buttons, metric cards, nav — and an
  effect applied everywhere ranks nothing; that, plus one uniform radius and a single type role, is
  what reads as templated. Now: gradient survives in exactly **one** place in the whole stylesheet,
  the decision block. Default palette moved off Fluent blue + violet (`#0078D4`/`#8764B8`) to deep
  petrol + burnt amber on cool paper, encoding a thesis that is actually true of the product —
  **the machine's work is cool, the human decision is warm** — with amber reserved solely for "this
  awaits a person". Three earned type roles: a display serif for the document's voice
  (`BRAND_FONT_DISPLAY`, Fraunces default — a token, so a client can still override), the client's
  sans for the tool's voice, and IBM Plex Mono, **not** parametrable, for machine values, because
  tabular figures in a queue need to align. Signature element: **`ui_kit.signoff()`**, a "Bon pour
  accord" cartouche naming who is responsible, when, and — before the gesture — exactly what
  validating will write. **(3) Scheduled sends + reminders** ([task_store.py](aca/storage/task_store.py),
  `gmail_reader.send_draft`, a new `tasks` scheduler job): the honest reading is that scheduling is
  not autonomous action — the human read and approved the draft, they merely chose a later moment,
  and what goes out is the Gmail draft itself, so editing or deleting it in Gmail still wins.
  Reminders are deliberately independent of validation ("I'll deal with it Tuesday" is an intention
  that exists whether you validate, reject, or defer). **(4) Intake**
  ([intake_window.py](aca/core/intake_window.py)): the sidebar had explained the *mechanism*
  ("traités par le poller en arrière-plan (`poller.py`)" — a filename nobody in a sales team can
  place) and nothing was adjustable; now it says what it does in one sentence, shows a live
  readout, and a settings panel controls on/off, days, hours and frequency, re-read every cycle
  with no restart. Suite: 561 → **606 tests**. Two things verification caught rather than review:
  the reminder's three fields each triggered a rerun, so pressing Enter in the note collapsed the
  expander before the button could be clicked (now an `st.form`, which is what those three fields
  always were — one intention); and an existing test's promise that `BRAND_FONT="Système"` emits
  **no** CDN call, which the new display/mono `@import`s had quietly broken — the fix suppresses
  all three imports, since that setting is a promise about the network, not about a font. Not
  verified live, and stated plainly: the scheduled send has never fired against a real Gmail
  account (the branch is exercised through `AppTest` with a seeded `gmail_message_id`, and
  `send_draft` is covered only by its graceful-degradation contract), because demo mode produces
  manual entries with no Gmail thread — the same limit as every other Gmail-dependent path here.
- ✅ **Fixed (2026-08-04)** — §20 "handing a lead to a colleague, and telling the month's story".
  Three requests in one pass: (1) let an **operator hand the admin specific emails** to re-read,
  several in one gesture, visible to the admin on login; (2) a **monthly PDF** of what happened,
  from emails to statistics, with graphs, **comparing against the previous month**; (3) that PDF
  made **as parametrable as possible** ("classification and email names only", a chosen date range,
  anything), always with context and always themed. Delivered:
  [review_store.py](aca/storage/review_store.py) + [app_pages/6_reviews.py](app_pages/6_reviews.py),
  [reporting.py](aca/core/reporting.py) + [report_pdf.py](aca/integrations/report_pdf.py) +
  [app_pages/7_reports.py](app_pages/7_reports.py), a `report` scheduler job, period-windowed reads
  and a row-level `list_events()` in `analytics_store`, `action_counts`/`actors_between` in
  `activity_log`, GDPR purge wiring, and ~100 i18n keys. Suite: 620 → **697 tests**. **The pass's
  own finding, stated as such**: request (1) was not a missing *screen* but a missing **gesture** —
  an operator facing a lead beyond their authority could validate (CRM write), reject (removes it
  from everyone's queue), or go tell someone outside the product, where the information stops being
  traceable; "I'm not deciding this" had no representation anywhere in the data model. Four defects
  found by verification rather than review: (a) **every French paragraph overran the right margin**
  in the PDF — `fitz.get_text_length()` under-measures accented characters so badly that
  "ééééééééée" measures 22.8pt against 45.6pt for "eeeeeeeeee", and the wrap calculation believed it
  was inside the margin; caught by rendering the pages to images and looking at them, invisible in
  the code; (b) **`…` and `—` rendered as stray dots** in every truncated cell and empty value,
  because a PDF's Base-14 fonts are addressed through Latin-1 — while accented letters *are* in
  Latin-1 and rendered correctly, which is exactly what made it easy to miss; (c) a typo in a
  `BRAND_*` hex made `_Palette` raise, so `build_report_pdf` returned `None` per its own
  never-raises contract and **the nightly report silently stopped being produced** — the protective
  contract was again what hid the loss, the same shape as §17's swallowed audit line; (d) in the
  end-to-end harness itself, a fake session dated 1970 made the app show the login screen — a
  session TTL that *works* passing for a missing feature (fixed in the harness, not the product).
  Verified live: the full offline suite; both new pages rendered headless for all three roles; a
  real operator→admin round trip (a batch of 2 handed over, visible to the admin with its note and
  subjects, absent from the sender's own inbox but present in their sent list); the admin-only gate
  on archived monthly reports; and `ui.py`'s hero pill + sidebar panel + toast, with **one** toast
  for a two-email batch. Still open, and stated plainly: the monthly report has **never run over
  twelve months of real data** (verified on synthetic data and through the scheduled job in tests);
  its "your report is ready" notification reaches nobody when neither Slack nor `NOTIFY_EMAIL` is
  configured (the file is still there, but nothing goes to fetch it); and the PDF is **not
  attached** to that notification, since `notify.py` carries text only and adding attachment support
  exceeded the request.
- ✅ **Fixed (2026-08-05)** — §21 "design pass". Request: improve the Streamlit design "to the top",
  each element with a purpose, "neat and fluid", using the animation/design skills. The pass's own
  finding is that it was mostly **not** a decoration job: the rule adopted at the outset — *judge
  nothing from the source, measure everything on the rendered page* (app run in a sandbox with
  copied DBs, demo mode, no API key, driven through a real browser) — turned up **five rules that
  were written and never rendered**, each invisible to code review because dead CSS raises no
  exception and breaks no test: (1) no page heading was ever in the display serif (specificity lost
  to Streamlit's own emotion class — so a third of §19's typographic thesis had never been seen by
  anyone); (2) §19's opaque header had been silently reverted by a stale `background: transparent`
  130 lines lower (measured `rgba(0,0,0,0)`: content scrolled under a hairline with nothing behind
  it); (3) `--aca-header-h` claimed to be the header height and was 49px against a real 52.5px,
  because `3.5rem` assumed a 16px root while `config.toml` sets `baseFontSize = 14`; (4) the active
  nav item was unbranded grey while hover carried the brand colour, lift and shadow — the hierarchy
  inverted on a seven-entry bar; (5) two dashboard chart categories drew in the identical colour,
  because the categorical palette concatenated six semantic tokens that are legitimately allowed to
  be equal (`BRAND_INFO == BRAND_PRIMARY`, `BRAND_WARNING == BRAND_ACCENT` in the defaults). Beyond
  rendering, one genuine **design** defect: `BRAND_ACCENT` was given a single exclusive meaning at
  §19 ("a human must decide here") but none of the 18 shipped presets were revisited, so 4 gave it
  the primary's own hue family — the test instance ran an all-blue palette with not one pixel of the
  reserved colour. Fixed via a new `signal_separation()` (CIELAB a\*b\* distance, L\* ignored —
  WCAG contrast ranks these backwards), 5 corrected preset accents, and a warning in
  `accessibility_report()`. Also: muted text raised to clear AA, permanent low card shadow, easing
  tokens, all UI motion under 300 ms, press feedback, pointer-gated hover, `:focus-visible` rings,
  one perpetual decorative animation removed, and `.streamlit/config.toml` regenerated from the
  token defaults (it was still the pre-§19 Fluent blue, so a fresh clone rendered half in the
  intended palette and half in an abandoned one). A follow-up request added an **ambient background
  wash** — accepted only under the three constraints described in the `branding.py` entry above,
  since it arrived immediately after *removing* a perpetual decorative animation for competing with
  the loops that carry meaning. Suite: 697 → **767 tests**. A methodological note
  worth keeping, since it cuts the other way: the pass *suspected* entrance animations replayed on
  every Streamlit rerun, which would have justified deleting them; measuring `getAnimations()`
  before and after a widget-triggered rerun showed them unchanged at `finished` — React reconciles
  the nodes — so **the hypothesis was wrong and the animations were kept**, merely shortened. Still
  open, stated plainly: nothing was checked on a real phone or tablet (only a resized desktop
  browser); dark mode and the 18 presets are verified by computation only, none opened by eye; the
  `data-testid`/emotion selectors remain Streamlit 1.59 implementation details, so a version bump
  can make the page **less pretty, never broken**; and this deployment's own colours are stored as
  *explicit* overrides, which by the project's long-standing rule are never overridden by a preset
  — so the corrected palettes do **not** change this instance, though the Apparence panel now warns
  that its accent scores 0.08.
- ✅ **Fixed (2026-08-06)** — §22 "dashboard: prettier, more interactive, more statistics". The
  pass's finding is that the page showed a **state** and never a **change**, and that every datum
  needed to fix that was already being recorded — the fourth instance of this project's recurring
  built-but-never-read gap (`get_draft_edit` §18, `list_events` §20, and the response-time
  distribution whose own comment described the intended "< 1h vs > 24h" reading while only a raw
  minutes table was ever rendered). Delivered: four read-side aggregations in `analytics_store`
  (`bucket_response_times`/`by_source`/`hourly_volume`/`top_senders`), period-over-period deltas on
  every KPI reusing `reporting.previous_period()` and its `better` direction metadata, an
  `st.fragment` scoping period changes to the dashboard, horizontal bars where labels are long,
  humanised source labels (`gmail_import` → "Import Gmail (à la demande)"), and ~20 i18n keys.
  Suite: 767 → **773 tests**. Three things found rather than built: (1) the KPI row silently went
  **ragged on some data** — Streamlit sets `align-items: start`, so a metric with no delta to show
  is 22px shorter than its neighbours, and the fix needed two attempts because a flex item with a
  *definite* height ignores `stretch` (measured: the row did switch to `stretch` while the cards
  stayed 144/144/122/144); (2) §21's press-feedback and focus rules covered only
  `.stButton`/`.stFormSubmitButton`/`.stDownloadButton`, so `st.segmented_control`/`st.pills`
  (`stButtonGroup`) — i.e. the dashboard's two main controls — were the app's only clickable
  elements with no press signal, and Streamlit animates `all` on them; (3) the `source` column had
  been written on every analysis since the beginning and displayed nowhere, leaving the adoption
  question (automation vs. retyping by hand) unanswerable inside the product. **The font was
  offered and deliberately not changed**: the three-role system was decided at §19 and *repaired*
  at §21, where the display serif turned out never to have reached a single page heading — churning
  it now would undo the work that made it visible. Still open: the new distributions have never run
  at volume (the demo DB holds ~20 analyses over 11 days — enough to validate chart shape, not
  legibility with hundreds of correspondents), and nothing was checked on a phone.
- ✅ **Fixed (2026-08-08)** — §28 "acami: the agency brand, its legal pages and its marketing
  surface". The pass began from a classification question — *is this a SaaS or an AI agency?* — and
  the answer is the finding: **agency** (zero licence fee, deployment on client infrastructure,
  client-owned keys, price quoted after a call). That exposed the real defect: **the marketing site
  branded itself as the product, not the business.** `<title>` was "ACA — Agentic Commercial
  Assistant" and the Organization JSON-LD named ACA, so the page argued "buy this software" while
  the copy beneath it argued "hire us to install it". Settled with the user as a three-layer
  architecture: **acami** the commercial entity (always lowercase — that is what separates the
  ownable, trademarkable name from the two acronyms), **ACAM** the multi-agent engine, **ACA** the
  installed framework. Delivered: a generated logo system
  ([scripts/build_brand_assets.py](scripts/build_brand_assets.py) → 4 SVG + 3 PNG, byte-identical
  across runs so CI can detect drift exactly as it does for `export_graph.py`);
  [docs/BRAND.md](docs/BRAND.md), [docs/BRAND_GUIDELINES.md](docs/BRAND_GUIDELINES.md),
  [docs/MARKETING.md](docs/MARKETING.md), [docs/AGENCY_VS_SAAS.md](docs/AGENCY_VS_SAAS.md); an
  `AGENCY_*` token layer in [branding.py](aca/core/branding.py) (`resolve_agency`,
  `agency_mark_html`, `star_path`) wired into `ui.py`'s footer, `aca/ui/shared.py`'s login screen
  and a new `5_settings.py` panel — kept in a table **separate** from `BRAND_*` precisely so a
  client re-theming the app cannot erase the maker's mark by accident; and a substantially completed
  [legal.html](static/legal.html). Suite 805 → **829 passing** (+19 §28 tests, +5 parametrized),
  zero new failures.
  **Six things the pass found rather than built**, five of them this project's recurring shape —
  silent failures no amount of re-reading would catch: (1) **`acami-mark.svg` was not well-formed
  XML** — two consecutive hyphens inside an XML comment (`--ink-accent`) make the file invalid; a
  browser renders it anyway, which is exactly what lets the fault survive, and the first attempt at
  the fix *reintroduced it* by quoting the character pair; (2) **a hand-written Bézier control point
  was wrong** (84.32 for 86.32) — a wrong curve raises nothing, breaks no test and is barely
  visible, so the geometry now computes all eight control points from three numbers and the SVGs
  became generated artifacts; (3) **the landing page contradicted itself about its own prices** —
  two comments claimed the figures "have been removed rather than guessed at" and "are still
  placeholders" while the cards rendered 0 / 1 490 / 8 900 / 290 EUR; (4) **switching the legal page
  to French deleted every `[TO COMPLETE]` marker** — they sat inside elements whose `data-fr` string
  did not contain them, so French would have dropped the real company name once filled in, leaving a
  privacy policy naming no controller at all (now one `ENTITY` seam, both languages carrying the
  span, verified 9-of-9 paired); (5) **the privacy policy confused controller roles** — written as
  though the deploying client were the controller, on a page served by acami, while claiming "no
  tracking cookies… solely from the email you send us" two paragraphs below a table listing the
  Cal.com iframe and Web3Forms; (6) **international transfers were absent entirely** —
  Groq/HubSpot/Web3Forms are US and acami operates from Tunisia, a non-adequacy country, which made
  this the policy's most serious legal gap. Also added to the legal pages: cookie/iframe honesty,
  breach notification, children's data, a DPA offer, AI-output ownership (the client's), EU AI Act
  positioning (limited-risk under art. 50, explicitly not Annex III), deposit and refund terms,
  end-of-engagement data return, a monthly-care SLA table, and AUP enforcement steps.
  **A separate finding, not this pass's work**: `tests/test_branding.py` carries **117 uncommitted
  lines** from an earlier, unfinished **§27 "readable borders"** pass — tests for
  `readable_border_on()`, `BORDER_MIN_CONTRAST`, an `--aca-border-soft`/`--aca-border` split, a
  Streamlit 1.59 card-selector fix and a DOMPurify guard, **none of which is implemented**. Those 29
  failures pre-date this session (confirmed against HEAD) and this pass deliberately did not absorb
  them: finishing it means changing `BRAND_BORDER` on all 19 shipped palettes, a design decision of
  its own. §28 was renumbered from §27 to avoid colliding with it.
  **The pSEO layer was also delivered**: [scripts/build_seo_pages.py](scripts/build_seo_pages.py)
  generates `static/fr/index.html` (a genuinely French-rendered landing page — see below), 10
  pSEO pages (4 integrations, 3 comparisons, 3 glossary entries, each in both languages = 20 files)
  from [static/seo/pages.json](static/seo/pages.json), plus `robots.txt`/`sitemap.xml`. The French
  page is the one place this pass touched the flagship `landing.html`'s *rendering*, so it was
  built deliberately conservatively: a stdlib-only `html.parser.HTMLParser` locates the exact byte
  offsets of every top-level `[data-fr]` element (mirroring the page's own toggle script, including
  its rule that a `[data-fr]` element nested inside another one is inert) and splices in the French
  text — never reconstructing the document from a parsed tree. `lxml` was available and deliberately
  **not used**: it is present only as a transitive dependency of `python-docx`, and importing it
  directly would replay the exact trap `totp.py` documents for `cryptography`. Verified rather than
  assumed: the `<style>` and `<script>` blocks are asserted byte-identical between the English and
  French files, the 235-entry diff touches only text content plus `lang`/`title`/meta/`aria-pressed`,
  and the generator is idempotent (24 output files, byte-identical across two runs). **Still open,
  stated plainly**: none of this can rank without a domain, and none exists — canonical URLs,
  `sitemap.xml` and `robots.txt` all point at the reserved placeholder `acami.example`; the
  wordmark's original typeface remains unknown; no trademark search (INPI / INNORPI) has been run;
  the pricing-card reorder for anchoring was identified and deliberately left alone; and no browser
  was available this session, so every page was verified by parsing (XML/JS/JSON-LD/HTML balance)
  and PyMuPDF rasterisation rather than against a live DOM.


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
(item 6, live-verified), and routing `SUPPORT`/`AUTRE` via `routing_node` (item 5, **live-verified
2026-07-21** — `SUPPORT` now bypasses Stratège/CRM like SPAM/AUTRE; both the Slack alert and the
Gmail forward-draft branches confirmed against real `SUPPORT_EMAIL`/`HR_EMAIL` destinations and a
real Gmail message — see `docs/PROJECT_JOURNAL.md`).
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
**96% classifier accuracy** (48/50) initially, with the 2 errors both on deliberately ambiguous
cases — remeasured at **100%** (50/50) after the classifier moved to structured output (§10/§11.6
item 4), which also fixed both ambiguous cases), and a
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
verified done/partial/todo status — to be started only after §11.6). Also done, from a third
external AI-generated document (two overlapping PDF "Bid Governance" blueprints, audited against
the code and re-scoped away from their single-client framing at the user's request — see
`docs/ACAM_roadmap.md` §13): a deterministic contractual-risk scanner (`risk_scan_node`), an
explicit `knowledge_gap` flag when neither the FAQ nor a web search can answer a question (both
feed `stratege_node`'s prompt and `notification_node`'s alert), an editable draft before validation
with (original, edited) capture as a future few-shot/eval corpus (not fine-tuning), and per-analysis
Groq token-usage logging (`sum_usage()` + `analytics_store.record_tokens()`) — the free first step
of usage tracking already anticipated in §12 item 4. The audit also caught two factual errors in
the source PDFs before they could be copied in: a hallucination-gate threshold (0.85) that would
have blocked most genuinely relevant matches on this project's real embedding-similarity
distribution (0.73–0.80, measured 2026-07-11), and a pgvector schema sized for a paid OpenAI
embedding model instead of the free Gemini one actually in use.

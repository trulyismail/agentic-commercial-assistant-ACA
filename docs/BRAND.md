# acami — Brand strategy

> **What this document is.** The strategy layer: who acami is, who it serves, what it says and how
> it says it. The implementation layer — hexadecimal values, logo geometry, clear space, application
> rules — lives in [BRAND_GUIDELINES.md](BRAND_GUIDELINES.md).
>
> **Status.** Written 2026-08-08 (§28). acami has **no customers yet**. Every claim below is either
> a stated intention or something checkable in this repository. Nothing here invents a track record,
> for the same reason the landing page's "What is verified live — and what is not" section exists.

---

## 1. Brand architecture

Three layers, three jobs. Keeping them separate is what lets the commercial name be ownable while
the technical names stay descriptive.

| Layer | Name | What it is | Where it appears |
|---|---|---|---|
| **Commercial entity** | **acami** | The agency. Who a client hires, signs with, pays, and retains for audits. Owns the domain and the trademark filing. | Site, invoices, contracts, email, footer of every deployment |
| **Engine** | **ACAM** | The proprietary multi-agent framework: supervisor, worker agents, self-critique, validation gate. | Technical pages, proposals, the "how it works" section |
| **Deployed system** | **ACA** | The Agentic Commercial Assistant — what actually gets installed on a client's machines. | Inside the product, its documentation, handover material |

**Written as**: *"acami installs the ACA framework, powered by our ACAM engine."*

This is a **Branded House**. One commercial name, technical names beneath it. It gives acami a term
that can be owned in a trademark register and ranked for on Google — which `ACA` and `ACAM`, being
generic-looking acronyms, never could — while keeping the acronyms doing the job they are good at:
telling an engineer exactly what is being installed.

### Casing: always lowercase

**acami**, never *Acami* or *ACAMI*. Lowercase at the start of a sentence too. This is not
decoration: it is the one typographic signal separating the commercial name from the two engineering
acronyms sitting next to it. `ACAM` shouts; `acami` does not; and a reader scanning a page can tell
which layer they are in without being told.

### The name, read honestly

**acami is ACAM plus one letter.** That is a fact about the string, not a story invented afterwards.

The reading we choose to give it: the extra letter is **i**, for the individual — the person the
engine stops for. It is worth choosing because the logo already says it. In the wordmark, the `i` is
the only letter whose mark is **detached** from its stem; every other letter is one continuous
stroke. The single separated element in the whole logotype is the one that stands for the human.

State it as a chosen reading, never as an etymology. It is a good story because it is consistent
with the product, not because it is where the name came from.

---

## 2. Purpose

> **acami exists so that a small team can work at the speed of a machine without surrendering the
> judgement that makes them worth hiring.**

Not "to automate sales". The automation is the easy half and everyone sells it. The hard half — the
half that decides whether an AI tool survives contact with a real inbox — is the moment a person
still has to look at the thing before it goes out, and whether the product makes that moment fast,
informed and traceable, or treats it as friction to be removed.

## 3. Values

Not aspirations. Each is enforced somewhere in the code, and the enforcement is named so the value
can be checked rather than believed.

| Value | What it means | Where it is enforced |
|---|---|---|
| **The human keeps the last word** | Nothing reaches a CRM or a customer without a person approving it. Not a setting — the architecture. | `interrupt_before=["action"]` in [app.py](../aca/core/app.py); `guard_write()` raises rather than degrades in [demo.py](../aca/core/demo.py) |
| **Say what is not true yet** | Unverified is stated as unverified, in public, on the sales page. | The "What is verified live — and what is not" section of [landing.html](../static/landing.html); the Known gaps section of `CLAUDE.md` |
| **Degrade, never break** | A missing key disables a feature; it never takes the product down. | `notify.py`, `hubspot.py`, `webhook.py`, `pdf_export.py` each document a never-raises contract |
| **Measure, do not assume** | Design and behaviour are judged from the rendered result, not the source. | §21 found five CSS rules written and never rendered; §26 found a stray comment delimiter that silently deleted an animation |
| **The client owns everything** | Their infrastructure, their API keys, their data, their configuration at handover. | Zero licence fee; deployment on client infrastructure (pricing section of [landing.html](../static/landing.html)) |

**The value deliberately absent: "we are the fastest".** Speed is what every competitor claims, and
it is the one thing a validation gate trades away on purpose.

## 4. Who this is for

**Primary ICP.** A French or Tunisian SMB, 5–50 people, selling business-to-business, receiving 10 to
100 inbound commercial emails a week. Somebody — often a founder, often the person who closes the
deals — reads and answers them personally. They lose deals to slow replies, not to bad ones.

**The buyer and the user are usually the same person**, which changes how this is sold: no
procurement committee to impress with a feature matrix, no IT department to reassure with a SOC 2
report. One busy person deciding whether to trust a stranger with their inbox.

**Jobs to be done**, in their words rather than ours:

1. *"Stop the good leads getting buried under the junk."*
2. *"Give me a reply I can send in one minute instead of writing from scratch in fifteen."*
3. *"Do not let a machine email my customers something stupid in my name."*

Job 3 is the one every competitor treats as a caveat and acami treats as the product.

**Explicitly not for**: teams wanting fully autonomous outbound; anyone sending cold email at volume
(forbidden by the [Acceptable Use policy](../static/legal.html), not merely discouraged); enterprises
needing SSO, SCIM and a signed DPA from a company with a compliance department.

## 5. Positioning

> For a **small B2B team drowning in inbound email**, who need replies out fast **without letting an
> AI speak for them**, **acami** is an **AI agency** that installs a working agent system **on their
> own infrastructure, with a human approval gate built into the architecture**. Unlike an **AI SaaS
> subscription**, acami charges **no licence fee and holds none of their data** — because the stack
> runs on free tiers and the API keys stay the client's, so what is paid for is the installation,
> not the permission to use it.

### Differentiation, concretely

| Alternative | What it does | What acami does differently |
|---|---|---|
| **AI SaaS subscription** | You log in, they host, you pay monthly forever | Runs on the client's machine; zero licence fee; the client's keys, the client's data |
| **A generic RAG chatbot** | Answers questions about documents | Runs the whole workflow: classify, enrich, retrieve, draft, self-critique, route, log, follow up |
| **Zapier / Make / n8n alone** | Moves data between apps | The orchestrator does not supply the judgement; the ACAM engine is the reasoning that sits inside it. acami will wire it into n8n if the client already lives there |
| **Hiring an SDR** | A person, fully capable | A one-off installation fee against a salary — and ACA never decides, it prepares; the person decides |
| **A freelance prompt engineer** | Builds a prompt, hands over a chat window | An installed system with an audit trail, a GDPR purge, follow-up scheduling and a test suite |

## 6. Archetype: the Sage, with a Creator's hands

**Sage** — authority comes from *knowing and disclosing*, not from promising. The brand's most
persuasive asset is a section admitting what has not been tested. The voice explains mechanisms
rather than benefits.

**Creator**, secondary — it builds things, and the building is visible: the roadmap, the journal, the
test count, the shipped passes.

**Never the Magician**, the default archetype of AI marketing ("transform your business overnight").
That archetype is unavailable by construction: a product whose selling point is that it *stops and
waits for you* cannot credibly promise magic.

## 7. Origin story

The honest one, which is also the more persuasive one:

> ACA began as an eight-week internship prototype: read incoming sales email, pull out the lead,
> write it to a spreadsheet. The first working version could do the whole loop by itself — and that
> is exactly where it stopped being a good idea. A model that classifies correctly 96 times out of
> 100 is a model that emails a customer something wrong four times out of 100, with your name on it.
>
> So the pause was built in, and then everything else was built around the pause: the audit trail
> recording who approved what, the self-critique pass that re-reads the draft before a person ever
> sees it, the scanner that flags a contractual clause, the flag that says *nobody knows the answer
> to this question* instead of inventing one.
>
> Several development passes later the system had 773 tests and a habit its author had not expected:
> it kept finding, in its own code, features that had been built and never wired up — a purge nobody
> scheduled, a diagram that had silently gone wrong, a monthly report that stopped being produced
> and told no one. Every one was found by running the thing and looking, never by re-reading the
> code.
>
> That habit is the service. acami installs AI systems for people who have to live with the output.

**Rules for retelling it**: never round 773 up; never call the internship "research"; never imply a
customer exists. The story's whole force is that it is checkable.

## 8. The customer is the hero

| Element | Content |
|---|---|
| **Hero** | The person who reads the inbox. Usually the founder. Competent, trusted by their customers, and the bottleneck. |
| **Problem** | External: replies go out late and deals go cold. Internal: they cannot delegate it, because a bad reply in their name costs more than a slow one. |
| **Inciting insight** | The choice was never "automate or don't". It was "who reads it first". A machine can do the reading, the drafting and the filing; the *deciding* was never the slow part. |
| **Guide** | acami — not the hero. The hero still signs. acami's authority is that it already made the mistake: it built the autonomous version first, and took it out. |
| **The plan** | 1. Try the demo — no account, no card. 2. A scoping call. 3. Installed on your machines, on your data, in your colours. 4. Your team trained; the system is yours. |
| **Transformation** | Every inbound email arrives pre-read, qualified and drafted. The founder's morning becomes thirty minutes of decisions instead of three hours of typing. Nothing goes out that they did not approve. |
| **Stakes if nothing changes** | The tool that eventually gets adopted will be one that does *not* pause — and it will send something wrong, in their name, to their best prospect. |

## 9. Voice and tone

**Voice** — a senior engineer explaining their own work to a smart non-specialist. Precise, plain,
unhurried. Willing to say "this has not been tested". Never salesy, never chummy, never mystical.

**Tone by surface**:

| Surface | Tone |
|---|---|
| Landing page | Confident and declarative. Short sentences. Every claim immediately followed by its evidence. |
| Product UI | Quiet and instrumental. The interface's job is to get out of the way of a decision. |
| Legal pages | Flat and complete. No reassurance that is not also a commitment. |
| Errors and refusals | Say what happened, say what to do, do not apologise twice. |
| Proposals to a prospect | Warm, specific, no jargon. The client's voice, not acami's. |

### Never use

| Avoid | Because |
|---|---|
| *revolutionise, game-changing, cutting-edge, unlock, supercharge, seamless, effortless* | Category filler. Every competitor says them, so they carry no information. |
| *fully autonomous, hands-free, set and forget* | The opposite of the product. Using them would sell something acami refuses to build. |
| *AI-powered* as a standalone claim | Says nothing. Name the model's actual job instead. |
| *enterprise-grade, military-grade, bank-level security* | Unverifiable puffery, on a product whose own documentation lists what is untested. |
| *just, simply, easy* before an instruction | Whoever it is not easy for now feels stupid. |
| *users* for the buying customer | They are a **client** (the company) or a **person** (the human at the screen). |
| *Acami*, *ACAMI* | Wrong casing. See §1. |

### Prefer

| Prefer | Over |
|---|---|
| *sign-off*, *approval*, *the human decides* | *review*, *oversight* |
| *drafts and waits* | *automates* |
| *installed on your infrastructure* | *deployed*, *onboarded* |
| *what is verified live* | *proven*, *battle-tested* |
| *quoted after a call* | *contact us for pricing* |
| *lead*, *inbound email* | *ticket*, *conversation* |

**One rule above the others: every number must be traceable.** 773 tests, 74 FAQ rows, 365-day
retention, 96 % then 100 % classifier accuracy — each is measurable in this repository. Do not write
a number that is not.

## 10. Ready-to-use descriptions

Copy these rather than improvising. Consistency across a directory listing, a proposal and the site's
`<meta>` tag is most of what "professional" means at this size.

**Micro (≤ 40 chars) — nav, footer, image alt**
> EN `AI agency` · FR `Agence IA`

**Short (≤ 90 chars) — social bio, directory listing**
> EN: `AI agents that read your inbox, draft the reply, and stop for a human.`
> FR: `Des agents IA qui lisent votre boîte mail, rédigent la réponse, et s'arrêtent là.`

**Meta description (150–160 chars)**
> EN: `acami installs custom AI agents that pre-read your inbound sales email, qualify the lead and draft the reply. Nothing reaches your CRM before a human approves it.`
> FR: `acami installe des agents IA qui pré-lisent vos e-mails commerciaux, qualifient le lead et rédigent la réponse. Rien n'atteint votre CRM sans validation humaine.`

**One paragraph — proposals, an About section, a deck's opening slide**
> acami is an AI agency. We install agent systems on your own infrastructure — your machines, your
> API keys, your data — and charge for the installation, never a licence. The system we install,
> ACA, reads the sales email arriving in your inbox, works out what the sender wants, researches
> them, drafts a reply in your voice, and then stops: nothing is written to your CRM and nothing is
> sent until someone on your team has read it and approved it. That pause is not a limitation we are
> working to remove. It is the reason the system can be trusted with a real inbox.

**Technical one-liner — for an engineer in the room**
> acami deploys the ACA framework on your infrastructure, powered by the ACAM engine: a LangGraph
> supervisor orchestrating classification, enrichment, hybrid RAG, drafting and self-critique,
> behind a hard human approval gate before any CRM write.

**Boilerplate — the end of a press note or a partner page**
> acami designs and installs AI systems for small B2B teams who have to live with the output. Based
> in Tunisia and serving clients in France and Tunisia, it deploys the ACA framework — inbound sales
> email read, researched and drafted automatically, with every decision left to a person. acami
> charges no software licence: deployments run on client infrastructure under client-owned API keys.

## 11. Naming: what was considered, and why acami

Recorded so the decision is not relitigated every six months.

An earlier draft of this pass proposed promoting **ACAM** itself to the commercial name. That was
rejected, correctly, for two reasons worth keeping written down: an acronym is very hard to own in a
trademark register or to rank for on Google, and `ACA` versus `ACAM` — one letter apart — would have
forced every sentence to disambiguate two names doing different jobs.

Candidates considered before **acami** was chosen:

| Candidate | Meaning | Why not |
|---|---|---|
| **Aval** (FR) | *donner son aval* — to give one's sign-off | Strongest meaning of the set, but French-only; an English buyer reads it as a river term |
| **Notch** | the notch that stops a mechanism | Very ownable mark; no kinship at all with the technical names |
| **Assent** | the English word for the thing itself | A funded B2B compliance-software company already owns it well |
| **Acuma** | invented, from *acumen* | Safe, but means nothing until explained |
| **Docket** | the list of matters awaiting decision | Names the screen rather than the promise |
| **Seuil**, **Paraphe**, **Cran** (FR) | threshold / initials on a contract page / notch | Distinctive in France, unpronounceable elsewhere; *Seuil* also collides with a major French publisher |
| **acami** ✅ | ACAM + i | **Chosen.** Ownable and searchable, keeps audible kinship with the engine, short, and lowercase-distinct from the acronyms. Accepted cost: it carries no inherent meaning, so **the tagline must do that work** — which is why no wordmark-only lockup exists |

**Not done, and required before commercial use**: a trademark search at **INPI** (France) and
**INNORPI** (Tunisia), and a domain acquisition. Neither can be performed from this repository, and
neither should be assumed clear.

---

## Related

- [BRAND_GUIDELINES.md](BRAND_GUIDELINES.md) — colours, type, logo rules, applications
- [MARKETING.md](MARKETING.md) — ICP, channels, the selected growth plays
- [AGENCY_VS_SAAS.md](AGENCY_VS_SAAS.md) — why this is an agency, and what would make it a SaaS
- [../static/legal.html](../static/legal.html) — privacy, terms, acceptable use

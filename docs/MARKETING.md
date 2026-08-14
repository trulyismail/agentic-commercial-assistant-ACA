# acami — Marketing plan

> Written 2026-08-08 (§28). Positioning and voice live in [BRAND.md](BRAND.md); why this is an
> agency rather than a SaaS is in [AGENCY_VS_SAAS.md](AGENCY_VS_SAAS.md). This document is about
> **how a stranger comes to hire acami**.
>
> **Honest starting position**: no customers, no domain, no traffic, no case studies, no budget, and
> one person's time. Every play below is chosen for that situation specifically. A plan assuming an
> ad budget or a reference client would be a plan for a different company.

---

## 1. Who to sell to first

From [BRAND.md](BRAND.md) §4, narrowed to the version worth prospecting **this quarter**:

> A French or Tunisian B2B company, 5–50 people, where **one identifiable person** reads the
> `contact@` inbox and also closes the deals. They receive 10–100 commercial emails a week and lose
> deals to slow replies rather than bad ones.

**Three qualifying signals**, all visible from outside without asking anyone:

1. A `contact@` or `commercial@` address published on the site — proof that inbound exists.
2. A quote or demo request form — proof that inbound is *commercially valuable* to them.
3. Fewer than ~50 employees on LinkedIn — above that, someone has already bought a CRM with a
   workflow module, and the sale becomes a displacement, which is far harder.

**Sectors that fit best** (a knowledge base that can actually be ingested, and repetitive
enquiries): industrial suppliers, B2B services, software integrators, equipment rental, technical
training.

**The disqualifier, stated plainly**: anyone who opens with "can it send automatically?". They are
buying something acami refuses to build, and the first invoice will be followed by an argument about
the one feature that will never exist.

## 2. The one message

> **The AI reads the inbox. The human still decides.**

Everything else is evidence for that sentence. Where a channel forces a choice between explaining
the technology and explaining the guarantee, choose the guarantee — the technology is a commodity by
now and the guarantee is not.

## 3. The five plays

Selected from the 139-idea catalogue against the constraints above. Each is here because it works
with **zero budget and no customers**, which eliminates most of the catalogue outright.

---

### Play 1 — The demo *is* the free tool (engineering as marketing, #15)

**Why it fits.** Already built, and the single strongest asset acami owns.
[demo.py](../aca/core/demo.py) runs the entire graph — same agents, same supervisor, same
self-critique, same validation pause — with **no API key at all**, and CRM writes hard-blocked. Most
agencies' "demo" is a recorded video. This one is the actual product.

**Why it converts.** It removes every objection at once: no account, no card, no data, no risk. The
zero-price effect is disproportionate — the gap between free and €1 is psychologically far larger
than the gap between €1 and €50.

**First three steps**
1. Host the demo somewhere public (the one genuine blocker — see §5).
2. Make the landing page's demo tier a real link rather than a jump to the booking section.
3. Instrument nothing beyond a count of demo launches. Do not add analytics to a page whose privacy
   policy says there are none.

**Success looks like**: 1 in 10 demo visitors books a call. **Cost**: hosting only.

---

### Play 2 — Comparison pages (#11)

**Why it fits.** People in this market do not search for "agentic email assistant". They search for
*"Zapier email AI"*, *"alternative to hiring an SDR"*, *"ChatGPT to answer customer emails"*. Those
are the entry points, and each is a page acami can write with more authority than a competitor
because the comparison already exists in the code.

**The four pages, in order of intent**
1. acami vs. Zapier / Make — *the orchestrator does not supply the judgement*
2. acami vs. a generic RAG chatbot — the table already drafted in `ACA_presentation_source.md`
3. acami vs. hiring an SDR — the cost comparison a founder is actually making
4. acami vs. doing nothing — the honest one, and the one that converts the undecided

**The rule that makes them work**: describe the competitor accurately, including where it wins. A
comparison page where the alternative loses on every axis reads as an advertisement and is believed
on nothing.

**Success looks like**: ranking for two long-tail comparison terms within six months.
**Cost**: writing time.

---

### Play 3 — Glossary pages (#9)

**Why it fits.** acami has genuine first-hand expertise on terms currently being defined badly by
everyone: *human-in-the-loop*, *agentic workflow*, *supervisor agent*, *AI validation gate*. Each
page can cite a real implementation rather than restating a definition — the difference between
content and content marketing.

**Why now**: these are also the terms a language model cites when answering "what is a
human-in-the-loop AI system", which is increasingly where a technical buyer starts.

**Success looks like**: being the page a prospect forwards to a colleague. **Cost**: writing time.

---

### Play 4 — Founder-led outreach (#47)

**Why it fits.** With zero traffic, the first three clients will not arrive through a channel. They
will arrive because someone was contacted personally — and this is the only play with a same-week
feedback loop.

**The method, deliberately narrow**: 10 companies a week, chosen against §1's three signals. Send
**a real analysis of their own public enquiry flow**, not a pitch — what a lead arriving on their
contact form triggers today, and what it would trigger afterwards.

**Why this and not cold email at volume**: acami's own
[Acceptable Use policy](../static/legal.html) forbids bulk unsolicited messaging. Selling a product
whose policy bans a practice while using that practice is the fastest possible way to destroy the
trust argument the entire brand rests on.

**Success looks like**: 10 contacts → 2 replies → 1 call. Three calls before changing anything.
**Cost**: roughly four hours a week.

---

### Play 5 — Powered-by marketing (#87) — **already shipped**

**Why it fits.** Every deployment acami installs runs on a screen someone looks at daily. A discreet
*"installed by acami"* in the footer is the cheapest distribution an agency will ever own, and the
only one that compounds with delivery instead of competing with it for time.

**Status**: built in §28. `AGENCY_*` tokens in [branding.py](../aca/core/branding.py) render the
mark in the app footer and on the login screen — the one screen every role sees before anything
else. Deliberately separate from the client's `BRAND_*` white label so re-theming cannot erase it by
accident, and switchable off (`AGENCY_SHOW=non`) for a client whose specification forbids naming a
supplier.

**Success looks like**: one enquiry that begins "I saw this at a supplier of ours".
**Cost**: already paid.

---

## 4. What is deliberately NOT being done

Saying no is most of a plan at this size.

| Not doing | Why |
|---|---|
| Paid ads | No landing-page conversion data. Buying traffic to an unmeasured page is buying a number you cannot read. |
| Cold email at volume | Forbidden by acami's own Acceptable Use policy. See Play 4. |
| Sector landing pages at scale | 18 sector pages were considered and rejected: a brand preset is a palette, not a proof point, and 18 near-identical pages is exactly the thin content that gets penalised. |
| Social media presence | A daily-feeding channel with no compounding value at this stage, competing directly with delivery time. |
| A newsletter | Nothing to send weekly yet, and a lapsed newsletter is worse than none. |
| Case studies | **There are no customers.** Fabricating one would contradict the page that lists what is unverified — the brand's single most persuasive asset. |

## 5. The blocker, named

**No domain exists.** It gates Plays 1, 2 and 3 entirely: nothing can rank, nothing can be linked,
the demo cannot be hosted, and no `og:image`, canonical or sitemap can be activated (all written and
inert — see [landing.html](../static/landing.html)'s head).

Play 4 works today and needs nothing. It is where to start regardless.

## 6. The psychology already working on the page

Recorded so it is *preserved* under future edits rather than rediscovered:

| Principle | Where it is used |
|---|---|
| **Pratfall effect** | "What is verified live — and what is not." Admitting a limitation raises credibility rather than lowering it — and no competitor will copy it. |
| **Zero-price effect** | The free demo tier: no card, no account. |
| **Authority** | Six shipped passes and 773 tests, each checkable. |
| **Paradox of choice** | Three tiers plus one add-on. Not five. |
| **Social proof** | *Deliberately absent* — there are no customers, and the testimonials section says so outright. |

**Still weak, and worth fixing next:**

- **Loss aversion is never used.** The page argues capability and never states the cost of *not*
  acting: a lead answered on Thursday that was worth answering on Monday. Losses weigh roughly twice
  what equivalent gains do, and this is the largest untouched lever on the page.
- **Anchoring is inverted.** Tiers run cheapest-first, so 1 490 € is read before 8 900 € rather than
  after it; presenting the anchor first makes the target read as reasonable. Identified and
  **deliberately not changed** in §28 — reordering pricing cards is a real UX change that deserves
  its own before/after look, not a slip into a branding pass.

---

## Related

- [BRAND.md](BRAND.md) — positioning, voice, ready-to-use descriptions
- [AGENCY_VS_SAAS.md](AGENCY_VS_SAAS.md) — the business model and its ceiling
- [BRAND_GUIDELINES.md](BRAND_GUIDELINES.md) — the visual rules any of this must obey

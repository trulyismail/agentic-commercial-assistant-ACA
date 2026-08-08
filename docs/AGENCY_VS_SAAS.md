# Is acami an AI agency or a SaaS?

> Written 2026-08-08 (§28). This began as a direct question, and the answer turned out to be the
> organising fact of the whole brand: **acami is an AI agency that sells a productised service.**
> Written up here because it is the section a thesis or a business plan needs, and because the
> distinction determines how the site is worded, what the contract says, and where the revenue
> ceiling sits.

---

## 1. The two models

| | **AI agency** (service / labour) | **SaaS** (software / product) |
|---|---|---|
| What is sold | Expertise, time, custom implementation | Access to a standardised product |
| Revenue | One-off project fees, retainers | Recurring subscriptions |
| Delivery | Bespoke, per client | Self-serve, identical for everyone |
| Scaling | Limited by hours and headcount | Automatic; a new customer costs almost nothing |
| Who hosts | Often the client | Always the vendor |

## 2. The verdict, from the code

acami is an **agency**, and the landing page says so more plainly than most agencies would dare.
From [static/landing.html](../static/landing.html):

> "The software is not what you buy: the whole stack fits on free tiers and the API keys stay yours.
> What is paid for is the setup and the wiring into your tools — quoted after a call, because a
> figure announced before seeing your inbox would be a made-up one."

Every structural marker points the same way:

| Marker | SaaS would be | acami is |
|---|---|---|
| What is sold | Access to a product | Installation and configuration labour |
| Licence fee | The entire revenue | **0 €**, stated three times on the page |
| Pricing | Per seat, per month, self-serve | 1 490 € one-off; from 8 900 € quoted after a scoping call |
| Who hosts | The vendor | The client — `run_solo.py` on their own machine |
| Who pays for compute | The vendor, out of margin | The client: "the API keys are yours" |
| Onboarding | Sign up, log in | We install it, ingest their knowledge base, calibrate thresholds, train the team |
| Revenue ceiling | Users acquired | Deployments that can be personally set up |

The Enterprise tier — custom agent work for the client's trade, scope depending on which tools must
be connected, deposit on signature and balance on handover — is a **consulting engagement with a
deliverable**. That is the textbook agency shape.

## 3. The one genuine ambiguity: monthly care

290 €/month recurring looks SaaS-like. It is not: it is a **retainer**.

Token monitoring, knowledge-base curation, GDPR-purge verification, access review — human labour on
a schedule, priced per client. The clearest evidence is in the page's own markup, in a comment
written before this analysis existed:

> "An ADD-ON, not a fourth tier: it cannot be bought on its own (there is nothing to maintain before
> a deployment exists), so presenting it as a peer would invite exactly the purchase we would then
> have to refuse."

SaaS revenue exists whether or not the vendor does anything this month. That 290 € does not.

## 4. The interesting part: SaaS-shaped software, sold as a service

A real tension in the repository, worth stating rather than hiding. Sitting unused under an agency
business model:

- `org_id` tenant scoping across every local store, plus Postgres row-level security
  ([tenant.py](../aca/core/tenant.py), [vector_store.py](../aca/integrations/vector_store.py))
- Usage-based billing wired to Stripe ([billing.py](../aca/integrations/billing.py))
- Per-tenant white-label branding and settings, editable at runtime with no restart
  ([branding.py](../aca/core/branding.py), [config_store.py](../aca/storage/config_store.py))
- Named accounts, roles, TOTP, session expiry — multi-user authentication
- A Prometheus `/metrics` endpoint whose own roadmap note says it is "useful only once several
  clients run"

That is the plumbing of a multi-tenant product. But `ACA_ORG_ID` is documented as *"one ACA
deployment = one tenant, not per-request multi-org routing within a single process"* — the
capability exists and the deployment model deliberately does not use it.

**Why it exists anyway**: it was built during the §12/§14 commercialisation passes, before the
delivery model was settled at §23.2 as hybrid — demo hosted by acami, production at the client. It
is best described as an *optional future path*, not the current plan.

## 5. What would have to reverse to become a SaaS

Three decisions, all currently load-bearing:

1. **acami would host it.** Today: client infrastructure, explicitly, as a selling point.
2. **acami would own and pay for the API keys.** Today: "the keys are yours" is on the pricing card.
3. **Self-serve signup would replace the install engagement.** Today: the install *is* the product.

Each reversal removes one of the page's three strongest trust arguments. That is not an argument
against ever doing it — it is an argument for not doing it by accident.

## 6. Why agency-first is the right sequence

Not a consolation. It is how you find out what is worth standardising:

- **You learn the shape of the work by doing it manually.** Five hand-installations tell you which
  three settings every client changes and which fifteen nobody touches. No amount of planning
  produces that list.
- **The productised part already exists**, which is what makes 1 490 € viable where bespoke
  consulting would be ten times that. The agency is not selling hours from scratch; it is selling
  hours on top of a system with 773 tests.
- **Cash arrives before a platform bill starts.** A SaaS pays for hosting and inference from day one,
  for every trial user. An agency carries none of that.
- **The multi-tenant foundation is already there** if the model ever changes — see §4. The path is
  open; it is simply not the current road.

## 7. How to describe it

**In a thesis or a report**: *an AI agency selling a productised service.* The product is real and
reusable; the revenue model is service-based and scales with time, not user count.

**To a prospect**: do not say "agency" or "SaaS" at all — both invite the wrong comparison. Say what
happens: *"We install a system on your machines. You pay for the installation. There is no licence
and no subscription for the software itself."*

**In the contract**: a supply of services with a deliverable, not a software licence. This is
already what [legal.html](../static/legal.html) §2 of the Terms says — *"What is invoiced is setup
and integration, never a software licence."*

---

## Related

- [BRAND.md](BRAND.md) — positioning, values, voice
- [MARKETING.md](MARKETING.md) — how the service actually reaches buyers
- [ACAM_roadmap.md](ACAM_roadmap.md) §12/§14 — where the multi-tenant scaffolding came from

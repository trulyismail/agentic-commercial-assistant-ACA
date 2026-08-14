# acami — Brand guidelines

> The implementation layer. For *why* any of this is the way it is, read [BRAND.md](BRAND.md) first.
>
> **Every number here was measured, not chosen.** Contrast ratios come from
> `branding.contrast_ratio()`; minimum sizes come from rendering the logo at each size and looking
> at it. Where something is unresolved, it says so.

---

## 1. The assets

**All seven files are generated.** Do not edit them by hand — edit
[scripts/build_brand_assets.py](../scripts/build_brand_assets.py) and re-run it:

```
python scripts/build_brand_assets.py
```

The script is idempotent (verified: two consecutive runs produce byte-identical output across all
seven files), so CI can re-run it and fail if the committed assets have drifted — the arrangement
`export_graph.py` and `export_openapi.py` already use.

| File | Use |
|---|---|
| `static/brand/acami-lockup.svg` | **Primary.** Mark + wordmark, light backgrounds. |
| `static/brand/acami-lockup-dark.svg` | Same, for dark backgrounds. |
| `static/brand/acami-mark.svg` | Mark alone. Avatars, app icons, favicons, watermarks. |
| `static/brand/acami-mark-mono.svg` | Mark in `currentColor` — the host page decides the ink. |
| `static/brand/acami-favicon-32.png` | Browser tab. |
| `static/brand/acami-favicon-180.png` | `apple-touch-icon`. |
| `static/brand/acami-og.png` | 1200×630 social card (`og:image`, `twitter:image`). |

### Why the wordmark is drawn, not typed

`acami` is not text set in a font. It is a monoline construction of circles, straight stems and
arcs, generated from four numbers (x-height 60, stroke 12, letter gap 7, bowl radius 24).

Two reasons, and the second is the honest one:

1. A logo that depends on a web font falls back to a system font the first time a network is slow,
   and the wordmark silently changes shape.
2. **The typeface of the supplied artwork is not known.** A geometric reconstruction that says so is
   better than a near-miss font presented as correct.

**If you have the original font or vector**, this is the one thing worth replacing: swap it into
`_wordmark()` in the build script and all seven assets regenerate from it.

---

## 2. The mark

Four-pointed star, concave sides, slightly taller than wide.

| Parameter | Value | Meaning |
|---|---|---|
| `STAR_F` | `0.62` | How far each Bézier control point is pulled toward the centre. `0` = a bulging lozenge, `1` = four needles. |
| `STAR_RATIO` | `1.17` | Height ÷ width. |

`0.62` was chosen by rendering six values (0.40 / 0.48 / 0.55 / 0.62 / 0.70 / 0.78) side by side
against the supplied artwork. A first attempt at 0.44 came out visibly plumper than the reference;
a first sweep across 0.55–0.92 came out visibly thinner.

Each side is **one** cubic Bézier between adjacent tips — four segments, no node anywhere except at
a tip. That is what keeps the points sharp; a spline through a mid-side node rounds them off at
small sizes.

---

## 3. Clear space and minimum sizes

**Clear space** — keep a margin of **one star-width** on all four sides of the lockup, and of
**half a star-width** around the mark alone. Nothing enters it: no rule, no text, no photograph edge.

**Minimum sizes**, established by rendering a size ladder and reading it, not by convention:

| Asset | Minimum | What fails below it |
|---|---|---|
| **Lockup** | **120 px wide** (≈ 32 mm print) | At 90 px the `i` dot crowds its stem and the letter gaps close up. Still legible, no longer clean. |
| **Mark** | **16 px** | Verified legible at 16 px — the concave sides survive. This is why the favicon works. |

Below 120 px, **use the mark alone**. Never shrink the lockup past it to fit a space; the mark
exists precisely so you do not have to.

---

## 4. Misuse

Six things that break the identity. All six are easy to do by accident.

1. **Do not retype the wordmark in a font.** It is a drawn shape. Setting "acami" in Poppins is a
   different logo.
2. **Do not capitalise.** `acami` — never *Acami*, never *ACAMI*, including at the start of a
   sentence. The lowercase is what separates the commercial name from `ACAM` and `ACA`.
3. **Do not recolour the mark into the accent.** Amber means one thing in this system (*a human must
   decide here*). A permanently amber logo empties the signal everywhere else — the exact defect
   §21 found across four shipped palettes.
4. **Do not use the light lockup on a dark ground.** Use `acami-lockup-dark.svg`; its ink is
   `#FDFDFD`, not a lightened grey.
5. **Do not separate the mark from the wordmark and re-space them.** The gap, and the star's height
   relative to the x-height, are part of the drawing.
6. **Do not add effects.** No drop shadow, no gradient, no outline, no bevel. The supplied artwork
   was an embossed 3D mockup; that was a *presentation* of the logo, not the logo.

---

## 5. Colour

The supplied artwork is monochrome, so the identity is monochrome: ink and paper, with **one**
accent that is reserved rather than decorative.

| Token | Value | Role | Provenance |
|---|---|---|---|
| **Ink** | `#12171C` | Wordmark, mark, headings, body text | `BRAND_TEXT` in [branding.py](../aca/core/branding.py) |
| **Paper** | `#FDFDFD` | Page background | the `paper` CSS variable in [landing.html](../static/landing.html) |
| **Muted** | `#636363` | Secondary text, captions, rules | the `ink-3` CSS variable |
| **Accent** | `#B4622A` | **Reserved: a human must decide here.** Nothing else. | `BRAND_ACCENT`, the `signal` variable |

### Measured contrast

| Pair | Ratio | Verdict |
|---|---|---|
| Ink on paper | **17.72** | AA and AAA, normal text |
| Muted on paper | **5.91** | AA, normal text |
| Paper on ink (dark mode) | **17.72** | AA and AAA |
| Accent on paper | **4.37** | **AA large only** — see the rule below |
| White on accent | **4.45** | **AA large only** |
| Accent on ink (dark) | **4.05** | AA large only |

**The rule those numbers force, and it is a real constraint:** the accent must never carry small
body text. At 4.37 it misses AA for normal text by a hair. Use it for a pill, a rule, a badge, a
filled button with ≥ 18.66 px bold or ≥ 24 px regular text, or a small solid shape. If you find
yourself wanting amber-coloured prose, you do not want amber.

Signal separation between ink and accent measures **0.574** on `branding.signal_separation()` —
comfortably clear of the floor that function exists to enforce, so the accent cannot be read as a
variant of the ink.

### The accent's budget

On the landing page, amber appears **three times on the whole page**. That is not a coincidence and
not a ceiling to grow into — it is the point. Every additional amber element makes the other three
mean less.

---

## 6. Typography

Three roles, each earned. This is the product's system (§19, repaired §21) and acami inherits it, so
the site and the installed tool read as one thing.

| Role | Family | Used for | Parametrable? |
|---|---|---|---|
| **Display** | Instrument Serif (web) / Fraunces (app) | The document's voice: page titles, hero lines, section headings | Yes — `BRAND_FONT_DISPLAY` |
| **Sans** | Figtree (web) / Inter (app) | The tool's voice: UI, forms, tables, body copy | Yes — `BRAND_FONT` |
| **Mono** | Fragment Mono (web) / IBM Plex Mono (app) | Machine values: token counts, IDs, timestamps, code | **No, deliberately** — tabular figures in a queue must align |

**The wordmark belongs to none of these.** It is drawn geometry (§1) and shares no family with the
running text — normal for a logotype, and the reason it stays itself when the site loads without its
web fonts.

Every family has a full system fallback stack. Setting `BRAND_FONT` to `Système` suppresses **all
three** web-font requests — a promise about the network, not about a font — and a test locks that.

---

## 7. Applications

| Surface | Logo | Notes |
|---|---|---|
| **Landing page** nav | Lockup, ~150 px | Links to `/`. Clear space respected against the nav rule. |
| **Landing page** footer | Mark, mono, muted ink | Small; the lockup already appeared at the top. |
| **Legal pages** header | Lockup, ~140 px | Same treatment, so the pages read as the same site. |
| **Social card** | `acami-og.png` | Generated. Do not crop — 1200×630 is the format, not a suggestion. |
| **Browser tab** | `acami-favicon-32.png` | Opaque paper ground on purpose: a transparent star vanishes on a dark tab strip. |
| **Streamlit app** footer | Agency mark, muted, ≤ 16 px | The **maker's** mark, distinct from the client's white label. |
| **Streamlit login screen** | Agency mark + name | The one screen every role sees before anything else. |
| **Client deployments** | Client's `BRAND_LOGO` throughout; acami mark in the footer only | The app wears the client's identity. acami signs it, quietly, once. |
| **Proposal PDFs** | Client's brand, not acami's | The document is from the client to their prospect. acami does not appear on it. |
| **Slides / decks** | Lockup on the title slide, mark in the footer rule | Paper ground, ink text, at most one amber element per section. |

### The white-label boundary

This distinction is what makes the system work, and it is easy to get backwards:

- `BRAND_*` tokens are **the client's**. They change per tenant, the client edits them, and they
  cover the entire interface.
- `AGENCY_*` tokens are **acami's**. Set once, they survive a client re-theming everything, and they
  cover exactly one small footer mark.

A client who picks a new preset must never be able to erase the maker's mark by accident — which is
why these are two separate tables and not one.

---

## 8. Unresolved

Stated rather than left to be discovered:

- **The wordmark's original typeface is unknown.** The current letterforms are a geometric
  reconstruction, compared against the supplied artwork by eye. If the source font or vector exists,
  it should replace `_wordmark()`.
- **No trademark search has been performed.** `acami` must be checked at INPI (France) and INNORPI
  (Tunisia) before commercial use. Nothing in this repository can do that.
- **No domain is registered.** Every canonical URL, `hreflang` and sitemap entry in this project is
  prepared and inert until one exists.
- **Nothing has been printed.** All contrast figures are for screen. CMYK conversion of `#B4622A`
  has not been checked, and amber is the colour most likely to shift.

# Test emails — one per agent / feature

A catalogue of ready-to-use emails to exercise every node of the ACAM v2 graph, plus how to
trigger each and how to verify it fired. Built from live testing (2026-07-24).

## How to use

1. Run the UI: `streamlit run ui.py`
2. Either **send the email** (from any address) to the Gmail account ACA monitors, then sidebar →
   *"Rechercher les e-mails non lus"* → load it → *"Lancer l'analyse IA"*; **or** paste it into the
   **manual form** (sender / subject / body) — required for a few features (see caveats).
3. Watch **"Raisonnement de l'équipe"** — the agent graph lights up node by node, and the reasoning
   log names each step. That log is your proof of which agents ran.

## Read these caveats first (they save confusion)

- **Enrichment needs a real company domain, and the domain comes from the *sender's e-mail*, not the
  body.** A mail sent from your personal Gmail shows `@gmail.com` → *"aucun profil (domaine générique)"*.
  To test enrichment, use the **manual form** with a sender like `nadia.cherif@teamwill-consulting.com`.
- **`veille` is rare by design.** It only fires when the FAQ returns *nothing*. With a comprehensive
  74-row FAQ, almost every product question amber-matches something, so `veille` seldom triggers —
  that's the system working (a thorough FAQ = few gaps). To *force* a demo, see the `veille` section.
- **`Valider` writes REAL data**: a Google Sheet row, a HubSpot Contact+Deal+Note (token is set), and
  a Gmail draft reply. Use test data and clean it up afterwards. `Rejeter` writes nothing.
- **Formula-injection** is easiest to test via the **manual form** (put `=1+2` in a field).
- The pipeline reasons in **French** — keep test emails in French for realistic classification.

---

## 1. `classifier` — every email (category + confidence)

Fires on **every** email. The four examples below each land in a different category. Watch the
category badge + "confiance".

### 1a. `DEMANDE_DEMO` — the main happy path (also triggers stratège + Calendly link)

**Subject:** `Demande de démonstration – déploiement pour nos équipes conseil`
```
Bonjour,

Je me permets de vous contacter au nom de Teamwill, cabinet de conseil spécialisé
dans les solutions pour le secteur bancaire et financier. Nous évaluons plusieurs
plateformes pour équiper nos équipes projet, et la vôtre nous a été recommandée.

Nous serions intéressés par une démonstration pour environ 25 utilisateurs.
Pourriez-vous m'indiquer vos tarifs pour une licence professionnelle et le niveau
de SLA que vous garantissez ?

Bien cordialement,
Marc Lefèvre
Responsable des opérations – Teamwill
```
**Triggers:** classification `DEMANDE_DEMO`, extraction, `connaissance` (tarifs/SLA are in the FAQ),
`stratège` proposal with the real **Calendly link** appended, `reflection`.

### 1b. `DEVIS` — quote request

**Subject:** `Demande de devis – 30 licences`
```
Bonjour,

Nous souhaitons souscrire à votre offre professionnelle pour 30 utilisateurs.
Pourriez-vous m'envoyer un devis détaillé avec le tarif annuel et les conditions
de paiement ?

Cordialement,
Karim Benali – Teamwill
```
**Triggers:** classification `DEVIS`, full worker path, proposal (no Calendly — not a demo).

### 1c. `SUPPORT` — routed, no CRM

**Subject:** `Problème de connexion à mon compte`
```
Bonjour,

Je suis client chez vous et je n'arrive plus à me connecter à mon compte depuis
ce matin (message « identifiants invalides »). J'ai déjà réinitialisé mon mot de
passe sans succès. Pouvez-vous m'aider rapidement ? C'est bloquant.

Merci,
Sophie Marchand – Teamwill
```
**Triggers:** classification `SUPPORT` → **`routing`** (alert to SUPPORT channel + Gmail forward
draft). No prospect card, no "Valider", no CRM write.

### 1d. `AUTRE` — routed to HR, no CRM

**Subject:** `Proposition de partenariat`
```
Bonjour,

Je représente un cabinet de recrutement et je souhaiterais explorer un
partenariat commercial avec votre entreprise. Seriez-vous ouvert à un échange ?

Cordialement,
Julien Petit
```
**Triggers:** classification `AUTRE` → routed to HR (alert / Gmail forward draft). No CRM.

### 1e. `SPAM`

**Subject:** `!!! GAGNEZ un iPhone GRATUIT maintenant !!!`
```
FÉLICITATIONS !!! Vous avez été sélectionné pour recevoir un iPhone GRATUIT.
Cliquez ici immédiatement : http://bit.ly/xxxx  Offre limitée !!!
```
**Triggers:** classification `SPAM` → plain error box, workers skipped, no CRM.

---

## 2. `memory_lookup` — returning customer / duplicate

**How to trigger:** send **any** of the emails above, validate it, then send **another email from the
same sender address**. On the second run you'll see the **returning-customer** and/or **duplicate**
banner.
**Verify:** blue "client déjà connu" / duplicate banner; reasoning log mentions `sender_history`.

---

## 3. `risk_scan` — contractual red flags (deterministic, no LLM)

Take email **1a** or **1b** and add this line to the body:
```
Notre service juridique exige que le contrat prévoie des pénalités de retard et
une responsabilité illimitée du prestataire.
```
**Triggers:** red **risk-flag banner** ("Responsabilité illimitée, Pénalités de retard"); the stratège
is told to refuse committing and defer to legal; the flags are prepended to the notification.
**Bonus:** put the risky clause **inside a PDF attachment** instead of the body — `risk_scan` reads
`attachment_text` too, so a flag firing from a PDF-only clause proves attachment extraction *and* the
risk scan at once.

---

## 4. `extractor` + `ingestion` — multimodal attachments

**How to trigger:** attach a **PDF + a Word (.docx) + an Excel (.xlsx)** to email **1a**.
**Tip:** put a detail in the PDF that is **not** in the email body (a specific deadline or licence
count). If the prospect card / draft reflects it, you've proven `ingestion_node` parsed the files.
**Verify:** reasoning log shows `Ingestion`; extracted fields / draft reference the attachment content.

---

## 5. `clarification` — dynamic interrupt (agent asks *you* a question)

**Subject:** `Renseignements`
```
Bonjour,

J'ai entendu parler de votre solution et j'aimerais en savoir plus. Est-ce que
ça pourrait convenir à une structure comme la nôtre ?

Merci,
Julien Petit – Teamwill
```
**Triggers:** `besoin_principal` too vague → the graph **pauses mid-run** and asks one question. Type
an answer in the box → it merges your answer and resumes to the stratège.
**Note:** do **not** attach an RFP here — a detailed attachment fills `besoin_principal` and the
clarification won't fire.

---

## 6. `enrichissement` — company profile from the sender domain

**Use the manual form** (not Gmail import). Set:
- **Sender:** `nadia.cherif@teamwill-consulting.com` (a real corporate domain)
- **Subject / body:** reuse email **1a**

**Triggers:** a real Tavily lookup on `teamwill-consulting.com` → a company profile in the
"Fiche prospect" card. (First call hits Tavily; it's then cached in the `Enrichissement_Cache` tab.)
**Why the form:** enrichment keys off the sender's *domain*. A mail from your personal Gmail →
`@gmail.com` → *"aucun profil (domaine générique ou indisponible)"*.

---

## 7. `connaissance` — RAG over the FAQ

**How to trigger:** ask something clearly **in** the FAQ, e.g. add to any commercial email:
`Quels sont vos tarifs professionnels et votre disponibilité garantie (uptime) ?`
**Triggers:** `connaissance` injects FAQ context (reasoning log: *"contexte FAQ injecté"*); the draft
answers from the FAQ. A borderline match shows *"confiance modérée — à vérifier"* (the amber zone).

---

## 8. `veille` — web fallback (rare by design — see caveat)

`veille` fires **only** when `connaissance` returns empty. Because the FAQ is broad, this is hard to
hit organically: off-domain questions tend to be classified `AUTRE` (skips all workers), while
commercial questions amber-match the FAQ. To force a demo:

1. Temporarily set `_DENSE_LOW_CONFIDENCE = 0.85` in `aca/integrations/sheets.py` (line ~248; normal
   value `0.62`).
2. Send an email whose **only** need is off-domain with rare vocabulary, e.g.:

**Subject:** `Prérequis avant de finaliser notre choix`
```
Bonjour,

Nous envisageons de souscrire à votre offre. Une seule condition avant tout
engagement : votre produit est-il conforme aux normes d'accessibilité WCAG 2.1
niveau AA et au référentiel RGAA pour les personnes en situation de handicap
visuel ?

Cordialement,
Nadia Cherif – Teamwill
```
3. **Revert `0.62` afterwards** (it's the empirically calibrated value).

**Triggers:** `connaissance` empty → **`veille`** runs a Tavily search → `knowledge_gap = True` if the
web finds nothing → the found Q/R is **staged** in the FAQ tab (`à valider`), reviewable in the sidebar
*"FAQ en attente"*.
**Verify:** the `Veille` node lights up; reasoning log shows *"aucun contexte FAQ trouvé"* then
*"prochain agent = veille"*.

---

## 9. `stratège` + Calendly — proposal writer

Fires on every commercial lead (`DEMANDE_DEMO`/`DEVIS`). Use email **1a** (a demo request) to see the
real **Calendly link** appended to the draft; use **1b** (a devis) to see it *absent* (Calendly only
on demo requests).
**Verify:** the editable draft = personalized reply + indicative quote + next action.

---

## 10. `reflection` — self-critique (max 1 rewrite)

Fires automatically after the stratège. To *see it rewrite*, give it a question whose answer isn't in
the FAQ (e.g. the accessibility line from §8) — the stratège may invent an answer, and `reflection`
catches it: reasoning log shows *"réécriture demandée (...)"* then a second `Stratège` pass, then
*"2e passage, brouillon conservé"* (the anti-loop cap).

---

## 11. `notification` — human alert

Fires on every real lead (a commercial one that pauses for validation). Reasoning log ends with
*"Notification envoyée"*.
**Verify:** a Slack message (and/or a self-email) arrives. On a real lead the Slack alert carries
**Valider / Rejeter** buttons — but clicking them requires the FastAPI service + a public URL
(`POST /slack/interactions`); without it, validate in Streamlit.

---

## 12. `action` — CRM write (only after "Valider")

Load any commercial lead → optionally edit the draft → click **"Valider et ajouter au CRM"**.
**Writes:** a Google Sheet `Leads` row (always) + a HubSpot Contact+Deal+Note (`HUBSPOT_ACCESS_TOKEN`
is set) + (if Gmail-sourced) marks the mail processed and creates a **Gmail draft reply** in the
thread (never auto-sent).
**Verify:** new Sheet row; new HubSpot Contact + Deal; a Gmail draft in the thread. ⚠️ Clean up test
data afterwards.

## 13. Reject — the new Streamlit button

Load any commercial lead → click **"Rejeter (ne pas envoyer au CRM)"**.
**Effect:** removes it from the queue (`queue_store.mark_rejected`), **no** CRM write, no Gmail draft,
graph not resumed. Verify the Sheet gets **no** new row.

## 14. Formula-injection escaping (security)

**Use the manual form.** Set the **company** or **contact** field to `=1+2` or
`=HYPERLINK("http://x","clic")`, then validate. Open the Google Sheet `Leads` tab: the cell shows the
**literal text** (prefixed with `'`), not an executed formula.

---

## Suggested run order (all branches, one sitting)

1. **Vague** (§5) → see the clarification interrupt, answer it, watch it resume.
2. **Demo / 1a** (§1a) with a **PDF+Word+Excel** attachment (§4) and the **risk clause** (§3) → the
   richest single run: classification, extraction, attachments, risk flags, RAG, proposal, Calendly,
   reflection → **Valider** → check Sheet + HubSpot + Gmail draft.
3. **Re-send 1a** → returning-customer / duplicate banner (§2).
4. **Enrichment** via manual form with `@teamwill-consulting.com` (§6).
5. **SUPPORT** (§1c) and **AUTRE** (§1d) → routing paths, no CRM.
6. Any loaded lead → **Rejeter** (§13) → confirm no Sheet row.
7. (Optional) **veille** demo with the threshold bump (§8), then revert.
8. (Optional) **formula-injection** via manual form (§14).

This exercises every node in the graph: `classifier`, `memory_lookup`, `risk_scan`, `extractor`,
`ingestion`, `clarification`, `supervisor`, `enrichissement`, `connaissance`, `veille`, `stratège`,
`reflection`, `routing`, `notification`, and `action`.

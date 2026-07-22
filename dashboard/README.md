# ACA — Cockpit (dashboard client, §12 item 8)

Next.js 16 (App Router, TypeScript, Tailwind v4) — le dashboard client dédié du roadmap
(`docs/ACAM_roadmap.md` §12 item 8), longtemps volontairement non construit ("décision produit
délibérément non prise") jusqu'à ce que l'utilisateur en fasse la demande explicite. Tourne
**à côté** de Streamlit (`ui.py`), pas à sa place — Streamlit reste l'outil opérationnel interne
(ingestion, réglages avancés, revue FAQ) ; ce dashboard est la vue client-facing : file d'attente,
graphe visuel de l'équipe d'agents, HITL (Valider/Rejeter/Éditer), réglages, usage.

Ne parle jamais directement à la base de données — tout passe par `aca/api.py` (le microservice
FastAPI existant, pensé à l'origine pour un futur port n8n) via une clé API (`ACA_API_KEY`), envoyée
uniquement depuis le serveur Next.js (Server Components / Server Actions / Route Handlers) —
jamais depuis le navigateur.

## Démarrer en local

1. Backend : `uvicorn aca.api:api --port 8000` depuis la racine du projet (voir `CLAUDE.md`).
2. `cp .env.local.example .env.local` et remplir :
   - `ACA_API_KEY` — identique à celle du `.env` racine (laisser vide si le backend tourne sans, en dev)
   - `ACA_API_URL` — `http://localhost:8000` par défaut
   - `DASHBOARD_PASSWORD` — mot de passe partagé du dashboard (même contrat que `ACA_UI_PASSWORD`)
   - `DASHBOARD_SESSION_SECRET` — chaîne aléatoire longue (`node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`)
3. `npm install`
4. `npm run dev` puis ouvrir [http://localhost:3000](http://localhost:3000)

## Structure

- `middleware.ts` — protège toutes les routes derrière le cookie de session, sauf `/login`.
- `lib/session.ts` — session à mot de passe unique (HMAC, pas de compte individuel).
- `lib/aca.ts` — client serveur vers `aca/api.py` (`server-only`, attache `X-API-Key`).
- `lib/graph-topology.ts` — topologie du `StateGraph` LangGraph réel, à tenir synchronisée avec
  `aca/core/app.py` (même remarque que côté Streamlit, `ui.py`'s `GRAPH_EDGES`).
- `components/pulse-graph.tsx` — le graphe animé (SVG + `motion`), signature visuelle du dashboard :
  mode `"ambient"` (fond du login) ou `"progress"` (état réel d'un thread).
- `app/(dashboard)/` — Vue d'ensemble (roster + file d'attente + historique), Réglages, Facturation.
- `app/api/threads/[id]/...` — routes proxy minces utilisées par les composants client (le drawer
  HITL) pour Valider/Rejeter/Répondre à une clarification, sans jamais exposer `ACA_API_KEY`.

## Ce qui n'est pas fait

- Pas de comptes individuels (mot de passe partagé, comme `ACA_UI_PASSWORD`) — cohérent avec le
  reste du projet, pas un vrai système multi-utilisateur.
- Pas de mise à jour en temps réel (WebSocket/SSE) : la liste se rafraîchit au chargement de la
  page / après une action. Suffisant au volume prototype.
- Pas de vue Stripe live — `billing.py` reste un scaffold côté backend, jamais exercé contre un
  vrai compte Stripe (voir `CLAUDE.md`).
- Pas encore déployé (Vercel ou autre) — tourne en local pour l'instant, décision de hosting
  différée volontairement (cf. roadmap §12 item 8).

# ACA — Assistant Commercial Agentique

**Vos e-mails commerciaux sont lus, qualifiés, enrichis et une réponse est rédigée — pendant votre
absence. À votre connexion, tout le travail est fait : il ne reste qu'à cliquer « Valider ».**

ACA pré-lit les e-mails entrants et leurs pièces jointes (PDF, Word, Excel), extrait les informations
du lead, interroge votre base de connaissances, enrichit le profil de l'entreprise, rédige une
proposition — puis **s'arrête**. Aucune ligne n'entre dans le CRM et aucun e-mail n'est envoyé avant
qu'un humain ait validé. C'est un rédacteur, pas un agent autonome sur votre CRM.

> Prototype de stage (8 semaines) construit sur **LangGraph** (graphe d'états à interruption native
> pour le *human-in-the-loop*), **Groq** (Llama, gratuit) et **Google Sheets** comme CRM + base de
> connaissances. Stack intégralement en paliers gratuits.

---

## Deux paliers de déploiement — n8n est optionnel

| Palier | Composants | Pour qui |
|---|---|---|
| **Solo** | API + interface + poller + planificateur | Consultant seul, PME, démo. **Automatisé de bout en bout, sans n8n.** |
| **Enterprise** | idem **+ n8n** | Orchestration avec vos autres outils (CRM, ERP, ticketing) |

```bash
docker compose --profile solo up          # sans n8n
docker compose --profile enterprise up    # avec n8n
```

Le palier Solo n'est **pas** un mode dégradé « à boutons » : `poller.py` ingère Gmail et exécute le
graphe 24/7 même interface fermée, `scheduler.py` passe les relances et la purge RGPD à heure fixe.
**n8n n'apporte pas l'automatisation — il apporte l'orchestration inter-systèmes.** Les deux paliers
font tourner la même image et la même API : on retire n8n en changeant un mot.

| Capacité autonome | Palier Solo | Palier Enterprise |
|---|---|---|
| Ingestion des e-mails | `poller.py` | nœud Gmail Trigger |
| Traitement passif 24/7 | `poller.py` | workflow déclenché par webhook |
| Purge RGPD automatique | `scheduler.py` | nœud Schedule |
| Maintenance de la file | `scheduler.py` | nœud Schedule |
| Relances commerciales | `scheduler.py` | nœud Schedule |

Détails d'intégration : [n8n/README.md](n8n/README.md).

---

## Démarrage rapide

```bash
python -m venv venv && venv\Scripts\activate     # Windows ; source venv/bin/activate ailleurs
pip install -r requirements.txt
cp .env.example .env                             # puis remplir GROQ_API_KEY + GOOGLE_SHEETS_ID
python scripts/setup_sheets.py                   # crée l'en-tête de l'onglet Leads (une fois)
python scripts/setup_faq.py                      # seed la FAQ : 74 paires Q/R (une fois)
python scripts/run_solo.py                       # API + interface + poller + planificateur
```

Interface sur <http://localhost:8501>, API sur <http://localhost:8000> (santé : `/health`).

Le **strict minimum** est `GROQ_API_KEY` + un Google Sheets. Tout le reste est optionnel : une
variable absente signifie « fonctionnalité ignorée », jamais un plantage. Les 54 variables sont
documentées une par une dans [.env.example](.env.example).

Essai sans rien configurer — exécute le graphe sur 6 e-mails de démonstration et s'arrête à la
validation, **sans jamais écrire au CRM** :

```bash
python -m aca.core.app
```

---

## Architecture

Graphe multi-agents à superviseur, compilé avec `interrupt_before=["action"]` et une `RetryPolicy`
sur chaque nœud à appel externe :

```
START → ingestion → classifier → memory_lookup → risk_scan → extractor → clarification (❓ question à l'humain)
      → SUPERVISEUR ⇄ workers ──FINISH──→ routing → notification ──⏸ PAUSE── action → END
           ├─ enrichissement   profil entreprise (Tavily + cache Sheets)
           ├─ connaissance     RAG hybride dense+creux, fusion RRF (Gemini)
           ├─ veille           repli web si la FAQ ne sait pas → enrichit la FAQ
           └─ stratege ──→ reflection ──rewrite (1× max)──→ stratege
                            (auto-critique du brouillon)
```

La topologie affichée dans l'interface est **dérivée du graphe compilé**, jamais recopiée
([graph_topology.py](aca/core/graph_topology.py)). Export : `python scripts/export_graph.py` →
[docs/assets/graph.json](docs/assets/graph.json).

**Deux pauses humaines :** une *clarification* en cours de route (le graphe pose une question quand
le besoin est ambigu) et la *validation* finale avant écriture CRM.

**Mémoire hybride :** court terme = checkpointer LangGraph (survit aux pauses, aux redémarrages et
au passage d'un processus à l'autre) ; long terme = Google Sheets (`Leads`, `FAQ`,
`Enrichissement_Cache`).

---

## Ce qui est vérifié en direct, et ce qui ne l'est pas

Cette section existe parce qu'un prototype qui prétend tout avoir vérifié n'est pas crédible.

**Vérifié contre les vrais services :** Gmail (lecture, marquage, brouillons), Google Sheets (CRM +
RAG), Groq, Gemini, Tavily (enrichissement + veille), Slack (alertes + routage), HubSpot (contact +
deal + note, créés puis supprimés), Supabase (pgvector + checkpointer partagé + RLS multi-tenant),
rétention RGPD, relances.

**Codé et testé hors ligne, jamais exercé en réel** — faute de compte ou d'instance, dit sans détour :
- le workflow n8n ([n8n/aca_workflow.json](n8n/aca_workflow.json)) — aucune instance n8n n'existe ;
- la facturation Stripe ([billing.py](aca/integrations/billing.py)) — aucun compte de test ;
- les boutons d'approbation Slack — nécessitent une app Slack avec Interactivité et une URL publique ;
- TLS — la procédure est écrite ([docs/DEPLOYMENT_HARDENING.md](docs/DEPLOYMENT_HARDENING.md)), rien n'est hébergé.

**Mesuré :** classification **100 %** (50/50) sur un jeu labellisé de 50 e-mails
(`python -m aca.eval.eval_classifier`) · **352 tests** hors ligne en ~13 s · seuils du RAG calibrés
empiriquement sur une FAQ de 74 lignes.

---

## Sécurité et conformité

- **Human-in-the-loop non contournable** — `action_node` reste derrière `interrupt_before`. Aucune
  écriture CRM, aucun e-mail envoyé sans clic humain, sur *toutes* les surfaces (interface, API, Slack, n8n).
- **Comptes nominatifs**, mots de passe PBKDF2 salés, rôles `admin`/`operator`, verrou progressif,
  sessions à TTL absolu + inactivité.
- **Journal d'audit chaîné par hachage** — modifier ou supprimer une ligne casse la chaîne, et
  `python -m aca.storage.audit_log` le détecte et localise la rupture.
- **RGPD** — purge par ancienneté *et* droit à l'effacement (`--oublier <adresse>`), politique de
  confidentialité, isolation multi-tenant (`org_id` + RLS Postgres vérifiée en direct).
- **Injections de prompt** signalées (jamais bloquantes : le gate humain reste la protection) et
  **risques contractuels** détectés par regex déterministe, tous deux remontés dans l'alerte.
- `ACA_ENV=production` rend ces protections **obligatoires** : l'application refuse de démarrer si
  une clé manque, au lieu du « absent = ouvert » du mode développement.

Détail complet : §15 de [docs/ACAM_roadmap.md](docs/ACAM_roadmap.md) et
[docs/DEPLOYMENT_HARDENING.md](docs/DEPLOYMENT_HARDENING.md).

---

## Les surfaces

| Surface | Rôle | Statut |
|---|---|---|
| **Streamlit** ([ui.py](ui.py)) | console opérateur complète : intake, validation, édition, ingestion, KPI, réglages | colonne vertébrale opérationnelle |
| **API FastAPI** ([aca/api.py](aca/api.py)) | le cerveau en HTTP — pilote le dashboard, Slack et n8n | active |
| **Slack** | valider/rejeter un lead sans ouvrir aucune interface | active (app Slack à configurer) |
| **n8n** | orchestration avec vos autres outils | optionnelle ([n8n/](n8n/)) |
| **Dashboard Next.js** ([dashboard/](dashboard/)) | vitrine construite, sous-ensemble en lecture seule | **parqué** (§12bis) |

---

## Commandes utiles

```bash
python -m pytest tests/                         # 352 tests, hors ligne, ~13 s
python -m aca.eval.eval_classifier              # précision du classifieur sur 50 e-mails labellisés
python -m aca.core.scheduler --status           # dernier passage de chaque travail planifié
python -m aca.core.retention --oublier a@b.fr   # RGPD : effacement complet d'une personne
python -m aca.storage.audit_log                 # vérifie l'intégrité de la chaîne d'audit
python -m aca.storage.user_store create x --role admin   # créer un compte
python scripts/verify_rls.py                    # audit RLS Supabase (lecture seule)
python scripts/export_openapi.py                # régénère docs/openapi.json
```

## Structure

| Chemin | Rôle |
|---|---|
| [aca/core/](aca/core/) | graphe LangGraph, poller, planificateur, relances, rétention, sécurité |
| [aca/integrations/](aca/integrations/) | Sheets, Gmail, HubSpot, Slack, webhook sortant, pgvector, Stripe |
| [aca/storage/](aca/storage/) | 8 registres SQLite locaux (file, analytics, audit chaîné, comptes…) |
| [aca/ingestion/](aca/ingestion/) | extraction PDF/Word/Excel, ingestion de connaissances |
| [ui.py](ui.py) · [aca/api.py](aca/api.py) | interface Streamlit · microservice FastAPI |
| [tests/](tests/) · [n8n/](n8n/) · [docs/](docs/) | suite de tests · intégration n8n · documentation |

Onglets Google Sheets — **`Leads`** : `Date · Expéditeur · Entreprise · Contact · Urgence · Besoin ·
Catégorie · Brouillon` · **`FAQ`** : `Question · Réponse · Statut` · **`Enrichissement_Cache`** :
`Domaine · Profil · Date`.

## Prérequis

Python 3.11+ (développé sous 3.14) · un tableur Google Sheets · un compte de service Google (Sheets)
et un client OAuth « installed app » (Gmail) dans `credentials/` · des clés gratuites Groq et Gemini.

> Au premier accès Gmail, un navigateur s'ouvre pour l'autorisation (scope `gmail.modify`) ; le jeton
> est ensuite mis en cache. À faire une fois en local — impossible en headless.

## Alimenter la base de connaissances

```bash
python -m aca.ingestion.ingest chemin/vers/doc.pdf           # ajoute les Q/R extraites à la FAQ
python -m aca.ingestion.ingest chemin/vers/doc.md replace     # ou remplace tout le contenu
```

(Également possible via l'uploader de la barre latérale Streamlit.)

## Documentation

[static/landing.html](static/landing.html) (page de présentation — atteignable depuis la barre
latérale de l'interface, bouton « Page de présentation », ou à ouvrir directement dans un
navigateur) · [docs/ACAM_roadmap.md](docs/ACAM_roadmap.md) (architecture, audits, décisions) ·
[docs/PROJECT_JOURNAL.md](docs/PROJECT_JOURNAL.md) (journal de bord) ·
[docs/DEPLOYMENT_HARDENING.md](docs/DEPLOYMENT_HARDENING.md) (TLS, secrets, rotation) ·
[docs/PRIVACY_POLICY.md](docs/PRIVACY_POLICY.md) (RGPD) · [CLAUDE.md](CLAUDE.md) (référence technique).

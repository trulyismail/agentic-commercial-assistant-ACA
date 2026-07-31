# Améliorations suggérées — ce qu'attendent les produits comparables

Document de recommandations rédigé le 2026-07-30, à la demande explicite (« tell me and suggest what
I should add more, u can do a research on what's needed on famous apps »).

> **Statut au 2026-07-30 (§18 de `docs/ACAM_roadmap.md`) :** toutes les recommandations ont été mises
> en œuvre, à l'exception de l'hébergement (§1.2, §2 TLS, §10 du récapitulatif) et de l'alerte Slack
> sur incident (§1.1, §3 du récapitulatif) — exclues explicitement, les deux seules exigeant une
> infrastructure réelle absente de ce projet. SSO/SCIM et multilingue restent non faits, comme ce
> document le recommandait lui-même (« à n'engager que sur demande d'un client réel »). Voir le §18
> de `docs/ACAM_roadmap.md` pour le détail de ce qui a été livré et vérifié.
>
> **Mise à jour du 2026-07-31 :** le multilingue a en fait été engagé le lendemain, sur demande
> explicite (pas un « client réel » au sens de cette recommandation, mais la même logique : ne pas
> le construire avant qu'on le demande). Portée délibérément réduite au **chrome principal**
> (navigation, en-têtes/légendes, boutons/messages clés) plutôt qu'à chaque chaîne du projet — un
> compromis choisi par l'utilisateur lorsqu'on lui a posé la question, pas une lacune. Voir
> `aca/core/i18n.py` et le §18 (addendum) de `docs/ACAM_roadmap.md`/`docs/PROJECT_JOURNAL.md`.
> SSO/SCIM reste non fait, sans changement.

**Méthode.** Chaque point a été confronté au code réel avant d'être écrit, pas listé de mémoire ni
recopié d'un article. Beaucoup de « bonnes pratiques » que l'on retrouve dans les listes en ligne
**existent déjà** dans ce projet — les signaler comme manquantes serait faux et ferait perdre du
temps. La colonne « État » distingue donc trois cas : ✅ déjà fait (avec le fichier qui le prouve),
⚠️ partiel, ❌ absent.

**Sources externes consultées :** [SOC 2 Compliance in 2026 — Venn](https://www.venn.com/learn/soc2-compliance/) ·
[User Authentication Best Practices for B2B SaaS in 2026 — Security Boulevard](https://securityboulevard.com/2026/05/user-authentication-best-practices-for-b2b-saas-in-2026/) ·
[SOC 2 Evidence Storage Best Practices — Konfirmity](https://www.konfirmity.com/blog/soc-2-evidence-storage-best-practices) ·
[Maintaining SOC 2 Compliance in 2026 — Scytale](https://scytale.ai/resources/maintaining-soc-2-compliance/) ·
[10 Must-Have Features in an Enterprise SSO Solution for B2B SaaS in 2026 — Security Boulevard](https://securityboulevard.com/2026/04/10-must-have-features-in-an-enterprise-sso-solution-for-b2b-saas-in-2026/) ·
[Top 7 enterprise SSO providers for B2B SaaS — WorkOS](https://workos.com/blog/enterprise-sso-providers-b2b-saas) ·
[White-Label SaaS Integration Platform Guide 2026 — Albato](https://albato.com/blog/publications/embedded-white-label-saas) ·
[SaaS White-Labeling in 2026 — Viprasol](https://viprasol.com/blog/saas-white-label/) ·
[White-Label Analytics for SaaS 2026 — Bold BI](https://www.boldbi.com/blog/white-label-analytics-for-saas-in-2026/)

---

## 1. Les cinq choses à faire en premier

Classées par (valeur perçue par un acheteur) ÷ (effort réel sur ce code). Ce sont les seules que je
recommanderais d'attaquer avant une première démonstration client sérieuse.

### 1.1 Alerter sur incident, pas seulement le consigner ❌ (exclu du §18, hors périmètre)

Le journal d'activité (§17) enregistre désormais les échecs de connexion, les verrouillages et les
refus de permission. **Personne n'est prévenu.** Or le projet possède déjà tout le nécessaire :
`notify.py` (Slack + e-mail, dégradation gracieuse) et `scheduler.py` (table `JOBS` déclarative, une
entrée = un travail périodique).

*Ce que ça donne :* un travail `security_digest` qui, une fois par heure, compte les entrées
`outcome != success` de la dernière heure et poste une alerte Slack au-delà d'un seuil.
*Effort :* une fonction dans `scheduler.py`, une entrée dans `JOBS`, une variable d'environnement
pour le seuil.
*Pourquoi d'abord :* c'est ce qui transforme un journal — qu'il faut penser à ouvrir — en détection.

### 1.2 Domaine personnalisé + page de connexion à la marque ⚠️ (exclu du §18, hors périmètre)

La page de connexion porte déjà la marque du client depuis §17 (`branding.hero_html`). Ce qui manque
est l'URL : les guides de marque blanche insistent tous sur le domaine propre, parce qu'une adresse
tierce annule visuellement tout le travail de charte graphique.

*Effort :* nul en code — c'est une configuration de reverse proxy, déjà à moitié documentée dans
[DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md) (Caddy/Nginx, avec les en-têtes WebSocket sans
lesquels l'UI Streamlit reste bloquée sur « Connecting… »). Il reste à ajouter une section « domaine
client » et à héberger réellement.

### 1.3 Export du journal archivé, avec conservation prouvable ✅ (fait au §18)

L'export CSV existe (§17) et la chaîne d'empreintes est vérifiable. Le point que soulèvent
spécifiquement les guides SOC 2 est ailleurs : un auditeur demande des échantillons à des dates
précises (« semaine 28 »), et une absence de preuve devient une exception au rapport.

*Ce qui manque concrètement :* rien n'empêche aujourd'hui la purge RGPD
(`ACTIVITY_RETENTION_DAYS`) d'effacer la période qu'un auditeur demandera. Recommandation : un export
mensuel automatique (travail `scheduler.py`) déposant un CSV horodaté **et son empreinte** dans un
dossier d'archives, de sorte que la purge n'efface pas la preuve. C'est peu de code, et c'est
exactement la question posée en audit.

### 1.4 Deuxième facteur (TOTP) sur les comptes `admin` ✅ (fait au §18)

`user_store.py` fait déjà correctement le difficile : PBKDF2 avec sel par utilisateur, coût stocké
dans le hachage, comparaison à temps constant, hachage factice contre l'énumération des comptes,
verrou progressif. Le facteur unique reste le maillon faible pour un compte capable de créer des
administrateurs et de rediriger les alertes commerciales.

*Effort :* modéré et sans dépendance lourde — `pyotp`, ou ~40 lignes de HMAC en bibliothèque
standard ; une colonne `totp_secret` dans `users` et un champ de plus au gate. À réserver aux `admin`
dans un premier temps : imposer le TOTP à un opérateur qui valide vingt leads par jour se paierait en
contournements.

### 1.5 Avertissement avant expiration de session ✅ (fait au §18)

`session.py` applique un TTL absolu et un délai d'inactivité, et l'UI explique honnêtement le motif
d'expiration. Ce qui manque est l'avertissement **avant** : perdre un brouillon de proposition
retouché à la main parce que la session a expiré en silence est la petite frustration qui
décrédibilise un outil.

*Effort :* faible. `st.session_state` connaît déjà `last_seen` ; il suffit d'afficher un `st.toast`
quand il reste moins de cinq minutes.

---

## 2. Attentes d'un achat grand compte (si ACA se vend à une DSI)

| Attente | État | Commentaire honnête |
|---|---|---|
| Journal d'audit attribuable, exportable | ✅ | [activity_log.py](../aca/storage/activity_log.py) — §17 |
| Journal inviolable | ⚠️ | Tamper-**evident** (chaîné, HMAC si `ACA_AUDIT_HMAC_KEY`). Une vraie inviolabilité demande un stockage append-only ou un ancrage externe ; annoncé comme telle limite, jamais surpromis |
| Rôles et permissions | ✅ | `ROLE_PERMISSIONS`, fail-closed sur un rôle inconnu |
| Rôle « lecture seule » | ✅ | `ROLE_VIEWER` (§18) — `view_dashboard`/`view_history` uniquement, exclut délibérément `reject_lead` (rejeter retire l'analyse de la file de tout le monde, ce n'est pas de la consultation) |
| SSO (SAML 2.0 / OIDC) | ❌ | Attente quasi systématique au-delà de ~200 salariés. Effort important et structurant : à ne pas engager avant qu'un client réel le demande |
| SCIM (synchronisation d'annuaire) | ❌ | Corollaire du SSO : un salarié qui part doit perdre son accès sans geste manuel. Même arbitrage |
| Chiffrement en transit (TLS) | ⚠️ | Documenté ([DEPLOYMENT_HARDENING.md](DEPLOYMENT_HARDENING.md)), **jamais appliqué** — rien n'est hébergé |
| Chiffrement au repos | ❌ | Les bases SQLite locales sont en clair. Sur un portable, c'est le chiffrement disque du système qui protège (BitLocker/FileVault) : à **écrire dans la politique** plutôt qu'à recoder |
| RGPD : politique, rétention, droit à l'effacement | ✅ | [PRIVACY_POLICY.md](PRIVACY_POLICY.md), `retention.py --oublier` |
| DPA / DPIA | ❌ | Documents contractuels qui appartiennent à l'entreprise utilisatrice, pas au prototype |
| Cloisonnement multi-tenant | ✅ | `org_id` partout + RLS Postgres, vérifié en direct le 2026-07-21 |
| Isolation RLS des bases locales | ❌ | Cloisonnement applicatif seulement (`WHERE org_id = ?`). Suffisant à « un déploiement = un tenant », insuffisant pour du vrai multi-client mutualisé |

---

## 3. Marque blanche : ce qu'il reste après le §17

| Attente des guides | État | Reste à faire |
|---|---|---|
| Logo, couleurs, police, arrondis | ✅ | — |
| Cohérence connexion / application / navigation | ✅ | — |
| Mode sombre | ✅ | Deux préréglages sombres, avec défauts cohérents |
| Accessibilité de la palette | ✅ | Contrastes WCAG calculés, avertissement sans blocage |
| Personnalisation par tenant | ✅ | Stockée dans `config_store`, cloisonnée par `org_id` |
| Domaine personnalisé | ❌ | Cf. §1.2 |
| Favicon personnalisé | ❌ | `st.set_page_config(page_icon=…)` accepte une image : ~3 lignes en réutilisant `decode_logo`. Petit détail, très visible dans un onglet de navigateur |
| Modèles d'e-mail à la marque | ❌ | Les brouillons Gmail et les alertes Slack ne portent pas la charte. `notify.py` et `relance.py` sont les points d'entrée |
| Export PDF à la marque | ❌ | Une proposition commerciale exportable en PDF au logo du client serait très demandée en démonstration — et le contenu existe déjà (`draft_response`) |
| Traduction / multilingue | ✅ (partiel, 2026-07-31) | Chrome principal FR/EN (`aca/core/i18n.py`) — navigation, en-têtes, boutons/messages clés. Écrans de curation admin, journal d'activité (détail), export PDF et logs restent en français par choix de portée |

---

## 4. Ce que le journal d'activité pourrait apprendre de plus

Le §17 répond à « qui, quoi, quand, d'où ». Les produits matures vont un cran plus loin :

1. **Vue chronologique par lead** (✅ fait au §18) — reconstituer l'histoire d'un prospect : reçu,
   classé, clarifié, réécrit par Untel, validé par Untel. `activity_log.lead_timeline()` +
   `ui_kit.timeline()`, dans `app_pages/1_inbox.py`. A confirmé être le meilleur rapport
   valeur/effort de cette liste, comme prédit.
2. **Différentiel avant/après lisible** (✅ fait au §18) — `analytics_store.get_draft_edit()` (lecture
   qui manquait à `draft_edits`) + `ui_kit.diff()` (stdlib `difflib`), affiché dans la même frise.
3. **Alerte sur comportement inhabituel** (✅ fait au §18) — `activity_log.is_new_device()`/
   `known_devices()` (comparaison d'ensembles, pas d'apprentissage automatique, comme suggéré),
   surfacé comme une note sur chaque événement de la frise par lead.
4. **Rétention à deux vitesses** (✅ fait au §18) — `activity_log.purge_older_than(sensitive_days=…)`
   existait déjà depuis §17 mais n'était jamais invoqué ; `retention.py` lui passe désormais
   `ACTIVITY_SENSITIVE_RETENTION_DAYS` (défaut : deux fois `ACTIVITY_RETENTION_DAYS`).
5. **Journal des actions machine** (✅ fait au §18) — `poller.py`, `relance.py`, `retention.py` et
   `scheduler.py` journalisent désormais via `SOURCE_POLLER`/`SOURCE_CLI`, comme prédit ici
   « raccordement trivial ».

---

## 5. Interface : ce qui aiderait vraiment les utilisateurs

Au-delà de l'esthétique traitée au §17 :

1. **Raccourcis clavier** pour valider/rejeter (❌ reporté au §18, faute de temps). Un opérateur qui
   traite trente leads par jour à la souris est un opérateur ralenti. Faisable avec un composant
   `st.components.v2`.
2. **Traitement par lot** (❌ reporté au §18, faute de temps). Valider dix leads évidents d'un coup.
   À concevoir avec prudence : la validation humaine est *la* garantie du produit, un bouton
   « tout valider » la viderait de son sens. Un lot **avec confirmation par élément** est le bon
   compromis.
3. **Vue mobile** (⚠️ reporté au §18 — aucun appareil mobile disponible pour la tester). La CSS du
   §17 est responsive, mais l'interface n'a jamais été ouverte sur un téléphone. Valider un lead
   depuis un train est un usage commercial réel — que Slack couvre déjà partiellement (boutons
   Valider/Rejeter).
4. **Recherche globale** (❌ reporté au §18, faute de temps). Chaque onglet a son filtre ; rien ne
   cherche « ce prospect » partout à la fois.
5. **Notifications dans l'application** (✅ fait au §18, portée volontairement modeste). Une pastille
   « N nouvelle(s) depuis votre connexion » dans l'en-tête + un bouton « Marquer comme vu » dans la
   barre latérale. Portée par **session authentifiée** (« depuis que vous vous êtes connecté(e) »),
   pas par persistance inter-session — une persistance véritable demanderait un nouveau magasin, hors
   proportion pour cette passe.
6. **États vides actionnables** (✅ fait au §18, sur le tableau de bord uniquement). En mode
   démonstration, un bouton « Charger un exemple de démonstration » sur le tableau de bord vide
   bascule directement vers la page « Nouvel e-mail ».

---

## 6. Ce que je ne recommande pas de faire

Un document de recommandations qui ne dit jamais « non » n'aide pas à choisir.

- **Un Vector DB dédié** (Pinecone, Weaviate). pgvector sur Supabase est déjà en place, vérifié en
  direct, et exact à ce volume (74 lignes de FAQ). Migrer ne servirait qu'à la présentation.
- **Du fine-tuning sur les brouillons corrigés.** Le corpus `draft_edits` est délibérément prévu pour
  du few-shot, pas pour de l'entraînement — la remarque est déjà écrite dans le code. Quelques
  centaines d'exemples ne justifient pas un fine-tuning, et le résultat serait moins pilotable que le
  prompt actuel.
- **Reprendre le dashboard Next.js.** La décision de le mettre en pause (2026-07-24) reste juste : il
  est un sous-ensemble en lecture seule de Streamlit, sans prise d'e-mail ni curation, donc incapable
  de fonctionner seul. Le §17 vient encore d'élargir l'écart (marque blanche, journal d'activité). Le
  reprendre signifierait porter deux surfaces en parallèle.
- **Des micro-animations partout.** Le §17 en ajoute là où elles portent une information (progression
  du graphe, apparition des cartes, reflet sur l'action principale). En ajouter davantage rendrait
  l'outil fatigant pour quelqu'un qui l'utilise huit heures par jour — c'est précisément pourquoi le
  niveau est réglable et pourquoi `prefers-reduced-motion` gagne toujours.

---

## 7. Récapitulatif ordonné

| # | Action | Effort | Pourquoi | État au 2026-07-30 |
|---|---|---|---|---|
| 1 | Rôle « lecture seule » | Très faible | Une entrée dans `ROLE_PERMISSIONS` ; débloque le cas « direction qui consulte » | ✅ fait (§17) |
| 2 | Favicon à la marque | Très faible | Très visible, `decode_logo` existe déjà | ✅ fait (§17) |
| 3 | Alerte Slack sur incidents de sécurité | Faible | Transforme le journal en détection ; `notify.py` + `scheduler.py` en place | ❌ exclu du §18 (hors périmètre) |
| 4 | Journaliser les actions machine (poller/scheduler) | Faible | Les constantes `SOURCE_*` existent et ne servent à personne | ✅ fait (§18) |
| 5 | Vue chronologique par lead | Faible | Données déjà présentes ; forte valeur en démonstration | ✅ fait (§18) |
| 6 | Avertissement avant expiration de session | Faible | Évite de perdre un brouillon retouché | ✅ fait (§18) |
| 7 | Export mensuel archivé + empreinte | Moyen | La question réellement posée en audit SOC 2 | ✅ fait (§18) |
| 8 | Export PDF de la proposition à la marque | Moyen | Argument commercial direct | ✅ fait (§18) |
| 9 | TOTP sur les comptes admin | Moyen | Dernier maillon faible d'une authentification par ailleurs solide | ✅ fait (§18) |
| 10 | TLS + domaine personnalisé appliqués | Moyen | Zéro code ; il faut un hébergement | ❌ exclu du §18 (hors périmètre) |
| 11 | SSO / SCIM | Élevé | À n'engager que sur demande d'un client réel | ❌ non fait, comme recommandé |
| 12 | Multilingue | Élevé | Idem | ✅ partiel (2026-07-31), sur demande explicite — chrome principal seulement |

"""
Bascule de langue FR/EN pour l'interface (suivi de la suggestion §12 des recommandations —
"Multilingue", marquée effort élevé et "à n'engager que sur demande d'un client réel" ; ce pass
répond à une demande explicite, avec une portée volontairement réduite : le **chrome principal**
(navigation, en-têtes de page, boutons/étiquettes premiers, messages clés) plutôt que chaque chaîne
du projet — les écrans de curation admin (base de connaissances, comptes, jetons de marque),
le journal d'activité, l'export PDF et les logs console restent en français pour cette passe.

**Pourquoi un dictionnaire fait main plutôt qu'une bibliothèque i18n** (Babel, gettext…) : les clés
traduites ici tiennent dans quelques dizaines d'entrées, toutes statiques (aucun pluriel complexe,
aucun format de date localisé) — une dépendance entière pour ça reproduirait exactement le travers
que ce projet évite ailleurs (cf. `totp.py`/`slack_verify.py`, stdlib-only par principe).

**Pur, sans import Streamlit** (même posture que `session.py`/`branding.py`/`ui_kit.py`) : la lecture
de la langue courante (`st.session_state`) reste dans `aca/ui/shared.py`, qui expose `t()` aux pages.
"""
LANGUAGES = ("fr", "en")
DEFAULT_LANGUAGE = "fr"

LANGUAGE_LABELS = {"fr": "Français", "en": "English"}

# Clés nommées par zone d'écran (`nav.*`, `sidebar.*`, `auth.*`…) plutôt que par le texte français
# lui-même : un texte français n'est pas un identifiant stable, il change si on en corrige le
# libellé — la clé, elle, ne bouge pas.
TRANSLATIONS = {
    # ── Navigation (ui.py) ───────────────────────────────────────────────────────────────────
    "nav.inbox": {"fr": "Nouvel e-mail", "en": "New email"},
    "nav.dashboard": {"fr": "Tableau de bord", "en": "Dashboard"},
    "nav.history": {"fr": "Historique", "en": "History"},
    "nav.activity": {"fr": "Journal d'activité", "en": "Activity log"},
    "nav.settings": {"fr": "Réglages", "en": "Settings"},

    # ── En-tête de marque / pastilles (ui.py) ───────────────────────────────────────────────
    "hero.pending_pill": {"fr": "{n} analyse(s) en attente", "en": "{n} analysis(es) pending"},
    "hero.new_since_login_pill": {"fr": "{n} nouvelle(s) depuis votre connexion",
                                  "en": "{n} new since you signed in"},
    "hero.demo_mode_pill": {"fr": "MODE DÉMONSTRATION — modèle simulé, écritures CRM bloquées",
                            "en": "DEMO MODE — simulated model, CRM writes blocked"},

    # ── Barre latérale (ui.py) ───────────────────────────────────────────────────────────────
    "sidebar.logout": {"fr": "Se déconnecter", "en": "Sign out"},
    "sidebar.queue_header": {"fr": "File d'attente ({n})", "en": "Queue ({n})"},
    # §19 — remplace « traités automatiquement par le poller en arrière-plan (`poller.py`) ».
    "sidebar.queue_explainer": {
        "fr": "ACA relève votre boîte de réception et prépare une analyse pour chaque e-mail. "
              "Rien n'atteint le CRM avant votre validation.",
        "en": "ACA checks your inbox and prepares an analysis for each email. Nothing reaches "
              "the CRM before you validate it.",
    },
    "sidebar.intake_state": {"fr": "Réception", "en": "Intake"},
    "sidebar.intake_on": {"fr": "en marche", "en": "running"},
    "sidebar.intake_paused": {"fr": "hors plage", "en": "outside hours"},
    "sidebar.intake_off": {"fr": "désactivée", "en": "off"},
    "sidebar.intake_next": {"fr": "reprise", "en": "resumes"},
    "sidebar.mark_seen": {"fr": "Marquer comme vu", "en": "Mark as seen"},
    # §19 — rappels échus affichés dans l'application (canal sans aucun service externe).
    "sidebar.reminders_header": {"fr": "Rappels à traiter ({n})", "en": "Reminders due ({n})"},
    "sidebar.reminder_ack": {"fr": "Vu", "en": "Got it"},
    "sidebar.reminder_toast": {"fr": "Rappel : {note}", "en": "Reminder: {note}"},
    "sidebar.reminder_no_note": {"fr": "(rappel sans note)", "en": "(reminder with no note)"},
    "sidebar.new_since_login_caption": {"fr": "{n} nouvelle(s) depuis votre connexion.",
                                        "en": "{n} new since you signed in."},
    "sidebar.queue_empty": {"fr": "Aucune analyse en attente pour le moment.",
                           "en": "No analysis pending right now."},
    "sidebar.open_button": {"fr": "Ouvrir", "en": "Open"},
    "sidebar.gmail_header": {"fr": "Import Gmail", "en": "Gmail import"},
    "sidebar.gmail_search": {"fr": "Rechercher les e-mails non lus", "en": "Search unread emails"},
    "sidebar.gmail_unread_label": {"fr": "E-mails non lus", "en": "Unread emails"},
    "sidebar.gmail_load": {"fr": "Charger cet e-mail", "en": "Load this email"},
    "sidebar.models_caption": {
        "fr": "Modèles : Llama 3.1 8B (routage) · Llama 3.3 70B (extraction/rédaction) · "
              "Gemini embeddings (RAG sémantique)",
        "en": "Models: Llama 3.1 8B (routing) · Llama 3.3 70B (extraction/drafting) · "
              "Gemini embeddings (semantic RAG)",
    },
    # §23 — accès à la page de présentation depuis l'application. Elle existait depuis §16.5 mais
    # n'était atteignable qu'en connaissant son chemin sur le disque : en pratique, personne ne
    # l'ouvrait au moment où elle sert, c'est-à-dire en montrant le produit à quelqu'un, depuis
    # l'écran du produit.
    "sidebar.landing_header": {"fr": "Présentation", "en": "Overview"},
    "sidebar.landing_button": {"fr": "Page de présentation", "en": "Product overview"},
    "sidebar.landing_caption": {
        "fr": "Le pitch d'ACA en une page — ce qu'il fait, ce qui est sécurisé, ce qui est vérifié.",
        "en": "The ACA pitch on one page — what it does, what is secured, what is verified.",
    },
    "sidebar.landing_disabled": {
        "fr": "Page indisponible : activez `server.enableStaticServing` dans "
              "`.streamlit/config.toml`, puis redémarrez.",
        "en": "Page unavailable: enable `server.enableStaticServing` in "
              "`.streamlit/config.toml`, then restart.",
    },
    "footer.deployed_for": {"fr": "déployé pour {company}", "en": "deployed for {company}"},
    "footer.human_validation": {"fr": "validation humaine obligatoire avant toute écriture CRM",
                                "en": "human validation required before any CRM write"},
    "lang.switch_label": {"fr": "Langue", "en": "Language"},

    # ── Connexion (aca/ui/shared.py) ────────────────────────────────────────────────────────
    "auth.username": {"fr": "Identifiant", "en": "Username"},
    "auth.password": {"fr": "Mot de passe", "en": "Password"},
    "auth.login_button": {"fr": "Se connecter", "en": "Sign in"},
    "auth.incorrect": {"fr": "Identifiants incorrects.", "en": "Incorrect credentials."},
    "auth.incorrect_locked": {
        "fr": "Identifiants incorrects. Trop de tentatives : verrouillé {seconds} s.",
        "en": "Incorrect credentials. Too many attempts: locked for {seconds}s.",
    },
    "auth.locked_out": {
        "fr": "Trop de tentatives échouées. Réessayez dans {seconds} s.",
        "en": "Too many failed attempts. Try again in {seconds}s.",
    },
    "auth.session_idle_expired": {
        "fr": "Session fermée après une période d'inactivité. Reconnectez-vous.",
        "en": "Session closed after a period of inactivity. Please sign in again.",
    },
    "auth.session_expired": {"fr": "Session expirée. Reconnectez-vous.",
                             "en": "Session expired. Please sign in again."},
    "auth.expiry_warning": {
        "fr": "Session bientôt expirée (~{minutes} min) — enregistrez votre brouillon avant de "
              "continuer.",
        "en": "Session expiring soon (~{minutes} min) — save your draft before continuing.",
    },

    # ── Second facteur TOTP (aca/ui/shared.py) ──────────────────────────────────────────────
    "totp.code_label": {"fr": "Code à 6 chiffres", "en": "6-digit code"},
    "totp.verify_button": {"fr": "Vérifier", "en": "Verify"},
    "totp.cancel_button": {"fr": "Annuler", "en": "Cancel"},
    "totp.cancel_enrollment_button": {"fr": "Annuler l'inscription", "en": "Cancel enrollment"},
    "totp.activate_button": {"fr": "Activer le second facteur", "en": "Activate two-factor"},
    "totp.verify_prompt": {
        "fr": "Second facteur requis pour « {username} ». Saisissez le code de votre application "
              "d'authentification.",
        "en": "Two-factor code required for \"{username}\". Enter the code from your "
              "authenticator app.",
    },
    "totp.enroll_prompt": {
        "fr": "Ce compte exige un second facteur (rôle admin). Ajoutez-le dans votre application "
              "d'authentification (Google Authenticator, Aegis, 1Password…) avec le secret "
              "ci-dessous, puis saisissez le code affiché pour l'activer.",
        "en": "This account requires two-factor authentication (admin role). Add it to your "
              "authenticator app (Google Authenticator, Aegis, 1Password…) using the secret "
              "below, then enter the displayed code to activate it.",
    },
    "totp.manual_entry_expander": {"fr": "Impossible de scanner ? Saisir le secret manuellement",
                                   "en": "Can't scan? Enter the secret manually"},
    "totp.invalid_code": {
        "fr": "Code invalide — vérifiez l'heure de votre téléphone et réessayez.",
        "en": "Invalid code — check your phone's clock and try again.",
    },
    # §24 — appareils de confiance. Le libellé dit ce qui est réellement échangé : le code est
    # sauté, le mot de passe ne l'est jamais. Une case « rester connecté » laisserait croire le
    # contraire, et c'est exactement le malentendu à éviter sur un écran de second facteur.
    "totp.remember_device": {
        "fr": "Se souvenir de cet appareil pendant {days} jours",
        "en": "Remember this device for {days} days",
    },
    "totp.remember_help": {
        "fr": "Sur ce navigateur uniquement, et seul le code sera sauté : votre mot de passe reste "
              "demandé à chaque connexion. Changer de mot de passe annule immédiatement tous les "
              "appareils mémorisés. À éviter sur un poste partagé.",
        "en": "On this browser only, and only the code is skipped: your password is still required "
              "every time. Changing your password immediately cancels every remembered device. "
              "Avoid this on a shared computer.",
    },
    "totp.remembered_until": {
        "fr": "Appareil mémorisé — aucun code ne sera demandé ici avant le {date}.",
        "en": "Device remembered — no code will be asked here before {date}.",
    },
    "devices.section": {"fr": "Appareils de confiance", "en": "Trusted devices"},
    "devices.none": {
        "fr": "Aucun appareil mémorisé : le second facteur est demandé à chaque connexion.",
        "en": "No remembered device: the second factor is asked at every login.",
    },
    "devices.revoke_all": {"fr": "Révoquer tous les appareils", "en": "Revoke all devices"},
    "devices.revoked": {
        "fr": "{count} appareil(s) révoqué(s). Le code sera redemandé partout.",
        "en": "{count} device(s) revoked. The code will be asked everywhere again.",
    },
    "devices.disabled": {
        "fr": "Mémorisation d'appareil désactivée sur ce déploiement (ACA_TOTP_TRUST_DAYS=0).",
        "en": "Device remembering is disabled on this deployment (ACA_TOTP_TRUST_DAYS=0).",
    },

    # ── Onglet « Nouvel e-mail » (app_pages/1_inbox.py) ─────────────────────────────────────
    "inbox.demo_banner": {
        "fr": "**Mode démonstration** — modèle simulé, aucune clé d'API utilisée. "
              "Le graphe, lui, est le vrai (mêmes agents, même pause de validation). "
              "Toute écriture CRM est **matériellement bloquée**.",
        "en": "**Demo mode** — simulated model, no API key used. The graph itself is real "
              "(same agents, same validation pause). Any CRM write is **hard-blocked**.",
    },
    "inbox.demo_select": {"fr": "Charger un e-mail de démonstration",
                          "en": "Load a demo email"},
    "inbox.demo_select_placeholder": {
        "fr": "Choisir un exemple (démo, devis, spam, support, clause à risque…)",
        "en": "Choose an example (demo, quote, spam, support, risky clause…)",
    },
    "inbox.demo_load_button": {"fr": "Charger cet exemple", "en": "Load this example"},
    "inbox.form_header": {"fr": "Nouvel e-mail entrant", "en": "New incoming email"},
    "inbox.sender": {"fr": "Expéditeur", "en": "Sender"},
    "inbox.subject": {"fr": "Objet", "en": "Subject"},
    "inbox.body": {"fr": "Corps du message", "en": "Message body"},
    "inbox.attachments_label": {"fr": "Pièces jointes (optionnel)", "en": "Attachments (optional)"},
    "inbox.attachments_uploader": {
        "fr": "Importer un ou plusieurs documents (cahier des charges, devis, tableur...)",
        "en": "Upload one or more documents (spec sheet, quote, spreadsheet...)",
    },
    "inbox.attachments_from_gmail": {
        "fr": "Pièce(s) jointe(s) récupérée(s) automatiquement depuis Gmail.",
        "en": "Attachment(s) automatically retrieved from Gmail.",
    },
    "inbox.launch_button": {"fr": "Lancer l'analyse IA", "en": "Run AI analysis"},
    "inbox.result_header": {"fr": "Résultat de l'IA", "en": "AI result"},
    "inbox.clarification_reply_label": {"fr": "Votre réponse à l'agent", "en": "Your reply to the agent"},
    "inbox.clarification_reply_button": {"fr": "Répondre à l'agent", "en": "Reply to the agent"},
    "inbox.clarification_empty_error": {
        "fr": "Merci de saisir une réponse avant de continuer.",
        "en": "Please enter a reply before continuing.",
    },
    "inbox.spam_message": {"fr": "E-mail classé comme spam. Aucune action requise.",
                           "en": "Email classified as spam. No action required."},
    "inbox.support_message": {
        "fr": "E-mail classé SUPPORT : ce n'est pas un lead commercial, donc pas de proposition ni "
              "de fiche CRM. L'e-mail a été routé vers l'équipe support (alerte et/ou brouillon de "
              "transfert Gmail, selon la configuration).",
        "en": "Email classified as SUPPORT: not a sales lead, so no proposal or CRM record. "
              "The email was routed to the support team (alert and/or Gmail forward draft, "
              "depending on configuration).",
    },
    "inbox.autre_message": {
        "fr": "E-mail hors périmètre commercial (candidature, partenariat, question générale) — "
              "routé vers les RH (alerte et/ou brouillon de transfert Gmail, selon la "
              "configuration). Aucune fiche CRM n'est créée automatiquement.",
        "en": "Email outside the sales scope (application, partnership, general question) — "
              "routed to HR (alert and/or Gmail forward draft, depending on configuration). "
              "No CRM record is created automatically.",
    },
    "inbox.routing_detail_expander": {"fr": "Détail du routage", "en": "Routing detail"},
    "inbox.duplicate_warning": {
        "fr": "Cet expéditeur existe déjà dans le CRM. Vérifiez avant d'ajouter une nouvelle "
              "ligne (risque de doublon).",
        "en": "This sender already exists in the CRM. Check before adding a new row "
              "(possible duplicate).",
    },
    "inbox.risk_flags_message": {
        "fr": "Clause(s) contractuelle(s) à risque détectée(s) : {flags} — à faire relire par "
              "l'équipe juridique/la direction avant tout engagement.",
        "en": "Risky contractual clause(s) detected: {flags} — have legal/management review "
              "before any commitment.",
    },
    "inbox.knowledge_gap_message": {
        "fr": "Aucune réponse vérifiée (base de connaissances ni recherche web) pour ce besoin — "
              "la proposition ci-dessous reste volontairement prudente. Relisez avant validation.",
        "en": "No verified answer (knowledge base or web search) for this need — the proposal "
              "below is deliberately cautious. Review before validating.",
    },
    "inbox.injection_flags_message": {
        "fr": "Tentative(s) de manipulation de l'IA détectée(s) dans le message entrant : {flags}. "
              "Le contenu reçu essaie de donner des instructions à l'assistant — relisez "
              "la proposition avec méfiance et vérifiez qu'elle n'accorde aucune remise, "
              "exception ou engagement inhabituel.",
        "en": "AI manipulation attempt(s) detected in the incoming message: {flags}. "
              "The received content is trying to instruct the assistant — review the "
              "proposal with suspicion and check it doesn't grant any unusual discount, "
              "exception or commitment.",
    },
    "inbox.fiche_header": {"fr": "Fiche prospect", "en": "Prospect summary"},
    "inbox.company": {"fr": "Entreprise", "en": "Company"},
    "inbox.contact": {"fr": "Contact", "en": "Contact"},
    "inbox.urgency": {"fr": "Urgence", "en": "Urgency"},
    "inbox.main_need": {"fr": "Besoin principal", "en": "Main need"},
    "inbox.company_profile_caption": {"fr": "Profil entreprise (agent Enrichissement)",
                                      "en": "Company profile (Enrichment agent)"},
    "inbox.reasoning_expander": {"fr": "Raisonnement de l'équipe d'agents", "en": "Agent team reasoning"},
    "inbox.history_expander": {"fr": "Historique de ce lead", "en": "This lead's history"},
    "inbox.diff_caption": {"fr": "Différentiel du brouillon (dernière modification humaine)",
                           "en": "Draft diff (latest human edit)"},
    "inbox.draft_header": {"fr": "Proposition rédigée", "en": "Drafted proposal"},
    "inbox.draft_caption": {
        "fr": "Modifiable avant validation — la version ci-dessous est celle envoyée au CRM/Gmail.",
        "en": "Editable before validation — the version below is what's sent to the CRM/Gmail.",
    },
    "inbox.pdf_download_button": {"fr": "Télécharger en PDF", "en": "Download as PDF"},
    "inbox.validation_header": {"fr": "Validation humaine", "en": "Human validation"},
    "inbox.validation_caption": {
        "fr": "Si la recommandation de l'IA est correcte, validez pour envoyer dans le CRM.",
        "en": "If the AI's recommendation is correct, validate to send it to the CRM.",
    },
    "inbox.no_validate_permission": {
        "fr": "Votre rôle ne permet pas de valider un lead (écriture CRM). Contactez un "
              "administrateur.",
        "en": "Your role doesn't allow validating a lead (CRM write). Contact an administrator.",
    },
    "inbox.validate_button": {"fr": "Valider et ajouter au CRM", "en": "Validate and add to CRM"},
    "inbox.validation_error": {"fr": "Erreur technique lors de la validation",
                               "en": "Technical error during validation"},
    "inbox.reject_button": {"fr": "Rejeter (ne pas envoyer au CRM)", "en": "Reject (do not send to CRM)"},
    "inbox.rejected_info": {"fr": "Lead rejeté — non envoyé au CRM.", "en": "Lead rejected — not sent to CRM."},

    # ── §19 — envoi programmé et rappels (app_pages/1_inbox.py) ─────────────────────────────
    # Libellés écrits du point de vue de la personne : « Envoi de la réponse » plutôt que
    # « déclencher la tâche », « De quoi faut-il se souvenir ? » plutôt que « note ». Le
    # vocabulaire d'une interface est la signalétique de ceux qui s'y déplacent.
    "inbox.send_mode_label": {"fr": "Envoi de la réponse", "en": "Sending the reply"},
    "inbox.send_now": {"fr": "Brouillon Gmail seulement", "en": "Gmail draft only"},
    "inbox.send_scheduled": {"fr": "Programmer l'envoi", "en": "Schedule the send"},
    "inbox.send_mode_help": {
        "fr": "Par défaut, ACA prépare un brouillon dans le fil Gmail et vous l'envoyez vous-même. "
              "« Programmer l'envoi » expédie ce même brouillon à l'heure choisie — vous pouvez "
              "encore le corriger ou le supprimer dans Gmail d'ici là.",
        "en": "By default ACA prepares a draft in the Gmail thread and you send it yourself. "
              "\"Schedule the send\" sends that same draft at the chosen time — you can still "
              "edit or delete it in Gmail before then.",
    },
    "inbox.send_date": {"fr": "Date d'envoi", "en": "Send date"},
    "inbox.send_time": {"fr": "Heure d'envoi", "en": "Send time"},
    "inbox.send_past_warning": {
        "fr": "Cette date est déjà passée. Choisissez un moment à venir.",
        "en": "That date has already passed. Pick a future moment.",
    },
    "inbox.send_confirm": {
        "fr": "Le brouillon partira le {when}, sauf si vous le supprimez dans Gmail d'ici là.",
        "en": "The draft will go out on {when}, unless you delete it in Gmail before then.",
    },
    "inbox.validate_and_schedule_button": {
        "fr": "Valider et programmer l'envoi", "en": "Validate and schedule the send",
    },
    "inbox.send_scheduled_ok": {
        "fr": "Envoi programmé pour le {when}.", "en": "Send scheduled for {when}.",
    },
    "inbox.send_scheduled_no_draft": {
        "fr": "Le lead est validé, mais le brouillon Gmail n'a pas pu être créé : "
              "l'envoi n'a donc pas été programmé.",
        "en": "The lead is validated, but the Gmail draft could not be created, so no send "
              "was scheduled.",
    },
    "inbox.reminder_expander": {"fr": "Se faire rappeler ce lead", "en": "Remind me about this lead"},
    "inbox.reminder_caption": {
        "fr": "Une note datée pour vous et votre équipe. Rien n'est envoyé au prospect — c'est un "
              "rappel interne, pas une relance.",
        "en": "A dated note for you and your team. Nothing is sent to the prospect — this is an "
              "internal reminder, not a follow-up.",
    },
    "inbox.reminder_note": {"fr": "De quoi faut-il se souvenir ?", "en": "What should you remember?"},
    "inbox.reminder_placeholder": {
        "fr": "Rappeler le prix révisé avant la réunion",
        "en": "Mention the revised price before the meeting",
    },
    "inbox.reminder_date": {"fr": "Date du rappel", "en": "Reminder date"},
    "inbox.reminder_time": {"fr": "Heure du rappel", "en": "Reminder time"},
    "inbox.reminder_button": {"fr": "Créer le rappel", "en": "Create reminder"},
    "inbox.reminder_needs_note": {
        "fr": "Écrivez ce dont il faut se souvenir — un rappel vide n'apprend rien le jour venu.",
        "en": "Write what to remember — an empty reminder tells you nothing on the day.",
    },
    "inbox.reminder_past": {
        "fr": "Ce moment est déjà passé. Choisissez une date à venir.",
        "en": "That moment has already passed. Pick a future date.",
    },
    "inbox.reminder_ok": {"fr": "Rappel créé pour le {when}.", "en": "Reminder created for {when}."},

    # ── Onglet « Tableau de bord » (app_pages/2_dashboard.py) ───────────────────────────────
    "dashboard.caption": {
        "fr": "Calculé à partir de `analytics_store.py` — TOUTES les analyses (y compris SPAM/AUTRE/"
              "SUPPORT, jamais validées), indépendamment de l'onglet Sheets Leads qui ne reçoit que "
              "les leads commerciaux validés.",
        "en": "Computed from `analytics_store.py` — ALL analyses (including SPAM/OTHER/SUPPORT, "
              "never validated), independently of the Sheets Leads tab which only receives "
              "validated sales leads.",
    },
    "dashboard.period_label": {"fr": "Période", "en": "Period"},
    "dashboard.period_days": {"fr": "{d} jours", "en": "{d} days"},
    "dashboard.empty_state": {
        "fr": "Aucune analyse enregistrée sur cette période — le tableau de bord se remplit au fur "
              "et à mesure des e-mails classés (onglet « Nouvel e-mail » ou poller.py).",
        "en": "No analysis recorded for this period — the dashboard fills in as emails get "
              "classified (\"New email\" tab or poller.py).",
    },
    "dashboard.demo_link_button": {"fr": "Charger un exemple de démonstration",
                                   "en": "Load a demo example"},
    "dashboard.metric_classified": {"fr": "E-mails classés", "en": "Emails classified"},
    "dashboard.metric_conversion": {"fr": "Taux de validation", "en": "Validation rate"},
    "dashboard.metric_median_response": {"fr": "Temps de réponse médian", "en": "Median response time"},
    "dashboard.metric_edited": {"fr": "Brouillons édités avant validation", "en": "Drafts edited before validation"},
    "dashboard.metric_tokens": {"fr": "Tokens Groq / analyse (moyenne)", "en": "Groq tokens / analysis (average)"},
    "dashboard.chart_volume": {"fr": "Volume par catégorie", "en": "Volume by category"},
    "dashboard.chart_daily": {"fr": "Tendance quotidienne", "en": "Daily trend"},
    "dashboard.chart_no_trend": {"fr": "Pas assez de données pour une tendance.",
                                 "en": "Not enough data for a trend."},
    "dashboard.chart_funnel": {"fr": "Entonnoir de conversion", "en": "Conversion funnel"},
    "dashboard.response_times_expander": {"fr": "Détail des temps de réponse (leads validés)",
                                          "en": "Response time detail (validated leads)"},
    # §22 — comparaison, nouvelles répartitions et bascules graphique/tableau.
    "dashboard.compare_label": {"fr": "Comparer à la période précédente",
                                "en": "Compare with the previous period"},
    "dashboard.compare_help": {
        "fr": "Affiche l'écart avec la période de MÊME DURÉE qui précède immédiatement. "
              "Une flèche verte signale toujours une amélioration : pour le délai de réponse, "
              "c'est donc une baisse.",
        "en": "Shows the change against the immediately preceding window of the SAME LENGTH. "
              "A green arrow always means an improvement — so for response time, a decrease.",
    },
    "dashboard.compare_caption": {"fr": "Écarts calculés sur les {d} jours précédents.",
                                  "en": "Changes measured against the previous {d} days."},
    "dashboard.view_chart": {"fr": "Graphique", "en": "Chart"},
    "dashboard.view_table": {"fr": "Tableau", "en": "Table"},
    "dashboard.view_label": {"fr": "Affichage", "en": "View"},
    "dashboard.chart_buckets": {"fr": "Rapidité de réponse", "en": "Response speed"},
    "dashboard.chart_buckets_help": {
        "fr": "Répartition des leads validés par délai entre l'analyse et la décision humaine. "
              "Au-delà de 24 h, un prospect a généralement déjà sollicité un concurrent.",
        "en": "Validated leads by delay between analysis and the human decision. Past 24 h, a "
              "prospect has usually already contacted a competitor.",
    },
    "dashboard.chart_source": {"fr": "Origine des e-mails", "en": "Where emails come from"},
    "dashboard.chart_source_help": {
        "fr": "Réception automatique contre saisie manuelle. C'est la mesure d'adoption : un outil "
              "présenté comme automatique dont l'essentiel du volume est saisi à la main ne l'est pas.",
        "en": "Automatic intake versus manual entry. This is the adoption measure: a tool sold as "
              "automatic whose volume is mostly typed in by hand is not being adopted.",
    },
    "dashboard.chart_hours": {"fr": "Heures d'arrivée", "en": "Arrival hours"},
    "dashboard.chart_hours_help": {
        "fr": "Quand les e-mails arrivent réellement, heure par heure. À comparer avec la fenêtre "
              "de réception configurée dans Réglages.",
        "en": "When emails actually arrive, hour by hour. Compare with the intake window "
              "configured in Settings.",
    },
    "dashboard.chart_senders": {"fr": "Correspondants les plus actifs", "en": "Most active senders"},
    "dashboard.col_sender": {"fr": "Expéditeur", "en": "Sender"},
    "dashboard.col_emails": {"fr": "E-mails", "en": "Emails"},
    "dashboard.col_validated": {"fr": "Validés", "en": "Validated"},
    "dashboard.no_data": {"fr": "Aucune donnée sur cette période.",
                          "en": "No data for this period."},
    # Les valeurs stockées (`poller`, `gmail_import`, `manuel`) sont des identifiants techniques.
    # Les afficher telles quelles obligerait la personne à connaître le nom des composants pour lire
    # son propre tableau de bord — on nomme donc le geste, pas le module qui l'exécute.
    "dashboard.source_poller": {"fr": "Réception automatique", "en": "Automatic intake"},
    "dashboard.source_gmail_import": {"fr": "Import Gmail (à la demande)",
                                      "en": "Gmail import (on demand)"},
    "dashboard.source_manuel": {"fr": "Saisie manuelle", "en": "Typed in by hand"},
    "dashboard.source_inconnu": {"fr": "Origine inconnue", "en": "Unknown origin"},

    # ── Onglet « Historique » (app_pages/3_history.py) ──────────────────────────────────────
    "history.caption": {
        "fr": "Journal d'audit (`audit_log.py`, §12 item 2) — qui a validé quel lead, quand, et sous "
              "quelle classification. Ne couvre que les événements de validation (« Valider ») ; le "
              "Tableau de bord ci-dessus couvre lui *toutes* les classifications, y compris SPAM/AUTRE.",
        "en": "Audit log (`audit_log.py`) — who validated which lead, when, and under which "
              "classification. Covers only validation events (\"Validate\"); the Dashboard above "
              "covers *all* classifications, including SPAM/OTHER.",
    },
    "history.limit_label": {"fr": "Nombre d'entrées", "en": "Number of entries"},
    "history.search_label": {"fr": "Rechercher une exécution passée", "en": "Search a past run"},
    "history.search_placeholder": {
        "fr": "expéditeur, classification, validé par, ID...",
        "en": "sender, classification, validated by, ID...",
    },
    "history.empty_state": {
        "fr": "Aucun événement de validation pour le moment (ou aucun résultat pour cette recherche).",
        "en": "No validation event yet (or no results for this search).",
    },

    # ── Onglet « Journal d'activité » (app_pages/4_activity.py) ─────────────────────────────
    "activity.caption": {
        "fr": "Toutes les actions humaines de cette instance (`activity_log.py`) : connexions et "
              "**échecs de connexion**, validations et rejets, réglages, curation de la base de "
              "connaissances, gestion des comptes. Complète l'onglet « Historique », qui ne montre "
              "que les validations, et le « Tableau de bord », qui compte les analyses de l'IA.",
        "en": "All human actions on this instance (`activity_log.py`): logins and **failed "
              "logins**, validations and rejections, settings, knowledge-base curation, account "
              "management. Complements \"History\" (validations only) and \"Dashboard\" (AI "
              "analysis counts).",
    },
    "activity.period_label": {"fr": "Période", "en": "Period"},
    "activity.period_24h": {"fr": "24 h", "en": "24h"},
    "activity.period_days": {"fr": "{d} jours", "en": "{d} days"},
    "activity.empty_state": {
        "fr": "Aucune activité enregistrée sur cette période. Le journal se remplit dès la "
              "prochaine connexion ou validation — il ne contient rien d'antérieur à sa mise en "
              "place, ce qui est normal et non un signe de suppression.",
        "en": "No activity recorded for this period. The log fills in from the next login or "
              "validation onward — it holds nothing from before it was set up, which is normal, "
              "not a sign of deletion.",
    },
    "activity.per_person_header": {"fr": "Activité par personne", "en": "Activity by person"},
    "activity.audit_sheet_header": {"fr": "Fiche d'audit", "en": "Audit sheet"},
    "activity.events_detail_header": {"fr": "Détail des événements", "en": "Event detail"},
    "activity.export_csv_button": {"fr": "Exporter en CSV", "en": "Export as CSV"},
    "activity.integrity_expander": {"fr": "Intégrité du journal", "en": "Log integrity"},
    "activity.verify_now_button": {"fr": "Vérifier maintenant", "en": "Verify now"},

    # ── Onglet « Réglages » (app_pages/5_settings.py) ───────────────────────────────────────
    "settings.readonly_notice": {
        "fr": "Les réglages sont réservés au rôle « admin ». Vous pouvez consulter la configuration "
              "courante ci-dessous, mais pas la modifier.",
        "en": "Settings are restricted to the \"admin\" role. You can view the current "
              "configuration below, but not change it.",
    },
    "settings.save_button": {"fr": "Enregistrer les réglages", "en": "Save settings"},

    # ── §19 — réception automatique et tâches programmées ───────────────────────────────────
    "settings.intake_header": {"fr": "Réception automatique", "en": "Automatic intake"},
    "settings.intake_caption": {
        "fr": "Quand ACA relève votre boîte de réception. Hors de cette plage, aucun e-mail n'est "
              "lu ni analysé — utile pour éviter qu'une alerte parte la nuit ou le week-end. Les "
              "e-mails ne sont pas perdus : ils sont traités à la réouverture.",
        "en": "When ACA checks your inbox. Outside these hours no email is read or analysed — "
              "handy to avoid alerts firing at night or over the weekend. Nothing is lost: "
              "emails are processed when the window reopens.",
    },
    "settings.intake_status": {"fr": "État", "en": "Status"},
    "settings.intake_status_open": {"fr": "en marche", "en": "running"},
    "settings.intake_status_closed": {"fr": "en veille", "en": "idle"},
    "settings.intake_next": {"fr": "prochaine relève", "en": "next check"},
    "settings.intake_now": {"fr": "en continu", "en": "continuous"},
    "settings.intake_enabled": {"fr": "Activer la réception automatique", "en": "Enable automatic intake"},
    "settings.intake_enabled_help": {
        "fr": "Désactivée, ACA n'ouvre plus votre boîte de réception. Vous pouvez toujours "
              "importer un e-mail à la main depuis la barre latérale.",
        "en": "When off, ACA no longer opens your inbox. You can still import an email by hand "
              "from the sidebar.",
    },
    "settings.intake_days": {"fr": "Jours de réception", "en": "Days"},
    "settings.intake_days_help": {
        "fr": "Aucun jour coché = tous les jours.",
        "en": "No day selected = every day.",
    },
    "settings.intake_start": {"fr": "À partir de", "en": "From"},
    "settings.intake_end": {"fr": "Jusqu'à", "en": "Until"},
    "settings.intake_every": {"fr": "Vérifier toutes les (min)", "en": "Check every (min)"},
    "settings.intake_every_help": {
        "fr": "Un délai court repère les e-mails plus vite mais interroge Gmail plus souvent. "
              "Cinq minutes conviennent à la plupart des équipes.",
        "en": "A short delay spots emails sooner but queries Gmail more often. Five minutes "
              "suits most teams.",
    },
    "settings.intake_save": {"fr": "Enregistrer la réception", "en": "Save intake settings"},
    "settings.intake_saved": {
        "fr": "Réception mise à jour — prise en compte au prochain cycle, sans redémarrage.",
        "en": "Intake updated — applied on the next cycle, no restart needed.",
    },
    "settings.tasks_header": {"fr": "Tâches programmées", "en": "Scheduled tasks"},
    "settings.tasks_caption": {
        "fr": "Envois différés et rappels à venir. Annuler un envoi ici l'empêche de partir ; le "
              "brouillon reste dans Gmail.",
        "en": "Upcoming scheduled sends and reminders. Cancelling a send here stops it going "
              "out; the draft stays in Gmail.",
    },
    "settings.tasks_empty": {
        "fr": "Rien de programmé pour le moment.", "en": "Nothing scheduled right now.",
    },
    "settings.tasks_by": {"fr": "Programmé par {who}", "en": "Scheduled by {who}"},
    "settings.tasks_cancel": {"fr": "Annuler", "en": "Cancel"},
    "settings.appearance_header": {"fr": "Apparence et identité visuelle", "en": "Appearance and branding"},
    "settings.apply_appearance_button": {"fr": "Appliquer l'identité visuelle", "en": "Apply branding"},
    "settings.accounts_header": {"fr": "Comptes et rôles", "en": "Accounts and roles"},
    "settings.create_account_button": {"fr": "Créer le compte", "en": "Create account"},

    # ── §20 — Relectures (app_pages/6_reviews.py) ────────────────────────────────────────────
    "nav.reviews": {"fr": "Relectures", "en": "Reviews"},
    "hero.reviews_pill": {"fr": "{n} relecture(s) à traiter", "en": "{n} review(s) to handle"},
    "sidebar.reviews_header": {"fr": "Relectures demandées ({n})", "en": "Reviews requested ({n})"},
    "sidebar.reviews_toast": {"fr": "{who} vous demande de relire {n} e-mail(s)",
                              "en": "{who} asks you to review {n} email(s)"},
    "sidebar.reviews_open": {"fr": "Voir les relectures", "en": "Open reviews"},
    "reviews.caption": {
        "fr": "Transmettez à un administrateur les e-mails que vous ne souhaitez pas trancher "
              "seul(e) — plusieurs d'un seul geste. Rien n'est envoyé au prospect.",
        "en": "Hand an administrator the emails you would rather not decide alone — several in a "
              "single gesture. Nothing is sent to the prospect.",
    },
    "reviews.received_header": {"fr": "À relire ({n})", "en": "To review ({n})"},
    "reviews.received_sub": {
        "fr": "Ce que vos collègues vous ont transmis. Tant que rien n'est répondu, la demande "
              "reste ouverte de leur côté.",
        "en": "What your colleagues sent you. Until you answer, the request stays open on their "
              "side.",
    },
    "reviews.received_empty_title": {"fr": "Rien à relire", "en": "Nothing to review"},
    "reviews.received_empty_body": {
        "fr": "Aucun collègue n'attend votre avis pour le moment. Les demandes apparaissent ici "
              "dès leur envoi, et vous êtes prévenu(e) à la connexion.",
        "en": "Nobody is waiting for your opinion right now. Requests show up here as soon as they "
              "are sent, and you are notified when you sign in.",
    },
    "reviews.chip_from": {"fr": "De {who}", "en": "From {who}"},
    "reviews.chip_count": {"fr": "{n} e-mail(s)", "en": "{n} email(s)"},
    "reviews.open_lead": {"fr": "Ouvrir le lead", "en": "Open lead"},
    "reviews.open_failed": {
        "fr": "Ce lead n'est plus consultable (probablement purgé par la rétention)",
        "en": "This lead can no longer be opened (most likely purged by retention)",
    },
    "reviews.resolve": {"fr": "Traiter", "en": "Resolve"},
    "reviews.dismiss": {"fr": "Écarter", "en": "Dismiss"},
    "reviews.answer_label": {"fr": "Réponse au demandeur", "en": "Answer to the requester"},
    "reviews.answer_placeholder": {"fr": "Facultatif — ce que vous en pensez",
                                   "en": "Optional — what you think"},
    "reviews.answer_batch_placeholder": {"fr": "Réponse commune à tout le lot",
                                         "en": "One answer for the whole batch"},
    "reviews.resolve_batch": {"fr": "Traiter les {n} d'un coup", "en": "Resolve all {n} at once"},
    "reviews.no_send_permission": {
        "fr": "Votre rôle est en lecture seule : vous pouvez consulter, pas transmettre du travail.",
        "en": "Your role is read-only: you can consult, not hand work to others.",
    },
    "reviews.send_header": {"fr": "Transmettre des e-mails", "en": "Hand over emails"},
    "reviews.send_sub": {
        "fr": "Sélectionnez une ou plusieurs lignes, puis choisissez qui doit les regarder.",
        "en": "Select one or more rows, then choose who should look at them.",
    },
    "reviews.source_label": {"fr": "Parmi", "en": "From"},
    "reviews.source_queue": {"fr": "File d'attente", "en": "Pending queue"},
    "reviews.source_recent": {"fr": "E-mails des 30 derniers jours", "en": "Last 30 days"},
    "reviews.no_candidates_title": {"fr": "Aucun e-mail à transmettre",
                                    "en": "No email to hand over"},
    "reviews.no_candidates_body": {
        "fr": "La file d'attente est vide. Lancez une analyse ou attendez la prochaine relève de "
              "la boîte de réception.",
        "en": "The queue is empty. Run an analysis or wait for the next inbox check.",
    },
    "reviews.pick_hint": {
        "fr": "Cochez les lignes à transmettre (plusieurs possibles).",
        "en": "Tick the rows to hand over (several allowed).",
    },
    "reviews.recipient_label": {"fr": "Destinataire", "en": "Recipient"},
    "reviews.recipient_all_admins": {"fr": "Tous les administrateurs", "en": "All administrators"},
    "reviews.note_label": {"fr": "Pourquoi cette relecture ?", "en": "Why this review?"},
    "reviews.note_placeholder": {
        "fr": "Ex. : clause de pénalité inhabituelle, je préfère un second avis avant de valider.",
        "en": "E.g. unusual penalty clause, I'd rather get a second opinion before validating.",
    },
    "reviews.priority_label": {"fr": "Marquer comme prioritaire", "en": "Mark as priority"},
    "reviews.priority_help": {
        "fr": "Remonte la demande en tête de la file du destinataire.",
        "en": "Moves the request to the top of the recipient's queue.",
    },
    "reviews.send_button": {"fr": "Transmettre ({n})", "en": "Hand over ({n})"},
    "reviews.none_picked": {
        "fr": "Aucune ligne sélectionnée — cochez au moins un e-mail.",
        "en": "No row selected — tick at least one email.",
    },
    "reviews.no_admin_account": {
        "fr": "Aucun compte administrateur n'existe : personne ne verrait cette demande. Créez un "
              "compte depuis « Réglages → Comptes et rôles ».",
        "en": "No administrator account exists: nobody would see this request. Create one from "
              "\"Settings → Accounts and roles\".",
    },
    "reviews.sent_ok": {"fr": "{n} e-mail(s) transmis à {who}.",
                        "en": "{n} email(s) handed over to {who}."},
    "reviews.sent_header": {"fr": "Ce que vous avez transmis", "en": "What you handed over"},
    "reviews.sent_sub": {
        "fr": "Pour savoir ce qui a été vu, ce qui a été tranché, et ce qui attend encore.",
        "en": "So you know what was seen, what was decided, and what is still waiting.",
    },
    "reviews.sent_empty": {"fr": "Vous n'avez encore transmis aucun e-mail.",
                           "en": "You have not handed over any email yet."},

    # ── §20 — Rapports (app_pages/7_reports.py) ──────────────────────────────────────────────
    "nav.reports": {"fr": "Rapports", "en": "Reports"},
    "reports.no_permission": {
        "fr": "Votre rôle ne donne pas accès aux rapports d'activité.",
        "en": "Your role does not give access to activity reports.",
    },
    "reports.caption": {
        "fr": "Composez un rapport PDF aux couleurs de la maison : choisissez la période, ce qu'il "
              "contient, et jusqu'aux colonnes du détail. Chaque section explique ce qu'elle "
              "montre.",
        "en": "Build a PDF report in your own colours: choose the period, what goes in it, down to "
              "the columns of the detail table. Every section explains what it shows.",
    },
    "reports.preset_label": {"fr": "Préréglage enregistré", "en": "Saved preset"},
    "reports.preset_load": {"fr": "Charger", "en": "Load"},
    "reports.preset_delete": {"fr": "Supprimer", "en": "Delete"},
    "reports.period_header": {"fr": "Période", "en": "Period"},
    "reports.period_sub": {
        "fr": "La comparaison porte sur la période de même durée qui précède immédiatement.",
        "en": "The comparison uses the immediately preceding window of the same length.",
    },
    "reports.period_label": {"fr": "Sur quelle période ?", "en": "Which period?"},
    "reports.period_last_month": {"fr": "Mois dernier", "en": "Last month"},
    "reports.period_this_month": {"fr": "Ce mois-ci", "en": "This month"},
    "reports.period_last_days": {"fr": "{d} derniers jours", "en": "Last {d} days"},
    "reports.period_custom": {"fr": "Dates précises", "en": "Exact dates"},
    "reports.from_date": {"fr": "Du", "en": "From"},
    "reports.to_date": {"fr": "Au (inclus)", "en": "To (included)"},
    "reports.period_invalid": {
        "fr": "La date de fin doit être postérieure à la date de début.",
        "en": "The end date must be after the start date.",
    },
    "reports.readout_period": {"fr": "Période", "en": "Period"},
    "reports.readout_compared": {"fr": "Comparée à", "en": "Compared to"},
    "reports.content_header": {"fr": "Contenu du rapport", "en": "Report contents"},
    "reports.content_sub": {
        "fr": "Cochez ce qui doit figurer dans le document. Survolez un intitulé pour savoir ce "
              "qu'il apporte.",
        "en": "Tick what belongs in the document. Hover a label to see what it brings.",
    },
    "reports.no_section": {
        "fr": "Aucune section sélectionnée — le rapport serait vide.",
        "en": "No section selected — the report would be empty.",
    },
    "reports.email_options_header": {"fr": "Détail des e-mails : colonnes et filtres",
                                     "en": "Email detail: columns and filters"},
    "reports.email_options_sub": {
        "fr": "C'est ici qu'on obtient « la catégorie et l'expéditeur seulement », ou au contraire "
              "le détail complet.",
        "en": "This is where you get \"category and sender only\", or the full detail instead.",
    },
    "reports.columns_label": {"fr": "Colonnes à afficher", "en": "Columns to show"},
    "reports.filter_categories": {"fr": "Limiter aux catégories", "en": "Limit to categories"},
    "reports.filter_sender": {"fr": "Expéditeur contenant", "en": "Sender containing"},
    "reports.filter_validated": {"fr": "Leads validés uniquement", "en": "Validated leads only"},
    "reports.max_rows": {"fr": "Lignes maximum", "en": "Maximum rows"},
    "reports.max_rows_help": {
        "fr": "Un rapport n'est pas un export de base : au-delà de quelques centaines de lignes, "
              "il n'est plus lu.",
        "en": "A report is not a database dump: past a few hundred rows nobody reads it.",
    },
    "reports.presentation_header": {"fr": "Présentation", "en": "Presentation"},
    "reports.title_label": {"fr": "Titre du document", "en": "Document title"},
    "reports.title_default": {"fr": "Rapport d'activité", "en": "Activity report"},
    "reports.note_label": {"fr": "Note de contexte", "en": "Context note"},
    "reports.note_placeholder": {
        "fr": "Ex. : période marquée par la campagne de rentrée et deux semaines de congés.",
        "en": "E.g. period marked by the back-to-school campaign and two weeks of leave.",
    },
    "reports.note_help": {
        "fr": "Figure sur la couverture. Un chiffre inhabituel s'explique mieux sur le document "
              "que trois semaines plus tard, de mémoire, en réunion.",
        "en": "Printed on the cover. An unusual figure is better explained on the document than "
              "three weeks later from memory in a meeting.",
    },
    "reports.compare_label": {"fr": "Comparer à la période précédente",
                              "en": "Compare with the previous period"},
    "reports.compare_help": {
        "fr": "Désactivé, le document présente les chiffres bruts sans aucun écart — plus honnête "
              "qu'une comparaison à une période vide.",
        "en": "Turned off, the document shows raw figures with no deltas — more honest than "
              "comparing against an empty period.",
    },
    "reports.build_button": {"fr": "Générer le rapport", "en": "Build the report"},
    "reports.building": {"fr": "Collecte des données et mise en page…",
                         "en": "Collecting data and laying out…"},
    "reports.build_failed": {
        "fr": "Le rendu PDF a échoué. Le détail technique est consigné côté serveur.",
        "en": "PDF rendering failed. The technical detail is logged server-side.",
    },
    "reports.preset_save": {"fr": "Enregistrer ce réglage", "en": "Save this setup"},
    "reports.preset_name": {"fr": "Nom du préréglage", "en": "Preset name"},
    "reports.preset_save_button": {"fr": "Enregistrer", "en": "Save"},
    "reports.preset_saved": {"fr": "Préréglage « {name} » enregistré.",
                             "en": "Preset \"{name}\" saved."},
    "reports.result_header": {"fr": "Aperçu", "en": "Preview"},
    "reports.result_sub": {
        "fr": "Ce que contiendra le PDF, section par section — avant de le télécharger.",
        "en": "What the PDF will contain, section by section — before you download it.",
    },
    "reports.block_empty": {"fr": "Aucune donnée sur cette période.",
                            "en": "No data for this period."},
    "reports.table_truncated": {
        "fr": "Aperçu limité à 50 lignes sur {n} — le PDF les contient toutes.",
        "en": "Preview limited to 50 of {n} rows — the PDF has them all.",
    },
    "reports.download_button": {"fr": "Télécharger le PDF", "en": "Download the PDF"},
    "reports.archive_header": {"fr": "Rapports mensuels", "en": "Monthly reports"},
    "reports.archive_sub": {
        "fr": "Produits automatiquement à chaque fin de mois par le planificateur.",
        "en": "Produced automatically at the end of each month by the scheduler.",
    },
    "reports.archive_admin_only": {
        "fr": "Les rapports mensuels contiennent l'activité nominative de chaque compte : ils sont "
              "réservés aux administrateurs, comme le journal d'activité.",
        "en": "Monthly reports contain each account's named activity: they are restricted to "
              "administrators, like the activity log.",
    },
    "reports.archive_empty_title": {"fr": "Aucun rapport mensuel", "en": "No monthly report"},
    "reports.archive_empty_body": {
        "fr": "Le premier sera écrit dans {dir} au prochain passage du planificateur, une fois un "
              "mois civil entièrement écoulé.",
        "en": "The first one will be written to {dir} on the scheduler's next run, once a full "
              "calendar month has elapsed.",
    },
    "reports.archive_unreadable": {"fr": "Fichier illisible", "en": "Unreadable file"},
    "reports.archive_hint": {
        "fr": "Répertoire : {dir} (variable ACA_REPORT_DIR). Cadence : ACA_SCHEDULE_REPORT_HOURS.",
        "en": "Directory: {dir} (ACA_REPORT_DIR variable). Cadence: ACA_SCHEDULE_REPORT_HOURS.",
    },
}


def translate(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Chaîne traduite pour `key`, dans `lang` (repli sur `DEFAULT_LANGUAGE` si la langue ou la clé
    sont inconnues). `kwargs` alimente `str.format(...)` pour les chaînes avec un espace réservé
    (ex. `{n}`, `{seconds}`).

    Une clé absente renvoie la clé elle-même plutôt que de lever : un texte anglais manquant doit
    rester un bug visible et sans conséquence (`"missing.key"` affiché tel quel à l'écran), jamais
    un écran qui plante.
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text

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
    "sidebar.mark_seen": {"fr": "Marquer comme vu", "en": "Mark as seen"},
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
    "settings.appearance_header": {"fr": "Apparence et identité visuelle", "en": "Appearance and branding"},
    "settings.apply_appearance_button": {"fr": "Appliquer l'identité visuelle", "en": "Apply branding"},
    "settings.accounts_header": {"fr": "Comptes et rôles", "en": "Accounts and roles"},
    "settings.create_account_button": {"fr": "Créer le compte", "en": "Create account"},
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

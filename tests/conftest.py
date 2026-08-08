"""
Configuration pytest partagée.

IMPORTANT — isolation d'environnement : ce module s'exécute AVANT l'import de tout module `aca.*`
par les fichiers de test. Les modules du projet appellent `load_dotenv()` (qui, par défaut,
n'écrase PAS une variable déjà présente dans l'environnement) et certains figent leur configuration
au moment de l'import (`DATABASE_URL` dans app.py, `DB_PATH` dans les modules de stockage,
`CALENDLY_URL`...). En préremplissant ici ces variables avec des valeurs vides/factices, on
garantit que la suite de tests :
- ne se connecte JAMAIS au vrai Supabase (DATABASE_URL vide → SqliteSaver local temporaire) ;
- ne touche JAMAIS le vrai Google Sheets / Gmail / Tavily / HubSpot / Slack (clés vides →
  chemins de repli gracieux) ;
- n'écrit JAMAIS dans les vraies bases locales `data/*.sqlite` (chemins redirigés vers un
  répertoire temporaire).
Aucun test ne fait d'appel réseau : les LLM sont remplacés par des faux (FakeLLM ci-dessous).
"""
import os
import tempfile

# ── Neutralisation de l'environnement AVANT tout import `aca.*` ──────────────────────────────
_TMP_DIR = tempfile.mkdtemp(prefix="aca-tests-")

_ENV_OVERRIDES = {
    "DATABASE_URL": "",                      # → SqliteSaver, jamais Supabase
    "GOOGLE_SERVICE_ACCOUNT_FILE": "",       # → sheets._open_spreadsheet() renvoie None
    "GOOGLE_SHEETS_ID": "",
    "GOOGLE_API_KEY": "",                    # → RAG sémantique se replie sur les mots-clés
    "TAVILY_API_KEY": "",                    # → enrichment/veille renvoient ""
    "SLACK_WEBHOOK_URL": "",                 # → notify.send() renvoie False
    "NOTIFY_EMAIL": "",
    "SUPPORT_EMAIL": "",
    "SUPPORT_SLACK_WEBHOOK_URL": "",
    "HR_EMAIL": "",
    "HR_SLACK_WEBHOOK_URL": "",
    # §16.1.2 — le SEUL canal sortant qui avait été oublié ici, et le plus coûteux à oublier :
    # `webhook.emit()` est appelé depuis les nœuds du graphe, donc depuis presque tous les tests
    # d'intégration. Sans ces deux lignes, `ACA_WEBHOOK_URL` du `.env` réel restait actif pendant
    # la suite et chaque exécution de pytest expédiait de VRAIS événements à l'instance n8n de
    # production — constaté le 2026-07-29 : une rafale de 10 exécutions `analysis.paused` portant
    # de faux prospects (« jean@entreprise.fr ») dans le journal n8n Cloud. Inoffensif tant que le
    # workflow s'arrêtait à l'alerte ; depuis qu'il porte la moitié « validation », le même
    # incident enverrait des e-mails d'approbation et laisserait autant d'exécutions en attente
    # pour toujours. `test_webhook.py`/`test_api_n8n.py` posent leur propre valeur par monkeypatch
    # et ne sont donc pas affectés.
    "ACA_WEBHOOK_URL": "",                   # → webhook.emit() no-op silencieux, comme notify.send()
    "ACA_WEBHOOK_SECRET": "",
    "HUBSPOT_ACCESS_TOKEN": "",              # → hubspot.is_enabled() False
    "STRIPE_API_KEY": "",                    # → billing.is_enabled() False
    "CALENDLY_URL": "",                      # → pas de lien ajouté par défaut (test dédié le simule)
    "LANGCHAIN_TRACING_V2": "false",         # → pas de traces LangSmith pendant les tests
    "ACA_ORG_ID": "default",                 # → fondation multi-tenant (§12 item 3) : tenant déterministe en test
    # ── Sécurité (§15) : tout neutralisé par défaut, chaque test active ce qu'il vérifie ──
    "ACA_ENV": "development",                # → prod_check.enforce() est un no-op (aucune garde imposée)
    "ACA_API_KEY": "",                       # → require_api_key laisse passer (mode dev), comme avant §15
    "ACA_METRICS_TOKEN": "",                 # → /metrics reste ouvert en test
    "ACA_UI_PASSWORD": "",                   # → gate Streamlit désactivé
    "ACA_RATE_LIMIT": "0",                   # → limite de débit désactivée (sinon les tests se limitent eux-mêmes)
    "ACA_AUDIT_HMAC_KEY": "",                # → chaînage d'audit en SHA-256 simple, déterministe en test
    "ACA_ENABLE_DOCS": "",
    "ACA_USERS_DB": os.path.join(_TMP_DIR, "users.sqlite"),
    "ACA_CHECKPOINT_DB": os.path.join(_TMP_DIR, "checkpoints.sqlite"),
    "ACA_QUEUE_DB": os.path.join(_TMP_DIR, "queue.sqlite"),
    "ACA_ANALYTICS_DB": os.path.join(_TMP_DIR, "analytics.sqlite"),
    "ACA_AUDIT_DB": os.path.join(_TMP_DIR, "audit.sqlite"),
    # §17 — journal d'activité. Sans cette ligne, la suite écrirait dans le VRAI
    # `data/activity.sqlite` : des connexions et des validations fictives viendraient se mêler aux
    # traces réelles, dans le journal même dont l'intérêt est d'être digne de foi.
    "ACA_ACTIVITY_DB": os.path.join(_TMP_DIR, "activity.sqlite"),
    "ACA_FOLLOWUP_DB": os.path.join(_TMP_DIR, "followup.sqlite"),
    "ACA_CONFIG_DB": os.path.join(_TMP_DIR, "config.sqlite"),
    "ACA_SCHEDULE_DB": os.path.join(_TMP_DIR, "schedule.sqlite"),
    # §19 : sans cette redirection, un test qui programme un envoi écrirait dans le vrai
    # `data/tasks.sqlite` — et le planificateur de la machine de développement finirait par
    # exécuter une tâche née d'un test, c'est-à-dire par envoyer un e-mail pour de bon.
    "ACA_TASK_DB": os.path.join(_TMP_DIR, "tasks.sqlite"),
    # §20 : même raison que `ACA_TASK_DB` ci-dessus. Sans cette redirection, un test qui crée une
    # demande de relecture écrirait dans le vrai `data/reviews.sqlite`, et un administrateur
    # verrait apparaître à sa connexion des demandes fictives portant de faux prospects — dans
    # l'écran même dont l'intérêt est de ne montrer que du travail réel.
    "ACA_REVIEW_DB": os.path.join(_TMP_DIR, "reviews.sqlite"),
    # §24 : redirigée pour la même raison que les précédentes, avec un enjeu qui lui est propre —
    # une ligne écrite ici autorise un navigateur à SAUTER le second facteur. Un test qui en
    # créerait une dans la vraie base affaiblirait l'authentification de l'installation réelle,
    # ce qu'aucune suite ne doit pouvoir faire par effet de bord.
    "ACA_DEVICE_TRUST_DB": os.path.join(_TMP_DIR, "device_trust.sqlite"),
    # Répertoire des rapports mensuels (§20). Redirigé pour la même raison que les bases : le
    # travail planifié `report` écrit un PDF sur disque, et une exécution de la suite ne doit pas
    # déposer de document dans `data/reports/` — ni, pire, en écraser un vrai.
    "ACA_REPORT_DIR": os.path.join(_TMP_DIR, "reports"),
    # §29 — le même piège que `ACA_WEBHOOK_URL` ci-dessus, sous une autre forme : `branding.py` lit
    # `BRAND_NAME` dynamiquement depuis l'environnement (jamais figé à l'import, par contrat), donc
    # une vraie personnalisation posée dans le `.env` réel de la machine de développement (ici,
    # `BRAND_NAME=acami`, §29) fuiterait dans `branding.resolve()` dès qu'un module `aca.*` important
    # `load_dotenv()` (app.py, sheets.py...) s'exécute pendant la suite — faisant échouer
    # `test_customised_tokens_ne_liste_que_les_ecarts` selon l'ORDRE de collecte des tests, un défaut
    # découvert précisément ainsi (passait isolé, échouait dans la suite complète).
    "BRAND_NAME": "",
}
os.environ.update(_ENV_OVERRIDES)


class FakeLLM:
    """
    Faux LLM minimal : `invoke(messages)` renvoie un objet avec `.content`. `reply` peut être une
    chaîne fixe ou une fonction (messages -> str) pour router la réponse selon le prompt (utile
    quand plusieurs nœuds partagent la même factory, ex. fast_llm). Enregistre le dernier appel
    dans `last_messages` pour permettre d'inspecter le prompt réellement envoyé.
    """

    def __init__(self, reply):
        self.reply = reply
        self.last_messages = None
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        content = self.reply(messages) if callable(self.reply) else self.reply

        class _Response:
            pass

        response = _Response()
        response.content = content
        return response

    def with_structured_output(self, schema):
        """Simule `ChatGroq.with_structured_output(schema)` : parse la réponse JSON du faux LLM en
        une instance du schéma Pydantic donné, comme le ferait le tool-calling réel de Groq."""
        return _StructuredFakeLLM(self, schema)


class _StructuredFakeLLM:
    def __init__(self, fake_llm, schema):
        self._fake_llm = fake_llm
        self._schema = schema

    def invoke(self, messages):
        import json

        response = self._fake_llm.invoke(messages)
        return self._schema(**json.loads(response.content))


class ExplodingLLM(FakeLLM):
    """Faux LLM qui échoue s'il est appelé — pour vérifier qu'un chemin n'appelle PAS le LLM."""

    def __init__(self):
        super().__init__(reply=None)

    def invoke(self, messages):
        raise AssertionError("Le LLM ne devait pas être appelé sur ce chemin.")

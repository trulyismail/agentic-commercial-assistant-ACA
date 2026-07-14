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
    "HUBSPOT_ACCESS_TOKEN": "",              # → hubspot.is_enabled() False
    "CALENDLY_URL": "",                      # → pas de lien ajouté par défaut (test dédié le simule)
    "LANGCHAIN_TRACING_V2": "false",         # → pas de traces LangSmith pendant les tests
    "ACA_CHECKPOINT_DB": os.path.join(_TMP_DIR, "checkpoints.sqlite"),
    "ACA_QUEUE_DB": os.path.join(_TMP_DIR, "queue.sqlite"),
    "ACA_ANALYTICS_DB": os.path.join(_TMP_DIR, "analytics.sqlite"),
    "ACA_AUDIT_DB": os.path.join(_TMP_DIR, "audit.sqlite"),
    "ACA_FOLLOWUP_DB": os.path.join(_TMP_DIR, "followup.sqlite"),
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


class ExplodingLLM(FakeLLM):
    """Faux LLM qui échoue s'il est appelé — pour vérifier qu'un chemin n'appelle PAS le LLM."""

    def __init__(self):
        super().__init__(reply=None)

    def invoke(self, messages):
        raise AssertionError("Le LLM ne devait pas être appelé sur ce chemin.")

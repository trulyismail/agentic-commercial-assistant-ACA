"""
Mode démonstration (§16.3 de docs/ACAM_roadmap.md) — faire tourner ACA **sans aucune clé d'API**.

Le problème qu'il résout : jusqu'ici, essayer ce projet exigeait cinq comptes externes (Groq,
Gemini, Tavily, un compte de service Google, un client OAuth Gmail). Une entreprise qui l'évalue
pouvait donc *lire* le code, jamais l'*essayer*. C'est la différence entre « dépôt intéressant » et
« je viens de le faire tourner ».

`ACA_DEMO_MODE=1` remplace les trois fabriques de LLM par un modèle factice déterministe et
**interdit matériellement toute écriture réelle**. Le graphe, lui, est le vrai : mêmes nœuds, même
superviseur, même auto-critique, même pause de validation humaine. Ce qui est simulé, c'est
uniquement ce qui coûterait une clé d'API.

⚠️ Deux garanties, dans cet ordre :
1. **Aucune écriture réelle possible.** `guard_write()` lève si quoi que ce soit tente d'écrire dans
   Sheets, HubSpot ou Gmail. Une démo qui pollue le CRM d'un prospect serait pire que pas de démo —
   d'où un échec bruyant plutôt qu'un no-op silencieux (le seul endroit du projet où l'on ne dégrade
   PAS gracieusement, et c'est délibéré).
2. **Jamais activable par accident.** La variable est lue dynamiquement et vaut faux par défaut.

À ne pas confondre avec le `FakeLLM` de `tests/conftest.py` : celui-ci vit dans l'arbre de tests,
n'est importable que par les tests, et `tests/` est exclu de l'image Docker. Ce module-ci est du
code d'exécution, livré, et destiné à une personne qui regarde l'écran.
"""
import os

# Les six e-mails de démonstration, repris du bloc `__main__` de app.py (où ils ne servaient qu'en
# ligne de commande) pour être enfin utilisables depuis l'interface. Ils couvrent volontairement
# tout l'éventail : les deux catégories commerciales, un SPAM, un AUTRE, un SUPPORT, et un cas
# piégeux porteur d'une clause de responsabilité illimitée et d'une question hors FAQ.
#
# ⚠️ Nuance constatée en exécutant réellement la démonstration, pas supposée : en mode
# démonstration ce 6e cas déclenche bien `risk_flags`, mais **pas** `knowledge_gap`. La raison est
# structurelle — `connaissance_node` renvoie ici le `DEMO_FAQ_CONTEXT` ci-dessous, jamais vide, donc
# le garde-fou déterministe n'appelle jamais `veille_node`, seul endroit où `knowledge_gap` est posé.
# Avec de vraies clés et une vraie FAQ, la question sur le mainframe COBOL le déclenche bel et bien.
# Simuler le drapeau ici serait pire que de ne pas l'avoir : une démonstration doit montrer ce que
# le système fait, pas ce qu'on aimerait qu'il montre.
DEMO_EMAILS = [
    {
        "label": "Demande de démo — startup",
        "sender": "alice.martin@startup-tech.fr",
        "subject": "Demande de démonstration",
        "body": (
            "Bonjour, je suis directrice des opérations chez Startup Tech. "
            "Nous cherchons un outil pour automatiser notre gestion commerciale. "
            "Serait-il possible d'organiser une démo la semaine prochaine ? "
            "Nous sommes disponibles mardi ou jeudi après-midi."
        ),
    },
    {
        "label": "Devis — 50 licences",
        "sender": "bob.dupont@pme-industrie.com",
        "subject": "Demande de devis — 50 licences",
        "body": "Bonjour, pouvez-vous m'envoyer un devis pour 50 licences Enterprise ? Merci.",
    },
    {
        "label": "SPAM (court-circuité)",
        "sender": "promo@spammer.net",
        "subject": "Gagnez 1000€ maintenant !",
        "body": "Cliquez ici pour récupérer votre cadeau. Offre limitée !",
    },
    {
        "label": "Candidature (routée RH)",
        "sender": "jean.candidat@gmail.com",
        "subject": "Candidature — Stage développeur",
        "body": "Bonjour, je vous envoie ma candidature spontanée pour un stage. Mon CV est en pièce jointe.",
    },
    {
        "label": "Support (routé équipe support)",
        "sender": "client.existant@pme-industrie.com",
        "subject": "Problème de connexion à la plateforme",
        "body": "Bonjour, je n'arrive plus à me connecter depuis ce matin, erreur 500. Pouvez-vous m'aider ?",
    },
    {
        "label": "⚠️ Clause à risque + question hors FAQ",
        "sender": "achats@grand-compte-banque.fr",
        "subject": "Cahier des charges — intégration critique",
        "body": (
            "Bonjour, notre cahier des charges impose une responsabilité illimitée du "
            "prestataire en cas de manquement, ainsi que des pénalités de retard. Par "
            "ailleurs, votre solution est-elle compatible avec notre mainframe COBOL "
            "propriétaire des années 1980 ?"
        ),
    },
]

# Base de connaissances factice : sans clé Gemini ni Google Sheets, `connaissance_node` n'a rien à
# lire. Deux entrées suffisent à rendre la démonstration crédible (le Stratège cite un vrai tarif).
DEMO_FAQ_CONTEXT = (
    "- Q: Quels sont vos tarifs ?\n"
    "  R: L'offre Enterprise est à 49 €/utilisateur/mois, dégressive au-delà de 25 licences.\n"
    "- Q: Proposez-vous une démonstration ?\n"
    "  R: Oui, une démonstration de 30 minutes est possible sous 48 h."
)


def is_enabled() -> bool:
    """
    Lue DYNAMIQUEMENT, jamais figée à l'import — même leçon que `DATABASE_URL` dans
    `vector_store.py`, gelé à vide pendant des semaines à cause de l'ordre des imports.
    """
    return os.getenv("ACA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "oui"}


def guard_write(operation: str) -> None:
    """
    Interdit une écriture réelle en mode démonstration. **Lève au lieu de dégrader**.

    C'est le seul endroit du projet qui n'applique pas la dégradation gracieuse, et c'est voulu :
    « absent = ignoré » est le bon défaut pour une fonctionnalité optionnelle, jamais pour une
    barrière de sécurité. Écrire un faux lead dans le CRM d'un prospect pendant une démonstration
    est un incident ; échouer bruyamment ne l'est pas.
    """
    if is_enabled():
        raise RuntimeError(
            f"Mode démonstration actif : « {operation} » est bloqué. "
            "Aucune écriture réelle (Sheets, HubSpot, Gmail) n'est possible en démo. "
            "Retirez ACA_DEMO_MODE pour utiliser les vrais services."
        )


class DemoLLM:
    """
    LLM factice déterministe — même interface que `ChatGroq` (`invoke`, `with_structured_output`).

    Il inspecte le prompt système pour savoir quel nœud l'appelle, exactement comme le `FakeLLM` de
    la suite de tests. Aucun appel réseau, aucune clé, et une sortie stable : deux démonstrations
    successives montrent la même chose, ce qui est précisément ce qu'on veut devant un client.
    """

    def __init__(self, role: str = "fast"):
        self.role = role

    # ── Interface ChatGroq ────────────────────────────────────────────────────────────────────
    def invoke(self, messages):
        system = getattr(messages[0], "content", "") if messages else ""
        content = self._respond(system, messages)

        class _Response:
            pass

        response = _Response()
        response.content = content
        response.usage_metadata = {"input_tokens": 0, "output_tokens": 0}
        return response

    def with_structured_output(self, schema):
        return _StructuredDemoLLM(self, schema)

    # ── Réponses par nœud ─────────────────────────────────────────────────────────────────────
    def _respond(self, system: str, messages) -> str:
        body = getattr(messages[-1], "content", "") if len(messages) > 1 else ""

        if "Classe l'e-mail" in system:
            return self._classify(body)
        if "SUPERVISEUR" in system:
            return self._supervise(body)
        if "relecteur qualité" in system:
            return "OK"
        if "Reformule la DEMANDE" in system:
            return "Demande commerciale reformulée pour la recherche."
        if "extrai" in system.lower() or "extract" in system.lower():
            return self._extract(body)
        return self._draft(body)

    @staticmethod
    def _classify(body: str) -> str:
        lowered = body.lower()
        if "cadeau" in lowered or "gagnez" in lowered or "offre limitée" in lowered:
            return '{"categorie": "SPAM", "confiance": 0.97}'
        if "candidature" in lowered or "stage" in lowered:
            return '{"categorie": "AUTRE", "confiance": 0.91}'
        if "connecter" in lowered or "erreur 500" in lowered:
            return '{"categorie": "SUPPORT", "confiance": 0.93}'
        if "démonstration" in lowered or "démo" in lowered:
            return '{"categorie": "DEMANDE_DEMO", "confiance": 0.95}'
        return '{"categorie": "DEVIS", "confiance": 0.94}'

    @staticmethod
    def _supervise(body: str) -> str:
        # Le superviseur reçoit la liste des agents déjà passés ; on suit un parcours réaliste
        # (connaissance → stratège) sans jamais boucler.
        return "stratege" if "connaissance" in body else "connaissance"

    @staticmethod
    def _extract(body: str) -> str:
        lowered = body.lower()
        if "50 licences" in lowered:
            entreprise, besoin = "PME Industrie", "devis pour 50 licences Enterprise"
        elif "démonstration" in lowered:
            entreprise, besoin = "Startup Tech", "démonstration de l'outil de gestion commerciale"
        elif "cahier des charges" in lowered:
            entreprise, besoin = "Grand Compte Banque", "intégration avec un mainframe COBOL"
        else:
            entreprise, besoin = "Entreprise inconnue", "demande d'information commerciale"
        return (
            f'{{"entreprise": "{entreprise}", "contact": "Contact démo", '
            f'"urgence": "moyenne", "besoin_principal": "{besoin}"}}'
        )

    @staticmethod
    def _draft(body: str) -> str:
        return (
            "Bonjour,\n\n"
            "Merci de votre message. Notre offre Enterprise est proposée à 49 €/utilisateur/mois, "
            "avec une dégressivité au-delà de 25 licences.\n\n"
            "Je vous propose un échange de 30 minutes pour cadrer précisément votre besoin.\n\n"
            "Bien cordialement,\n"
            "L'équipe commerciale\n\n"
            "— (brouillon généré en MODE DÉMONSTRATION : aucun modèle réel n'a été appelé)"
        )


class _StructuredDemoLLM:
    """Équivalent de `ChatGroq.with_structured_output(schema)` — parse la sortie JSON factice."""

    def __init__(self, demo_llm: DemoLLM, schema):
        self._demo_llm = demo_llm
        self._schema = schema

    def invoke(self, messages):
        import json

        response = self._demo_llm.invoke(messages)
        return self._schema(**json.loads(response.content))

"""
Tests du mode démonstration (§16.3) — essayer ACA sans aucune clé d'API.

Le test le plus important de ce fichier est `test_action_node_refuses_to_write_in_demo_mode` : une
démonstration qui écrirait un faux lead dans le CRM d'un prospect serait un incident. La barrière
LÈVE au lieu de dégrader gracieusement — seule exception assumée au contrat du projet — et ces
tests sont ce qui garantit qu'elle ne redevienne pas silencieuse.
"""
import uuid

import pytest

import aca.core.app as app_module
from aca.core import demo


# ── Commutateur ───────────────────────────────────────────────────────────────────────────────


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ACA_DEMO_MODE", raising=False)
    assert demo.is_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "oui"])
def test_enabled_values(monkeypatch, value):
    monkeypatch.setenv("ACA_DEMO_MODE", value)
    assert demo.is_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "non", "  "])
def test_disabled_values(monkeypatch, value):
    monkeypatch.setenv("ACA_DEMO_MODE", value)
    assert demo.is_enabled() is False


def test_read_dynamically_not_frozen_at_import(monkeypatch):
    """Leçon `DATABASE_URL`/pgvector : une valeur figée à l'import se gèle silencieusement."""
    monkeypatch.delenv("ACA_DEMO_MODE", raising=False)
    assert demo.is_enabled() is False
    monkeypatch.setenv("ACA_DEMO_MODE", "1")
    assert demo.is_enabled() is True


# ── Barrière d'écriture : la garantie centrale ────────────────────────────────────────────────


def test_guard_write_raises_in_demo_mode(monkeypatch):
    monkeypatch.setenv("ACA_DEMO_MODE", "1")
    with pytest.raises(RuntimeError, match="Mode démonstration"):
        demo.guard_write("écriture CRM")


def test_guard_write_is_a_noop_outside_demo_mode(monkeypatch):
    monkeypatch.delenv("ACA_DEMO_MODE", raising=False)
    demo.guard_write("écriture CRM")  # ne doit pas lever


def test_action_node_refuses_to_write_in_demo_mode(monkeypatch):
    """
    LE test de ce fichier. `action_node` est le seul point du graphe qui écrit réellement ; en
    démonstration il doit échouer bruyamment AVANT d'atteindre Sheets ou HubSpot.
    """
    monkeypatch.setenv("ACA_DEMO_MODE", "1")

    def _forbidden(**kwargs):
        raise AssertionError("Aucune écriture ne doit être tentée en mode démonstration.")

    monkeypatch.setattr(app_module.sheets, "append_lead", _forbidden)
    monkeypatch.setattr(app_module.hubspot, "create_lead", _forbidden)

    state = {
        "email_raw": {"sender": "demo@example.com", "subject": "Devis", "body": "Bonjour"},
        "classification": "DEVIS",
        "extracted_info": {"entreprise": "Demo SA"},
        "draft_response": "Proposition de démonstration.",
    }
    with pytest.raises(RuntimeError, match="Mode démonstration"):
        app_module.action_node(state)


def test_action_node_writes_normally_outside_demo_mode(monkeypatch):
    """Contre-épreuve : la barrière ne doit pas gêner le fonctionnement réel."""
    monkeypatch.delenv("ACA_DEMO_MODE", raising=False)
    written = []
    monkeypatch.setattr(app_module.sheets, "append_lead", lambda **k: written.append(k))
    monkeypatch.setattr(app_module.hubspot, "create_lead", lambda **k: None)

    state = {
        "email_raw": {"sender": "vrai@example.com", "subject": "Devis", "body": "Bonjour"},
        "classification": "DEVIS",
        "extracted_info": {"entreprise": "Vraie SA"},
        "draft_response": "Proposition.",
    }
    app_module.action_node(state)
    assert len(written) == 1


# ── Substitution des LLM ──────────────────────────────────────────────────────────────────────


def test_llm_factories_return_demo_llm(monkeypatch):
    """Sans cette bascule, le graphe exigerait une GROQ_API_KEY dès le premier nœud."""
    monkeypatch.setenv("ACA_DEMO_MODE", "1")
    for factory in (app_module.fast_llm, app_module.smart_llm, app_module.creative_llm):
        assert isinstance(factory(), demo.DemoLLM)


def test_llm_factories_are_real_outside_demo_mode(monkeypatch):
    monkeypatch.delenv("ACA_DEMO_MODE", raising=False)
    assert not isinstance(app_module.fast_llm(), demo.DemoLLM)


# ── Le LLM factice ────────────────────────────────────────────────────────────────────────────


class _Msg:
    def __init__(self, content):
        self.content = content


def _invoke(system: str, human: str = "") -> str:
    return demo.DemoLLM().invoke([_Msg(system), _Msg(human)]).content


@pytest.mark.parametrize("body,expected", [
    ("Cliquez ici pour récupérer votre cadeau", "SPAM"),
    ("Je vous envoie ma candidature pour un stage", "AUTRE"),
    ("Je n'arrive plus à me connecter, erreur 500", "SUPPORT"),
    ("Serait-il possible d'organiser une démonstration ?", "DEMANDE_DEMO"),
    ("Pouvez-vous m'envoyer un devis pour 50 licences ?", "DEVIS"),
])
def test_demo_classifier_covers_every_category(body, expected):
    """Une démonstration doit pouvoir montrer les 5 catégories, y compris celles sans suite."""
    assert expected in _invoke("Classe l'e-mail suivant", body)


def test_demo_llm_is_deterministic():
    """Deux démonstrations successives doivent montrer exactement la même chose."""
    first = _invoke("Classe l'e-mail suivant", "devis 50 licences")
    second = _invoke("Classe l'e-mail suivant", "devis 50 licences")
    assert first == second


def test_demo_llm_reports_zero_tokens():
    """Aucun modèle réel appelé ⇒ la consommation journalisée doit rester nulle, pas inventée."""
    response = demo.DemoLLM().invoke([_Msg("Classe l'e-mail suivant"), _Msg("devis")])
    assert response.usage_metadata == {"input_tokens": 0, "output_tokens": 0}


def test_demo_draft_states_it_is_a_demo():
    """Honnêteté : personne ne doit confondre un brouillon de démonstration avec un vrai."""
    draft = _invoke("Tu es un commercial", "Rédige une proposition")
    assert "DÉMONSTRATION" in draft


def test_structured_output_parses_into_the_schema():
    """`with_structured_output` doit se comporter comme le tool-calling réel de Groq."""
    from aca.core.app import ClassificationResult

    structured = demo.DemoLLM().with_structured_output(ClassificationResult)
    result = structured.invoke([_Msg("Classe l'e-mail suivant"), _Msg("devis 50 licences")])
    assert result.categorie == "DEVIS"
    assert 0 <= result.confiance <= 1


# ── Jeu de démonstration ──────────────────────────────────────────────────────────────────────


def test_demo_emails_are_wellformed():
    assert len(demo.DEMO_EMAILS) == 6
    for email in demo.DEMO_EMAILS:
        assert {"label", "sender", "subject", "body"} <= set(email)
        assert email["body"].strip()


def test_demo_emails_exercise_the_interesting_paths():
    """
    Le jeu doit permettre de MONTRER les fonctionnalités différenciantes, pas seulement le cas
    nominal : une clause contractuelle à risque et une question hors base de connaissances.
    """
    from aca.core.risk_scan import scan_risks

    bodies = [e["body"] for e in demo.DEMO_EMAILS]
    assert any(scan_risks(b) for b in bodies), "aucun e-mail ne déclenche risk_scan"
    assert any("COBOL" in b for b in bodies), "aucun e-mail ne provoque de lacune de connaissance"


# ── Bout en bout, sans aucune clé ─────────────────────────────────────────────────────────────


def test_full_graph_runs_with_no_credentials(monkeypatch):
    """
    La promesse du §16.3 : le graphe complet tourne sans clé et s'arrête à la pause humaine.
    `conftest.py` a déjà vidé toutes les variables d'API — c'est donc un vrai test « zéro clé ».
    """
    monkeypatch.setenv("ACA_DEMO_MODE", "1")
    monkeypatch.setattr(app_module.notify, "send", lambda *a, **k: False)
    monkeypatch.setattr(app_module.notify, "send_approval", lambda *a, **k: False)

    email = demo.DEMO_EMAILS[1]  # devis 50 licences
    config = {"configurable": {"thread_id": f"demo-test-{uuid.uuid4()}"}}
    app_module.app.invoke(
        {
            "email_raw": {k: email[k] for k in ("sender", "subject", "body")},
            "attachments_raw": [],
        },
        config,
    )

    snapshot = app_module.app.get_state(config)
    assert snapshot.values["classification"] == "DEVIS"
    assert snapshot.values["extracted_info"]["entreprise"] == "PME Industrie"
    assert snapshot.values["draft_response"]
    assert snapshot.next == ("action",), "le graphe doit s'arrêter à la validation humaine"

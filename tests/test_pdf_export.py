"""
Tests de l'export PDF de la proposition (§18, `aca/integrations/pdf_export.py`).

Priorité, dans cet ordre :

1. **Le PDF produit contient réellement le contenu attendu** — un round-trip complet (construire,
   réouvrir, extraire le texte) plutôt qu'un simple "ça n'a pas levé", qui aurait laissé passer un
   document vide ou cassé.
2. **`build_proposal_pdf` ne lève jamais** (même contrat que `notify.py`/`hubspot.py`) : un bouton
   de téléchargement cassé ne doit pas faire tomber l'écran de validation.
3. **`proposal_filename` assainit strictement** une entrée qui vient d'un champ extrait par un LLM
   à partir d'un e-mail entrant — donc non fiable — et qui finit dans un en-tête
   `Content-Disposition`.
4. **Le pied de page atteste la relecture humaine** : ce n'est pas un ornement, c'est la mention
   dont dépend tout le produit.
"""
import fitz
import pytest

from aca.integrations import pdf_export

# Palette synthétique minimale — les mêmes clés que `branding.resolve()` renverrait réellement,
# valeurs choisies pour matcher la palette par défaut du projet.
_TOKENS = {
    "BRAND_NAME": "ACA", "BRAND_COMPANY": "Acme Solutions",
    "BRAND_PRIMARY": "#0078D4", "BRAND_TEXT": "#111111", "BRAND_BACKGROUND": "#FFFFFF",
    "BRAND_LOGO": "",
}


def _extract_text(pdf_bytes: bytes) -> str:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = document[0].get_text()
    document.close()
    return text


# ── Round-trip : le contenu attendu est réellement dans le document ─────────────────────────
def test_build_proposal_pdf_round_trip_contains_expected_content():
    pdf_bytes = pdf_export.build_proposal_pdf(
        "Bonjour,\n\nVoici notre offre : 40 licences à 12 euros/mois.\n\nCordialement.",
        extracted_info={"entreprise": "Société Générale & Fils", "contact": "Jean Dupont",
                        "urgence": "haute", "besoin_principal": "40 licences"},
        classification="DEVIS", sender="jean@societe-generale-fils.fr", tokens=_TOKENS,
    )
    assert pdf_bytes is not None
    text = _extract_text(pdf_bytes)
    assert "Société Générale" in text
    assert "Jean Dupont" in text
    assert "40 licences" in text
    assert "relue et validée par un commercial avant envoi" in text
    assert "Acme Solutions" in text  # BRAND_COMPANY dans l'en-tête et le pied de page


def test_build_proposal_pdf_returns_real_pdf_bytes():
    pdf_bytes = pdf_export.build_proposal_pdf("Bonjour.", {}, "DEVIS", "a@b.fr", tokens=_TOKENS)
    assert pdf_bytes.startswith(b"%PDF")


def test_build_proposal_pdf_multiline_draft_becomes_paragraphs():
    pdf_bytes = pdf_export.build_proposal_pdf(
        "Première ligne.\nDeuxième ligne.\nTroisième ligne.", {}, "DEVIS", "a@b.fr", tokens=_TOKENS,
    )
    text = _extract_text(pdf_bytes)
    assert "Première ligne." in text
    assert "Deuxième ligne." in text
    assert "Troisième ligne." in text


# ── Dégradation gracieuse : ne lève jamais ───────────────────────────────────────────────────
def test_build_proposal_pdf_never_raises_on_internal_failure(monkeypatch):
    import aca.integrations.pdf_export as mod

    def _boom(*args, **kwargs):
        raise RuntimeError("PyMuPDF indisponible")

    # `fitz` est importé À L'INTÉRIEUR de build_proposal_pdf (paresseux) ; on force l'échec au
    # premier appel utile pour vérifier le filet de sécurité sans dépendre de l'implémentation.
    monkeypatch.setitem(__import__("sys").modules, "fitz", type("FakeFitz", (), {"open": staticmethod(_boom)}))
    result = mod.build_proposal_pdf("Bonjour.", {}, "DEVIS", "a@b.fr", tokens=_TOKENS)
    assert result is None


def test_build_proposal_pdf_handles_missing_extracted_info():
    # `extracted_info=None` est le cas d'un lead sans fiche extraite -- ne doit pas lever.
    pdf_bytes = pdf_export.build_proposal_pdf("Bonjour.", None, "", "", tokens=_TOKENS)
    assert pdf_bytes is not None


def test_build_proposal_pdf_handles_oversized_body_without_raising():
    long_draft = "Ligne très longue répétée. " * 2000  # bien au-delà de MAX_BODY_CHARS
    assert len(long_draft) > pdf_export.MAX_BODY_CHARS
    pdf_bytes = pdf_export.build_proposal_pdf(long_draft, {}, "DEVIS", "a@b.fr", tokens=_TOKENS)
    assert pdf_bytes is not None


def test_build_proposal_pdf_skips_svg_logo_without_crashing():
    tokens = dict(_TOKENS, BRAND_LOGO="data:image/svg+xml;base64,PHN2Zy8+")
    pdf_bytes = pdf_export.build_proposal_pdf("Bonjour.", {}, "DEVIS", "a@b.fr", tokens=tokens)
    assert pdf_bytes is not None  # le logo vectoriel est simplement omis, pas une erreur


# ── proposal_filename : assainissement strict ────────────────────────────────────────────────
def test_proposal_filename_sanitizes_accents_and_special_characters():
    name = pdf_export.proposal_filename({"entreprise": "Société Générale & Fils"}, "DEVIS")
    assert "societe-generale-fils" in name
    assert " " not in name and "&" not in name and "é" not in name


def test_proposal_filename_falls_back_when_no_company():
    name = pdf_export.proposal_filename(None, "DEVIS")
    assert name.startswith("proposition-prospect-devis-")


def test_proposal_filename_includes_classification_slug():
    name = pdf_export.proposal_filename({"entreprise": "Acme"}, "DEMANDE_DEMO")
    assert "demande-demo" in name


def test_proposal_filename_ends_with_pdf_extension():
    name = pdf_export.proposal_filename({"entreprise": "Acme"}, "DEVIS")
    assert name.endswith(".pdf")


@pytest.mark.parametrize("malicious", [
    "../../../etc/passwd", "Acme\"; rm -rf /", "Acme\r\nContent-Disposition: evil",
])
def test_proposal_filename_neutralises_path_and_header_injection(malicious):
    name = pdf_export.proposal_filename({"entreprise": malicious}, "DEVIS")
    assert "/" not in name and "\\" not in name and "\r" not in name and "\n" not in name
    assert '"' not in name

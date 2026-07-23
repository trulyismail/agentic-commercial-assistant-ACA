"""
Tests unitaires des fonctions pures de aca/integrations/sheets.py : tokenisation, extraction Q/R,
recherche par mots-clés (voie sparse), fusion RRF (recherche hybride) et similarité cosinus.
Aucun accès réseau ni Google Sheets — ces fonctions ne dépendent que de leurs arguments.
"""
import pytest

from aca.integrations.sheets import (
    _cosine_similarity,
    _escape_formula,
    _keyword_candidates,
    _row_qr,
    _rrf_fuse,
    _tokenize,
)


# ── _tokenize ─────────────────────────────────────────────────────────────────────────────────
def test_tokenize_lowercases_and_filters_short_words():
    assert _tokenize("Le TARIF de la licence Pro") == {"tarif", "licence", "pro"}


def test_tokenize_empty():
    assert _tokenize("") == set()


# ── _row_qr (tolérance d'en-têtes) ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("row", [
    {"Question": "Q1", "Réponse": "R1"},
    {"question": "Q1", "reponse": "R1"},
    {"Sujet": "Q1", "Information": "R1"},
])
def test_row_qr_header_variants(row):
    assert _row_qr(row) == ("Q1", "R1")


def test_row_qr_missing_fields():
    assert _row_qr({}) == ("", "")


# ── _keyword_candidates (voie sparse) ────────────────────────────────────────────────────────
RECORDS = [
    {"Question": "Quels sont vos tarifs professionnels ?", "Réponse": "La licence pro coûte 50€/mois."},
    {"Question": "Quel est votre délai de livraison ?", "Réponse": "Livraison en 48 heures ouvrées."},
    {"Question": "Proposez-vous un SLA ?", "Réponse": "Disponibilité garantie de 99.9%."},
]


def test_keyword_candidates_ranks_by_overlap():
    results = _keyword_candidates("tarifs licence pro", RECORDS)
    assert results, "au moins une correspondance attendue"
    top_score, top_q, _ = results[0]
    assert top_q == "Quels sont vos tarifs professionnels ?"
    assert top_score >= 2  # "tarifs" + "licence" + "pro" recouvrent question + réponse


def test_keyword_candidates_matches_answer_text():
    # "99.9" est côté réponse uniquement — la voie sparse doit le trouver (raison d'être du RRF).
    results = _keyword_candidates("SLA 99.9%", RECORDS)
    assert results[0][1] == "Proposez-vous un SLA ?"


def test_keyword_candidates_no_match():
    assert _keyword_candidates("recette tarte aux pommes", RECORDS) == []


def test_keyword_candidates_empty_query():
    assert _keyword_candidates("", RECORDS) == []


# ── _rrf_fuse (Reciprocal Rank Fusion) ───────────────────────────────────────────────────────
def test_rrf_pair_in_both_lists_wins():
    dense = [("A", "ra"), ("B", "rb")]
    sparse = [("C", "rc"), ("A", "ra")]
    fused = _rrf_fuse(dense, sparse, top_n=3)
    assert fused[0] == ("A", "ra")  # présent dans les deux listes → score RRF cumulé le plus haut


def test_rrf_respects_top_n():
    dense = [("A", "r"), ("B", "r"), ("C", "r")]
    sparse = [("D", "r")]
    assert len(_rrf_fuse(dense, sparse, top_n=2)) == 2


def test_rrf_empty_lists():
    assert _rrf_fuse([], [], top_n=3) == []


# ── _cosine_similarity ───────────────────────────────────────────────────────────────────────
def test_cosine_identical_vectors():
    assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_safe():
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ── _escape_formula (anti-injection de formule CSV / Sheets) ──────────────────────────────────
@pytest.mark.parametrize("payload", [
    "=IMPORTXML(\"http://evil\",\"//x\")",
    "+cmd|'/c calc'!A1",
    "-2+3",
    "@SUM(A1:A9)",
    "\t=1",
    "\r=1",
])
def test_escape_formula_neutralizes_triggers(payload):
    escaped = _escape_formula(payload)
    assert escaped.startswith("'"), "un déclencheur de formule doit être préfixé d'une apostrophe"
    assert escaped == "'" + payload


@pytest.mark.parametrize("safe", [
    "Doctolib SAS",
    "besoin d'une démo la semaine prochaine",
    "contact@example.com n'est PAS un déclencheur (le @ n'est pas en tête)",
    "99.9% de SLA",
    "",
])
def test_escape_formula_leaves_safe_text_untouched(safe):
    assert _escape_formula(safe) == safe


def test_escape_formula_handles_non_string():
    # append_lead peut recevoir un None / un nombre via extracted_info.get(...) — pas d'exception.
    assert _escape_formula(None) == ""
    assert _escape_formula(42) == "42"

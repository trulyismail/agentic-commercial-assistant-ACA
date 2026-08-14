"""
Tests du rendu PDF des rapports (§20, `aca/integrations/report_pdf.py`).

Priorité, dans cet ordre :

1. **Un vrai aller-retour**, comme dans `test_pdf_export.py` : construire le document, le rouvrir
   avec `fitz`, en extraire le texte et vérifier que le contenu attendu y est RÉELLEMENT. « Aucune
   exception levée » ne prouve pas qu'un PDF est lisible — un document blanc passe ce test-là.
2. **Ne lève jamais**, quoi qu'on lui donne. Ce rendu est appelé par un travail planifié de nuit
   autant que par un bouton : une exception y coûterait plus que l'absence du fichier.
3. **Les caractères non-Latin-1 ne cassent rien.** Défaut réel trouvé au premier rendu : les
   polices Base-14 d'un PDF sont écrites avec un encodage Latin-1, si bien que « … » et « — »
   sortaient en points parasites — et un objet d'e-mail entrant peut contenir n'importe quoi.
4. **La mesure de texte tient compte des accents**, sinon chaque paragraphe français déborde de la
   marge droite (l'autre défaut trouvé en regardant le document produit, pas en relisant le code).
5. **L'écriture sur disque est idempotente** : un rapport déjà présent n'est jamais réécrit, sinon
   une nouvelle exécution après une purge remplacerait un rapport complet par un rapport amputé
   portant le même nom.
"""
import pytest

from aca.core import branding, reporting
from aca.integrations import report_pdf


@pytest.fixture
def rapport():
    """Un rapport minimal mais représentatif : les cinq types de blocs, avec des accents."""
    return {
        "meta": {
            "title": "Rapport de test",
            "period_label": "juillet 2026",
            "period_start": "2026-07-01 00:00:00",
            "period_end": "2026-08-01 00:00:00",
            "comparison_label": "juin 2026",
            "generated_at": "04/08/2026 à 14:30",
            "generated_by": "marie",
            "note": "Période marquée par la campagne de rentrée.",
            "sections": ["summary", "categories"],
        },
        "groups": [
            {
                "title": "Activité commerciale",
                "blocks": [
                    {"key": "summary", "type": "kpis", "title": "Vue d'ensemble",
                     "context": "Chiffres clés de la période.",
                     "items": [
                         {"label": "E-mails traités", "value": 42, "suffix": "", "better": "up",
                          "hint": "", "comparison": {"delta": 12, "pct": 40.0,
                                                     "direction": "up", "previous": 30}},
                         {"label": "Délai médian", "value": 18, "suffix": " min", "better": "down",
                          "hint": "", "comparison": {"delta": 4, "pct": 28.6,
                                                     "direction": "up", "previous": 14}},
                     ]},
                    {"key": "categories", "type": "bars", "title": "Répartition par catégorie",
                     "context": "Ce qu'ACA a reçu, par nature de demande.",
                     "items": [{"label": "Devis", "value": 20, "previous": 12},
                               {"label": "Demande demo", "value": 8, "previous": 10}]},
                    {"key": "trend", "type": "line", "title": "Volume quotidien",
                     "context": "Nombre d'e-mails analysés par jour.",
                     "points": [{"label": "2026-07-01", "value": 3},
                                {"label": "2026-07-02", "value": 7},
                                {"label": "2026-07-03", "value": 5}]},
                    {"key": "emails", "type": "table", "title": "Détail des e-mails",
                     "context": "Le détail ligne à ligne.",
                     "columns": ["Date", "Expéditeur", "Catégorie"],
                     "rows": [["2026-07-01 09:12", "p1@exemple.fr", "DEVIS"],
                              ["2026-07-02 14:03", "p2@exemple.fr", "DEMANDE_DEMO"]],
                     "total": 2},
                    {"key": "note", "type": "text", "title": "Remarque",
                     "context": "Section indisponible.", "body": "Données non lisibles."},
                ],
            },
        ],
    }


def _texte(payload: bytes) -> str:
    import fitz

    with fitz.open(stream=payload, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)


def _pages(payload: bytes) -> int:
    import fitz

    with fitz.open(stream=payload, filetype="pdf") as document:
        return document.page_count


# ── Aller-retour réel ────────────────────────────────────────────────────────────────────────
def test_le_document_contient_reellement_son_contenu(rapport):
    """Le test qui compte : on relit le PDF produit, on n'affirme pas seulement qu'il existe."""
    payload = report_pdf.build_report_pdf(rapport, branding.resolve())
    assert payload and payload[:4] == b"%PDF"

    texte = _texte(payload)
    assert "Rapport de test" in texte
    assert "juillet 2026" in texte
    assert "marie" in texte
    assert "Vue d'ensemble" in texte
    assert "Devis" in texte
    assert "p1@exemple.fr" in texte


def test_la_couverture_annonce_ce_que_contient_le_rapport(rapport):
    """
    Sans ce sommaire, un lecteur ne peut pas distinguer « il ne s'est rien passé » de « cette
    section n'a pas été demandée » — deux conclusions opposées tirées de la même absence.
    """
    texte = _texte(report_pdf.build_report_pdf(rapport, branding.resolve()))
    assert "Contenu du rapport" in texte
    assert reporting.SECTIONS["summary"]["label"] in texte
    assert reporting.SECTIONS["categories"]["label"] in texte


def test_chaque_page_porte_la_mention_de_validation_humaine(rapport):
    """
    Ce document énonce des volumes de leads entrés au CRM : un lecteur qui ne connaît pas l'outil
    pourrait en conclure qu'une machine a engagé l'entreprise. Même raisonnement que le pied de
    page de `pdf_export.py`.
    """
    texte = _texte(report_pdf.build_report_pdf(rapport, branding.resolve()))
    assert "validation humaine" in texte
    assert "page 1/" in texte


def test_le_contexte_de_chaque_section_est_imprime(rapport):
    """Un chiffre sans son mode de calcul devient au mieux inutile, au pire trompeur."""
    texte = _texte(report_pdf.build_report_pdf(rapport, branding.resolve()))
    assert "Chiffres" in texte
    assert "nature de demande" in texte


def test_la_comparaison_est_imprimee_a_cote_de_la_valeur(rapport):
    texte = _texte(report_pdf.build_report_pdf(rapport, branding.resolve()))
    assert "+12" in texte  # écart absolu
    assert "40" in texte   # pourcentage


# ── Sens des couleurs ────────────────────────────────────────────────────────────────────────
def test_une_hausse_du_delai_est_signalee_comme_defavorable():
    """
    La seule couleur qui porte un jugement dans tout le document. Peindre en vert toute hausse
    ferait passer une dégradation du délai de réponse pour une bonne nouvelle.
    """
    palette = report_pdf._Palette(branding.resolve())
    hausse = {"delta": 4, "pct": 28.6, "direction": "up", "previous": 14}
    assert report_pdf._comparison_colour(hausse, "down", palette) == palette.danger
    assert report_pdf._comparison_colour(hausse, "up", palette) == palette.success
    # « neutral » (jetons consommés) ne doit ni féliciter ni alarmer.
    assert report_pdf._comparison_colour(hausse, "neutral", palette) == palette.muted


def test_une_valeur_stable_nest_ni_verte_ni_rouge():
    palette = report_pdf._Palette(branding.resolve())
    stable = {"delta": 0, "pct": 0.0, "direction": "flat", "previous": 30}
    assert report_pdf._comparison_colour(stable, "up", palette) == palette.muted


# ── Lisibilité imposée au thème ──────────────────────────────────────────────────────────────
def test_un_theme_sombre_ne_produit_pas_un_document_illisible():
    """
    Un thème sombre est correct à l'écran et désastreux sur un document imprimé puis transféré :
    du texte clair sur du papier clair. Le papier est donc forcé au clair, l'encre au foncé.
    """
    sombre = dict(branding.resolve())
    sombre.update({"BRAND_BACKGROUND": "#101418", "BRAND_TEXT": "#F2F4F7"})
    palette = report_pdf._Palette(sombre)

    assert palette.paper_hex == "#FFFFFF"
    assert branding.contrast_ratio(palette.ink_hex, palette.paper_hex) >= 4.5


def test_la_couleur_du_client_est_respectee_quand_elle_est_lisible():
    """Forcer la lisibilité ne doit pas revenir à ignorer la marque du client."""
    clair = dict(branding.resolve())
    clair.update({"BRAND_BACKGROUND": "#FFFDF7", "BRAND_TEXT": "#241C10",
                  "BRAND_PRIMARY": "#7A1E3C"})
    palette = report_pdf._Palette(clair)
    assert palette.paper_hex == "#FFFDF7"
    assert palette.ink_hex == "#241C10"
    assert palette.primary == report_pdf._rgb("#7A1E3C")


# ── Encodage et mesure ───────────────────────────────────────────────────────────────────────
def test_les_caracteres_hors_latin1_sont_remplaces_pas_casses():
    """
    Défaut réel : « … » et « — » sortaient en points parasites, dans chaque cellule tronquée et à
    chaque valeur absente. Un objet d'e-mail entrant peut en outre contenir n'importe quel
    caractère Unicode.
    """
    assert report_pdf._safe("a… b — c – d’e") == "a... b - c - d'e"
    assert report_pdf._safe("prix : 12 €") == "prix : 12 EUR"
    # Contenu non maîtrisé (objet d'e-mail) : un « ? » visible vaut mieux qu'un glyphe cassé.
    assert report_pdf._safe("見積もり") == "????"
    # Les caractères qui SONT dans Latin-1 doivent être préservés tels quels.
    assert report_pdf._safe("été « déjà » · à") == "été « déjà » · à"


def test_safe_ne_leve_sur_aucune_entree():
    for valeur in (None, 42, 3.5, b"octets", ["liste"], {"clé": "valeur"}):
        assert isinstance(report_pdf._safe(valeur), str)


def test_la_mesure_tient_compte_des_accents():
    """
    `fitz.get_text_length` sous-évalue gravement les accents ; sur cette machine « ééééééééée »
    mesurait 22,8 points contre 45,6 pour « eeeeeeeeee ». Conséquence constatée sur le premier
    rendu réel : chaque paragraphe en français débordait de la marge droite.
    """
    avec = report_pdf._measure("ééééééééée", report_pdf.FONT, 9)
    sans = report_pdf._measure("eeeeeeeeee", report_pdf.FONT, 9)
    assert avec == pytest.approx(sans, rel=0.01)


def test_le_retour_a_la_ligne_respecte_la_largeur_demandee():
    texte = ("Chiffres clés de la période, comparés à la période précédente de même durée. "
             "Un e-mail est traité dès qu'ACA l'a analysé, validé seulement après relecture.")
    lignes = report_pdf._wrap(texte, report_pdf.CONTENT_WIDTH, report_pdf.FONT, 8.2)
    assert len(lignes) > 1
    assert all(report_pdf._measure(ligne, report_pdf.FONT, 8.2) <= report_pdf.CONTENT_WIDTH
               for ligne in lignes)


def test_une_cellule_trop_longue_est_tronquee_dans_sa_largeur():
    tronque = report_pdf._ellipsize("un objet d'e-mail vraiment très long", 60,
                                    report_pdf.FONT, 8)
    assert tronque.endswith("…")
    assert report_pdf._measure(tronque, report_pdf.FONT, 8) <= 60


# ── Ne lève jamais ───────────────────────────────────────────────────────────────────────────
def test_un_rapport_vide_produit_un_document_pas_une_exception():
    payload = report_pdf.build_report_pdf({"meta": {}, "groups": []}, branding.resolve())
    assert payload and payload[:4] == b"%PDF"


def test_une_structure_absurde_ne_leve_pas():
    """Contrat identique à `notify.py`/`hubspot.py` : `None` plutôt qu'une exception."""
    for entree in ({}, {"groups": None}, {"meta": None, "groups": [{"title": "x"}]},
                   {"meta": {}, "groups": [{"title": "x", "blocks": [{"type": "inconnu"}]}]}):
        resultat = report_pdf.build_report_pdf(entree, branding.resolve())
        assert resultat is None or resultat[:4] == b"%PDF"


def test_un_type_de_bloc_inconnu_est_rendu_en_texte(rapport):
    rapport["groups"][0]["blocks"] = [
        {"key": "x", "type": "diagramme-du-futur", "title": "Inconnu",
         "context": "Bloc d'un type que ce rendu ne connaît pas.", "body": "Repli en texte."},
    ]
    texte = _texte(report_pdf.build_report_pdf(rapport, branding.resolve()))
    assert "Repli en texte." in texte


def test_un_jeton_de_marque_invalide_ne_casse_pas_le_document(rapport):
    casse = dict(branding.resolve())
    casse["BRAND_PRIMARY"] = "pas-une-couleur"
    payload = report_pdf.build_report_pdf(rapport, casse)
    assert payload and payload[:4] == b"%PDF"


# ── Pagination ───────────────────────────────────────────────────────────────────────────────
def test_un_tableau_long_est_pagine_avec_len_tete_repete(rapport):
    """
    Sans l'en-tête répété, la deuxième page d'un tableau devient une grille de valeurs dont plus
    personne ne sait laquelle est la date et laquelle est l'expéditeur.
    """
    rapport["groups"][0]["blocks"] = [{
        "key": "emails", "type": "table", "title": "Détail des e-mails",
        "context": "Beaucoup de lignes.",
        "columns": ["Date", "Expéditeur", "Catégorie"],
        "rows": [[f"2026-07-{(i % 28) + 1:02d} 09:00", f"p{i}@exemple.fr", "DEVIS"]
                 for i in range(120)],
        "total": 120,
    }]
    payload = report_pdf.build_report_pdf(rapport, branding.resolve())
    assert _pages(payload) >= 3  # couverture + au moins deux pages de tableau
    assert _texte(payload).count("Expéditeur") >= 2  # en-tête répété


def test_chaque_famille_de_sections_commence_sur_une_page_neuve(rapport):
    """Ce qui rend le document feuilletable : on cherche une famille, on ne la trouve pas au
    milieu d'un tableau."""
    rapport["groups"].append({
        "title": "Traçabilité et conformité",
        "blocks": [{"key": "note", "type": "text", "title": "Validations",
                    "context": "Registre d'audit.", "body": "Rien à signaler."}],
    })
    assert _pages(report_pdf.build_report_pdf(rapport, branding.resolve())) >= 3


# ── Écriture sur disque ──────────────────────────────────────────────────────────────────────
def test_lecriture_est_idempotente(tmp_path):
    """
    Si le travail planifié repasse après une purge de rétention, réécrire remplacerait un rapport
    complet par un rapport amputé portant le même nom, sans que personne s'en aperçoive.
    """
    dossier = str(tmp_path / "reports")
    premier = report_pdf.write_pdf(dossier, "rapport-2026-07.pdf", b"%PDF-complet")
    assert premier["skipped"] is False

    second = report_pdf.write_pdf(dossier, "rapport-2026-07.pdf", b"%PDF-court")
    assert second["skipped"] is True
    assert second["bytes"] == premier["bytes"]
    with open(second["path"], "rb") as handle:
        assert handle.read() == b"%PDF-complet"


def test_la_reecriture_reste_possible_explicitement(tmp_path):
    dossier = str(tmp_path / "reports")
    report_pdf.write_pdf(dossier, "r.pdf", b"%PDF-1")
    resultat = report_pdf.write_pdf(dossier, "r.pdf", b"%PDF-2", overwrite=True)
    assert resultat["skipped"] is False
    with open(resultat["path"], "rb") as handle:
        assert handle.read() == b"%PDF-2"


def test_un_repertoire_absent_donne_une_liste_vide_pas_une_erreur(tmp_path):
    """Avant le premier passage du planificateur, ce répertoire n'existe pas — ce n'est pas une
    anomalie."""
    assert report_pdf.list_reports(str(tmp_path / "jamais-cree")) == []


def test_les_rapports_sont_listes_du_plus_recent_au_plus_ancien(tmp_path):
    import os
    import time

    dossier = str(tmp_path / "reports")
    report_pdf.write_pdf(dossier, "rapport-2026-06.pdf", b"%PDF-juin")
    report_pdf.write_pdf(dossier, "rapport-2026-07.pdf", b"%PDF-juillet")
    # Horodatages distincts : sur un disque rapide, deux écritures successives peuvent partager la
    # même seconde et rendre l'ordre indéterminé.
    maintenant = time.time()
    os.utime(os.path.join(dossier, "rapport-2026-07.pdf"), (maintenant, maintenant))
    os.utime(os.path.join(dossier, "rapport-2026-06.pdf"),
             (maintenant - 3600, maintenant - 3600))

    noms = [entree["name"] for entree in report_pdf.list_reports(dossier)]
    assert noms == ["rapport-2026-07.pdf", "rapport-2026-06.pdf"]


def test_seuls_les_pdf_sont_listes(tmp_path):
    dossier = tmp_path / "reports"
    dossier.mkdir()
    (dossier / "rapport-2026-07.pdf").write_bytes(b"%PDF")
    (dossier / "notes.txt").write_text("pas un rapport")
    assert [e["name"] for e in report_pdf.list_reports(str(dossier))] == ["rapport-2026-07.pdf"]

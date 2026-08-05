"""
Tests du moteur de rapport (§20, `aca/core/reporting.py`).

Priorité, dans cet ordre — un rapport est lu par quelqu'un qui n'a pas accès aux données brutes et
qui prendra des décisions dessus, donc **un chiffre faux y est pire qu'une section absente** :

1. **Les bornes de période sont justes**, et la borne haute est exclue. Sans cela, l'événement qui
   tombe pile à la frontière serait compté dans deux mois consécutifs et la comparaison — la raison
   d'être du rapport — gonflerait des deux côtés.
2. **La comparaison compare ce qu'il faut** : une fenêtre de même durée immédiatement antérieure,
   jamais « le mois d'avant » naïf (28 ≠ 31 jours).
3. **Une section qui échoue n'emporte pas le rapport**, et le dit au lieu de se taire — un lecteur
   qui voit une section vide doit pouvoir distinguer « rien ne s'est passé » de « la lecture a
   échoué ».
4. **Chaque section porte son contexte**, parce qu'un rapport circule hors de son écran d'origine.
5. **Sans comparaison demandée, aucun écart n'est affiché** — plutôt que des « 0 % » partout, qui
   seraient une comparaison fausse présentée comme vraie.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from aca.core import reporting
from aca.storage import analytics_store, review_store


@pytest.fixture(autouse=True)
def _bases_neuves(tmp_path, monkeypatch):
    """Bases neuves par test : ces assertions portent sur des COMPTES, donc sur le contenu global."""
    monkeypatch.setattr(analytics_store, "DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setattr(review_store, "DB_PATH", str(tmp_path / "reviews.sqlite"))
    review_store.init_db()


def _classify(thread_id: str, classification: str, when: datetime, *, validated: bool = False):
    """Enregistre un e-mail classé à une date choisie (antidatée en base : les fonctions
    d'enregistrement horodatent à `now`, et on ne peut pas attendre juillet 2026)."""
    analytics_store.record_classification(thread_id, classification, f"{thread_id}@exemple.fr",
                                          "poller")
    if validated:
        analytics_store.record_draft_ready(thread_id)
        analytics_store.record_validation(thread_id)
    with sqlite3.connect(analytics_store.DB_PATH) as conn:
        conn.execute("UPDATE events SET classified_at = ? WHERE thread_id = ?",
                     (when.strftime("%Y-%m-%d %H:%M:%S"), thread_id))
        conn.commit()


# ── Bornes de période ────────────────────────────────────────────────────────────────────────
def test_month_bounds_borne_haute_exclue():
    start, end = reporting.month_bounds(2026, 7)
    assert start == datetime(2026, 7, 1)
    assert end == datetime(2026, 8, 1)  # exclue : le 1er août n'appartient pas à juillet


def test_month_bounds_passe_lannee_en_decembre():
    assert reporting.month_bounds(2026, 12)[1] == datetime(2027, 1, 1)


def test_last_completed_month_nest_jamais_le_mois_en_cours():
    """
    Un rapport « du mois » produit le 12 ne porterait que sur onze jours, et se comparerait à un
    mois plein : il inventerait une chute d'activité qui n'a pas eu lieu.
    """
    assert reporting.last_completed_month(datetime(2026, 8, 12)) == (2026, 7)
    assert reporting.last_completed_month(datetime(2026, 1, 3)) == (2025, 12)


def test_previous_period_a_la_meme_duree_que_la_periode():
    """
    Volontairement « même durée » et non « mois précédent » : comparer 31 jours à 28 ferait
    apparaître février en recul de 10 % chaque année sans qu'il s'y passe quoi que ce soit.
    """
    start, end = datetime(2026, 7, 1), datetime(2026, 7, 15)
    previous_start, previous_end = reporting.previous_period(start, end)
    assert previous_end == start
    assert (end - start) == (previous_end - previous_start)


def test_period_label_affiche_le_dernier_jour_INCLUS():
    """Écrire « au 1er août » pour un rapport de juillet ferait douter de ce qu'il contient."""
    assert reporting.period_label(*reporting.month_bounds(2026, 7)) == "juillet 2026"
    assert (reporting.period_label(datetime(2026, 7, 1), datetime(2026, 7, 16))
            == "du 01/07/2026 au 15/07/2026")


def test_report_filename_est_triable():
    assert reporting.report_filename(reporting.monthly_spec(2026, 7)) == "rapport-2026-07.pdf"
    spec = reporting.new_spec(datetime(2026, 7, 1), datetime(2026, 7, 16))
    assert reporting.report_filename(spec) == "rapport-2026-07-01_2026-07-15.pdf"


# ── Fenêtrage des données ────────────────────────────────────────────────────────────────────
def test_un_evenement_a_la_frontiere_nest_compte_quune_fois():
    """La propriété qui rend la comparaison mois/mois honnête."""
    _classify("bord", "DEVIS", datetime(2026, 8, 1, 0, 0, 0))

    juillet = analytics_store.funnel_counts(start=datetime(2026, 7, 1), end=datetime(2026, 8, 1))
    aout = analytics_store.funnel_counts(start=datetime(2026, 8, 1), end=datetime(2026, 9, 1))
    assert juillet["classifiés"] == 0
    assert aout["classifiés"] == 1


def test_les_sections_lisent_bien_la_periode_demandee():
    _classify("a", "DEVIS", datetime(2026, 7, 10), validated=True)
    _classify("b", "DEMANDE_DEMO", datetime(2026, 7, 20))
    _classify("c", "SPAM", datetime(2026, 6, 15))  # période précédente

    report = reporting.collect(reporting.monthly_spec(2026, 7))
    resume = report["groups"][0]["blocks"][0]
    valeurs = {item["label"]: item["value"] for item in resume["items"]}
    assert valeurs["E-mails traités"] == 2
    assert valeurs["Leads validés"] == 1


def test_la_comparaison_porte_sur_la_periode_precedente():
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    _classify("b", "DEVIS", datetime(2026, 7, 11))
    _classify("c", "DEVIS", datetime(2026, 6, 10))  # juin : la période de comparaison

    report = reporting.collect(reporting.monthly_spec(2026, 7))
    resume = report["groups"][0]["blocks"][0]
    traites = next(i for i in resume["items"] if i["label"] == "E-mails traités")
    assert traites["value"] == 2
    assert traites["comparison"]["previous"] == 1
    assert traites["comparison"]["delta"] == 1
    assert traites["comparison"]["direction"] == "up"


def test_une_categorie_disparue_reste_visible_dans_la_comparaison():
    """
    Un canal qui s'est tari est une information. L'omettre ferait lire le rapport comme si la
    catégorie n'avait jamais existé.
    """
    _classify("c", "SUPPORT", datetime(2026, 6, 10))  # présent en juin, absent en juillet
    _classify("a", "DEVIS", datetime(2026, 7, 10))

    report = reporting.collect(reporting.monthly_spec(2026, 7))
    categories = next(b for g in report["groups"] for b in g["blocks"] if b["key"] == "categories")
    libelles = {item["label"]: item for item in categories["items"]}
    assert "Support" in libelles
    assert libelles["Support"]["value"] == 0
    assert libelles["Support"]["previous"] == 1


# ── Comparaison honnête ──────────────────────────────────────────────────────────────────────
def test_pas_de_pourcentage_quand_la_periode_precedente_est_a_zero():
    """Afficher « +100 % » en passant de 0 à 3 raconterait une progression sans base."""
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    report = reporting.collect(reporting.monthly_spec(2026, 7))
    traites = next(i for i in report["groups"][0]["blocks"][0]["items"]
                   if i["label"] == "E-mails traités")
    assert traites["comparison"]["previous"] == 0
    assert traites["comparison"]["pct"] is None
    assert traites["comparison"]["delta"] == 1


def test_le_delai_de_reponse_est_favorable_a_la_baisse():
    """
    Sans ce sens déclaré, le rendu colorierait en vert un délai de validation qui se dégrade —
    un rapport flatteur et faux.
    """
    report = reporting.collect(reporting.monthly_spec(2026, 7))
    delai = next(i for i in report["groups"][0]["blocks"][0]["items"]
                 if i["label"].startswith("Délai médian"))
    assert delai["better"] == "down"


def test_sans_comparaison_demandee_aucun_ecart_nest_affiche():
    """Des « 0 % » partout seraient une comparaison fausse présentée comme vraie."""
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    spec = reporting.new_spec(*reporting.month_bounds(2026, 7), sections=["summary"],
                              compare=False)
    report = reporting.collect(spec)
    assert report["meta"]["comparison_label"] == ""
    assert all("comparison" not in item for item in report["groups"][0]["blocks"][0]["items"])


# ── Structure et robustesse ──────────────────────────────────────────────────────────────────
def test_chaque_section_porte_un_contexte():
    """Un rapport circule hors de son écran d'origine : un chiffre sans son mode de calcul y
    devient au mieux inutile, au pire trompeur."""
    report = reporting.collect(
        reporting.new_spec(*reporting.month_bounds(2026, 7), sections=list(reporting.SECTIONS)))
    blocks = [block for group in report["groups"] for block in group["blocks"]]
    assert blocks, "aucune section produite"
    assert all(block.get("context") for block in blocks)


def test_les_sections_sont_regroupees_dans_lordre_declare():
    report = reporting.collect(
        reporting.new_spec(*reporting.month_bounds(2026, 7), sections=list(reporting.SECTIONS)))
    titres = [group["title"] for group in report["groups"]]
    assert titres == [g for g in reporting.GROUP_ORDER if g in titres]


def test_une_section_en_echec_ne_fait_pas_tomber_le_rapport_et_le_dit(monkeypatch):
    """
    Un rapport amputé d'une section reste utile ; un rapport qui ne se génère pas ne l'est pas. Et
    le taire serait pire : le lecteur croirait qu'il n'y avait rien à dire.
    """
    def _explose(*_args, **_kwargs):
        raise RuntimeError("base indisponible")

    monkeypatch.setitem(reporting.SECTIONS["categories"], "fn", _explose)
    report = reporting.collect(
        reporting.new_spec(*reporting.month_bounds(2026, 7), sections=["summary", "categories"]))

    blocks = {block["key"]: block for group in report["groups"] for block in group["blocks"]}
    assert blocks["summary"]["type"] == "kpis"          # le reste du rapport est intact
    assert blocks["categories"]["type"] == "text"
    assert "RuntimeError" in blocks["categories"]["body"]


def test_une_section_inconnue_est_ignoree_sans_lever():
    """Un préréglage enregistré avant qu'une section soit retirée ne doit pas casser la page."""
    report = reporting.collect(
        reporting.new_spec(*reporting.month_bounds(2026, 7),
                           sections=["summary", "section-qui-nexiste-plus"]))
    keys = [block["key"] for group in report["groups"] for block in group["blocks"]]
    assert keys == ["summary"]


def test_le_rapport_mensuel_exclut_le_detail_email_par_email():
    """
    Le détail peut faire des dizaines de pages et recopie des adresses de prospects dans un fichier
    qui circulera : il reste un choix conscient du rapport paramétrable, jamais un défaut.
    """
    assert "emails" not in reporting.MONTHLY_SECTIONS
    assert "emails" in reporting.SECTIONS


# ── Détail e-mail et filtres ─────────────────────────────────────────────────────────────────
def test_le_detail_email_respecte_les_colonnes_demandees():
    """« La catégorie et le nom des e-mails seulement » — la demande explicite de l'utilisateur."""
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    spec = reporting.new_spec(*reporting.month_bounds(2026, 7), sections=["emails"],
                              columns=["sender", "classification"])
    block = reporting.collect(spec)["groups"][0]["blocks"][0]
    assert block["columns"] == ["Expéditeur", "Catégorie"]
    assert block["rows"] == [["a@exemple.fr", "DEVIS"]]


def test_le_detail_email_respecte_le_filtre_de_categorie_et_le_dit_dans_le_contexte():
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    _classify("b", "SPAM", datetime(2026, 7, 11))
    spec = reporting.new_spec(*reporting.month_bounds(2026, 7), sections=["emails"],
                              columns=["classification"],
                              filters={"classifications": ["DEVIS"]})
    block = reporting.collect(spec)["groups"][0]["blocks"][0]
    assert block["rows"] == [["DEVIS"]]
    # Le filtre appliqué doit être ÉCRIT dans le document : un tableau filtré sans mention du
    # filtre se lit comme un tableau complet.
    assert "DEVIS" in block["context"]


def test_le_detail_email_respecte_le_filtre_expediteur():
    _classify("a", "DEVIS", datetime(2026, 7, 10))
    _classify("b", "DEVIS", datetime(2026, 7, 11))
    spec = reporting.new_spec(*reporting.month_bounds(2026, 7), sections=["emails"],
                              columns=["sender"], filters={"sender_contains": "b@"})
    block = reporting.collect(spec)["groups"][0]["blocks"][0]
    assert block["rows"] == [["b@exemple.fr"]]


# ── Réactivité (classement par tranche) ──────────────────────────────────────────────────────
def test_les_delais_sont_ranges_par_tranche():
    """
    Le classement par tranche est ce qui rend le chiffre actionnable : « moins d'une heure » est le
    seuil commercial qui compte, pas une moyenne.
    """
    block = reporting._s_response(
        (datetime(2026, 7, 1), datetime(2026, 8, 1)),
        (datetime(2026, 6, 1), datetime(2026, 7, 1)),
        {},
    )
    assert [item["label"] for item in block["items"]] == [
        "Moins d'1 h", "1 h à 4 h", "4 h à 24 h", "Plus de 24 h",
    ]


# ── Préréglages ──────────────────────────────────────────────────────────────────────────────
def test_un_prereglage_ne_fige_pas_les_dates():
    """
    Un préréglage nommé « Revue mensuelle direction » décrit un CONTENU, pas un mois. Y figer
    juillet en ferait un préréglage inutilisable dès août.
    """
    spec = reporting.new_spec(datetime(2026, 7, 1), datetime(2026, 8, 1), title="Revue direction",
                              sections=["summary"])
    reporting.save_preset("Revue direction", spec)
    stored = reporting.list_presets()["Revue direction"]
    assert stored["sections"] == ["summary"]
    assert "start" not in stored and "end" not in stored


def test_un_prereglage_sans_nom_est_refuse():
    with pytest.raises(ValueError):
        reporting.save_preset("   ", reporting.new_spec(datetime(2026, 7, 1), datetime(2026, 8, 1)))


def test_un_prereglage_supprime_disparait_de_la_liste():
    reporting.save_preset("Jetable", reporting.new_spec(datetime(2026, 7, 1), datetime(2026, 8, 1)))
    reporting.delete_preset("Jetable")
    assert "Jetable" not in reporting.list_presets()


def test_un_prereglage_illisible_est_ignore_pas_fatal():
    """Un JSON abîmé est une gêne ; une page de rapports inaccessible est une panne."""
    from aca.storage import config_store

    config_store.set_setting(reporting.PRESET_PREFIX + "Casse", "{ceci n'est pas du json")
    assert "Casse" not in reporting.list_presets()


# ── Sections alimentées par les autres registres ─────────────────────────────────────────────
def test_la_section_relectures_compte_ce_qui_a_ete_transmis():
    review_store.create_batch(
        [{"thread_id": "t-1", "subject": "Devis urgent", "sender": "p1@exemple.fr",
          "classification": "DEVIS"}],
        requester="marie", note="Second avis",
    )
    now = datetime.now()
    spec = reporting.new_spec(now - timedelta(days=1), now + timedelta(days=1),
                              sections=["reviews"])
    block = reporting.collect(spec)["groups"][0]["blocks"][0]
    assert block["total"] == 1
    assert "marie" in block["rows"][0]

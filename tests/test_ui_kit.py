"""
Tests de la trousse de composants d'interface (§18, `aca/core/ui_kit.py`).

Priorité, dans cet ordre :

1. **Rien de ce qui traverse ces fonctions ne casse le HTML injecté.** Le corps d'un e-mail entrant,
   un nom d'entreprise extrait par le LLM, ou un libellé d'action du journal d'activité peuvent tous
   contenir des caractères hostiles (`<`, `>`, `"`) — c'est `prompt_guard.py` qui les signale, pas
   ce module qui les neutralise ; si l'échappement manquait ici, l'affichage serait le trou.
2. **Les états (`tone`/`state`) produisent les bonnes classes CSS**, puisque c'est uniquement par
   elles que `branding.py` applique une couleur de sens — une faute de frappe dans un nom de classe
   romprait silencieusement le rendu sans qu'aucun test fonctionnel ne le remarque.
3. **Les cas vides ont un comportement défini** (`timeline([])`, `diff` sans changement) plutôt que de
   produire une liste HTML vide et déroutante.

Aucun import Streamlit : `ui_kit.py` est pur, ces tests tournent hors ligne comme le reste de la
suite.
"""
from aca.core import ui_kit


# ── Échappement HTML (propriété de sécurité) ─────────────────────────────────────────────────
def test_section_echappe_le_html_hostile():
    html = ui_kit.section("<script>alert(1)</script>", subtitle="<img src=x onerror=alert(1)>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=" not in html or "&lt;img" in html


def test_decision_rail_echappe_les_libelles():
    html = ui_kit.decision_rail([{"label": '"><svg onload=alert(1)>', "state": ui_kit.STEP_DONE}])
    assert "<svg onload" not in html
    assert "&lt;svg" in html


def test_chip_echappe_le_label():
    html = ui_kit.chip('<b>injecte</b>')
    assert "<b>injecte</b>" not in html
    assert "&lt;b&gt;" in html


def test_timeline_echappe_les_champs_evenement():
    html = ui_kit.timeline([{"when": "2026-01-01", "who": "<i>x</i>", "what": "<i>y</i>",
                             "detail": "<i>z</i>"}])
    assert "<i>" not in html
    assert html.count("&lt;i&gt;") == 3


def test_empty_state_echappe_le_titre_et_le_corps():
    html = ui_kit.empty_state("<b>Titre</b>", "<b>Corps</b>")
    assert "<b>Titre</b>" not in html and "<b>Corps</b>" not in html


# ── En-tête de section ────────────────────────────────────────────────────────────────────────
def test_section_sans_eyebrow_ni_sous_titre():
    html = ui_kit.section("Titre seul")
    assert "aca-section__eyebrow" not in html
    assert "aca-section__sub" not in html
    assert "Titre seul" in html


def test_section_avec_eyebrow_et_icone():
    html = ui_kit.section("Titre", subtitle="Sous-titre", icon="mail", eyebrow="Étape 1")
    assert "aca-section__eyebrow" in html and "Étape 1" in html
    assert "aca-section__sub" in html and "Sous-titre" in html
    assert 'aria-hidden="true"' in html  # l'icône Material est bien émise


# ── Rail de décision (composant signature) ───────────────────────────────────────────────────
def test_decision_rail_etats_produisent_les_bonnes_classes():
    html = ui_kit.decision_rail([
        {"label": "Reçu", "state": ui_kit.STEP_DONE},
        {"label": "En cours", "state": ui_kit.STEP_ACTIVE},
        {"label": "À venir", "state": ui_kit.STEP_TODO},
        {"label": "Risque détecté", "state": ui_kit.STEP_ALERT},
    ])
    assert "aca-rail__step--done" in html
    assert "aca-rail__step--active" in html
    assert "aca-rail__step--todo" in html
    assert "aca-rail__step--alert" in html


def test_decision_rail_defaut_todo_si_etat_absent():
    html = ui_kit.decision_rail([{"label": "Sans état déclaré"}])
    assert "aca-rail__step--todo" in html


def test_decision_rail_numerote_a_partir_de_un_sans_icone():
    html = ui_kit.decision_rail([{"label": "A"}, {"label": "B"}])
    assert 'aca-rail__num">1<' in html
    assert 'aca-rail__num">2<' in html


def test_decision_rail_icone_remplace_le_numero():
    html = ui_kit.decision_rail([{"label": "A", "icon": "check"}])
    assert "aca-rail__num" not in html
    assert "aca-i" in html


def test_decision_rail_detail_optionnel():
    with_detail = ui_kit.decision_rail([{"label": "A", "detail": "précision"}])
    without_detail = ui_kit.decision_rail([{"label": "A"}])
    assert "aca-rail__detail" in with_detail
    assert "aca-rail__detail" not in without_detail


# ── Indicateurs ───────────────────────────────────────────────────────────────────────────────
def test_stat_tonalite_optionnelle():
    neutral = ui_kit.stat("Actions", 12)
    warn = ui_kit.stat("Incidents", 3, tone="warn")
    assert "aca-stat--" not in neutral
    assert "aca-stat--warn" in warn


def test_stat_hint_optionnel():
    with_hint = ui_kit.stat("Actions", 12, hint="sur 7 jours")
    without_hint = ui_kit.stat("Actions", 12)
    assert "aca-stat__hint" in with_hint
    assert "aca-stat__hint" not in without_hint


def test_stat_row_compose_plusieurs_stats():
    html = ui_kit.stat_row([{"label": "A", "value": 1}, {"label": "B", "value": 2, "tone": "ok"}])
    assert html.count("aca-stat\"") + html.count("aca-stat ") >= 1
    assert "aca-stat--ok" in html


def test_les_rangees_acceptent_aussi_des_tuples():
    """
    Régression : `chip_row`/`stat_row` n'acceptaient que des dicts, alors que `readout()` prend des
    tuples — deux conventions voisines dans le même module. Le panneau « Tâches programmées » est
    parti en `TypeError` illisible en production pour cette seule raison. Les deux écritures sont
    désormais équivalentes ; ce test verrouille les deux formes en même temps.
    """
    en_dict = ui_kit.chip_row([{"label": "Envoi programmé", "tone": "info", "icon": "schedule_send"}])
    en_tuple = ui_kit.chip_row([("Envoi programmé", "info", "schedule_send")])
    assert en_dict == en_tuple
    assert "aca-chip2--info" in en_tuple

    assert ui_kit.stat_row([{"label": "A", "value": 1}]) == ui_kit.stat_row([("A", 1)])


def test_chip_row_tuple_partiel_ne_leve_pas():
    # Un tuple plus court doit rester valide : les champs absents gardent leur défaut.
    html = ui_kit.chip_row([("Sans ton",)])
    assert "Sans ton" in html
    assert "aca-chip2--" not in html


def test_chip_row_compose_plusieurs_chips():
    html = ui_kit.chip_row([{"label": "OK", "tone": "ok"}, {"label": "Attention", "tone": "warn"}])
    assert "aca-chip2--ok" in html
    assert "aca-chip2--warn" in html


# ── États vides ───────────────────────────────────────────────────────────────────────────────
def test_empty_state_action_hint_optionnel():
    with_hint = ui_kit.empty_state("Titre", "Corps", action_hint="Fais ceci")
    without_hint = ui_kit.empty_state("Titre", "Corps")
    assert "aca-empty__hint" in with_hint and "Fais ceci" in with_hint
    assert "aca-empty__hint" not in without_hint


# ── Chronologie d'un lead ─────────────────────────────────────────────────────────────────────
def test_timeline_vide_retombe_sur_empty_state():
    html = ui_kit.timeline([])
    assert "aca-empty" in html
    assert "aca-tl" not in html


def test_timeline_ordre_et_champs_optionnels():
    html = ui_kit.timeline([
        {"when": "10:00", "what": "Analyse lancée"},  # sans who/detail/tone
        {"when": "10:05", "who": "alice", "what": "Validé", "tone": "ok"},
    ])
    assert html.index("Analyse lancée") < html.index("Validé")
    assert "aca-tl__item--ok" in html
    # Le premier événement, sans `who`, ne doit pas produire un span vide.
    first_item_end = html.index("Analyse lancée") + len("Analyse lancée")
    assert "aca-tl__who" not in html[first_item_end:html.index("10:05")]


def test_timeline_item_sans_tone_nest_pas_suffixe():
    html = ui_kit.timeline([{"when": "10:00", "what": "Neutre"}])
    assert 'aca-tl__item">' in html
    assert "aca-tl__item--" not in html


# ── Différentiel de texte ─────────────────────────────────────────────────────────────────────
def test_diff_identique_dit_explicitement_aucun_changement():
    html = ui_kit.diff("même texte", "même texte")
    assert "aca-diff__none" in html
    assert "aca-diff__line" not in html


def test_diff_ajout_et_suppression_sont_distingues():
    html = ui_kit.diff("prix: 500 euros", "prix: 450 euros")
    assert "aca-diff__line--del" in html
    assert "aca-diff__line--add" in html
    assert "500" in html and "450" in html


def test_diff_tronque_au_dela_de_max_lines():
    original = "\n".join(f"ligne {i}" for i in range(100))
    edited = "\n".join(f"ligne modifiée {i}" for i in range(100))
    html = ui_kit.diff(original, edited, max_lines=5)
    assert "tronqué" in html


# ── Rappel des raccourcis ─────────────────────────────────────────────────────────────────────
def test_key_hints_rend_chaque_paire():
    html = ui_kit.key_hints([("V", "Valider"), ("R", "Rejeter")])
    assert "<kbd>V</kbd>" in html and "Valider" in html
    assert "<kbd>R</kbd>" in html and "Rejeter" in html


# ── Bloc de signature (§19) ───────────────────────────────────────────────────────────────────
def test_signoff_porte_le_signataire_et_le_moment():
    html = ui_kit.signoff("Entreprise Exemple", "operator", "03/08/2026 à 14:12")
    assert "Entreprise Exemple" in html
    assert "operator" in html
    assert "03/08/2026 à 14:12" in html
    assert "aca-signoff" in html


def test_signoff_enumere_les_effets_avant_le_geste():
    """
    Les effets ne sont pas décoratifs : valider sans savoir ce que ça écrit n'est pas un
    consentement éclairé, et c'est précisément la garantie que ce produit revendique.
    """
    html = ui_kit.signoff(
        "Lead", "operator", "maintenant",
        effects=[("cloud_upload", "Écriture CRM"), "Brouillon Gmail"],
    )
    assert "Écriture CRM" in html
    assert "Brouillon Gmail" in html


def test_signoff_echappe_un_nom_dentreprise_hostile():
    # Le nom vient du LLM, qui l'a extrait d'un e-mail entrant : non fiable par construction.
    html = ui_kit.signoff("<script>alert(1)</script>", "op", "maintenant")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_signoff_sans_effets_reste_valide():
    html = ui_kit.signoff("Lead", "op", "maintenant")
    assert "aca-signoff__effects" not in html
    assert "aca-signoff" in html


# ── Relevé (§19) ──────────────────────────────────────────────────────────────────────────────
def test_readout_rend_chaque_couple():
    html = ui_kit.readout([("Réception", "en marche", "on"), ("prochaine", "05/08 08:00", "")])
    assert "Réception" in html and "en marche" in html
    assert "05/08 08:00" in html


def test_readout_applique_le_ton_demande():
    assert "aca-readout__v--on" in ui_kit.readout([("k", "v", "on")])
    assert "aca-readout__v--off" in ui_kit.readout([("k", "v", "off")])
    assert "aca-readout__v--due" in ui_kit.readout([("k", "v", "due")])


def test_readout_ignore_un_ton_inconnu_sans_lever():
    html = ui_kit.readout([("k", "v", "fuchsia")])
    assert "aca-readout__v--fuchsia" not in html
    assert "v" in html


def test_readout_echappe_les_valeurs():
    html = ui_kit.readout([("<b>k</b>", "<i>v</i>")])
    assert "<b>" not in html and "<i>" not in html
    assert "&lt;b&gt;" in html


def test_readout_vide_ne_leve_pas():
    assert "aca-readout" in ui_kit.readout([])

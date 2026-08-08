"""
Tests de l'identité visuelle paramétrable (§17, `aca/core/branding.py`).

Priorité donnée aux propriétés qui coûteraient le plus cher à casser en clientèle, dans cet ordre :

1. **La résolution ne se trompe jamais de priorité** — un client qui choisit une couleur doit la
   voir, quel que soit le préréglage ou le mode ; l'inverse est un bug qu'on ne diagnostique pas au
   téléphone.
2. **Une valeur invalide ne descend jamais dans la feuille de style** — un `#XYZ` recopié de travers
   dans un `.env` ne doit pas produire du CSS cassé pour toute l'application.
3. **L'écriture de `config.toml` ne détruit pas la configuration de déploiement** — écraser un
   `[server]` casserait la mise en ligne, pas seulement l'apparence.
4. **Le logo est borné** — il est réinjecté à chaque rerun ; sans plafond, un PNG lourd rendrait
   l'application poussive sans que personne ne fasse le lien.

Aucun import Streamlit : `branding.py` est pur, ces tests tournent hors ligne comme le reste de la
suite.
"""
import re

import pytest

from aca.core import branding
from aca.storage import config_store


# ── Couleurs (fonctions pures) ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", ["#fff", "#FFFFFF", "#0078D4", "#abc"])
def test_hex_valide(value):
    assert branding.is_valid_hex(value)


@pytest.mark.parametrize("value", ["", None, "0078D4", "#XYZXYZ", "#12345", "rouge", "#00000000"])
def test_hex_invalide(value):
    assert not branding.is_valid_hex(value)


def test_texte_lisible_choisit_le_blanc_sur_fonce_et_le_noir_sur_clair():
    assert branding.readable_text_on("#0F4C81") == "#FFFFFF"
    assert branding.readable_text_on("#FFEB3B") == "#111111"


def test_contraste_wcag_connu():
    # Repères normatifs : noir sur blanc = 21:1, une couleur avec elle-même = 1:1.
    assert round(branding.contrast_ratio("#000000", "#FFFFFF")) == 21
    assert round(branding.contrast_ratio("#123456", "#123456")) == 1


def test_melange_borne_les_extremes():
    assert branding.mix("#000000", "#FFFFFF", 0) == "#000000"
    assert branding.mix("#000000", "#FFFFFF", 1) == "#FFFFFF"
    assert branding.mix("#000000", "#FFFFFF", 0.5) == "#808080"
    # Un ratio hors bornes est ramené dans [0, 1] au lieu de produire une couleur impossible.
    assert branding.mix("#000000", "#FFFFFF", 5) == "#FFFFFF"


def test_rgb_string_pour_composer_des_rgba():
    assert branding.rgb_string("#0078D4") == "0, 120, 212"


# ── Résolution des jetons ─────────────────────────────────────────────────────────────────────
def test_resolve_renvoie_tous_les_jetons_avec_les_defauts():
    tokens = branding.resolve()
    assert set(branding.TOKENS) <= set(tokens)
    assert tokens["BRAND_PRIMARY"] == branding.TOKENS["BRAND_PRIMARY"]["default"]
    assert tokens[branding.PRESET_KEY] == "ACA (défaut)"


def test_override_explicite_prime_sur_le_defaut():
    assert branding.resolve({"BRAND_PRIMARY": "#123456"})["BRAND_PRIMARY"] == "#123456"


def test_config_store_prime_sur_le_defaut_et_est_relu_a_chaud():
    """Le cœur de la promesse « paramétrable sans redémarrage » : rien n'est figé à l'import."""
    assert branding.resolve()["BRAND_NAME"] != "Acme Solutions"
    config_store.set_setting("BRAND_NAME", "Acme Solutions")
    try:
        assert branding.resolve()["BRAND_NAME"] == "Acme Solutions"
    finally:
        config_store.set_setting("BRAND_NAME", "")


def test_env_prime_sur_le_defaut_mais_pas_sur_config_store(monkeypatch):
    """Permet de livrer une image Docker déjà aux couleurs du client, sans base de réglages — tout
    en laissant l'administrateur reprendre la main depuis l'UI."""
    monkeypatch.setenv("BRAND_PRIMARY", "#AA0000")
    assert branding.resolve()["BRAND_PRIMARY"] == "#AA0000"
    config_store.set_setting("BRAND_PRIMARY", "#00BB00")
    try:
        assert branding.resolve()["BRAND_PRIMARY"] == "#00BB00"
    finally:
        config_store.set_setting("BRAND_PRIMARY", "")


def test_prereglage_applique_sa_palette():
    tokens = branding.resolve({branding.PRESET_KEY: "Azur corporate"})
    assert tokens["BRAND_PRIMARY"] == branding.PRESETS["Azur corporate"]["BRAND_PRIMARY"]


def test_couleur_choisie_nest_jamais_ecrasee_par_le_prereglage():
    """La règle qui évite l'appel client « j'ai mis notre rouge et il est resté bleu »."""
    tokens = branding.resolve({branding.PRESET_KEY: "Azur corporate", "BRAND_PRIMARY": "#FF0000"})
    assert tokens["BRAND_PRIMARY"] == "#FF0000"


def test_mode_sombre_change_les_defauts_de_fond_ET_de_texte():
    """Un mode sombre qui ne changerait que le fond donnerait du texte noir sur fond noir."""
    tokens = branding.resolve({"BRAND_MODE": "sombre"})
    assert branding.contrast_ratio(tokens["BRAND_TEXT"], tokens["BRAND_BACKGROUND"]) > 4.5


def test_mode_sombre_respecte_un_fond_explicite():
    tokens = branding.resolve({"BRAND_MODE": "sombre", "BRAND_BACKGROUND": "#012345"})
    assert tokens["BRAND_BACKGROUND"] == "#012345"


def test_couleur_invalide_retombe_sur_le_defaut_au_lieu_dentrer_dans_le_css():
    tokens = branding.resolve({"BRAND_PRIMARY": "pas-une-couleur"})
    assert tokens["BRAND_PRIMARY"] == branding.TOKENS["BRAND_PRIMARY"]["default"]
    assert "pas-une-couleur" not in branding.css(tokens)


def test_customised_tokens_ne_liste_que_les_ecarts():
    assert branding.customised_tokens(branding.resolve()) == {}
    custom = branding.customised_tokens(branding.resolve({"BRAND_PRIMARY": "#111111"}))
    assert list(custom) == ["BRAND_PRIMARY"]


# ── Feuille de style ──────────────────────────────────────────────────────────────────────────
def test_css_injecte_les_couleurs_choisies_en_variables():
    css = branding.css(branding.resolve({"BRAND_PRIMARY": "#123456", "BRAND_ACCENT": "#654321"}))
    assert "--aca-primary: #123456" in css
    assert "--aca-accent: #654321" in css
    assert css.startswith("<style>") and css.rstrip().endswith("</style>")


def test_niveau_complet_emet_les_animations():
    css = branding.css(branding.resolve({"BRAND_ANIMATIONS": "complet"}))
    assert "@keyframes aca-rise" in css
    assert "animation: aca-pop" in css


def test_niveau_aucune_nemet_aucune_animation():
    """« Aucune » ne doit pas émettre puis neutraliser : le navigateur composerait des couches
    inutiles à chaque rerun pour rien."""
    css = branding.css(branding.resolve({"BRAND_ANIMATIONS": "aucune"}))
    assert "@keyframes" not in css
    assert "animation:" not in css


def test_prefers_reduced_motion_est_toujours_present():
    """Le réglage applicatif ne peut jamais contredire le système d'exploitation : une personne
    ayant activé « réduire les animations » l'a souvent fait pour une raison médicale."""
    for level in ("complet", "sobre", "aucune"):
        assert "prefers-reduced-motion" in branding.css(
            branding.resolve({"BRAND_ANIMATIONS": level})
        )


def test_police_systeme_nappelle_aucun_cdn():
    """À choisir quand le réseau du client bloque les CDN — la promesse doit être tenue."""
    css = branding.css(branding.resolve({"BRAND_FONT": "Système"}))
    assert "fonts.googleapis.com" not in css


def test_police_google_est_importee():
    css = branding.css(branding.resolve({"BRAND_FONT": "Poppins"}))
    assert "fonts.googleapis.com" in css and "Poppins" in css


def test_densite_change_lespacement():
    compacte = branding.css(branding.resolve({"BRAND_DENSITY": "compacte"}))
    aeree = branding.css(branding.resolve({"BRAND_DENSITY": "aérée"}))
    assert "--aca-gap: 0.55rem" in compacte
    assert "--aca-gap: 1.5rem" in aeree


# ── En-tête de marque ─────────────────────────────────────────────────────────────────────────
def test_hero_affiche_le_nom_et_laccroche():
    html = branding.hero_html(branding.resolve({"BRAND_NAME": "Acme", "BRAND_TAGLINE": "Bonjour"}))
    assert "Acme" in html and "Bonjour" in html


def test_hero_echappe_le_html_saisi():
    """Le nom et l'accroche viennent d'un formulaire d'administration et finissent dans du HTML."""
    html = branding.hero_html(branding.resolve({"BRAND_NAME": "<script>alert(1)</script>"}))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_hero_echappe_aussi_les_pastilles():
    html = branding.hero_html(branding.resolve(), [("<b>x</b>", "normal")])
    assert "<b>x</b>" not in html


def test_hero_masque_ne_rend_rien():
    assert branding.hero_html(branding.resolve({"BRAND_HERO": "masqué"})) == ""


# ── Logo ──────────────────────────────────────────────────────────────────────────────────────
_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452")  # en-tête PNG, suffisant pour ces tests


def test_encode_puis_decode_rend_les_octets_dorigine():
    uri = branding.encode_logo("logo.png", _PNG)
    assert uri.startswith("data:image/png;base64,")
    assert branding.decode_logo(uri) == _PNG


def test_logo_refuse_un_format_non_gere():
    with pytest.raises(branding.LogoRejected):
        branding.encode_logo("logo.bmp", _PNG)


def test_logo_refuse_un_fichier_vide():
    with pytest.raises(branding.LogoRejected):
        branding.encode_logo("logo.png", b"")


def test_logo_refuse_un_fichier_trop_lourd():
    """Le logo est réinjecté à CHAQUE rerun : sans plafond, un PNG de plusieurs Mo alourdirait
    chaque interaction sans que personne ne fasse le lien avec l'apparence."""
    with pytest.raises(branding.LogoRejected):
        branding.encode_logo("logo.png", b"\x00" * (branding.MAX_LOGO_BYTES + 1))


def test_sans_logo_on_retombe_sur_une_icone_material():
    assert branding.logo_for_streamlit(branding.resolve()) == branding.DEFAULT_LOGO_ICON


def test_logo_configure_est_rendu_en_octets():
    tokens = branding.resolve({"BRAND_LOGO": branding.encode_logo("l.png", _PNG)})
    assert branding.logo_for_streamlit(tokens) == _PNG


def test_decode_logo_tolere_une_valeur_corrompue():
    assert branding.decode_logo("data:image/png;base64,pas-du-base64!!") is None
    assert branding.decode_logo("http://exemple.fr/logo.png") is None
    assert branding.decode_logo("") is None


# ── Thème natif (config.toml) ─────────────────────────────────────────────────────────────────
def test_config_toml_contient_les_couleurs_et_la_barre_laterale():
    toml = branding.config_toml(branding.resolve({"BRAND_PRIMARY": "#123456"}))
    assert 'primaryColor = "#123456"' in toml
    assert "[theme.sidebar]" in toml
    assert "chartCategoricalColors" in toml


def test_config_toml_traduit_le_mode_sombre():
    assert 'base = "dark"' in branding.config_toml(branding.resolve({"BRAND_MODE": "sombre"}))
    assert 'base = "light"' in branding.config_toml(branding.resolve({"BRAND_MODE": "clair"}))


def test_merge_preserve_les_sections_non_theme():
    """La propriété qui protège le déploiement : écraser `[server]` casserait la mise en ligne
    (cf. docs/DEPLOYMENT_HARDENING.md), pas seulement l'apparence."""
    existing = """[server]
port = 8501
enableCORS = false

[theme]
primaryColor = "#OLD"

[browser]
gatherUsageStats = false
"""
    merged = branding.merge_config_toml(existing, branding.config_toml(branding.resolve()))
    assert "[server]" in merged and "port = 8501" in merged
    assert "[browser]" in merged and "gatherUsageStats = false" in merged
    assert "#OLD" not in merged


def test_merge_est_idempotent():
    """
    Appliquer deux fois de suite doit donner exactement le même fichier : empiler deux `[theme]`
    rendrait la configuration ambiguë (Streamlit ne lirait qu'une des deux valeurs), et empiler
    l'en-tête de commentaires finirait par produire un fichier surtout composé d'en-têtes périmés.

    On compare le résultat entier plutôt que de compter le texte « [theme] » : ce dernier apparaît
    aussi dans le commentaire d'en-tête généré, ce qui rendait un simple `count()` trompeur.
    """
    theme = branding.config_toml(branding.resolve())
    once = branding.merge_config_toml("", theme)
    twice = branding.merge_config_toml(once, theme)
    assert once == twice
    assert re.findall(r"^\[.*\]$", twice, re.M) == ["[theme]", "[theme.sidebar]"]


def test_merge_nempile_pas_les_commentaires_dune_section_theme_precedente():
    """Un commentaire qui documente `[theme]` doit partir avec `[theme]`, même s'il suit une autre
    section (auquel cas une logique « ignorer les commentaires du début » le conserverait)."""
    existing = """[server]
port = 8501

# Réglé par le prestataire le 12/03 — NE PAS TOUCHER
[theme]
primaryColor = "#OLD"
"""
    merged = branding.merge_config_toml(existing, branding.config_toml(branding.resolve()))
    assert "NE PAS TOUCHER" not in merged
    assert "port = 8501" in merged


def test_write_config_toml_cree_larborescence(tmp_path):
    cible = tmp_path / "sous" / "dossier" / "config.toml"
    branding.write_config_toml(branding.resolve({"BRAND_PRIMARY": "#ABCDEF"}), str(cible))
    assert 'primaryColor = "#ABCDEF"' in cible.read_text(encoding="utf-8")


def test_write_config_toml_preserve_un_fichier_existant(tmp_path):
    cible = tmp_path / "config.toml"
    cible.write_text("[server]\nheadless = true\n", encoding="utf-8")
    branding.write_config_toml(branding.resolve(), str(cible))
    contenu = cible.read_text(encoding="utf-8")
    assert "headless = true" in contenu and "[theme]" in contenu


# ── Accessibilité ─────────────────────────────────────────────────────────────────────────────
def test_rapport_accessibilite_vide_sur_la_palette_par_defaut():
    assert branding.accessibility_report(branding.resolve()) == []


def test_rapport_accessibilite_signale_un_texte_trop_pale():
    tokens = branding.resolve({"BRAND_TEXT": "#CCCCCC", "BRAND_BACKGROUND": "#FFFFFF"})
    assert any("contraste" in problem for problem in branding.accessibility_report(tokens))


def test_rapport_accessibilite_signale_des_cartes_invisibles():
    tokens = branding.resolve({"BRAND_SURFACE": "#FFFFFF", "BRAND_BACKGROUND": "#FFFFFF"})
    assert any("cartes" in problem for problem in branding.accessibility_report(tokens))


def test_rapport_accessibilite_avertit_sans_jamais_bloquer():
    """C'est la charte du client : on prévient, on ne refuse pas — un produit qui interdit la
    charte graphique de son client se fait remplacer."""
    tokens = branding.resolve({"BRAND_TEXT": "#EEEEEE", "BRAND_BACKGROUND": "#FFFFFF"})
    assert branding.accessibility_report(tokens)          # un avertissement est bien émis…
    assert "--aca-text: #EEEEEE" in branding.css(tokens)  # …et la couleur est quand même appliquée


# ── Graphiques ────────────────────────────────────────────────────────────────────────────────
def test_palette_de_graphiques_commence_par_la_couleur_principale():
    tokens = branding.resolve({"BRAND_PRIMARY": "#123456"})
    palette = branding.chart_colors(tokens)
    assert palette[0] == "#123456"
    assert all(branding.is_valid_hex(color) for color in palette)


def _declaration(css: str, selecteur: str) -> str:
    """
    Corps d'une règle CSS, commentaires retirés.

    Une première version de ces tests découpait simplement 700 caractères après le sélecteur ; les
    commentaires explicatifs de la feuille (volontairement longs — ils portent le « pourquoi » de
    chaque choix) remplissaient toute la fenêtre et le test n'atteignait jamais la déclaration.
    Retirer les commentaires d'abord rend l'assertion indépendante de leur longueur.
    """
    nettoye = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    debut = nettoye.index(selecteur) + len(selecteur)
    return nettoye[debut:nettoye.index("}", debut)]


# ── §21 — Ce que la passe de design a corrigé, et que rien ne surveillait ──────────────────────
#
# Chacun des défauts ci-dessous vivait dans une feuille de style RELUE plusieurs fois : ils n'ont
# été trouvés qu'en mesurant le DOM réellement rendu. Une règle CSS morte ne lève aucune exception,
# ne casse aucun test et ne se voit pas à la lecture — d'où ces tests, qui vérifient la feuille
# émise plutôt que l'intention qu'on avait en l'écrivant.

def test_le_titrage_serif_atteint_reellement_les_titres_de_page():
    """
    Régression : `h1, h2, h3 { font-family: var(--aca-display) }` a une spécificité de (0,0,1) et
    perdait contre la règle interne de Streamlit `.st-emotion-cache-XXXX h1, … h6` (0,1,1). Mesuré
    sur le DOM : un `h3` de page calculait « Segoe UI », jamais la serif. La moitié de la thèse
    typographique de §19 n'existait donc que dans le fichier.
    """
    css = branding.css(branding.resolve())
    assert '[data-testid="stMarkdownContainer"] h3' in css
    assert '[data-testid="stHeadingWithActionElements"] h3' in css
    # Sans `!important` : une montée de version doit pouvoir reprendre la main proprement.
    assert "var(--aca-display) !important" not in css


def test_la_barre_den_tete_reste_opaque():
    """
    Régression : une règle `[data-testid="stHeader"] { background: transparent; }` traînait en fin
    de bloc et annulait, à spécificité égale, le fond opaque posé par §19 — le contenu défilait donc
    sous un filet flottant sans matière derrière. Relevé sur le DOM : `rgba(0, 0, 0, 0)`.
    """
    css = branding.css(branding.resolve())
    assert '[data-testid="stHeader"] { background: transparent; }' not in css
    assert "background: var(--aca-bg)" in css


def test_la_hauteur_den_tete_est_en_pixels():
    """
    `3.5rem` supposait une racine à 16 px, alors que `config.toml` fixe `baseFontSize = 14` : la
    variable censée DÉCRIRE la hauteur de la barre (52,5 px mesurés) n'en valait que 49.
    """
    css = branding.css(branding.resolve())
    assert re.search(r"--aca-header-h:\s*\d+px", css)
    assert "--aca-header-h: 3.5rem" not in css


@pytest.mark.parametrize("preset", list(branding.PRESETS))
def test_le_gris_secondaire_reste_lisible_sur_toutes_les_palettes(preset):
    """`--aca-muted` porte les accroches, les relevés et les libellés d'indicateurs, c'est-à-dire du
    texte petit : exactement la taille pour laquelle le seuil AA de 4,5:1 existe."""
    tokens = branding.resolve({branding.PRESET_KEY: preset})
    muted = branding.mix(tokens["BRAND_TEXT"], tokens["BRAND_BACKGROUND"], 0.34)
    assert branding.contrast_ratio(muted, tokens["BRAND_SURFACE"]) >= 4.5
    assert branding.contrast_ratio(muted, tokens["BRAND_BACKGROUND"]) >= 4.5


# ── Le signal de décision ─────────────────────────────────────────────────────────────────────
def test_separation_de_signal_ignore_la_clarte_et_retient_la_teinte():
    """
    La propriété qui compte, et celle qu'une mesure de contraste WCAG donnait à l'envers : deux
    bleus de clartés différentes restent « du bleu » (donc aucun signal), tandis que pétrole et
    ambre se distinguent immédiatement malgré un contraste de luminance faible (1,66:1).
    """
    assert branding.signal_separation("#125E6B", "#125E6B") == 0.0
    assert branding.signal_separation("#0F4C81", "#3E8FD0") < 0.25   # bleu foncé / bleu clair
    assert branding.signal_separation("#125E6B", "#B4622A") > 0.6    # pétrole / ambre
    # Un neutre n'a pas de teinte : c'est sa distance à l'origine chromatique qui le sépare.
    assert branding.signal_separation("#171717", "#A8874B") > 0.25


@pytest.mark.parametrize("preset", list(branding.PRESETS))
def test_chaque_palette_livree_conserve_un_signal_de_decision(preset):
    """
    L'invariant visuel central du produit : l'accent ne sert QU'À signaler ce qui attend une
    validation humaine. Quatre palettes livrées le perdaient (turquoise sur turquoise, bleu clair
    sur bleu foncé) — l'écran restait beau et n'indiquait plus où agir.
    """
    tokens = branding.resolve({branding.PRESET_KEY: preset})
    separation = branding.signal_separation(tokens["BRAND_PRIMARY"], tokens["BRAND_ACCENT"])
    assert separation >= 0.25, f"{preset} : accent indistinguable de la couleur principale"


def test_le_rapport_accessibilite_signale_un_accent_noye():
    tokens = branding.resolve({"BRAND_PRIMARY": "#0F4C81", "BRAND_ACCENT": "#3E8FD0"})
    assert any("accent" in problem.lower() for problem in branding.accessibility_report(tokens))


@pytest.mark.parametrize("preset", list(branding.PRESETS))
def test_la_palette_de_graphiques_na_aucun_doublon(preset):
    """
    Rien n'oblige les jetons sémantiques à être distincts — dans la palette par défaut `BRAND_INFO`
    vaut `BRAND_PRIMARY` et `BRAND_WARNING` vaut `BRAND_ACCENT`, ce qui est JUSTE quant au sens.
    Aplati en palette catégorielle, cela dessinait deux catégories du tableau de bord dans la même
    couleur. Une palette catégorielle a une exigence propre : chaque série doit être séparable.
    """
    tokens = branding.resolve({branding.PRESET_KEY: preset})
    palette = branding.chart_colors(tokens)
    assert len(palette) == len({color.upper() for color in palette})


# ── Mouvement ─────────────────────────────────────────────────────────────────────────────────
def test_aucune_animation_dinterface_ne_depasse_300ms():
    """
    Au-delà d'environ 300 ms, un mouvement cesse d'être perçu comme une réponse et devient une
    attente. Les boucles délibérément lentes (pouls, lueur d'alerte, dérive de l'en-tête, reflet)
    sont exclues : elles signalent un état continu, elles n'accompagnent pas une action.
    """
    css = branding.css(branding.resolve())
    # `aca-ring` rejoint la liste : c'est la respiration de l'étape EN COURS du rail de décision,
    # un indicateur d'état continu au même titre que le pouls ou la lueur d'alerte — pas
    # l'accompagnement d'un geste, donc la borne des 300 ms ne s'y applique pas.
    # `aca-grain` rejoint la liste pour la même raison que `aca-ambient` : c'est le fond, pas une
    # réponse à un geste. §26.2 lui a donné sa propre boucle (26 s au lieu des 48 s partagées) parce
    # qu'une texture répétée tous les 96 px, translatée de 1,7 px/s, se lit comme immobile.
    lentes = ("aca-halo", "aca-warn-glow", "aca-drift", "aca-float", "aca-sheen", "aca-progress",
              "aca-ring", "aca-ambient", "aca-grain")
    for ligne in css.splitlines():
        if "animation:" not in ligne or any(nom in ligne for nom in lentes):
            continue
        for duree in re.findall(r"(\d*\.?\d+)s", ligne):
            assert float(duree) <= 0.3, f"animation trop longue : {ligne.strip()}"


def test_le_fond_dambiance_nemprunte_jamais_la_couleur_reservee():
    """
    La contrainte qui compte sur ce fond, et la raison pour laquelle il est admissible : l'ambre ne
    signifie qu'une chose dans toute l'application — « une personne doit trancher ». Un décor qui
    l'emploierait viderait le signal de son sens, c'est-à-dire referait le défaut corrigé sur quatre
    palettes le même jour.
    """
    # §26.3 : la couleur est désormais CUITE en hexadécimal au lieu d'être `var(--aca-primary)`,
    # parce qu'elle doit aussi entrer dans une `data:` URI, qui ne peut lire aucune variable CSS.
    # Ce qui est protégé reste identique — le fond PAR DÉFAUT prend la primaire et jamais l'accent —
    # mais cela se vérifie sur les valeurs et non plus sur les noms de variables.
    jetons = branding.resolve()
    couche = _declaration(branding.css(jetons),
                          '[data-testid="stAppViewContainer"]::before')
    assert jetons["BRAND_PRIMARY"].lower() in couche.lower()
    assert jetons["BRAND_ACCENT"].lower() not in couche.lower()

    # Le réglage explicite, lui, appartient au client : s'il désigne une couleur, elle est
    # respectée. Le garde-fou porte sur le DÉFAUT, pas sur un choix fait en connaissance de cause —
    # une décoration imposée contre un choix explicite serait un autre défaut.
    choisi = dict(jetons)
    choisi["BRAND_AMBIENT_COLOR"] = "#C21807"
    assert "#c21807" in _declaration(
        branding.css(choisi), '[data-testid="stAppViewContainer"]::before').lower()


def test_le_fond_dambiance_se_dimensionne_sur_la_fenetre():
    """
    Régression signalée par l'utilisateur (« je ne vois pas l'animation de fond ») et confirmée à la
    mesure : les rayons étaient exprimés en `rem`, donc figés à 588 px (racine à 14 px imposée par
    `config.toml`). Sur un écran de 1892 px, six points de fond sur sept restaient rigoureusement
    intacts et un seul coin portait la couleur. Une décoration de fond se mesure à la FENÊTRE, pas à
    la taille du texte.
    """
    couche = _declaration(branding.css(branding.resolve()),
                          '[data-testid="stAppViewContainer"]::before')
    assert "vmax" in couche
    assert "rem" not in couche


def test_le_fond_dambiance_reste_derriere_le_contenu():
    """Le voile est en `position: fixed` dans le même contexte d'empilement que la page : sans la
    règle de superposition explicite il passerait DEVANT elle. C'est structurel, pas décoratif."""
    css = branding.css(branding.resolve())
    assert "pointer-events: none" in css
    assert re.search(r'\[data-testid="stMain"\][^{]*\{[^}]*z-index: 1', css)


def test_le_fond_dambiance_ne_bouge_quau_niveau_complet():
    """Le dégradé (profondeur) reste à tous les niveaux ; seul le MOUVEMENT est conditionnel — un
    client qui a demandé le calme garde un fond agréable au lieu d'un aplat."""
    for niveau in ("complet", "sobre", "aucune"):
        css = branding.css(branding.resolve({"BRAND_ANIMATIONS": niveau}))
        assert '[data-testid="stAppViewContainer"]::before' in css
        assert ("animation: aca-ambient" in css) is (niveau == "complet")


def test_le_fond_dambiance_est_compose_par_le_gpu():
    """Seule animation de la feuille qui ne s'arrête jamais : elle ne doit déclencher ni calcul de
    disposition ni repeinture. `transform` seul le garantit, `background-position` ne l'aurait pas
    fait."""
    css = branding.css(branding.resolve())
    corps = css[css.index("@keyframes aca-ambient"):]
    corps = corps[:corps.index("}\n")]
    assert "transform:" in corps
    assert "background-position" not in corps


def test_les_courbes_sont_des_jetons_partages_et_jamais_ease_in():
    css = branding.css(branding.resolve())
    assert "--aca-ease-out:" in css and "--aca-ease-in-out:" in css
    # `ease-in` démarre lentement : il retarde exactement l'instant que l'œil regarde le plus.
    assert not re.search(r":\s*ease-in[;\s,]", css)


def test_les_boutons_donnent_un_retour_dappui():
    """Un clic déclenche ici un rerun Streamlit complet : l'enfoncement est la seule confirmation
    disponible avant que le serveur réponde."""
    assert "scale(.97)" in branding.css(branding.resolve())


def test_le_survol_est_reserve_aux_pointeurs_fins():
    """Sur tactile, `:hover` se déclenche au toucher et RESTE actif : le bouton qu'on vient
    d'utiliser garde l'air sélectionné."""
    assert "@media (hover: hover) and (pointer: fine)" in branding.css(branding.resolve())


def test_le_clavier_voit_ou_il_se_trouve():
    """Sur l'écran de validation, ne pas voir le focus revient à ne pas savoir quel bouton on est
    sur le point d'actionner."""
    css = branding.css(branding.resolve())
    assert ":focus-visible" in css
    assert "outline: 2px solid var(--aca-primary)" in css


def test_la_page_courante_est_plus_marquee_que_le_survol():
    """
    Relevé sur le DOM : l'onglet actif recevait un gris de Streamlit (`rgba(173,173,173,.25)`)
    pendant que le survol recevait la couleur de marque, une élévation et une ombre — la hiérarchie
    était donc inversée sur une barre à sept entrées.
    """
    css = branding.css(branding.resolve())
    # On découpe sur le SÉLECTEUR, pas sur le mot : « aria-current » apparaît d'abord dans le
    # commentaire qui explique la règle, et une première version de ce test inspectait donc la prose
    # au lieu du CSS.
    selecteur = '[data-testid="stTopNavLink"][aria-current]'
    assert selecteur in css
    actif = css.split(selecteur)[1][:300]
    # §25 : l'onglet actif utilise désormais `--aca-nav-active`, une primaire éclaircie de 26 %,
    # la primaire pure ayant été jugée trop sombre en pleine surface. Ce qui est vérifié reste la
    # MÊME propriété qu'avant — l'état courant porte un aplat dérivé de la marque là où le survol
    # se contente d'une teinte pâle — et non la valeur exacte : le test protège la hiérarchie entre
    # les deux états, pas une couleur qu'on doit pouvoir ajuster sans le réécrire.
    assert "background: var(--aca-nav-active)" in actif
    assert "--aca-nav-active:" in css and "--aca-nav-active-text:" in css


# ══ §26 — FOND D'AMBIANCE EN BLOCS ══════════════════════════════════════════════════════════════
# La page de présentation et l'application vendaient le même produit dans deux langages visuels
# sans rapport. Ces tests protègent la couche qui les réunit — et surtout les deux façons dont elle
# peut disparaître SANS que rien ne lève : une `url()` mal encodée, et une règle non analysée.


def test_les_commentaires_css_sont_tous_refermes():
    """
    Le garde-fou le plus rentable de ce fichier, et il vient d'un défaut commis DEUX FOIS dans la
    même passe : en rallongeant un commentaire existant, un `*/` s'est retrouvé au milieu du bloc,
    le refermant trop tôt et laissant de la prose dans la feuille de style. Le navigateur l'ignore,
    la page s'affiche, et le seul symptôme est une règle « qui ne s'applique pas ».

    On compte les délimiteurs sur la feuille ÉMISE, pas sur le source : c'est elle que le
    navigateur lit, et c'est là que les f-strings ont fini de tout assembler.
    """
    css = branding.css(branding.resolve())
    assert css.count("/*") == css.count("*/"), (
        f"{css.count('/*')} ouvertures pour {css.count('*/')} fermetures"
    )
    # Et aucune fermeture ne doit précéder son ouverture : un compte égal peut cacher un `*/`
    # orphelin suivi d'un `/*` plus loin, ce qui est exactement la forme du défaut rencontré.
    profondeur = 0
    for i in range(len(css) - 1):
        if css[i:i + 2] == "/*" and profondeur == 0:
            profondeur = 1
        elif css[i:i + 2] == "*/" and profondeur == 1:
            profondeur = 0
        elif css[i:i + 2] == "*/" and profondeur == 0:
            raise AssertionError(f"fermeture orpheline vers : {css[max(0, i - 90):i + 2]!r}")


def test_la_texture_de_fond_est_posee_et_masquee():
    """La couche existe, porte ses deux calques et n'est visible que là où sont les voiles."""
    css = branding.css(branding.resolve())
    assert '[data-testid="stAppViewContainer"]::after' in css
    regle = css.split('[data-testid="stAppViewContainer"]::after')[1].split("}")[0]
    assert regle.count("url(") == 2, "deux échelles, sinon le motif redevient un quadrillage"
    assert "mask-image:" in regle and "-webkit-mask-image:" in regle
    assert "position: fixed" in regle and "z-index: 0" in regle


def test_le_diese_de_la_couleur_est_encode_dans_la_data_uri():
    """
    Laissé tel quel dans une `url()`, le `#` d'un code hexadécimal est lu comme un fragment
    d'adresse : la tuile entière disparaît, sans erreur, sans test rouge, sans rien à voir en
    relecture. C'est le mode de panne le plus probable de toute cette couche.
    """
    css = branding.css(branding.resolve())
    uri = re.search(r'url\("(data:image/svg\+xml,[^"]+)"\)', css)
    assert uri, "aucune data: URI émise"
    assert "#" not in uri.group(1)
    assert "%23" in uri.group(1)


def test_la_texture_emprunte_la_primaire_et_jamais_l_accent():
    """
    Règle du §21, reprise telle quelle : l'ambre ne signifie qu'une chose dans toute
    l'application, « une personne doit trancher ». Un fond décoratif qui l'utiliserait viderait
    le signal — le défaut corrigé sur quatre palettes ce jour-là.
    """
    from urllib.parse import unquote

    jetons = branding.resolve()
    css = branding.css(jetons)
    uri = re.search(r'url\("(data:image/svg\+xml,[^"]+)"\)', css).group(1)
    svg = unquote(uri.split(",", 1)[1])
    assert jetons["BRAND_PRIMARY"].lower() in svg.lower()
    assert jetons["BRAND_ACCENT"].lower() not in svg.lower()


def test_la_texture_survit_a_une_couleur_invalide():
    """
    Une décoration n'a pas à décider si un fond existe. Leçon du §20 : une coquille dans un
    hexadécimal a fait lever `_Palette`, `build_report_pdf` a rendu `None` conformément à son
    contrat, et le rapport mensuel a cessé d'être produit en silence.
    """
    # Visé sur `_ambient_texture` et non sur `css()` : `resolve()` valide déjà chaque couleur et
    # retombe sur le défaut, donc un hexadécimal corrompu ne peut PAS arriver jusqu'ici par le
    # chemin normal — une première version de ce test forçait un jeton invalide après `resolve()`
    # et échouait dans `_variables`, c'est-à-dire ailleurs que dans ce qu'elle prétendait vérifier.
    # Ce qui est protégé ici, c'est le garde-fou de la couche elle-même, pour un appelant qui
    # construirait ses jetons à la main.
    couche = branding._ambient({"BRAND_PRIMARY": "pas-une-couleur"})
    assert '[data-testid="stAppViewContainer"]::after' in couche
    assert "data:image/svg+xml," in couche
    # Et une couleur de fond corrompue ne doit pas non plus emporter la couche.
    abime = branding._ambient({"BRAND_PRIMARY": "#125E6B", "BRAND_AMBIENT_COLOR": "rouge vif"})
    assert "data:image/svg+xml," in abime


def test_chaque_style_de_fond_emet_ses_propres_couches():
    """
    Cinq styles, et chacun doit produire EXACTEMENT ses couches — pas une de plus, sinon deux
    décors se superposent, ni une de moins, sinon le réglage ne fait rien de visible.
    """
    attendu = {
        # style        voile, trame, quadrillage, filet
        "particules": (True,  True,  False, False),
        "voile":      (True,  False, False, False),
        "grille":     (True,  False, True,  False),
        "cadre":      (True,  False, False, True),
        "aucun":      (False, False, False, False),
    }
    for style, (veil, tile, grid, frame) in attendu.items():
        jetons = dict(branding.resolve())
        jetons["BRAND_AMBIENT"] = style
        css = branding.css(jetons)
        assert ("radial-gradient(78vmax" in css) is veil, style
        assert ("data:image/svg+xml" in css) is tile, style
        assert ("repeating-linear-gradient(0deg" in css) is grid, style
        assert ("inset 0 0 0 1px color-mix" in css) is frame, style

    # Une valeur inconnue (jeton recopié à la main, base migrée) retombe sur le défaut plutôt que
    # de supprimer le fond : le contrat « absent = fonctionnalité ignorée » de ce projet ne dit pas
    # « invalide = écran nu ».
    jetons = dict(branding.resolve())
    jetons["BRAND_AMBIENT"] = "papier peint"
    assert "data:image/svg+xml" in branding.css(jetons)


def test_lintensite_du_fond_agit_sur_toutes_les_couches():
    """
    Un seul curseur, sinon on obtient des combinaisons où le dégradé crie pendant que le grain
    chuchote. On vérifie l'ordre, pas les valeurs : ce sont des chiffres à pouvoir ajuster sans
    réécrire le test.
    """
    valeurs = []
    for niveau in ("discret", "normal", "marqué"):
        jetons = dict(branding.resolve())
        jetons["BRAND_AMBIENT_INTENSITY"] = niveau
        css = branding.css(jetons)
        from urllib.parse import unquote

        voile = float(re.search(r"color-mix\(in srgb, #\w+ ([\d.]+)%", css).group(1))
        grain = float(re.search(r"fill-opacity='([\d.]+)'", unquote(
            re.search(r'url\("(data:image/svg\+xml,[^"]+)"\)', css).group(1))).group(1))
        valeurs.append((voile, grain))
    assert valeurs[0][0] < valeurs[1][0] < valeurs[2][0], valeurs
    assert valeurs[0][1] < valeurs[1][1] < valeurs[2][1], valeurs


def test_le_mouvement_du_fond_est_borne_au_niveau_complet():
    """
    La matière reste à tous les niveaux, seule la DÉRIVE est conditionnelle : « animations :
    aucune » demande le calme, pas un aplat. Et les deux couches doivent partager la même boucle,
    sinon elles glissent l'une sur l'autre et se lisent comme deux calques.
    """
    for niveau in ("aucune", "sobre", "complet"):
        jetons = dict(branding.resolve())
        jetons["BRAND_ANIMATIONS"] = niveau
        css = branding.css(jetons)
        assert '[data-testid="stAppViewContainer"]::after' in css, niveau
        # `animation: aca-ambient`, pas `aca-ambient` seul : le bloc `@keyframes` est émis dès que
        # les animations ne sont pas coupées, donc chercher le nom nu voyait la DÉFINITION et
        # concluait que la boucle tournait au niveau « sobre », où elle ne tourne pas.
        # Les DEUX couches doivent s'animer, ou aucune : un fond dont seule la moitié bouge se
        # lit comme un calque qui glisse sur un autre.
        anime = "animation: aca-ambient" in css
        assert anime is (niveau == "complet"), niveau
        assert ("animation: aca-grain" in css) is (niveau == "complet"), niveau
    complet = branding.css(branding.resolve())
    # §26.2 : boucles SÉPARÉES, et c'est le correctif. Partagée, la dérive était mesurée comme
    # imperceptible sur la texture — ce test protège donc que chaque pseudo-élément ait bien la
    # sienne, et non plus qu'ils partagent la même règle.
    avant = complet.split("animation: aca-ambient")[0].rstrip()
    assert avant.endswith('[data-testid="stAppViewContainer"]::before {'), avant[-90:]
    apres = complet.split("animation: aca-grain")[0].rstrip()
    assert apres.endswith('[data-testid="stAppViewContainer"]::after {'), apres[-90:]


def test_la_tuile_est_deterministe_et_sans_axe():
    """
    Reproductible d'un appel et d'une machine à l'autre — aucun `random`, aucun état. Et pas de
    rangée pleine ni de rangée vide : c'est ce que produisait la matrice de Bayer seuillée à une
    seule valeur (rangées de 4, 2, 4, 0 cellules), d'où un tissage à coutures visibles.
    """
    a = branding._scatter_cells(16, 0.3)
    b = branding._scatter_cells(16, 0.3)
    assert a == b and 40 < len(a) < 120
    par_ligne = [sum(1 for x, y in a if y == ligne) for ligne in range(16)]
    par_colonne = [sum(1 for x, y in a if x == colonne) for colonne in range(16)]
    # Ni bande vide ni bande pleine, dans les deux sens. Le premier tirage laissait une rangée
    # vide, et une rangée vide dans une tuile répétée tous les 96 px se lit comme une couture.
    assert min(par_ligne) >= 2 and max(par_ligne) < 16, par_ligne
    assert min(par_colonne) >= 2 and max(par_colonne) < 16, par_colonne

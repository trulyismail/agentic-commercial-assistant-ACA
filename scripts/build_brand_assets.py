# -*- coding: utf-8 -*-
"""
Génération des fichiers de marque acami (§28, corrigé §29).

Produit, à partir de DEUX fichiers sources et d'une recoloration — jamais d'une géométrie calculée :

    static/brand/acami-mark.svg          l'étoile seule, encre pleine
    static/brand/acami-mark-mono.svg     l'étoile en « currentColor » (une seule encre)
    static/brand/acami-lockup.svg        étoile + mot-symbole, fonds clairs
    static/brand/acami-lockup-dark.svg   étoile + mot-symbole, fonds sombres
    static/brand/acami-favicon-32.png    icône d'onglet
    static/brand/acami-favicon-180.png   apple-touch-icon
    static/brand/acami-og.png            carte de partage 1200x630

── §29 — CORRECTION : LE TRACÉ EST FOURNI, PLUS RECONSTRUIT ─────────────────────────────────────
La version précédente de ce script CALCULAIT l'étoile (quatre Béziers paramétrées par `f`/`ratio`)
et DESSINAIT le mot « acami » à la main (cercles, fûts, arcs) — un pis-aller assumé et documenté
comme tel : « la police exacte de l'artwork fourni n'est pas connue [...] une reconstruction
géométrique assumée vaut mieux qu'une police approchante présentée comme la bonne — et elle se
corrige ici, en un seul endroit, le jour où la police d'origine est fournie. »

Ce jour est arrivé, et la correction a eu lieu ici : l'utilisateur a fourni le tracé vectoriel réel
de son artwork (exporté en SVG depuis l'image source), déposé sans modification dans :

    static/brand/star.svg    l'étoile seule
    static/brand/acami.svg   étoile + mot-symbole déjà assemblés (le lockup complet)

Ces deux fichiers sont la SEULE source — ce script ne fait plus que les RECOLORER (une substitution
de `fill="#000000"`, rien d'autre) pour produire les variantes ci-dessus. Aucune coordonnée n'est
plus jamais calculée ni recopiée à la main, donc l'erreur qui a motivé la première version de ce
script (un point de contrôle mal recopié, invisible à l'œil) ne peut plus se reproduire : il n'y a
plus de coordonnées à recopier du tout. Les `<svg>` sources sont un export brut (potrace/Illustrator,
d'où le `<g transform="translate(...) scale(...)">` qui les enveloppe) ; ce script ne recadre PAS le
`viewBox` à chaque génération — c'est fait une fois, à la main, quand le fichier source change (voir
la note de tête de `static/brand/star.svg`/`acami.svg`), pour que le tracé lui-même reste identique
d'une régénération à l'autre.

── PALETTE ───────────────────────────────────────────────────────────────────────────────────────
L'artwork fourni est monochrome, donc l'identité d'acami l'est aussi : encre et papier. L'ambre
reste la seule couleur d'accent et garde le sens que le produit lui donne depuis §19 — *une
personne doit trancher ici*. Aucune valeur nouvelle : les quatre viennent des jetons existants et
de `static/landing.html`.

Lancement : `python scripts/build_brand_assets.py`
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "static", "brand")

sys.path.insert(0, ROOT)

import fitz  # noqa: E402  (PyMuPDF — déjà épinglé pour la lecture/écriture de PDF)

# ── Palette ───────────────────────────────────────────────────────────────────────────────────
INK = "#12171C"       # = BRAND_TEXT (aca/core/branding.py)
PAPER = "#FDFDFD"     # = la variable CSS « paper » de static/landing.html
ACCENT = "#B4622A"    # = BRAND_ACCENT, et la variable « signal » : réservée à la décision humaine
MUTED = "#636363"     # = la variable CSS « ink-3 » de static/landing.html
INK_DARK = "#FDFDFD"  # l'encre sur fond sombre

_FILL_RE = re.compile(r'fill="#000000"')


def _recolored_artwork(filename: str, ink: str) -> str:
    """
    Contenu intégral d'un fichier source de `static/brand/` (le tracé RÉEL fourni par
    l'utilisateur — voir l'en-tête du module), avec son unique remplissage noir substitué par
    `ink`. Aucune coordonnée n'est touchée : recolorer n'est pas redessiner.
    """
    with open(os.path.join(OUT_DIR, filename), encoding="utf-8") as handle:
        text = handle.read()
    recolored, count = _FILL_RE.subn(f'fill="{ink}"', text)
    if count != 1:
        raise ValueError(
            f"{filename}: attendu exactement un fill(#000000) à recolorer, trouvé {count} — "
            "le fichier source a peut-être changé de forme, revoir _recolored_artwork()."
        )
    return recolored


def _mark_svg(ink: str) -> str:
    """L'étoile seule — tracé réel de `static/brand/star.svg`, recoloré."""
    return _recolored_artwork("star.svg", ink)


def _lockup_svg(ink: str) -> str:
    """Étoile + mot-symbole — tracé réel de `static/brand/acami.svg` (déjà assemblés), recoloré."""
    return _recolored_artwork("acami.svg", ink)


# ── Rastérisation ─────────────────────────────────────────────────────────────────────────────
def _rgb(hex_color: str) -> tuple:
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _tint(fg: str, bg: str, alpha: float) -> str:
    """
    `fg` aplati à l'opacité `alpha` sur `bg`, en hexadécimal.

    Aplatir plutôt que poser une opacité : le PNG a un fond connu et définitif, donc le résultat ne
    dépend d'aucun réglage de transparence du rastériseur et reste reproductible d'une exécution à
    l'autre — ce dont dépend le contrôle de dérive en CI.
    """
    front, back = _rgb(fg), _rgb(bg)
    mixed = (round(255 * (f * alpha + b * (1 - alpha))) for f, b in zip(front, back))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def _svg_to_pdf(svg_text: str) -> fitz.Document:
    """SVG -> document PDF d'une page, pour pouvoir le poser dans une autre page."""
    source = fitz.open("svg", svg_text.encode("utf-8"))
    return fitz.open("pdf", source.convert_to_pdf())


def _favicon(size: int, round_disc: bool = True) -> fitz.Pixmap:
    """
    L'étoile sur un disque papier.

    Le disque reste OPAQUE (une étoile pleine sur transparence disparaît sur un onglet sombre — et
    c'est exactement la taille à laquelle l'icône doit rester reconnaissable) ; seuls les quatre
    coins hors du cercle sont transparents, ce qui est ce qui fait qu'un navigateur (et les icônes
    de raccourci macOS/Windows) le traite comme rond plutôt que comme un carré plein. `round_disc`
    reste réglable car l'apple-touch-icon, lui, doit rester un carré plein : iOS applique déjà son
    propre masque arrondi à cette icône-là et remplit les coins transparents en noir — un disque
    prédécoupé s'y superposerait mal plutôt que de l'améliorer.
    """
    document = fitz.open()
    page = document.new_page(width=size, height=size)
    if round_disc:
        page.draw_circle(fitz.Point(size / 2, size / 2), size / 2, color=None, fill=_rgb(PAPER))
    else:
        page.draw_rect(fitz.Rect(0, 0, size, size), color=None, fill=_rgb(PAPER))
    inset = size * 0.08
    page.show_pdf_page(fitz.Rect(inset, inset, size - inset, size - inset),
                       _svg_to_pdf(_mark_svg(INK)), 0)
    return page.get_pixmap(dpi=72, alpha=round_disc)


def _lockup_png(ink: str, height: int = 192) -> fitz.Pixmap:
    """
    Le lockup complet, en PNG à fond TRANSPARENT.

    §29 — `st.logo()` (et `st.set_page_config(page_icon=…)`) ouvrent l'image reçue via PIL pour la
    réencoder ; PIL ne sait pas ouvrir un SVG, donc lui donner les octets d'`acami-lockup.svg`
    lève `UnidentifiedImageError` — la MÊME contrainte déjà documentée sur `favicon_for_streamlit`
    (aca/core/branding.py), simplement oubliée lors du premier câblage du repli côté sidebar.

    Ce PNG sert AUSSI, en `data:` URI dans un `<img>`, partout où `agency_mark_html()`/`hero_html()`
    injectent le mot-symbole via `st.html()` : un `<svg>` EN LIGNE dans ce HTML-là ne suffit pas —
    Streamlit « sanitize[s] HTML with DOMPurify » côté client (sa propre documentation), dont le
    profil par défaut élimine l'espace de noms SVG ; le HTML envoyé reste syntaxiquement correct
    (donc invisible dans un test Python type `AppTest`, qui n'inspecte que le message envoyé, jamais
    le DOM après sanitisation par le navigateur) mais rend une balise vide à l'écran. `<img
    src="data:…">` n'a pas ce problème : DOMPurify autorise `img[src]`, le mécanisme déjà utilisé
    pour un logo client personnalisé (`aca-agency__glyph`).

    Rendu à 192px de hauteur — bien au-delà de l'affichage le plus grand qui en dépend (le titre
    Streamlit à 64px) — pour rester net même sur un écran à forte densité de pixels, sans
    transparence forcée nulle part (contrairement à `_favicon`, ici TOUT le fond doit être
    transparent, pas seulement les coins).
    """
    lockup = _svg_to_pdf(_lockup_svg(ink))
    box = lockup[0].rect
    width = round(height * box.width / box.height)
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.show_pdf_page(fitz.Rect(0, 0, width, height), lockup, 0)
    return page.get_pixmap(dpi=72, alpha=True)


def _mark_png(ink: str, height: int = 64) -> fitz.Pixmap:
    """
    L'étoile seule, en PNG à fond TRANSPARENT — même raisonnement que `_lockup_png`, pour les
    endroits qui n'ont besoin que du mark (pas du mot complet), en `<img>` intégré au HTML plutôt
    qu'en logo de barre latérale.
    """
    mark = _svg_to_pdf(_mark_svg(ink))
    box = mark[0].rect
    width = round(height * box.width / box.height)
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.show_pdf_page(fitz.Rect(0, 0, width, height), mark, 0)
    return page.get_pixmap(dpi=72, alpha=True)


def _og_card() -> fitz.Pixmap:
    """
    Carte de partage 1200x630.

    TEXTE EN ASCII UNIQUEMENT : les polices Base-14 d'un PDF s'adressent en Latin-1, donc les
    caractères typographiques (tiret cadratin, points de suspension) y sortent en points parasites.
    `report_pdf.py` a découvert ce piège en §20 en regardant le document rendu, pas le code.
    """
    width, height = 1200, 630
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=_rgb(PAPER))

    # Marque agrandie, très pâle, débordant du bord droit. Constaté en regardant le premier rendu :
    # toute la composition tenait à gauche et près de la moitié du canevas restait vide, ce qui se
    # lit comme une image inachevée plutôt que comme du blanc voulu. On répète la SEULE forme de
    # l'identité au lieu d'introduire un motif de plus — et à 4 % d'encre elle donne une profondeur
    # sans jamais disputer la lecture du titre.
    watermark = _svg_to_pdf(_mark_svg(_tint(INK, PAPER, 0.04)))
    page.show_pdf_page(fitz.Rect(830, -80, 1330, 420), watermark, 0)

    lockup = _svg_to_pdf(_lockup_svg(INK))
    box = lockup[0].rect
    target_width = 330.0
    target_height = target_width * box.height / box.width
    page.show_pdf_page(fitz.Rect(96, 120, 96 + target_width, 120 + target_height), lockup, 0)

    page.insert_text((96, 372), "The AI reads the inbox.",
                     fontname="hebo", fontsize=48, color=_rgb(INK))
    page.insert_text((96, 430), "The human still decides.",
                     fontname="helv", fontsize=48, color=_rgb(MUTED))
    page.insert_text((96, 508), "Custom AI agents, installed on your own infrastructure.",
                     fontname="helv", fontsize=20, color=_rgb(MUTED))

    # Le filet de pied fermé par le carré d'ambre : la seule couleur de la carte, et elle y dit la
    # même chose que dans le produit.
    page.draw_rect(fitz.Rect(96, 560, 1080, 561.5), color=None, fill=_rgb(MUTED))
    page.draw_rect(fitz.Rect(1080, 548, 1104, 572), color=None, fill=_rgb(ACCENT))
    return page.get_pixmap(dpi=72, alpha=False)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    os.makedirs(OUT_DIR, exist_ok=True)

    for source in ("star.svg", "acami.svg"):
        if not os.path.exists(os.path.join(OUT_DIR, source)):
            print(f"[ERREUR] static/brand/{source} est introuvable — c'est la source réelle, "
                  "ce script ne sait plus rien reconstruire sans elle.")
            return 1

    vectors = {
        "acami-mark.svg": _mark_svg(INK),
        "acami-mark-mono.svg": _mark_svg("currentColor"),
        "acami-lockup.svg": _lockup_svg(INK),
        "acami-lockup-dark.svg": _lockup_svg(INK_DARK),
    }
    for name, text in vectors.items():
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"   static/brand/{name}")

    rasters = {
        "acami-favicon-32.png": _favicon(32, round_disc=True),
        "acami-favicon-180.png": _favicon(180, round_disc=False),
        "acami-og.png": _og_card(),
        # §29 — le repli de `st.logo()` ET les usages en `<img>` intégré au HTML (`st.html()`) :
        # voir la docstring de `_lockup_png` pour les deux raisons distinctes (PIL, puis DOMPurify).
        "acami-lockup.png": _lockup_png(INK),
        "acami-lockup-dark.png": _lockup_png(INK_DARK),
        "acami-mark.png": _mark_png(INK),
        "acami-mark-dark.png": _mark_png(INK_DARK),
    }
    for name, pixmap in rasters.items():
        pixmap.save(os.path.join(OUT_DIR, name))
        print(f"   static/brand/{name}  {pixmap.width}x{pixmap.height}")

    print(f"OK {len(vectors)} vecteurs + {len(rasters)} images, recolorés depuis "
          f"static/brand/star.svg + acami.svg (tracé réel, non reconstruit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

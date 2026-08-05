"""
Rendu PDF d'un rapport d'activité, aux couleurs du client (§20).

**Ce que ce module fait, et ce qu'il ne fait pas.** Il ne sait rien des bases de données : il reçoit
la structure neutre produite par `aca/core/reporting.py::collect()` et la dessine. Cette séparation
est ce qui permet de tester le *contenu* d'un rapport sans ouvrir un document, et de vérifier la
*mise en page* sur des données inventées.

**Aucune dépendance nouvelle, et c'est un choix, pas une contrainte subie.** Les graphiques sont
tracés à la main avec les primitives de PyMuPDF (déjà épinglé pour lire les pièces jointes et écrire
les propositions). Ajouter matplotlib pour quatre diagrammes en barres aurait fait entrer des
dizaines de mégaoctets de dépendances, une police à embarquer et un moteur de rendu supplémentaire
dans l'image Docker — pour produire des images matricielles qu'il aurait fallu re-thématiser à la
main de toute façon, puisque la palette vient du client. Des rectangles et des lignes vectorielles
sont plus nets à l'impression, pèsent quelques kilo-octets et prennent la couleur de la marque sans
effort.

**Toujours à la marque (§17), mais jamais illisible.** Un thème sombre choisi par un client est
correct à l'écran et désastreux sur un document destiné à être imprimé et transféré : du texte clair
sur un fond clair. Le papier est donc toujours clair, et l'encre est celle du client **si son
contraste le permet** — sinon on retombe sur un gris très foncé. La couleur d'accent, elle, est
toujours respectée : c'est elle qu'on reconnaît.

**Ne lève jamais** (même contrat que `pdf_export.py`, `notify.py`, `hubspot.py`) : `build_report_pdf`
renvoie `None` en cas d'échec. Ce document est produit à la fois par un bouton d'interface et par un
travail planifié de nuit ; dans les deux cas une exception coûterait plus que l'absence du fichier.
"""
import os
from datetime import datetime

from aca.core import branding

# A4 en points PostScript, comme `pdf_export.py`.
PAGE_WIDTH, PAGE_HEIGHT = 595, 842
MARGIN = 44
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
TOP = 58
BOTTOM = PAGE_HEIGHT - 52

FONT = "helv"
FONT_BOLD = "hebo"
FONT_MONO = "cour"

# Contraste minimal exigé de l'encre sur le papier avant de renoncer à la couleur de texte du
# client. 4.5 est le seuil WCAG AA pour du texte courant ; en dessous, le document resterait
# « à la marque » tout en étant pénible à lire, ce qui n'est un service pour personne.
_MIN_INK_CONTRAST = 4.5


def _rgb(hex_color: str) -> tuple:
    """Couleur PyMuPDF (0-1) à partir d'un hexadécimal de marque, avec repli noir si invalide."""
    try:
        return tuple(channel / 255 for channel in branding._to_rgb(hex_color))
    except Exception:  # noqa: BLE001 — un jeton de marque abîmé ne doit pas coûter le document
        return (0.0, 0.0, 0.0)


class _Palette:
    """
    Couleurs du document, dérivées des jetons de marque une seule fois.

    Le papier est forcé au clair (cf. docstring du module) : `BRAND_BACKGROUND` n'est utilisé que
    s'il est déjà clair, sinon on prend un blanc franc. Le calcul est fait ici, pas dans
    `branding.py`, parce que c'est une règle propre à l'imprimé — l'écran, lui, a parfaitement le
    droit d'être sombre.
    """

    # Valeurs de repli, employées quand un jeton est absent OU invalide (cf. `_hex`).
    _FALLBACKS = {
        "BRAND_BACKGROUND": "#FFFFFF", "BRAND_TEXT": "#1A1A1A", "BRAND_PRIMARY": "#0B4F6C",
        "BRAND_ACCENT": "#C2703D", "BRAND_SUCCESS": "#2E7D32", "BRAND_DANGER": "#C62828",
    }

    @staticmethod
    def _hex(tokens: dict, key: str, fallback: str) -> str:
        """
        Couleur de marque validée, avec repli silencieux si elle ne tient pas la route.

        Trouvé par un test, pas par relecture : `branding.relative_luminance()` et ses voisines
        lèvent sur un hexadécimal malformé, et cette exception remontait jusqu'au `except` général
        de `build_report_pdf` — qui renvoie `None` conformément à son contrat. Autrement dit, une
        faute de frappe dans un `BRAND_PRIMARY` d'un fichier `.env` faisait qu'aucun rapport
        mensuel n'était plus jamais produit, silencieusement, la nuit. Une couleur est un
        ornement ; elle ne doit pas décider si le document existe.
        """
        value = tokens.get(key) or fallback
        return value if branding.is_valid_hex(value) else fallback

    def __init__(self, tokens: dict):
        self.tokens = tokens
        background = self._hex(tokens, "BRAND_BACKGROUND", self._FALLBACKS["BRAND_BACKGROUND"])
        light_enough = branding.relative_luminance(background) > 0.75
        self.paper_hex = background if light_enough else "#FFFFFF"
        ink_hex = self._hex(tokens, "BRAND_TEXT", self._FALLBACKS["BRAND_TEXT"])
        if branding.contrast_ratio(ink_hex, self.paper_hex) < _MIN_INK_CONTRAST:
            ink_hex = "#1F2430"
        self.ink_hex = ink_hex
        self.primary_hex = self._hex(tokens, "BRAND_PRIMARY", self._FALLBACKS["BRAND_PRIMARY"])
        self.paper = _rgb(self.paper_hex)
        self.ink = _rgb(ink_hex)
        self.muted = _rgb(branding.mix(ink_hex, self.paper_hex, 0.45))
        self.faint = _rgb(branding.mix(ink_hex, self.paper_hex, 0.9))
        self.rule = _rgb(self._hex(tokens, "BRAND_BORDER",
                                   branding.mix(ink_hex, self.paper_hex, 0.8)))
        self.primary = _rgb(self.primary_hex)
        self.accent = _rgb(self._hex(tokens, "BRAND_ACCENT", self._FALLBACKS["BRAND_ACCENT"]))
        self.success = _rgb(self._hex(tokens, "BRAND_SUCCESS", self._FALLBACKS["BRAND_SUCCESS"]))
        self.danger = _rgb(self._hex(tokens, "BRAND_DANGER", self._FALLBACKS["BRAND_DANGER"]))
        self.on_primary = _rgb(branding.readable_text_on(self.primary_hex))


# Caractères typographiques que les polices Base-14 d'un PDF ne savent pas adresser, et leur
# équivalent le plus proche. Cf. `_safe()` pour le pourquoi.
_SUBSTITUTIONS = str.maketrans({
    "…": "...", "—": "-", "–": "-", "―": "-",
    "’": "'", "‘": "'", "‚": ",", "“": '"', "”": '"', "„": '"',
    "€": "EUR", "™": "(TM)", "•": "·", "→": "->", "≥": ">=", "≤": "<=",
    " ": " ", " ": " ", " ": " ",
})


def _safe(text) -> str:
    """
    Rend un texte représentable par les polices Base-14 du PDF.

    **Défaut réel, trouvé en regardant le document produit et non en relisant le code.** Les polices
    Base-14 (helv/hebo/cour) sont écrites dans le PDF avec un encodage Latin-1 : `fitz.Font` possède
    bien les glyphes « … » et « — », mais le document n'a aucun moyen de les désigner, et ils
    sortaient à l'écran en points parasites — dans chaque cellule tronquée d'un tableau et à chaque
    valeur absente. Les lettres accentuées, elles, sont dans Latin-1 et s'affichaient correctement,
    ce qui rendait le défaut d'autant plus facile à ne pas voir.

    Le repli final `encode('latin-1', 'replace')` n'est pas de la ceinture-bretelle : les objets et
    les adresses d'expéditeurs viennent d'e-mails entrants, donc de sources non maîtrisées, et
    peuvent contenir n'importe quel caractère Unicode (idéogrammes, émojis). Un « ? » visible vaut
    mieux qu'un glyphe cassé, et bien mieux qu'une exception dans un rapport nocturne.

    Corrigé **à la frontière du dessin** (un seul `_put`) plutôt qu'à chacun des vingt-trois points
    d'appel — même raisonnement que `aca/core/console.py`, qui règle l'encodage console une fois
    pour toutes au lieu d'envelopper 68 `print()`.
    """
    rendered = str(text if text is not None else "").translate(_SUBSTITUTIONS)
    return rendered.encode("latin-1", "replace").decode("latin-1")


def _put(page, point, text, **kwargs) -> None:
    """Écrit une ligne de texte sur la page, après normalisation (`_safe`)."""
    page.insert_text(point, _safe(text), **kwargs)


_FONT_CACHE = {}


def _measure(text: str, font: str, size: float) -> float:
    """
    Largeur d'un texte, **en tenant compte des accents**.

    Volontairement `fitz.Font(...).text_length()` et non la fonction de commodité
    `fitz.get_text_length()`. Cette dernière sous-évalue gravement les caractères accentués : sur
    cette machine, « ééééééééée » mesure 22,8 points là où « eeeeeeeeee » en mesure 45,6 — les « é »
    comptent pour presque rien. Conséquence constatée sur le premier rendu réel : chaque paragraphe
    en français dépassait la marge droite et se faisait couper au bord de la page, alors que le
    calcul de retour à la ligne se croyait dans les clous. Le défaut ne se voit pas en relisant le
    code, seulement en regardant le document produit.

    L'objet `Font` est mis en cache : le construire coûte, et il est sollicité une fois par mot de
    chaque paragraphe et par cellule de chaque tableau.
    """
    if font not in _FONT_CACHE:
        import fitz

        _FONT_CACHE[font] = fitz.Font(font)
    # Mesurer la forme RÉELLEMENT dessinée : `_safe` transforme « … » (un caractère) en « ... »
    # (trois), et mesurer l'original ferait déborder chaque cellule tronquée de la largeur de deux
    # points supplémentaires.
    return _FONT_CACHE[font].text_length(_safe(text), size)


def _wrap(text: str, width: float, font: str = FONT, size: float = 9.5) -> list:
    """
    Découpe un texte en lignes tenant dans `width`.

    Fait à la main plutôt qu'en confiant le retour à la ligne à `insert_textbox` : connaître le
    nombre de lignes AVANT de dessiner est ce qui permet de décider s'il faut changer de page. Sans
    ça, un paragraphe se retrouve coupé net en bas de page, ou bien il faut le dessiner puis
    constater qu'il a débordé — trop tard.

    Un mot plus long que la largeur disponible (une URL, un identifiant) est laissé tel quel plutôt
    que coupé arbitrairement : il déborde d'un peu, ce qui reste lisible, là où une césure au
    milieu d'une adresse la rendrait fausse pour qui la recopie.
    """
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure(candidate, font, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _ellipsize(text: str, width: float, font: str = FONT, size: float = 8.5) -> str:
    """Tronque une cellule trop longue avec une ellipse — un tableau qui déborde est illisible."""
    text = str(text if text is not None else "")
    if _measure(text, font, size) <= width:
        return text
    while text and _measure(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _format_value(value, suffix: str = "") -> str:
    """Nombres lisibles : séparateur de milliers fin, décimale française, tiret pour l'absence."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return ("oui" if value else "non") + suffix
    if isinstance(value, float):
        rendered = f"{value:,.1f}".replace(",", " ").replace(".", ",")
    elif isinstance(value, int):
        rendered = f"{value:,}".replace(",", " ")
    else:
        rendered = str(value)
    return rendered + suffix


def _comparison_text(comparison: dict) -> str:
    if not comparison:
        return ""
    delta = comparison["delta"]
    sign = "+" if delta > 0 else ("-" if delta < 0 else "=")
    magnitude = _format_value(round(abs(delta), 1) if isinstance(delta, float) else abs(delta))
    if comparison.get("pct") is not None:
        return f"{sign}{magnitude} ({sign}{abs(comparison['pct'])} %)"
    if delta == 0:
        return "stable"
    return f"{sign}{magnitude} (précédent : 0)"


def _comparison_colour(comparison: dict, better: str, palette: _Palette) -> tuple:
    """
    Couleur d'un écart, selon le sens qui est FAVORABLE à cet indicateur.

    C'est le seul endroit du document où une couleur porte un jugement, et c'est pourquoi elle est
    calculée ici plutôt qu'au cas par cas : peindre en vert toute hausse ferait passer une
    dégradation du délai de réponse pour une bonne nouvelle (cf. `reporting._kpi`).
    """
    direction = comparison.get("direction")
    if better == "neutral" or direction == "flat":
        return palette.muted
    favourable = (direction == "up" and better == "up") or (direction == "down" and better == "down")
    return palette.success if favourable else palette.danger


class _Canvas:
    """Document en cours d'écriture : page courante, ordonnée courante, sauts de page."""

    def __init__(self, palette: _Palette):
        import fitz

        self.fitz = fitz
        self.palette = palette
        self.doc = fitz.open()
        self.page = None
        self.y = TOP
        self.new_page()

    # ── Pages ────────────────────────────────────────────────────────────────────────────────
    def new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        if self.palette.paper_hex.upper() not in ("#FFFFFF", "#FFF"):
            self.page.draw_rect(
                self.fitz.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT),
                color=self.palette.paper, fill=self.palette.paper,
            )
        self.y = TOP

    def need(self, height: float) -> None:
        """Garantit `height` points disponibles, en changeant de page si nécessaire."""
        if self.y + height > BOTTOM:
            self.new_page()

    # ── Texte ────────────────────────────────────────────────────────────────────────────────
    def line(self, text: str, *, size: float = 9.5, font: str = FONT, color=None,
             indent: float = 0, leading: float = 1.35) -> None:
        self.need(size * leading)
        _put(
            self.page, (MARGIN + indent, self.y + size), text, fontname=font, fontsize=size,
            color=color if color is not None else self.palette.ink,
        )
        self.y += size * leading

    def paragraph(self, text: str, *, size: float = 9, font: str = FONT, color=None,
                  indent: float = 0, width: float = None, gap: float = 4) -> None:
        width = width or (CONTENT_WIDTH - indent)
        for chunk in _wrap(text, width, font, size):
            self.line(chunk, size=size, font=font, color=color, indent=indent, leading=1.32)
        self.y += gap

    def rule(self, *, gap_before: float = 4, gap_after: float = 8) -> None:
        self.need(gap_before + gap_after + 1)
        self.y += gap_before
        self.page.draw_line(
            self.fitz.Point(MARGIN, self.y), self.fitz.Point(PAGE_WIDTH - MARGIN, self.y),
            color=self.palette.rule, width=0.6,
        )
        self.y += gap_after


# ── Blocs ─────────────────────────────────────────────────────────────────────────────────────
def _draw_cover(canvas: _Canvas, meta: dict, tokens: dict) -> None:
    """
    Première page : bandeau de marque, titre, période, et surtout **ce que contient le document**.

    Le sommaire n'est pas décoratif. Un rapport paramétrable ne contient pas toujours les mêmes
    sections ; sans cette liste, un lecteur ne peut pas distinguer « il ne s'est rien passé » de
    « cette section n'a pas été demandée » — deux conclusions opposées tirées de la même absence.
    """
    palette = canvas.palette
    band_height = 168
    canvas.page.draw_rect(
        canvas.fitz.Rect(0, 0, PAGE_WIDTH, band_height),
        color=palette.primary, fill=palette.primary,
    )
    # Filet d'accent : la seule touche de la couleur secondaire sur la couverture, posée à la
    # jonction du bandeau et du papier pour la rendre reconnaissable sans la disperser partout.
    canvas.page.draw_rect(
        canvas.fitz.Rect(0, band_height - 5, PAGE_WIDTH, band_height),
        color=palette.accent, fill=palette.accent,
    )

    text_left = MARGIN
    logo_uri = tokens.get("BRAND_LOGO") or ""
    logo = branding.decode_logo(logo_uri)
    # SVG écarté : `insert_image` n'accepte que des images matricielles. Le logo reste parfaitement
    # utilisable ailleurs ; il est simplement omis ici plutôt que de faire échouer l'export entier
    # (même choix que `pdf_export.py`).
    if logo and not logo_uri.startswith("data:image/svg"):
        try:
            canvas.page.insert_image(
                canvas.fitz.Rect(MARGIN, 30, MARGIN + 46, 76), stream=logo, keep_proportion=True,
            )
            text_left = MARGIN + 60
        except Exception:  # noqa: BLE001
            text_left = MARGIN

    company = tokens.get("BRAND_COMPANY") or tokens.get("BRAND_NAME") or "ACA"
    _put(canvas.page,(text_left, 52), company, fontname=FONT_BOLD, fontsize=15,
                            color=palette.on_primary)
    _put(canvas.page,
        (text_left, 68), tokens.get("BRAND_TAGLINE") or "Assistant commercial agentique",
        fontname=FONT, fontsize=8.5, color=palette.on_primary,
    )

    for index, chunk in enumerate(_wrap(meta.get("title", ""), CONTENT_WIDTH, FONT_BOLD, 22)[:2]):
        _put(canvas.page,(MARGIN, 108 + index * 26), chunk, fontname=FONT_BOLD,
                                fontsize=22, color=palette.on_primary)
    _put(canvas.page,(MARGIN, 152), f"Période : {meta.get('period_label', '')}",
                            fontname=FONT, fontsize=10, color=palette.on_primary)

    canvas.y = band_height + 26

    facts = [("Généré le", meta.get("generated_at", ""))]
    if meta.get("generated_by"):
        facts.append(("Généré par", meta["generated_by"]))
    if meta.get("comparison_label"):
        facts.append(("Comparé à", meta["comparison_label"]))
    for label, value in facts:
        _put(canvas.page,(MARGIN, canvas.y + 9), label.upper(), fontname=FONT,
                                fontsize=7.5, color=palette.muted)
        _put(canvas.page,(MARGIN + 92, canvas.y + 9), str(value), fontname=FONT_BOLD,
                                fontsize=9.5, color=palette.ink)
        canvas.y += 16
    canvas.y += 6

    if meta.get("note"):
        canvas.paragraph(meta["note"], size=9, color=palette.muted)

    canvas.rule(gap_before=6)
    canvas.line("Contenu du rapport", size=11, font=FONT_BOLD)
    canvas.y += 4

    from aca.core import reporting

    for key in meta.get("sections", []):
        section = reporting.SECTIONS.get(key)
        if not section:
            continue
        canvas.need(28)
        canvas.page.draw_rect(
            canvas.fitz.Rect(MARGIN, canvas.y + 3, MARGIN + 3, canvas.y + 11),
            color=palette.accent, fill=palette.accent,
        )
        _put(canvas.page,(MARGIN + 12, canvas.y + 10), section["label"],
                                fontname=FONT_BOLD, fontsize=9, color=palette.ink)
        canvas.y += 13
        canvas.paragraph(section["description"], size=8.2, color=palette.muted, indent=12, gap=5)

    canvas.rule(gap_before=8)
    canvas.paragraph(
        "Toutes les propositions comptabilisées dans ce document ont été relues et validées par une "
        "personne avant tout enregistrement dans le CRM : ACA ne prend aucune décision commerciale "
        "seul. Les chiffres proviennent des journaux locaux de l'application.",
        size=8.2, color=palette.muted,
    )


def _draw_group_title(canvas: _Canvas, title: str) -> None:
    canvas.need(46)
    palette = canvas.palette
    canvas.y += 6
    canvas.page.draw_rect(
        canvas.fitz.Rect(MARGIN, canvas.y, MARGIN + 26, canvas.y + 2.5),
        color=palette.accent, fill=palette.accent,
    )
    canvas.y += 12
    canvas.line(title.upper(), size=11.5, font=FONT_BOLD, color=palette.primary, leading=1.5)
    canvas.y += 4


def _draw_block_header(canvas: _Canvas, block: dict) -> None:
    canvas.need(58)
    canvas.line(block.get("title", ""), size=11, font=FONT_BOLD)
    canvas.y += 2
    if block.get("context"):
        canvas.paragraph(block["context"], size=8.2, color=canvas.palette.muted, gap=7)


def _draw_kpis(canvas: _Canvas, block: dict) -> None:
    palette = canvas.palette
    items = block.get("items") or []
    if not items:
        canvas.paragraph("Aucune donnée sur cette période.", size=9, color=palette.muted)
        return
    per_row = 3
    card_width = (CONTENT_WIDTH - 2 * 10) / per_row
    card_height = 62
    for start in range(0, len(items), per_row):
        row = items[start:start + per_row]
        canvas.need(card_height + 10)
        top = canvas.y
        for index, item in enumerate(row):
            left = MARGIN + index * (card_width + 10)
            rect = canvas.fitz.Rect(left, top, left + card_width, top + card_height)
            canvas.page.draw_rect(rect, color=palette.rule, fill=palette.faint, width=0.6)
            _put(canvas.page,
                (left + 10, top + 16),
                _ellipsize(str(item.get("label", "")).upper(), card_width - 20, FONT, 7.2),
                fontname=FONT, fontsize=7.2, color=palette.muted,
            )
            _put(canvas.page,
                (left + 10, top + 38), _format_value(item.get("value"), item.get("suffix", "")),
                fontname=FONT_BOLD, fontsize=17, color=palette.ink,
            )
            comparison = item.get("comparison")
            if comparison:
                _put(canvas.page,
                    (left + 10, top + 52), _comparison_text(comparison), fontname=FONT_BOLD,
                    fontsize=8,
                    color=_comparison_colour(comparison, item.get("better", "up"), palette),
                )
            elif item.get("hint"):
                _put(canvas.page,
                    (left + 10, top + 52), _ellipsize(item["hint"], card_width - 20, FONT, 7.5),
                    fontname=FONT, fontsize=7.5, color=palette.muted,
                )
        canvas.y = top + card_height + 10


def _draw_bars(canvas: _Canvas, block: dict) -> None:
    """
    Diagramme en barres horizontales, avec la période précédente en barre fantôme.

    Horizontal et non vertical : les libellés sont des catégories nommées (« Demande demo »,
    « Échec de connexion »), et à la verticale ils devraient être pivotés ou tronqués. La barre
    fantôme derrière plutôt qu'à côté permet de lire l'écart d'un coup d'œil sans doubler la
    hauteur du diagramme.
    """
    palette = canvas.palette
    items = list(block.get("items") or [])
    if not items or all(not item.get("value") and not item.get("previous") for item in items):
        canvas.paragraph("Aucune donnée sur cette période.", size=9, color=palette.muted)
        return

    label_width = 132
    value_width = 108
    plot_left = MARGIN + label_width
    plot_width = CONTENT_WIDTH - label_width - value_width
    peak = max(max(item.get("value") or 0, item.get("previous") or 0) for item in items) or 1
    row_height = 22

    has_previous = any(item.get("previous") is not None for item in items)
    if has_previous:
        canvas.need(16)
        canvas.page.draw_rect(
            canvas.fitz.Rect(plot_left, canvas.y + 3, plot_left + 12, canvas.y + 8),
            color=palette.primary, fill=palette.primary,
        )
        _put(canvas.page,(plot_left + 17, canvas.y + 8), "période", fontname=FONT,
                                fontsize=7, color=palette.muted)
        canvas.page.draw_rect(
            canvas.fitz.Rect(plot_left + 58, canvas.y + 3, plot_left + 70, canvas.y + 8),
            color=palette.rule, fill=palette.faint, width=0.5,
        )
        _put(canvas.page,(plot_left + 75, canvas.y + 8), "période précédente",
                                fontname=FONT, fontsize=7, color=palette.muted)
        canvas.y += 13

    for item in items:
        canvas.need(row_height)
        top = canvas.y
        _put(canvas.page,
            (MARGIN, top + 11), _ellipsize(str(item.get("label", "")), label_width - 8, FONT, 8.5),
            fontname=FONT, fontsize=8.5, color=palette.ink,
        )
        previous = item.get("previous")
        if previous:
            width = max(1.0, plot_width * previous / peak)
            canvas.page.draw_rect(
                canvas.fitz.Rect(plot_left, top + 1, plot_left + width, top + 14),
                color=palette.rule, fill=palette.faint, width=0.5,
            )
        value = item.get("value") or 0
        if value:
            width = max(1.5, plot_width * value / peak)
            canvas.page.draw_rect(
                canvas.fitz.Rect(plot_left, top + 3.5, plot_left + width, top + 11.5),
                color=palette.primary, fill=palette.primary,
            )
        _put(canvas.page,
            (PAGE_WIDTH - MARGIN - value_width + 6, top + 11), _format_value(value),
            fontname=FONT_BOLD, fontsize=8.5, color=palette.ink,
        )
        if previous is not None:
            delta = value - previous
            comparison = {
                "delta": delta,
                "direction": "flat" if delta == 0 else ("up" if delta > 0 else "down"),
                "pct": round(100 * delta / previous, 1) if previous else None,
            }
            _put(canvas.page,
                (PAGE_WIDTH - MARGIN - value_width + 44, top + 11), _comparison_text(comparison),
                fontname=FONT, fontsize=7.2,
                color=_comparison_colour(comparison, "up", palette),
            )
        canvas.y = top + row_height - 4
    canvas.y += 8


def _draw_line(canvas: _Canvas, block: dict) -> None:
    """Courbe de volume quotidien : aire remplie sous la ligne, repères de niveau, dates aux bornes."""
    palette = canvas.palette
    points = block.get("points") or []
    if not points:
        canvas.paragraph("Aucune donnée sur cette période.", size=9, color=palette.muted)
        return
    if len(points) == 1:
        canvas.paragraph(
            f"Un seul jour d'activité sur la période ({points[0]['label']} : "
            f"{points[0]['value']}). Une courbe demande au moins deux points.",
            size=9, color=palette.muted,
        )
        return

    plot_height = 118
    canvas.need(plot_height + 36)
    top = canvas.y
    bottom = top + plot_height
    peak = max(p["value"] for p in points) or 1
    step = CONTENT_WIDTH / (len(points) - 1)

    # Lignes de repère horizontales : sans elles, une courbe sans échelle ne se lit pas — on
    # distingue une forme, pas une grandeur.
    for fraction in (0, 0.5, 1):
        y = bottom - plot_height * fraction
        canvas.page.draw_line(
            canvas.fitz.Point(MARGIN, y), canvas.fitz.Point(PAGE_WIDTH - MARGIN, y),
            color=palette.rule, width=0.4,
        )
        _put(canvas.page,
            (PAGE_WIDTH - MARGIN + 3, y + 3), _format_value(round(peak * fraction)),
            fontname=FONT, fontsize=6.5, color=palette.muted,
        )

    coordinates = [
        (MARGIN + index * step, bottom - plot_height * (point["value"] / peak))
        for index, point in enumerate(points)
    ]
    # Aire sous la courbe, en teinte très claire de la couleur principale : donne du poids visuel
    # au volume sans transformer le graphique en aplat opaque.
    area = canvas.page.new_shape()
    area.draw_polyline(
        [canvas.fitz.Point(MARGIN, bottom)]
        + [canvas.fitz.Point(x, y) for x, y in coordinates]
        + [canvas.fitz.Point(coordinates[-1][0], bottom)]
    )
    area.finish(
        color=None,
        fill=_rgb(branding.mix(palette.primary_hex, palette.paper_hex, 0.85)),
        closePath=True,
    )
    area.commit()

    canvas.page.draw_polyline(
        [canvas.fitz.Point(x, y) for x, y in coordinates], color=palette.primary, width=1.4,
    )
    for x, y in coordinates:
        canvas.page.draw_circle(canvas.fitz.Point(x, y), 1.6, color=palette.primary,
                                fill=palette.primary)

    canvas.page.draw_line(canvas.fitz.Point(MARGIN, bottom),
                          canvas.fitz.Point(PAGE_WIDTH - MARGIN, bottom),
                          color=palette.muted, width=0.7)
    # Trois dates suffisent : une par point rendrait l'axe illisible dès trois semaines de données.
    for index in sorted({0, len(points) // 2, len(points) - 1}):
        x = MARGIN + index * step
        _put(canvas.page,
            (min(max(MARGIN, x - 22), PAGE_WIDTH - MARGIN - 44), bottom + 12),
            points[index]["label"], fontname=FONT, fontsize=6.8, color=palette.muted,
        )
    canvas.y = bottom + 24


def _draw_table(canvas: _Canvas, block: dict) -> None:
    """
    Tableau paginé, en-tête répété à chaque nouvelle page.

    Répéter l'en-tête n'est pas un détail : sans lui, la deuxième page d'un tableau de six colonnes
    devient une grille de valeurs dont plus personne ne sait laquelle est la date et laquelle est
    l'expéditeur.
    """
    palette = canvas.palette
    columns = block.get("columns") or []
    rows = block.get("rows") or []
    if not columns:
        return
    if not rows:
        canvas.paragraph("Aucune ligne sur cette période.", size=9, color=palette.muted)
        return

    # Largeurs proportionnelles au contenu réel, bornées : une colonne « Date » n'a pas besoin d'un
    # sixième de la page, une colonne « Objet » en a besoin de bien plus.
    weights = []
    for index, header in enumerate(columns):
        widest = _measure(str(header), FONT_BOLD, 8)
        for row in rows[:60]:  # échantillon : mesurer 2000 lignes coûterait plus que ça ne rapporte
            if index < len(row):
                widest = max(widest, _measure(str(row[index]), FONT, 8))
        weights.append(min(max(widest, 40), 210))
    total = sum(weights) or 1
    widths = [CONTENT_WIDTH * weight / total for weight in weights]

    row_height = 15

    def header_row():
        canvas.need(row_height * 2)
        top = canvas.y
        canvas.page.draw_rect(
            canvas.fitz.Rect(MARGIN, top, PAGE_WIDTH - MARGIN, top + row_height),
            color=palette.primary, fill=palette.primary,
        )
        x = MARGIN
        for index, header in enumerate(columns):
            _put(canvas.page,
                (x + 5, top + 10.5), _ellipsize(str(header), widths[index] - 10, FONT_BOLD, 8),
                fontname=FONT_BOLD, fontsize=8, color=palette.on_primary,
            )
            x += widths[index]
        canvas.y = top + row_height

    header_row()
    for position, row in enumerate(rows):
        if canvas.y + row_height > BOTTOM:
            canvas.new_page()
            header_row()
        top = canvas.y
        if position % 2:
            canvas.page.draw_rect(
                canvas.fitz.Rect(MARGIN, top, PAGE_WIDTH - MARGIN, top + row_height),
                color=palette.faint, fill=palette.faint,
            )
        x = MARGIN
        for index in range(len(columns)):
            cell = row[index] if index < len(row) else ""
            _put(canvas.page,
                (x + 5, top + 10.5), _ellipsize(cell, widths[index] - 10, FONT, 8),
                fontname=FONT, fontsize=8, color=palette.ink,
            )
            x += widths[index]
        canvas.y = top + row_height

    canvas.y += 4
    canvas.paragraph(f"{len(rows)} ligne(s).", size=7.5, color=palette.muted)


def _draw_text(canvas: _Canvas, block: dict) -> None:
    canvas.paragraph(block.get("body", ""), size=9, color=canvas.palette.muted)


_DRAWERS = {
    "kpis": _draw_kpis,
    "bars": _draw_bars,
    "line": _draw_line,
    "table": _draw_table,
    "text": _draw_text,
}


def _draw_footers(canvas: _Canvas, meta: dict, tokens: dict) -> None:
    """
    Pied de page sur chaque page — écrit à la fin, une fois le nombre total de pages connu.

    La mention de validation humaine n'est pas un ornement : ce document énonce des volumes de leads
    entrés au CRM, et un lecteur qui ne connaît pas l'outil pourrait en conclure qu'une machine a
    engagé l'entreprise. C'est le même raisonnement que le pied de page de `pdf_export.py`.
    """
    palette = canvas.palette
    company = tokens.get("BRAND_COMPANY") or tokens.get("BRAND_NAME") or "ACA"
    total = canvas.doc.page_count
    for number, page in enumerate(canvas.doc, start=1):
        page.draw_line(
            canvas.fitz.Point(MARGIN, PAGE_HEIGHT - 40),
            canvas.fitz.Point(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 40),
            color=palette.rule, width=0.5,
        )
        _put(
            page, (MARGIN, PAGE_HEIGHT - 28),
            _ellipsize(
                f"{company} · {meta.get('period_label', '')} · validation humaine obligatoire "
                "avant toute écriture CRM", CONTENT_WIDTH - 60, FONT, 7),
            fontname=FONT, fontsize=7, color=palette.muted,
        )
        _put(
            page, (PAGE_WIDTH - MARGIN - 46, PAGE_HEIGHT - 28), f"page {number}/{total}",
            fontname=FONT_MONO, fontsize=7, color=palette.muted,
        )


def build_report_pdf(report: dict, tokens: dict = None) -> bytes:
    """
    Rend un rapport (sortie de `reporting.collect()`) en PDF à la marque du tenant.

    Renvoie les octets, ou `None` en cas d'échec — **ne lève jamais** (cf. docstring du module).
    `tokens` est injectable pour les tests ; par défaut la marque courante est résolue à l'appel,
    donc un changement de couleur est pris en compte sans redémarrage, comme partout depuis le §17.
    """
    try:
        tokens = tokens or branding.resolve()
        palette = _Palette(tokens)
        canvas = _Canvas(palette)
        meta = report.get("meta") or {}

        _draw_cover(canvas, meta, tokens)

        for group in report.get("groups") or []:
            # Chaque famille de sections commence sur une page neuve : c'est ce qui rend le document
            # feuilletable — on cherche « Traçabilité », on ne le trouve pas au milieu d'un tableau.
            canvas.new_page()
            _draw_group_title(canvas, group["title"])
            for block in group.get("blocks") or []:
                _draw_block_header(canvas, block)
                drawer = _DRAWERS.get(block.get("type"), _draw_text)
                drawer(canvas, block)
                canvas.rule(gap_before=6, gap_after=10)

        _draw_footers(canvas, meta, tokens)
        payload = canvas.doc.tobytes()
        canvas.doc.close()
        return payload
    except Exception as exc:  # noqa: BLE001 — même contrat que pdf_export.py / notify.py
        print(f"[ACA] Rapport PDF indisponible ({exc.__class__.__name__}: {exc}).")
        return None


def write_pdf(directory: str, filename: str, payload: bytes, *, overwrite: bool = False) -> dict:
    """
    Écrit un rapport sur disque. Renvoie `{"path", "skipped", "bytes"}`.

    **Idempotent par défaut** : un fichier déjà présent n'est jamais réécrit, exactement comme
    `activity_log.archive_period`. La raison est la même et elle est concrète — si le travail
    planifié repasse après une purge de rétention, réécrire remplacerait un rapport complet par un
    rapport amputé portant le même nom, et personne ne s'en apercevrait.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    if os.path.exists(path) and not overwrite:
        return {"path": path, "skipped": True, "bytes": os.path.getsize(path)}
    with open(path, "wb") as handle:
        handle.write(payload)
    return {"path": path, "skipped": False, "bytes": len(payload)}


def list_reports(directory: str) -> list:
    """
    Rapports déjà produits, les plus récents d'abord — alimente la page « Rapports ».

    Un répertoire absent renvoie une liste vide plutôt qu'une erreur : avant le premier passage du
    planificateur il n'existe tout simplement pas, et ce n'est pas une anomalie.
    """
    if not os.path.isdir(directory):
        return []
    entries = []
    for name in os.listdir(directory):
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append({
            "name": name, "path": path, "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y à %H:%M"),
            "modified_epoch": stat.st_mtime,
        })
    return sorted(entries, key=lambda entry: entry["modified_epoch"], reverse=True)

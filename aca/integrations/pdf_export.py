"""
Export PDF d'une proposition, aux couleurs du client (§18).

**Pourquoi.** Le contenu existait déjà (`draft_response`), la marque aussi (§17) — mais un commercial
qui voulait envoyer la proposition autrement que par le brouillon Gmail devait la copier-coller dans
Word et la remettre en forme à la main. C'est aussi l'argument le plus direct en démonstration :
« voilà le document que votre client recevra, à votre logo » porte plus qu'un brouillon dans une
boîte de réception.

**Aucune dépendance nouvelle.** PyMuPDF (`pymupdf==1.28.0`) est déjà épinglé et sert à *lire* les
pièces jointes ([pdf_reader.py](aca/ingestion/pdf_reader.py)) ; il sait aussi écrire.
`insert_htmlbox()` accepte un sous-ensemble de HTML/CSS, ce qui évite de placer chaque ligne au pixel
— et surtout de recalculer les retours à la ligne à la main, source de texte tronqué dès qu'un nom
d'entreprise est long.

**Même contrat de dégradation gracieuse que le reste du projet** (`notify.py`, `hubspot.py`) :
`build_proposal_pdf()` **ne lève jamais** et renvoie `None` en cas d'échec. Un bouton de
téléchargement cassé ne doit pas faire tomber l'écran de validation, qui est la fonction vitale de
l'application.
"""
from datetime import datetime
from html import escape

from aca.core import branding

# A4 en points PostScript.
PAGE_WIDTH, PAGE_HEIGHT = 595, 842
MARGIN = 48
HEADER_HEIGHT = 96

# Le corps est borné : au-delà, ce n'est plus une proposition commerciale mais un document
# contractuel, qui n'a pas à être produit par un assistant sans relecture juridique.
MAX_BODY_CHARS = 12_000


def _header_html(tokens: dict, title: str) -> str:
    """Bandeau d'en-tête : nom de marque et titre du document, posés sur la couleur principale."""
    on_primary = branding.readable_text_on(tokens["BRAND_PRIMARY"])
    return f"""
    <div style="font-family:sans-serif;color:{on_primary};">
      <div style="font-size:17px;font-weight:bold;letter-spacing:-0.3px;">
        {escape(tokens.get("BRAND_COMPANY") or tokens.get("BRAND_NAME", ""))}
      </div>
      <div style="font-size:10px;margin-top:3px;">{escape(title)}</div>
    </div>
    """


def _body_html(tokens: dict, body: str, fiche: dict) -> str:
    """Corps du document : fiche prospect en tête, puis la proposition telle qu'elle sera envoyée."""
    primary = tokens["BRAND_PRIMARY"]
    text_color = tokens["BRAND_TEXT"]
    muted = branding.mix(text_color, tokens["BRAND_BACKGROUND"], 0.42)

    rows = "".join(
        f"<tr>"
        f'<td style="padding:3px 10px 3px 0;font-size:9px;color:{muted};white-space:nowrap;">'
        f"{escape(label.upper())}</td>"
        f'<td style="padding:3px 0;font-size:11px;color:{text_color};">{escape(str(value))}</td>'
        f"</tr>"
        for label, value in fiche.items() if value
    )
    table = (
        f'<table style="width:100%;border-collapse:collapse;margin-bottom:16px;">{rows}</table>'
        if rows else ""
    )

    # Les sauts de ligne du brouillon deviennent des paragraphes : `insert_htmlbox` ignore les `\n`
    # bruts, et la proposition arriverait sinon en un seul bloc illisible.
    paragraphs = "".join(
        f'<p style="margin:0 0 9px;font-size:11px;line-height:1.55;color:{text_color};">'
        f"{escape(chunk.strip())}</p>"
        for chunk in (body or "").split("\n") if chunk.strip()
    )
    return f"""
    <div style="font-family:sans-serif;">
      {table}
      <div style="height:2px;background:{primary};width:46px;margin:0 0 14px;"></div>
      {paragraphs}
    </div>
    """


def _footer_html(tokens: dict) -> str:
    """
    Pied de page : marque, date, et la mention de relecture humaine.

    Cette mention n'est pas un ornement juridique : tout le produit repose sur le fait qu'aucune
    proposition ne part sans validation humaine. Un document exporté qui ne le dit pas laisserait
    croire, chez le destinataire comme en interne, que le prix a été arrêté par une machine.
    """
    muted = branding.mix(tokens["BRAND_TEXT"], tokens["BRAND_BACKGROUND"], 0.5)
    company = tokens.get("BRAND_COMPANY") or tokens.get("BRAND_NAME", "")
    return (
        f'<div style="font-family:sans-serif;font-size:8px;color:{muted};">'
        f"{escape(company)} · Document généré le {datetime.now().strftime('%d/%m/%Y')} · "
        "Proposition relue et validée par un commercial avant envoi.</div>"
    )


def build_proposal_pdf(draft: str, extracted_info: dict = None, classification: str = "",
                       sender: str = "", tokens: dict = None) -> bytes:
    """
    Rend la proposition en PDF à la marque du tenant. Renvoie les octets, ou `None` en cas d'échec.

    `tokens` est injectable pour les tests ; par défaut la marque courante est résolue à l'appel, donc
    un changement de couleur est pris en compte sans redémarrage, comme partout depuis le §17.
    """
    try:
        import fitz  # PyMuPDF, déjà épinglé pour la LECTURE des pièces jointes

        tokens = tokens or branding.resolve()
        info = extracted_info or {}

        document = fitz.open()
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

        # Bandeau coloré pleine largeur, puis le texte par-dessus.
        rgb = tuple(channel / 255 for channel in branding._to_rgb(tokens["BRAND_PRIMARY"]))
        page.draw_rect(fitz.Rect(0, 0, PAGE_WIDTH, HEADER_HEIGHT), color=rgb, fill=rgb)

        logo_uri = tokens.get("BRAND_LOGO") or ""
        logo = branding.decode_logo(logo_uri)
        text_left = MARGIN
        # SVG écarté : PyMuPDF n'insère que des images bitmap. Un logo vectoriel reste parfaitement
        # utilisable dans l'interface ; il est simplement omis ici plutôt que de faire échouer
        # l'export entier.
        if logo and not logo_uri.startswith("data:image/svg"):
            try:
                page.insert_image(
                    fitz.Rect(MARGIN, 26, MARGIN + 44, 70), stream=logo, keep_proportion=True,
                )
                text_left = MARGIN + 56
            except Exception:  # noqa: BLE001 — un logo illisible ne doit pas coûter le document
                text_left = MARGIN

        page.insert_htmlbox(
            fitz.Rect(text_left, 24, PAGE_WIDTH - MARGIN, HEADER_HEIGHT - 10),
            _header_html(tokens, "Proposition commerciale"),
        )

        fiche = {
            "Entreprise": info.get("entreprise"),
            "Contact": info.get("contact"),
            "Adresse": sender,
            "Demande": classification.replace("_", " ").capitalize() if classification else "",
            "Urgence": (info.get("urgence") or "").capitalize(),
            "Besoin": info.get("besoin_principal"),
        }
        page.insert_htmlbox(
            fitz.Rect(MARGIN, HEADER_HEIGHT + 26, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 62),
            _body_html(tokens, (draft or "")[:MAX_BODY_CHARS], fiche),
        )
        page.insert_htmlbox(
            fitz.Rect(MARGIN, PAGE_HEIGHT - 52, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 22),
            _footer_html(tokens),
        )

        payload = document.tobytes()
        document.close()
        return payload
    except Exception as exc:  # noqa: BLE001 — même contrat que notify.py / hubspot.py
        print(f"[ACA] Export PDF indisponible ({exc.__class__.__name__}: {exc}).")
        return None


def proposal_filename(extracted_info: dict = None, classification: str = "") -> str:
    """
    Nom de fichier lisible : `proposition-acme-corp-devis-2026-07-30.pdf`.

    Assaini strictement (lettres, chiffres, tirets) : le nom vient d'un champ extrait par un LLM à
    partir d'un e-mail entrant — donc d'une source non fiable — et il finit dans un en-tête
    `Content-Disposition`.
    """
    import re
    import unicodedata

    company = (extracted_info or {}).get("entreprise") or "prospect"
    normalised = unicodedata.normalize("NFKD", company).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalised).strip("-").lower()[:40] or "prospect"
    kind = re.sub(r"[^a-z]+", "-", (classification or "proposition").lower()).strip("-") or "proposition"
    return f"proposition-{slug}-{kind}-{datetime.now().strftime('%Y-%m-%d')}.pdf"

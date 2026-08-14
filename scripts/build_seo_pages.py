# -*- coding: utf-8 -*-
"""
Génération des pages SEO d'acami (§28).

Produit :

    static/fr/index.html          la page de vente, en français réellement rendu
    static/seo/<slug>.html        10 pages (intégrations, comparaisons, glossaire)
    static/robots.txt
    static/sitemap.xml

── LE FRANÇAIS N'ÉTAIT VISIBLE QUE PAR JAVASCRIPT ────────────────────────────────────────────────
`landing.html` porte l'anglais dans le HTML et le français dans des attributs `data-fr`, échangés
par un script au clic. Un moteur de recherche qui n'exécute pas ce script — et beaucoup ne le font
pas de façon fiable — ne voit que l'anglais, sur un site dont l'acheteur visé est une PME française.

La correction n'est PAS une seconde copie traduite à la main : le fichier documente lui-même
pourquoi une seconde copie diverge au premier changement de contenu. `_render_french()` ci-dessous
fait exactement ce que fait le script du navigateur — remplacer le contenu de chaque élément
`[data-fr]` par sa valeur — mais une fois, à la construction, pour produire du HTML où le français
est déjà dans le texte que l'outil lit.

── UN PARSEUR MAISON, ET NON `lxml` ──────────────────────────────────────────────────────────────
`lxml` est installé, mais seulement comme dépendance TRANSITIVE de `python-docx` (déjà épinglé,
pour `attachment_reader.py`) — jamais comme dépendance du projet lui-même. L'utiliser directement
ici reproduirait exactement le piège que `totp.py` documente pour `cryptography` (transitive via
`google-auth`) et que `segno` a été choisi pour éviter : un script qui fonctionne aujourd'hui parce
qu'un paquet non garanti se trouve présent, et qui casse le jour où `python-docx` change ses propres
dépendances. `html.parser.HTMLParser`, présent dans la bibliothèque standard, suffit à la tâche —
et la tâche est volontairement étroite : trouver les bornes exactes des éléments `[data-fr]` dans le
FICHIER SOURCE BRUT, jamais reconstruire le document depuis un arbre. Tout le reste du fichier — la
feuille de style, le script, chaque octet non concerné — traverse intact, par construction : on ne
touche que ce qu'on remplace explicitement.

── LA RÈGLE D'IMBRICATION ─────────────────────────────────────────────────────────────────────────
Le script du navigateur appelle `querySelectorAll("[data-fr]")` puis, dans l'ORDRE DU DOCUMENT,
remplace l'`innerHTML` de chaque élément trouvé. Un élément portant `data-fr` et imbriqué dans un
AUTRE élément portant aussi `data-fr` se fait donc détacher du document avant que son propre tour
n'arrive — son remplacement à lui ne s'applique à rien de visible. `_outer_spans()` reproduit cette
règle : seuls les éléments `[data-fr]` qui ne sont imbriqués dans AUCUN autre élément `[data-fr]`
sont effectivement remplacés.

Lancement : `python scripts/build_seo_pages.py`
"""
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")

sys.path.insert(0, ROOT)

VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

FR_TITLE = ("acami — Agence IA. L'IA lit la boîte de réception, "
            "l'humain décide toujours.")
FR_DESCRIPTION = ("acami installe des agents IA qui pré-lisent vos e-mails commerciaux, "
                   "qualifient le lead et rédigent la réponse. Rien n'atteint votre CRM "
                   "sans validation humaine.")


# ── Transformation FR de landing.html ────────────────────────────────────────────────────────
class _DataFrSpanFinder(HTMLParser):
    """
    Repère, dans le texte SOURCE BRUT, les bornes de chaque élément `[data-fr]`.

    N'accumule aucune reconstruction du document : seulement, pour chaque élément fermé, le
    triplet (début du contenu, fin du contenu, valeur brute de l'attribut data-fr — telle
    qu'écrite dans le fichier, échappements compris) quand cet attribut est présent.
    """

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self._line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._line_starts.append(i + 1)
        self.stack = []   # [{tag, content_start, fr}]
        self.spans = []   # [(content_start, content_end, fr_raw)]

    def _abs(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or ""
        content_start = self._abs() + len(raw)
        match = re.search(r'data-fr="([^"]*)"', raw)
        entry = {"tag": tag, "content_start": content_start,
                 "fr": match.group(1) if match else None}
        if tag not in VOID_ELEMENTS:
            self.stack.append(entry)

    def handle_startendtag(self, tag, attrs):
        pass  # self-fermé (<br/>, <img/>…) : aucun contenu interne à remplacer

    def handle_endtag(self, tag):
        end = self._abs()
        if not self.stack:
            return
        if self.stack[-1]["tag"] == tag:
            entry = self.stack.pop()
        elif tag in [e["tag"] for e in self.stack]:
            while self.stack and self.stack[-1]["tag"] != tag:
                self.stack.pop()
            entry = self.stack.pop() if self.stack else None
        else:
            entry = None
        if entry and entry["fr"] is not None:
            self.spans.append((entry["content_start"], end, entry["fr"]))


def _outer_spans(spans: list) -> list:
    """Ne garde que les spans qui ne sont imbriqués dans AUCUN autre span — cf. docstring du module."""
    ordered = sorted(spans, key=lambda s: s[0])
    kept = []
    for span in ordered:
        if kept and span[0] < kept[-1][1] and span[1] <= kept[-1][1]:
            continue  # imbriqué dans le précédent span conservé
        kept.append(span)
    return kept


def _render_french(source: str) -> str:
    """Substitue, dans `source`, chaque élément `[data-fr]` de premier niveau par sa valeur française."""
    finder = _DataFrSpanFinder(source)
    finder.feed(source)
    spans = _outer_spans(finder.spans)

    out = source
    for start, end, fr_raw in sorted(spans, key=lambda s: s[0], reverse=True):
        out = out[:start] + fr_raw + out[end:]

    out = out.replace('<html lang="en">', '<html lang="fr">', 1)
    out = re.sub(r"<title>.*?</title>", f"<title>{html.escape(FR_TITLE)}</title>", out,
                 count=1, flags=re.S)
    out = re.sub(r'(<meta name="description" content=")[^"]*(")',
                 lambda m: m.group(1) + html.escape(FR_DESCRIPTION) + m.group(2), out, count=1)
    # Un lecteur d'écran ou un visiteur clavier arrivant directement sur /fr/ doit trouver l'état
    # des boutons de langue cohérent avec ce qu'il voit, avant même que le script ne s'exécute.
    out = out.replace('data-lang="en" aria-pressed="true"', 'data-lang="en" aria-pressed="false"')
    out = out.replace('data-lang="fr" aria-pressed="false"', 'data-lang="fr" aria-pressed="true"')
    return out


def build_french_page() -> str:
    source = open(os.path.join(STATIC, "landing.html"), encoding="utf-8").read()
    rendered = _render_french(source)
    out_dir = os.path.join(STATIC, "fr")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    return path


# ── Pages pSEO ────────────────────────────────────────────────────────────────────────────────
_PAGE_CSS = """
:root{--paper:#fdfdfd;--ink:#12171c;--ink-2:#4b5560;--ink-3:#636363;--rule:#e3e3e3;
--primary:#125e6b;--accent:#b4622a;--radius:10px}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font-family:Figtree,Inter,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
line-height:1.6}
.wrap{max-width:760px;margin:0 auto;padding:48px 24px 96px}
nav{border-bottom:1px solid var(--rule);padding:16px 24px}
nav a{color:var(--ink);text-decoration:none;font-weight:600;font-size:.9rem}
nav a.back{color:var(--ink-2);font-weight:500;float:right}
h1{font-family:'Instrument Serif',Georgia,serif;font-size:2.1rem;line-height:1.15;margin:.4em 0 .3em}
h2{font-size:1.2rem;margin-top:2.2em}
.kicker{font-family:'Fragment Mono',ui-monospace,monospace;font-size:.72rem;letter-spacing:.08em;
text-transform:uppercase;color:var(--primary)}
.lede{color:var(--ink-2);font-size:1.05rem;max-width:60ch}
p{max-width:68ch}
code{background:#f1f4f5;padding:.1em .35em;border-radius:4px;font-size:.9em}
ol,ul{max-width:66ch;padding-left:1.3em}
li{margin-bottom:.6em}
table{border-collapse:collapse;width:100%;margin:1.4em 0;font-size:.92rem}
th,td{border:1px solid var(--rule);padding:.55em .7em;text-align:left;vertical-align:top}
th{background:#f6f8f8;font-weight:600}
.verified{border-left:3px solid var(--primary);padding:.2em 0 .2em 1em;margin:2em 0;
color:var(--ink-2);font-size:.92rem}
.verified.live{border-left-color:var(--accent)}
.sources{font-family:'Fragment Mono',ui-monospace,monospace;font-size:.78rem;color:var(--ink-3)}
.sources a{color:inherit}
footer{border-top:1px solid var(--rule);padding:32px 24px;text-align:center;
color:var(--ink-3);font-size:.82rem}
footer a{color:inherit}
"""

_NAV_EN = ('<nav><a href="../landing.html">acami</a>'
          '<a class="back" href="../landing.html">&larr; Back to acami.com</a></nav>')
_NAV_FR = ('<nav><a href="../fr/index.html">acami</a>'
          '<a class="back" href="../fr/index.html">&larr; Retour sur acami.com</a></nav>')
_FOOTER_EN = ('<footer>acami — AI agency. '
             '<a href="../legal.html#privacy">Privacy</a> &middot; '
             '<a href="../legal.html#terms">Terms</a></footer>')
_FOOTER_FR = ('<footer>acami — Agence IA. '
             '<a href="../legal.html#privacy">Confidentialité</a> &middot; '
             '<a href="../legal.html#terms">Conditions</a></footer>')

_KIND_LABEL = {
    "integration": ("Integration", "Intégration"),
    "comparison": ("Comparison", "Comparaison"),
    "glossary": ("Glossary", "Glossaire"),
}


def _table_html(rows: list) -> str:
    head, *body = rows
    thead = "".join(f"<th>{html.escape(cell)}</th>" for cell in head)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in body
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"


def _page_html(page: dict, lang: str) -> str:
    fr = lang == "fr"
    title = page["title_fr"] if fr else page["title_en"]
    meta = page["meta_fr"] if fr else page["meta_en"]
    kicker = _KIND_LABEL[page["kind"]][1 if fr else 0]

    body_parts = []
    intro = page.get("intro_fr" if fr else "intro_en")
    if intro:
        body_parts.append(f"<p class=\"lede\">{intro}</p>")

    steps = page.get("how_fr" if fr else "how_en")
    if steps:
        heading = "Comment ça marche" if fr else "How it works"
        items = "".join(f"<li>{step}</li>" for step in steps)
        body_parts.append(f"<h2>{heading}</h2><ol>{items}</ol>")

    table = page.get("table_fr" if fr else "table_en")
    if table:
        body_parts.append(_table_html(table))

    long_body = page.get("body_fr" if fr else "body_en")
    if long_body:
        body_parts.append(long_body)

    verified_text = page.get("verified_fr" if fr else "verified_en")
    live_class = " live" if page.get("verified_live") else ""
    body_parts.append(f'<div class="verified{live_class}">{verified_text}</div>')

    sources = ", ".join(f"<code>{html.escape(f)}</code>" for f in page.get("source_files", []))
    updated_label = "Mis à jour" if fr else "Updated"
    body_parts.append(
        f'<p class="sources">Sources : {sources}'
        f'<br>{updated_label} : {page["updated"]}</p>'
    )

    nav = _NAV_FR if fr else _NAV_EN
    footer = _FOOTER_FR if fr else _FOOTER_EN
    other_lang_href = f"../seo/{page['slug']}.html" if fr else f"../seo/{page['slug']}.fr.html"
    other_lang_label = "English version" if fr else "Version française"

    return (
        f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{html.escape(title)}</title>\n'
        f'<meta name="description" content="{html.escape(meta)}">\n'
        f'<link rel="canonical" href="https://acami.example/seo/{page["slug"]}'
        f'{".fr" if fr else ""}.html">\n'
        f'<style>{_PAGE_CSS}</style>\n</head>\n<body>\n{nav}\n<main class="wrap">\n'
        f'<p class="kicker">{kicker}</p>\n<h1>{html.escape(title)}</h1>\n'
        + "\n".join(body_parts)
        + f'\n<p><a href="{other_lang_href}">{other_lang_label}</a></p>\n'
        f'</main>\n{footer}\n</body>\n</html>\n'
    )


def build_seo_pages() -> list:
    data = json.load(open(os.path.join(STATIC, "seo", "pages.json"), encoding="utf-8"))
    out_dir = os.path.join(STATIC, "seo")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for page in data["pages"]:
        for lang, suffix in (("en", ""), ("fr", ".fr")):
            path = os.path.join(out_dir, f"{page['slug']}{suffix}.html")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_page_html(page, lang))
            written.append(path)
    return written


# ── robots.txt / sitemap.xml ─────────────────────────────────────────────────────────────────
def build_robots_and_sitemap() -> tuple:
    """
    Écrits et INERTES tant qu'aucun domaine n'existe — même statut que le `<link rel="canonical">`
    commenté dans `landing.html`. `sitemap.xml` utilise le même domaine de réservation
    `acami.example` que ce commentaire, pour qu'un seul remplacement (chercher/remplacer le nom de
    domaine) rende les deux actifs le jour venu.
    """
    robots_path = os.path.join(STATIC, "robots.txt")
    with open(robots_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "User-agent: *\n"
            "Allow: /\n\n"
            "Sitemap: https://acami.example/sitemap.xml\n"
        )

    data = json.load(open(os.path.join(STATIC, "seo", "pages.json"), encoding="utf-8"))
    urls = ["", "fr/", "legal.html"]
    for page in data["pages"]:
        urls.append(f"seo/{page['slug']}.html")
        urls.append(f"seo/{page['slug']}.fr.html")

    entries = "\n".join(
        f'  <url><loc>https://acami.example/{u}</loc></url>' for u in urls
    )
    sitemap_path = os.path.join(STATIC, "sitemap.xml")
    with open(sitemap_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'{entries}\n'
            '</urlset>\n'
        )
    return robots_path, sitemap_path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    fr_page = build_french_page()
    print(f"   {os.path.relpath(fr_page, ROOT)}")

    seo_pages = build_seo_pages()
    for path in seo_pages:
        print(f"   {os.path.relpath(path, ROOT)}")

    robots, sitemap = build_robots_and_sitemap()
    print(f"   {os.path.relpath(robots, ROOT)}")
    print(f"   {os.path.relpath(sitemap, ROOT)}")

    print(f"✅ 1 page française + {len(seo_pages)} pages pSEO + robots.txt + sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

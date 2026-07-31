"""
Trousse de composants d'interface (§18) — le vocabulaire visuel réutilisable de l'application.

**Pourquoi ce module existe.** Le §17 a donné à ACA une identité paramétrable (couleurs, logo,
animations). Il n'a pas donné de *grammaire* : chaque écran composait ses propres titres de section,
ses propres cartes, ses propres états vides, avec des `st.markdown("##### …")` et des `st.caption`
disséminés. Résultat, deux blocs jouant le même rôle ne se ressemblaient pas, et le lecteur devait
réapprendre la hiérarchie à chaque onglet. Ce module fournit les quelques briques dont l'application
a réellement besoin, une seule fois, pour que « bloc de décision », « en-tête de section » ou
« écran vide » veuillent dire la même chose partout.

**Le composant signature : le rail de décision** (`decision_rail`). Le métier d'ACA est une
séquence — un e-mail arrive, il est classé, enrichi, une proposition est rédigée, **puis un humain
décide**. Numéroter des étapes est d'ordinaire un tic décoratif ; ici l'ordre porte une information
que le relecteur a besoin de connaître, et le dernier maillon est précisément le geste qu'on lui
demande. Le rail rend donc visible ce que la machine a fait *et* que la décision humaine en est le
terminus, pas une case à cocher accessoire.

**Contrat.** Tout est **pur** : des fonctions qui prennent des données et rendent des chaînes HTML.
Aucun import Streamlit (même posture que `risk_scan.py`, `session.py`, `branding.py`), donc tout est
testable hors ligne et `ui.py`/`app_pages/*` se contentent de `st.html(...)`. Toute valeur venant
d'un e-mail, d'un formulaire ou d'un LLM est **échappée** : ces chaînes finissent dans du HTML
injecté, et le corps d'un e-mail entrant est par définition un contenu potentiellement hostile
(cf. `prompt_guard.py`).

Les styles correspondants vivent dans `branding.py` (`_UI_KIT`), pour qu'il n'existe qu'un seul
endroit d'où sorte la feuille de style de l'application.
"""
from html import escape


def _text(value) -> str:
    """Échappe une valeur quelconque pour insertion dans du HTML (`None` → chaîne vide)."""
    return escape("" if value is None else str(value))


def _icon(name: str) -> str:
    """
    Glyphe Material Symbols en ligne.

    Les `:material/x:` de Streamlit ne fonctionnent que dans ses propres libellés, jamais dans du
    HTML injecté ; la police est en revanche déjà chargée par la page, donc un `<span>` porteur de
    la bonne classe suffit et n'ajoute aucune requête réseau.
    """
    return f'<span class="aca-i" aria-hidden="true">{escape(name)}</span>'


# ── En-têtes de section ───────────────────────────────────────────────────────────────────────
def section(title: str, subtitle: str = "", icon: str = "", eyebrow: str = "") -> str:
    """
    En-tête de section : surtitre optionnel, titre, sous-titre.

    Le surtitre (`eyebrow`) n'est pas là pour décorer : il situe la section dans un ensemble
    (« Étape 2 sur 3 », « Réservé aux administrateurs »). Laissé vide, il ne s'affiche pas — plutôt
    que d'inventer une catégorie pour remplir un gabarit.
    """
    parts = ['<div class="aca-section">']
    if eyebrow:
        parts.append(f'<span class="aca-section__eyebrow">{_text(eyebrow)}</span>')
    parts.append('<h3 class="aca-section__title">')
    if icon:
        parts.append(_icon(icon))
    parts.append(f"<span>{_text(title)}</span></h3>")
    if subtitle:
        parts.append(f'<p class="aca-section__sub">{_text(subtitle)}</p>')
    parts.append("</div>")
    return "".join(parts)


# ── Rail de décision (composant signature) ────────────────────────────────────────────────────
STEP_DONE = "done"
STEP_ACTIVE = "active"
STEP_TODO = "todo"
STEP_ALERT = "alert"


def decision_rail(steps) -> str:
    """
    Chronologie verticale du traitement d'un lead, dont le dernier maillon est la décision humaine.

    `steps` est une séquence de dicts `{"label", "detail", "state", "icon"}` où `state` vaut
    `done`/`active`/`todo`/`alert`. L'état `alert` existe pour les étapes qui se sont *bien*
    déroulées mais exigent une lecture méfiante (clause contractuelle à risque, tentative
    d'injection) : les confondre avec un échec ferait croire à une panne, les confondre avec un
    succès ferait manquer l'avertissement.
    """
    rows = []
    for index, step in enumerate(steps, start=1):
        state = step.get("state", STEP_TODO)
        icon = step.get("icon")
        marker = _icon(icon) if icon else f'<span class="aca-rail__num">{index}</span>'
        detail = step.get("detail")
        rows.append(
            f'<li class="aca-rail__step aca-rail__step--{escape(state)}">'
            f'<span class="aca-rail__marker">{marker}</span>'
            f'<span class="aca-rail__body">'
            f'<span class="aca-rail__label">{_text(step.get("label"))}</span>'
            + (f'<span class="aca-rail__detail">{_text(detail)}</span>' if detail else "")
            + "</span></li>"
        )
    return f'<ol class="aca-rail">{"".join(rows)}</ol>'


# ── Indicateurs ───────────────────────────────────────────────────────────────────────────────
def stat(label: str, value, hint: str = "", tone: str = "") -> str:
    """
    Tuile d'indicateur autonome — à préférer à `st.metric` quand la valeur doit porter une couleur
    de sens : un compteur d'incidents à zéro et un compteur d'incidents à douze ne se lisent pas de
    la même façon. `tone` : `""`/`ok`/`warn`/`danger`.
    """
    classes = "aca-stat" + (f" aca-stat--{escape(tone)}" if tone else "")
    return (
        f'<div class="{classes}">'
        f'<span class="aca-stat__label">{_text(label)}</span>'
        f'<span class="aca-stat__value">{_text(value)}</span>'
        + (f'<span class="aca-stat__hint">{_text(hint)}</span>' if hint else "")
        + "</div>"
    )


def stat_row(stats) -> str:
    """Rangée d'indicateurs. `stats` : séquence de dicts passés tels quels à `stat()`."""
    return f'<div class="aca-stat-row">{"".join(stat(**item) for item in stats)}</div>'


def chip(label: str, tone: str = "", icon: str = "") -> str:
    """Pastille compacte pour un état ou une métadonnée. `tone` : `""`/`ok`/`warn`/`danger`/`info`."""
    classes = "aca-chip2" + (f" aca-chip2--{escape(tone)}" if tone else "")
    return f'<span class="{classes}">' + (_icon(icon) if icon else "") + f"{_text(label)}</span>"


def chip_row(chips) -> str:
    """Rangée de pastilles. `chips` : séquence de dicts passés tels quels à `chip()`."""
    return f'<div class="aca-chip-row">{"".join(chip(**item) for item in chips)}</div>'


# ── États vides ───────────────────────────────────────────────────────────────────────────────
def empty_state(title: str, body: str, icon: str = "inbox", action_hint: str = "") -> str:
    """
    Écran vide qui **oriente** au lieu de constater.

    Un « aucune donnée » sec laisse la personne sans savoir si l'outil est cassé, mal configuré, ou
    simplement neuf. `body` dit pourquoi c'est vide, `action_hint` dit quoi faire ensuite — c'est la
    différence entre un cul-de-sac et une invitation.
    """
    return (
        '<div class="aca-empty">'
        f'<span class="aca-empty__icon">{_icon(icon)}</span>'
        f'<span class="aca-empty__title">{_text(title)}</span>'
        f'<span class="aca-empty__body">{_text(body)}</span>'
        + (f'<span class="aca-empty__hint">{_text(action_hint)}</span>' if action_hint else "")
        + "</div>"
    )


# ── Chronologie d'un lead ─────────────────────────────────────────────────────────────────────
def timeline(events) -> str:
    """
    Frise chronologique d'événements datés (`{"when", "who", "what", "detail", "tone"}`).

    Utilisée pour l'histoire d'un lead : reçu, classé, clarifié, réécrit par Untel, validé par
    Untel. Les données existaient déjà dans le journal d'activité (§17) sans qu'aucune vue ne les
    remette en ordre — or « raconte-moi ce qui est arrivé à ce prospect » est la question qu'on pose
    réellement, pas « liste-moi les lignes de la table ».
    """
    if not events:
        return empty_state(
            "Aucun événement pour ce lead",
            "Le journal se remplit à chaque action humaine : analyse, clarification, modification "
            "du brouillon, validation ou rejet.",
            icon="history",
        )
    rows = []
    for event in events:
        tone = event.get("tone", "")
        rows.append(
            f'<li class="aca-tl__item{f" aca-tl__item--{escape(tone)}" if tone else ""}">'
            f'<span class="aca-tl__dot"></span>'
            f'<span class="aca-tl__when">{_text(event.get("when"))}</span>'
            f'<span class="aca-tl__what">{_text(event.get("what"))}</span>'
            + (f'<span class="aca-tl__who">{_text(event.get("who"))}</span>'
               if event.get("who") else "")
            + (f'<span class="aca-tl__detail">{_text(event.get("detail"))}</span>'
               if event.get("detail") else "")
            + "</li>"
        )
    return f'<ul class="aca-tl">{"".join(rows)}</ul>'


# ── Différentiel de texte ─────────────────────────────────────────────────────────────────────
def diff(original: str, edited: str, max_lines: int = 40) -> str:
    """
    Différentiel ligne à ligne entre le brouillon de l'IA et la version réellement envoyée.

    Le §17 ne consignait que des longueurs de caractères (« 340 → 412 »), ce qui ne dit rien de ce
    qui a changé — alors que c'est exactement ce qu'un responsable veut voir : l'IA a-t-elle promis
    un prix qu'il a fallu corriger ? Le texte intégral vivait déjà dans
    `analytics_store.draft_edits`, il ne manquait que la mise en regard.

    `difflib` de la bibliothèque standard, borné à `max_lines` : un différentiel de trois cents
    lignes n'est plus lu, il est parcouru du regard puis abandonné.
    """
    import difflib

    lines = list(difflib.unified_diff(
        (original or "").splitlines(), (edited or "").splitlines(),
        fromfile="proposition de l'IA", tofile="version envoyée", lineterm="", n=2,
    ))
    if not lines:
        return '<p class="aca-diff__none">Brouillon envoyé tel quel, sans modification.</p>'

    truncated = len(lines) > max_lines
    rendered = []
    for line in lines[:max_lines]:
        if line.startswith(("+++", "---")):
            kind = "meta"
        elif line.startswith("@@"):
            kind = "hunk"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "same"
        rendered.append(f'<div class="aca-diff__line aca-diff__line--{kind}">{_text(line)}</div>')
    if truncated:
        rendered.append(
            '<div class="aca-diff__line aca-diff__line--meta">… différentiel tronqué '
            f'({len(lines) - max_lines} ligne(s) de plus)</div>'
        )
    return f'<div class="aca-diff">{"".join(rendered)}</div>'


# ── Rappel des raccourcis ─────────────────────────────────────────────────────────────────────
def key_hints(pairs) -> str:
    """
    Rappel discret des raccourcis clavier. `pairs` : séquence de `(touche, action)`.

    Affiché près des actions concernées plutôt que dans une page d'aide séparée : un raccourci qu'il
    faut aller chercher n'est jamais appris.
    """
    items = "".join(
        f'<span class="aca-keys__pair"><kbd>{_text(key)}</kbd>{_text(action)}</span>'
        for key, action in pairs
    )
    return f'<div class="aca-keys">{items}</div>'

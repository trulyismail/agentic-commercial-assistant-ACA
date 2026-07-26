"""
Topologie du graphe agentique — **source unique** (§16.1.6 de docs/ACAM_roadmap.md).

Le §12bis signalait un risque : la liste des arêtes du graphe était recopiée à la main en trois
endroits — `aca/core/app.py` (le vrai graphe), `GRAPH_EDGES` dans `ui.py`, et
`dashboard/lib/graph-topology.ts` — donc modifier le graphe obligeait à resynchroniser deux copies,
ou elles divergeaient en silence. **Ce risque s'était déjà matérialisé** : au moment d'écrire ce
module, `ui.py` ne connaissait pas l'arête `supervisor → routing` (le chemin FINISH du superviseur).
Le schéma affiché à l'utilisateur montrait donc un superviseur sans issue vers la suite du
pipeline, et personne ne pouvait s'en apercevoir puisque rien ne comparait les deux listes.

La correction consiste à ne plus jamais recopier la topologie : elle est **dérivée du graphe
compilé lui-même** (`app.get_graph()`), donc juste par construction. Ajouter un nœud dans `app.py`
le fait apparaître ici sans aucune autre modification — seul son libellé français reste à déclarer
dans `NODE_LABELS` (et un test échoue si on l'oublie).

Consommé par :
- `ui.py` — rendu `st.graphviz_chart` (nœud actif en direct pendant `app.stream()`) ;
- `scripts/export_graph.py` — export SVG/JSON pour le README, le one-pager et la documentation n8n.

`to_dot()` vit ici plutôt que dans `ui.py` pour la même raison que le reste : deux consommateurs,
une seule implémentation. Il ne renvoie qu'une chaîne DOT — aucune dépendance Streamlit dans
`aca/core/`.
"""
from aca.core import app as aca_graph

# Libellés français affichés dans le schéma. Seule information réellement « à maintenir » ici : la
# topologie, elle, est dérivée. Un nœud sans libellé retombe sur son nom technique (et
# `tests/test_graph_topology.py` le signale).
NODE_LABELS = {
    "ingestion": "Ingestion", "classifier": "Classifieur", "memory_lookup": "Mémoire",
    "risk_scan": "Risques", "extractor": "Extracteur", "clarification": "Clarification",
    "supervisor": "Superviseur", "enrichissement": "Enrichissement", "connaissance": "Connaissance",
    "veille": "Veille", "stratege": "Stratège", "reflection": "Réflexion", "routing": "Routage",
    "notification": "Notification", "action": "Action",
}

# Ordre d'affichage (de gauche à droite). Purement cosmétique : `get_graph()` renvoie les arêtes
# dans un ordre non topologique, et un rendu qui suit le pipeline se lit beaucoup mieux. Un nœud
# absent de cette liste s'affiche quand même, à la fin.
DISPLAY_ORDER = [
    "ingestion", "classifier", "memory_lookup", "risk_scan", "extractor", "clarification",
    "supervisor", "enrichissement", "connaissance", "veille", "stratege", "reflection",
    "routing", "notification", "action",
]

# Nœuds toujours traversés une fois la pause de validation atteinte (§13/§14) — seuls les workers
# (enrichissement/connaissance/veille/stratege/reflection) sont conditionnels, cf. les
# `add_conditional_edges` du superviseur dans app.py.
FIXED_NODES = {
    "ingestion", "classifier", "memory_lookup", "risk_scan", "extractor", "clarification",
    "supervisor", "routing", "notification",
}

# LangGraph nomme les bornes `__start__`/`__end__` ; on les affiche START/END.
START, END = "START", "END"
_BOUNDARIES = {"__start__": START, "__end__": END}

_cache = {}


def _normalize(node: str) -> str:
    return _BOUNDARIES.get(node, node)


def nodes() -> list:
    """Nœuds réels du graphe (hors START/END), dans l'ordre d'affichage."""
    if "nodes" not in _cache:
        raw = [n for n in aca_graph.app.get_graph().nodes if n not in _BOUNDARIES]
        _cache["nodes"] = sorted(
            raw, key=lambda n: (DISPLAY_ORDER.index(n) if n in DISPLAY_ORDER else len(DISPLAY_ORDER)),
        )
    return list(_cache["nodes"])


def edges() -> list:
    """
    Arêtes `(source, cible, étiquette)` du graphe compilé, bornes normalisées en START/END.

    `étiquette` vaut `None` sauf pour les arêtes conditionnelles portant une valeur de routage
    explicite (`rewrite`/`ok` après la réflexion, `FINISH` depuis le superviseur) : c'est
    l'information qui rend le schéma lisible, et elle vient elle aussi du graphe réel.
    """
    if "edges" not in _cache:
        _cache["edges"] = [
            (_normalize(e.source), _normalize(e.target), e.data if isinstance(e.data, str) else None)
            for e in aca_graph.app.get_graph().edges
        ]
    return list(_cache["edges"])


def label_for(node: str) -> str:
    """Libellé humain d'un nœud (repli sur son nom technique)."""
    return NODE_LABELS.get(node, node)


def to_dot(current: str = None, done: set = frozenset()) -> str:
    """
    Graphe au format DOT — nœud actif en ambre, nœuds déjà franchis en vert, reste en gris.

    Rendu par `st.graphviz_chart` (viz.js côté navigateur : aucune dépendance système Graphviz) et
    par `scripts/export_graph.py`.
    """
    lines = [
        "digraph G {", 'rankdir=LR; bgcolor="transparent"; splines=true;',
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, margin="0.15,0.08"];',
        'edge [color="#9e9e9e", fontname="Helvetica", fontsize=9, fontcolor="#757575"];',
        f'"{START}" [shape=ellipse, style=filled, fillcolor="#616161", fontcolor=white, label="{START}"];',
        f'"{END}" [shape=ellipse, style=filled, fillcolor="#616161", fontcolor=white, label="{END}"];',
    ]
    for node in nodes():
        if node == current:
            fill = "#FFB300"
        elif node in done:
            fill = "#43A047"
        else:
            fill = "#E0E0E0"
        fontcolor = "white" if node in done and node != current else "black"
        lines.append(f'"{node}" [label="{label_for(node)}", fillcolor="{fill}", fontcolor="{fontcolor}"];')
    for source, target, edge_label in edges():
        attrs = []
        if (source, target) == ("reflection", "stratege"):
            attrs.append("style=dashed")
        if edge_label:
            attrs.append(f'label="{edge_label}"')
        suffix = f" [{', '.join(attrs)}]" if attrs else ""
        lines.append(f'"{source}" -> "{target}"{suffix};')
    lines.append("}")
    return "\n".join(lines)


def to_dict() -> dict:
    """
    Topologie sérialisable — consommée par `scripts/export_graph.py` pour produire
    `docs/assets/graph.json` (schéma canonique pour la documentation, le one-pager et n8n).
    """
    return {
        "nodes": [
            {"id": node, "label": label_for(node), "always_traversed": node in FIXED_NODES}
            for node in nodes()
        ],
        "edges": [
            {"source": source, "target": target, "label": edge_label}
            for source, target, edge_label in edges()
        ],
    }

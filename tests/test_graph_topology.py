"""
Tests de la source unique de topologie (§16.1.6).

Raison d'être : la liste des arêtes du graphe était recopiée à la main dans `ui.py`, et cette copie
**avait déjà divergé** du vrai graphe — il lui manquait `supervisor → routing` (le chemin FINISH du
superviseur), si bien que le schéma affiché montrait un superviseur sans issue. Personne ne pouvait
s'en apercevoir : rien ne comparait les deux listes. Ces tests sont précisément cette comparaison.
"""
from aca.core import app as aca_graph
from aca.core import graph_topology


def test_nodes_match_compiled_graph():
    """Aucun nœud inventé, aucun nœud oublié — la liste vient du graphe compilé."""
    compiled = {n for n in aca_graph.app.get_graph().nodes if n not in ("__start__", "__end__")}
    assert set(graph_topology.nodes()) == compiled


def test_edges_match_compiled_graph():
    """Le test qui aurait attrapé la dérive de `ui.py` (arête `supervisor → routing` manquante)."""
    compiled = {
        (graph_topology._normalize(e.source), graph_topology._normalize(e.target))
        for e in aca_graph.app.get_graph().edges
    }
    derived = {(source, target) for source, target, _ in graph_topology.edges()}
    assert derived == compiled


def test_supervisor_finish_edge_is_present():
    """
    Régression explicite du défaut trouvé au §16.1.6 : le superviseur DOIT avoir une issue vers
    `routing` (son chemin FINISH). C'est l'arête qui manquait à la copie manuelle.
    """
    assert ("supervisor", "routing") in {(s, t) for s, t, _ in graph_topology.edges()}


def test_start_and_end_are_normalized():
    """LangGraph nomme les bornes `__start__`/`__end__` ; l'affichage attend START/END."""
    pairs = {(s, t) for s, t, _ in graph_topology.edges()}
    assert ("START", "ingestion") in pairs
    assert ("action", "END") in pairs
    flat = {n for pair in pairs for n in pair}
    assert "__start__" not in flat and "__end__" not in flat


def test_every_node_has_a_french_label():
    """Un nœud ajouté dans app.py sans libellé doit échouer ici, pas s'afficher en anglais en démo."""
    unlabelled = [n for n in graph_topology.nodes() if n not in graph_topology.NODE_LABELS]
    assert unlabelled == [], f"Nœuds sans libellé dans NODE_LABELS : {unlabelled}"


def test_display_order_covers_every_node():
    """Un nœud hors de DISPLAY_ORDER s'afficherait en fin de schéma, silencieusement mal placé."""
    missing = [n for n in graph_topology.nodes() if n not in graph_topology.DISPLAY_ORDER]
    assert missing == [], f"Nœuds absents de DISPLAY_ORDER : {missing}"


def test_fixed_nodes_are_real_nodes():
    """Garde-fou : un nom mal orthographié dans FIXED_NODES serait invisible à l'exécution."""
    unknown = graph_topology.FIXED_NODES - set(graph_topology.nodes())
    assert unknown == set(), f"FIXED_NODES contient des nœuds inexistants : {unknown}"


# ── Rendu DOT ─────────────────────────────────────────────────────────────────────────────────


def test_dot_is_wellformed_and_contains_every_node():
    dot = graph_topology.to_dot()
    assert dot.startswith("digraph G {") and dot.rstrip().endswith("}")
    for node in graph_topology.nodes():
        assert f'"{node}"' in dot


def test_dot_highlights_current_and_done_nodes():
    """Ambre = nœud actif, vert = déjà franchi (le direct pendant `app.stream()`)."""
    dot = graph_topology.to_dot(current="stratege", done={"classifier", "extractor"})
    stratege_line = next(l for l in dot.splitlines() if l.startswith('"stratege" ['))
    classifier_line = next(l for l in dot.splitlines() if l.startswith('"classifier" ['))
    neutral_line = next(l for l in dot.splitlines() if l.startswith('"action" ['))
    assert "#FFB300" in stratege_line       # actif
    assert "#43A047" in classifier_line     # franchi
    assert "#E0E0E0" in neutral_line        # non franchi


def test_rewrite_edge_is_dashed():
    """La boucle de réflexion doit rester visuellement distincte du flux principal."""
    line = next(
        l for l in graph_topology.to_dot().splitlines()
        if l.startswith('"reflection" -> "stratege"')
    )
    assert "style=dashed" in line


# ── Export sérialisable ───────────────────────────────────────────────────────────────────────


def test_to_dict_shape():
    data = graph_topology.to_dict()
    assert set(data) == {"nodes", "edges"}
    assert set(data["nodes"][0]) == {"id", "label", "always_traversed"}
    assert set(data["edges"][0]) == {"source", "target", "label"}
    assert len(data["nodes"]) == len(graph_topology.nodes())


def test_to_dict_is_json_serializable():
    """Consommé tel quel par scripts/export_graph.py → docs/assets/graph.json."""
    import json

    assert json.loads(json.dumps(graph_topology.to_dict())) == graph_topology.to_dict()

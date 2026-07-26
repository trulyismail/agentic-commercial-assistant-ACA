# -*- coding: utf-8 -*-
"""
Export de la topologie du graphe agentique (§16.1.6 de docs/ACAM_roadmap.md).

Produit, à partir du graphe **compilé** (jamais d'une liste recopiée à la main — cf.
`aca/core/graph_topology.py` et la dérive réellement constatée dans `ui.py`) :

    docs/assets/graph.dot          — source DOT, rendable par n'importe quel Graphviz
    docs/assets/graph.json         — topologie sérialisée (README, one-pager, documentation n8n)
    docs/assets/architecture.svg   — uniquement si le binaire `dot` est installé

Graphviz n'est **pas** une dépendance du projet : l'UI Streamlit rend le DOT côté navigateur via
viz.js, précisément pour ne rien imposer à l'installation. Ce script reste donc utile sans lui —
il écrit toujours `.dot` et `.json`, et signale simplement que le SVG n'a pas pu être rendu.

Idempotent : réécrit intégralement ses fichiers de sortie à chaque exécution (même esprit que
`format_sheets.py`).

Lancement : `python scripts/export_graph.py`
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "docs", "assets")

# Ce script vit hors du package `aca/` et se lance en exécution directe, ce qui ne place pas la
# racine du dépôt sur `sys.path` (même contrainte que setup_faq.py). Contrairement à lui, il a
# besoin d'importer `aca.*` : on ajoute donc explicitement la racine.
sys.path.insert(0, ROOT)

from aca.core import graph_topology  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    os.makedirs(ASSETS, exist_ok=True)

    dot_source = graph_topology.to_dot()
    dot_path = os.path.join(ASSETS, "graph.dot")
    with open(dot_path, "w", encoding="utf-8") as handle:
        handle.write(dot_source + "\n")

    topology = graph_topology.to_dict()
    json_path = os.path.join(ASSETS, "graph.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(topology, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"✅ {len(topology['nodes'])} nœuds, {len(topology['edges'])} arêtes "
          "(dérivés du graphe compilé)")
    print(f"   {os.path.relpath(dot_path, ROOT)}")
    print(f"   {os.path.relpath(json_path, ROOT)}")

    dot_binary = shutil.which("dot")
    if not dot_binary:
        print("ℹ️  Binaire Graphviz « dot » absent — SVG non rendu (ce n'est pas une erreur :")
        print("   Graphviz n'est pas une dépendance du projet, l'UI rend le DOT via viz.js).")
        print("   Pour l'obtenir : installez Graphviz puis relancez ce script.")
        return 0

    svg_path = os.path.join(ASSETS, "architecture.svg")
    try:
        subprocess.run(
            [dot_binary, "-Tsvg", "-o", svg_path, dot_path], check=True, capture_output=True,
        )
        print(f"   {os.path.relpath(svg_path, ROOT)}")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Échec du rendu SVG : {e.stderr.decode('utf-8', 'replace')[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
Export du schéma OpenAPI vers `docs/openapi.json` (§16.1.5 de docs/ACAM_roadmap.md).

Pourquoi un fichier commité alors que FastAPI sert déjà `/openapi.json` ? Parce que **§15.3.3 coupe
cette route dès `ACA_ENV=production`** (sauf `ACA_ENABLE_DOCS=1` explicite) : sur un déploiement
réel, n8n — ou n'importe quel client — ne peut donc pas aspirer le schéma depuis le serveur. Le
fichier versionné est le seul chemin fiable, et il a l'avantage d'être lisible dans une revue de
code : un diff sur `docs/openapi.json` montre noir sur blanc qu'un contrat d'API a changé.

Usage côté n8n : nœud « HTTP Request » → *Import* depuis ce fichier, ou simplement s'y référer pour
construire les appels à la main (cf. n8n/README.md).

Idempotent : réécrit intégralement le fichier à chaque exécution.

Lancement : `python scripts/export_openapi.py`
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Même contrainte de `sys.path` que export_graph.py : exécution directe depuis `scripts/`.
sys.path.insert(0, ROOT)

# Le schéma doit décrire l'API COMPLÈTE, indépendamment de l'environnement de la machine qui
# l'exporte : on force donc le mode développement le temps de l'import, sinon `prod_check.enforce()`
# refuserait de charger le module sur une machine configurée en production sans clé d'API.
os.environ.setdefault("ACA_ENV", "development")

from aca.api import api  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    schema = api.openapi()
    output = os.path.join(ROOT, "docs", "openapi.json")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(schema, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    paths = schema.get("paths", {})
    operations = sum(len(methods) for methods in paths.values())
    print(f"✅ Schéma OpenAPI {schema.get('openapi')} exporté — "
          f"{len(paths)} chemins, {operations} opérations.")
    print(f"   {os.path.relpath(output, ROOT)}")
    for path in sorted(paths):
        methods = ", ".join(sorted(m.upper() for m in paths[path]))
        print(f"     {methods:<12} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

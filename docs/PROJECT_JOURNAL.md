# Journal de bord du projet ACA

Ce document est le carnet de bord du projet, tenu à jour à chaque changement important. Il explique,
en langage simple, **ce qui a été fait, pourquoi, et ce qui a été essayé et rejeté**. Il sert de
matière première pour le rapport de stage / mémoire final (chapitres, sommaire, cas d'usage,
schémas, résumé) et pour la présentation qui l'accompagnera.

Chaque entrée répond à trois questions : qu'est-ce qu'on a changé, pourquoi c'était nécessaire, et
qu'est-ce que ça veut dire concrètement (expliqué simplement, sans jargon non défini).

---

## 2026-07-11 — Réorganisation du projet en dossiers

### Le problème

Le projet contenait environ 25 fichiers Python, plus des fichiers de données et de documentation,
tous rangés en vrac directement à la racine du dossier — un peu comme si tous les outils d'un
atelier (marteaux, tournevis, plans, factures) étaient posés en pile au même endroit au lieu d'être
rangés dans des tiroirs étiquetés. Ça fonctionnait, mais c'était difficile à parcourir visuellement
et ça ne donnait pas une image "professionnelle" de l'organisation du code.

### Ce qui a été fait

Le code a été réorganisé en un vrai **package Python** nommé `aca/`, avec des sous-dossiers par
rôle :

- `aca/core/` — le cerveau du projet : le graphe LangGraph (`app.py`) et les scripts qui tournent
  en tâche de fond ou sur planification (`poller.py`, `relance.py`, `retention.py`).
- `aca/agents/` — les agents spécialisés appelés par le superviseur (`enrichment.py`, `veille.py`).
- `aca/integrations/` — tout ce qui parle à un service extérieur : Google Sheets, Gmail, la base
  vectorielle Supabase, les notifications (`sheets.py`, `gmail_reader.py`, `vector_store.py`,
  `notify.py`).
- `aca/ingestion/` — la chaîne qui transforme un document (PDF/Word/Excel) en texte exploitable
  (`ingest.py`, `pdf_reader.py`, `attachment_reader.py`).
- `aca/storage/` — les petits registres locaux en SQLite (`analytics_store.py`, `audit_log.py`,
  `queue_store.py`, `followup_store.py`).
- `aca/eval/` — le jeu de test du classifieur (`eval_classifier.py`, `eval_dataset.json`).
- `scripts/` — les scripts ponctuels de mise en place qui ne font pas partie du programme principal
  (`setup_sheets.py`, `setup_faq.py`, `format_sheets.py`).
- `docs/` — la documentation longue (`ACAM_roadmap.md`, `ACA project description.md`, et ce
  journal).
- `data/` — les fichiers de données locales (SQLite) qui étaient avant éparpillés à la racine.

`ui.py` (l'application Streamlit que l'utilisateur ouvre dans son navigateur) reste à la racine du
projet exprès, pour que la commande pour la lancer ne change pas (`streamlit run ui.py`).

### Pourquoi ce n'était pas juste "déplacer des fichiers"

En Python, quand un fichier fait `import sheets`, l'interpréteur va chercher un fichier
`sheets.py` au même endroit que le fichier qui l'appelle (ou dans son "chemin de recherche").
Déplacer `sheets.py` dans un sous-dossier sans rien changer d'autre aurait cassé tous les fichiers
qui l'importaient — comme déplacer un livre dans une autre pièce de la bibliothèque sans mettre à
jour la fiche qui dit où il se trouve.

La solution : chaque `import` a été réécrit pour pointer vers le nouvel emplacement
(`from aca.integrations import sheets` au lieu de `import sheets`), et les commandes pour lancer
les scripts ont changé de forme — `python app.py` devient `python -m aca.core.app`. Le `-m` dit à
Python "cherche ce module dans le dossier où je me trouve actuellement", ce qui fait que tout
continue à fonctionner sans avoir besoin d'installer quoi que ce soit en plus.

### Vérifications faites

- Les 5 e-mails factices du mode test (`python -m aca.core.app`) sont passés par tout le pipeline
  (classification → mémoire → extraction → superviseur → agents → pause de validation) sans
  erreur, exactement comme avant.
- Le test de précision du classifieur (`python -m aca.eval.eval_classifier`) a redonné exactement
  le même score qu'avant : 96 % (48/50).
- La vraie commande utilisateur, `streamlit run ui.py`, a été lancée en tâche de fond et a répondu
  correctement (HTTP 200, aucune erreur d'import).
- Les fichiers de données SQLite existants (`checkpoints.sqlite`, `queue.sqlite`,
  `analytics.sqlite`) ont été déplacés dans `data/` sans perte — pas de nouveaux fichiers vides
  créés par erreur à l'ancien emplacement.
- Tous les liens et commandes dans `CLAUDE.md`, `docs/ACAM_roadmap.md` et `README.md` ont été mis à
  jour pour pointer vers les nouveaux emplacements.

### Décision prise et pourquoi

Option retenue : garder le projet **sans installation** (pas de `pip install`), en utilisant
uniquement `python -m` pour les scripts internes au package, plutôt que de transformer le projet en
paquet Python installable (qui aurait demandé un fichier `pyproject.toml` et une étape
d'installation supplémentaire). Raison : le projet est un prototype de stage de 8 semaines, pas une
librairie destinée à être publiée — ajouter une étape d'installation aurait été une complexité
inutile par rapport au bénéfice.

### Rien n'a été cassé — comment on le sait

Avant de commencer, l'état de git était propre (aucune modification en attente). Chaque fichier a
été déplacé avec `git mv` (qui garde l'historique du fichier, contrairement à un simple
déplacement). Après le déplacement, `git status` montre bien 25 fichiers en "renommage" (`R`), pas
en "suppression + création" — ce qui veut dire que git a bien reconnu qu'il s'agissait des mêmes
fichiers, juste déplacés.

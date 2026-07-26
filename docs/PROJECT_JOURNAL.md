# Journal de bord du projet ACA

Ce document est le carnet de bord du projet, tenu à jour à chaque changement important. Il explique,
en langage simple, **ce qui a été fait, pourquoi, et ce qui a été essayé et rejeté**. Il sert de
matière première pour le rapport de stage / mémoire final (chapitres, sommaire, cas d'usage,
schémas, résumé) et pour la présentation qui l'accompagnera.

Chaque entrée répond à trois questions : qu'est-ce qu'on a changé, pourquoi c'était nécessaire, et
qu'est-ce que ça veut dire concrètement (expliqué simplement, sans jargon non défini).

---

## 2026-07-11 (suite) — Une vraie grande FAQ, un seuil calibré, et un bug de RAG "fantôme" trouvé

### Le problème de départ

La FAQ (base de connaissances) du projet ne contenait que 2 questions/réponses jouets ("délai de
livraison", "tarifs"). Le "Known gaps" du projet notait déjà que le seuil de similarité du RAG
(la fonction qui décide si une question du prospect ressemble assez à une entrée de la FAQ pour
y répondre) était réglé trop bas : même une question totalement hors-sujet comme "recette de
tarte aux pommes" "matchait" avec la FAQ. Avec seulement 2 lignes, impossible de savoir où
placer un bon seuil — il n'y avait pas assez de variété pour voir la différence entre une
question pertinente et une question hors-sujet.

### Ce qui a été fait

**1. Une vraie FAQ de 74 questions/réponses**, réparties en 10 catégories réalistes pour une
entreprise B2B (tarifs, fonctionnalités, sécurité/RGPD, support/SLA, intégrations, mise en route,
démo/essai, comptes/utilisateurs, contrat/résiliation, plateforme technique). Écrite dans le vrai
Google Sheets (onglet FAQ) et dans `scripts/setup_faq.py` pour pouvoir la re-générer plus tard.

**2. Un calibrage du seuil basé sur de vraies mesures, pas une estimation.** On a demandé à
Gemini (le modèle qui transforme du texte en vecteurs numériques, l'"embedding") de mesurer à
quel point 8 questions reformulées (proches de vraies questions de la FAQ, mais pas identiques
mot pour mot) et 6 questions totalement hors-sujet ("quelle est la capitale de l'Australie ?")
ressemblent à chaque entrée de la FAQ. Résultat très net :
- Questions pertinentes : score de similarité entre 0.73 et 0.80.
- Questions hors-sujet : score entre 0.56 et 0.61.

Il y avait un vrai "trou" entre les deux groupes. Le seuil a été fixé à **0.65**, confortablement
entre les deux — assez haut pour rejeter les questions hors-sujet, assez bas pour garder les
questions reformulées.

**3. Un bug réel trouvé en vérifiant "est-ce que le RAG utilise vraiment la vraie base de
données ?"** — question posée directement par l'utilisateur. Bonne question, car la réponse
était non : depuis la migration vers Supabase (session précédente), le RAG était censé utiliser
une vraie base de données partagée (pgvector sur Supabase) plutôt qu'un cache local à chaque
processus. En vérifiant concrètement (en listant les tables et leur contenu dans Supabase), on a
découvert que la table `faq_embeddings` ne contenait que 2 lignes périmées — alors que la FAQ en
avait 74. Le RAG utilisait donc, en silence, un repli local (qui fonctionne, mais qui n'est pas
partagé entre les différents processus de l'application — poller, interface web, etc. — exactement
le problème que la migration Supabase devait résoudre).

**Cause du bug** : dans `sheets.py`, la ligne qui importe le module `vector_store` (qui lit la clé
de connexion `DATABASE_URL`) s'exécutait **avant** la ligne qui charge le fichier `.env`. En
Python, une variable définie au niveau du fichier (pas dans une fonction) n'est calculée qu'une
seule fois, au moment où le fichier est chargé pour la première fois — donc `vector_store` "voyait"
une clé de connexion vide et se désactivait silencieusement, sans erreur, pour le reste de
l'exécution. Un peu comme si on demandait l'adresse d'un ami avant qu'il ait eu le temps de nous
la donner, puis qu'on refusait de lui redemander plus tard.

**Correction** : (1) réordonner `sheets.py` pour charger `.env` avant d'importer `vector_store` ;
(2) rendre `vector_store.py` plus robuste en lisant la clé de connexion à chaque appel plutôt
qu'une seule fois au chargement — pour que ce type de bug ne puisse plus se reproduire, même si
un autre fichier l'importe dans un ordre différent à l'avenir.

### Vérifications faites

- Après correction, `vector_store.is_enabled()` renvoie bien `True` dès le premier import.
- La table `faq_embeddings` sur Supabase contient maintenant les 74 vraies lignes (vérifié par
  une lecture directe de la base, pas juste via le code de l'application).
- Le test de bout en bout (`search_knowledge_base_semantic`) renvoie de bons résultats pour des
  questions reformulées et un résultat vide pour des questions hors-sujet (ce qui déclenchera
  correctement l'agent `veille` de recherche web quand la FAQ ne sait pas répondre).
- Suite de test complète (5 e-mails factices) et test de précision du classifieur (96 %, 48/50)
  ré-exécutés après la correction : aucune régression.

### Ce que ça veut dire concrètement

Avant cette session, la fonctionnalité "base de données vectorielle partagée" existait dans le
code et avait été vérifiée avec succès **une fois, dans un test isolé** — mais ne fonctionnait
jamais réellement une fois l'application démarrée normalement, à cause d'un problème d'ordre de
chargement invisible. C'est un bon exemple de pourquoi "vérifié une fois en test" ne veut pas
toujours dire "fonctionne en production" : le contexte d'exécution (quel fichier importe quoi, et
dans quel ordre) peut changer le résultat sans qu'aucune erreur ne s'affiche.

### Question en attente (à traiter dans une prochaine étape)

L'utilisateur a demandé si la FAQ pouvait être personnalisée par entreprise prospecte plutôt que
générique, et si le système "se souvient" déjà des entreprises déjà recherchées. Réponse donnée en
conversation (à formaliser ici dans une prochaine entrée) : le projet a déjà deux mémoires
distinctes qui répondent à des besoins différents — la FAQ (onglet Google Sheets + `faq_embeddings`
sur Supabase) est la base de connaissances de **notre** entreprise (utilisée pour répondre aux
questions du prospect sur nos tarifs/fonctionnalités), tandis que l'agent `enrichment.py` +
l'onglet `Enrichissement_Cache` fait déjà exactement ce qui était demandé pour **l'entreprise du
prospect** : recherche web à la première rencontre, mise en cache par nom de domaine, réutilisation
immédiate (sans nouvelle recherche) si la même entreprise recontacte plus tard.

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

---

## 2026-07-12 — Un vrai CRM (HubSpot), en plus de Google Sheets

### Le problème de départ

Jusqu'ici, le "CRM" du projet, c'est-à-dire l'endroit où on garde la trace de chaque lead validé
(client potentiel), c'était un onglet Google Sheets ("Leads"). Ça fonctionne, mais ce n'est pas un
vrai outil de CRM (pas de pipeline de vente, pas de suivi de statut par étape, pas d'historique de
contact structuré). L'utilisateur a donc demandé de connecter le projet à HubSpot, un vrai logiciel
de CRM, en donnant une clé d'accès ("access token") à mettre dans le fichier `.env`.

### La question à trancher : remplacer Sheets, ou les deux en même temps ?

Deux options possibles :
- **Remplacer complètement** Google Sheets par HubSpot pour les leads : plus propre à terme, mais
  ça veut dire aussi réécrire la détection "ce client a-t-il déjà écrit avant ?" (`find_leads_by_sender`)
  et le tableau de bord (onglet "Tableau de bord" de l'interface) pour qu'ils lisent HubSpot au lieu
  de Sheets — beaucoup plus de travail, et plus risqué à faire d'un coup.
- **Faire tourner les deux en parallèle** ("alongside") pendant une période de transition : chaque
  lead validé est écrit à la fois dans Sheets (comme avant) et dans HubSpot (nouveau). Sheets reste
  la mémoire utilisée par le reste du programme (détection de doublons, tableau de bord), HubSpot
  devient le vrai outil que l'équipe commerciale utilisera au quotidien.

Décision : **les deux en parallèle**, pour rester cohérent avec la façon dont tout le reste du
projet a été construit — chaque nouvelle brique externe (Tavily pour la recherche web, Gemini pour
les embeddings, Supabase pour la base vectorielle) a toujours été ajoutée en **plus** de ce qui
existait déjà, jamais en cassant l'existant, avec un repli silencieux si la clé n'est pas configurée.

### Ce qui a été fait

**Un nouveau fichier, `aca/integrations/hubspot.py`**, qui fait le miroir de la fonction qui
écrivait déjà dans Sheets (`sheets.append_lead`). Concrètement, quand un lead est validé par un
humain (`action_node`, après le clic sur "Valider" dans l'interface), le programme :
1. Cherche si un "Contact" avec cet e-mail existe déjà dans HubSpot ; sinon il le crée, avec le nom
   de l'entreprise et le nom du contact extraits par l'IA.
2. Crée une "opportunité commerciale" ("Deal" en anglais, le terme HubSpot) associée à ce contact.
3. Ajoute une "Note" sur ce deal avec l'urgence, le besoin exprimé par le prospect, et le brouillon
   de réponse déjà rédigé par l'IA — pour que le commercial ait tout le contexte directement dans
   HubSpot, sans avoir à rouvrir l'e-mail d'origine.

Si la clé `HUBSPOT_ACCESS_TOKEN` n'est pas configurée dans `.env`, cette fonction ne fait
simplement rien et renvoie "aucune écriture" — exactement le même principe de repli silencieux que
partout ailleurs dans le projet (Tavily, Slack, etc.).

### Un vrai bug trouvé pendant la vérification (et pourquoi c'était important de tester en vrai)

Pour être sûr que ça marche vraiment (pas juste "ça compile"), un vrai contact + deal + note de
test ont été créés dans le compte HubSpot réel de l'utilisateur (avec son accord), puis supprimés
juste après pour ne rien laisser traîner.

Premier essai : plantage. La cause n'avait rien à voir avec HubSpot lui-même — c'était le
`print()` qui affiche "→ Deal créé..." dans le terminal, qui contient une flèche spéciale
(caractère Unicode). Sur ce PC Windows, le terminal utilisé pour lancer le script n'accepte pas ce
caractère et fait planter le programme *après* que l'écriture dans HubSpot ait déjà réussi. Comme
cette fonction est appelée depuis un morceau du programme qui **réessaie automatiquement en cas
d'erreur** (jusqu'à 3 fois, pour survivre à de vraies pannes réseau), un plantage sur un simple
message d'affichage aurait pu déclencher une nouvelle tentative complète — et donc créer **le
même lead deux fois**, à la fois dans Sheets et dans HubSpot. C'est exactement ce qui s'est passé
pendant le test : deux deals de test identiques sont apparus dans HubSpot au lieu d'un seul.

**Correction** : la fonction a été réorganisée pour que "je renvoie le résultat de l'écriture
HubSpot" ne dépende plus jamais de "est-ce que l'affichage du message de succès a marché ?" — les
deux choses sont maintenant complètement séparées, avec un message de secours sans caractères
spéciaux si l'affichage normal échoue. Retesté ensuite avec succès (un seul deal créé, aucun
plantage), puis supprimé comme les précédents.

**Ce qu'il faut retenir** : ce genre de bug (une erreur d'affichage qui fait planter un
programme après qu'il ait déjà fait le travail important) est le genre de chose qu'on ne peut
repérer qu'en testant pour de vrai contre le vrai service — un simple relecture du code ne
l'aurait pas révélé, puisque le code "a l'air correct" à l'œil nu.

---

## 2026-07-12 (suite) — Un document de conseils généré par IA, passé au crible, et deux vraies améliorations gardées

### Le point de départ

L'utilisateur a reçu (d'un outil externe, pas de ce projet) un document listant des suggestions
d'amélioration pour le pipeline RAG et l'architecture du graphe — écrit par une IA qui n'avait
jamais vu le vrai code, seulement une description du projet. Il a demandé de vérifier ce document
et d'appliquer les améliorations pertinentes.

### Première étape : vérifier avant de croire

Avant de changer quoi que ce soit, chaque suggestion du document a été comparée au code réel :

- **Deux suggestions étaient déjà faites.** Le document proposait une "fusion RAG hybride" (combiner
  recherche par sens/embeddings et recherche par mots-clés exacts) et une "zone ambre" (une zone de
  confiance intermédiaire entre "j'ai confiance" et "je rejette", plutôt qu'une coupure nette). Les
  deux existaient déjà dans `sheets.py`, écrites lors d'une session précédente le même jour mais pas
  encore documentées ici — corrigé dans cette entrée et dans `CLAUDE.md`.
- **Une suggestion était déjà résolue différemment.** Le document proposait un système de "hash de
  ligne" pour ne recalculer les embeddings Gemini que sur les lignes de FAQ modifiées, plus un
  webhook Google Apps Script pour déclencher la synchronisation automatiquement. Le projet fait déjà
  l'équivalent (comparaison du contenu complet de la FAQ à chaque appel, recalcul uniquement si ça a
  changé) sans avoir besoin d'un script externe à déployer séparément dans Google — pas la peine de
  dupliquer ce qui marche déjà.
- **Le reste étaient de vraies nouvelles fonctionnalités, pas de simples "réglages".** Un filtrage
  par catégorie métier (nécessite de re-taguer toute la FAQ), un système d'apprentissage par exemples
  passés validés (nécessite une nouvelle table), et une boucle d'auto-vérification du brouillon
  ("reflection"). Vu que ce projet est un prototype de stage de 8 semaines déjà très chargé en
  fonctionnalités (voir la section "Known gaps" de `CLAUDE.md`), plutôt que de tout construire sans
  discussion, une question a été posée à l'utilisateur pour choisir lesquelles valaient la peine.
  Réponse : la boucle d'auto-vérification, et la "décontextualisation" des questions de recherche.

### Amélioration 1 : le nœud « Reflect » (auto-vérification du brouillon)

**Le problème que ça résout** : le Stratège (l'IA qui rédige la proposition commerciale) peut se
tromper — par exemple répéter deux fois la même information avec des détails légèrement différents,
ou affirmer un prix qui ne correspond pas exactement à ce qu'il y a dans la FAQ. Avant, ce genre
d'erreur n'était détecté que par l'humain au moment de valider.

**Ce qui a été ajouté** : après que le Stratège ait écrit son brouillon, un nouveau nœud
(`reflection_node`) relit le brouillon face à la FAQ qui a servi à le rédiger, avec un modèle plus
petit et plus rapide (Llama 8B — c'est une simple vérification, pas besoin du gros modèle). Deux
réponses possibles :
- "OK" → le brouillon part directement vers la suite du circuit (transfert au bon service pour
  SUPPORT/AUTRE, notification, puis pause de validation humaine).
- "REWRITE : <raison>" → le brouillon repart vers le Stratège, avec la raison en note, pour qu'il le
  réécrive.

**Le garde-fou anti-boucle infinie** : si les deux IA n'étaient jamais d'accord, le programme
pourrait tourner en boucle indéfiniment (réécrire, se faire recritiquer, réécrire encore...). Pour
éviter ça, une seule réécriture est autorisée : après ça, le brouillon passe tel quel, quoi qu'en
dise le relecteur. Ce n'est pas un problème pour la sécurité du processus, puisque l'humain reste de
toute façon le dernier filtre avant que quoi que ce soit ne parte réellement (la pause de validation
existait déjà avant cette fonctionnalité).

**Vérifié en conditions réelles** : sur un e-mail de test, le Stratège a écrit un brouillon
mentionnant deux fois la même information (le délai de mise en place) avec des formulations
différentes. Le relecteur l'a repéré, a demandé une réécriture avec une raison précise, le Stratège
a réécrit, et le relecteur a laissé passer la deuxième version (garde-fou anti-boucle : après une
réécriture, on ne relance plus le débat). Séquence complète observée dans les logs, sans plantage ni
boucle infinie.

### Amélioration 2 : la "décontextualisation" des questions envoyées au RAG

**Le problème que ça résout** : avant, la recherche dans la FAQ (le RAG) utilisait l'e-mail brut du
prospect tel quel — objet + corps du message, formules de politesse comprises ("Bonjour, j'espère
que vous allez bien, je voulais savoir..."). Ce bruit dilue la comparaison mathématique entre la
question et les entrées de la FAQ (l'"embedding", un vecteur de nombres qui représente le sens du
texte). Pire : si un client récurrent écrit "et pour cette option-là, ça donne quoi ?", en faisant
référence à un échange précédent, l'e-mail brut ne contient aucune information sur "cette option-là"
— impossible pour le RAG de deviner de quoi il s'agit.

**Ce qui a été ajouté** : une fonction (`_build_rag_query`) qui construit la requête envoyée au RAG
autrement :
1. Elle utilise en priorité le `besoin_principal` déjà extrait par l'IA d'extraction (un résumé
   propre du besoin du prospect, débarrassé des formules de politesse) — cette information existe
   déjà dans le programme, elle n'était simplement pas réutilisée ici.
2. Si le client est reconnu comme un client récurrent (le programme le sait déjà, via la mémoire
   CRM), une IA rapide (Llama 8B) reformule le besoin en une question autonome, en s'appuyant sur le
   résumé de l'historique de ce client, pour résoudre les références implicites ("cette option-là" →
   "l'option Enterprise à 50 licences dont on a parlé la dernière fois").
3. Si aucun besoin n'a pu être extrait, on retombe sur l'ancien comportement (e-mail brut) — aucune
   régression possible.

**Vérifié en conditions réelles** (trois cas testés séparément) :
- Nouveau contact, besoin déjà clair → utilisé tel quel, aucun appel IA supplémentaire (pas de coût
  inutile).
- Client récurrent avec référence implicite ("Et pour cette option-là, ça donne quoi ?", avec un
  historique mentionnant un devis de 50 licences Enterprise) → reformulé automatiquement en "Quel est
  le résultat de l'option entreprise pour 50 licences ?", une question autonome et précise.
- Aucun besoin extrait → repli sur l'e-mail brut, comme avant.

### Ce qui n'a volontairement pas été fait

Le document de conseils suggérait aussi : un filtrage de la FAQ par catégorie métier, une table
d'apprentissage par exemples passés validés, et un webhook Google Apps Script pour automatiser la
synchronisation Sheets↔Supabase. Ces trois idées ont été jugées être de vraies nouvelles
fonctionnalités (pas de simples réglages), demandant chacune un vrai travail de conception (nouvelle
table, nouveau schéma, ou déploiement externe à Google). Elles n'ont pas été construites sans
qu'on en discute d'abord — cohérent avec la façon dont ce projet évite d'ajouter des choses "juste
au cas où" sans qu'elles soient vraiment demandées.

---

## 2026-07-12 (suite 2) — Remise à plat de la feuille de route : audit complet + une phase « commercialisation » ajoutée pour plus tard

### Le point de départ

Deux choses se sont combinées. D'abord, la question « est-ce que le plan ACAM v2 est terminé, ou
manque-t-il quelque chose ? » a déclenché un audit complet de la feuille de route
(`docs/ACAM_roadmap.md`) contre le code réel — et cet audit a révélé que le document était en
retard sur la réalité : plusieurs choses marquées « à faire » étaient en fait terminées et vérifiées
depuis des sessions précédentes. Ensuite, l'utilisateur a reçu un **second document de conseils
externe** (généré par une IA n'ayant jamais vu le code), cette fois sur la
**commercialisation** du projet : comment le transformer un jour en produit vendable à plusieurs
entreprises (SaaS). Consigne : l'ajouter au plan, mais clairement marqué « seulement après avoir
fini le cœur du projet », et vérifier chaque suggestion contre le code avant de l'écrire.

### 1. Les cases à jour (le plan reflète enfin la réalité)

Exemples de statuts corrigés dans la feuille de route (choses faites mais encore marquées « à
faire ») : le remplacement de la mémoire volatile `MemorySaver` par une vraie sauvegarde sur disque
puis sur Supabase (fait depuis longtemps, encore décrit comme manquant à trois endroits), le
`RetryPolicy` (réessai automatique en cas de panne réseau), le traitement automatique par lot des
e-mails (le poller), le vrai CRM HubSpot, et le nœud « Reflect » construit le matin même. Le schéma
du graphe en tête de document a aussi été redessiné : il lui manquait quatre nœuds ajoutés au fil
des sessions (veille, routage, notification, reflection).

**Pourquoi c'est important** : ce document sert de base au rapport de stage. Un plan qui dit « à
faire » sur des choses terminées et vérifiées ferait sous-estimer le travail accompli — et
inversement, un plan qui ne liste pas ce qui manque vraiment donnerait une fausse impression de
complétude.

### 2. Une nouvelle section « dette technique » (§11.6) — ce qui manque VRAIMENT au cœur

L'audit a produit une liste honnête de ce qui reste avant toute suite, classée par importance :
1. **Une suite de tests automatisée** (le manque n°1 : aujourd'hui, chaque changement oblige à
   revérifier tout à la main avec les e-mails de test).
2. **Exercer les chemins jamais joués en réel** : plusieurs fonctionnalités sont codées et testées
   côté « repli gracieux » (que se passe-t-il sans clé API), mais jamais avec de vraies références
   (vraie clé Tavily, vrai canal Slack, vraie relance sur un vrai fil Gmail...).
3. Quelques solidifications techniques (réessai sur les écritures de la file d'attente, extraction
   JSON plus stricte, exemples dans les prompts, score de confiance de classification).
4. Le rapport de stage et le backlog Scrum (volontairement gardés pour la fin).

### 3. Une nouvelle section « P3 — Commercialisation » (§12) — le futur lointain, audité avant d'être écrit

Chaque suggestion du document externe a d'abord été vérifiée contre le code (en cherchant
concrètement dans les fichiers), avec un verdict par item. Résultat : sur 9 suggestions, **1 était
déjà entièrement construite** (les points de contrôle humains — c'est littéralement le cœur du
projet depuis le premier jour, le document ne pouvait pas le savoir), **2 partiellement** (le
journal d'audit existe mais n'a pas d'écran de consultation ; la « trace de raisonnement » existe
mais pas le graphe visuel animé), et **6 sont réellement nouvelles** : l'isolation multi-clients
(pour que l'entreprise A ne voie jamais les données de l'entreprise B), le suivi de consommation +
facturation, le panneau de réglages sans fichier technique, le tableau de bord client dédié, la
stratégie d'intégration n8n (garder le code Python comme « cerveau », n8n comme enveloppe), et la
supervision d'infrastructure.

Chaque item porte aussi un « ⚠️ point de vigilance » quand la suggestion entre en conflit avec la
contrainte zéro euro du stage (Stripe, modèles payants, Grafana) ou risquerait de faire reconstruire
quelque chose qui existe déjà. Un ordre de dépendance est proposé (le multi-clients d'abord — tout
le reste en dépend), pour que le choix de « par quoi commencer » se fasse en connaissance de cause.

**La règle retenue** : cette phase P3 ne démarre qu'une fois la dette technique du §11.6 soldée. Un
prototype qui n'a pas de tests automatisés n'a rien à faire devant des clients payants.

---

## 2026-07-12 (suite 3) — La suite de tests automatisée : 84 vérifications rejouables en 2 secondes

### Le problème de départ

C'était le manque n°1 identifié par l'audit du même jour (§11.6 de la feuille de route) : le projet
n'avait **aucun test automatisé**. Chaque fois qu'on ajoutait ou modifiait quelque chose (le nœud
Reflect le matin même, HubSpot la veille...), il fallait relancer à la main les 5 e-mails de
démonstration et relire les logs pour vérifier que rien d'autre n'avait cassé. Ça fonctionne, mais
c'est lent, ça dépend de la vigilance humaine, et ça consomme de vrais appels d'API à chaque fois.

### Ce qu'est un test automatisé (expliqué simplement)

Un test automatisé est un petit programme qui vérifie qu'un morceau précis du logiciel fait bien ce
qu'il doit faire — par exemple : « si le classificateur renvoie un mot inconnu, le programme doit le
ranger dans AUTRE, pas planter ». On écrit cette vérification une fois, et ensuite on peut la
rejouer à volonté en une commande (`python -m pytest tests/`). Si un changement futur casse ce
comportement, le test échoue immédiatement et dit exactement où.

### Le défi particulier de ce projet : tester sans toucher à rien de réel

Le programme parle à beaucoup de services externes (Groq pour l'IA, Google Sheets, Gmail, Supabase,
HubSpot, Tavily). Des tests qui appelleraient les vrais services seraient lents, coûteux, et
risqueraient d'écrire des données de test dans le vrai CRM. La solution, en deux parties :

1. **Des « faux LLM »** : au lieu du vrai modèle d'IA, les tests branchent un objet factice qui
   répond ce qu'on lui dit de répondre ("DEVIS", "OK", "REWRITE: telle raison"...). On teste ainsi
   la **logique du programme autour de l'IA** (les garde-fous, les replis, le câblage), pas l'IA
   elle-même — qui, elle, est déjà mesurée séparément par l'évaluation du classificateur (96 %).
   Un faux spécial ("ExplodingLLM") plante volontairement s'il est appelé — il sert à prouver
   qu'un chemin donné n'appelle PAS l'IA (ex. : le garde-fou anti-boucle du nœud Reflect).
2. **Une isolation complète de l'environnement** : avant même que le code du projet soit chargé,
   le fichier de configuration des tests (`tests/conftest.py`) vide toutes les clés d'API et
   redirige les petites bases de données locales vers un dossier temporaire jetable. Subtilité
   technique qui rend ça possible : la fonction qui charge le fichier `.env` du projet n'écrase
   jamais une variable déjà présente — donc en préremplissant des valeurs vides, le vrai `.env`
   (avec les vraies clés) devient inoffensif pendant les tests.

### Ce qui est couvert (84 tests, ~2 secondes, zéro réseau)

- **Chaque nœud du graphe, isolément** : le repli du classificateur sur AUTRE, les garde-fous du
  superviseur (jamais deux fois le même agent, veille forcée si la FAQ est vide, stratège en
  dernier), la boucle Reflect (réécriture demandée, plafonnée à une), les trois branches de la
  décontextualisation de requête, l'ajout déterministe du lien Calendly (présent pour une démo,
  absent pour un devis), le routage SUPPORT/AUTRE, et la politique de réessai (retry sur 429,
  jamais sur une erreur de programmation).
- **Les mathématiques du RAG hybride** : la fusion RRF (une réponse trouvée par les deux voies de
  recherche doit gagner), la recherche par mots-clés (dont le cas « 99.9% de SLA » qui justifie
  son existence), la similarité cosinus.
- **Les quatre registres SQLite locaux** : idempotence de la file d'attente (un crash du poller ne
  retraite pas deux fois le même e-mail), récupération des entrées bloquées, entonnoir du tableau
  de bord, journal d'audit, suivi de relance.
- **Les contrats de dégradation gracieuse** : sans aucune clé configurée, chaque intégration doit
  rendre un résultat neutre sans jamais lever d'exception — y compris l'extraction de pièces
  jointes, testée avec de vrais fichiers PDF/Word/Excel synthétiques fabriqués en mémoire (dont un
  fichier volontairement corrompu, qui doit être ignoré en silence).
- **Cinq tests d'intégration du graphe entier** : un e-mail DEVIS traverse tout le pipeline et
  s'arrête bien à la pause de validation ; la reprise après « Valider » écrit bien (et une seule
  fois) au CRM ; la boucle de réécriture s'arrête bien après un tour ; un SPAM ne déclenche ni
  workers ni notification ; la veille se déclenche bien quand la FAQ est vide, avec la requête
  décontextualisée.

### Un vrai bug de test trouvé en route (et pourquoi c'est instructif)

Les deux premiers tests d'intégration ont échoué au premier passage : le faux LLM du superviseur
était **recréé à neuf à chaque appel**, donc sa « liste de réponses prévues » repartait de zéro à
chaque décision — le superviseur recevait toujours la première réponse de la liste, jamais la
deuxième. Corrigé en partageant une seule instance du faux pour toute la durée du test. Leçon
classique : les tests aussi ont des bugs, et un test qui échoue doit d'abord être suspecté
lui-même avant d'accuser le code.

### Ce que ça change pour la suite

Le point n°1 de la dette technique (§11.6) est soldé. Tout changement futur — les points restants
du §11.6, et a fortiori la phase commercialisation P3 — pourra être vérifié en 2 secondes au lieu
d'une session de tests manuels. `pytest` a été ajouté aux dépendances du projet (section dev de
`requirements.txt`).

---

## 2026-07-12 (suite 4) — Les vraies clés arrivent : Tavily et Slack passent du « repli gracieux » au réel

### Le problème de départ

Depuis leur création, plusieurs briques du projet n'avaient jamais tourné que sur leur « repli
gracieux » : le code vérifiait bien que *sans* clé d'API tout se dégradait proprement (pas de
plantage, résultat vide), mais le chemin nominal — celui qu'une vraie démo ou un vrai usage
emprunterait — n'avait jamais été exécuté, faute de clés. C'était le point n°2 de la dette
technique (§11.6). L'utilisateur a créé les deux références manquantes : une clé Tavily (recherche
web, gratuite) et un webhook Slack entrant (workspace « acam », canal #nouveau-canal).

### Ce qui a été vérifié en direct (et ce que ça a donné)

1. **Agent Enrichissement (Tavily + cache)** : premier appel sur un vrai domaine d'entreprise
   (doctolib.fr) → Tavily a renvoyé un vrai profil (secteur, taille, activité), immédiatement mis
   en cache dans l'onglet `Enrichissement_Cache` du Google Sheets. Deuxième appel sur le même
   domaine → réponse servie **depuis le cache**, sans appel web (c'est le principe de la « mémoire
   hybride » : on ne paie la recherche qu'une fois par entreprise).
2. **Notification Slack** : `python -m aca.integrations.notify` a livré un vrai message dans
   #nouveau-canal — la chaîne « une analyse attend votre validation » est opérationnelle de bout
   en bout.
3. **Routage SUPPORT** : un e-mail SUPPORT simulé passé à `routing_node` a déclenché une vraie
   alerte Slack via `SUPPORT_SLACK_WEBHOOK_URL` (pour l'instant le même canal que les leads — à
   séparer en canaux dédiés support/RH quand ils existeront).
4. **Agent Veille (le circuit complet)** : une question absente de la FAQ (« bonnes pratiques RGPD
   pour un CRM de PME ») → vraie recherche Tavily → reformulation en paire question/réponse par
   Groq → écriture dans l'onglet FAQ avec le statut « à valider » (invisible du RAG tant qu'un
   humain n'approuve pas — le mécanisme anti-pollution vérifié en conditions réelles). La ligne de
   test a ensuite été supprimée après vérification, pour ne rien laisser traîner.

### Ce qui reste du point n°2

Trois choses, qui demandent du réel plutôt que du code : de vraies adresses e-mail support/RH
(elles débloquent aussi le brouillon de transfert Gmail du routage), un test de `relance.py` sur un
vrai fil Gmail où un prospect a réellement répondu, et laisser le tableau de bord accumuler
quelques jours de données réelles.

---

## 2026-07-12 (suite 5) — Le point n°3 de la dette technique : les écritures locales resistent maintenant aux conflits d'accès concurrent

### Le problème de départ

Le programme tourne en réalité comme **deux processus séparés** qui peuvent s'exécuter en même
temps : `poller.py` (qui surveille la boîte Gmail en arrière-plan) et `ui.py` (l'interface
Streamlit qu'un commercial utilise). Chacun peut, au même instant, vouloir écrire dans les mêmes
petits fichiers de base de données locaux (SQLite) — par exemple la « file d'attente » des e-mails
en cours de traitement, ou le journal d'audit des validations. SQLite ne permet qu'une seule
écriture à la fois sur un même fichier ; si les deux processus tombent pile au même moment, l'un
des deux reçoit une erreur (« la base de données est verrouillée »).

Ce risque était déjà connu et documenté (§11.6 de la feuille de route), mais laissé de côté : à
faible volume (un cycle de sondage par minute, quelques clics dans l'interface), la probabilité que
ça arrive réellement est faible. Le nœud principal du graphe (`app.py`) a déjà un mécanisme de
réessai automatique (`RETRY_POLICY`) pour ce genre de panne passagère — mais il ne couvre QUE ce qui
se passe pendant l'exécution du graphe ; ces quatre petits registres locaux (file d'attente,
tableau de bord, audit, relances) s'exécutent en dehors du graphe et n'étaient donc pas protégés.

### Ce qui a été fait

Un petit module dédié, [sqlite_retry.py](../aca/storage/sqlite_retry.py) : un décorateur Python
(une fonction qui « enveloppe » une autre fonction pour lui ajouter un comportement, sans toucher à
son code) qui réessaie automatiquement jusqu'à 3 fois, avec un court délai croissant entre les
essais, **uniquement** si l'erreur rencontrée est bien un conflit de verrou SQLite — toute autre
erreur (un vrai bug de programmation, par exemple) est laissée passer immédiatement, sans être
réessayée, parce que la rejouer à l'identique ne la corrigerait pas.

Ce décorateur a ensuite été appliqué à **toutes** les fonctions publiques des quatre registres
locaux concernés : la file d'attente du poller, le journal du tableau de bord, le journal d'audit,
et le suivi des relances. Changement mécanique et à faible risque — aucune logique métier n'a été
modifiée, seule une couche de protection a été ajoutée autour de chaque fonction existante.

### Comment on sait que ça marche

Cinq nouveaux tests (dans la suite automatisée construite plus tôt dans la journée) : un scénario où
le verrou se libère après deux échecs (le code doit réussir au 3e essai), un scénario où le verrou
ne se libère jamais (le code doit abandonner proprement après 3 tentatives, pas tourner en boucle
indéfiniment), un scénario avec une erreur qui n'a rien à voir avec un verrou (le code ne doit faire
AUCUNE tentative supplémentaire), et deux vérifications que le décorateur est bien branché sur les
vraies fonctions du projet (pas seulement testé en isolation). Les 89 tests de la suite (84 + ces 5
nouveaux) passent en un peu plus de 5 secondes.

### Ce que ça change pour la suite

Les 3 premiers points de la dette technique (§11.6) sont maintenant soldés : la suite de tests, la
plupart des vérifications en conditions réelles (Tavily, Slack), et ce retry local. Il reste dans
cette section : quelques renforcements de robustesse de l'IA (peu urgents), la cadence de relance
multi-tours, et le rapport de stage lui-même (volontairement pour la fin).

---

## 2026-07-12 (suite 6) — L'extracteur ne « parse » plus du texte à la main : sortie structurée garantie

### Le problème de départ

Le nœud `extractor_node` (celui qui lit un e-mail et en tire l'entreprise, le contact, l'urgence et
le besoin) demandait jusqu'ici à l'IA de répondre avec du texte au format JSON, puis essayait de le
relire avec `json.loads()` — une fonction Python qui transforme du texte en objet, mais qui plante
si le texte n'est pas EXACTEMENT du JSON valide (une virgule en trop, un mot ajouté par l'IA avant
le JSON, etc.). En cas d'échec, le code se rabattait sur `{"raw": le texte brut}` — un filet de
sécurité qui évitait le plantage, mais qui perdait complètement la structure attendue (plus
d'entreprise, plus de contact, etc., juste du texte informe). Ce risque était réel : plus l'IA est
poussée à produire un format précis par la seule force du texte, plus elle peut s'en écarter.

### La solution : demander à l'IA elle-même de garantir le format

La bibliothèque utilisée (LangChain) propose une fonctionnalité appelée `with_structured_output()` :
au lieu de demander du texte et croiser les doigts, on donne au modèle un schéma exact (ici, un
« modèle Pydantic » — une classe Python qui décrit précisément quels champs existent, quels sont
optionnels, et quelles valeurs sont autorisées) et l'IA renvoie une réponse qui respecte forcément
ce schéma. Techniquement, Groq fait ça via de l'« appel d'outil » (tool-calling) : le modèle ne
génère pas du texte libre, il remplit les arguments d'un outil dont la forme est fixée à l'avance.

Le nouveau schéma, `ExtractedInfo`, définit quatre champs : `entreprise`, `contact`, `urgence`
(qui ne peut valoir QUE "haute", "moyenne", "basse" ou rien — plus de faute de frappe possible sur
ce champ) et `besoin_principal`. Le vieux `json.loads()` et le filet de sécurité `{"raw": ...}` ont
disparu — plus besoin, puisque le format est maintenant garanti par construction plutôt que vérifié
après coup.

### Un vrai plan B a quand même été gardé

Même avec cette garantie, un cas reste possible : une vraie panne réseau qui survit aux 3 tentatives
automatiques déjà en place (`RETRY_POLICY`), ou un cas limite où le modèle refuse de produire une
sortie conforme. Plutôt que de laisser ce cas planter tout le programme, `extractor_node` intercepte
l'erreur et renvoie un objet `ExtractedInfo` vide (tous les champs à `None`) — exactement le même
traitement qu'un e-mail vague : la suite du programme (`clarification_node`) posera une question à
l'humain, comme elle le fait déjà quand le besoin n'est pas clair. Aucune régression du principe
« ne jamais planter tout le pipeline pour une seule information manquante ».

### Comment on sait que ça marche

Trois nouveaux tests unitaires (extraction complète, champs manquants qui deviennent bien `None`,
sortie invalide qui déclenche le repli gracieux) — pour les rendre possibles sans appeler la
vraie IA, le faux LLM des tests (`FakeLLM`) a appris à simuler `with_structured_output()`. Et,
pour être sûr que ça marche aussi en vrai : trois appels réels contre l'API Groq — un e-mail clair
et complet (tous les champs bien remplis, l'urgence correctement classée « haute »), un e-mail
vague (les champs absents redescendent bien à `None`, pas de plantage). Suite complète repassée :
92 tests (89 + ces 3 nouveaux) en un peu plus de 2 secondes, plus un run complet des 5 e-mails de
démonstration contre la vraie API sans aucune régression.

---

## 2026-07-12 (suite 7) — Un score de confiance pour le classificateur, et un vrai bug de résilience trouvé en le testant

### Le problème de départ

Le point suivant de la liste des améliorations restantes (§10 de la feuille de route) : faire en
sorte que le classificateur (le premier maillon du pipeline, qui décide DEMANDE_DEMO / DEVIS /
SUPPORT / AUTRE / SPAM) dise aussi **à quel point il est sûr de lui**, et pas seulement son
verdict. L'idée, déjà notée dans la feuille de route : en dessous d'un certain seuil de confiance,
prévenir un humain plutôt que de faire confiance aveuglément au verdict de l'IA.

Ce point est particulièrement important pour trois catégories (SPAM, AUTRE, SUPPORT) qui, dans
l'architecture actuelle, **ne passent jamais devant un humain** — elles sont automatiquement
transférées à la bonne équipe (`routing_node`) sans jamais s'arrêter à l'écran de validation. Si le
classificateur se trompe sur l'une de ces trois catégories avec un vrai prospect commercial (par
exemple : un vrai client potentiel écrit un message ambigu, classé par erreur en SPAM), ce lead est
purement et simplement perdu, sans qu'aucun humain ne le revoie jamais. C'est donc exactement là que
la confiance du modèle compte le plus.

### Ce qui a été fait

Même technique que pour l'extracteur la veille : `classifier_node` utilise maintenant
`with_structured_output()` avec un nouveau schéma, `ClassificationResult` (catégorie + un nombre
entre 0 et 1 représentant la confiance). En dessous d'un seuil (fixé à 0.6), le nœud
`notification_node` — qui envoie déjà des alertes Slack/e-mail pour les leads en attente de
validation — envoie maintenant AUSSI une alerte pour les catégories normalement silencieuses
(SPAM/AUTRE/SUPPORT), avec un message clair : « classification à confiance faible, à vérifier
manuellement ». Pas de nouveau mécanisme construit de zéro : on réutilise le canal d'alerte déjà
en place et déjà vérifié en direct (Slack) la veille.

### Un vrai bug trouvé en testant pour de vrai (et pas seulement avec les tests automatisés)

En relançant la suite de démonstration (les 5 e-mails de test) contre la vraie API Groq pour
vérifier que tout fonctionnait, un des cas est ressorti avec « AUTRE (confiance 0%) » au lieu du
résultat attendu (DEMANDE_DEMO). Première réaction : est-ce un vrai bug, ou juste une donnée
périmée ? Le programme réutilise les mêmes identifiants de conversation à chaque lancement de la
suite de démonstration (un détail déjà documenté), donc les anciens résultats s'accumulaient
parfois dans les journaux affichés. Un second lancement a confirmé : cette fois, le même e-mail a
été classé correctement du premier coup. Le problème n'était donc pas dans la logique du
classificateur lui-même — il s'agissait d'un vrai raté ponctuel, quelque part dans le passé,
resté visible dans les journaux accumulés.

Mais ce raté ponctuel a révélé quelque chose d'important en creusant : le code venait tout juste
d'être écrit avec un filet de sécurité qui attrapait **toutes** les erreurs possibles autour de
l'appel à l'IA (panne réseau, limite de débit dépassée, sortie invalide...) et renvoyait
directement un résultat par défaut (« AUTRE », confiance 0). Le problème : le programme a déjà un
mécanisme de réessai automatique intégré au niveau du graphe entier (`RETRY_POLICY`, qui retente
jusqu'à 3 fois en cas de panne passagère — une limite de débit dépassée, par exemple). Mais avec ce
nouveau filet de sécurité posé À L'INTÉRIEUR du nœud, l'erreur ne remontait JAMAIS jusqu'à ce
mécanisme de réessai — elle était interceptée et « résolue » (avec un résultat dégradé) dès la
toute première tentative, empêchant tout réessai. Autrement dit : une panne passagère, qui aurait
probablement réussi à la 2e tentative, se traduisait immédiatement par un résultat dégradé sans
qu'aucun réessai n'ait jamais lieu.

**Pourquoi c'est un vrai bug et pas un détail** : le mécanisme de réessai existe précisément pour
absorber ce genre de panne temporaire (une limite de débit dépassée quelques secondes, un délai
réseau). Le neutraliser silencieusement pour cette seule pièce du code revenait à annuler un filet
de sécurité déjà en place, sans s'en rendre compte, en en ajoutant un autre au mauvais endroit.

**La correction** : retirer complètement le filet de sécurité local. Techniquement, ça se justifie
d'autant plus que la raison d'être de l'ancien filet (du texte mal formé que `json.loads()` n'arrive
pas à lire) a disparu avec la sortie structurée — la nouvelle technique garantit déjà un format
valide par construction. Ce qui reste comme risque résiduel (une vraie panne d'API) doit maintenant
remonter jusqu'au mécanisme de réessai du graphe, exactement comme pour tous les autres nœuds du
projet qui appellent l'IA (aucun d'entre eux n'a de filet de sécurité local équivalent). Si les 3
tentatives échouent toutes, le programme s'arrête proprement avec une erreur explicite — au lieu de
continuer silencieusement avec un résultat dégradé qui aurait pu passer inaperçu.

**Ce qu'il faut retenir** : ce bug n'aurait probablement jamais été repéré par la seule suite de
tests automatisés (qui simule des LLM factices, sans vraie notion de panne réseau réelle) — c'est
en relançant le programme contre la vraie API, pour de vrai, qu'un résultat inattendu a mis la puce
à l'oreille. Même leçon que le bug HubSpot de la veille : tester en conditions réelles reste
irremplaçable pour ce genre de problème de robustesse.

### Effet de bord positif : la précision du classificateur remesurée

En repassant le jeu de 50 e-mails étiquetés (`eval_classifier.py`) après ce changement, la précision
est passée de 96 % (48/50, mesure d'origine) à **100 % (50/50)** — les deux cas ambigus qui
posaient problème avant sont maintenant classés correctement. Piste probable (non prouvée avec
certitude, mais plausible) : contraindre le modèle à remplir un schéma précis via l'appel d'outil
force une réponse plus disciplinée qu'un simple mot en texte libre, ce qui aide sur les cas limites.

### Comment on sait que ça marche

Cinq nouveaux tests unitaires (label + confiance renvoyés correctement, avertissement de confiance
faible dans le journal de raisonnement quand le score est bas, propagation de l'erreur au lieu d'un
repli silencieux sur une sortie invalide, alerte déclenchée pour un SPAM/AUTRE/SUPPORT à confiance
faible, toujours pas d'alerte si la confiance reste haute). Suite complète repassée : 95 tests, plus
plusieurs vérifications en direct contre la vraie API Groq et le run complet des 5 e-mails de
démonstration.

---

## 2026-07-16 — Deux documents externes audités : scanner de risques, lacune de connaissance signalée, brouillon éditable, suivi des tokens

### Le point de départ

L'utilisateur a reçu deux PDF générés par IA ("ACAM v2 Complete Engineering Blueprint" et une
version condensée, "Unified Master Blueprint") proposant une refonte du projet en "moteur de
gouvernance d'appels d'offres" pour Teamwill (conseil bancaire) : score de probabilité de gagner un
appel d'offre par similarité avec d'anciennes propositions gagnantes, disponibilité du personnel
consultant, marge de rentabilité automatique, etc. Même démarche que pour le document externe déjà
audité en §12 de `ACAM_roadmap.md` : auditer chaque idée contre le code réel plutôt que de l'ajouter
telle quelle. Décision de cadrage de l'utilisateur avant l'audit : oublier le cadrage "Teamwill/appel
d'offres" — l'objectif réel du projet est une solution générique pour plusieurs entreprises clientes,
pas un pivot vers un seul secteur.

### Ce que l'audit a trouvé

**Déjà construit, souvent en mieux** : l'intake événementiel (`poller.py` existe déjà), le
pré-traitement déterministe avant tout raisonnement IA (`extractor_node` structuré existe déjà),
l'interface à deux vues technique/exécutive (le `st.status` en direct + l'écran de validation
existent déjà), l'optimisation du contexte (troncature globale déjà en place dans
`attachment_reader.py`).

**Deux erreurs factuelles repérées dans les PDF**, qui auraient dégradé le projet si copiées
telles quelles :
1. Le "Anti-Hallucination Gate" des PDF propose un seuil de similarité cosinus fixe à **0.85** pour
   bloquer toute génération IA en dessous. Mais la vraie mesure empirique déjà faite sur ce projet
   (session du 2026-07-11, ci-dessus dans ce journal) montre que de vraies questions pertinentes,
   reformulées, obtiennent un score entre **0.73 et 0.80** avec le modèle d'embedding réellement
   utilisé (Gemini). Un seuil à 0.85 aurait donc bloqué la quasi-totalité des bonnes réponses. Les
   seuils de similarité ne se recopient pas d'un projet à l'autre : ils dépendent du modèle
   d'embedding utilisé et se mesurent, ils ne se devinent pas.
2. Le schéma Supabase proposé dans les PDF utilise `vector(1536)` (la dimension des embeddings
   OpenAI `text-embedding-ada-002`, un modèle payant). Le projet utilise Gemini
   (`gemini-embedding-001`, gratuit, 3072 dimensions) — copier ce schéma tel quel aurait cassé la
   contrainte "0 € du projet" sans même que ça saute aux yeux dans un premier temps.

**Trois idées jugées bonnes, mais irréalisables telles quelles avec les données actuelles**, mises
de côté dans le backlog long terme (§12/§13 de `ACAM_roadmap.md`) : le score de probabilité de
gagner un appel d'offre (a besoin d'un historique de propositions gagnantes qui n'existe pas), la
disponibilité du personnel et la marge de rentabilité (ont besoin de données RH/finance qui
n'existent pas — le calcul lui-même est une simple formule, la vraie difficulté est la donnée), et
le "SME Matchmaker" complet avec fiches de compétences et cartes interactives Teams/Slack (a besoin
d'une base de compétences et d'un bot dédié qui n'existent pas).

**Quatre idées jugées bonnes ET réalisables avec ce qui existe déjà**, implémentées cette session :

### 1. Le scanner de risques contractuels (`aca/core/risk_scan.py`)

Inspiré du "Trapdoor Risk Engine" des PDF : avant toute rédaction, un nouveau nœud du graphe
(`risk_scan_node`, entre la mémoire CRM et l'extraction) cherche dans le corps de l'e-mail et les
pièces jointes des formulations qui engagent lourdement l'entreprise — responsabilité illimitée,
pénalités de retard, clause de non-concurrence, garantie bancaire, etc. Entièrement déterministe
(des expressions régulières, aucun appel à un modèle de langage) : rapide, gratuit, résultat
identique à chaque exécution sur le même texte. Volontairement placé sans `RetryPolicy` dans le
graphe (rien d'externe à réessayer). Si une clause est détectée, l'Agent Stratège est explicitement
prévenu dans son prompt (« ne t'engage sur aucune de ces clauses, renvoie vers l'équipe juridique »)
et `notification_node` l'ajoute en tête de l'alerte Slack/e-mail. Vérifié en direct : un e-mail de
test avec « responsabilité illimitée » et « pénalités de retard » a bien été détecté, et le
brouillon final rédigé par le Stratège a correctement refusé de s'engager sur ces clauses et
proposé une relecture juridique — exactement le comportement recherché.

### 2. La lacune de connaissance signalée explicitement (`knowledge_gap`)

Avant ce changement, quand ni la FAQ interne (agent Connaissance) ni une recherche web (agent
Veille) ne trouvaient de réponse, l'Agent Stratège rédigeait quand même une proposition — sans
aucun contexte factuel, en silence. Inspiré du "[UNANSWERED GAP]" des PDF (en version réalisable :
pas de blocage strict de la génération, la solution existante avec relecture humaine à la
validation reste supérieure — voir l'erreur du seuil 0.85 ci-dessus). Maintenant, `veille_node` pose
un drapeau `knowledge_gap` explicite dans ce cas ; le Stratège est prévenu de rester honnête (ne
jamais inventer un prix, un délai ou une fonctionnalité) et `notification_node` pousse la question
sans réponse dans l'alerte humaine — une version économique du "SME Matchmaker" des PDF : pas de
nouveau canal Teams/Slack dédié, juste la réutilisation du canal d'alerte déjà en place.

### 3. Le brouillon éditable avant validation (capture pour un futur corpus)

Le brouillon rédigé par le Stratège était jusqu'ici affiché en lecture seule dans l'interface — le
commercial ne pouvait que l'accepter tel quel ou taper sa propre réponse depuis zéro dans Gmail
après coup. Il est maintenant modifiable directement dans un champ de texte avant de cliquer
« Valider », et c'est cette version corrigée qui part vers le CRM/HubSpot et le brouillon Gmail (pas
l'originale). Chaque modification réelle (texte différent) est enregistrée en base locale — la
paire (brouillon original, brouillon corrigé). Version réalisable du "Continuous Training Loop" des
PDF : **ce n'est pas du réentraînement automatique** (la stack reste 100 % gratuite, Groq ne
s'entraîne pas sur ces données), seulement une matière première brute pour, plus tard, enrichir
manuellement les exemples "few-shot" des prompts ou le jeu d'évaluation `eval_dataset.json` avec de
vrais cas corrigés par un commercial.

### 4. Le suivi de consommation de tokens (« Quota Usage Tracker »)

Chaque exécution du graphe capture maintenant, via un mécanisme standard de LangChain
(`UsageMetadataCallbackHandler`), le nombre de tokens envoyés/reçus sur l'ensemble des appels aux
modèles Groq (classification, extraction, supervision, rédaction). C'est la première marche —
gratuite — d'un futur suivi de coût par client, déjà identifiée comme telle dans le §12 de
`ACAM_roadmap.md` avant même la lecture de ces PDF : tant que Groq reste gratuit, c'est purement
informatif ; le jour où la stack migre vers un fournisseur payant, la base de calcul existe déjà.

### Comment on sait que ça marche

23 nouveaux tests unitaires (détection de chaque motif de risque, insensibilité aux accents/
majuscules, texte propre sans faux positif, drapeau `knowledge_gap` posé seulement quand Connaissance
ET Veille échouent toutes les deux, injection correcte des deux avertissements dans le prompt du
Stratège, présence des deux signaux dans le message d'alerte, agrégation des tokens sur plusieurs
modèles, taux d'édition et statistiques de tokens sur des bases temporaires). Suite complète repassée
sans régression : **125 tests, ~5 secondes**. Vérifié en direct contre la vraie API Groq (et les vrais
Sheets/Tavily/Slack déjà configurés) avec un 6e e-mail de démonstration ajouté au run manuel
(`python -m aca.core.app`) contenant une clause de responsabilité illimitée et une question hors
FAQ : les deux clauses à risque ont été détectées, et le brouillon final rédigé par le Stratège a
correctement refusé tout engagement dessus.

---

## 2026-07-21 (suite) — Onglet Historique + graphe visuel, un bug Gmail silencieux trouvé, et la RLS Supabase vérifiée en direct (et corrigée)

### Le point de départ

Après la clôture du §14 (audit sécurité) et du §12 (fondation multi-tenant + scaffold
commercialisation) plus tôt le même jour, trois choses restaient : deux items du backlog §12 qui
sont de purs ajouts de code (onglet « Historique », graphe LangGraph visuel), et la vérification en
direct des points marqués « codé mais jamais exercé en réel » — en particulier la policy RLS sur
Supabase, jamais testée contre un vrai réseau faute d'accès dans les sessions précédentes.

### 1. Onglet « Historique » et graphe visuel (§12 items 2 et 5)

`audit_log.list_recent()` existait déjà mais n'était branché nulle part dans `ui.py`. Nouvel onglet
« Historique » : tableau des validations passées avec une recherche texte libre (expéditeur,
classification, validé par, ID). Le graphe visuel réutilise `st.graphviz_chart` (rendu côté
navigateur via viz.js, aucune dépendance système Graphviz nécessaire) pour dessiner la topologie
réelle du `StateGraph` : nœud actif en orange pendant une analyse en direct (mis à jour à chaque
étape du `stream()`), nœuds déjà passés en vert ; une version statique du même graphe apparaît aussi
dans les expanders « Raisonnement »/« Détail du routage » pour les analyses déjà terminées ou
chargées depuis la file d'attente du poller. L'item 8 (dashboard client Next.js dédié) reste
volontairement non construit, comme déjà décidé le même jour — décision produit, pas un manque de
code.

### 2. Un bug Gmail silencieux trouvé en préparant la vérification du routage

En préparant le test en direct du brouillon de transfert Gmail de `routing_node` (SUPPORT/AUTRE),
lecture du code a révélé `import gmail_reader` (import nu) au lieu de
`from aca.integrations import gmail_reader`, à deux endroits d'`app.py` (`action_node` et
`routing_node`). Cet import échoue toujours en réalité (`ModuleNotFoundError`, il n'existe aucun
`gmail_reader.py` à la racine du projet) — mais l'échec était systématiquement absorbé par un
`try/except` déjà en place, donc le graphe ne plantait jamais : il sautait juste silencieusement la
création du brouillon de réponse après « Valider » **et** le brouillon de transfert SUPPORT/AUTRE,
sans aucun symptôme visible. Aucun test ne couvrait ce chemin (les tests ne fixent jamais
`gmail_message_id`, donc la branche Gmail de ces deux nœuds n'est jamais exercée). Corrigé aux deux
endroits ; suite de tests repassée sans régression.

### 3. La vérification en direct du routage Gmail, débloquée et confirmée

Premier essai bloqué : `google.auth.exceptions.RefreshError: Token has been expired or revoked`. Le
token OAuth mis en cache (`credentials/gmail_token.json`) avait expiré et nécessitait un nouveau
consentement navigateur interactif — impossible à faire depuis une session non interactive. Après
que l'utilisateur a supprimé l'ancien token et relancé `python -m aca.integrations.gmail_reader`
(depuis le venv du projet, `.\venv\Scripts\python.exe`) pour renouveler le consentement, le test
en direct de `routing_node` contre un vrai message Gmail a été rejoué avec succès pour les deux
catégories : **SUPPORT** → alerte Slack envoyée + brouillon de transfert créé vers
`hajriismail02@gmail.com` ; **AUTRE** → alerte Slack envoyée + brouillon de transfert créé vers
`worldwc26@gmail.com` — les deux brouillons visibles dans Gmail, jamais auto-envoyés. Confirme que
le bug `import gmail_reader` corrigé au point 2 ci-dessus fonctionne réellement de bout en bout, pas
seulement en théorie.

### 4. La RLS Supabase, vérifiée en direct pour la première fois — et trouvée inopérante

Contrairement au reste du § 14.3 (marqué « non vérifié en direct, pas d'accès réseau »), le réseau
était disponible cette session. Vérification directe au niveau SQL (en contournant volontairement le
`WHERE org_id = ...` applicatif, qui aurait masqué une policy cassée) : un tenant bidon et même une
connexion sans variable de session positionnée voyaient les **74** lignes réelles de
`faq_embeddings` — alors qu'ils auraient dû en voir zéro. Cause confirmée par requête directe sur
`pg_roles` : le rôle `postgres` utilisé par `DATABASE_URL` a `rolbypassrls = true` (comportement par
défaut du rôle de connexion standard chez Supabase). Un rôle avec cet attribut ignore **toutes** les
policies RLS, quoi que fasse `FORCE ROW LEVEL SECURITY` — cette clause n'a d'effet que sur le
propriétaire de la table quand celui-ci n'a ni `SUPERUSER` ni `BYPASSRLS`. Le code SQL de la policy
elle-même était correct ; c'est le rôle de connexion qui rendait toute la protection cosmétique.

**Correction, avec l'accord explicite de l'utilisateur** ("Create a restricted DB role now") :

1. Création d'un rôle Postgres restreint `aca_app` (ni superuser, ni `BYPASSRLS`), avec les
   privilèges nécessaires sur les tables/séquences existantes de `public`.
2. Vérifié en direct : sous ce rôle, le tenant bidon et la session non positionnée voient bien
   **0** ligne, le tenant réel voit ses **74** lignes — la policy s'applique enfin réellement.
3. **Deuxième bug trouvé en testant le premier fix** : les tables de checkpoint LangGraph
   (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) avaient elles
   aussi la RLS activée automatiquement par Supabase (comportement par défaut sur toute nouvelle
   table du schéma `public`, pour éviter une exposition accidentelle via PostgREST) — mais sans
   aucune policy, ce qui équivaut à un refus total pour un rôle sans `BYPASSRLS`. Sans rapport avec
   le travail multi-tenant de cette session (ces tables n'ont pas de colonne `org_id`, leur isolation
   se fait par `thread_id`, entièrement gérée par les requêtes internes de LangGraph) : une policy
   permissive (`USING (true)`) suffit, exécutée une fois par l'utilisateur via l'éditeur SQL Supabase.
4. **Troisième bug, trouvé en rejouant la suite complète sous le nouveau rôle** :
   `vector_store._get_pool()` réexécute à chaque démarrage de process les commandes
   `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY` et `CREATE POLICY` — des opérations réservées
   au propriétaire de la table. Sous `postgres` (propriétaire), aucun souci ; sous `aca_app` (non
   propriétaire, par construction), ces commandes échouaient (`InsufficientPrivilege`). Un premier
   passage semblait pourtant réussir de bout en bout (`python -m aca.core.app`, 6 cas) — en creusant,
   ce succès était un faux positif : le `reasoning_log` affiché provenait d'un état déjà persisté par
   des runs précédents dans le même thread Postgres (mêmes `thread_id` figés dans le bloc `__main__`),
   donc `connaissance_node` n'avait pas été réellement réinvoqué. Corrigé en encadrant ce bloc de
   migration/policy d'un `try/except` : une erreur « must be owner » y est maintenant traitée comme
   normale (configuration déjà faite par un rôle admin), pas comme un échec. Une régression annexe
   trouvée au passage et corrigée immédiatement : le message d'information de ce nouveau `except`
   utilisait un caractère spécial qui faisait planter l'affichage sous la console Windows cp1252 —
   exactement le même type de bug déjà corrigé une fois dans `hubspot.py` (§ Known gaps) — remplacé
   par un texte ASCII pur.
5. Revérifié en direct après chaque correction : `sheets.search_knowledge_base_semantic()` sur une
   vraie question renvoie maintenant un vrai résultat pertinent, sans aucun repli silencieux vers la
   recherche par mots-clés. `DATABASE_URL` bascule sur le rôle `aca_app` ; le rôle `postgres`
   d'origine reste disponible pour l'administration (migrations futures) mais n'est plus utilisé à
   l'exécution.

### Ce que ça veut dire concrètement

La routine de vérification "on code une protection, elle est testée par des tests unitaires, mais
jamais rejouée contre le vrai service" a, cette fois, effectivement révélé un problème réel — le
scénario exact que "non vérifié en direct" était censé signaler comme risque. Trois bugs distincts
liés entre eux (rôle avec BYPASSRLS, RLS par défaut de Supabase sur les tables du checkpointer,
DDL réservée au propriétaire réexécutée à chaque démarrage) ont été trouvés et corrigés dans la
même session de vérification, chacun révélé par la correction du précédent — illustrant pourquoi
"non vérifié en direct" reste une mention honnête à garder tant que le test réel n'a pas eu lieu,
plutôt que de supposer qu'un code qui passe les tests unitaires se comporte forcément pareil contre
le vrai service.


## 2026-07-22 — Le dashboard client Next.js tourne, et la validation passe désormais dans Slack

### Le point de départ

Le dashboard Next.js avait été échafaudé la veille (§12 item 8) mais jamais lancé : `npm install`
échouait en boucle avec `ERR_SSL_CIPHER_OPERATION_FAILED` — un bug connu de Node/OpenSSL (TLS 1.3,
`ossl_gcm_stream_update`) sur cette machine Windows, pas un problème de réseau. Contourné en passant
à **pnpm** (pile réseau différente), puis un simple dépassement de délai sur les deux plus gros
fichiers (`next` + le binaire natif `swc-win32`), réglé avec un `.npmrc` (`fetch-timeout` allongé).
Le serveur de dev tourne, la page de login s'affiche avec son graphe d'agents animé en fond — vérifié
visuellement par l'utilisateur.

### La vraie question posée : le dashboard était-il le bon choix ?

L'utilisateur a demandé, en temps réel, si un dashboard Node.js était le choix pertinent pour de
« l'automatisation de workflow pour des entreprises ». Réponse honnête : le dashboard est une **tour
de contrôle**, pas l'automatisation elle-même (le moteur, c'est le graphe + le poller). Pour une
entreprise, la vraie commodité, c'est de **valider là où l'équipe travaille déjà** — Slack, Gmail —
pas d'ouvrir encore une application web. D'où trois décisions de cadrage (§12bis de la roadmap) :

1. **Le dashboard devient la colonne vertébrale UI** ; Streamlit est reclassé en outil
   d'administration/curation interne. La migration future de Streamlit, quand elle aura lieu, doit
   séparer par audience (vues client dans le dashboard, curation dans un futur groupe `(admin)`) —
   sinon on ne fait que refaire Streamlit avec de plus jolies polices.
2. **n8n reste la dernière chose à câbler**, repositionné en couche d'intégration optionnelle (« si
   une entreprise fait déjà tourner n8n, elle pilote ACA via l'API »), plus une 3e UI concurrente.
3. **La boucle d'approbation Slack** est ajoutée comme le vrai gain de commodité.

### Ce qui a été construit : Valider/Rejeter directement dans Slack

L'alerte Slack d'un nouveau lead porte maintenant deux boutons cliquables « ✅ Valider » / « ✕
Rejeter » (`notify.send_approval`, format Block Kit, le `thread_id` embarqué dans le bouton). Un clic
déclenche un POST signé de Slack vers un nouvel endpoint `POST /slack/interactions` (aca/api.py), qui
rejoue le graphe jusqu'à l'écriture CRM (Valider) ou retire le lead de la file (Rejeter) — exactement
la même logique que le bouton « Valider » de l'UI, mais **sans ouvrir aucune interface**. La
validation humaine, cœur non négociable du projet, est préservée : le clic EST la validation humaine.

Sécurité : cet endpoint est le seul où un clic déclenche une écriture CRM sans passer par la clé API
(Slack n'envoie pas notre en-tête). Il est protégé par la **signature HMAC de Slack**
(`aca/core/slack_verify.py`, pur/stdlib, `SLACK_SIGNING_SECRET`) et **échoue fermé** (rejet 503) si
ce secret est absent — à l'inverse du reste de l'API dont la garde est optionnelle. La logique de
validation/rejet a été factorisée (`_do_validate`/`_do_reject`) pour que REST et Slack partagent une
seule source de vérité.

Un vrai bug corrigé au passage : `notify.py` avait le même `import gmail_reader` nu que celui trouvé
la veille dans `app.py` — l'envoi e-mail de secours était donc silencieusement cassé. Corrigé.

### Comment on sait que ça marche

5 nouveaux tests (`test_api.py`, 22 tests au total) : vérification de signature pure (bonne/mauvaise
signature, secret absent, horodatage trop ancien), endpoint non configuré → 503, mauvaise signature
→ 401, et les flux complets Valider/Rejeter avec des requêtes **réellement signées en HMAC** comme
Slack le ferait. Suite complète : **175 tests, hors ligne, ~4 s**. Non vérifié contre une vraie app
Slack (nécessite une app Slack avec Interactivité + une URL publique/tunnel — étape manuelle de
l'utilisateur, comme l'OAuth Gmail l'a été) ni contre une vraie instance n8n.

---

## 2026-07-22 (suite) — Durcissement sécurité : injection de formule, limitation de débit, comparaisons à temps constant

### Le point de départ

En préparant la présentation, on s'est posé la question « est-ce que la partie sécurité est
complète pour un vrai usage en entreprise ? ». Un audit honnête a séparé les problèmes en **deux
paquets** : ceux qui se corrigent proprement tout de suite, et ceux qui sont de vraies décisions
d'architecture (une phase ultérieure). On a corrigé les premiers et **documenté honnêtement** les
seconds, sans faire semblant — un système d'authentification à moitié construit qui *a l'air* vrai
mais ne l'est pas serait un risque, pas une sécurité.

### Ce qui a été corrigé (dans le code, avec tests)

**1. Injection de formule dans Google Sheets (CSV/Sheets injection).** C'est le bug le plus concret.
Une donnée venant d'un e-mail entrant (nom, besoin, brouillon, réponse web de la veille) est du texte
**non fiable**. Si elle commence par `=`, `+`, `-`, `@`, Google Sheets l'interprète comme une
**formule** quand un humain ouvre la feuille — par exemple `=IMPORTXML(...)` peut exfiltrer discrètement
des données de la feuille, `=HYPERLINK(...)` piéger un clic. C'est une attaque classique contre
exactement notre flux (e-mail non fiable → tableur). Corrigé par une fonction pure `_escape_formula`
qui préfixe une apostrophe (Sheets traite alors la cellule comme du texte ; l'apostrophe reste
invisible à l'affichage, « -5 » reste affiché « -5 »), appliquée aux seuls champs d'origine non fiable
— jamais à la date ni à la catégorie, qui viennent de notre propre code. Couvert par 12 tests.

**2. Limitation de débit (rate limiting) sur l'API.** La clé API empêchait l'accès *non authentifié*,
mais pas l'*abus* par un client (rafales, brute-force du mot de passe, déni de service). Ajout d'une
fenêtre glissante en mémoire par client (clé API si présente, sinon IP source) sur toutes les routes
sauf `/metrics`, en middleware ASGI : au-delà de `ACA_RATE_LIMIT` requêtes par `ACA_RATE_WINDOW_SECONDS`
(défaut 60 s), la réponse est un HTTP 429 + en-tête `Retry-After`. Même contrat gracieux que le reste
du projet : variable absente = désactivé, comportement historique inchangé (usage local/n8n et tests).
Lu dynamiquement à chaque requête (jamais gelé à l'import — la leçon déjà apprise avec `DATABASE_URL`).
Couvert par 4 tests (désactivé par défaut, blocage au seuil, `/metrics` exempté, quotas séparés par
client).

**3. Comparaisons à temps constant sur les secrets.** Le gate mot de passe de Streamlit faisait
`pwd == required` et la vérification du cookie de session du dashboard faisait `token === attendu`. Un
`==`/`===` sur un secret **court-circuite au premier caractère différent** : le temps de réponse fuit
alors la longueur du préfixe correct, permettant de deviner le secret octet par octet (attaque par
timing). Corrigé côté Python avec `hmac.compare_digest`, et côté dashboard avec une comparaison hex à
temps constant en JS pur (`timingSafeEqualHex`, pour rester valide sur le runtime edge du middleware,
sans dépendre de `crypto.timingSafeEqual` de Node).

### Ce qui est resté volontairement non fait (phases ultérieures, dit clairement)

- **Identité par utilisateur (comptes/SSO).** Aujourd'hui : mots de passe partagés + champ « Validé
  par » en texte libre. La traçabilité est donc sur l'honneur — dit tel quel. Un vrai système
  d'identité est une fonctionnalité à part entière (territoire US-33), pas un correctif.
- **Isolation renforcée au niveau base pour les 5 stores SQLite locaux.** Le modèle réel est « un
  déploiement = un tenant » ; le filtrage `org_id` applicatif y suffit (contrairement à la table
  pgvector partagée, elle protégée par la RLS Postgres, déjà vérifiée en direct le 2026-07-21).
- **Backend de rate limiting multi-process (Redis).** La fenêtre en mémoire est exacte à l'échelle
  mono-process d'un prototype ; un déploiement multi-worker demanderait un store partagé.

### Comment on sait que ça marche

**191 tests, hors ligne, ~4,5 s** (contre 175 avant : +16 tests sécurité). Une nouvelle annexe
« Security posture » a aussi été ajoutée au document de présentation (`docs/ACA_presentation_source.md`,
Annexe C) : un tableau des contrôles en place, la mitigation architecturale de l'injection de prompt
(le gate humain + le scanner de risques déterministe font que le pire cas est un brouillon trompeur
qu'un humain rejette, jamais une action autonome nuisible), et la liste honnête des trois éléments
reportés. Le message clé : la sécurité ici n'est pas « verrouillé comme une banque » mais « chaque
surface que touche une entrée non fiable est soit échappée, soit signée, soit limitée en débit, soit
derrière un humain — et les trois choses non faites sont reportées exprès, avec leur état intermédiaire
dit franchement ».

---

## 2026-07-26 — Phase sécurité (§15) : des comptes nominatifs, un journal infalsifiable, et cinq problèmes trouvés en vérifiant

### Le problème de départ

La roadmap gardait une dernière phase volontairement repoussée en fin de projet : le §15,
« checklist production-ready ». Ses statuts avaient été écrits en auto-audit **sans re-vérifier le
code**, avec une consigne explicite : « à re-vérifier au moment de l'implémentation ». C'est
exactement ce qui a été fait — et c'est cette re-vérification qui s'est révélée la partie la plus
utile du travail.

Le trou central restait le même, connu et assumé jusqu'ici : **personne n'était identifié**. Les
trois surfaces (Streamlit, dashboard, API) partageaient chacune un mot de passe unique, et le champ
« Validé par » du journal d'audit était une zone de texte libre que la personne remplissait
elle-même. La traçabilité était donc sur l'honneur : n'importe qui pouvait signer une validation au
nom d'un collègue, et rien ne distinguait un commercial d'un administrateur.

### Ce qui a été fait

**1. De vrais comptes, avec des rôles.** Nouveau registre `aca/storage/user_store.py` : identifiant,
mot de passe **haché** (PBKDF2-HMAC-SHA256, un sel différent par personne, jamais de mot de passe en
clair nulle part), et un rôle — `operator` ou `admin`. Un opérateur traite les leads (valider,
rejeter) ; seul un admin touche aux réglages, à la base de connaissances et aux comptes. Deux détails
qui comptent : le coût de calcul est stocké *à l'intérieur* du hachage, donc on pourra le durcir plus
tard sans invalider les mots de passe existants ; et un identifiant inconnu déclenche quand même un
calcul factice, sinon le temps de réponse révélerait quels comptes existent. Un départ se traite par
**désactivation**, jamais par suppression — le journal d'audit cite l'identifiant, l'effacer rendrait
des validations passées non attribuables.

Conséquence directe : « Validé par » vient désormais de la session authentifiée. La traçabilité
cesse d'être déclarative.

**2. Les sessions expirent enfin.** Avant, une fois connecté, on l'était tant que l'onglet du
navigateur vivait — des jours, sur un poste non verrouillé. Désormais deux bornes
(`aca/core/session.py`) : une durée de vie absolue (8 h) **et** un délai d'inactivité (30 min), la
plus stricte l'emportant. Nuance volontaire : l'activité repousse le compteur d'inactivité mais
**jamais** la durée absolue — sinon une session volée mais maintenue active ne mourrait jamais.

**3. Un journal d'audit qu'on ne peut plus retoucher discrètement.** Chaque ligne intègre désormais
l'empreinte de la précédente. Modifier ou supprimer une vieille ligne casse toutes les empreintes
suivantes, et `verify_chain()` **désigne** la première ligne fautive. Vérifié en direct : après avoir
changé un `validated_by` directement dans la base, le contrôle a bien pointé la ligne 2. Deux
contrôles séparés sont nécessaires, et c'est le point subtil : l'empreinte de la ligne *et* le
chaînage vers la précédente — sans le second, supprimer une ligne au milieu passerait inaperçu,
puisque chaque ligne restante resterait cohérente prise isolément. Avec une clé
(`ACA_AUDIT_HMAC_KEY`), forger la chaîne devient impossible sans cette clé, qui vit hors de la base.
Dit franchement dans le code et la doc : c'est **tamper-evident, pas tamper-proof**.

**4. Le droit à l'oubli, pour de vrai.** Le projet savait purger *par ancienneté* — la partie facile,
parce qu'automatisable. Une personne qui écrit « supprimez mes données » a pourtant un droit
immédiat. Jusqu'ici, y répondre imposait de retrouver à la main des lignes dans un Google Sheet, des
threads dans un fichier de checkpoints et deux registres SQLite : en pratique, ça ne se faisait pas.
Désormais : `python -m aca.core.retention --oublier adresse@exemple.fr` efface tout et renvoie le
décompte par emplacement, pour pouvoir répondre précisément à la personne. Effet de bord
souhaitable : elle n'est plus relancée automatiquement. Le journal d'audit est **volontairement
conservé** (intérêt légitime, et le supprimer romprait la chaîne du point 3, ce qui ressemblerait à
une falsification) — décision documentée, pas un oubli.

**5. Les instructions cachées dans les e-mails sont signalées.** Nouveau `aca/core/prompt_guard.py` :
détection déterministe (FR/EN, sans IA) des tentatives de manipulation du modèle — « ignore les
instructions précédentes », « tu es désormais… », faux messages système, demandes de révéler le
prompt. **On signale, on ne bloque pas** : la vraie protection reste la validation humaine. Mais
c'était précisément le problème — sans signalement, une consigne glissée page 14 d'un cahier des
charges ressortait dans la proposition comme une phrase plausible de plus. Le relecteur voyait un
brouillon, pas une attaque : il ne pouvait juger que ce qu'il savait. Ces alertes sont volontairement
rangées dans une liste **séparée** des clauses contractuelles à risque : une clause appelle une
relecture juridique, une injection appelle la méfiance envers le brouillon lui-même.

**6. « Absent = ouvert » ne passe plus en production.** Tout le projet repose sur la dégradation
gracieuse : une protection non configurée est simplement ignorée. C'est le bon défaut en local et
exactement le mauvais sur un serveur public. Nouveau `aca/core/prod_check.py` : avec
`ACA_ENV=production`, l'application **refuse de démarrer** s'il manque la clé API, une garde d'accès
à l'UI, la limite de débit ou le jeton `/metrics`. En développement, rien ne change.

**7. Ce que l'API expose est réduit.** Bornes strictes sur tous les champs entrants (un corps
d'e-mail de 200 ko est refusé *avant* d'atteindre le LLM, donc avant d'être facturé) ; liste blanche
des clés de réglages ; Swagger (`/docs`) coupé en production ; `/metrics` derrière son propre jeton,
distinct de la clé qui écrit dans le CRM — Prometheus peut scraper sans jamais détenir de quoi
écrire un lead.

**8. TLS et coffre à secrets : la procédure, à défaut du serveur.** Rien n'est hébergé, donc ni HTTPS
ni coffre ne pouvaient être « codés ». Nouveau `docs/DEPLOYMENT_HARDENING.md` : configurations Caddy
et Nginx complètes, en-têtes de sécurité, règles de gestion des secrets et tableau de rotation par
secret.

### Les cinq choses trouvées en vérifiant (le plus intéressant)

Un item « à vérifier » vaut souvent plus qu'un item « à construire » :

1. **`ui.py` affichait le texte brut des exceptions à l'écran**, à quatre endroits. Or le message
   d'erreur d'une API contient régulièrement l'URL appelée, des en-têtes, parfois un fragment de clé.
2. **17 vulnérabilités connues dans les dépendances**, dont 11 dans deux paquets **transitifs** que
   le projet n'importe même pas (`gitpython` arrive via Streamlit, `pyasn1` via `google-auth`).
   « `requirements.txt` est épinglé » donnait une fausse assurance : les dépendances *indirectes* n'y
   figuraient pas. Corrigé et re-scanné : plus aucune.
3. **Le cookie du dashboard n'expirait jamais côté serveur.** Le jeton signé était une valeur
   constante ; la date d'expiration du cookie n'est appliquée que par le navigateur et ne survit pas
   à une simple recopie du cookie. L'expiration est désormais signée *dans* le jeton.
4. **`POST /settings` acceptait n'importe quelle clé** — le magasin de configuration est générique
   par conception, mais rien ne filtrait à la frontière réseau.
5. **Une subtilité Postgres qui aurait rendu le rapport RLS trompeur.** `FORCE ROW LEVEL SECURITY` ne
   s'applique qu'au *propriétaire* de la table. Un script naïf aurait signalé les quatre tables
   LangGraph comme problématiques à chaque exécution — et un rapport bruyant finit par ne plus être
   lu.

### Ce qui est resté volontairement non fait (dit clairement)

- **HTTPS n'est pas appliqué**, seulement documenté : il n'y a aujourd'hui aucun serveur ni domaine.
- **Pas de coffre à secrets** (Vault/Doppler) : décision d'hébergement. Aucun changement de code ne
  sera nécessaire le jour venu, puisque tous les modules lisent leur configuration dynamiquement.
- **Les registres SQLite locaux restent cloisonnés au niveau applicatif**, pas au niveau base. Le
  modèle réel reste « un déploiement = un tenant ».
- **Pas de double authentification, pas de SSO.** Comptes locaux uniquement.
- **Le logging structuré, les disjoncteurs et tout le §15.4** (CI, tests de charge, E2E navigateur)
  ne font pas partie de cette passe : c'est de la qualité et de l'exploitation, pas de la sécurité.

### Comment on sait que ça marche

**261 tests, hors ligne, ~12 s** (contre 192 avant : +69), dont 43 dédiés dans
`tests/test_security.py`. Ces tests vérifient les scénarios d'attaque eux-mêmes plutôt que le
« chemin heureux » : une ligne d'audit modifiée est détectée, une ligne **supprimée** aussi, un corps
d'e-mail démesuré est rejeté **sans que le LLM soit appelé**, un rôle inconnu n'obtient aucun droit,
l'activité ne prolonge pas la durée de vie absolue d'une session, et dix e-mails commerciaux
parfaitement normaux ne déclenchent **aucune** fausse alerte d'injection.

Vérifications en direct, pas seulement en test : le balayage RLS sur le vrai Supabase (5 tables,
0 sans politique, connexion via le rôle restreint `aca_app`), la détection de falsification du
journal d'audit, le scan de dépendances, et les commandes en ligne de commande (création de comptes,
contrôle de configuration de production, vérification du journal).

---

## 2026-07-26 (suite) — Le produit était déjà automatique, il ne le montrait pas ; et n8n ne pouvait pas s'y brancher

### Le problème de départ

Le point de départ était une simple question : *« puis-je utiliser pgvector en même temps que Google
Sheets, et quel flux est le meilleur pour mon workflow n8n ? »* Plutôt que d'y répondre de mémoire,
je suis allé relire le code. La réponse à la question posée était courte (oui, les deux coexistent
déjà : Google Sheets reste l'endroit où les commerciaux écrivent, pgvector n'est que le moteur de
recherche). Mais l'inspection a mis au jour deux problèmes autrement plus importants, longtemps
confondus en un seul.

**Le premier : ACA passait pour un outil « à boutons ».** Il ne l'est pas — `poller.py` lit la boîte
Gmail et exécute le graphe en continu, interface fermée, depuis des semaines. Le malentendu venait de
la présentation, pas du produit. Et il coûtait cher : quelqu'un qui croit devoir cliquer pour lancer
chaque traitement n'achète pas un assistant autonome.

**Le second, à l'inverse, était bien réel :** il manquait une brique d'automatisation, et pas la plus
anodine. `relance.py` (les relances commerciales) et `retention.py` (la purge RGPD) étaient écrits,
testés, et chacun documenté « à planifier périodiquement, par exemple une fois par jour ». Sauf que
**rien ne les planifiait**. Aucun mécanisme de planification n'existait dans le projet, et la machine
de développement tourne sous Windows, qui n'a même pas l'équivalent d'un `cron`. En clair : la purge
des données personnelles ne partait que si un humain pensait à taper la commande à la main —
c'est-à-dire jamais. Une conformité RGPD qui repose sur la mémoire d'un opérateur n'est pas une
conformité.

**Et un troisième, découvert dans la foulée :** l'API existait bien, mais n8n n'aurait pas pu s'en
servir correctement. Deux blocages rédhibitoires, décrits plus bas.

### Ce qui a été fait

**1. Un planificateur, sans nouvelle dépendance.** `aca/core/scheduler.py` cadence désormais quatre
travaux : relances, purge RGPD, maintenance de la file d'attente, remontée de consommation. Il ne
réécrit aucune logique métier — il ne fait qu'appeler, à heure dite, des fonctions qui existaient
déjà. Pas d'APScheduler ni de Celery : une boucle qui regarde toutes les minutes si un travail est
échu suffit très largement pour quatre tâches dont la plus fréquente tourne une fois par heure, et
cela reste cohérent avec la contrainte « 0 € et le moins de dépendances possible ».

Un petit registre (`schedule_store.py`) mémorise quand chaque travail est passé pour la dernière
fois. Sans lui, **tous les travaux se relanceraient à chaque redémarrage** — donc une purge et une
rafale de brouillons de relance Gmail à chaque redéploiement.

Le détail qui ne se voit qu'en poussant le raisonnement jusqu'au bout : un travail jamais exécuté est
considéré comme « en retard » (sinon, sur une installation neuve, la purge n'aurait jamais lieu). Au
tout premier démarrage, les quatre partiraient donc d'un coup — dont les relances, qui écrivent de
vrais brouillons dans Gmail. C'est défendable, mais surprenant le jour de la mise en service. D'où
une commande `--prime` qui décale proprement tout d'un intervalle sans rien exécuter.

**2. Une commande pour tout lancer.** `python scripts/run_solo.py` démarre les quatre processus
ensemble. Avant, il fallait quatre terminaux et quatre commandes à retenir — ce qui suffit, en
pratique, à ce que le poller et le planificateur ne soient jamais lancés, et donc à ce que le produit
*paraisse* manuel. Exactement le malentendu de départ.

**3. Deux paliers de déploiement clairement nommés.** « Solo » (sans n8n, automatisé de bout en
bout) et « Enterprise » (avec n8n). On passe de l'un à l'autre en changeant un mot. La phrase qui
résume la position, et qui manquait : **n8n n'apporte pas l'automatisation, il apporte
l'orchestration avec vos autres outils.**

**4. Ce qui bloquait vraiment n8n.** Deux choses, dont aucune n'était évidente avant de regarder :

- **Les pièces jointes ne passaient pas par l'API.** Le champ était littéralement écrit « liste
  vide » dans le code. L'analyse conjointe e-mail + document — le premier des trois piliers
  d'innovation du projet — était donc inatteignable depuis l'interface HTTP, alors que le graphe
  savait parfaitement la faire depuis l'interface Streamlit.
- **ACA n'émettait aucun événement.** n8n aurait dû interroger l'API en boucle pour savoir s'il
  s'était passé quelque chose : c'est-à-dire réécrire le poller *à l'intérieur* de n8n, exactement ce
  que ce port est censé remplacer. ACA **pousse** désormais cinq événements (analyse en attente,
  question posée, e-mail routé, lead validé, lead rejeté), signés, et dont l'envoi ne peut jamais
  faire échouer une analyse.

S'y ajoutent une sonde de disponibilité (`/health`), une protection contre les réessais (un même
e-mail renvoyé deux fois ne relance plus une analyse complète et ne renotifie plus l'équipe), un mode
asynchrone, une image Docker avec les deux paliers, et un workflow n8n prêt à importer.

**5. Un mode démonstration, sans aucune clé.** Jusqu'ici, essayer ce projet demandait **cinq comptes
externes**. On pouvait lire le code, pas l'exécuter — la différence entre « dépôt intéressant » et
« je viens de le faire tourner ». `ACA_DEMO_MODE=1` remplace les modèles de langage par une doublure
déterministe : le graphe reste le vrai, seuls les appels facturables sont simulés. Point important :
en mode démonstration, **toute écriture réelle échoue bruyamment** au lieu d'être silencieusement
ignorée. C'est le seul endroit du projet qui ne « dégrade pas gracieusement », et c'est voulu —
écrire un faux lead dans le CRM d'un prospect pendant une démonstration serait un incident.

**6. La première impression.** README réécrit (il avait environ cinq versions de retard : il
décrivait encore un graphe sans superviseur), un fichier `.env.example` documentant les 54 variables
une par une (il n'en existait aucun : il fallait lire 700 lignes de documentation technique pour
savoir quoi configurer), six fichiers parasites supprimés à la racine, une intégration continue — il
n'y avait aucun dossier `.github/` — et un one-pager de présentation autonome.

### Ce que la vérification a trouvé, une fois encore

Comme lors de la phase sécurité, la partie la plus utile n'est pas ce que le plan prévoyait, mais ce
que la relecture du code a révélé :

1. **Le schéma du graphe affiché dans l'interface était faux.** La liste des étapes y était recopiée
   à la main, et il lui manquait une flèche : celle qui relie le superviseur à la suite du pipeline.
   L'utilisateur voyait donc un superviseur sans issue. Personne ne pouvait s'en apercevoir, puisque
   rien ne comparait ce schéma au vrai graphe. Le schéma est désormais **déduit du graphe lui-même**,
   donc juste par construction.
2. **Le webhook envoyait un journal de raisonnement en retard d'une ligne** par rapport à ce que la
   même analyse affichait via l'API. Corrigé à la source, pas dans le test.
3. **Un événement était déclaré, documenté… et jamais envoyé.** Trouvé en rédigeant cette entrée de
   journal : `analysis.clarification` figurait dans le code et dans la documentation n8n, mais aucun
   appelant ne l'émettait. Or c'est la **seule situation où le graphe s'arrête sans rien signaler** —
   un workflow automatique lancé sur un e-mail ambigu serait resté muet indéfiniment, à attendre un
   signal qui n'arrive qu'après la réponse humaine. Émis désormais au bon endroit : à l'extérieur du
   nœud concerné, car une pause de clarification fait **rejouer ce nœud depuis son début** à la
   reprise — l'envoi serait donc parti deux fois pour une seule question.
4. Un piège dans le fichier `.gitignore` : la règle ajoutée pour exclure les variantes de `.env`
   aurait aussi exclu le modèle `.env.example` qu'on venait d'écrire.

**Et une erreur de ma part, notée telle quelle :** mon plan affirmait que l'interface affichait deux
fois son titre. Vérification faite, ce sont deux écrans mutuellement exclusifs — l'écran de connexion
et l'application. Il n'y avait pas de doublon ; je n'ai rien touché.

### Ce qui reste volontairement non fait

- **Le workflow n8n n'a jamais été importé dans un vrai n8n**, et les webhooks n'ont jamais été reçus
  par une vraie instance : aucune n'existe pour ce projet.
- **L'image Docker n'a jamais été construite** — Docker n'est pas installé sur la machine de
  développement. Seule la configuration a été validée (le bon nombre de services par palier).
- **L'intégration continue ne s'exécutera qu'au premier envoi vers un dépôt distant.**
- **Le one-pager n'est hébergé nulle part**, pour la même raison que le HTTPS de la phase précédente :
  il n'y a ni serveur ni domaine.

Aucun de ces points n'est du code manquant. À chaque fois, c'est un compte, une instance ou un
hébergement qui n'existe pas encore.

### Comment on sait que ça marche

**352 tests, hors ligne, ~13 s** (contre 261 à la fin de la phase sécurité : +91), dont 18 pour le
planificateur, 30 pour le mode démonstration, et le reste pour les nouvelles capacités de l'API et
les webhooks. Les tests portent sur ce qui pourrait réellement mal tourner : un travail périodique en
échec est enregistré comme tel plutôt que réessayé toutes les minutes ; le graphe complet tourne
**sans aucune clé d'API** ; un e-mail renvoyé deux fois ne déclenche qu'une seule analyse ; une pièce
jointe surdimensionnée est refusée **avant** que le modèle ne soit appelé ; la sonde `/health` ne
laisse fuir aucune valeur de secret ; et un test compare la charge utile du webhook à la réponse de
l'API pour interdire toute dérive future entre les deux.

Vérifié aussi hors tests : le graphe complet sur les six e-mails de démonstration sans aucune clé, la
configuration Docker résolue par palier, et les exports (schéma OpenAPI, topologie du graphe)
régénérés à l'identique.

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
  *(Dépassé depuis — voir l'entrée du 2026-07-28 : le workflow a été importé dans une instance n8n
  Cloud réelle, ce qui a révélé quatre défauts que la relecture seule n'avait pas vus.)*
- **L'image Docker n'a jamais été construite** — Docker n'est pas installé sur la machine de
  développement. Seule la configuration a été validée (le bon nombre de services par palier).
  *(Dépassé depuis — voir l'entrée du 2026-07-28 : Docker 29.3.1 installé, image construite
  (1,28 Go), conteneur `api` démarré et sonde de santé au vert.)*
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

---

## 2026-07-28 — Brancher le workflow pour de vrai : quatre défauts qu'aucune relecture n'avait vus

### Le problème de départ

Le workflow n8n était écrit, commité, documenté — et n'avait jamais été exécuté. L'entrée précédente
le disait honnêtement. Le brancher à une vraie instance a suffi à faire tomber quatre défauts, dont
trois sont invisibles à la lecture parce qu'ils ne se manifestent qu'à l'exécution.

### Ce qui a été fait

**Les quatre défauts du workflow.**

1. **`item.index` n'existe pas.** Le nœud de préparation lisait les pièces jointes avec
   `getBinaryDataBuffer(item.index ?? 0, clé)`. Un item n8n expose `.json`, `.binary` et
   `.pairedItem`, mais **pas** `.index` : l'expression valait donc toujours `0`. Avec deux e-mails
   dans un même cycle, le lead n°2 recevait les documents du lead n°1 — une proposition rédigée à
   partir du mauvais dossier, sans le moindre message d'erreur. Remplacé par un compteur de boucle
   explicite.
2. **L'alerte n'était envoyée à personne.** Le nœud « Mettre en forme l'alerte » n'avait aucune
   connexion sortante : le workflow mettait l'alerte en forme, puis la jetait. Ajout du nœud
   « Alerter l'équipe (Slack) », qui poste sur le **même** webhook entrant que `notify.py` — donc
   aucun identifiant n8n à créer.
3. **La sonde de santé ne pouvait jamais s'exécuter.** Elle n'avait aucune entrée. Sa propre note
   disait « branche d'erreur » — mais rien ne l'avait branchée. Reliée à la **sortie d'erreur** de
   l'appel `POST /threads` (`onError: continueErrorOutput`) : si `/health` répond, ACA est debout et
   c'est cet e-mail-là qui a échoué ; sinon, c'est ACA qui est tombé.
4. **Un nœud parasite** (`evaluationTrigger` vide, déconnecté) accaparait le bouton « Execute » dans
   l'instance Cloud. Supprimé.

**Trois erreurs de configuration, toutes du même genre : silencieuses.**

- `ACA_WEBHOOK_URL` valait `http://n8n:5678/webhook/aca-events` : l'hôte `n8n` ne se résout que dans
  le réseau Docker, alors que l'instance visée tournait ailleurs. Une URL fausse se manifeste par un
  404 silencieux, jamais par une erreur au démarrage.
  **Correction d'une correction — l'erreur la plus instructive de la journée.** J'avais d'abord
  « corrigé » le *chemin* en `/webhook/<webhookId>/<path>`, en me fiant au champ « Production URL »
  remonté par l'API MCP de n8n, et j'avais réécrit `n8n/README.md` en conséquence. C'était faux : le
  test en conditions réelles contre l'instance active a tranché sans ambiguïté —
  `/webhook/aca-events` répond `200 {"message":"Workflow was started"}`, la forme avec `webhookId`
  répond `404 … is not registered`. Le `webhookId` ne sert qu'à *engendrer* un chemin lorsque `path`
  est vide. Le README d'origine avait donc raison, et j'ai passé plusieurs heures à faire confiance
  à un champ d'API plutôt qu'à une requête. La leçon vaut d'être écrite : **une documentation ne se
  corrige pas sur la foi d'un résumé d'outil, mais sur celle d'un appel qui répond.**
- **n8n Cloud interdit `$env`.** Toute expression `$env.…` y lève « access to env vars denied », et
  le réglage `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` posé par `docker-compose.yml` est un réglage
  *auto-hébergé*, sans effet sur Cloud. Constaté empiriquement plutôt que supposé, via un nœud de
  diagnostic temporaire : `$env` lève, `$vars` répond. La copie Cloud utilise désormais `$vars` ; le
  fichier du dépôt garde `$env`, correct pour l'auto-hébergement, et `n8n/README.md` documente les
  deux formes côte à côte.
- **`run_solo.py --without` n'était pas répétable.** `--without scheduler --without poller` ne
  retenait que la dernière occurrence : le planificateur a démarré alors qu'on le croyait exclu, et
  a lancé « relance », qui écrit de vrais brouillons Gmail à de vrais prospects. Aucun dégât (le
  seuil de 4 jours n'était pas atteint), mais par chance, pas par maîtrise. Corrigé
  (`action="append"`), les noms exclus sont désormais validés eux aussi — une faute de frappe
  refuse de démarrer au lieu d'ignorer silencieusement l'exclusion — et un `--dry-run` permet de
  vérifier ce qui serait lancé sans rien lancer.

### Ce que la vérification a trouvé, une fois encore

- **Le contrat le plus important du webhook s'est vérifié en conditions réelles.** L'émission de
  `analysis.paused` a échoué (n8n n'écoutait pas) et **`POST /threads` a tout de même renvoyé 200**.
  C'est `webhook.emit()` qui tient sa promesse de ne jamais lever. S'il avait levé, `RETRY_POLICY`
  aurait rejoué le nœud jusqu'à trois fois — et dans `action_node`, cela signifie une **double
  écriture CRM**, exactement l'incident HubSpot du 2026-07-12. Ce contrat n'avait jusqu'ici que des
  tests unitaires ; il a maintenant été éprouvé contre un vrai point d'entrée injoignable.
- **`console.py` protège les `print()` contre l'encodage, pas contre un tuyau fermé.** Un `POST
  /threads` a renvoyé 500 alors que le même appel passait juste après. Cause probable : le processus
  API était orphelin, sa sortie standard fermée — et un `print()` vers un tuyau mort lève, à
  l'intérieur de nœuds enveloppés par `RETRY_POLICY`. Même classe d'incident que le bug HubSpot,
  mais par rupture de tuyau plutôt que par encodage. Noté, non corrigé.
- **n8n Cloud ne peut pas joindre une API locale, par conception.** Ce n'est pas un réglage : Cloud
  refuse les adresses de bouclage et privées au titre de la protection anti-SSRF. Aucune
  modification du workflow n'y change quoi que ce soit ; il faut une URL publique (tunnel ou
  hébergement) ou une instance n8n locale.

### Ce qui reste volontairement non fait

- **La moitié « réaction » a bel et bien tourné de bout en bout** (exécution n°8, `mode: webhook`,
  2026-07-28) : une analyse réelle — vrai e-mail, vrais appels de modèles, enrichissement du domaine
  de l'expéditeur — s'est arrêtée à la pause de validation, a émis `analysis.paused`, et n8n Cloud
  l'a reçue, filtrée et mise en forme. L'alerte produite portait le bon `thread_id`
  (`e2e-final-…`), l'entreprise déduite (« Teamwill Consulting ») et les deux drapeaux de risque
  contractuel détectés par `risk_scan_node`. Seul l'envoi Slack final manque : il attend la création
  de la variable `SLACK_WEBHOOK_URL` côté Cloud. Le nœud a d'ailleurs échoué **sans faire tomber le
  workflow**, exactement comme prévu par son `onError: continueRegularOutput`.
- **La moitié « ingestion » n'a pas été éprouvée** : le déclencheur Gmail exige un consentement
  OAuth via navigateur, et n8n Cloud ne peut pas joindre l'API locale. La correction n°1 — la plus
  lourde de conséquences — **reste donc vérifiée par lecture seule**, faute de binaire dans les
  données épinglées.
- **Le déclencheur Gmail n'a pas de justificatif en local** : son OAuth exige un navigateur.
- **`SLACK_SIGNING_SECRET` n'est pas défini**, donc les boutons « Valider »/« Rejeter » répondent
  503 — échec fermé, conforme à la conception, mais la boucle d'approbation Slack reste non testée.
- **Aucun hébergement de l'API**, qui est pourtant la seule vraie solution pour n8n Cloud.

---

## 2026-07-29 — La moitié « validation » passe en production, et la suite de tests avoue qu'elle n'était pas hors ligne

### Le point de départ

Relire le workflow n8n **contre la définition réelle des nœuds** plutôt que de mémoire. Les cinq
nœuds de validation écrits la veille n'avaient jamais quitté le dépôt : l'instance Cloud tournait
toujours sur la version à 8 nœuds. La relecture, puis la mise en production, ont fait apparaître
trois défauts — dont un qui n'a rien à voir avec n8n et qui est de loin le plus gênant.

### Ce qui a été corrigé

1. **Le lien d'approbation pointait vers la mauvaise URL.** L'e-mail transportait
   `$execution.resumeUrl` alors que le nœud *Wait* est en mode `form`. Ce sont deux chemins
   distincts (`/webhook-waiting/` contre `/form-waiting/`), et la définition du nœud ne rattache
   `resumeFormUrl` qu'à `resume: ["form"]`. Le lien envoyé était donc mort, ou reprenait
   l'exécution **sans réponses de formulaire** — auquel cas `Décision` est vide et l'aiguillage
   sûr retombe sur « Rejeter ». Autrement dit : toute la boucle d'approbation était cassée, et le
   défaut par sécurité aurait masqué la panne en rejetant silencieusement les prospects.
2. **`formSubmittedText` était placé sous `options`, où ce n'est pas une clé du schéma** — donc
   ignoré sans le moindre avertissement. Le vrai chemin est
   `options.respondWithOptions.values.formSubmittedText`.
3. **Le nœud « Mettre en forme l'alerte » de Cloud n'exposait ni `draft` ni `besoin`**, que le
   formulaire d'approbation lit. Poussé tel quel, le formulaire aurait affiché un brouillon vide.

Aucun des trois ne se voit à la lecture du JSON : il faut comparer aux définitions des nœuds.

### Ce que la vérification a trouvé, et qui ne concernait pas n8n

**La suite de tests n'était pas hors ligne.** En cherchant *pourquoi* le journal n8n comptait une
rafale de dix exécutions ce matin-là, l'en-tête `user-agent: python-requests` et l'horodatage ont
désigné le coupable : ma propre exécution de `pytest`. `tests/conftest.py` vide consciencieusement
tous les canaux sortants — Slack, e-mail de notification, support, RH, HubSpot, Stripe — mais
**`ACA_WEBHOOK_URL` avait été oublié** lors de l'ajout du §16.1.2. Chaque exécution locale de la
suite expédiait donc de vrais événements `analysis.paused`, portant de faux prospects
(« jean@entreprise.fr »), vers l'instance n8n de production.

C'est doublement instructif. D'abord parce que « la suite est entièrement hors ligne » est affirmé
noir sur blanc dans `CLAUDE.md`, dans la feuille de route **et dans le commentaire d'en-tête du
fichier de CI** : une garantie répétée quatre fois, fausse depuis l'ajout des webhooks, et que
personne ne pouvait démentir puisque rien ne la mesurait. Ensuite parce que la gravité venait
d'augmenter à l'instant même : tant que le workflow s'arrêtait à l'alerte, ces faux événements
étaient inoffensifs ; depuis qu'il porte la moitié « validation », la même exécution de `pytest`
enverrait une rafale d'e-mails d'approbation et laisserait autant d'exécutions en attente
**pour toujours**.

Correction : deux lignes dans `_ENV_OVERRIDES`. La vérification vaut mieux que la correction —
après coup, la suite passe toujours (352 tests) et crée **zéro** nouvelle exécution n8n, là où elle
en créait dix. Elle tourne au passage en 14 s au lieu de 34 s : les vingt secondes manquantes
étaient des allers-retours réseau réels vers n8n Cloud, ce qui est en soi la preuve du diagnostic.

### État de l'instance Cloud

13 nœuds, publiés et actifs. L'identifiant Gmail existe et est rattaché ; le déclencheur Gmail
**reste volontairement désactivé** — l'activer lancerait une scrutation toutes les minutes vers une
API que Cloud ne peut pas joindre, produisant un échec par minute.

### Ce qui reste non fait

- **Les variables Cloud** (`ACA_API_URL`, `ACA_API_KEY`, `NOTIFY_EMAIL`, `SLACK_WEBHOOK_URL`) — à
  créer dans Settings → Variables ; aucun outil de l'API MCP ne permet de les poser.
- **Le tunnel ou l'hébergement public**, sans quoi n8n Cloud ne joindra jamais l'API locale
  (protection anti-SSRF, cf. entrée du 2026-07-28).
- **Le fichier du dépôt garde `$env`**, correct en auto-hébergement ; Cloud utilise `$vars`. Six
  nœuds sont désormais concernés, contre quatre avant la moitié « validation » — le tableau de
  correspondance de `n8n/README.md` a été complété.

---

## 2026-07-30 — Marque blanche, animations, et un journal qui sait qui a fait quoi

Demande en trois volets : embellir l'interface avec beaucoup d'animations, rendre **tout**
paramétrable (logo, couleurs) pour l'aligner sur le cahier des charges du client, et créer un profil
d'audit du rôle `operator` pour qu'un administrateur voie qui a fait quel changement, depuis quel
poste, quand.

### Ce que l'inventaire a révélé avant d'écrire une ligne

Le troisième volet supposait qu'il existait déjà quelque chose à compléter. Il n'existait presque
rien. `audit_log.py` — le « journal d'audit » du projet depuis §12 — ne consigne **qu'un seul type
d'événement** : la validation d'un lead. Ni les connexions, ni les échecs de connexion, ni les
rejets, ni les changements de réglages, ni la curation de la base de connaissances, ni la création
d'un compte administrateur ne laissaient de trace. Le rôle `operator` existait depuis §15.1.6 ; rien
ne permettait de dire ce qu'une personne portant ce rôle avait fait de sa semaine.

Le cas le plus parlant est le verrou anti-force brute. Depuis §14, `auth_lockout.py` bloque un bot
qui essaie des mots de passe. Il le faisait **en silence** : aucune trace de la tentative, donc
aucune possibilité de détecter l'attaque, de la dater ou de la rattacher à une adresse. Un dispositif
de sécurité qui ne laisse pas de trace protège l'instant et n'apprend rien.

Quatrième constat, celui du volet « paramétrable » : l'apparence n'était pas un paramètre mais un
fichier du dépôt. Livrer ACA à une entreprise imposant sa charte imposait de **modifier le produit**
pour ce client.

### Le défaut le plus instructif : une entrée d'audit perdue en silence

La première exécution de bout en bout dans l'interface (mode démonstration, graphe réel) s'est bien
déroulée : classification, superviseur, quatre agents, auto-critique, pause de validation. Puis le
journal affichait **zéro ligne**. L'action « analyse lancée » avait disparu.

Cause : `ui.py` lit `st.context.ip_address`, dont rien ne garantit que ce soit une chaîne de
caractères. La valeur est descendue jusqu'à SQLite, qui a refusé de la lier
(`Error binding parameter 13`), et `log()` a attrapé l'exception — au titre de son contrat
« ne lève jamais », écrit précisément pour qu'un journal indisponible ne fasse jamais échouer une
validation CRM légitime. Le mécanisme de protection a donc parfaitement fonctionné, et c'est
exactement lui qui a masqué la perte : la ligne d'audit s'est volatilisée en laissant une seule ligne
dans la console du serveur.

**Un journal de sécurité qu'on croit complet et qui ne l'est pas est plus dangereux qu'un journal
absent** : on s'appuie dessus. Corrigé aux deux bouts — conversion explicite dans `ui.py`,
normalisation systématique dans le magasin (défense au bon endroit : les appelants sont nombreux et
le resteront) — et verrouillé par un test de régression. Ce défaut ne se voyait *que* dans une
exécution réelle : ni la relecture, ni les tests unitaires écrits jusque-là ne pouvaient le montrer.

En corrigeant, un second problème de qualité de données est apparu : la colonne « Adresse IP »
acceptait n'importe quel texte. Derrière un reverse proxy, cette valeur vient d'un en-tête
`X-Forwarded-For`, donc du client, donc falsifiable. Elle est désormais validée (`ipaddress`, et
seule la première entrée d'une liste « client, proxy1, proxy2 » est retenue) et le user-agent est
plafonné : ne jamais laisser un tiers décider de la taille de ce qu'on stocke.

### Deux autres défauts trouvés par les tests

`log()` **pouvait lever**, en violation de son contrat : la sérialisation de `details` avait lieu hors
du `try`. Et `merge_config_toml` n'était pas idempotent — réappliquer la marque laissait une section
`[theme]` vide en tête de fichier. Sur ce dernier point, la première version du test était elle-même
fausse : elle comptait les occurrences du texte « [theme] », qui apparaît aussi dans le commentaire
d'en-tête généré. Comparer le fichier entier est le bon contrôle, et c'est cette comparaison qui a
révélé le vrai défaut derrière la fausse alerte.

### Choix assumés

**De la CSS, contre la doctrine par défaut du projet.** La skill Streamlit prescrit « jamais de CSS,
tout dans `config.toml` » — juste, pour un thème figé. Un thème qui change à l'exécution, par tenant,
ne peut pas être un fichier lu au démarrage du serveur. D'où deux couches assumées : la CSS vivante
(effet immédiat, porte les animations) et le thème natif `config.toml` écrit sur action explicite,
seul à atteindre l'intérieur des composants React de Streamlit. Le prix est énoncé dans le code : les
sélecteurs `data-testid` ne sont pas contractuels, donc une montée de version peut rendre la page
moins jolie — jamais cassée, aucune règle ne conditionne une fonctionnalité.

**« Depuis quel poste », sans surpromesse.** Une application web ne peut pas identifier une machine.
Sont stockés : l'IP vue par le serveur, le user-agent (déclaratif, donc falsifiable), une empreinte
qui regroupe les actions d'un même poste sans déposer de cookie de traçage, et le nom de la machine
serveur — qui, en déploiement « Solo », **est** le poste du commercial. Vérifié en pratique : la
colonne « Serveur » affiche bien `ISMAIL`. Prétendre à une identification matérielle dans un journal
d'audit serait pire que de ne rien écrire.

**L'accessibilité avertit, elle ne refuse pas.** Un client peut demander « notre jaune d'entreprise »
en couleur principale. Le panneau calcule les contrastes WCAG et le signale — puis applique la
couleur. C'est sa charte ; un produit qui interdit la charte graphique de son client se fait
remplacer. En revanche, le texte des boutons est choisi automatiquement (noir ou blanc selon la
luminance) : livrer des boutons illisibles serait notre défaut, pas son mauvais goût.

### Vérifications réellement effectuées

- Suite complète : **451 tests** (352 avant), hors ligne, ~19 s.
- Rendu headless de l'application entière (`AppTest`) : aucune exception, 5 onglets, 11 sélecteurs de
  couleur, panneau Apparence et vérification d'intégrité présents.
- Analyse de bout en bout puis rejet via l'interface : les deux actions apparaissent au journal avec
  poste, serveur et issue ; chaîne d'empreintes intacte ; résumé par personne correct.
- Changement de charte appliqué depuis le magasin de configuration, puis relu dans le HTML rendu :
  couleur principale, accent, arrondi, police Poppins, densité compacte, nom et pied de page du
  client, animations désactivées, `prefers-reduced-motion` présent. Tout concorde.

### Ce qui reste non fait

Le journal n'a jamais tourné sur un vrai déploiement multi-poste (aucun n'existe : l'IP observée est
celle de la boucle locale). Aucune alerte en temps réel sur incident : les échecs de connexion sont
consignés et visibles, mais rien ne prévient personne. SSO/SCIM et domaine personnalisé — attentes
classiques d'un achat grand compte — ne sont pas faits et sont documentés comme recommandations dans
`docs/AMELIORATIONS_SUGGEREES.md`.

---

## 2026-07-30 (suite) — Toutes les suggestions du document, un fichier de 1700 lignes découpé, et un second facteur

Demande : mettre en œuvre les suggestions de `docs/AMELIORATIONS_SUGGEREES.md` (le document produit
la veille), à l'exception de l'hébergement et de l'alerte Slack — les deux seules à exiger une
infrastructure qui n'existe pas ici — avec une passe de design délibérée pour que l'interface reste
lisible à mesure que les nouvelles surfaces s'ajoutent.

### Ce que l'inventaire a révélé avant d'écrire une ligne

Deux choses existaient déjà, à moitié. La première : les constantes `SOURCE_POLLER`/`SOURCE_CLI`
vivaient dans `activity_log.py` depuis la veille, et personne ne les utilisait — le document de
suggestions le relève lui-même, en les qualifiant de « raccordement trivial ». La seconde, plus
subtile : `activity_log.purge_older_than()` savait déjà purger les événements sensibles à une
échéance différente du bruit courant (`sensitive_days`), mais `retention.py` — le seul appelant réel
en production — ne lui passait jamais ce paramètre. La fonctionnalité existait ; en pratique, elle
ne se déclenchait jamais. Trouvé en écrivant les tests de cette passe, pas en relisant le code —
exactement le genre d'écart entre « construit » et « branché » que ce projet trouve à chaque audit
depuis le §16.0.

Troisième constat, plus lourd : aucune des nouvelles surfaces demandées — la frise d'un lead, le
bouton d'export PDF, l'inscription TOTP — n'avait d'endroit sensé où vivre dans l'ancien `ui.py`, un
fichier unique qui avait grossi jusqu'à ~1700 lignes en portant à la fois la logique de session, le
formulaire d'e-mail, le tableau de bord, l'historique, le journal d'activité et les réglages. La
découpe en pages `st.navigation` — routeur fin (`ui.py`) + aides partagées (`aca/ui/shared.py`) +
cinq pages (`app_pages/*.py`) — n'était donc pas une fin en soi : c'était la condition pour livrer
le reste sans aggraver le fichier.

### La restructuration : la partie la plus risquée, vérifiée à chaque étape

Extraire le gate d'authentification en premier, avec une exécution `AppTest` (mot de passe correct
puis incorrect) avant de toucher au reste. Puis les aides d'interaction avec le graphe
(`advance_graph`, `sync_result`), vérifiées avec une analyse de démonstration complète bout en bout.
Puis, seulement à ce stade, la découpe effective des cinq onglets en pages — en commençant par les
plus simples (tableau de bord, historique) pour valider le schéma avant d'attaquer le plus gros et
le plus risqué, la page « Nouvel e-mail » (analyse, clarification, validation, rejet). Chaque étape
revérifiée avant la suivante, jamais l'inverse.

Deux défauts trouvés précisément par cette prudence :

1. **`st.navigation` n'exécute que la page sélectionnée.** Sous l'ancien `st.tabs()`, le corps de
   *tous* les onglets tournait à chaque interaction — un résultat chargé depuis la barre latérale
   apparaissait donc quel que soit l'onglet affiché. Une fois la découpe faite, cliquer « Ouvrir »
   sur une analyse en file ne montrait plus rien tant qu'on ne cliquait pas soi-même sur « Nouvel
   e-mail ». Corrigé par un `st.switch_page()` explicite après chaque chargement depuis la barre
   latérale.

2. **L'avertissement de session testait le mauvais instant.** Le premier jet vérifiait le temps
   restant *après* avoir appelé `session.touch()` — qui repousse systématiquement le compteur
   d'inactivité à sa valeur maximale. L'avertissement n'aurait donc jamais pu se déclencher en usage
   réel. Un test écrit avant de supposer l'ordre correct l'a révélé immédiatement.

Une découverte propre à l'outillage, sans rapport avec le code du projet, mais qui vaut d'être
consignée : `streamlit.testing.v1` classe **tout** `st.expander(..., icon=...)` sous son type interne
`Status` plutôt que `Expander` — un détail d'implémentation de cette version de Streamlit. `at.expander`
n'en trouve donc aucun dans toute l'application (pas seulement les nouveaux composants) ; `at.status`
est l'accessoire à utiliser. Sans ce constat, plusieurs vérifications auraient semblé échouer alors
que l'interface elle-même fonctionnait parfaitement.

### Vérifications réellement effectuées

- Suite complète : **550 tests** (451 avant), hors ligne, ~19 s — les deux derniers ajoutés après
  coup, quand le secret d'inscription TOTP est devenu un vrai QR code scannable (`segno`, pur
  Python, sans dépendance) plutôt qu'un texte `otpauth://` brut, sur demande de suivi.
- Balayage headless sur les trois rôles (`admin` avec inscription TOTP, `operator`, `viewer`) et
  chacune des pages qu'ils peuvent atteindre : aucune exception.
- Analyse de démonstration complète (classification → extraction → agents → proposition) puis
  validation et rejet, sur la page restructurée : comportement identique à l'ancien fichier unique.
- Boucle TOTP de bout en bout : mot de passe correct → inscription forcée (secret généré, code
  erroné rejeté, code correct accepté et persisté) → deuxième connexion passant directement par la
  vérification, sans réinscription.
- `code_at()` vérifié contre les vecteurs de test officiels du RFC 4226 (Appendix D), pas seulement
  contre les attentes du projet lui-même.
- Export PDF : construction, réouverture avec `fitz`, extraction du texte — nom de l'entreprise,
  contact, contenu du brouillon et mention de relecture humaine tous réellement présents dans le
  document rendu.

### Ce qui reste non fait, par choix explicite

Hébergement et alerte Slack : exclus par la demande initiale, tous deux exigeant une infrastructure
réelle absente ici. Du §5 du document de suggestions : raccourcis clavier (nécessite
`st.components.v2`), traitement par lot (le document appelle lui-même à la prudence — diluerait la
garantie de validation humaine), vue mobile (aucun appareil disponible pour la tester), recherche
globale inter-pages — reportés sans avoir été commencés, faute de rapport effort/valeur suffisant
dans le temps de cette passe.

## 2026-07-31 — Trois demandes de suivi sur la découpe fraîchement livrée : QR code, style de la barre de navigation, bascule FR/EN

Pas un nouvel item de la feuille de route : trois retours consécutifs sur ce qui venait d'être livré
la veille (§18). D'abord une question de compréhension sur l'écran d'inscription TOTP, puis « fais-en
un QR code » (le secret s'affichait jusque-là en texte brut `otpauth://`), puis, sur une capture
d'écran de la barre de navigation et du bandeau de sécurité admin : « rends cette barre plus visible
et animée », suivi de « centre les onglets et ajoute une option pour switcher entre français et
anglais ».

### QR code d'inscription

`_totp_qr_png(uri)` (`aca/ui/shared.py`) encode l'URI `otpauth://` en PNG via `segno` — bibliothèque
pure Python, zéro dépendance transitive, retenue justement pour éviter de dépendre de Pillow comme
dépendance transitive non déclarée (le même écueil déjà repéré une fois pour `cryptography` via
`google-auth`). Le rendu de l'image reste dans la couche UI, volontairement hors de `totp.py`, dont
tout l'intérêt est de rester pur/stdlib pour le calcul cryptographique lui-même.

### Barre de navigation : centrée, visible, animée — puis un vrai bug trouvé après coup

Les vrais noms de classe internes de Streamlit (`stTopNavLinkContainer`, `stTopNavLink`) ne sont
documentés nulle part — trouvés en grep-ant directement le bundle JS compilé plutôt qu'en devinant.
Une fois ces sélecteurs en main : carte à dégradé autour de la rangée de navigation, centrée
(`margin-inline: auto; width: max-content` sur le même sélecteur), effet de survol par lien, et une
entrée animée à l'ouverture (contrôlée par le niveau d'animation existant de `branding.py`, donc
toujours neutralisée si le client a désactivé les animations ou si le système a demandé moins de
mouvement). Le bandeau de sécurité admin reçoit en plus un halo pulsé lent — seulement aux niveaux
animés, pour ne pas laisser un signal d'alerte qui bouge tout seul chez un client sans animations.
Les anciennes règles `.stTabs [data-baseweb=…]` étaient mortes (confirmé par grep : plus aucun appel
à `st.tabs()` depuis la découpe en pages) — supprimées plutôt que laissées à côté des nouvelles.

**Ce premier essai ne fonctionnait en fait pas.** L'utilisateur a signalé, capture d'écran à
l'appui, que la barre restait ni visible ni centrée. Plutôt que de retoucher le CSS à l'aveugle une
deuxième fois, un vrai navigateur a été monté pour l'occasion (Playwright + Chromium, installés
dans le venv le temps de la vérification) contre une instance Streamlit isolée (compte `operator`
jetable, bases de données temporaires) et le DOM **réellement rendu** a été inspecté par script
(`getComputedStyle`, `getBoundingClientRect`, chaîne des ancêtres). Résultat : `*:has(> [data-
testid="stTopNavLinkContainer"])` cible bien le bon testid, mais capture son parent *direct* — un
`div` privé à Streamlit qui n'enveloppe qu'**un seul** lien à la fois, jamais la rangée qui les
aligne tous. Le fond, la bordure et le `margin-inline: auto` de centrage tombaient donc chacun sur
quatre petites boîtes séparées, quasi invisibles côte à côte — exactement ce que l'utilisateur
avait sous les yeux. Le vrai conteneur flexbox, confirmé par l'inspection, vient de `rc-overflow`,
la bibliothèque tierce de liste que Streamlit utilise pour cette rangée — `.rc-overflow`/
`.rc-overflow-item` sont des classes stables (posées par la bibliothèque elle-même, pas hachées par
Streamlit à chaque version), une cible plus fiable que deviner la profondeur exacte de `:has()`.
Trouvaille annexe pendant la même inspection : l'animation « échelonnée » des liens ne l'était
jamais réellement — `:nth-child(N)` appliqué à un élément qui est toujours l'unique enfant de son
parent vaut toujours `:nth-child(1)`, donc les quatre liens recevaient tous le même délai
d'entrée. Les deux corrigés ensemble en ciblant `.rc-overflow`/`.rc-overflow-item`, revérifiés en
direct (captures d'écran avant/après, `justify-content`/fond/ombre lus sur l'élément réel) : la
barre est maintenant une carte unique, visible, et les quatre onglets sont centrés dedans.

### Bascule de langue FR/EN

Avant d'écrire quoi que ce soit, question posée à l'utilisateur : traduire tout le projet (des
centaines de chaînes sur ~15 fichiers) ou seulement le chrome principal ? Réponse : le chrome
principal seulement — navigation, en-têtes/légendes de page, boutons/étiquettes premiers, messages
clés. Les écrans de curation admin (base de connaissances, comptes, jetons de marque), le détail du
journal d'activité, l'export PDF et les logs console restent en français, par choix assumé et non
par oubli.

Un dictionnaire fait main (`aca/core/i18n.py`), pas une bibliothèque i18n (Babel, gettext) : la
surface traduite tient dans quelques dizaines d'entrées statiques, sans pluriel ni date localisée —
une dépendance entière pour ça aurait reproduit exactement le travers que ce projet évite ailleurs
(`totp.py`, `slack_verify.py`, stdlib par principe). `translate(key, lang, **kwargs)` ne lève jamais :
une clé inconnue renvoie la clé elle-même (un bug visible à l'écran, jamais une page qui plante), une
langue inconnue replie sur le français. La lecture/écriture de la langue courante
(`st.session_state["_lang"]`, portée à la session, pas persistée par utilisateur ni par tenant) vit
dans `aca/ui/shared.py` (`current_language()`/`t()`/`language_switcher()`), pas dans `i18n.py`
lui-même — même posture que `session.py`/`branding.py` : un module pur, testable hors ligne, sans
import Streamlit.

Un vrai bug trouvé par la vérification, avant qu'il ne devienne visible en usage réel : le sélecteur
de langue avait d'abord été placé dans la barre latérale de `ui.py` *après* la porte
`check_auth()`/`st.stop()` — qui arrête le script avant d'atteindre le reste si la personne n'est pas
connectée. Résultat : le sélecteur ne s'affichait jamais sur l'écran de connexion, exactement l'écran
où une personne anglophone en aurait le plus besoin. Corrigé en déplaçant le bloc avant
`prod_check.enforce()`.

### Vérifications

Suite complète : 550 → **561 tests** (`tests/test_i18n.py`, 11 tests — `translate()` ne lève jamais
sur une clé/langue inconnue, chaque clé déclarée porte réellement les deux langues non vides,
formatage `{placeholder}` correct dans les deux langues, et un échantillon de clés qui diffèrent
réellement entre français et anglais, pour qu'une future clé copiée-collée à l'identique dans les
deux langues ne neutralise pas silencieusement le sélecteur). Balayage `AppTest` en direct : connexion
d'un compte admin fraîchement créé (inscription TOTP forcée complétée avec le vrai code calculé à
partir du secret généré), bascule vers l'anglais en cours de session, puis passage sur les cinq
pages (`1_inbox.py` à `5_settings.py`) — aucune exception, et la légende propre à chaque page
(`dashboard.caption`, `history.caption`, `activity.caption`) vérifiée comme réellement traduite dans
la langue active, pas seulement l'absence d'erreur.

## 2026-08-03 — Un chevauchement mesuré, une identité visuelle assumée, et trois manques que l'usage révélait

Quatre demandes en une : corriger la barre d'en-tête qui recouvre le contenu, rendre l'interface
moins générique, permettre de **programmer une réponse pour une heure choisie** et d'**écrire un
rappel**, et enfin clarifier et rendre paramétrable la réception automatique des e-mails.

### Le chevauchement : mesuré, pas deviné

La barre d'en-tête de Streamlit est en `position: absolute`, `z-index: 999990`, fond transparent,
et mesure **52,5 px**. La feuille de style de marque avait remplacé la marge haute de la page par
une valeur fixe issue de la densité — **30,8 px**. L'écart de 22 px est exactement ce que montrait
la capture d'écran : le haut de chaque page passait *sous* la navigation. Constat obtenu en
inspectant le DOM réellement rendu avec un vrai navigateur, pas à l'œil.

Deux corrections complémentaires : une marge exprimée en `max()`, pour que la densité « aérée »
puisse ajouter de l'air mais jamais descendre sous le seuil de dégagement ; et un vrai fond opaque
sur la barre, sans quoi le contenu défilerait en transparence derrière elle — et sans quoi, aussi,
la barre continuait de se lire comme une pastille flottant sur rien.

### L'identité visuelle : le diagnostic n'était pas la couleur

Le défaut n'était pas la teinte, c'était que **le dégradé était appliqué à tout** : en-tête,
boutons, cartes d'indicateurs, navigation. Un effet appliqué partout ne hiérarchise rien — et c'est
précisément ce qui donne à une interface l'air d'être sortie d'un gabarit, avec un rayon d'angle
unique et un seul rôle typographique.

Désormais un seul dégradé subsiste dans toute la feuille de style : celui du bloc de décision. La
palette par défaut quitte le bleu Fluent de Microsoft et son violet (`#0078D4` / `#8764B8`) pour un
pétrole profond et un ambre brûlé sur papier froid, avec un parti pris qui dit quelque chose de
vrai sur le produit — **le travail de la machine est froid, la décision humaine est chaude** —
l'ambre étant réservé au seul moment où quelqu'un doit trancher. Trois rôles typographiques, chacun
justifié : une serif de titrage pour la voix du **document** (ce produit fabrique des propositions
commerciales qu'un client finit par signer), la sans du client pour la voix de l'**outil**, et un
monospace non paramétrable pour les valeurs **machine**, parce que des chiffres tabulaires dans une
file d'attente doivent s'aligner. Tout reste surchargeable par client : ce ne sont que des défauts.

L'élément signature est le **cartouche « Bon pour accord »** : qui engage sa responsabilité, quand,
et — énoncé *avant* le geste — ce que la validation va réellement écrire. Deux boutons posés sous
une zone de texte ne portaient aucune de ces trois informations.

### Programmer un envoi sans trahir la promesse du produit

C'est la question qui demandait le plus de prudence : ACA revendique de ne jamais laisser partir un
message qu'aucun humain n'a lu. Un envoi différé la contredit-il ? Non, à condition d'être précis :
la personne lit le brouillon, le corrige, puis décide elle-même qu'il partira à telle heure.
L'autorisation humaine existe bien, elle est simplement antérieure à l'exécution — comme l'envoi
différé de n'importe quelle messagerie.

Trois choix découlent de ce raisonnement. Ce qui est programmé est le **brouillon Gmail déjà créé**,
donc exactement le texte relu, et non une regénération ultérieure du modèle. S'il est modifié ou
supprimé dans Gmail avant l'échéance, c'est la volonté de l'humain qui l'emporte. Et l'annulation
est protégée par une clause SQL (`status = 'pending'`) plutôt que par une vérification en Python :
sans elle, un planificateur lent et une annulation humaine simultanée pourraient se croiser, et un
e-mail partirait après qu'une personne a explicitement dit non.

Les rappels sont volontairement **indépendants** de la validation : « je m'en occupe mardi » est une
intention qui existe qu'on valide, qu'on rejette ou qu'on laisse en attente. Les lier au bouton
Valider les aurait rendus inaccessibles exactement dans le cas où ils servent le plus.

### La réception : dire ce que c'est, et laisser la régler

La barre latérale annonçait « E-mails traités automatiquement par le poller en arrière-plan
(`poller.py`) » — un nom de fichier en guise d'explication, que personne dans une équipe
commerciale ne peut situer — et rien n'était réglable : ni l'activation, ni les horaires, ni la
fréquence, l'intervalle étant même lu **à l'import**, donc figé jusqu'au redémarrage.

Conséquence concrète, pas théorique : un e-mail arrivé à 3 h du matin était analysé à 3 h du matin,
consommait du quota, déclenchait une alerte — pour une équipe qui ne la verrait qu'à 9 h, et avec
une analyse qui paraîtrait « ancienne » alors que personne n'aurait pu la traiter plus tôt.

La légende dit maintenant ce que fait le produit en une phrase, un relevé montre l'état réel, et un
panneau de réglages contrôle marche/arrêt, jours, plage horaire et fréquence — relus à chaque cycle,
sans redémarrage. Les heures sont locales et naïves, délibérément : une équipe énonce ses horaires
en heure de bureau, pas en UTC. La fenêtre à cheval sur minuit (22 h → 6 h) est gérée, parce que
c'est le cas que la comparaison naïve transforme silencieusement en plage vide. Et toutes les
fonctions d'analyse des réglages sont tolérantes : ces valeurs viennent d'un formulaire, et une
saisie erronée qui ferait tomber la boucle signifierait qu'**aucun** e-mail n'est plus relevé — une
panne bien pire que le réglage raté qui l'a causée.

### Ce que la vérification a trouvé, et que la relecture n'aurait pas vu

1. **Le formulaire de rappel se repliait avant d'être validé.** Ses trois champs déclenchaient
   chacun un rerun ; valider la note par Entrée relançait le script et refermait l'accordéon avant
   qu'on ait pu cliquer. Corrigé en `st.form` — ce que ces trois champs auraient toujours dû être,
   puisqu'ils décrivent une seule intention.
2. **Un test existant a rattrapé une promesse cassée.** Le réglage « police Système » garantit
   *aucun appel à un CDN* ; les nouveaux imports de titrage et de monospace la violaient
   discrètement. La correction coupe les trois imports d'un coup : ce réglage est une promesse sur
   le réseau, pas sur une police.
3. **Une erreur dans mon propre outillage de sonde**, consignée par honnêteté : la variable de
   redirection de base était restée à un ancien nom, si bien que l'application de test écrivait
   dans le vrai `data/tasks.sqlite` du dépôt. Trois rappels de test y ont atterri avant que je le
   remarque — supprimés, variable corrigée, et `conftest.py` complété pour que la suite ne puisse
   jamais reproduire le problème.

### Vérifications réellement effectuées

- Suite complète : 561 → **606 tests**, hors ligne, ~20 s (`test_intake_window.py`,
  `test_task_store.py`, plus des ajouts à `test_ui_kit.py`).
- Mesure du DOM réel avant/après pour le chevauchement (52,5 px contre 30,8 px, puis 63 px).
- Analyse complète dans un vrai navigateur : le cartouche de signature s'affiche, un rappel créé
  depuis l'interface existe réellement en base, les deux panneaux de réglages sont présents.
- Branche « envoi programmé » vérifiée via `AppTest` en semant la clé de session qu'un import Gmail
  fournirait : l'option apparaît pour un lead Gmail, reste absente pour une saisie manuelle (il n'y
  a alors aucun brouillon à expédier), et le bouton devient « Valider et programmer l'envoi ».

### Ce qui reste non vérifié, dit clairement

L'envoi programmé n'a **jamais été déclenché contre un vrai compte Gmail** : le mode démonstration
ne produit que des saisies manuelles, sans fil Gmail. La branche d'interface est vérifiée, le
stockage et l'annulation le sont par tests, mais `send_draft` n'est couvert que par son contrat de
dégradation gracieuse — même limite que tous les autres chemins dépendant de Gmail dans ce projet.

---

## 2026-08-04 — Passer le relais à un collègue, et raconter le mois écoulé

### Les trois demandes

L'utilisateur a demandé trois choses d'un coup :

1. Qu'un **opérateur puisse transmettre à l'administrateur des e-mails précis** à faire relire avant
   validation — **plusieurs d'un seul geste**, et que l'administrateur les voie en se connectant.
2. Qu'un **PDF mensuel** raconte ce qui s'est passé, des e-mails aux statistiques, avec des
   graphiques, **en comparant au mois précédent** pour que ce soit utile.
3. Que ce PDF soit **paramétrable au maximum** : choisir ce qu'il contient (par exemple « la
   catégorie et le nom des e-mails seulement »), la période, « n'importe quoi » — toujours avec le
   contexte, et toujours à un thème.

### 1. Le geste qui manquait dans l'outil

Jusqu'ici, un opérateur devant un lead qui le dépasse n'avait que trois issues : **valider** (ce qui
écrit dans le CRM), **rejeter** (ce qui fait disparaître le lead de la file de toute l'équipe), ou
**ne rien faire** et prévenir son responsable par un autre canal — un message, un mot dans le
couloir. Dans ce dernier cas, l'information sort du produit : plus de trace, plus de date, plus
personne pour savoir si quelqu'un s'en est occupé.

Le troisième geste — « je ne tranche pas, quelqu'un doit regarder » — n'existait pas. C'est
exactement ce qui a été ajouté.

**Comment ça marche, concrètement.** Une nouvelle page « Relectures ». L'opérateur y voit la file
d'attente (ou les e-mails des 30 derniers jours), **coche plusieurs lignes**, écrit pourquoi
(« clause de pénalité inhabituelle »), choisit le destinataire — par défaut « tous les
administrateurs » — et envoie. L'administrateur, à sa prochaine connexion, voit :

- une **pastille rouge** dans l'en-tête (« 2 relecture(s) à traiter »),
- un **panneau dans la barre latérale** indiquant qui a demandé quoi,
- un **toast**, annoncé une seule fois par lot.

Il peut ouvrir chaque lead, répondre par écrit, traiter ou écarter — ou répondre au lot entier d'un
seul clic quand l'avis vaut pour tout le monde.

**Pourquoi un nouveau registre plutôt que réutiliser celui des tâches (§19).** Les deux se
ressemblent en surface, mais pas du tout dans leur fonctionnement :

- une **tâche** est *datée* : c'est le planificateur qui la déclenche quand l'heure arrive, et elle
  se termine toute seule ;
- une **demande de relecture** est *adressée* : c'est la connexion d'une personne qui la fait
  apparaître, et elle se termine quand cette personne a décidé quelque chose.

La question posée n'est pas la même : « qu'est-ce qui est échu ? » d'un côté, « qu'est-ce qui
m'attend ? » de l'autre. Les mettre dans la même table aurait obligé le planificateur à sauter ce
type, la purge à le traiter à part, et la liste des échéances à l'exclure — trois exceptions dans
une table dont l'intérêt était justement d'être uniforme.

**Un détail qui compte : les informations de l'e-mail sont recopiées dans la demande.** L'objet et
l'adresse de l'expéditeur sont dupliqués plutôt que référencés, pour qu'un administrateur puisse
encore comprendre de quoi il s'agit si le lead a été effacé entre-temps par la purge RGPD. Une
demande dont l'intitulé s'évapore ne peut plus être ni comprise ni close.

**Deux garde-fous repris du §19 :** « vu » et « traité » restent deux choses différentes (consulter
une demande ne la retire pas de la file, sinon une relecture ouverte puis oubliée serait perdue pour
tout le monde) ; et une demande déjà tranchée ne peut pas l'être une deuxième fois — deux
administrateurs peuvent parfaitement ouvrir la même file au même moment, et le second ne doit pas
écraser la réponse du premier.

### 2. Le rapport mensuel : un tableau de bord montre un état, un rapport raconte une évolution

Toutes les données existaient déjà. Le projet compte les e-mails classés, les validations, les
gestes de chaque personne, les envois programmés. Mais elles n'existaient qu'**à l'écran, en
« N derniers jours », et jamais comparées**. Personne ne pouvait répondre à la seule question qui
justifie de reconduire un outil : « qu'est-ce que ça nous a apporté en juillet, par rapport à
juin ? »

Le rapport mensuel est produit automatiquement par le planificateur, en PDF, aux couleurs de
l'entreprise. Il couvre toujours le **dernier mois entièrement écoulé** — jamais le mois en cours,
qui n'a pas fini de recevoir des lignes : un rapport « du mois » produit le 12 ne porterait que sur
onze jours et se comparerait à un mois plein, ce qui inventerait une chute d'activité qui n'a pas eu
lieu.

**La comparaison est faite honnêtement**, et c'est le point le plus important :

- elle porte sur la **période de même durée qui précède**, pas sur « le mois d'avant » pris
  naïvement. Comparer 31 jours à 28 ferait apparaître février en baisse de 10 % chaque année sans
  qu'il s'y passe quoi que ce soit ;
- une hausse n'est **pas automatiquement une bonne nouvelle**. Chaque indicateur déclare le sens qui
  lui est favorable : un délai de réponse qui augmente s'affiche en rouge, un volume qui augmente en
  vert. Tout colorier en vert produirait un rapport flatteur et faux ;
- passer de 0 à 3 n'affiche **aucun pourcentage**, juste « +3 ». Écrire « +100 % » raconterait une
  progression qui n'a pas de base de comparaison.

**Le contenu est classé en quatre familles**, qui correspondent à quatre lecteurs : activité
commerciale (le commercial), qualité et intervention humaine (le responsable), traçabilité et
conformité (l'administrateur), exploitation (celui qui fait tourner l'outil). Quinze tableaux
alignés dans l'ordre où le code les a produits ne se lisent pas. Chaque famille commence sur une
page neuve, ce qui rend le document feuilletable.

### 3. Le rapport paramétrable : « le plus paramétrable possible »

La même machinerie, pilotée depuis une page « Rapports ». On y choisit :

- **la période** : mois dernier, ce mois-ci, 7/30/90 derniers jours, ou deux dates précises ;
- **les sections** : quatorze au total, cochées une par une, chacune accompagnée d'une phrase qui
  dit ce qu'elle apporte ;
- **les colonnes du détail e-mail** : c'est là qu'on obtient « la catégorie et l'expéditeur
  seulement », comme demandé, ou au contraire le détail complet ;
- **des filtres** : par catégorie, par expéditeur, leads validés seulement ;
- **un titre et une note de contexte** libres, imprimés sur la couverture ;
- et l'ensemble peut être **enregistré comme préréglage** réutilisable — un rapport qu'il faut
  recomposer case par case chaque mois ne sera composé qu'une fois.

Un préréglage enregistre volontairement **tout sauf les dates** : « Revue mensuelle direction »
décrit un contenu, pas un mois. Y figer juillet en ferait un préréglage inutilisable dès août.

**« Toujours avec le contexte »** a été pris au sérieux. Chaque bloc du document porte une phrase
qui explique d'où vient le chiffre et sur quoi il porte, et la couverture liste les sections
demandées. Sans cette liste, un lecteur ne peut pas distinguer « il ne s'est rien passé » de « cette
section n'a pas été demandée » — deux conclusions opposées tirées de la même absence. Un rapport
circule : il finit dans une réunion trois semaines plus tard, et un nombre sans son mode de calcul y
devient au mieux inutile, au pire trompeur.

**« Toujours avec un thème »** aussi, avec une nuance assumée : le document reprend les couleurs du
client, **sauf si elles le rendent illisible**. Un thème sombre est parfait à l'écran et désastreux
sur un document imprimé puis transféré — du texte clair sur du papier clair. Le papier est donc
toujours clair, et l'encre du client n'est conservée que si son contraste tient la norme
d'accessibilité. La couleur d'accent, elle, est toujours respectée : c'est celle qu'on reconnaît.

**Aucune nouvelle dépendance.** Les graphiques (barres avec la période précédente en filigrane,
courbe de volume avec son aire remplie) sont dessinés directement avec la bibliothèque PDF déjà
présente dans le projet. Ajouter matplotlib pour quatre diagrammes aurait fait entrer des dizaines
de mégaoctets, une police à embarquer et un moteur de rendu de plus — pour produire des images
qu'il aurait fallu recolorier à la main de toute façon, puisque la palette vient du client.

### Ce que la vérification a trouvé, et que la relecture n'aurait pas vu

Trois défauts réels, tous découverts en **regardant le document produit** ou en écrivant un test —
pas en relisant le code :

1. **Chaque paragraphe en français dépassait la marge droite** et se faisait couper au bord de la
   page. La fonction qui mesure la largeur d'un texte sous-évalue gravement les lettres accentuées :
   « ééééééééée » mesurait 22,8 points là où « eeeeeeeeee » en mesurait 45,6 — les « é » comptaient
   pour presque rien. Le calcul de retour à la ligne se croyait donc dans les clous. Visible d'un
   coup d'œil sur la page rendue, invisible dans le code.
2. **Les « … » et les « — » sortaient en petits points parasites**, dans chaque cellule tronquée
   d'un tableau et à chaque valeur absente. Les polices standard d'un PDF sont écrites avec un
   encodage occidental limité : la police possède bien ces caractères, mais le document n'a aucun
   moyen de les désigner. Les lettres accentuées, elles, passent très bien — ce qui rendait le
   défaut d'autant plus facile à ne pas voir. Corrigé **à un seul endroit**, là où le texte est posé
   sur la page, plutôt qu'aux vingt-trois points d'appel : même raisonnement que la correction
   d'encodage console faite plus tôt dans le projet.
3. **Une faute de frappe dans une couleur de marque empêchait tout le rapport d'exister.** Une
   valeur invalide dans le fichier de configuration faisait échouer le rendu, qui renvoyait « rien »
   conformément à son contrat de robustesse — donc plus aucun rapport mensuel n'était produit, en
   silence, la nuit. Une couleur est un ornement : elle ne doit pas décider si le document existe.
   Corrigé par un repli sur la couleur par défaut.

Une quatrième chose, trouvée en montant le test de bout en bout : le premier scénario donnait à la
fausse session une date de début en 1970, si bien que l'application affichait l'écran de connexion —
la session avait **légitimement** expiré. Autrement dit, une protection qui fonctionne se faisait
passer pour une fonctionnalité manquante. Corrigé côté test, pas côté produit.

### Vérifications faites

- **Suite de tests : 620 → 697** (tout hors ligne, ~30 s). Trois nouveaux fichiers — le registre des
  relectures (21 tests), le moteur de rapport (27), le rendu PDF (25) — plus quatre tests de
  planificateur pour le travail mensuel.
- **Le PDF est réellement relu** dans les tests : on le construit, on le rouvre, on en extrait le
  texte et on vérifie que le contenu attendu y est. « Aucune erreur » ne prouve pas qu'un document
  est lisible — une page blanche passe ce test-là.
- **Les deux nouvelles pages ont été rendues pour les trois rôles** (administrateur, opérateur,
  lecteur) sans exception.
- **Un parcours réel a été rejoué** : Marie transmet deux e-mails, l'administrateur les voit avec la
  note et les objets, Marie ne les voit pas dans sa propre file de réception mais bien dans ses
  envois.
- **L'en-tête, la barre latérale et le toast ont été vérifiés** : l'administrateur voit les trois,
  l'opérateur aucun, et un lot de deux e-mails ne produit **qu'un seul** toast.
- **Le rapport a été inspecté page par page**, en images, avant et après correction des deux défauts
  de mise en page.

### Ce qui reste non vérifié, dit clairement

Le rapport mensuel n'a **jamais tourné sur douze mois de données réelles** : il est vérifié sur des
données synthétiques et par le travail planifié en test. La notification « votre rapport est prêt »
n'atteint personne si ni Slack ni l'adresse e-mail ne sont configurés — le rapport reste alors
visible dans l'onglet, mais rien ne va le chercher. Et le PDF n'est **pas joint** à cette
notification : le module d'envoi ne transporte que du texte, et lui ajouter la gestion des pièces
jointes dépassait la demande.


## 2026-08-05 — Une passe de design qui a surtout trouvé du design déjà écrit mais jamais affiché

**La demande.** « Améliore le design Streamlit au maximum, chaque élément avec une intention, que ce
soit net et fluide », en s'appuyant sur des méthodes de design d'interface (Emil Kowalski pour le
mouvement, une skill de direction artistique, et la skill Streamlit maison).

**Ce qu'on croyait faire, et ce qu'on a fait.** On s'attendait à « redécorer ». En pratique, la
règle qu'on s'est donnée dès le départ a tout changé : *ne rien juger sur le code, tout mesurer sur
la page réellement affichée*. On a donc lancé l'application dans un bac à sable (copies des bases,
mode démonstration, aucune clé d'API) et piloté un vrai navigateur pour relever les valeurs
calculées par le moteur de rendu. Résultat : l'essentiel du travail n'a pas été d'inventer un
nouveau style, mais de **faire exister celui qui était déjà écrit et qui ne s'affichait pas**.

### Cinq choses qui étaient dans le fichier et pas à l'écran

**1. Les titres n'étaient pas dans la bonne police.** Le projet s'était donné en §19 trois « voix »
typographiques : une serif pour la voix du document, une sans pour la voix de l'outil, un monospace
pour les valeurs de la machine. Sur la page, un titre de section calculait « Segoe UI ». Explication
en clair : en CSS, quand deux règles veulent la même chose, c'est la plus « précise » qui gagne, pas
la dernière écrite. Notre règle disait `h1, h2, h3` (très général) ; celle de Streamlit disait
`.st-emotion-cache-1vxakfx h3` (plus précise). La nôtre perdait à tous les coups. Le seul titre qui
sortait bien en serif était l'en-tête de marque — et par hasard, parce qu'il est désigné par une
classe et non par son nom de balise. Autrement dit : **un tiers de la thèse typographique du projet
n'avait jamais été visible par personne.**

**2. La barre d'en-tête était transparente.** §19 avait corrigé un chevauchement en lui donnant un
fond opaque. Sauf qu'une vieille ligne `background: transparent` traînait cent trente lignes plus
bas dans le même fichier et l'annulait. Mesuré sur la page : `rgba(0, 0, 0, 0)`. Le contenu défilait
donc sous un filet horizontal derrière lequel il n'y avait rien.

**3. La hauteur réservée à cette barre était fausse.** La variable valait `3.5rem`. Un « rem » est
une unité relative à la taille de police de base. On supposait 16 pixels — mais le fichier de thème
fixe cette base à 14. La variable censée *décrire* une barre mesurée à 52,5 px n'en valait donc que
49. Elle est passée en pixels : une valeur qui prétend décrire une mesure doit décrire cette mesure.

**4. L'onglet de la page courante était gris.** Relevé : l'onglet actif recevait un gris neutre de
Streamlit, pendant que le survol recevait la couleur de marque, un léger soulèvement et une ombre.
La hiérarchie était **inversée** — l'effet le plus visible désignait l'endroit où passe la souris,
pas l'endroit où l'on se trouve. Sur une barre à sept entrées, c'est pourtant la seule information
qui compte.

**5. Deux catégories du tableau de bord se dessinaient dans la même couleur.** La palette des
graphiques était fabriquée en enfilant les six couleurs « à sens » (principale, succès,
avertissement, accent, information, erreur). Or rien n'oblige ces six-là à être différentes : dans
la palette par défaut, « information » vaut la couleur principale et « avertissement » vaut la
couleur d'accent — ce qui est **juste** du point de vue du sens, et devient faux dès qu'on aplatit
la liste en palette de graphique. Une légende à cinq entrées n'en distinguait que trois.

### Le vrai défaut de conception : le signal de décision pouvait disparaître

C'est la trouvaille qui dépasse la cosmétique. Depuis §19, la couleur d'accent n'est plus « la
deuxième couleur de la marque » : elle a **un seul rôle**, signaler *ce qui attend une décision
humaine* — le cartouche « Bon pour accord », la pastille d'alerte, la fin du rail de décision. C'est
le repère central d'un produit dont toute la promesse est qu'une personne tranche avant que quoi que
ce soit parte.

Mais les dix-huit palettes livrées avaient été écrites **avant** que l'accent reçoive ce rôle, et
personne n'y était revenu. Sur l'instance de test — qui tourne avec une palette bleue — il n'y avait
littéralement **pas un pixel** de la couleur réservée : accent et couleur principale étaient deux
bleus. L'écran restait joli et n'indiquait plus où agir.

Pour mesurer ça, il a fallu trouver le bon critère, et le premier essai était faux. On a d'abord
utilisé le contraste WCAG (l'outil habituel) : il donne le résultat **à l'envers**, parce qu'il
mesure une différence de clair/foncé et pas de couleur — le couple par défaut pétrole/ambre, qui
saute aux yeux, y obtient une note médiocre, tandis que bleu foncé/bleu clair y obtient une bonne
note tout en restant « du bleu ». Deuxième essai en teinte + saturation : faux aussi, et le
classement des dix-huit palettes l'a montré tout de suite (deux turquoises passaient pour distincts,
un vert profond et un sable étaient signalés à tort). La bonne mesure ignore complètement la
clarté : on compare les deux couleurs **dans le plan des couleurs** d'un espace perceptif (CIELAB),
ce qui reproduit enfin le jugement de l'œil — couleur identique = 0, bleu foncé/bleu clair = 0,08,
pétrole/ambre = 0,74.

Quatre palettes livrées ont donc reçu un nouvel accent, et une cinquième pour une raison différente :
« Accessibilité renforcée » passait la mesure automatique, mais associait bleu et violet, c'est-à-dire
exactement la paire que confondent les personnes daltoniennes — sur une palette dont le nom promet
l'accessibilité, se fier à une mesure qui suppose une vision normale était le pire endroit possible.
Au passage, la règle a été formulée plus honnêtement : ce qui compte n'est pas que l'accent soit
*chaud*, mais qu'il soit **réservé et impossible à confondre**. La palette « Corail », dont la
couleur de marque est déjà chaude, reçoit donc un accent froid.

Enfin, un garde-fou a été ajouté au panneau Apparence : si un administrateur choisit un accent trop
proche de sa couleur principale, il est **prévenu** — jamais empêché, c'est sa charte graphique.

### Le mouvement : une hypothèse démentie par la mesure

Point de méthode intéressant. On soupçonnait que les animations d'entrée se rejouaient à **chaque**
interaction (Streamlit ré-exécute tout le script au moindre clic), ce qui aurait imposé de les
supprimer purement et simplement. Avant de toucher à quoi que ce soit, on l'a vérifié en interrogeant
le navigateur avant et après un clic : les animations restaient à « terminée », inchangées. React
réutilise les éléments existants au lieu de les recréer, donc l'animation ne repart pas.
**L'hypothèse était fausse, et les animations ont été gardées.** Elles ont seulement été raccourcies
sous 300 ms (au-delà, un mouvement cesse d'être perçu comme une réponse et devient une attente), et
toutes les courbes ont été rassemblées en deux variables partagées au lieu de cinq écritures
différentes semées dans le fichier.

Trois ajouts sur le ressenti, chacun avec une raison : un **enfoncement du bouton** au clic (avant,
appuyer ne produisait aucun signal — or ici chaque clic déclenche un aller-retour serveur, donc
c'est la seule confirmation immédiate possible) ; le **survol réservé aux souris** (sur tactile,
`:hover` se déclenche au toucher et *reste* actif, si bien que le bouton qu'on vient d'utiliser a
l'air sélectionné) ; et un **contour de focus au clavier** sur les boutons et les liens (il n'existait
que sur les champs de saisie — sur l'écran de validation, ne pas voir le focus revient à ne pas
savoir quel bouton on s'apprête à actionner).

Une seule chose a été **retirée** : l'icône des écrans vides flottait en boucle indéfiniment. C'était
le seul mouvement perpétuel purement décoratif ; dans un outil ouvert toute la journée, il se
disputait l'attention avec les deux boucles qui, elles, signalent vraiment quelque chose (le pouls
d'une analyse en cours, la lueur de la bannière de sécurité). Enlever un accessoire rend les autres
audibles.

### Deux corrections de lisibilité

Le gris des textes secondaires (accroches, relevés, libellés d'indicateurs — donc du **petit**
texte) était sous le seuil d'accessibilité AA. Il a été assombri. Détail méthodologique utile : un
premier réglage calibré à la main sur quatre palettes passait ces quatre-là et **échouait** sur une
cinquième ; c'est le test paramétré sur les dix-huit palettes qui l'a rattrapé immédiatement. Le
réglage final laisse de la marge sur toutes.

Les cartes, elles, reposaient entièrement sur une différence de couleur entre le fond de page et le
fond des cartes — que rien n'oblige un client à conserver, et que plusieurs palettes livrées
annulaient presque (mesuré : 1,01 contre 1). Elles reçoivent désormais une ombre très basse en
permanence : la séparation devient une propriété du système et non un coup de chance de palette.

### Le fichier de thème n'avait pas suivi

`.streamlit/config.toml` — la couche qui atteint l'*intérieur* des composants Streamlit (menu
déroulant ouvert, en-tête de tableau, palette des graphiques) — était resté sur le bleu Microsoft
d'avant §19. Une installation neuve s'affichait donc pour moitié dans la palette voulue et pour
moitié dans une palette abandonnée. Il a été régénéré depuis les valeurs par défaut du produit,
c'est-à-dire exactement ce que l'application écrit elle-même quand un administrateur enregistre sa
marque.

### Ce qui a été vérifié, et ce qui ne l'a pas été

Vérifié : la suite complète passe (**697 → 762 tests**, 65 ajoutés) ; chaque correction est relevée
sur la page réellement affichée, avant et après (barre d'en-tête devenue opaque, titres passés en
serif, onglet actif passé à la couleur de marque, gris secondaire assombri, aucun débordement
horizontal à 900 px de large) ; les graphiques se dessinent bien — une capture prise trop tôt les
montrait vides, ce qui a été levé en comptant les éléments réellement dessinés plutôt qu'en se fiant
à l'image.

Les soixante-cinq tests ajoutés portent volontairement sur la **feuille de style produite**, pas sur
l'intention : c'est précisément parce qu'une règle CSS morte ne lève aucune erreur, ne casse aucun
test et ne se voit pas à la relecture que ces cinq défauts avaient pu survivre à plusieurs passes.

Non vérifié, et il faut le dire : rien n'a été regardé sur un vrai téléphone ni sur une vraie
tablette (on ne dispose ici que d'un navigateur redimensionné) ; le mode sombre et les dix-huit
palettes ne sont contrôlés que par calcul, aucune n'a été ouverte à l'œil ; et les sélecteurs
utilisés restent des détails d'implémentation de Streamlit 1.59 — une montée de version peut rendre
la page **moins jolie**, jamais cassée, puisque aucune de ces règles ne conditionne une
fonctionnalité.

**Une conséquence pour l'instance actuelle, à signaler.** Les couleurs de cette installation ont été
enregistrées explicitement dans les réglages (un bleu foncé et un bleu clair). Or le produit garantit
depuis toujours qu'une couleur *explicitement choisie* n'est jamais écrasée — ni par un préréglage,
ni par une correction comme celle-ci. La correction des palettes ne changera donc **pas** cet écran.
En revanche, le panneau Apparence affiche maintenant l'avertissement : accent trop proche de la
couleur principale, séparation 0,08 sur 1. Vider le champ « couleur d'accent » suffit à retomber sur
la valeur corrigée du préréglage.

### Ajout demandé dans la foulée : un fond d'ambiance

Demande : « ajoute une petite animation de fond ». La tension est réelle et vaut d'être notée, parce
qu'on venait de **retirer** une animation perpétuelle (l'icône flottante) au motif qu'un mouvement
sans fin dans un outil ouvert toute la journée dispute l'attention aux deux boucles qui signalent
vraiment quelque chose. Ajouter un fond animé juste après ne pouvait donc pas être fait à la légère :
il fallait qu'il tienne trois conditions, chacune héritée d'une décision déjà prise.

**Ce qu'il dit.** La machine tourne même quand personne ne regarde — le relevé d'e-mails et le
planificateur travaillent en tâche de fond, et un écran parfaitement inerte quand la file est vide
dit le contraire de ce que fait le produit. C'est un fond qui *respire*, pas un objet qui bouge.

**Trois garde-fous.** (1) **Froid uniquement** : le voile emprunte la couleur principale, jamais
l'ambre — celle-ci ne signifie qu'une chose, « une personne doit trancher », et l'employer
décorativement aurait vidé le signal de son sens, c'est-à-dire refait le défaut corrigé sur quatre
palettes le matin même. (2) **Sous le seuil de l'attention** : 7 % de la couleur de marque, des
rayons énormes, aucun contour, et 48 secondes par cycle. C'est la lenteur qui fait la différence
entre une matière et un objet — à 10 s l'œil suit le mouvement, à 48 s l'écran n'est jamais tout à
fait le même sans qu'on puisse dire ce qui a changé. (3) **Le plan de travail seulement** : la barre
latérale et l'en-tête ont leurs propres fonds opaques et passent par-dessus, donc le chrome reste
stable.

**Mesuré plutôt que supposé**, comme le reste de la journée. Sur une bande de fond sans contenu, le
voile ne fait varier l'image que de **5 niveaux sur 255 au maximum** en douze secondes (moyenne
inférieure à 1) : le mouvement est littéralement imperceptible d'un instant à l'autre. En revanche,
d'un bout à l'autre de la page, le fond passe de 245 à 237 — soit une profondeur bien visible. C'est
exactement le réglage recherché : perceptible comme relief, imperceptible comme déplacement. Une
première mesure brute annonçait un écart de 227 sur 255 entre deux captures ; en isolant les zones,
il s'agissait de la notification de rappel qui apparaissait entre les deux images, pas du voile —
d'où l'intérêt de mesurer une région sans contenu plutôt que l'écran entier.

Deux détails techniques qui comptent. Le voile est animé en `transform` uniquement, donc composé par
la carte graphique : aucun recalcul de mise en page ni repeinture, ce qui est indispensable pour la
seule animation de la feuille qui ne s'arrête jamais (animer la position du dégradé aurait été plus
court à écrire et aurait repeint la page entière à chaque image). Et le dégradé est posé dans le
bloc *statique* de la feuille, pas dans le bloc d'animations : un client qui choisit « animations :
sobre » ou « aucune » garde la profondeur et perd seulement le mouvement — et `prefers-reduced-motion`
fige la boucle sans effacer le fond, ce qui est le bon comportement (moins de mouvement, pas moins
d'interface).

Vérifié en direct sur le DOM : `position: fixed`, `z-index: 0`, `pointer-events: none`, dégradé bien
construit sur la couleur principale, animation `48s infinite` en cours, et le contenu principal
au-dessus (`position: relative; z-index: 1`) — cette dernière règle n'est pas décorative : sans
elle, le voile serait passé **devant** la page.

**Puis l'utilisateur a répondu : « je ne vois pas l'animation de fond ». Il avait raison, et ma
vérification était en défaut.** Deux causes, dont une entièrement de mon fait.

La première est banale : Streamlit ne recharge pas un module importé comme `branding.py` quand on
enregistre le fichier. Une instance démarrée avant la modification continue de servir l'ancienne
feuille de style — c'est d'ailleurs le même piège qui m'avait fait croire deux fois dans la journée
qu'un correctif « ne prenait pas ».

La seconde est un vrai défaut de conception. Les rayons des voiles étaient exprimés en `rem`, donc
figés à 588 pixels (la racine est à 14 px, imposée par `config.toml`). Sur un écran large, deux
taches de 588 px dans une couche de près de 2 900 px deviennent deux petits îlots, dont l'un tombait
carrément hors du champ visible. Mesuré à 1892 px de large : **six points de fond sur sept étaient
rigoureusement intacts** (245, 245, 245), et seul le coin inférieur droit portait la couleur.

Ce qui rend l'erreur intéressante, c'est *pourquoi je ne l'avais pas vue* : ma mesure « le voile ne
varie que de 5 niveaux sur 255 » avait été prise à 1440 px de large, et sur une bande qui se trouvait
justement près de la seule tache visible. J'avais donc mesuré une vraie valeur, au seul endroit qui
la rendait flatteuse, et j'en avais tiré la conclusion inverse de la bonne : je croyais avoir réglé
une subtilité, j'avais en fait une décoration absente sur la majeure partie de l'écran.

Correctif en trois points. (1) Rayons en `vmax` : une décoration de fond se mesure à la **fenêtre**,
pas à la taille du texte. (2) Débordement ramené de 25 % à 10 % : la dérive ne déplace le voile que
de 2,5 %, donc une couche 1,5 fois plus grande que l'écran ne servait qu'à repousser les taches hors
du cadre. (3) Intensité portée de 7 % à 14 % — et c'est une correction de jugement autant que de
code : « en dessous du seuil d'attention » était mon critère, alors que la demande était *une
animation de fond qu'on voit*. Un effet que l'utilisateur ne peut pas percevoir n'est pas un effet
discret, c'est un effet raté. L'intensité est en revanche **plus faible en mode sombre** (9 %), parce
que sur fond clair le voile assombrit la page — donc augmente le contraste du texte — tandis que sur
fond sombre il l'éclaircit et le réduit.

Après correction, au même format d'écran : **six points sur sept portent le voile** (contre un seul),
pour un écart de 22 niveaux de luminance d'un bord à l'autre — visible comme une matière, sans
jamais concurrencer le contenu. Suite : 762 → **767 tests**, dont un qui verrouille précisément la
leçon (les rayons doivent rester en unités de fenêtre, jamais en `rem`).


## 2026-08-06 — Un tableau de bord qui montrait un état, et ne racontait aucun changement

**La demande.** Rendre le tableau de bord « plus joli, plus interactif, avec plus de statistiques »,
avec la proposition explicite de changer la police si je le souhaitais.

**Le diagnostic.** L'écran affichait cinq compteurs et trois graphes sur « les N derniers jours ».
C'est un ÉTAT. Or les trois questions qu'on se pose vraiment en ouvrant cet onglet sont : *est-ce
que ça va mieux qu'avant ?*, *est-ce qu'on répond assez vite ?*, *est-ce que la réception automatique
sert à quelque chose ?* Aucune n'avait de réponse — et pourtant **les données étaient déjà toutes
enregistrées**. Il ne manquait que des lectures et une comparaison. C'est la même forme de manque
que le projet a déjà rencontrée trois fois (`get_draft_edit` au §18, `list_events` au §20, et la
répartition des délais dont le commentaire décrivait l'usage sans que personne ne l'affiche).

### Quatre statistiques, chacune répondant à une question précise

- **Rapidité de réponse** (moins d'1 h / 1-4 h / 4-24 h / plus de 24 h). La fonction qui calcule les
  délais portait depuis toujours, dans son propre commentaire, l'intention « répondre en moins d'1 h
  contre plus de 24 h » — et n'affichait qu'un tableau brut de minutes. C'est pourtant la statistique
  commercialement décisive : au-delà de 24 h, un prospect a généralement déjà sollicité un concurrent.
- **Origine des e-mails** (réception automatique / import Gmail / saisie manuelle). La colonne
  existait, enregistrée à chaque analyse, et n'était affichée **nulle part**. C'est la mesure
  d'adoption : un outil présenté comme automatique dont l'essentiel du volume est ressaisi à la main
  ne l'est pas, et rien dans l'interface ne permettait de s'en apercevoir.
- **Heures d'arrivée** (histogramme sur 24 h). Utile parce qu'un réglage l'attend : le §19 laisse
  choisir une fenêtre de réception (jours et heures), qui se décidait jusqu'ici au jugé.
- **Correspondants les plus actifs**, avec une barre de proportion plutôt qu'un nombre — le rang
  relatif est toute la question qu'on pose à ce bloc.

Deux détails de justesse plutôt que d'affichage. Les tranches de délai **vides sont conservées** :
une tranche « plus de 24 h » absente se lirait « pas de données », alors qu'elle veut dire « aucun
retard », c'est-à-dire l'inverse exact et la meilleure nouvelle du tableau. Et les **24 heures sont
toujours renvoyées**, y compris à zéro, parce que les creux sont précisément l'information cherchée.

### La comparaison, et pourquoi elle ne pouvait pas être réécrite ici

Chaque indicateur affiche désormais son écart avec la période précédente. Deux choix de fond :

La fenêtre de comparaison **réutilise la fonction du rapport mensuel** (`previous_period`) au lieu
d'en refaire une. Si les deux divergeaient un jour d'une journée, l'écran et le PDF donneraient deux
chiffres différents pour la même période et personne ne saurait lequel croire. Cette fonction rend
la fenêtre de *même durée* qui précède — pas « le mois d'avant » — ce qui évite de faire apparaître
février en recul de 10 % chaque année sans qu'il s'y soit rien passé.

Et surtout : **chaque indicateur déclare quel sens lui est favorable.** Un délai de réponse qui
augmente est une dégradation ; le colorier en vert parce que la valeur monte produirait un tableau
flatteur et faux. Le taux d'édition des brouillons, lui, reste **gris** : un taux élevé signale des
propositions perfectibles, un taux nul peut signaler qu'on valide sans relire — la donnée ne permet
pas de trancher, donc l'interface ne tranche pas. Enfin, quand la période précédente n'a aucune
valeur, **rien** ne s'affiche plutôt que « +0 » : zéro laisserait croire à une stabilité alors qu'il
n'y a simplement aucun point de comparaison.

Un filtre global par catégorie a été **écarté volontairement**. Il aurait été facile à poser en haut
de page, mais il n'aurait pu s'appliquer ni à l'entonnoir, ni aux délais, ni aux tokens sans
réécrire cinq requêtes : la personne aurait filtré, la moitié de l'écran n'aurait pas bougé, et elle
n'aurait plus su ce qu'elle regardait. Un filtre qui ment sur sa portée est pire que pas de filtre.

### Trois défauts d'alignement, dont un que seules certaines données révèlent

**La rangée d'indicateurs partait en dents de scie.** Streamlit pose `align-items: start` sur ses
rangées : chaque carte se dimensionne donc sur son propre contenu, et il suffit qu'un indicateur
n'ait pas d'écart à afficher — faute de période de comparaison — pour qu'il soit 22 px plus court
que ses voisins. Le défaut n'apparaît donc **que sur certains jeux de données**, ce qui est la
meilleure façon de ne jamais le corriger. La correction a demandé deux essais : passer la rangée en
`stretch` n'a eu aucun effet (mesuré : la rangée passait bien à `stretch`, les cartes gardaient
144 / 144 / 122 / 144), parce qu'un élément flex dont la hauteur est **définie** ignore l'étirement.
Il fallait d'abord remettre cette hauteur à `auto`.

**Deux autres réglages du même ordre** : le compteur de tokens quitte la rangée principale — c'est
une mesure d'exploitation, pas commerciale, et à cinq éléments la rangée retombait à « 4 + 1 », une
carte seule sur une deuxième ligne ; et la courbe miniature d'un indicateur a été retirée, parce
qu'elle n'existait que pour un indicateur sur cinq (le seul disposant d'une vraie série quotidienne),
rendait sa carte plus haute que les autres, et traçait de toute façon la même série que le graphe
situé juste en dessous.

**Les barres passent à l'horizontale** là où les étiquettes sont longues (« DEMANDE_DEMO »,
« proposition rédigée ») : en vertical, le moteur de rendu les fait pivoter à 90°, ce qui oblige à
pencher la tête pour lire son propre tableau de bord.

### Ce que les skills de design ont apporté concrètement

La grille de relecture d'Emil Kowalski a fait apparaître un manque du passage précédent : le §21
avait donné un retour d'appui et un anneau de focus aux boutons, mais en ne visant que
`.stButton` / `.stFormSubmitButton` / `.stDownloadButton`. Or un `st.segmented_control` rend un
composant qui ne correspond à aucun des trois — si bien que **les deux commandes principales de ce
tableau de bord** (la période et la bascule de comparaison) étaient les seuls éléments cliquables de
l'application à n'accuser aucun enfoncement, précisément ceux qu'on actionne le plus souvent ici.
Au passage, Streamlit anime `all` sur ces boutons, ce qui inclut la géométrie : restreint aux
propriétés réellement concernées.

Le principe « les mots sont une matière de design » a produit une correction simple : les origines
s'affichaient `gmail_import` et `manuel`, des identifiants techniques. On nomme désormais le geste
(« Réception automatique », « Saisie manuelle »), pas le module qui l'exécute.

### La police : proposée, et non changée

L'utilisateur laissait le choix. Le système typographique actuel — une serif de titrage, une sans
pour l'outil, un monospace pour les valeurs machine — a été décidé au §19 puis **réparé au §21**,
où l'on a découvert que la serif n'avait en réalité jamais atteint le moindre titre de page. En
changer maintenant reviendrait à défaire un travail dont tout l'objet était de le rendre enfin
visible. C'est un changement d'une ligne si l'envie revient.

### Vérifié

Suite complète : 767 → **773 tests**. Rangée d'indicateurs mesurée à **0 px d'écart** après
correction (contre 22). Six graphes et deux tableaux rendus sans exception, relevés sur la page
réellement affichée. Les quatre nouvelles lectures ont été essayées sur les données réelles avant
d'être branchées.

Non vérifié : ces répartitions n'ont jamais tourné sur un volume important — la base de
démonstration compte une vingtaine d'analyses sur onze jours, ce qui suffit à valider la forme des
graphes mais pas leur lisibilité avec des centaines de correspondants. Et comme pour tout le reste
de l'interface, rien n'a été regardé sur un téléphone.

---

## 2026-08-06 (suite) — Une page de vente qu'on pouvait enfin atteindre, et refaite d'après le modèle demandé

### Le problème de départ

Deux demandes en une. **La première** : un bouton, dans l'interface, qui mène à la page de
présentation. Cette page existait depuis le §16.5, dans `docs/landing/index.html` — mais elle
n'était atteignable qu'en connaissant son chemin sur le disque et en l'ouvrant à la main. Autrement
dit : elle n'était jamais ouverte au moment où elle sert, c'est-à-dire quand on montre le produit à
quelqu'un, depuis l'écran du produit. Un support de vente qu'on ne peut pas sortir pendant la
démonstration ne sert à rien.

**La seconde** : la refaire en reprenant la direction artistique d'un gabarit précis (Stackgrid, un
modèle d'agence IA), et y mettre **tout ce qui a été construit depuis** — les six passes §17 à §22
n'apparaissaient nulle part sur la page, qui décrivait encore un produit s'arrêtant au §16.

### Ce qui a été fait

**1. Le modèle a été mesuré, pas deviné.** Plutôt que de travailler de mémoire à partir d'une
capture, la page de référence a été chargée dans un vrai navigateur et interrogée : couleurs
calculées, polices réellement utilisées, largeurs, rayons, structure des sections. Ce relevé a donné
un vocabulaire précis à reproduire — un canevas quasi blanc `#fdfdfd`, deux filets verticaux qui
enferment tout le document sur 1176 px, une serif de titrage (Instrument Serif) contre une
grotesque géométrique, des liens de navigation entre crochets `[comme ceci]`, des cartes bordées en
**pointillés**, des étiquettes noires reliées par des traits comme sur un schéma d'installation, des
coins de cadrage d'imprimeur, et une révélation au **flou** quand un bloc entre à l'écran. Le cadre
reconstruit mesure 1176 px à x=132 ; l'original, 1177 px à x=131.

**2. Les couleurs sont celles d'ACA, pas celles du modèle.** Structure, typographie et mouvement
sont repris ; l'accent est le pétrole et l'ambre du produit. L'ambre garde la règle posée au §19 :
il ne veut dire qu'une seule chose, « un humain doit décider ici ». Sur cette page il n'apparaît
donc que trois fois — le carré de la décision dans le logo, le nœud `⏸ PAUSE` du schéma, et
l'étiquette « votre validation » à l'arrivée de l'animation.

**3. Le contenu a été remis à jour, et les chiffres revérifiés un par un** plutôt que recopiés :
773 tests (la page annonçait 352), 15 nœuds et 21 arêtes lus dans le graphe compilé, 90 variables
d'environnement comptées dans `.env.example` (la page disait 54, et `CLAUDE.md` 83 — les deux
étaient périmés). S'y ajoutent une section entière sur les six passes §17-§22 et une section
sécurité de douze points, là où l'ancienne version en alignait six en une phrase chacun.

**4. L'animation d'en-tête raconte le produit.** Un champ de caractères ASCII où un flux
désordonné entre à gauche, se resserre, et se termine sur une marque nette : la validation humaine.
Elle s'arrête quand elle sort de l'écran ou que l'onglet passe en arrière-plan — c'est la seule
animation continue de la page, et la laisser tourner sous un onglet caché serait une consommation
de batterie que personne ne voit.

**5. Le fichier a déménagé, et c'est le cœur de la première demande.** Streamlit sait servir un
dossier `static/` (`server.enableStaticServing`). La page vit donc désormais dans
`static/landing.html` et le bouton de la barre latérale y mène par une simple adresse. Garder un
exemplaire dans `docs/` **et** un dans `static/` aurait garanti qu'un jour l'un des deux serait à
jour et l'autre non : il n'y a qu'un fichier.

### Ce qui a été trouvé en regardant la page rendue

Comme au §21, l'essentiel des défauts n'était pas visible en relisant le code.

- **Le point de convergence de l'animation était invisible**, pour deux raisons cumulées. D'abord un
  nombre de lignes **pair** : la bande se resserre vers le milieu, or avec 22 lignes aucune ne tombe
  exactement au centre, si bien que l'endroit le plus étroit passait entre deux lignes. Ensuite le
  fondu décoratif du bord droit, réglé à 91 %, effaçait purement et simplement la marque finale
  placée à 96 %. Le seul élément porteur de sens de toute l'animation était donc effacé par une
  décoration.
- **L'étiquette « ACA » du schéma s'affichait en noir sur son propre cartouche noir.** Un attribut
  `fill="…"` posé sur une balise SVG a une priorité inférieure à n'importe quelle règle CSS ; la
  règle générale l'écrasait. Le texte était bien là, simplement invisible.
- **La page défilait latéralement sur téléphone** (491 px de contenu pour 390 px d'écran). La grille
  de bureau utilisait `minmax(0, 1fr)` ; c'est en la « simplifiant » en `1fr` pour le mobile que le
  garde-fou avait sauté — un `1fr` nu prend pour minimum la largeur du contenu, et le bloc terminal
  imposait donc sa largeur à toute la page. Corrigé, puis remesuré à trois largeurs : la page ne
  déborde plus d'un seul pixel.
- **Les liens du pied de page auraient renvoyé une erreur 404 une fois la page servie.** Ils
  pointaient en relatif vers des fichiers du dépôt, ce qui fonctionne depuis le disque mais pas via
  le serveur. Ce sont désormais des **chemins affichés**, pas des liens : un lien mort vaut moins
  qu'un chemin qu'on peut retrouver.
- **Le bouton changeait de place selon le rôle connecté.** Placé après le bloc « Base de
  connaissances », réservé aux administrateurs, il apparaissait juste sous l'import Gmail pour un
  opérateur et bien plus bas pour un administrateur. Remonté avant, il occupe la même position pour
  tout le monde.

### Un compromis assumé

La version §16.5 s'interdisait **toute** ressource distante, et la raison était bonne : une page de
pitch qui dépend d'un CDN ne s'ouvre ni dans un train ni derrière le proxy d'un grand compte, or
c'est exactement là qu'on la montre. La refonte charge trois polices Google, parce que la direction
artistique demandée repose sur ces caractères précisément. Le compromis est borné et écrit dans le
fichier : chaque police est suivie d'une pile système complète, la page reste entièrement lisible et
composée hors ligne, et aucun script ni aucune feuille de style distants ne sont chargés.

### Vérifié

Suite complète : **773 tests**, inchangée. Page rendue et mesurée dans un vrai navigateur à 390,
768 et 1440 px — aucun débordement latéral, aucune erreur JavaScript, les 27 blocs à révélation
s'affichent tous, l'accordéon de la FAQ s'ouvre réellement. Chaîne complète éprouvée sur un serveur
Streamlit lancé pour l'occasion (registres SQLite redirigés vers un dossier temporaire, pour ne rien
écrire dans les vraies données) : l'adresse `/app/static/landing.html` répond 200 avec le bon
contenu, un fichier inexistant répond bien 404, le bouton est présent et visible dans la barre
latérale, le suivre mène à la page, et le sélecteur de langue le fait bien passer de « Page de
présentation » à « Product overview ».

Non vérifié, et dit franchement : rien n'a été regardé sur un vrai téléphone, seulement sur un
navigateur redimensionné — la même limite que tout le reste de l'interface. La page n'est hébergée
nulle part (même raison que TLS depuis le §15.1.9). Et le rendu **hors ligne**, sans les polices
Google, n'a pas été inspecté à l'œil : la pile de repli est déclarée et correcte, mais personne n'a
regardé à quoi la page ressemble exactement dans cet état.

---

## 2026-08-06 (fin) — La page de vente en anglais, et trois images qui existaient sans se voir

### Le problème de départ

Trois demandes. **(1)** Une version anglaise de la page, orientée SaaS / agence IA, plus
commerciale, avec les tarifs en fin de page et le bloc de contact du gabarit de référence. **(2)**
« Corrige cette image et ajoute celle qui manque, on dirait que tu n'as pas implémenté toutes les
images » — la refonte précédente utilisait une animation abstraite en guise d'illustration et
laissait de côté deux visuels du modèle : la composition « machine / main humaine » et les icônes en
pixel art des cartes de capacités. **(3)** Des paliers de tarifs et un calendrier de prise de
rendez-vous.

### Ce qui a été fait

**1. Une seule page, deux langues.** L'anglais est la version par défaut et vit directement dans le
balisage ; le français voyage à côté, attribut par attribut. Ce choix — plutôt que deux fichiers —
est le même raisonnement que celui qui avait sorti la page de `docs/` : un second exemplaire
traduit aurait divergé du premier dès la première correction de contenu. Sans JavaScript, on obtient
une page anglaise complète, jamais une page blanche.

**2. Les mots-clés viennent de la bonne source.** En allant chercher `acam.framer.website`, il est
apparu que c'est **votre propre déploiement du gabarit** : la page renvoie mot pour mot le texte du
modèle. Les formules commerciales sont donc reprises telles quelles (« The all new… era »,
« Engineered Core Capabilities », « The Human-AI Intersection », « Service Tiers », « Scale Your
Infrastructure »). Le vocabulaire de LangGraph a été ajouté là où il est **vrai** pour ce projet,
puisque ACA est réellement construit dessus : « Balance agent control with agency »,
human-in-the-loop, exécution durable, mémoire persistante, diffusion en direct.

**3. Toutes les illustrations sont dessinées à l'exécution.** Aucun fichier image n'est téléchargé :
les formes sont tracées sur un canevas invisible puis converties en caractères (les deux champs
ASCII) ou agrandies en gros pixels (les quatre icônes). C'est ce qui permet de reproduire le style
du modèle sans copier ses ressources, et de garder une page qui ne dépend d'aucun serveur d'images.

**4. Tarifs, calendrier, contact.** Trois paliers reprennent la structure du modèle — **les montants
sont des valeurs d'exemple**, signalées comme telles dans l'en-tête du fichier et repérables par un
attribut dédié, parce qu'ils ne sortent d'aucun chiffrage. Le calendrier est un vrai mois navigable
(semaine commençant le lundi, libellés dans la langue choisie, jours passés et week-ends non
sélectionnables) et le formulaire prépare un e-mail contenant le créneau retenu, au lieu d'envoyer
vers un serveur qui n'existe pas.

### Ce qui a été trouvé en regardant, et pas en relisant

Cette passe a répété la leçon des précédentes de façon presque caricaturale : **les défauts étaient
des formes correctement dessinées, mais invisibles.**

- **Le piège de la proportion.** Une cellule de police à chasse fixe est environ une fois et demie
  plus haute que large, et le champ est bien plus large que haut : en projetant naïvement un carré
  sur la grille, un cercle se retrouve étiré d'un facteur trois. La taille réelle d'un caractère est
  donc **mesurée sur la page affichée** — pas supposée, car la police de repli n'a pas la même
  chasse que celle qui est chargée — et les formes sont tracées à travers une correction.
- **L'étoile n'était pas une étoile.** Ses points de contrôle, placés trop loin du centre, rendaient
  les côtés presque droits : la forme s'affichait comme une pastille allongée, sans aucune pointe.
- **Le halo effaçait l'étoile.** Il était plus *grand* qu'elle, et son dégradé remplissait les creux
  entre les branches. La forme était juste, et personne ne pouvait la voir.
- **La main était une tache.** Dessinée ouverte avec quatre doigts, les écarts entre eux tombaient
  sous la taille d'un caractère et le tramage les fusionnait. Remplacée par une main qui pointe
  (poing fermé, un index épais, un pouce) sur un champ plus haut : ça survit à la réduction, et ça
  dit « qui se tend vers » plus clairement de toute façon.
- **L'icône « pipeline » était un cadre.** Quatre nœuds aux coins reliés tout autour : ça se lit
  comme une bordure, pas comme un flux. Devenue une chaîne de gauche à droite avec une dérivation.

### Sur les GIF de LangChain

La demande mentionnait de reprendre des vidéos ou GIF de la page LangGraph. Deux raisons de ne pas
le faire, dites franchement plutôt que contournées en silence : ce sont les ressources d'un tiers, et
les pointer depuis notre page casserait la propriété que tout le reste du fichier respecte (aucune
image distante). À la place, l'animation qui explique le produit est **la nôtre** et montre le vrai
graphe : chaque nœud s'allume à son tour, et la course **s'arrête** sur la pause humaine avant de
terminer — la pause étant l'argument central du produit, l'animation s'y arrête aussi au lieu de
glisser dessus.

### Vérifié

Suite complète : **773 tests**, inchangée. Page rendue et mesurée dans un navigateur réel à 390, 768
et 1440 px — aucun débordement latéral (`scrollWidth` exactement égal à la fenêtre aux trois
largeurs), aucune erreur JavaScript, les 31 blocs à révélation s'affichent tous. Interactions
réellement exercées : trois paliers avec leurs montants, calendrier d'août 2026 avec 18 jours ouvrés
sélectionnables, apparition des créneaux au clic sur une date, sélection d'un créneau, et **deux
allers-retours complets** anglais → français → anglais (titre, navigation et mois du calendrier
changent bien, l'attribut `lang` du document suit). En mouvement réduit : les deux champs ASCII
affichent une image fixe composée et **zéro** animation tourne. Chaîne complète re-testée sur un
serveur Streamlit lancé pour l'occasion (registres SQLite redirigés vers un dossier temporaire) :
`/app/static/landing.html` répond 200 avec le nouveau titre anglais, et le bouton de la barre
latérale y mène toujours.

Non vérifié : toujours rien sur un vrai téléphone. Les montants des paliers sont des valeurs
d'exemple à remplacer. Et le rendu hors ligne, sans les trois polices Google, n'a toujours pas été
regardé à l'œil.

## 2026-08-06 (suite 2) — Un calendrier qui ne réservait rien, et trois boutons qui faisaient la même chose

Quatre demandes en une : rendre le calendrier réel, avec les journées déjà réservées en gris ;
expliquer quand le client paie et ce que change le clic sur chaque palier, en vue de brancher
Stripe ; désencombrer la section « 03 — Sécurité & conformité » ; et compléter la page pour qu'il
n'y manque rien de logique, notamment **comment un client qui a payé obtient son interface
Streamlit**.

### Ce que l'audit a trouvé avant d'écrire quoi que ce soit

Le calendrier ne pouvait pas griser une journée réservée, et pas par oubli : la page n'a **aucun
backend**. Zéro `fetch`, zéro donnée de disponibilité, aucun appel réseau en dehors des polices. Il
proposait tous les jours ouvrés à venir, complets ou non. Sa propre note de bas de carte l'avouait
(« cette page ne réserve rien toute seule »), ce qui la rendait honnête et inutile à la fois.

Deuxième trouvaille, plus gênante commercialement : les trois boutons « Réserver » étaient trois
`<a href="#book">` identiques. Quelqu'un qui clique l'audit à 1 000 $ et quelqu'un qui clique la
construction à 18 500 $ atterrissaient au même endroit, et le palier choisi n'était transporté nulle
part. Un tableau de tarifs dont les trois boutons font la même chose invisible est exactement la
petite tromperie contre laquelle le reste de la page argumente.

Troisième : **le formulaire de contact n'arrivait nulle part.** `mailto:?subject=…` — sans
destinataire. Le client mail s'ouvrait avec un champ « À : » vide. Le seul chemin de conversion de
la page était cassé depuis le début, et rien ne lève d'exception quand on fait ça : le formulaire
« marchait », il ne livrait à personne.

### Décisions

**Calendly plutôt qu'un backend de réservation.** L'alternative était un `booking_store.py` avec deux
routes publiques sur `api.py` — cohérent avec le reste du projet, mais c'est un registre de plus à
purger, à isoler par tenant et à sauvegarder, pour un problème que Calendly résout en connaissant
déjà l'agenda. La carte garde sa coque, son titre et sa note ; seul son contenu devient le widget.

**Deux branches, pas un remplacement.** `CONFIG.calendly` renseigné ⇒ disponibilités réelles.
Vide ⇒ le sélecteur dessiné reste, inchangé. Le supprimer était tentant : le garder est ce qui
permet à ce fichier de continuer à fonctionner comme document autonome, hors ligne et à l'impression
— la raison même pour laquelle il n'a aucune dépendance. Et `<iframe>` nu, jamais `widget.js` : la
page ne charge aucun script distant, propriété qui vaut plus que le redimensionnement automatique.
Injection à l'approche, donc un visiteur qui ne descend jamais jusqu'à la section ne contacte jamais
Calendly.

**Trois paliers, trois gestes différents, et le moment du paiement écrit sur la carte.** L'audit se
paie en ligne d'avance (lien Stripe, dont la redirection après paiement pointe vers Calendly :
payer → réserver s'enchaîne sans serveur de notre côté). La construction ne se paie pas depuis un
bouton — elle se cadre, puis se devise. La maintenance démarre après la livraison et ne se vend pas
seule. Stripe n'est pas construit : un objet `CONFIG` en haut du script est le seul endroit où
coller les liens, et `POST /stripe/webhook` le jour venu se calquera sur `/slack/interactions`.

**Cinq groupes repliables plutôt que quatre.** Le plan en prévoyait quatre, ce qui obligeait à
réordonner les douze lignes. Cinq groupes **contigus** sur l'ordre existant donnent le même résultat
sans qu'aucune des douze chaînes bilingues n'ait à être retapée — donc sans risque d'en corrompre
une en silence. Panneaux **indépendants**, contrairement à la FAQ : quelqu'un qui compare deux
contrôles a une raison réelle d'en garder deux ouverts.

**Le modèle de livraison, tranché explicitement.** La FAQ affirmait « rien n'est hébergé de notre
côté, parce qu'il n'y a pas de notre côté ». Vendre une interface Streamlit rendait cette phrase à
moitié fausse. Retenu : hybride — la **démonstration** est hébergée par nous et ne contient aucune
donnée réelle, la **production** vit chez le client. Les deux réponses de FAQ concernées ont été
réécrites plutôt que laissées en contradiction, et une nouvelle section « 05 — Comment vous y
accédez » raconte les quatre étapes, essai → paiement → provisionnement → mise aux couleurs.

### Ce que le rendu a trouvé, et pas la relecture

Deux défauts de plus, tous deux invisibles en lisant le code :

- **`[hidden]` ne cachait rien.** `.chip{display:inline-block}` et `.btn{display:inline-flex}` sont
  des règles d'auteur de même spécificité que la règle navigateur `[hidden]{display:none}`, et
  l'auteur gagne. Résultat : une pastille vide affichée en permanence, et **les deux boutons de
  démonstration visibles alors qu'aucune démo n'est configurée** — précisément ce que l'attribut
  était là pour empêcher. Corrigé par une règle globale.
- **Un panneau ouvert affichait « | ».** La rotation de 90° du glyphe rend l'barre horizontale
  verticale et inversement ; c'est donc la **mauvaise** barre qui était effacée. Défaut préexistant
  de la FAQ, hérité par les nouveaux groupes, et corrigé pour les deux : ouvert affiche « − ».

### Vérifié

Rendu et piloté dans un navigateur réel, sur les **deux** branches (une copie sonde ayant permis de
capturer le `mailto:`, Chromium refusant qu'un test redéfinisse `window.location.href`).

Branche hors ligne : pastille et boutons démo réellement masqués, sélecteur présent, 18 jours
ouvrés sélectionnables, mois précédent désactivé sur le mois courant, créneau remis à zéro au
changement de date, focus clavier restitué après reconstruction de la grille, et `mailto:` portant
enfin un destinataire — avec le palier et le créneau dans le corps.

Branche Calendly : sélecteur et note hors ligne retirés du DOM, note « disponibilités réelles »
affichée, **aucune requête vers calendly.com au chargement**, iframe injecté à l'approche avec les
paramètres de thème et `utm_content=discovery`, cadre mesuré à 490×640.

Le reste : cinq groupes, un seul ouvert au chargement, les **douze** lignes toujours dans le DOM une
fois repliées (donc trouvables par Ctrl+F, par un moteur et à l'impression), deux groupes ouverts
simultanément, bascule FR/EN après ouverture d'un panneau puis vérification que l'accordéon répond
toujours (c'est là que les écouteurs meurent, `setLang` réécrivant `innerHTML`), sept bandeaux
numérotés dans le bon ordre 01→07, et aucun débordement latéral à 390, 768, 1440 et 1920 px. À
l'impression, les groupes repliés s'ouvrent réellement (hauteurs mesurées, pas supposées) et le
calendrier disparaît. Zéro erreur JavaScript sur toutes les passes.

Non vérifié, et dit franchement : **le grisage d'une journée complète n'a pas été vu**, faute de
compte Calendly — c'est Calendly qui le rend, notre part est de lui laisser la carte, et elle est
vérifiée. Aucun lien Stripe n'a été créé ni testé. Les trois montants restent des valeurs d'exemple.
Rien n'a été regardé sur un vrai téléphone.


## 2026-08-06 (fin) — Un second facteur qu'on peut espacer sans le vider

Demande : pouvoir cocher « se souvenir de cet appareil » sur l'écran TOTP et ne ressaisir le code
que tous les trois jours.

### La question à trancher avant d'écrire une ligne

Une case « se souvenir » n'a de sens que s'il existe quelque chose à reconnaître. Deux mécanismes
possibles, et l'écart de sécurité entre les deux est énorme :

1. **L'empreinte (IP, user-agent)** déjà calculée par `activity_log.device_fingerprint()`. Zéro
   travail — et une faute : cette empreinte **n'est pas un secret**. Deux personnes derrière le même
   NAT de bureau, avec le même navigateur, produisent la même valeur. Quiconque connaît le mot de
   passe depuis le même réseau sauterait le second facteur.
2. **Un vrai jeton aléatoire déposé dans le navigateur** — ce que font les vraies implémentations.
   Encore faut-il que Streamlit sache poser un cookie, or `st.context.cookies` est en lecture seule.

Plutôt que de trancher d'après la documentation, **essai** : une page jetable avec un
`components.html` écrivant `document.cookie` sur le parent, puis rechargement. Résultat sans
ambiguïté — le cookie apparaît côté navigateur *et* dans `st.context.cookies` à la passe suivante.
L'iframe `srcdoc` d'un composant hérite de l'origine de la page. Le mécanisme fort était donc
possible, et c'est celui qui est en place : 32 octets d'aléa, stockés **hachés** côté serveur.

### Ce qui est réellement affaibli, écrit noir sur blanc

Un appareil mémorisé saute **le code, et lui seul**. Le mot de passe reste exigé à chaque connexion,
et rien ici n'allonge une session (`session.py` répond à une tout autre question). On passe donc, sur
ce navigateur et pour trois jours, de « mot de passe + code » à « mot de passe + possession d'un
jeton ». C'est un facteur de moins que l'idéal, et c'est le sens même de la case : le compromis est
choisi, il n'est pas subi.

Quatre garde-fous, tous couverts par des tests :

- **Le jeton n'est jamais stocké**, seulement son SHA-256. Pas de sel, et c'est délibéré : 256 bits
  d'aléa n'ont aucune faiblesse d'entropie à compenser. Une fuite de la base ne rejoue rien.
- **Révocation automatique sans couplage.** Chaque ligne porte l'empreinte du mot de passe haché et
  du secret TOTP au moment de l'émission (`auth_state_fingerprint`, technique du `session_auth_hash`
  de Django). Changer le mot de passe invalide tout, **sans que `user_store` connaisse ce module**.
  Une révocation branchée par un appel explicite est une révocation qu'on oublie le jour où un
  troisième chemin de changement de mot de passe apparaît.
- **Expiration jugée côté serveur.** Le `max-age` du cookie est une politesse envers le navigateur,
  modifiable par qui détient le poste : il ne fait pas autorité.
- **Chaque saut est consigné** (`auth.device_trusted`, `auth.totp_skipped`, `auth.device_revoked`,
  tous dans `SENSITIVE_ACTIONS`). Sans cela, la seule chose que l'administrateur perdrait en
  accordant ce confort serait précisément sa visibilité dessus.

Un cran gratuit en plus : le cookie est lié à l'empreinte du user-agent. Rejoué depuis un autre
navigateur, il ne correspond plus et l'écran redemande simplement le code. Le faux positif est connu
et accepté — une mise à jour de navigateur coûte un code de plus, rien d'autre. `Secure` n'est ajouté
qu'en HTTPS : le poser en HTTP local ferait refuser le cookie et la case n'aurait aucun effet
visible. `HttpOnly` est hors de portée par construction (un cookie posé en JavaScript est lisible en
JavaScript) : limite assumée, écrite dans le module plutôt que passée sous silence.

### Un défaut évité parce qu'il avait déjà été rencontré le matin même

Première version : cocher la case, vérifier le code, poser le cookie, ouvrir la session. Or
`_open_session()` appelle `st.rerun()` immédiatement, ce qui interrompt le script et jette le rendu
en cours — le composant n'aurait **jamais** été exécuté par le navigateur. La case aurait été cochée,
la ligne écrite côté serveur, et le cookie n'aurait jamais existé : une panne parfaitement muette,
exactement la famille de défauts trouvée quelques heures plus tôt sur la page de vente (des règles
CSS écrites et jamais rendues). L'écriture est donc **différée** à la passe suivante, via
`flush_device_cookie()` appelée en tête de `check_auth` — le seul point traversé aussi bien par un
utilisateur authentifié que par l'écran de connexion, donc le seul qui couvre « mémoriser » et
« oublier » à la fois.

### Vérifié

22 nouveaux tests (`tests/test_device_trust.py`), ordonnés par gravité : jeton d'un compte inutile
pour un autre, expiration refusée et purgée, mot de passe changé qui révoque tout, secret TOTP
réinitialisé idem, cookie rejoué depuis un autre navigateur refusé, jeton absent de la base en clair,
cloisonnement multi-tenant. Suite complète : 773 → **795 tests**.

Surtout, la boucle complète dans un vrai navigateur, contre l'application réelle lancée sur un bac à
sable de bases neuves : **(1)** connexion, le code est demandé, la case est présente et cochable, le
cookie apparaît (43 caractères) ; **(2)** nouvelle page, nouvelle session serveur, mot de passe
seul — **le code n'est pas redemandé** et la session s'ouvre ; **(3)** après révocation côté serveur,
le code est redemandé. Le journal contient bien `auth.device_trusted` puis `auth.totp_skipped`.
L'écran « Réglages » affiche la ligne de l'appareil avec sa date d'expiration (09/08/2026, soit trois
jours) et son bouton de révocation.

Deux limites du harnais, sans conséquence produit : le contenu d'un `st.dataframe` est dessiné sur un
canvas et n'apparaît donc pas dans le texte du DOM (la présence du bouton, qui ne s'affiche que si la
liste n'est pas vide, sert de preuve) ; et la navigation entre pages a dû être déclenchée par un
événement DOM, les entrées du menu se recouvrant mutuellement. Non vérifié : rien n'a été essayé avec
plusieurs postes réels, ni en HTTPS — donc l'attribut `Secure`, qui ne s'ajoute qu'à ce moment-là,
n'a jamais été observé en conditions réelles.


## 2026-08-07 — Un cookie que le serveur ne pouvait pas lire, et un tableau de bord tout bleu

Quatre retours d'usage. Le premier est un vrai défaut de la veille ; les trois autres sont des
demandes de design.

### « Se souvenir de cet appareil » ne marchait pas à la reconnexion

Signalé après coup, et la vérification de la veille était passée à côté pour une raison précise :
elle ouvrait une **nouvelle page**, alors que « je me déconnecte puis je me reconnecte » reste dans
le **même onglet**.

Mesuré avec une page sonde plutôt que supposé : `st.context.cookies` est figé au **handshake** de la
session Streamlit. Un cookie déposé pendant la session en cours n'y apparaît jamais — ni tout de
suite, ni après plusieurs reruns (constaté : présent côté navigateur, absent côté serveur, visible
seulement après un rechargement complet). Le scénario du bug était donc structurellement
impossible : la déconnexion ne vide que la clé `session`, la session Streamlit survit, et le jeton
restait illisible.

Corrigé avec **deux sources**, chacune couvrant ce que l'autre ne peut pas : `st.session_state`
survit à la déconnexion et couvre le retour dans le même onglet ; le cookie survit à la fermeture du
navigateur et couvre le rechargement, le nouvel onglet et le redémarrage. Aucune des deux seule ne
suffit. Le jeton gardé en session ne quitte jamais le serveur.

Re-vérifié sur le chemin qui échouait : déconnexion, reconnexion dans le même onglet, **le code
n'est plus redemandé** — et le reste tient toujours (nouvel onglet, révocation qui réexige le code,
journal portant `auth.device_trusted` puis `auth.totp_skipped`).

### Le tableau de bord ne montrait qu'une couleur

Le §22 n'utilisait que `chart_colors(BRAND)[0]`, avec un motif défendable : les catégories sont
nommées sur l'axe, les colorer serait un encodage redondant. Le raisonnement vaut **à l'intérieur**
d'un graphe et a fait manquer l'échelle du dessus : six blocs mesurant six choses différentes se
dessinaient tous dans le même bleu. La couleur encode donc désormais le **bloc**, pas la catégorie.

Une tentative intermédiaire a été **écartée après mesure** : une rotation de teinte en HLS produisait
bien six couleurs distinctes, mais criardes, alors que `chart_colors()` renvoyait déjà six teintes
curatées et distinctes sur cette marque (bleu, vert, ambre, bleu clair, pétrole, rouge). Le défaut
n'était pas la palette, c'était l'index `[0]`. La fonction devenue inutile a été supprimée plutôt
que laissée « au cas où ».

Second défaut du même écran : les grandes zones vides sous plusieurs cartes. La hauteur de carte est
fixée par rangée (§22) tandis que Vega gardait sa hauteur par défaut ; les deux ne se parlaient pas.
Les graphes reçoivent maintenant une hauteur déduite de celle de leur carte.

Enfin, l'entrée des cartes est liée au **défilement** (`animation-timeline: view()`) et non plus à
une horloge au montage : sur un tableau de bord dont sept cartes sur dix sont sous la ligne de
flottaison, la moitié de la cascade était jouée pour personne. Sous `@supports` — un navigateur qui
l'ignore garde la cascade au montage, et la carte s'affiche dans tous les cas.

### L'onglet actif et le paquet de cartes

L'onglet courant portait déjà la couleur de marque (§21) mais en aplat. Un dégradé très court et un
liseré intérieur lui donnent de la matière, et un enfoncement au clic répond **avant** que Streamlit
n'ait rejoué le script — sans quoi la personne clique une seconde fois.

Sur la page de vente, les neuf cartes de « 02 — Depuis la v1 » occupaient trois écrans. Empilées,
elles en occupent une : les deux cartes du dessous restent visibles, parce que c'est cette
profondeur qui dit « il y en a d'autres ». Deux points ne se voient qu'à l'écran et ont été corrigés
après rendu : une hauteur d'estrade fixe laissait un trou sous les cartes courtes (elle est
désormais mesurée sur la plus haute, via `offsetHeight` — `getBoundingClientRect` renvoie la boîte
**mise à l'échelle** et sous-estimait de 7 %), et les cartes du dessous, blanches sur fond blanc, ne
se voyaient pas : il a fallu les teinter, pas seulement les estomper.

### Vérifié

Suite complète : **795 tests**, dont deux ont échoué sur mes propres modifications avant d'être
corrigées — l'un exige qu'aucune animation d'interface ne dépasse 300 ms (la durée est ignorée sous
une timeline de défilement, mais la laisser à 500 ms aurait été un piège pour la relecture
suivante), l'autre que la page courante porte littéralement `background: var(--aca-primary)` (le
raccourci est maintenant posé d'abord, le dégradé par-dessus). Aucun des deux n'a été affaibli.

Rendu : six couleurs de remplissage réellement peintes et relevées sur la page (`#0f4c81`,
`#107c10`, `#b4622a`, `#3e8fd0`, `#125e6b`, `#a32c1e`), graphes remplissant enfin leurs cartes,
paquet de cartes piloté au clavier et à la souris avec les cartes cachées `inert`, section passée de
trois écrans à 772 px, aller-retour FR/EN, et aucun débordement à 390, 768 et 1440 px. Le tableau de
bord a été rendu sur des **copies** des vraies bases, avec un compte `operator` créé dans la copie —
le second facteur étant réservé aux administrateurs, aucun secret réel n'a été lu.

Non vérifié : `animation-timeline: view()` n'a été observé que sur Chromium ; rien sur un téléphone.


### Les paliers, repris sur le déploiement réel (même jour)

Retour d'usage : les paliers « Démonstration / Solo / Enterprise » de l'ancienne page française
étaient préférés aux trois forfaits d'agence chiffrés. Ils l'ont remplacé, et c'est un meilleur
résultat que ce que la demande impliquait — parce qu'ils décrivent les trois formes de déploiement
que **le code prend réellement en charge** (mode démonstration, profil Solo, profil Enterprise avec
n8n), là où les précédents décrivaient des prestations inventées.

Effet de bord bienvenu : les trois montants d'exemple (1 000 / 18 500 / 3 500 $), hérités d'un
modèle de page et signalés comme provisoires depuis leur écriture, ont **disparu** au lieu d'être
devinés. Le 0 € affiché est littéral et vérifiable : la pile tient sur des paliers gratuits et les
clés d'API appartiennent au client. Ce qui se facture — installation, intégration — se devise après
un appel, ce qui est aussi la raison pour laquelle chaque bouton mène désormais à la réservation
plutôt qu'à un paiement. Le bouton du palier Démonstration est la seule exception : il mène à la
démonstration, parce qu'envoyer vers un formulaire quelqu'un qui a demandé à *essayer* est
exactement le petit détournement que cette page prétend éviter.

Un défaut visible seulement au rendu, et présent depuis longtemps : `.row` était `display:flex`, ce
qui transforme chaque enfant en ligne en **élément flex**. Une phrase contenant un `<em>` ou un
`<code>` se retrouvait donc coupée en trois morceaux séparés par 11 px — « la garde *lève* au lieu
de passer en silence » se lisait littéralement en morceaux, sur la page de vente comme dans la
capture envoyée. Seules les lignes numérotées ont besoin de l'axe flex : elles portent `.row__k`,
et c'est désormais la condition (`:has()`).

Vérifié : 795 tests inchangés, deux branches de réservation rejouées (le `mailto:` porte bien
« Palier 2 · Solo » et le créneau choisi ; l'iframe Calendly s'injecte avec `utm_content=solo`),
aller-retour FR/EN sur les trois cartes, et aucun débordement à 390 et 1440 px.

---

## 2026-08-07 — §26 : l'artwork refait en blocs, et la main devient une photographie

**Point de départ.** La section « The Human-AI Intersection » ne correspondait pas à ce qui était
attendu. Deux captures montraient le rendu voulu : une masse pixellisée, granuleuse, qui scintille
et passe du gris au bleu. Deux autres montraient les icônes de capacités et le pied de page.

### Mesurer plutôt que deviner

La page de référence a été ouverte dans un navigateur piloté et ses éléments interrogés un par un,
plutôt que jugée d'après une capture. Quatre sondes successives, chacune corrigeant l'hypothèse de
la précédente :

1. La section ne contient **qu'une seule `<img>`** (la main) et un `<svg>` de 1×50 px (le trait de
   la légende). La masse n'est donc ni l'une ni l'autre.
2. Le test de collision aux coordonnées de la masse trouve **deux `<div>` feuilles de 626×274**,
   l'une portant `filter: blur(11px)`, aucune avec `background-image`, aucune avec d'enfant. Rien
   là-dedans ne peint quoi que ce soit.
3. L'`outerHTML` du conteneur tranche : c'est du **texte**. Monospace, 10 px, `line-height: 1em`,
   `letter-spacing: 0em`, couleur `rgb(176,176,176)`, 47 lignes de 170 colonnes — et un décompte des
   glyphes qui ne renvoie que **trois caractères** : U+2593 (1137), U+2592 (969), U+2591 (334).
4. Un balayage de toute la page trouve **six instances** de la même construction, toujours par
   paires nette + floutée : la masse, les quatre icônes (bleu, magenta, vert, ambre) et la main du
   pied de page.

Autrement dit : tout le langage visuel du site tient dans une seule technique, et la seule image
qu'il télécharge est la photographie de la main.

### Ce que la ponctuation coûtait

Le moteur existant mappait la luminance sur `" .:-=+*#%@"`. C'est de l'art ASCII honnête, et c'était
le mauvais médium : la ponctuation a une forme interne, donc l'œil lit *de l'écriture qui dessine*.
Les blocs de trame remplissent tout leur cadratin ; à `line-height: 1` et `letter-spacing: 0` ils se
juxtaposent en aplat continu. Toute la différence entre l'ancien rendu et le nouveau tient là — le
tramage ordonné, la correction d'aspect et la boucle partagée à 16 im/s n'ont pas bougé, parce
qu'ils n'étaient pas le problème.

### Les mains ASCII : trois tentatives, un constat

Les passes précédentes gardent la trace de trois dessins successifs de main en ASCII, finissant tous
en ovale ou en botte. La cause est structurelle et non un défaut de dessin : à cette résolution
l'écart entre deux doigts fait **moins d'une cellule**, et le tramage les fusionne. Renoncer à
dessiner la main pour poser une photographie n'est pas un abandon — c'est reconnaître ce que le
médium sait faire (une masse diffuse, sans silhouette à perdre) et ce qu'il ne sait pas. La section
y gagne son argument : un côté est **synthétisé à chaque image**, l'autre est **une vraie
photographie**. Le contraste passe désormais par les médias, pas seulement par la légende.

### Trois défauts que seul le rendu a montrés

**(1) Trois icônes sur quatre en pavés uniformes.** Sur l'ancien canvas agrandi, un pixel de sprite
valait 5,3 pixels écran, donc un trait de 1 px dans le cylindre de base de données ou une encoche de
1 px entre deux nœuds de pipeline se voyait. À travers une grille de 27 lignes il vaut une cellule,
et le pré-flou de 0,9 px la referme : la base de données et le presse-papiers sont sortis en
rectangles arrondis vides, le pipeline en marteau. Seule l'icône « agent » a survécu, ses détails
faisant déjà 2 px. Règle désormais écrite dans le fichier : **aucun détail ni aucun écart sous
2 pixels de sprite**, et un contour plutôt qu'un aplat. Grille portée à 40 lignes, pré-flou ramené à
0,55. Le pipeline a en plus été **décalé en escalier** : trois nœuds de même hauteur reliés à cette
hauteur font une barre, quelle que soit la largeur des encoches.

**(2) Une masse trop dense.** Avec un tramage d'amplitude 0,22, tout ce qui dépasse la mi-luminosité
quantifie directement en ▓ et la forme sort en silhouette pleine — correct pour une icône, faux pour
une masse censée ressembler à des particules qui trouvent une forme. L'amplitude est devenue un
paramètre par champ (`grain`) : 0,22 pour les icônes, **0,55** pour la masse, contre une source qui
ne dépasse pas ~0,86. Même code, même forme ; la différence entre « une plaque » et « un nuage »
tient à ce seul nombre.

**(3) Un terminateur de commentaire de trop.** En réécrivant un commentaire CSS, un `*/` s'est
retrouvé au milieu du bloc, le refermant six lignes trop tôt et laissant de la prose dans la feuille
de style. Le navigateur l'a ignorée, la page s'est affichée, et le seul symptôme était une règle
« qui ne s'applique pas » — exactement la classe de panne silencieuse qui a déjà coûté cher à ce
projet (la ligne d'audit avalée de §17, le rapport mensuel disparu de §20). Une assertion a été
ajoutée à la sonde : elle vérifie les styles calculés qu'une erreur d'analyse mangerait. Anecdote
instructive : le commentaire de cette assertion contenait lui-même un terminateur littéral et a
cassé le script de la même façon, au premier essai.

### La photographie, et ce qu'elle coûte

`static/aca-hand.png`, 1040×585, **163 Ko**. Trois décisions :

- **Niveaux de gris + alpha, pas couleur.** La page la rend désaturée de toute façon. En couleur
  vraie le fichier pesait 487 Ko ; quantifié en palette il tombait à 69 Ko mais l'avant-bras bandait
  visiblement. Une rampe de luminance 8 bits **ne peut pas** bander, puisqu'elle *est* la rampe.
  Fait dans le fichier plutôt qu'avec un `filter: grayscale()`, pour que la désaturation survive là
  où les filtres CSS ne s'appliquent pas.
- **1040 px de large.** Mesuré : 91 / 124 / 163 / 207 Ko à 760 / 900 / 1040 / 1180 px. La main n'est
  jamais dessinée au-delà de ~620 px CSS.
- **La main du pied de page est précalculée**, expédiée en texte littéral dans le balisage. Relire
  une image depuis un canvas est **bloqué sous `file://`**, ce qui aurait supprimé silencieusement
  cette illustration dans exactement le cas « document autonome hors ligne » que cette page est
  faite pour supporter. Elle est statique de toute façon.

L'en-tête du fichier affirmait « aucune image distante, tout est dessiné à l'exécution ». C'est
toujours vrai pour les distantes, mais l'aveu est écrit noir sur blanc, avec la **provenance** : ce
détourage vient du modèle Framer dont la page suit la direction artistique, ce n'est pas un visuel
original. Acceptable pour un prototype ; à remplacer ou à licencier avant toute publication
commerciale. Ce n'est pas au code de trancher.

### Ce qui n'a délibérément pas été repris

Les trois portraits du site de référence. La section « témoignages » de cette page dit explicitement
qu'il n'y a pas encore de client et cite à la place trois découvertes du projet (§16, §17, §21). Y
coller des photos de mannequins transformerait une section honnête en section fabriquée.

### Vérifié

**795 tests** inchangés. Rendu mesuré à 1440 et 390 px :

- chaque champ a **une seule largeur de ligne** — une ligne irrégulière signifierait que l'espace et
  le bloc proviennent de deux polices différentes, d'où la pile `--font-block` séparée de
  `--font-mono` (Fragment Mono n'a pas les blocs de trame, ils tomberaient en repli) ;
- les deux copies se superposent au pixel près (`dx: 0, dy: 0`) — ce qui a demandé de remplacer
  `inset: 0` par `top/left/right` sur la couche floutée : `inset: 0` la contraint à la hauteur du
  conteneur, laquelle vient de la copie nette, laquelle est vide avant la première image ;
- la photographie se charge (1040×585 naturels, 643×362 affichés), aucun débordement horizontal,
  aucune erreur console.

Branches rejouées : `prefers-reduced-motion` — les **7 champs sont dessinés**, **0 animation** en
cours, la masse reste grise (« pas d'animation » ne doit jamais vouloir dire « pas d'image ») ;
bascule FR — les 7 champs survivent à la réécriture du texte ; impression — tout l'artwork masqué,
12 titres conservés.

Un défaut préexistant corrigé au passage, trouvé en regardant une section atteinte par ancre sur
mobile : aucune règle `scroll-margin-top` n'existait, donc chaque lien interne — dont « Why us? »,
qui pointe précisément sur la section refaite ici — amenait sa cible à y = 0, là où la barre
collante de 70 px recouvre le titre.

**Non vérifié, et dit comme tel :** rien n'a été ouvert sur un vrai téléphone (uniquement un
navigateur redimensionné) ; la page n'est hébergée nulle part ; et le rendu des blocs dépend de la
police retenue par la pile `--font-block`, contrôlée sur Chromium/Windows seulement.

### §26.1 — Icônes réduites, et le même fond dans l'application

Deux demandes de suite, le même jour : les icônes étaient trop grandes, et Streamlit devait
reprendre le fond de la page de présentation.

**Une icône de ce type ne se redimensionne pas par la largeur.** Le nombre de colonnes vaut
largeur ÷ (corps × chasse) et le nombre de lignes en découle, donc réduire la seule largeur divise
les lignes — et c'est très exactement ce qui avait déjà transformé trois icônes sur quatre en pavés
uniformes une heure plus tôt. En tenant le rapport à 40 px de largeur pour 1 px de corps, la grille
reste à ~73×40 quelle que soit la taille : 280 px/7 px sont devenus 200 px/5 px, vérifié après coup
(grille mesurée à 72×40 sur la page rendue), sans qu'un seul détail disparaisse.

**Le fond de l'application.** Une seconde couche `::after` sur `stAppViewContainer` répète une tuile
SVG de blocs éparpillés en `--aca-primary`, découpée par **les mêmes** rayons radiaux que les voiles
du §21 et animée par **la même** image-clé `aca-ambient`. Les deux « mêmes » sont le fond du sujet :
les pixels n'apparaissent que là où il y avait déjà de la couleur (sinon on obtient un quadrillage
plein écran, qui ne se lit plus comme une matière), et les deux couches dérivent ensemble (sinon
elles glissent l'une sur l'autre et se lisent comme deux calques).

Ce n'est pas le moteur de la page de présentation, et c'est délibéré : là-bas les blocs sont du vrai
texte régénéré par un canvas à chaque image ; ici `branding.py` n'émet que du CSS, contrainte qu'on
ne lève pas pour une décoration. Le coût est dit dans la docstring : le masque fait **fondre** les
blocs vers les bords là où un vrai tramage les **raréfie**. À 5 % d'opacité sur une tuile couverte à
27 %, l'écart est imperceptible — mais c'est une approximation, pas la même chose.

**Bayer essayé, rendu, rejeté.** Premier jet : la même matrice de Bayer que la page de présentation.
Rendu à l'écran, cela donnait un tissage régulier avec des coutures de tuile bien visibles. La cause
est structurelle : sur la page de présentation, Bayer est seuillée contre une densité qui **varie**
le long de la forme, et c'est ce dégradé qui casse la régularité de la matrice. Ici la densité est
uniforme, et Bayer seuillée à une seule valeur produit des rangées de 4, 2, 4, 0 cellules. Remplacée
par un hachage des coordonnées : même reproductibilité (aucun `random`, aucun état), aucun axe
privilégié — et l'argument qui impose l'ordonné là-bas (un tirage refait à chaque image scintille)
ne s'applique pas à une texture statique.

Deux réglages qui viennent de la mesure, pas du goût : **chaque rangée et chaque colonne compte au
moins deux cellules**, parce qu'un tirage à 30 % laisse une rangée de seize vide à peu près une fois
sur trois cents — c'est arrivé au premier essai — et qu'une rangée vide dans une tuile répétée tous
les 96 px devient une couture horizontale que l'œil finit par suivre ; et la **même** URI est posée
deux fois à 96 et 138 px avec des décalages premiers entre eux, la période combinée dépassant alors
tout écran.

**Le défaut le plus instructif, commis deux fois dans la même passe.** En rallongeant un commentaire
CSS existant, un `*/` s'est retrouvé au milieu du bloc. La première fois il a laissé six lignes de
prose dans la feuille et annulé les règles du pied de page ; la seconde, il a supprimé la dérive du
fond — sur `::before` **et** `::after`, donc y compris l'animation d'ambiance qui existait depuis le
§21. Dans les deux cas : aucune erreur, page affichée, seul symptôme une règle « qui ne s'applique
pas ». D'où un test qui ne protège pas cette fonctionnalité mais tout le fichier —
`test_les_commentaires_css_sont_tous_refermes` — et qui refuse aussi une fermeture orpheline suivie
d'une ouverture plus loin, cas qu'un simple comptage égal laisserait passer.

**Deux tests écrits faux, et ce qu'ils ont appris.** (1) « la boucle ne tourne qu'au niveau complet »
cherchait `aca-ambient` dans la feuille : le bloc `@keyframes` est émis dès que les animations ne
sont pas coupées, donc le test voyait la **définition** et concluait que la boucle tournait au niveau
« sobre ». Corrigé en cherchant `animation: aca-ambient`. (2) « la texture survit à une couleur
invalide » forçait un jeton corrompu **après** `resolve()` — or `resolve()` valide déjà chaque
couleur et retombe sur le défaut, si bien que le test échouait dans `_variables`, c'est-à-dire
ailleurs que dans ce qu'il prétendait vérifier. Visé désormais sur `_ambient_texture` directement.

**Vérifié.** 795 → **802 tests**. Dans l'application réellement lancée (bac à sable, aucune base
réelle ouverte, compte `operator` donc pas de TOTP) : la couche existe, porte ses deux calques aux
bonnes échelles, est masquée, et `::before` comme `::after` portent `aca-ambient 48s`. La peinture
est mesurée par **différence d'images** — capture, couche désactivée, seconde capture — parce
qu'échantillonner la capture seule ne mesure que l'interface : 18,7 % des pixels échantillonnés
changent, répartis en deux amas correspondant aux deux voiles. Un premier essai annonçait un écart
maximal de 86/255 pour une couche à 5 % d'opacité, ce qui est arithmétiquement impossible : les
animations n'étaient pas figées entre les deux captures et la mesure suivait le voile en train de
dériver. Les quelques écarts résiduels élevés se situent sur les bords de glyphes — un artefact de
rastérisation dû à la suppression d'une couche de composition, pas la texture.

**Non vérifié :** rien sur un vrai téléphone ; le mode sombre et les 18 préréglages ne sont vérifiés
que par calcul ; `mask-image` reste un détail d'implémentation navigateur, contrôlé sur
Chromium/Windows uniquement.

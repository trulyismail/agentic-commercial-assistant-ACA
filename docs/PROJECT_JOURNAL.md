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

# Dossier de Stage : Assistant Commercial Agentique (ACA)
## Automatisation de la qualification de leads via LangGraph et Google Sheets
**Durée du projet : 2 mois (8 semaines)**

Ce document présente le périmètre technique et le planning de réalisation pour le développement d'un assistant IA interne. L'objectif est de livrer un prototype fonctionnel et sécurisé en 8 semaines.

---

## 1. Contexte et Objectif du Stage

### Le Besoin
Le traitement manuel des demandes entrantes (e-mails, demandes de devis, cahiers des charges) prend un temps précieux aux équipes commerciales. Le risque de rater une information ou de tarder à répondre est élevé.

### L'Objectif
Créer un outil interne capable de pré-lire les e-mails et les pièces jointes, d'en extraire les informations clés, et de préparer le travail du commercial. Le système ne remplace pas l'humain : il pré-mâche le travail et s'arrête systématiquement pour demander une validation avant toute mise à jour des fichiers clients.

---

## 2. Le Périmètre Technique (Approche Lean)

Pour garantir la livraison d'un produit fonctionnel en 2 mois, l'architecture repose sur des outils simples, robustes et peu coûteux.

### Les 3 Fonctionnalités Clés
1. **Extraction de données (E-mail + PDF) :** L'IA lit le message et le document joint pour identifier l'entreprise, le contact et l'urgence.
2. **Contrôle Humain (Human-in-the-loop) :** L'agent prépare une fiche prospect, mais le système se met en pause. Un collaborateur doit cliquer sur "Valider" sur une interface simple pour que la donnée soit enregistrée.
3. **Recherche de Connaissances (FAQ Dynamique) :** Si le client pose une question classique (ex: prix, délais), l'agent va chercher la réponse dans un onglet Google Sheets dédié pour préparer un brouillon de réponse.

### La Stack Technique
* **Cerveau & Orchestration :** `LangGraph` (pour séquencer les étapes) et `GPT-4o-mini` (pour lire le texte à très bas coût).
* **Interface Utilisateur :** `Streamlit` (pour créer la page web de validation de l'outil en quelques lignes de code).
* **Base de données (CRM) :** `Google Sheets API`. Simple à configurer, gratuit, et modifiable facilement par n'importe quel commercial de l'entreprise.

---

## 3. Architecture du Flux (Workflow)

Le programme suit un chemin strict et déterministe en 4 étapes :
1. **Réception :** Le script détecte un nouvel e-mail entrant.
2. **Analyse :** Le texte et les pièces jointes sont envoyés au LLM avec des consignes strictes pour extraire les données au format JSON.
3. **Pause & Validation :** L'interface affiche le résultat. Le système attend.
4. **Action (post-validation) :** Ajout d'une nouvelle ligne dans le Google Sheets (Onglet "Leads") et marquage de l'e-mail comme traité.

---

## 4. Planning de Réalisation (8 semaines)

Ce planning inclut des marges de sécurité pour la gestion des bugs et les tests utilisateurs.

* **Semaine 1 : Preuve de Concept (POC) Base**
  * Configuration de l'environnement Python.
  * Création d'un mini-graphe LangGraph capable de classer un faux e-mail codé en dur.
* **Semaine 2-3 : Connexion aux Données**
  * Intégration de l'API Google Sheets.
  * L'agent est capable d'écrire une ligne dans le fichier après avoir analysé un texte.
* **Semaine 4-5 : Ajout des Pièces Jointes & Interface**
  * Mise à jour du prompt pour analyser un PDF fourni.
  * Création de l'interface Streamlit avec le bouton "Valider ce prospect".
* **Semaine 6 : La Base de Connaissances (FAQ Sheets)**
  * *Optionnel selon l'avancement* : Permettre à l'agent de lire un 2ème onglet Google Sheets pour trouver des réponses aux questions du prospect.
* **Semaine 7-8 : Tests, Déploiement et Documentation**
  * Tests avec l'API Gmail (vrais e-mails).
  * Correction des bugs, nettoyage du code et remise du rapport de stage.

---
*Bénéfice attendu pour l'entreprise : À l'issue des 2 mois, l'équipe disposera d'un prototype d'automatisation transparent, maîtrisable, sans coûts d'infrastructure lourde.*
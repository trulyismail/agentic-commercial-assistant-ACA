# Politique de confidentialité — Assistant Commercial Agentique (ACA)

*Dernière mise à jour : 2026-07-21 · §14 (audit de sécurité), item US-42*

> ⚠️ **Champs à compléter avant publication réelle** : les zones marquées
> **[À COMPLÉTER : ...]** dépendent de l'entreprise qui déploie ACA (raison sociale, contact,
> adresse) — ce document ne peut pas les deviner à la place d'une décision humaine/juridique
> réelle. Le reste (données collectées, sous-traitants, durées de conservation) reflète fidèlement
> le fonctionnement technique actuel du système, vérifié contre le code (`CLAUDE.md`,
> `docs/ACAM_roadmap.md`, `aca/core/retention.py`).

## 1. Qui est responsable du traitement ?

**[À COMPLÉTER : raison sociale de l'entreprise utilisatrice d'ACA]**, ci-après « nous »,
utilise l'Assistant Commercial Agentique (ACA) pour pré-qualifier les e-mails commerciaux entrants
adressés à son équipe commerciale.

Contact pour toute question relative à cette politique ou à l'exercice de vos droits :
**[À COMPLÉTER : adresse e-mail ou postale du responsable de traitement / DPO]**.

## 2. Quelles données sont collectées ?

ACA traite les données contenues dans les e-mails que vous nous envoyez, ainsi que les documents
que vous y joignez :

| Donnée | Exemple | D'où elle vient |
|---|---|---|
| Adresse e-mail de l'expéditeur | `contact@entreprise.fr` | En-tête de l'e-mail reçu |
| Contenu de l'e-mail (objet + corps) | Votre demande de devis/démo/support | Corps de l'e-mail |
| Pièces jointes | PDF, Word, Excel (cahier des charges, devis...) | Pièces jointes de l'e-mail |
| Informations extraites | Entreprise, nom du contact, urgence, besoin exprimé | Analyse automatique du contenu ci-dessus par un modèle de langage (IA) |
| Historique commercial | Échanges précédents avec la même adresse e-mail | Notre propre CRM (onglet « Leads ») |

Nous ne collectons **aucune donnée par un autre moyen** (pas de cookie de suivi, pas de tracking
web, pas d'achat de fichiers tiers) : tout provient exclusivement de l'e-mail que vous nous
envoyez.

## 3. Pourquoi traitons-nous ces données ? (finalité et base légale)

- **Finalité** : qualifier votre demande commerciale (devis, démonstration, support) et préparer
  une réponse personnalisée.
- **Base légale** : l'exécution de mesures précontractuelles à votre demande (vous nous avez
  contactés) et/ou notre intérêt légitime à traiter efficacement les demandes commerciales reçues
  (art. 6.1.b et 6.1.f du RGPD).
- **Aucune décision entièrement automatisée** : un être humain relit et valide systématiquement
  toute proposition avant qu'elle ne soit enregistrée dans notre CRM ou qu'un brouillon de réponse
  ne soit préparé (art. 22 du RGPD — le système « rédige et attend », il n'agit jamais seul).

## 4. Qui a accès à ces données ? (sous-traitants)

Pour fonctionner, ACA fait appel aux prestataires suivants, chacun agissant comme sous-traitant
au sens du RGPD (ou n'étant sollicité que si l'option correspondante est activée) :

| Prestataire | Rôle | Données transmises |
|---|---|---|
| **Groq** | Modèles de langage (classification, extraction, rédaction) | Contenu de l'e-mail et des pièces jointes |
| **Google (Gemini)** | Recherche sémantique dans notre base de connaissances | Le besoin exprimé (texte court, pas l'e-mail entier) |
| **Google Sheets** | CRM et base de connaissances internes | Lead qualifié (entreprise, contact, urgence, besoin) |
| **HubSpot** | CRM (en parallèle de Google Sheets) | Lead qualifié, même périmètre que ci-dessus |
| **Tavily** *(optionnel)* | Enrichissement du profil entreprise, recherche web de repli | Nom de domaine de l'expéditeur, questions sans réponse en base |
| **Supabase** *(optionnel)* | Stockage technique (mémoire de session, recherche sémantique) | Aucune donnée personnelle directe — contenu de la base de connaissances interne uniquement |
| **Slack** *(optionnel)* | Notification interne qu'une analyse attend validation | Résumé de l'alerte (catégorie, expéditeur) |
| **Gmail (Google Workspace)** | Réception des e-mails, création de brouillons de réponse | L'e-mail lui-même |

Nous ne vendons ni ne louons vos données à aucun tiers. Aucun de ces prestataires n'est autorisé
à réutiliser vos données à ses propres fins.

## 5. Combien de temps conservons-nous vos données ?

Vos données sont conservées **365 jours** par défaut à compter de leur réception
(`RETENTION_DAYS`, configurable), après quoi elles sont automatiquement purgées :
- de notre CRM (onglet « Leads ») ;
- de la mémoire technique du système (l'historique de conversation lié à votre e-mail) ;
- des files d'attente de traitement.

Les profils d'entreprise mis en cache (nom de domaine → informations publiques) et notre base de
connaissances interne (FAQ) ne contiennent pas de données personnelles et ne sont pas concernés par
cette purge.

## 6. Vos droits

Conformément au RGPD, vous disposez des droits suivants sur vos données :
- **Droit d'accès** : savoir quelles données nous détenons sur vous ;
- **Droit de rectification** : corriger une donnée inexacte ;
- **Droit à l'effacement** : demander la suppression de vos données avant l'échéance normale ;
- **Droit à la portabilité** : recevoir vos données dans un format structuré ;
- **Droit d'opposition** : vous opposer au traitement pour des motifs légitimes.

Pour exercer l'un de ces droits, contactez **[À COMPLÉTER : adresse e-mail du responsable de
traitement / DPO]**. Vous pouvez également introduire une réclamation auprès de l'autorité de
protection des données compétente **[À COMPLÉTER : ex. INPDP en Tunisie / CNIL en France, selon la
juridiction applicable]**.

## 7. Sécurité

- Toute écriture dans notre CRM est précédée d'une validation humaine explicite — le système ne
  peut jamais enregistrer ou envoyer quoi que ce soit de façon autonome.
- Les accès à l'outil interne sont protégés par un mot de passe avec verrouillage progressif après
  plusieurs échecs.
- Chaque validation humaine est journalisée (qui, quoi, quand) à des fins de traçabilité interne.
- Les secrets techniques (clés d'API) ne sont jamais stockés dans le code ni affichés à l'écran.

## 8. Modifications de cette politique

Cette politique peut être mise à jour pour refléter une évolution du système ou de la
réglementation. La date de dernière mise à jour figure en haut de ce document.

# -*- coding: utf-8 -*-
"""
Seed script (one-off) : réécrit intégralement l'onglet FAQ (Knowledge_Base) avec un jeu de
données réaliste de 74 paires question/réponse couvrant 10 catégories métier (tarifs,
fonctionnalités, sécurité/RGPD, support/SLA, intégrations, mise en route, démo, comptes,
contrat, plateforme technique) — remplace l'ancien jeu de 2 lignes, trop petit pour permettre un
calibrage significatif du seuil de similarité du RAG sémantique (voir
docs/PROJECT_JOURNAL.md, entrée du 2026-07-11, et le seuil calibré dans
aca/integrations/sheets.py / aca/integrations/vector_store.py).

Écrit directement via gspread (pas d'import `aca.*`) : ce script vit hors du package `aca/` et se
lance en exécution directe (`python scripts/setup_faq.py`), qui ne place pas la racine du dépôt
sur `sys.path` — contrairement à `python -m aca.<sous-module>` pour les scripts internes au
package (voir docs/PROJECT_JOURNAL.md, entrée du 2026-07-11, réorganisation en dossiers).

Lancement : `python scripts/setup_faq.py`
"""
import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

FAQ_PAIRS = [
    # --- Tarifs & Facturation ---
    ("Quels sont vos tarifs professionnels ?", "Nos licences pro démarrent à 50€/mois/utilisateur, avec des paliers dégressifs à partir de 20 utilisateurs."),
    ("Proposez-vous une remise pour les associations ou le secteur public ?", "Oui, une remise de 30% est appliquée sur présentation d'un justificatif (association loi 1901 ou organisme public)."),
    ("Quel est le délai normal de livraison ?", "Nous livrons en 48h jours ouvrés."),
    ("Puis-je payer annuellement plutôt que mensuellement ?", "Oui, le paiement annuel donne droit à 2 mois offerts par rapport au tarif mensuel."),
    ("Quels moyens de paiement acceptez-vous ?", "Carte bancaire, virement SEPA et prélèvement automatique pour les contrats annuels."),
    ("Y a-t-il des frais de mise en service ?", "Aucun frais de mise en service pour les offres Standard et Pro ; l'offre Entreprise inclut un accompagnement dédié facturé séparément."),
    ("Comment fonctionne la facturation à l'usage ?", "Au-delà du quota inclus dans votre forfait, chaque unité supplémentaire (e-mail traité, appel API) est facturée au tarif dégressif indiqué dans votre espace client."),
    ("Puis-je obtenir une facture avec TVA intracommunautaire ?", "Oui, indiquez votre numéro de TVA intracommunautaire lors de la souscription pour une facturation hors taxes automatique."),

    # --- Fonctionnalités produit ---
    ("Quelles sont les principales fonctionnalités de la plateforme ?", "Qualification automatique des leads entrants, extraction de données depuis les e-mails et pièces jointes, et validation humaine avant toute action sur le CRM."),
    ("La plateforme peut-elle traiter des pièces jointes PDF, Word et Excel ?", "Oui, la plateforme extrait et analyse automatiquement le contenu des pièces jointes PDF, Word (.docx) et Excel (.xlsx)."),
    ("Est-il possible de personnaliser les catégories de classification des e-mails ?", "Les catégories standard (demande de démo, devis, support, autre, spam) sont configurables sur l'offre Entreprise."),
    ("La plateforme génère-t-elle automatiquement des réponses aux prospects ?", "Elle prépare un brouillon de réponse personnalisé que le commercial relit et envoie manuellement — aucun envoi n'est automatique."),
    ("Peut-on suivre les relances automatiques envoyées aux prospects ?", "Oui, chaque relance automatique est tracée et visible dans le tableau de bord, avec la date d'envoi et le statut de réponse."),
    ("Existe-t-il un tableau de bord de suivi de l'activité ?", "Oui, un tableau de bord affiche le volume de leads par catégorie, le taux de conversion et les temps de réponse moyens."),
    ("La plateforme détecte-t-elle les clients existants qui recontactent l'entreprise ?", "Oui, chaque nouvel e-mail est automatiquement rapproché de l'historique CRM pour signaler les clients déjà connus."),
    ("Peut-on enrichir automatiquement le profil d'une entreprise prospecte ?", "Oui, un module de recherche web enrichit automatiquement le profil de l'entreprise à partir de son nom de domaine."),

    # --- Sécurité & Conformité ---
    ("Où sont hébergées nos données ?", "Toutes les données sont hébergées sur des serveurs situés dans l'Union européenne, conformément au RGPD."),
    ("La plateforme est-elle conforme au RGPD ?", "Oui, une politique de rétention des données personnelles est appliquée automatiquement, avec purge des données obsolètes."),
    ("Combien de temps mes données sont-elles conservées ?", "Les données de prospects sont conservées 365 jours par défaut ; ce délai est configurable selon votre politique interne."),
    ("Proposez-vous un accord de traitement des données (DPA) ?", "Oui, un accord de traitement des données conforme au RGPD est fourni sur simple demande à la souscription."),
    ("Les échanges avec la plateforme sont-ils chiffrés ?", "Oui, toutes les communications transitent en HTTPS/TLS et les données sensibles sont chiffrées au repos."),
    ("Qui a accès à mes données au sein de votre entreprise ?", "Seules les équipes support et infrastructure, sous accord de confidentialité, peuvent accéder aux données en cas d'incident déclaré."),
    ("Disposez-vous d'une certification de sécurité (ISO 27001, SOC 2) ?", "L'offre Entreprise inclut un accompagnement vers la certification ISO 27001 ; contactez notre équipe pour le détail des contrôles disponibles."),
    ("Puis-je demander la suppression définitive de mes données ?", "Oui, une demande de suppression peut être adressée à tout moment ; elle est traitée sous 30 jours conformément au RGPD."),

    # --- Support & SLA ---
    ("Quels sont vos horaires de support technique ?", "Le support est disponible du lundi au vendredi, de 9h à 18h, hors jours fériés."),
    ("Quel est le délai de réponse garanti en cas d'incident ?", "Le SLA standard garantit une première réponse sous 4h ouvrées ; l'offre Entreprise réduit ce délai à 1h."),
    ("Comment contacter le support en cas de problème ?", "Par e-mail à l'adresse support dédiée ou directement depuis le chat intégré à la plateforme."),
    ("Proposez-vous un support téléphonique ?", "Le support téléphonique est inclus dans l'offre Entreprise ; les offres Standard et Pro passent par e-mail et chat."),
    ("Quelle est la disponibilité garantie de la plateforme (uptime) ?", "Nous garantissons une disponibilité de 99,5% par mois, avec compensation contractuelle en cas de dépassement."),
    ("Où puis-je suivre l'état de service en temps réel ?", "Une page de statut public affiche la disponibilité en temps réel et l'historique des incidents des 90 derniers jours."),
    ("Proposez-vous une formation pour les nouvelles équipes ?", "Oui, une session de formation initiale d'1h30 est incluse pour toute nouvelle équipe sur les offres Pro et Entreprise."),
    ("Comment signaler un bug ou suggérer une amélioration ?", "Un formulaire dédié est disponible depuis la plateforme ; chaque retour est examiné par l'équipe produit sous 5 jours ouvrés."),

    # --- Intégrations & API ---
    ("La plateforme s'intègre-t-elle avec Gmail ?", "Oui, une intégration native avec Gmail permet la lecture automatique des e-mails non lus et la création de brouillons de réponse."),
    ("Proposez-vous une intégration avec Slack ?", "Oui, des notifications peuvent être envoyées sur un canal Slack dédié via un webhook entrant."),
    ("Existe-t-il une API publique pour connecter nos outils internes ?", "Oui, une API REST documentée permet d'interroger et d'alimenter les données de leads depuis vos outils internes."),
    ("La plateforme peut-elle se connecter à un vrai CRM (HubSpot, Pipedrive, Salesforce) ?", "Une intégration CRM tierce est en cours de développement ; en attendant, Google Sheets sert de CRM léger intégré."),
    ("Peut-on synchroniser les leads avec Google Sheets ?", "Oui, chaque lead validé est automatiquement ajouté à un onglet Google Sheets dédié, consultable et modifiable par toute l'équipe."),
    ("Y a-t-il des webhooks disponibles pour être notifié en temps réel ?", "Oui, des webhooks sortants peuvent être configurés pour notifier vos systèmes lors de chaque nouvelle qualification de lead."),
    ("Peut-on connecter un calendrier de prise de rendez-vous type Calendly ?", "Oui, un lien Calendly peut être configuré pour être automatiquement inséré dans les réponses aux demandes de démonstration."),
    ("La plateforme fonctionne-t-elle avec Outlook en plus de Gmail ?", "Le support d'Outlook est prévu sur la feuille de route ; seule l'intégration Gmail est disponible actuellement."),

    # --- Mise en route / Onboarding ---
    ("Combien de temps faut-il pour mettre en place la plateforme ?", "La mise en place initiale prend généralement moins d'une demi-journée pour une petite équipe commerciale."),
    ("Faut-il des compétences techniques pour configurer la plateforme ?", "Non, la configuration de base se fait via une interface simple ; l'intégration Gmail nécessite une autorisation en quelques clics."),
    ("Comment importer notre base de connaissances existante (FAQ, documentation) ?", "Vous pouvez importer un document PDF, Word ou Markdown ; il est automatiquement converti en questions-réponses exploitables par l'assistant."),
    ("Peut-on migrer nos leads existants depuis un autre outil ?", "Oui, un import CSV ou Google Sheets est possible en début de contrat, avec accompagnement de notre équipe."),
    ("Un accompagnement personnalisé est-il proposé au démarrage ?", "Oui, l'offre Entreprise inclut un accompagnement dédié de 2 semaines avec un chargé de compte."),
    ("Comment autoriser l'accès à notre boîte Gmail professionnelle ?", "Un simple flux d'autorisation OAuth est proposé lors de la première connexion ; aucun mot de passe n'est stocké."),
    ("Peut-on tester la plateforme avec une boîte e-mail de test avant le déploiement réel ?", "Oui, nous recommandons de commencer avec une boîte e-mail de test pour valider le paramétrage avant la mise en production."),

    # --- Démo & Essai gratuit ---
    ("Proposez-vous une démonstration gratuite ?", "Oui, une démonstration personnalisée de 30 minutes peut être réservée directement en ligne."),
    ("Existe-t-il une période d'essai gratuite ?", "Oui, un essai gratuit de 14 jours est disponible sans engagement ni carte bancaire requise."),
    ("Que se passe-t-il à la fin de la période d'essai ?", "Vous pouvez choisir de souscrire à une offre payante ou l'essai s'arrête automatiquement, sans facturation surprise."),
    ("La démonstration est-elle personnalisée selon notre secteur d'activité ?", "Oui, notre équipe commerciale adapte la démonstration à votre secteur et à vos cas d'usage spécifiques."),
    ("Puis-je inviter plusieurs collègues à la démonstration ?", "Oui, vous pouvez inviter autant de participants que nécessaire lors de la réservation du créneau."),
    ("Comment réserver un créneau de démonstration ?", "Un lien de réservation est envoyé automatiquement par e-mail après votre demande initiale."),

    # --- Compte, utilisateurs & permissions ---
    ("Combien d'utilisateurs peuvent avoir accès à la plateforme ?", "Le nombre d'utilisateurs dépend de votre forfait ; il est illimité sur l'offre Entreprise."),
    ("Peut-on définir des rôles et permissions différents par utilisateur ?", "Oui, trois niveaux de permission sont disponibles : lecture seule, validation de leads, et administration complète."),
    ("Comment ajouter ou supprimer un utilisateur ?", "Depuis les paramètres de l'espace client, un administrateur peut ajouter ou retirer un utilisateur en quelques clics."),
    ("Peut-on activer une authentification à deux facteurs ?", "Oui, l'authentification à deux facteurs est disponible et recommandée pour tous les comptes administrateurs."),
    ("Est-il possible de restreindre l'accès par mot de passe à l'interface ?", "Oui, un mot de passe global optionnel peut être configuré pour protéger l'accès à toute l'interface."),
    ("Comment consulter l'historique des validations effectuées par mon équipe ?", "Un journal d'audit trace chaque validation avec la date, l'utilisateur et la catégorie du lead concerné."),
    ("Peut-on avoir plusieurs boîtes e-mail connectées simultanément ?", "La gestion multi-boîtes n'est pas encore disponible ; chaque déploiement est actuellement rattaché à une seule boîte e-mail."),

    # --- Contrat & résiliation ---
    ("Quelle est la durée d'engagement minimale ?", "Aucun engagement minimal sur l'offre mensuelle ; l'offre annuelle est engageante pour 12 mois avec tarif préférentiel."),
    ("Comment résilier mon abonnement ?", "La résiliation se fait directement depuis l'espace client, avec un préavis d'un mois pour les offres mensuelles."),
    ("Puis-je changer d'offre en cours de contrat ?", "Oui, vous pouvez passer à une offre supérieure à tout moment ; le changement d'offre inférieure prend effet à la prochaine échéance."),
    ("Que deviennent nos données si nous résilions notre abonnement ?", "Vos données restent accessibles en export pendant 30 jours après résiliation, puis sont définitivement supprimées."),
    ("Proposez-vous un contrat cadre pour les grands comptes ?", "Oui, un contrat cadre avec conditions négociées est disponible à partir de 50 utilisateurs, contactez notre équipe commerciale."),
    ("Y a-t-il des pénalités en cas de résiliation anticipée d'un contrat annuel ?", "La résiliation anticipée d'un contrat annuel entraîne la facturation du solde restant dû, sauf accord contraire négocié."),
    ("Puis-je suspendre temporairement mon abonnement ?", "Une suspension temporaire de 3 mois maximum peut être accordée sur demande auprès de votre chargé de compte."),

    # --- Plateforme technique / compatibilité ---
    ("Sur quels navigateurs la plateforme fonctionne-t-elle ?", "La plateforme est compatible avec Chrome, Firefox, Edge et Safari, dans leurs versions récentes."),
    ("Existe-t-il une application mobile ?", "Il n'existe pas d'application mobile dédiée actuellement ; l'interface web est toutefois responsive et utilisable sur tablette."),
    ("Que faire si je rencontre un problème de connexion à la plateforme ?", "Vérifiez d'abord votre connexion internet, puis contactez le support technique si le problème persiste au-delà de quelques minutes."),
    ("La plateforme peut-elle fonctionner hors ligne ?", "Non, une connexion internet est requise pour accéder à la plateforme, qui fonctionne entièrement dans le cloud."),
    ("Quelle est la fréquence de mise à jour de la plateforme ?", "De nouvelles fonctionnalités sont déployées en continu, sans interruption de service ni action requise de votre part."),
    ("Les mises à jour peuvent-elles casser nos intégrations existantes ?", "Nous garantissons la rétrocompatibilité de notre API pendant au moins 12 mois après toute modification majeure."),
    ("Comment savoir si un incident technique est en cours ?", "La page de statut public et les notifications e-mail automatiques vous informent en temps réel de tout incident en cours."),
]


def force_populate_faq():
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")

    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    faq_worksheet = spreadsheet.worksheet("FAQ")
    print(f"Réécriture de l'onglet FAQ avec {len(FAQ_PAIRS)} paires...")

    rows = [["Question", "Réponse", "Statut"]] + [[q, r, "validé"] for q, r in FAQ_PAIRS]
    faq_worksheet.clear()
    faq_worksheet.update(range_name=f"A1:C{len(rows)}", values=rows)

    faq_worksheet.format("A1:C1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9},
    })
    print(f"✅ {len(FAQ_PAIRS)} paires écrites avec succès dans l'onglet FAQ !")


if __name__ == "__main__":
    force_populate_faq()

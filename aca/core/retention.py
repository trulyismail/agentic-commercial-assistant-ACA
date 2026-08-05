"""
Politique de rétention RGPD (P1 §11.4 item 13) : un e-mail contient des données personnelles
(expéditeur, contenu du message) — ce script purge ce qui dépasse une période de rétention
configurable (`RETENTION_DAYS`, défaut 365 jours), aux 3 endroits où cette donnée vit :

1. Onglet « Leads » (Google Sheets) — lignes plus anciennes que la rétention.
2. `checkpoints.sqlite` — threads LangGraph correspondants (`delete_thread`), qui contiennent le
   corps brut de l'e-mail dans l'état du graphe même après le passage en CRM.
3. `queue.sqlite` — entrées « validé » (déjà traitées) plus anciennes que la rétention.

N'affecte JAMAIS l'onglet `Enrichissement_Cache` (profils d'ENTREPRISE, pas de données à caractère
personnel) ni la `FAQ` (base de connaissances interne, pas liée à un individu).

Lancement : `python retention.py` — à planifier périodiquement (ex. une fois par semaine via un
planificateur de tâches), pas de process continu nécessaire ici.
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aca.storage import activity_log, followup_store, queue_store, review_store, task_store
from aca.integrations import sheets
from .app import checkpointer

load_dotenv()

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "365"))

# §17 — le journal d'activité conserve des adresses IP, donc des données à caractère personnel : il
# lui faut sa propre échéance. Réglable séparément parce que les deux durées répondent à des
# logiques opposées : un lead se garde pour la relation commerciale, une trace d'accès se garde
# pour pouvoir enquêter après coup. Le défaut aligné sur `RETENTION_DAYS` évite d'introduire une
# surprise ; une valeur plus courte (90 jours) reste un choix parfaitement défendable côté client.
ACTIVITY_RETENTION_DAYS = int(os.getenv("ACTIVITY_RETENTION_DAYS", str(RETENTION_DAYS)))

# §18 — rétention à deux vitesses (docs/AMELIORATIONS_SUGGEREES.md §4 item 4) : un échec de
# connexion ou un changement de rôle vieux de plusieurs mois est précisément ce qu'une enquête
# viendrait chercher après coup — le purger à la même échéance que le bruit d'usage courant
# (validations, ouvertures de file) reviendrait à détruire la preuve avant qu'elle ne serve. Défaut
# à deux fois `ACTIVITY_RETENTION_DAYS` : plus long sans être indéfini, sur le même principe que la
# distinction déjà faite entre `RETENTION_DAYS` et `ACTIVITY_RETENTION_DAYS`.
ACTIVITY_SENSITIVE_RETENTION_DAYS = int(
    os.getenv("ACTIVITY_SENSITIVE_RETENTION_DAYS", str(ACTIVITY_RETENTION_DAYS * 2))
)


def purge_old_leads(days: int = RETENTION_DAYS) -> int:
    """Supprime les lignes de l'onglet Leads dont la colonne Date dépasse `days` jours. Renvoie le nombre supprimé."""
    ws = sheets.get_sheet()
    cutoff = datetime.now() - timedelta(days=days)
    values = ws.get_all_values()
    if len(values) <= 1:
        return 0

    to_delete = []
    for row_index, row in enumerate(values[1:], start=2):  # ligne 1 = en-tête
        try:
            row_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        except (IndexError, ValueError):
            continue
        if row_date < cutoff:
            to_delete.append(row_index)

    for row_index in reversed(to_delete):  # du bas vers le haut : ne décale pas les index restants
        ws.delete_rows(row_index)
    return len(to_delete)


def purge_old_checkpoints(days: int = RETENTION_DAYS) -> int:
    """Supprime les threads LangGraph des leads validés depuis plus de `days` jours."""
    count = 0
    for thread_id in queue_store.list_validated_older_than(days):
        try:
            checkpointer.delete_thread(thread_id)
            count += 1
        except Exception as e:
            print(f"⚠️ Échec de la suppression du thread {thread_id} : {e}")
    return count


def purge_old_queue_entries(days: int = RETENTION_DAYS) -> int:
    """Supprime les entrées « validé » de queue.sqlite plus anciennes que `days` jours."""
    return queue_store.purge_validated_older_than(days)


def purge_leads_by_sender(sender: str) -> int:
    """Supprime de l'onglet Leads toutes les lignes dont la colonne « Expéditeur » vaut `sender`."""
    ws = sheets.get_sheet()
    values = ws.get_all_values()
    if len(values) <= 1:
        return 0
    needle = (sender or "").strip().lower()
    to_delete = [
        row_index for row_index, row in enumerate(values[1:], start=2)  # ligne 1 = en-tête
        if len(row) > 1 and row[1].strip().lower() == needle
    ]
    for row_index in reversed(to_delete):  # du bas vers le haut : ne décale pas les index restants
        ws.delete_rows(row_index)
    return len(to_delete)


def purge_subject(sender: str) -> dict:
    """
    Droit à l'effacement RGPD (article 17) — supprime TOUTES les données d'une personne, sans
    attendre l'expiration de la rétention (§15.2.4).

    C'était le manque : `retention.py` n'implémentait que l'effacement *par ancienneté*, la partie
    facile parce qu'automatisable. Une personne qui écrit « supprimez mes données » a un droit
    immédiat, indépendant de tout délai — et sans cette fonction, y répondre demandait de retrouver
    à la main des lignes dans un Google Sheet, des threads dans un fichier de checkpoints et deux
    registres SQLite. En pratique, cela ne se faisait pas.

    Portée : onglet Leads, checkpoints LangGraph (qui contiennent le corps brut de l'e-mail), file
    d'attente, suivi de relance.

    **Volontairement hors périmètre : le journal d'audit** (`audit_log.py`). Il enregistre quelle
    personne de l'entreprise a pris quelle décision, et sert de preuve de la validation humaine —
    un intérêt légitime au sens de l'article 17.3(e) (constatation/exercice de droits en justice).
    Le supprimer romprait de surcroît la chaîne d'empreintes (§15.2.7), ce qui ressemblerait à une
    falsification. La décision est donc consciente et documentée dans
    `docs/PRIVACY_POLICY.md`, pas un oubli. Si une analyse juridique conclut l'inverse pour un
    client donné, le geste correct est de purger la ligne PUIS de réinitialiser la chaîne, jamais
    de l'altérer en silence.

    Renvoie le décompte par emplacement, pour pouvoir répondre précisément à la personne.
    """
    threads = queue_store.list_threads_by_sender(sender)
    checkpoints_deleted = 0
    for thread_id in threads:
        try:
            checkpointer.delete_thread(thread_id)
            checkpoints_deleted += 1
        except Exception as e:
            print(f"⚠️ Échec de la suppression du thread {thread_id} : {e}")

    # §19 — annuler les tâches datées de chaque fil concerné. Un envoi programmé qui survivrait à
    # un effacement RGPD expédierait un e-mail commercial à quelqu'un qui vient précisément de
    # demander qu'on efface ses données : le pire moment possible pour que la machine parle encore.
    tasks_cancelled = 0
    reviews_cancelled = 0
    for thread_id in threads:
        tasks_cancelled += task_store.cancel_for_thread(
            thread_id, "Effacement RGPD (article 17).",
        )
        # §20 — même raisonnement pour les demandes de relecture : elles recopient l'objet et
        # l'adresse de l'expéditeur (exprès, pour rester lisibles après une purge). Les laisser en
        # attente conserverait le nom de la personne précisément à l'endroit où on vient de
        # l'effacer, et afficherait sa demande à un administrateur le lendemain.
        reviews_cancelled += review_store.cancel_for_thread(thread_id)

    result = {
        "leads": purge_leads_by_sender(sender),
        "checkpoints": checkpoints_deleted,
        "queue": queue_store.purge_sender(sender),
        "followup": followup_store.purge_sender(sender),
        "tasks": tasks_cancelled,
        "relectures": reviews_cancelled,
    }
    # §18 — un effacement RGPD explicite (article 17) mérite sa propre trace, distincte de la purge
    # d'ancienneté périodique de `run()` : c'est un événement rare et sensible qu'un administrateur
    # doit pouvoir retrouver, avec le compte par emplacement pour répondre précisément à la personne.
    activity_log.log(
        activity_log.ACTION_DATA_PURGED, actor="(retention)", target_type="expéditeur",
        target_id=sender, source=activity_log.SOURCE_CLI,
        details={"type": "effacement_rgpd", **result},
    )
    return result


def purge_old_activity(days: int = None, sensitive_days: int = None) -> int:
    """
    Purge le journal d'activité (§17) au-delà de `ACTIVITY_RETENTION_DAYS`.

    Le journal d'audit des validations (`audit_log.py`) reste, lui, délibérément conservé (intérêt
    légitime, art. 17.3(e) — cf. sa docstring). La distinction est assumée : `audit_log` porte peu
    de lignes, chacune attestant d'un engagement commercial ; `activity_log` porte tout le trafic
    d'usage, adresses IP comprises, et n'a pas la même justification de conservation illimitée.

    §18 — `sensitive_days` (par défaut `ACTIVITY_SENSITIVE_RETENTION_DAYS`) applique la rétention à
    deux vitesses d'`activity_log.purge_older_than()` : les événements de `SENSITIVE_ACTIONS`
    (échecs de connexion, verrouillages, changements de rôle/réglages…) survivent plus longtemps que
    le bruit d'usage courant.
    """
    return activity_log.purge_older_than(
        days or ACTIVITY_RETENTION_DAYS, sensitive_days=sensitive_days or ACTIVITY_SENSITIVE_RETENTION_DAYS,
    )


def run() -> None:
    print(f"[Rétention] Politique : {RETENTION_DAYS} jours.")
    n_checkpoints = purge_old_checkpoints()
    print(f"✅ {n_checkpoints} checkpoint(s) LangGraph supprimé(s) (corps d'e-mail purgé).")
    n_queue = purge_old_queue_entries()
    print(f"✅ {n_queue} entrée(s) de file d'attente supprimée(s) (queue.sqlite).")
    n_leads = purge_old_leads()
    print(f"✅ {n_leads} lead(s) supprimé(s) de l'onglet Leads.")
    n_activity = purge_old_activity()
    print(f"✅ {n_activity} entrée(s) de journal d'activité supprimée(s) "
          f"(> {ACTIVITY_RETENTION_DAYS} jours, > {ACTIVITY_SENSITIVE_RETENTION_DAYS} jours pour "
          f"les événements sensibles — la chaîne d'empreintes repart de la plus ancienne ligne "
          f"restante, ce n'est pas une falsification).")
    # §19 — les tâches TERMINÉES seulement : une note de rappel peut nommer un prospect, donc elle
    # relève de la même politique de rétention que le reste. Les tâches encore en attente sont
    # épargnées quelle que soit leur ancienneté (cf. `task_store.purge_older_than`) : une échéance
    # lointaine reste une intention valide, et l'effacer ferait disparaître un envoi qu'une
    # personne croit programmé.
    n_tasks = task_store.purge_older_than(RETENTION_DAYS)
    print(f"✅ {n_tasks} tâche(s) programmée(s) terminée(s) supprimée(s) (tasks.sqlite).")
    # §20 — les demandes de relecture CLOSES seulement, pour la même raison que les tâches : elles
    # recopient l'objet et l'adresse de l'expéditeur. Une demande encore en attente survit à la
    # purge quelle que soit son ancienneté — c'est un lead que personne n'a tranché, et l'effacer
    # ferait disparaître la trace du travail en souffrance en même temps que la donnée.
    n_reviews = review_store.purge_older_than(RETENTION_DAYS)
    print(f"✅ {n_reviews} demande(s) de relecture close(s) supprimée(s) (reviews.sqlite).")
    # §18 — trace machine de la purge périodique elle-même (distincte de `purge_old_activity`, qui
    # purge le JOURNAL — ici on trace le fait que Leads/checkpoints/file ont été purgés). Un seul
    # événement (pas un par emplacement) : c'est une seule décision de rétention exécutée d'un coup.
    activity_log.log(
        activity_log.ACTION_DATA_PURGED, actor="(retention)", source=activity_log.SOURCE_CLI,
        details={
            "type": "ancienneté", "seuil_jours": RETENTION_DAYS, "leads": n_leads,
            "checkpoints": n_checkpoints, "file_attente": n_queue, "journal_activité": n_activity,
            "tâches": n_tasks, "relectures": n_reviews,
        },
    )


def _main() -> None:
    """
    `python -m aca.core.retention` — purge périodique par ancienneté.
    `python -m aca.core.retention --oublier <adresse>` — droit à l'effacement d'une personne.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Rétention et effacement RGPD (§15.2.4/§15.2.5).")
    parser.add_argument(
        "--oublier", metavar="ADRESSE",
        help="adresse e-mail dont toutes les données doivent être effacées immédiatement",
    )
    args = parser.parse_args()

    if not args.oublier:
        run()
        return

    print(f"[RGPD] Effacement complet des donnees de {args.oublier}...")
    result = purge_subject(args.oublier)
    print(f"  Leads (Google Sheets)      : {result['leads']} ligne(s)")
    print(f"  Checkpoints LangGraph      : {result['checkpoints']} thread(s)")
    print(f"  File d'attente             : {result['queue']} entree(s)")
    print(f"  Suivi de relance           : {result['followup']} entree(s)")
    print("  Journal d'audit            : conserve volontairement (interet legitime, cf. docstring "
          "de purge_subject et docs/PRIVACY_POLICY.md)")


if __name__ == "__main__":
    _main()

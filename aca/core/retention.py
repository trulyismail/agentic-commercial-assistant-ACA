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

from aca.storage import followup_store, queue_store
from aca.integrations import sheets
from .app import checkpointer

load_dotenv()

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "365"))


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

    return {
        "leads": purge_leads_by_sender(sender),
        "checkpoints": checkpoints_deleted,
        "queue": queue_store.purge_sender(sender),
        "followup": followup_store.purge_sender(sender),
    }


def run() -> None:
    print(f"[Rétention] Politique : {RETENTION_DAYS} jours.")
    n_checkpoints = purge_old_checkpoints()
    print(f"✅ {n_checkpoints} checkpoint(s) LangGraph supprimé(s) (corps d'e-mail purgé).")
    n_queue = purge_old_queue_entries()
    print(f"✅ {n_queue} entrée(s) de file d'attente supprimée(s) (queue.sqlite).")
    n_leads = purge_old_leads()
    print(f"✅ {n_leads} lead(s) supprimé(s) de l'onglet Leads.")


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

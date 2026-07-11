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

from aca.storage import queue_store
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


def run() -> None:
    print(f"[Rétention] Politique : {RETENTION_DAYS} jours.")
    n_checkpoints = purge_old_checkpoints()
    print(f"✅ {n_checkpoints} checkpoint(s) LangGraph supprimé(s) (corps d'e-mail purgé).")
    n_queue = purge_old_queue_entries()
    print(f"✅ {n_queue} entrée(s) de file d'attente supprimée(s) (queue.sqlite).")
    n_leads = purge_old_leads()
    print(f"✅ {n_leads} lead(s) supprimé(s) de l'onglet Leads.")


if __name__ == "__main__":
    run()

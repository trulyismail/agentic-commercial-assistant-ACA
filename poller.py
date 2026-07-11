"""
Poller Gmail en arrière-plan : tourne indépendamment de l'UI Streamlit (processus séparé), détecte
les nouveaux e-mails non lus, les fait avancer dans le graphe LangGraph jusqu'à la pause de
validation, et les enregistre dans la file d'attente (`queue_store`) pour que l'UI les affiche.

Ne valide et n'envoie jamais rien lui-même — seul un humain clique « Valider » dans l'UI Streamlit
(le graphe reste interrompu avant `action` exactement comme dans le flux manuel).

Lancement : `python poller.py`, à laisser tourner dans un terminal séparé (ou via un planificateur
de tâches / service système). Intervalle configurable par la variable d'environnement
POLL_INTERVAL_SECONDS (défaut : 60).
"""
import os
import time
import traceback
from dotenv import load_dotenv

import analytics_store
import app as aca_graph
import gmail_reader
import queue_store
from attachment_reader import extract_text_from_attachments

load_dotenv()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


def _initial_state(email: dict, attachment_text: str) -> dict:
    """Même structure d'état initial que le formulaire manuel de ui.py, pour un e-mail Gmail."""
    return {
        "email_raw": {"sender": email["sender"], "subject": email["subject"], "body": email["body"]},
        "attachment_text": attachment_text,
        "gmail_message_id": email["id"],
        "gmail_thread_id": email.get("gmail_thread_id"),
        "extracted_info": {}, "faq_context": "", "company_profile": "", "draft_response": "",
        "sender_history": "", "is_duplicate": False, "action_status": "",
        "completed_agents": [], "reasoning_log": [],
    }


def process_one(service, summary: dict) -> None:
    """
    Fait avancer un e-mail Gmail non lu dans le graphe jusqu'à la pause, puis le rend visible dans
    la file. `queue_store.enqueue()` est appelé AVANT `app.invoke()` (statut « en_cours ») : si le
    process plante pendant l'analyse, l'e-mail reste marqué comme déjà pris en charge (pas de
    retraitement en double au prochain cycle) et `reset_stale()` le récupérera après expiration.
    """
    email = gmail_reader.get_email(service, summary["id"])
    thread_id = f"poll-{email['id']}"
    queue_store.enqueue(email["id"], thread_id, email["sender"], email["subject"])

    attachment_text = extract_text_from_attachments(email["attachments"])
    config = {"configurable": {"thread_id": thread_id}}
    final_state = aca_graph.app.invoke(_initial_state(email, attachment_text), config)

    # Tableau de bord (P2 §11.4 item 16) : le graphe a déjà tourné jusqu'à la pause dans ce
    # process, donc classification et éventuelle proposition sont connues dès maintenant — pas
    # besoin d'attendre qu'un humain ouvre l'analyse dans l'UI pour la comptabiliser.
    analytics_store.record_classification(
        thread_id, final_state.get("classification", "INCONNU"), email["sender"], source="poller",
    )
    if final_state.get("draft_response"):
        analytics_store.record_draft_ready(thread_id)

    queue_store.mark_ready(email["id"])
    print(f"   → mis en file d'attente : « {email['subject']} » ({email['sender']})")


def poll_once() -> None:
    """Un cycle : récupère les entrées bloquées, liste les e-mails non lus, traite les nouveaux."""
    stale = queue_store.reset_stale()
    if stale:
        print(f"[Poller] {stale} entrée(s) bloquée(s) réinitialisée(s) (cycle précédent interrompu).")

    service = gmail_reader.get_gmail_service()
    unread = gmail_reader.list_unread_emails(service)
    new_ones = [m for m in unread if not queue_store.is_known(m["id"])]
    print(f"[Poller] {len(unread)} non lu(s) au total, {len(new_ones)} nouveau(x) à traiter.")

    for summary in new_ones:
        try:
            process_one(service, summary)
        except Exception as e:
            print(f"⚠️ [Poller] Échec du traitement de {summary.get('id')} : {e}")
            traceback.print_exc()


def run_forever() -> None:
    queue_store.init_db()
    print(f"[Poller] Démarré — intervalle {POLL_INTERVAL_SECONDS}s. Ctrl+C pour arrêter.")
    while True:
        try:
            poll_once()
        except Exception as e:
            print(f"⚠️ [Poller] Erreur de cycle (connexion Gmail ?) : {e}")
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()

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
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.callbacks import UsageMetadataCallbackHandler

from aca.storage import activity_log, analytics_store, queue_store
from aca.core import app as aca_graph, intake_window
from aca.integrations import gmail_reader

load_dotenv()

# Conservé pour les déploiements existants dont le `.env` porte cette variable : `intake_window`
# la lit comme valeur de repli quand aucun intervalle n'a été réglé depuis l'interface. La cadence
# effective vient désormais de `intake_window.current_config()`, relue à chaque tour de boucle.
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


def _initial_state(email: dict) -> dict:
    """
    Même structure d'état initial que le formulaire manuel de ui.py, pour un e-mail Gmail.
    `attachments_raw` est transmis brut (§11.6) : l'extraction du texte se fait désormais dans le
    graphe (`ingestion_node`), plus ici — ce module n'a donc plus besoin d'appeler
    `extract_text_from_attachments()` lui-même avant `app.invoke()`.
    """
    return {
        "email_raw": {"sender": email["sender"], "subject": email["subject"], "body": email["body"]},
        "attachments_raw": email["attachments"],
        "gmail_message_id": email["id"],
        "gmail_thread_id": email.get("gmail_thread_id"),
        "extracted_info": {}, "faq_context": "", "knowledge_gap": False, "company_profile": "",
        "risk_flags": [], "draft_response": "",
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

    usage_handler = UsageMetadataCallbackHandler()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [usage_handler]}
    final_state = aca_graph.app.invoke(_initial_state(email), config)

    # §18 — trace machine : jusqu'ici seul le formulaire manuel de ui.py journalisait le lancement
    # d'une analyse (`ACTION_ANALYSIS_STARTED`, source=streamlit) ; la même action existe pour
    # l'intake automatique, seule la source change (`SOURCE_POLLER`) — sans acteur humain, `actor`
    # nomme le processus lui-même, comme le fait déjà `retention.py`/`relance.py` ailleurs.
    activity_log.log(
        activity_log.ACTION_ANALYSIS_STARTED, actor="(poller)", target_type="thread",
        target_id=thread_id, source=activity_log.SOURCE_POLLER,
        details={"expéditeur": email["sender"], "objet": email["subject"], "source": "poller"},
    )

    # Tableau de bord (P2 §11.4 item 16) : le graphe a déjà tourné jusqu'à la pause dans ce
    # process, donc classification et éventuelle proposition sont connues dès maintenant — pas
    # besoin d'attendre qu'un humain ouvre l'analyse dans l'UI pour la comptabiliser.
    analytics_store.record_classification(
        thread_id, final_state.get("classification", "INCONNU"), email["sender"], source="poller",
    )
    if final_state.get("draft_response"):
        analytics_store.record_draft_ready(thread_id)

    # Consommation de tokens de cette analyse (§13 item 4, "Quota Usage Tracker").
    tokens_in, tokens_out = aca_graph.sum_usage(usage_handler.usage_metadata)
    analytics_store.record_tokens(thread_id, tokens_in, tokens_out)

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
    """
    Boucle de réception, encadrée par la fenêtre horaire réglée dans « Réglages » (§19).

    Trois changements par rapport à la boucle nue d'origine, tous pour la même raison — un réglage
    affiché dans l'interface doit être vrai, pas décoratif :

    1. **La fenêtre est respectée.** Hors plage, Gmail n'est pas contacté du tout. Analyser à 3 h du
       matin consommait du quota et déclenchait une alerte pour une équipe qui ne la verrait qu'à
       9 h — et faisait paraître « ancienne » une analyse que personne n'aurait pu traiter plus tôt.
    2. **L'intervalle est relu à chaque tour**, jamais figé à l'import : changer la cadence depuis
       l'interface prend effet au cycle suivant, sans redémarrer le processus.
    3. **L'attente hors plage est plafonnée à une minute.** Dormir jusqu'à la prochaine ouverture
       serait plus économe, mais le réglage peut changer pendant ce sommeil : quelqu'un qui
       réactive la réception à 14 h ne doit pas attendre le lendemain matin parce que la boucle
       s'était endormie pour douze heures.
    """
    queue_store.init_db()
    print(f"[Poller] Démarré — {intake_window.describe()} Ctrl+C pour arrêter.")

    was_open = None
    while True:
        config = intake_window.current_config()
        now = datetime.now()
        open_now = intake_window.is_open(now, config)

        if open_now:
            if was_open is False:
                print(f"[Poller] Ouverture de la fenêtre de réception ({now:%H:%M}).")
            try:
                poll_once()
            except Exception as e:
                print(f"⚠️ [Poller] Erreur de cycle (connexion Gmail ?) : {e}")
                traceback.print_exc()
            delay = config["interval_seconds"]
        else:
            if was_open is not False:
                # Une seule ligne au moment de la fermeture, pas une par tour : un journal qui
                # répète « fermé » toutes les minutes devient illisible et noie les vraies lignes.
                nxt = intake_window.next_opening(now, config)
                when = f"jusqu'au {nxt:%d/%m à %H:%M}" if nxt else "(aucune ouverture programmée)"
                print(f"[Poller] Hors plage de réception — en veille {when}.")
            delay = min(60, config["interval_seconds"])

        was_open = open_now
        time.sleep(delay)


if __name__ == "__main__":
    run_forever()

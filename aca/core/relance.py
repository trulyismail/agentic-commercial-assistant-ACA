"""
Relances automatiques (P1 §11.4 item 7, cadence multi-round §11.6 item 5) : pour chaque lead suivi
(`followup_store`), si le COMMERCIAL a été le dernier à écrire dans le fil Gmail depuis au moins
`RELANCE_DAYS` jours (donc le prospect n'a pas répondu depuis), crée un brouillon de relance dans
le même fil — même mécanisme que `create_draft_reply` (P0-1) : un humain reste toujours celui qui
clique Envoyer, jamais un envoi automatique. Jusqu'à `RELANCE_MAX_ROUNDS` relances par lead
(défaut 3, cf. followup_store.py — ~80% des ventes demandent 5+ contacts au total) ; la cadence
s'arrête d'elle-même dès que le prospect répond (le dernier message du fil n'est alors plus de
nous), sans logique supplémentaire ici — chaque relance espace naturellement la suivante de
`RELANCE_DAYS` puisqu'elle devient à son tour le dernier message du fil une fois envoyée par
l'humain.

Lancement : `python relance.py` — à planifier périodiquement (ex. une fois par jour), indépendant
de `poller.py`. Variables d'environnement RELANCE_DAYS (défaut : 4) et RELANCE_MAX_ROUNDS (défaut : 3).
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from aca.storage import followup_store
from aca.integrations import gmail_reader

load_dotenv()

RELANCE_DAYS = int(os.getenv("RELANCE_DAYS", "4"))


def _relance_days() -> int:
    """
    Seuil d'inactivité effectif : réglage du panneau (config_store, prioritaire, §12 item 7) sinon
    `RELANCE_DAYS` (`.env`/défaut) — même principe que `app._calendly_url()`.
    """
    from aca.storage import config_store

    override = config_store.get_setting("RELANCE_DAYS")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return RELANCE_DAYS


def _last_message_info(service, gmail_thread_id: str):
    """Renvoie {message_id, from, days_since} pour le dernier message du fil, ou None si vide."""
    thread = service.users().threads().get(
        userId="me", id=gmail_thread_id, format="metadata", metadataHeaders=["From"],
    ).execute()
    messages = thread.get("messages", [])
    if not messages:
        return None
    last = messages[-1]
    headers = {h["name"]: h["value"] for h in last["payload"]["headers"]}
    sent_at = datetime.fromtimestamp(int(last["internalDate"]) / 1000, tz=timezone.utc)
    days_since = (datetime.now(timezone.utc) - sent_at).days
    return {"message_id": last["id"], "from": headers.get("From", ""), "days_since": days_since}


def check_one(service, our_email: str, lead: dict) -> None:
    """
    Relance un lead si : le dernier message du fil vient de NOUS (pas du prospect) ET date d'au
    moins RELANCE_DAYS jours. Si le dernier message vient du prospect, rien à faire (il a répondu,
    ou n'a pas encore été relancé une première fois — pas notre rôle ici).
    """
    info = _last_message_info(service, lead["gmail_thread_id"])
    if info is None:
        return
    if our_email.lower() not in info["from"].lower():
        return  # le prospect a répondu (ou nous n'avons pas encore envoyé notre première réponse)
    if info["days_since"] < _relance_days():
        return  # pas encore le moment

    # Cadence multi-round (§11.6 item 5) : ton légèrement différent selon le nombre de relances déjà
    # envoyées à ce lead (0 = première relance), pour ne pas répéter mot pour mot un message que le
    # prospect a déjà vu passer une ou deux fois.
    round_index = lead.get("followup_count", 0)
    if round_index == 0:
        relance_body = (
            f"Bonjour,\n\nJe me permets de revenir vers vous suite à mon précédent message concernant "
            f"« {lead['subject']} ». Restez-vous intéressé(e) ? N'hésitez pas à me faire signe si vous "
            f"avez des questions.\n\nCordialement."
        )
    else:
        relance_body = (
            f"Bonjour,\n\nJe reviens une nouvelle fois vers vous au sujet de « {lead['subject']} », "
            f"sans avoir eu de retour de votre part. Si le sujet n'est plus d'actualité, "
            f"n'hésitez pas à me le dire — sinon je reste à votre disposition pour en discuter."
            f"\n\nCordialement."
        )
    gmail_reader.create_draft_reply(
        service, message_id=info["message_id"], to=lead["sender"], subject=lead["subject"],
        body=relance_body,
    )
    followup_store.mark_followed_up(lead["thread_id"])
    print(f"   → brouillon de relance n°{round_index + 1} créé pour « {lead['subject']} » ({lead['sender']}).")


def run() -> None:
    followup_store.init_db()
    service = gmail_reader.get_gmail_service()
    our_email = service.users().getProfile(userId="me").execute()["emailAddress"]
    leads = followup_store.list_active()
    print(
        f"[Relance] {len(leads)} lead(s) suivi(s), seuil = {_relance_days()} jour(s), "
        f"max = {followup_store.relance_max_rounds()} relance(s)/lead."
    )
    for lead in leads:
        try:
            check_one(service, our_email, lead)
        except Exception as e:
            print(f"⚠️ [Relance] Échec pour {lead.get('sender')} : {e}")


if __name__ == "__main__":
    run()

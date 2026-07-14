"""
Intégration HubSpot CRM : miroir de `sheets.append_lead` (P2 — CRM réel). Écrit le lead validé dans
HubSpot en plus de l'onglet Sheets "Leads" (mode "alongside" pendant la période de transition — Sheets
reste la mémoire long terme lue par `find_leads_by_sender`/le tableau de bord, HubSpot devient le CRM
commercial réel).

Dégradation gracieuse identique à `notify.py`/`enrichment.py` : `HUBSPOT_ACCESS_TOKEN` absente ou tout
appel API en échec -> renvoie None, ne lève jamais d'exception, n'interrompt jamais `action_node`.

Modélisation : upsert d'un Contact (par e-mail) -> Deal associé (pipeline/étape par défaut du compte,
surchargeables via HUBSPOT_PIPELINE/HUBSPOT_DEALSTAGE) -> Note associée au contact et au deal portant
besoin/urgence/brouillon (les Deals HubSpot n'ont pas de propriété texte libre par défaut ; une Note
est la façon standard, sans configuration de propriété personnalisée requise côté portail).
"""
import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.hubapi.com"
PIPELINE = os.getenv("HUBSPOT_PIPELINE", "default")
DEALSTAGE = os.getenv("HUBSPOT_DEALSTAGE", "appointmentscheduled")


def is_enabled() -> bool:
    return bool(os.getenv("HUBSPOT_ACCESS_TOKEN"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('HUBSPOT_ACCESS_TOKEN')}",
        "Content-Type": "application/json",
    }


def _find_contact_id(email: str) -> str | None:
    response = requests.post(
        f"{API_BASE}/crm/v3/objects/contacts/search",
        headers=_headers(),
        json={
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
            "limit": 1,
        },
        timeout=10,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0]["id"] if results else None


def _upsert_contact(email: str, entreprise: str, contact_name: str) -> str | None:
    """Crée le contact s'il n'existe pas encore (recherche par e-mail), sinon met à jour ses champs."""
    if not email:
        return None

    properties = {"email": email}
    if entreprise and entreprise != "N/A":
        properties["company"] = entreprise
    if contact_name and contact_name != "N/A":
        parts = contact_name.strip().split(maxsplit=1)
        properties["firstname"] = parts[0]
        if len(parts) > 1:
            properties["lastname"] = parts[1]

    contact_id = _find_contact_id(email)
    if contact_id:
        response = requests.patch(
            f"{API_BASE}/crm/v3/objects/contacts/{contact_id}",
            headers=_headers(), json={"properties": properties}, timeout=10,
        )
        response.raise_for_status()
        return contact_id

    response = requests.post(
        f"{API_BASE}/crm/v3/objects/contacts",
        headers=_headers(), json={"properties": properties}, timeout=10,
    )
    response.raise_for_status()
    return response.json()["id"]


def _create_deal(dealname: str) -> str:
    response = requests.post(
        f"{API_BASE}/crm/v3/objects/deals",
        headers=_headers(),
        json={"properties": {"dealname": dealname, "pipeline": PIPELINE, "dealstage": DEALSTAGE}},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["id"]


def _associate_default(from_type: str, from_id: str, to_type: str, to_id: str) -> None:
    """Association par défaut HubSpot (v4) — pas besoin de connaître l'ID numérique du type d'association."""
    response = requests.put(
        f"{API_BASE}/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}",
        headers=_headers(), timeout=10,
    )
    response.raise_for_status()


def _create_note(body: str, contact_id: str | None, deal_id: str | None) -> None:
    if not body:
        return
    response = requests.post(
        f"{API_BASE}/crm/v3/objects/notes",
        headers=_headers(),
        json={"properties": {
            "hs_note_body": body,
            "hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }},
        timeout=10,
    )
    response.raise_for_status()
    note_id = response.json()["id"]
    if contact_id:
        _associate_default("notes", note_id, "contacts", contact_id)
    if deal_id:
        _associate_default("notes", note_id, "deals", deal_id)


def create_lead(email_classification: str, extracted_info: dict, sender: str, draft: str) -> str | None:
    """
    Miroir de sheets.append_lead : upsert le contact (par e-mail), crée un deal associé, et une note
    reprenant besoin/urgence/brouillon. Renvoie l'ID du deal créé, ou None si HUBSPOT_ACCESS_TOKEN
    est absente ou en cas d'échec (dégradation gracieuse, jamais d'exception propagée).
    """
    if not is_enabled():
        return None

    entreprise = extracted_info.get("entreprise", "N/A")
    contact_name = extracted_info.get("contact", "N/A")
    urgence = extracted_info.get("urgence", "N/A")
    besoin = extracted_info.get("besoin_principal", "N/A")

    try:
        contact_id = _upsert_contact(sender, entreprise, contact_name)
        dealname = f"{entreprise if entreprise != 'N/A' else sender} — {email_classification}"
        deal_id = _create_deal(dealname)
        if contact_id:
            _associate_default("deals", deal_id, "contacts", contact_id)
        note_body = f"Catégorie : {email_classification}\nUrgence : {urgence}\nBesoin : {besoin}\n\n{draft}"
        _create_note(note_body, contact_id, deal_id)
    except Exception as e:
        # Le HubSpot write a échoué avant d'aboutir : sûr de renvoyer None ici.
        try:
            print(f"⚠️ Écriture HubSpot échouée : {e}")
        except Exception:
            print("Echec de l'ecriture HubSpot (voir logs).")
        return None

    # Le deal (+ contact + note) est déjà créé côté HubSpot à ce stade : `return deal_id` ne doit
    # jamais dépendre du succès du print (ex. UnicodeEncodeError sur la console Windows/cp1252) —
    # sinon un print qui plante ferait remonter une exception après une écriture réussie, et le
    # RetryPolicy d'`action_node` re-déclencherait tout le nœud, dupliquant le lead (Sheets + HubSpot).
    try:
        print(f"   → [HubSpot] Deal créé ({deal_id}) pour {sender}.")
    except Exception:
        print(f"   -> [HubSpot] Deal cree ({deal_id}).")
    return deal_id


if __name__ == "__main__":
    if not is_enabled():
        print("HUBSPOT_ACCESS_TOKEN absente : rien à tester.")
    else:
        result = create_lead(
            email_classification="DEVIS",
            extracted_info={
                "entreprise": "Test ACA", "contact": "Jean Test",
                "urgence": "Faible", "besoin_principal": "Test d'intégration HubSpot",
            },
            sender="test-aca@example.com",
            draft="Ceci est un brouillon de test pour vérifier l'intégration HubSpot.",
        )
        print(f"Deal ID : {result}" if result else "Échec (voir logs ci-dessus).")

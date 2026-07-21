"""
Suivi de facturation par organisation (§12 item 4, audité §14 de docs/ACAM_roadmap.md).

La première marche — gratuite — est déjà faite : `analytics_store.record_tokens`/`token_stats`
journalisent la consommation Groq par tenant (`org_id`, fondation multi-tenant §12 item 3) sans
aucun coût, tant que Groq reste gratuit. Ce module ajoute la marche SUIVANTE, explicitement
marquée comme n'ayant de sens qu'en phase commerciale (⚠️ Stripe est un service payant — en
désaccord avec la contrainte 0 € du prototype tant qu'aucun client ne paie réellement) :
reporter cette consommation comme un enregistrement d'usage Stripe, pour facturer un client au
prorata de sa consommation réelle de tokens.

Dégradation gracieuse identique à `notify.py`/`hubspot.py` : `STRIPE_API_KEY` absente, ou aucun
`STRIPE_SUBSCRIPTION_ITEM_ID` réglé pour ce tenant (via le panneau de réglages, config_store) ->
`report_usage()` renvoie quand même les statistiques (utiles pour un affichage KPI local) sans
jamais appeler Stripe ni lever d'exception. Jamais exercé en direct contre un vrai compte Stripe
(aucun compte de test disponible dans ce projet) — contrairement à hubspot.py, ce module n'est
donc PAS vérifié en conditions réelles ; seule sa dégradation gracieuse et son appel API (via un
faux client Stripe) sont couverts par les tests.
"""
import os

import stripe

from aca.core.tenant import current_org_id
from aca.storage import analytics_store, config_store


def is_enabled() -> bool:
    return bool(os.getenv("STRIPE_API_KEY"))


def report_usage(org_id: str = None, days: int = 1) -> dict:
    """
    Calcule la consommation de tokens du tenant sur les `days` derniers jours
    (`analytics_store.token_stats`) et, si Stripe est configuré ET qu'un item d'abonnement est
    réglé pour ce tenant, reporte la quantité totale comme un enregistrement d'usage Stripe
    (`action="set"` : remplace la quantité du jour plutôt que de l'incrémenter, pour que des
    ré-exécutions ne comptent pas la même consommation plusieurs fois).

    Renvoie toujours les statistiques calculées (jamais `None` pour un problème de configuration
    — Stripe désactivé ou tenant non abonné sont des états normaux, pas des échecs). Une erreur
    Stripe réelle (réseau, item invalide...) est journalisée et absorbée : ce suivi ne doit jamais
    faire échouer le processus planifié qui l'appelle.
    """
    tenant = org_id or current_org_id()
    stats = analytics_store.token_stats(days=days, org_id=tenant)
    total_tokens = stats["total_entree"] + stats["total_sortie"]

    if not is_enabled() or not total_tokens:
        return stats

    subscription_item_id = config_store.get_setting("STRIPE_SUBSCRIPTION_ITEM_ID", org_id=tenant)
    if not subscription_item_id:
        return stats  # aucun abonnement Stripe rattaché à ce tenant pour l'instant

    stripe.api_key = os.getenv("STRIPE_API_KEY")
    try:
        stripe.SubscriptionItem.create_usage_record(
            subscription_item_id, quantity=total_tokens, action="set",
        )
        print(f"   → [Billing] {total_tokens} tokens reportés à Stripe pour le tenant « {tenant} ».")
    except Exception as e:
        print(f"⚠️ [Billing] Report d'usage Stripe échoué pour « {tenant} » : {e}")
    return stats


if __name__ == "__main__":
    if not is_enabled():
        print("STRIPE_API_KEY absente : rien à reporter (le suivi de tokens local reste actif).")
    else:
        result = report_usage()
        print(f"Statistiques du tenant courant : {result}")

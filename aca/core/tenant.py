"""
Identifiant de tenant (organisation) — fondation multi-tenant (§12 item 3, audité §14.3 de
docs/ACAM_roadmap.md). Ce projet reste un prototype mono-utilisateur : il n'y a ici NI
authentification, NI gestion réelle d'organisations (pas de table `organizations`, pas de
Supabase Auth) — seulement un identifiant de tenant lu depuis l'environnement, qui tague désormais
chaque ligne écrite dans les registres SQLite locaux et dans `faq_embeddings` (Supabase). C'est le
prérequis de données sur lequel une vraie isolation (RLS Postgres, puis plus tard des identifiants
Google Sheets/HubSpot distincts par client via le panneau de réglages) peut être construite,
sans lui-même constituer un système multi-tenant complet.

Lu dynamiquement (jamais figé dans une constante au niveau module) — même raisonnement que
`DATABASE_URL` dans vector_store.py (cf. docs/PROJECT_JOURNAL.md, bug du 2026-07-11) : figer la
valeur à l'import gèlerait un défaut incorrect si `ACA_ORG_ID` est défini par un `load_dotenv()`
qui s'exécute après le premier import de ce module.
"""
import os

DEFAULT_ORG_ID = "default"


def current_org_id() -> str:
    """Identifiant de tenant du process courant (`ACA_ORG_ID`, ou `DEFAULT_ORG_ID` si absent)."""
    return os.getenv("ACA_ORG_ID", DEFAULT_ORG_ID)

"""
Panneau de réglages dynamique (§12 item 7, audité §14 de docs/ACAM_roadmap.md) : un manager (pas
un développeur) peut éditer certains paramètres depuis l'onglet « Réglages » de l'UI Streamlit —
lien Calendly, adresses/webhooks de routage SUPPORT/RH, seuils de relance et de rétention — sans
toucher au fichier `.env`. Ce registre local (SQLite, `config.sqlite`) stocke ces réglages PAR
TENANT (`org_id`, cf. aca.core.tenant — fondation multi-tenant §12 item 3), prérequis avant que
chaque client puisse un jour avoir sa propre configuration sans redéploiement séparé.

Un réglage absent de cette base (jamais édité via l'UI) renvoie `None` — les appelants (`app.py`)
retombent alors sur la valeur `.env` existante, exactement comme avant ce module. Ce n'est donc
PAS un remplacement de `.env` mais une SURCOUCHE : `.env` reste le défaut d'usine, ce module ne
porte que les valeurs explicitement changées par un humain via l'UI.
"""
import os
import sqlite3
from datetime import datetime
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_CONFIG_DB", "data/config.sqlite")

# Clés reconnues par l'onglet « Réglages » (cf. ui.py) — une simple convention de nommage, pas une
# contrainte de schéma : `get_setting`/`set_setting` acceptent n'importe quelle clé texte.
SETTINGS_SCHEMA = {
    "CALENDLY_URL": "Lien de réservation Calendly (demandes de démo)",
    "SUPPORT_EMAIL": "Adresse e-mail de l'équipe support (routage SUPPORT)",
    "SUPPORT_SLACK_WEBHOOK_URL": "Webhook Slack de l'équipe support",
    "HR_EMAIL": "Adresse e-mail RH (routage AUTRE)",
    "HR_SLACK_WEBHOOK_URL": "Webhook Slack RH",
    "RELANCE_DAYS": "Jours d'inactivité avant une relance",
    "RELANCE_MAX_ROUNDS": "Nombre maximum de relances par lead",
    "RETENTION_DAYS": "Jours de conservation avant purge RGPD",
    # §19 — réception automatique. Ces clés ne sont pas seulement documentaires : `api.py` valide
    # `POST /settings` contre ce dictionnaire (liste blanche, §15), donc une clé absente d'ici
    # serait rejetée et le panneau de réglages ne pourrait rien enregistrer.
    "INTAKE_ENABLED": "Réception automatique des e-mails activée (1/0)",
    "INTAKE_DAYS": "Jours de réception (0 = lundi … 6 = dimanche, séparés par des virgules)",
    "INTAKE_START": "Heure d'ouverture de la réception (HH:MM)",
    "INTAKE_END": "Heure de fermeture de la réception (HH:MM)",
    "INTAKE_INTERVAL_SECONDS": "Délai entre deux relevés de la boîte de réception (secondes)",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings ("
        "org_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY (org_id, key))"
    )
    return conn


@with_sqlite_retry
def init_db() -> None:
    """Crée la table si nécessaire."""
    _connect().close()


@with_sqlite_retry
def get_setting(key: str, org_id: str = None) -> str:
    """Valeur éditée pour `key` par le tenant courant, ou `None` si jamais réglée (repli `.env`)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE org_id = ? AND key = ?", (org_id or current_org_id(), key)
        ).fetchone()
    return row[0] if row else None


@with_sqlite_retry
def set_setting(key: str, value: str, org_id: str = None) -> None:
    """Enregistre (ou met à jour) un réglage pour le tenant courant."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (org_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (org_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (org_id or current_org_id(), key, value, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


@with_sqlite_retry
def get_all_settings(org_id: str = None) -> dict:
    """Tous les réglages édités du tenant courant, sous forme de dict {clé: valeur}."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE org_id = ?", (org_id or current_org_id(),)
        ).fetchall()
    return {key: value for key, value in rows}

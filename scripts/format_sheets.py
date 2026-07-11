"""
Mise en forme visuelle des onglets Google Sheets (Leads, FAQ, Enrichissement_Cache) : en-têtes
figés/mis en gras, largeurs de colonnes, retour à la ligne sur le texte long, et mise en forme
conditionnelle par valeur (urgence, catégorie, statut) pour une lecture plus rapide.

Ne modifie AUCUNE donnée — script idempotent (relance sans risque, y compris après un changement de
schéma comme l'ajout de la colonne Statut par l'agent Veille).
"""
import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER_FORMAT = {
    "textFormat": {"bold": True, "fontSize": 10},
    "backgroundColor": {"red": 0.90, "green": 0.90, "blue": 0.90},
}

# Palette pastel cohérente avec les badges de catégorie de ui.py (CATEGORY_STYLE).
COLOR = {
    "vert": {"red": 0.85, "green": 0.94, "blue": 0.83},
    "bleu": {"red": 0.80, "green": 0.88, "blue": 0.98},
    "orange": {"red": 1.00, "green": 0.90, "blue": 0.70},
    "rouge": {"red": 0.96, "green": 0.80, "blue": 0.80},
    "gris": {"red": 0.90, "green": 0.90, "blue": 0.90},
}


def _spreadsheet():
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def _condition_rule(sheet_id: int, col: int, value: str, bg: dict, last_row: int = 2000) -> dict:
    """Règle « fond coloré si la cellule == value » sur une colonne, à partir de la ligne 2 (sous l'en-tête)."""
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": last_row,
                    "startColumnIndex": col, "endColumnIndex": col + 1,
                }],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": value}]},
                    "format": {"backgroundColor": bg},
                },
            },
            "index": 0,
        }
    }


def _column_width(sheet_id: int, col: int, pixels: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": pixels},
            "fields": "pixelSize",
        }
    }


def _wrap_column(sheet_id: int, col: int, last_row: int = 2000) -> dict:
    return {
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": last_row,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment",
        }
    }


def _clear_conditional_rules(ss, sheet_id: int) -> list:
    """Supprime les règles de mise en forme conditionnelle existantes sur cet onglet (relance idempotente)."""
    meta = ss.fetch_sheet_metadata()
    for sheet in meta["sheets"]:
        if sheet["properties"]["sheetId"] == sheet_id:
            n = len(sheet.get("conditionalFormats", []))
            return [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}} for i in reversed(range(n))]
    return []


def format_leads(ss) -> None:
    """Colonnes : A Date | B Expéditeur | C Entreprise | D Contact | E Urgence | F Besoin | G Catégorie | H Brouillon."""
    ws = ss.worksheet("Leads")
    sheet_id = ws.id
    ws.freeze(rows=1)
    ws.format("A1:H1", HEADER_FORMAT)

    requests = _clear_conditional_rules(ss, sheet_id)
    if requests:
        ss.batch_update({"requests": requests})

    requests = [
        _column_width(sheet_id, 0, 130),  # Date
        _column_width(sheet_id, 5, 260),  # Besoin
        _column_width(sheet_id, 7, 320),  # Brouillon
        _wrap_column(sheet_id, 5),
        _wrap_column(sheet_id, 7),
        # Urgence (E, col 4)
        _condition_rule(sheet_id, 4, "haute", COLOR["rouge"]),
        _condition_rule(sheet_id, 4, "moyenne", COLOR["orange"]),
        _condition_rule(sheet_id, 4, "basse", COLOR["vert"]),
        # Catégorie (G, col 6) — mêmes couleurs que les badges de l'UI Streamlit.
        _condition_rule(sheet_id, 6, "DEMANDE_DEMO", COLOR["vert"]),
        _condition_rule(sheet_id, 6, "DEVIS", COLOR["bleu"]),
        _condition_rule(sheet_id, 6, "SUPPORT", COLOR["orange"]),
        _condition_rule(sheet_id, 6, "SPAM", COLOR["gris"]),
        _condition_rule(sheet_id, 6, "AUTRE", COLOR["gris"]),
    ]
    ss.batch_update({"requests": requests})
    print("✅ Onglet Leads mis en forme.")


def format_faq(ss) -> None:
    """Colonnes : A Question | B Réponse | C Statut (validé / à valider / rejeté)."""
    ws = ss.worksheet("FAQ")
    sheet_id = ws.id
    ws.freeze(rows=1)
    ws.format("A1:C1", HEADER_FORMAT)  # ré-applique aussi sur l'en-tête Statut ajouté par write_knowledge_rows

    requests = _clear_conditional_rules(ss, sheet_id)
    if requests:
        ss.batch_update({"requests": requests})

    requests = [
        _column_width(sheet_id, 0, 320),  # Question
        _column_width(sheet_id, 1, 380),  # Réponse
        _column_width(sheet_id, 2, 110),  # Statut
        _wrap_column(sheet_id, 0),
        _wrap_column(sheet_id, 1),
        _condition_rule(sheet_id, 2, "validé", COLOR["vert"]),
        _condition_rule(sheet_id, 2, "à valider", COLOR["orange"]),
        _condition_rule(sheet_id, 2, "rejeté", COLOR["rouge"]),
    ]
    ss.batch_update({"requests": requests})
    print("✅ Onglet FAQ mis en forme.")


def format_enrichment_cache(ss) -> None:
    """Colonnes : A Domaine | B Profil | C Date. N'existe qu'après le premier appel Tavily réussi."""
    try:
        ws = ss.worksheet("Enrichissement_Cache")
    except gspread.WorksheetNotFound:
        print("ℹ️ Onglet Enrichissement_Cache absent (pas encore de profil mis en cache) — ignoré.")
        return

    sheet_id = ws.id
    ws.freeze(rows=1)
    ws.format("A1:C1", HEADER_FORMAT)
    ss.batch_update({"requests": [
        _column_width(sheet_id, 1, 380),  # Profil
        _wrap_column(sheet_id, 1),
    ]})
    print("✅ Onglet Enrichissement_Cache mis en forme.")


def format_all() -> None:
    ss = _spreadsheet()
    format_leads(ss)
    format_faq(ss)
    format_enrichment_cache(ss)


if __name__ == "__main__":
    format_all()

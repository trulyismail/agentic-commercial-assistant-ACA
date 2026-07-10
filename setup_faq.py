import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def force_populate_faq():
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    
    faq_worksheet = spreadsheet.worksheet("FAQ")
    print("Mise à jour des données de base de la FAQ...")
    faq_worksheet.update(range_name="A1:B3", values=[
        ["Question", "Réponse"],
        ["Quel est le délai normal de livraison ?", "Nous livrons en 48h jours ouvrés."],
        ["Quels sont vos tarifs professionnels ?", "Nos licences pro démarrent à 50€/mois/utilisateur."]
    ])
    
    faq_worksheet.format("A1:B1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
    })
    print("✅ Données FAQ réécrites avec succès !")

if __name__ == "__main__":
    force_populate_faq()

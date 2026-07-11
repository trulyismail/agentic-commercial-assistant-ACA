import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# Charger les variables d'environnement
load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def init_headers():
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    
    creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.worksheet("Leads")
    
    # Les titres des colonnes
    headers = ["Date", "Expéditeur", "Entreprise", "Contact", "Urgence", "Besoin", "Catégorie", "Brouillon"]
    
    # On ajoute la ligne tout en haut (index=1) pour pousser les données de test vers le bas
    print("Insertion des titres en ligne 1...")
    worksheet.insert_row(headers, index=1)
    
    # On met les titres en gras
    print("Mise en forme (gras) pour les titres...")
    worksheet.format("A1:H1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9} # Gris clair
    })
    
    print("✅ Titres ajoutés avec succès !")

if __name__ == "__main__":
    init_headers()

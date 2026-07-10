import fitz  # PyMuPDF

def extract_text_from_pdf(file_path_or_bytes) -> str:
    """
    Extrait le texte brut d'un document PDF.
    Accepte soit un chemin de fichier (str), soit un flux de bytes (si uploadé depuis Streamlit).
    """
    try:
        # Si c'est un flux de bytes (venant de st.file_uploader)
        if isinstance(file_path_or_bytes, bytes):
            doc = fitz.open("pdf", file_path_or_bytes)
        # Si c'est un chemin de fichier classique
        else:
            doc = fitz.open(file_path_or_bytes)
            
        full_text = []
        for page in doc:
            full_text.append(page.get_text())
            
        doc.close()
        
        # Retourne le texte complet, nettoyé de base
        text = "\n".join(full_text).strip()
        
        # Limiter la taille du texte si trop énorme (pour ne pas exploser les tokens LLM)
        if len(text) > 15000:
            text = text[:15000] + "... [Texte tronqué car trop long]"
            
        return text
        
    except Exception as e:
        print(f"❌ Erreur lors de l'extraction PDF : {e}")
        return ""

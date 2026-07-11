import fitz  # PyMuPDF

# Budget de caractères pour ne pas exploser les tokens LLM — partagé avec attachment_reader.py
# (une seule limite globale par e-mail, pas une limite par pièce jointe, cf. P2 §11.4 item 16).
MAX_CHARS = 15000


def extract_raw_text_from_pdf(file_path_or_bytes) -> str:
    """
    Extrait le texte brut d'un PDF, SANS troncature (usage interne, partagé avec
    attachment_reader.py pour concaténer plusieurs pièces jointes avant de tronquer l'ensemble une
    seule fois). Accepte soit un chemin de fichier (str), soit un flux de bytes.
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
        return "\n".join(full_text).strip()

    except Exception as e:
        print(f"❌ Erreur lors de l'extraction PDF : {e}")
        return ""


def extract_text_from_pdf(file_path_or_bytes) -> str:
    """
    Extrait le texte brut d'un document PDF, tronqué à MAX_CHARS. Accepte soit un chemin de
    fichier (str), soit un flux de bytes (si uploadé depuis Streamlit).
    """
    text = extract_raw_text_from_pdf(file_path_or_bytes)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "... [Texte tronqué car trop long]"
    return text

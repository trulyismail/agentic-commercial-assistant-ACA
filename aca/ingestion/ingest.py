"""
Ingestion de connaissances : doc/PDF/Markdown → paires Q/R → onglet Knowledge_Base (Google Sheets).

C'est le "remplacement du Vector DB" du projet : au lieu de chunker + embedder dans une base
vectorielle, on demande à Groq de transformer un document lourd (cahier de politiques, catalogue,
FAQ interne...) en paires Question/Réponse, écrites dans Google Sheets. Le RAG sémantique
(`sheets.search_knowledge_base_semantic`) lit ensuite depuis cet onglet — aucune base de données.

Utilisable en script (`python ingest.py chemin/doc.pdf`) ou depuis l'UI (uploader Streamlit).
"""
import os
import sys
import json

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from aca.integrations import sheets
from .pdf_reader import extract_text_from_pdf

load_dotenv()

# Borne le texte envoyé au LLM (le PDF est déjà tronqué à 15k par pdf_reader ; on reste cohérent).
MAX_CHARS = 15000


def _smart_llm():
    """Llama 3.3 70B (température 0) pour un découpage Q/R fidèle."""
    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0)


def _read_source(source) -> str:
    """
    Extrait le texte brut d'une source : bytes PDF, chemin .pdf, ou chemin .md/.txt.
    Renvoie "" si la source est illisible.
    """
    if isinstance(source, (bytes, bytearray)):
        return extract_text_from_pdf(bytes(source))
    if isinstance(source, str) and os.path.exists(source):
        if source.lower().endswith(".pdf"):
            return extract_text_from_pdf(source)
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    # Sinon : on considère que c'est directement du texte.
    return str(source or "")


def document_to_pairs(text: str) -> list:
    """
    Demande à Groq de transformer un document en paires Q/R prêtes pour la Knowledge_Base.
    Renvoie une liste de tuples (question, réponse) ; [] si le texte est vide ou le parsing échoue.
    """
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        return []

    messages = [
        SystemMessage(content=(
            "Tu es un assistant qui construit une base de connaissances commerciale. "
            "À partir du document fourni, extrais les informations utiles à un commercial (tarifs, "
            "délais, conditions, politiques, caractéristiques produit, FAQ...) sous forme de paires "
            "question/réponse concises et autonomes.\n"
            "Réponds UNIQUEMENT avec un tableau JSON de la forme :\n"
            '[{"question": "...", "reponse": "..."}, ...]\n'
            "Pas de markdown, pas de texte hors du JSON. 3 à 15 paires selon la richesse du document."
        )),
        HumanMessage(content=text),
    ]

    raw = _smart_llm().invoke(messages).content.strip()
    # Tolère un éventuel bloc de code ```json ... ```
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("["): raw.rfind("]") + 1]

    try:
        data = json.loads(raw)
    except Exception:
        print("⚠️ Réponse LLM non-JSON, aucune paire extraite.")
        return []

    pairs = []
    for item in data if isinstance(data, list) else []:
        q = str(item.get("question", "")).strip()
        r = str(item.get("reponse", item.get("réponse", ""))).strip()
        if q and r:
            pairs.append((q, r))
    return pairs


def ingest_document(source, mode: str = "append") -> int:
    """
    Pipeline complet : source (bytes/chemin/texte) → paires Q/R (Groq) → onglet Knowledge_Base (Sheets).
    Renvoie le nombre de lignes écrites (0 si rien).
    """
    print("\n📥 [Ingestion] Lecture de la source...")
    text = _read_source(source)
    if not text.strip():
        print("   → Source vide ou illisible.")
        return 0

    print("🧠 [Ingestion/Groq] Découpage du document en paires Q/R...")
    pairs = document_to_pairs(text)
    if not pairs:
        print("   → Aucune paire Q/R extraite.")
        return 0
    print(f"   → {len(pairs)} paire(s) extraite(s).")

    written = sheets.write_knowledge_rows(pairs, mode=mode)
    return written


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python ingest.py <chemin_du_document.pdf|.md|.txt> [append|replace]")
        sys.exit(1)
    path = sys.argv[1]
    ingest_mode = sys.argv[2] if len(sys.argv) > 2 else "append"
    n = ingest_document(path, mode=ingest_mode)
    print(f"\n✅ Ingestion terminée : {n} ligne(s) ajoutée(s) à la Knowledge_Base.")

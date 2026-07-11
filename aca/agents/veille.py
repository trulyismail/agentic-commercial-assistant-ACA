"""
Agent Veille : recherche web pour enrichir la FAQ quand la Knowledge_Base ne répond pas à la
question posée (mémoire hybride, même schéma que enrichment.py) :
- interroge Tavily (niveau gratuit) pour trouver une réponse,
- la reformule en paire Question/Réponse propre (Groq, 8B),
- l'ajoute à l'onglet FAQ (sheets.write_knowledge_rows, statut="à valider") en STAGING : le contenu
  web n'est pas vérifié, donc invisible du RAG jusqu'à validation humaine (sidebar Streamlit,
  sheets.get_pending_knowledge_rows/approve_knowledge_row) — une fois approuvée, la même question
  sera retrouvée directement par le RAG sémantique, sans repasser par le web.

Dégradation gracieuse comme les autres agents : renvoie "" si TAVILY_API_KEY absente, si la
recherche échoue, ou si aucune réponse exploitable n'est trouvée — l'agent continue sans erreur.
"""
import os
import json
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from aca.integrations import sheets

load_dotenv()


def _format_qr(query: str, raw_answer: str) -> tuple:
    """Reformule la réponse Tavily en une paire (question, réponse) claire et concise via Groq."""
    messages = [
        SystemMessage(content=(
            "Reformule ceci en UNE paire question/réponse claire et concise pour une FAQ, "
            "au format JSON exact :\n"
            '{"question": "...", "reponse": "..."}\n'
            "Réponds UNIQUEMENT avec le JSON, sans markdown ni explication."
        )),
        HumanMessage(content=f"Question du client : {query}\nInformation trouvée en ligne : {raw_answer}"),
    ]
    try:
        data = json.loads(ChatGroq(model="llama-3.1-8b-instant", temperature=0).invoke(messages).content.strip())
        return data.get("question") or query, data.get("reponse") or raw_answer
    except Exception:
        return query, raw_answer


def search_faq_online(query: str) -> str:
    """
    Cherche en ligne une réponse à `query`, l'ajoute à la FAQ (mémoire long terme) et la renvoie.
    Renvoie "" (jamais d'exception) si TAVILY_API_KEY absente ou recherche infructueuse.
    """
    if not query.strip():
        return ""

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("   → TAVILY_API_KEY absente : veille web ignorée (repli gracieux).")
        return ""

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_answer="advanced",
        )
    except Exception as e:
        print(f"⚠️ Échec de la recherche Tavily (veille), ignorée : {e}")
        return ""

    answer = (response.get("answer") or "").strip()
    if not answer:
        return ""

    question, reponse = _format_qr(query, answer)
    try:
        # statut="à valider" : contenu web non vérifié, invisible du RAG jusqu'à validation humaine
        # (cf. sheets.get_pending_knowledge_rows / approve_knowledge_row).
        sheets.write_knowledge_rows([(question, reponse)], mode="append", statut="à valider")
        print(f"   → FAQ enrichie (en attente de validation humaine) : « {question} »")
    except Exception as e:
        print(f"⚠️ Échec de l'écriture dans la FAQ (veille) : {e}")
    return reponse


if __name__ == "__main__":
    # Test manuel : une question absente de la FAQ de départ.
    print(search_faq_online("Proposez-vous une intégration avec Salesforce ?") or "(aucune réponse)")

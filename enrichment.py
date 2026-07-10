"""
Agent Enrichissement : recherche web sur l'entreprise de l'expéditeur (mémoire hybride).

- Mémoire long terme : consulte d'abord l'onglet `Enrichissement_Cache` (Google Sheets) indexé par
  domaine → si le profil est déjà connu, aucun appel réseau.
- Sinon : interroge Tavily (niveau gratuit, 1 000 req/mois) puis met le résultat en cache.

Comme le RAG sémantique, la fonction se dégrade gracieusement : domaine générique (gmail, outlook...),
`TAVILY_API_KEY` absente, ou erreur → renvoie "" et l'agent continue sans enrichissement plutôt que
de planter.
"""
import os
from dotenv import load_dotenv

import sheets

load_dotenv()

# Domaines de messagerie grand public : pas d'entreprise à enrichir derrière.
GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "yahoo.fr", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr", "laposte.net",
}


def domain_from_sender(sender: str) -> str:
    """Extrait le domaine minuscule d'une adresse ('Bob <b@ACME.com>' -> 'acme.com')."""
    if not sender or "@" not in sender:
        return ""
    return sender.split("@")[-1].strip().strip(">").lower()


def research_company(sender: str) -> str:
    """
    Renvoie un profil court (2-3 phrases) de l'entreprise derrière le domaine de l'expéditeur,
    ou "" si non applicable/indisponible. Ne lève jamais d'exception (dégradation gracieuse).
    Utilise le cache Sheets (mémoire long terme) avant tout appel réseau.
    """
    domain = domain_from_sender(sender)
    if not domain:
        return ""
    if domain in GENERIC_DOMAINS:
        print(f"   → Domaine générique ({domain}) : contact particulier, pas d'enrichissement.")
        return ""

    # Mémoire long terme : profil déjà recherché ?
    cached = sheets.get_cached_profile(domain)
    if cached:
        print(f"   → Profil en cache (mémoire long terme) pour {domain}.")
        return cached

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("   → TAVILY_API_KEY absente : enrichissement ignoré (repli gracieux).")
        return ""

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=f"Entreprise derrière le domaine {domain} : secteur d'activité, taille, activité principale",
            search_depth="basic",
            max_results=3,
            include_answer="advanced",
        )
    except Exception as e:
        print(f"⚠️ Échec de la recherche Tavily, enrichissement ignoré : {e}")
        return ""

    profile = (response.get("answer") or "").strip()
    if not profile:
        # Pas de réponse synthétique : on retombe sur les titres/extraits des résultats.
        snippets = [
            f"{r.get('title', '')} — {r.get('content', '')}".strip(" —")
            for r in response.get("results", [])
        ]
        profile = " ".join(s for s in snippets if s)[:600].strip()

    if profile:
        sheets.cache_profile(domain, profile)  # mémoriser pour la prochaine fois
    return profile


if __name__ == "__main__":
    # Test manuel : profil d'un domaine d'entreprise connu.
    for test_sender in ["contact@anthropic.com", "jean.dupont@gmail.com"]:
        print(f"\n=== {test_sender} ===")
        print(research_company(test_sender) or "(aucun profil)")

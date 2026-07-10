import os
import json
import operator
from typing import TypedDict, Optional, Annotated
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
import sheets
import enrichment

# Charger toutes les clés API du fichier .env
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Modèles Groq (tous gratuits) — deux profils selon la tâche
# ─────────────────────────────────────────────────────────────────────────────
# llama-3.1-8b-instant    → ultra-rapide, tâches simples (classification)
# llama-3.3-70b-versatile → plus puissant (extraction JSON, rédaction)

def fast_llm():
    """Llama 3.1 8B — ultra-rapide pour la classification."""
    return ChatGroq(model="llama-3.1-8b-instant", temperature=0)

def smart_llm():
    """Llama 3.3 70B — puissant pour l'extraction JSON structurée."""
    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0)

def creative_llm():
    """Llama 3.3 70B — légèrement créatif pour les brouillons de réponse."""
    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0.3)


# ─────────────────────────────────────────────────────────────────────────────
# 1. État partagé du graphe (mémoire de travail transmise de nœud en nœud)
# ─────────────────────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    email_raw: dict            # {sender, subject, body}
    attachment_text: str       # Texte extrait de la pièce jointe PDF
    classification: str        # DEMANDE_DEMO | DEVIS | SUPPORT | SPAM | AUTRE
    extracted_info: dict       # Infos structurées extraites
    faq_context: str           # Contexte RAG issu de la Knowledge_Base (Google Sheets)
    company_profile: str       # Profil entreprise (agent Enrichissement, Tavily + cache)
    draft_response: str        # Proposition rédigée pour le commercial (agent Stratège)
    # ── Mémoire long terme (lecture du CRM avant traitement) ──
    sender_history: str        # Résumé de l'historique de l'expéditeur (client récurrent)
    is_duplicate: bool         # True si l'expéditeur existe déjà dans l'onglet Leads
    gmail_message_id: Optional[str]  # ID Gmail (sérialisable) pour marquer l'e-mail traité
    action_status: str         # Message de résultat de l'écriture CRM (rempli par action_node)
    # ── Orchestration multi-agents (superviseur) ──
    next_agent: str            # Décision du superviseur : prochain worker ou "FINISH"
    completed_agents: Annotated[list, operator.add]  # Workers déjà exécutés (réducteur = concat)
    reasoning_log: Annotated[list, operator.add]     # Trace de raisonnement des agents (concat)


# Catégories valides. AUTRE = e-mail légitime mais hors périmètre commercial
# (candidature, partenariat, question générale) — à ne pas confondre avec du vrai SPAM.
CATEGORIES_VALIDES = {"DEMANDE_DEMO", "DEVIS", "SUPPORT", "SPAM", "AUTRE"}
# Catégories qui court-circuitent la génération de brouillon / l'écriture CRM.
CATEGORIES_SANS_SUITE = {"SPAM", "AUTRE"}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Nœud 1 — Classification (Llama 3.1 8B → vitesse maximale)
# ─────────────────────────────────────────────────────────────────────────────
def classifier_node(state: AgentState) -> dict:
    """Classe l'e-mail dans l'une des 5 catégories. Llama 8B = rapide + gratuit."""
    print("\n⚡ [Groq/Llama-8B] Classification de l'e-mail...")

    email = state["email_raw"]
    email_text = (
        f"Expéditeur : {email.get('sender', '')}\n"
        f"Objet : {email.get('subject', '')}\n"
        f"Corps : {email['body']}"
    )

    messages = [
        SystemMessage(content=(
            "Tu es un assistant commercial expert. "
            "Classe l'e-mail dans l'UNE des catégories suivantes et réponds UNIQUEMENT par ce mot :\n"
            "- DEMANDE_DEMO   → le prospect veut une démo ou une présentation\n"
            "- DEVIS          → demande un devis ou un tarif\n"
            "- SUPPORT        → client existant avec un problème technique\n"
            "- AUTRE          → e-mail légitime mais hors périmètre commercial "
            "(candidature/CV, partenariat, question générale)\n"
            "- SPAM           → message non sollicité, publicitaire ou frauduleux\n"
            "Réponds UNIQUEMENT par l'un de ces cinq mots, sans ponctuation ni explication."
        )),
        HumanMessage(content=email_text),
    ]

    response = fast_llm().invoke(messages)
    classification = response.content.strip().upper()
    # Sécurité : s'assurer que la réponse est valide. Fallback = AUTRE (plus sûr que SPAM
    # pour un message inconnu mais potentiellement légitime).
    if classification not in CATEGORIES_VALIDES:
        classification = "AUTRE"
    print(f"   → Résultat : {classification}")
    return {"classification": classification}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Nœud 2 — Mémoire long terme (lecture du CRM avant traitement)
# ─────────────────────────────────────────────────────────────────────────────
def memory_lookup_node(state: AgentState) -> dict:
    """
    Consulte l'onglet 'Leads' pour savoir si l'expéditeur est déjà connu.
    Alimente `sender_history` (personnalisation du brouillon) et `is_duplicate`
    (avertissement de doublon avant écriture). Aucun appel LLM.
    """
    print("\n🗃️  [Mémoire CRM] Recherche de l'expéditeur dans l'historique...")
    sender = state["email_raw"].get("sender", "")
    previous = sheets.find_leads_by_sender(sender)

    if not previous:
        print("   → Nouveau contact.")
        return {"sender_history": "", "is_duplicate": False}

    last = previous[-1]
    date = last.get("Date", "?")
    besoin = last.get("Besoin", last.get("Catégorie", "une demande précédente"))
    history = (
        f"Ce contact a déjà écrit {len(previous)} fois (dernier échange le {date} "
        f"à propos de : {besoin})."
    )
    print(f"   → Client récurrent : {len(previous)} entrée(s).")
    return {"sender_history": history, "is_duplicate": True}


# ─────────────────────────────────────────────────────────────────────────────
# Agent Connaissance (worker) — RAG "database-less", sémantique (Knowledge_Base)
# ─────────────────────────────────────────────────────────────────────────────
def connaissance_node(state: AgentState) -> dict:
    """
    Agent Connaissance : interroge la Knowledge_Base (Google Sheets) par recherche sémantique
    (embeddings Gemini + similarité cosinus, repli mots-clés) et remplit `faq_context`.
    Appelé par le superviseur (jamais pour SPAM/AUTRE). Mémoire long terme = Knowledge_Base.
    """
    print("\n📚 [Agent Connaissance] Recherche sémantique dans la Knowledge_Base...")
    email = state["email_raw"]
    query = f"{email.get('subject', '')} {email.get('body', '')}"
    context = sheets.search_knowledge_base_semantic(query)
    print(f"   → {'Contexte trouvé.' if context else 'Aucune correspondance.'}")
    reason = "Connaissance : contexte FAQ injecté." if context else "Connaissance : aucune correspondance FAQ."
    return {"faq_context": context, "completed_agents": ["connaissance"], "reasoning_log": [reason]}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Nœud 4 — Extraction structurée (Llama 70B → précision JSON)
# ─────────────────────────────────────────────────────────────────────────────
def extractor_node(state: AgentState) -> dict:
    """Extrait les informations clés au format JSON. Llama 70B = précision."""
    print("\n🧠 [Groq/Llama-70B] Extraction des informations clés...")

    email = state["email_raw"]
    attachment = state.get("attachment_text", "")
    
    email_text = (
        f"Expéditeur : {email.get('sender', '')}\n"
        f"Objet : {email.get('subject', '')}\n"
        f"Corps : {email['body']}\n"
    )
    if attachment:
        email_text += f"\n--- PIÈCE JOINTE ---\n{attachment}\n"

    messages = [
        SystemMessage(content=(
            "Tu es un assistant d'extraction de données. "
            "Lis l'e-mail et extrais les informations dans ce format JSON exact :\n"
            '{"entreprise": "...", "contact": "...", "urgence": "haute|moyenne|basse", "besoin_principal": "..."}\n'
            "Si une information est absente, mets null. "
            "Réponds UNIQUEMENT avec le JSON, sans markdown ni explication."
        )),
        HumanMessage(content=email_text),
    ]

    response = smart_llm().invoke(messages)
    try:
        extracted = json.loads(response.content.strip())
    except Exception:
        extracted = {"raw": response.content.strip()}

    print(f"   → Extrait : {extracted}")
    return {"extracted_info": extracted}


# ─────────────────────────────────────────────────────────────────────────────
# Clarification — raisonnement + question interactive à l'humain (interrupt dynamique)
# ─────────────────────────────────────────────────────────────────────────────
def clarification_node(state: AgentState) -> dict:
    """
    Si une information clé manque après extraction (besoin principal), l'agent met le graphe en pause
    et pose UNE question à l'humain via `interrupt()` (interrupt dynamique). La réponse est fusionnée
    dans `extracted_info`, puis le graphe reprend. Ignoré pour SPAM/AUTRE et quand l'info est présente.
    """
    if state["classification"] in CATEGORIES_SANS_SUITE:
        return {}

    info = state.get("extracted_info", {})
    besoin = info.get("besoin_principal")
    if besoin and str(besoin).strip().lower() not in ("", "null", "none", "n/a"):
        return {}  # info suffisante, aucune clarification nécessaire

    print("\n❓ [Clarification] Information manquante → question à l'humain...")
    # Au 1er passage : met en pause et renvoie la question. Au resume : `answer` = réponse humaine.
    answer = interrupt({
        "type": "clarification",
        "field": "besoin_principal",
        "question": "Le besoin principal du prospect n'est pas clair. Pouvez-vous le préciser ?",
    })

    merged = dict(info)
    merged["besoin_principal"] = str(answer).strip()
    print(f"   → Réponse humaine intégrée : {merged['besoin_principal']}")
    return {"extracted_info": merged,
            "reasoning_log": [f"Clarification : besoin précisé par l'humain → {merged['besoin_principal']}."]}


# ─────────────────────────────────────────────────────────────────────────────
# Agent Stratège (worker) — proposition commerciale (Llama 70B créatif)
# ─────────────────────────────────────────────────────────────────────────────
def stratege_node(state: AgentState) -> dict:
    """
    Agent Stratège : rédige une proposition commerciale personnalisée — accusé de réception, réponse
    factuelle via la FAQ, devis indicatif si des tarifs sont connus, et prochaine action concrète —
    en s'appuyant sur le profil entreprise (Enrichissement), la Knowledge_Base (Connaissance) et
    l'historique client (mémoire long terme). Toujours le dernier worker (imposé par le superviseur).
    """
    print("\n✍️  [Agent Stratège] Rédaction de la proposition commerciale...")

    email = state["email_raw"]
    info = state.get("extracted_info", {})
    faq_text = state.get("faq_context", "")
    history = state.get("sender_history", "")
    profile = state.get("company_profile", "")

    messages = [
        SystemMessage(content=(
            "Tu es un commercial senior. Rédige une proposition de réponse en français (4-6 phrases) qui :\n"
            "1. Accuse réception et personnalise selon le PROFIL ENTREPRISE si fourni.\n"
            "2. Répond factuellement via la BASE DE CONNAISSANCES (FAQ) ci-dessous quand c'est pertinent.\n"
            "3. Donne un devis/estimation indicatif si des tarifs figurent dans la FAQ (sinon propose un échange pour chiffrer).\n"
            "4. Propose une prochaine action concrète (appel, démo, devis détaillé).\n"
            "Si un HISTORIQUE CLIENT est fourni, adapte le ton (ex: « ravis de vous retrouver ») sans le citer.\n"
            "Professionnel et humain. Ne signe pas l'e-mail.\n\n"
            f"--- PROFIL ENTREPRISE ---\n{profile if profile else 'Inconnu.'}\n"
            f"--- BASE DE CONNAISSANCES (FAQ) ---\n{faq_text if faq_text else 'Aucune FAQ pertinente trouvée.'}\n"
            f"--- HISTORIQUE CLIENT ---\n{history if history else 'Nouveau contact.'}\n"
        )),
        HumanMessage(content=(
            f"Type de demande : {state['classification']}\n"
            f"Expéditeur : {email.get('sender', '')}\n"
            f"Informations extraites : {json.dumps(info, ensure_ascii=False)}\n"
            f"Message original : {email['body']}\n"
            f"Pièce jointe : {'(Présente)' if state.get('attachment_text') else '(Aucune)'}"
        )),
    ]

    response = creative_llm().invoke(messages)
    draft = response.content.strip()
    print(f"   → Proposition rédigée ({len(draft)} caractères)")
    return {"draft_response": draft, "completed_agents": ["stratege"],
            "reasoning_log": [f"Stratège : proposition rédigée ({len(draft)} car.)."]}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Nœud 6 — Action (écriture CRM). S'exécute UNIQUEMENT après validation humaine,
#    car le graphe est interrompu (interrupt_before) juste avant ce nœud.
# ─────────────────────────────────────────────────────────────────────────────
def action_node(state: AgentState) -> dict:
    """
    Écrit le lead validé dans l'onglet 'Leads' puis, si l'e-mail provient de Gmail,
    le marque comme traité. Ne place jamais d'objet non sérialisable dans l'état :
    le service Gmail est reconstruit ici à partir du token en cache.
    """
    print("\n📥 [Action] Écriture du lead validé dans le CRM...")
    email = state["email_raw"]
    sheets.append_lead(
        email_classification=state["classification"],
        extracted_info=state.get("extracted_info", {}),
        sender=email.get("sender", ""),
        draft=state.get("draft_response", ""),
    )

    status = "Lead ajouté au CRM."
    msg_id = state.get("gmail_message_id")
    if msg_id:
        try:
            import gmail_reader
            service = gmail_reader.get_gmail_service()
            gmail_reader.mark_as_processed(service, msg_id)
            status += " E-mail Gmail marqué comme traité."
        except Exception as e:
            status += f" (Échec du marquage Gmail : {e})"
    print(f"   → {status}")
    return {"action_status": status}


# ─────────────────────────────────────────────────────────────────────────────
# Superviseur — oriente dynamiquement vers les agents spécialisés (Llama-8B + garde-fous)
# ─────────────────────────────────────────────────────────────────────────────
def _supervisor_choose(state: AgentState, options: list) -> str:
    """Le superviseur (Llama-8B) choisit le prochain agent parmi `options` ; repli = options[0]."""
    email = state["email_raw"]
    messages = [
        SystemMessage(content=(
            "Tu es le SUPERVISEUR d'une équipe d'agents commerciaux. Choisis le prochain agent à activer.\n"
            "- enrichissement : recherche des infos sur l'entreprise de l'expéditeur (utile si domaine pro)\n"
            "- connaissance   : cherche tarifs/délais/politiques dans la base de connaissances\n"
            "- stratege       : rédige la proposition finale (à choisir en DERNIER, infos réunies)\n"
            f"Déjà exécutés : {state.get('completed_agents', [])}\n"
            f"Options disponibles : {options}\n"
            "Réponds UNIQUEMENT par le nom d'un agent de la liste des options."
        )),
        HumanMessage(content=(
            f"Expéditeur : {email.get('sender', '')}\n"
            f"Objet : {email.get('subject', '')}\n"
            f"Corps : {email.get('body', '')}"
        )),
    ]
    choice = fast_llm().invoke(messages).content.strip().lower()
    for option in options:
        if option in choice:
            return option
    return options[0]


def supervisor_node(state: AgentState) -> dict:
    """
    Superviseur : oriente vers le prochain agent spécialisé, avec des garde-fous déterministes
    (SPAM/AUTRE → FINISH ; jamais 2× le même agent ; `stratege` en dernier ; FINISH une fois la
    proposition rédigée). Émet une trace de raisonnement dans `reasoning_log`.
    """
    print("\n🧭 [Superviseur] Décision du prochain agent...")
    classification = state["classification"]
    completed = state.get("completed_agents", [])

    if classification in CATEGORIES_SANS_SUITE:
        print(f"   → FINISH ({classification})")
        return {"next_agent": "FINISH",
                "reasoning_log": [f"Superviseur : e-mail {classification} (hors périmètre) → FINISH."]}

    if "stratege" in completed:
        print("   → FINISH (proposition prête)")
        return {"next_agent": "FINISH",
                "reasoning_log": ["Superviseur : proposition rédigée → FINISH (attente validation)."]}

    helpers_left = [w for w in ("enrichissement", "connaissance") if w not in completed]
    if helpers_left:
        # Le LLM peut lancer un helper ou décider de passer directement au stratège.
        choice = _supervisor_choose(state, helpers_left + ["stratege"])
    else:
        choice = "stratege"

    print(f"   → {choice}")
    return {"next_agent": choice,
            "reasoning_log": [f"Superviseur : prochain agent = {choice} (déjà faits : {completed or 'aucun'})."]}


# ─────────────────────────────────────────────────────────────────────────────
# Agent Enrichissement (worker) — profil entreprise (Tavily + cache Sheets long terme)
# ─────────────────────────────────────────────────────────────────────────────
def enrichissement_node(state: AgentState) -> dict:
    """
    Agent Enrichissement : profil de l'entreprise de l'expéditeur. Mémoire hybride — lit d'abord le
    cache Sheets (`Enrichissement_Cache`), sinon interroge Tavily puis met en cache. Dégradation
    gracieuse (renvoie "" si domaine générique / clé absente / erreur).
    """
    print("\n🔎 [Agent Enrichissement] Recherche d'informations sur l'entreprise...")
    sender = state["email_raw"].get("sender", "")
    profile = enrichment.research_company(sender)
    print(f"   → {'Profil obtenu.' if profile else 'Aucun profil.'}")
    reason = ("Enrichissement : profil entreprise obtenu." if profile
              else "Enrichissement : aucun profil (domaine générique ou indisponible).")
    return {"company_profile": profile, "completed_agents": ["enrichissement"], "reasoning_log": [reason]}


# ─────────────────────────────────────────────────────────────────────────────
# Construction du graphe LangGraph (superviseur + équipe, mémoire hybride)
# ─────────────────────────────────────────────────────────────────────────────
#
#   START → [classifier] → [memory_lookup] → [extractor] → [SUPERVISEUR] ⇄ workers
#              (8B)          (CRM read)         (70B)         (8B, routage)
#                                                              ├─ enrichissement (Tavily + cache)
#                                                              ├─ connaissance   (RAG sémantique)
#                                                              └─ stratege        (proposition)
#          [SUPERVISEUR] --FINISH--> --interrupt-- [action] → END
#                                     (validation humaine)   (write CRM + Gmail)
#
# - Mémoire court terme : MemorySaver conserve l'état partagé (accessible à tous les agents)
#   pendant la pause de validation.
# - Mémoire long terme  : Google Sheets (Leads = CRM, FAQ = Knowledge_Base, Enrichissement_Cache).
#
workflow = StateGraph(AgentState)
workflow.add_node("classifier", classifier_node)
workflow.add_node("memory_lookup", memory_lookup_node)
workflow.add_node("extractor", extractor_node)
workflow.add_node("clarification", clarification_node)
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("enrichissement", enrichissement_node)
workflow.add_node("connaissance", connaissance_node)
workflow.add_node("stratege", stratege_node)
workflow.add_node("action", action_node)

workflow.add_edge(START, "classifier")
workflow.add_edge("classifier", "memory_lookup")
workflow.add_edge("memory_lookup", "extractor")
workflow.add_edge("extractor", "clarification")
workflow.add_edge("clarification", "supervisor")

# Le superviseur route dynamiquement vers un worker (qui lui revient) ou vers l'action (FINISH).
workflow.add_conditional_edges(
    "supervisor",
    lambda s: s["next_agent"],
    {
        "enrichissement": "enrichissement",
        "connaissance": "connaissance",
        "stratege": "stratege",
        "FINISH": "action",
    },
)
workflow.add_edge("enrichissement", "supervisor")
workflow.add_edge("connaissance", "supervisor")
workflow.add_edge("stratege", "supervisor")
workflow.add_edge("action", END)

# Checkpointer = mémoire court terme ; interrupt_before = pause Human-in-the-loop
# juste avant l'écriture CRM. L'UI reprend le graphe (invoke(None, config)) après « Valider ».
checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer, interrupt_before=["action"])


# ─────────────────────────────────────────────────────────────────────────────
# 9. Test manuel avec des faux e-mails (mock) — s'arrête à l'interruption, sans écrire au CRM
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    exemples = [
        {
            "sender": "alice.martin@startup-tech.fr",
            "subject": "Demande de démonstration",
            "body": (
                "Bonjour, je suis directrice des opérations chez Startup Tech. "
                "Nous cherchons un outil pour automatiser notre gestion commerciale. "
                "Serait-il possible d'organiser une démo la semaine prochaine ? "
                "Nous sommes disponibles mardi ou jeudi après-midi."
            ),
        },
        {
            "sender": "bob.dupont@pme-industrie.com",
            "subject": "Demande de devis — 50 licences",
            "body": "Bonjour, pouvez-vous m'envoyer un devis pour 50 licences Enterprise ? Merci.",
        },
        {
            "sender": "promo@spammer.net",
            "subject": "Gagnez 1000€ maintenant !",
            "body": "Cliquez ici pour récupérer votre cadeau. Offre limitée !",
        },
        {
            "sender": "jean.candidat@gmail.com",
            "subject": "Candidature — Stage développeur",
            "body": "Bonjour, je vous envoie ma candidature spontanée pour un stage. Mon CV est en pièce jointe.",
        },
    ]

    for i, faux_email in enumerate(exemples, 1):
        print(f"\n{'='*60}")
        print(f"  TEST {i} — {faux_email['sender']}")
        print(f"  Objet : {faux_email['subject']}")
        print(f"{'='*60}")

        # Chaque e-mail = un fil (thread) distinct pour le checkpointer (mémoire court terme)
        config = {"configurable": {"thread_id": f"cli-test-{i}"}}
        # Le graphe s'arrête avant 'action' (interrupt_before) : aucune écriture CRM en démo.
        output = app.invoke({"email_raw": faux_email}, config=config)

        print(f"\n{'─'*60}")
        print(f"  📬 Catégorie  : {output['classification']}")
        info = output.get("extracted_info", {})
        print(f"  🏢 Entreprise : {info.get('entreprise', 'N/A')}")
        print(f"  👤 Contact    : {info.get('contact', 'N/A')}")
        print(f"  🔥 Urgence    : {info.get('urgence', 'N/A')}")
        print(f"  🧭 Agents     : {output.get('completed_agents', []) or 'aucun (hors périmètre)'}")
        if output.get("company_profile"):
            print(f"  🔎 Profil     : {output['company_profile'][:120]}...")
        if output.get("sender_history"):
            print(f"  🗃️  Historique : {output['sender_history']}")
        if output.get("reasoning_log"):
            print("  🧠 Raisonnement :")
            for line in output["reasoning_log"]:
                print(f"       • {line}")
        if output.get("draft_response"):
            print(f"\n  📝 Proposition :\n{output['draft_response']}")

        # Preuve du Human-in-the-loop : le graphe est en pause juste avant l'écriture CRM.
        snapshot = app.get_state(config)
        if snapshot.next:
            print(f"\n  ⏸️  Graphe en pause avant : {snapshot.next} (validation humaine requise)")
        print()

    print("✅ ACAM v2 opérationnel — superviseur + équipe + mémoire hybride + RAG + interrupt.")

import os
import uuid
import streamlit as st
from langgraph.types import Command
import app as aca_graph
import audit_log
import followup_store
import gmail_reader
import ingest
import queue_store
import sheets
from pdf_reader import extract_text_from_pdf

st.set_page_config(
    page_title="ACA — Assistant commercial",
    page_icon=":material/smart_toy:",
    layout="wide",
)


def _check_auth() -> bool:
    """
    Gate mot de passe optionnel (`ACA_UI_PASSWORD`) : usage solo/petite équipe, pas un vrai système
    multi-utilisateurs. Sans variable définie, l'UI reste ouverte comme avant (mode développement) —
    même dégradation gracieuse que les autres options (Tavily, Gemini...).
    """
    required = os.getenv("ACA_UI_PASSWORD")
    if not required or st.session_state.get("authed"):
        return True
    st.title("Assistant commercial agentique (ACA)")
    pwd = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter", type="primary"):
        if pwd == required:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.", icon=":material/error:")
    return False


if not _check_auth():
    st.stop()

st.title("Assistant commercial agentique (ACA)")
st.caption("Pré-lecture et qualification des e-mails entrants — validation humaine avant écriture CRM.")

# Valeurs par défaut du formulaire (peuvent être écrasées par un import Gmail)
st.session_state.setdefault("email_sender", "client@entreprise.com")
st.session_state.setdefault("email_subject", "Cahier des charges pour refonte outil interne")
st.session_state.setdefault(
    "email_body",
    "Bonjour,\n\nJe suis intéressé par votre solution. Quel est le délai normal de livraison ?\n\nCordialement.",
)
st.session_state.setdefault("gmail_attachment_text", "")
st.session_state.setdefault("gmail_message_id", None)
st.session_state.setdefault("gmail_thread_id", None)

# Étapes du graphe LangGraph — libellés affichés en direct pendant le stream (par nom de nœud).
NODE_STEPS = {
    "classifier": ("Classification de l'e-mail", ":material/label:"),
    "memory_lookup": ("Mémoire CRM : historique de l'expéditeur", ":material/history:"),
    "extractor": ("Extraction des informations clés", ":material/data_object:"),
    "clarification": ("Vérification — besoin d'une précision ?", ":material/help:"),
    "supervisor": ("Le superviseur oriente l'équipe d'agents", ":material/hub:"),
    "enrichissement": ("Agent Enrichissement : profil entreprise", ":material/travel_explore:"),
    "connaissance": ("Agent Connaissance : RAG sémantique (base de connaissances)", ":material/search:"),
    "veille": ("Agent Veille : recherche web (FAQ sans correspondance)", ":material/travel_explore:"),
    "stratege": ("Agent Stratège : rédaction de la proposition", ":material/edit_note:"),
    "notification": ("Notification de l'équipe commerciale", ":material/notifications_active:"),
}

CATEGORY_STYLE = {
    "DEMANDE_DEMO": ("green", ":material/videocam:"),
    "DEVIS": ("blue", ":material/request_quote:"),
    "SUPPORT": ("orange", ":material/support_agent:"),
}


def advance_graph(payload):
    """
    Fait avancer le graphe (entrée initiale, ou `Command(resume=...)` après une clarification),
    en affichant la progression nœud par nœud, puis met à jour l'état de session :
    - `result` : valeurs courantes de l'état ;
    - `pending_clarification` : la question en attente (si le graphe s'est arrêté sur un interrupt
      dynamique), sinon None (le graphe est alors en pause avant `action`, prêt pour « Valider »).
    """
    config = {"configurable": {"thread_id": st.session_state.thread_id}}
    with st.status("Analyse en cours...", expanded=True) as status:
        seen_nodes = set()  # le superviseur repasse plusieurs fois → chaque étape affichée une seule fois
        for step in aca_graph.app.stream(payload, config=config, stream_mode="updates"):
            node_name = next(iter(step))
            if node_name == "__interrupt__" or node_name in seen_nodes:
                continue
            seen_nodes.add(node_name)
            label, icon = NODE_STEPS.get(node_name, (node_name, ":material/bolt:"))
            st.write(f"{icon} {label}")
        status.update(label="Analyse terminée", state="complete")

    _sync_result(st.session_state.thread_id)


def _sync_result(thread_id: str) -> None:
    """Recharge `result`/`pending_clarification` depuis l'état courant du graphe pour ce thread."""
    config = {"configurable": {"thread_id": thread_id}}
    state = aca_graph.app.get_state(config)
    st.session_state.result = state.values
    # `state.interrupts` non vide = clarification dynamique en attente ; sinon pause avant action.
    st.session_state.pending_clarification = state.interrupts[0].value if state.interrupts else None


def load_queued_thread(thread_id: str) -> None:
    """
    Charge dans la session une analyse déjà traitée par le poller (poller.py) : le graphe a déjà
    tourné jusqu'à la pause de validation dans un autre processus, donc pas de `stream()` ici —
    juste une lecture de l'état persistant (`checkpoints.sqlite`).
    """
    st.session_state.thread_id = thread_id
    _sync_result(thread_id)


with st.sidebar:
    st.text_input(
        "Validé par", key="validator_name", placeholder="Ton nom (traçabilité)",
        help="Enregistré dans le journal d'audit local à chaque validation (audit_log.py).",
    )
    st.divider()
    pending_queue = queue_store.list_pending()
    st.subheader(f"File d'attente ({len(pending_queue)})", anchor=False)
    st.caption(
        "E-mails traités automatiquement par le poller en arrière-plan (`poller.py`), en attente "
        "de validation humaine."
    )
    if not pending_queue:
        st.caption("Aucune analyse en attente pour le moment.")
    for item in pending_queue:
        with st.container(border=True):
            st.markdown(f"**{item['subject']}**")
            st.caption(f"{item['sender']} · {item['created_at']}")
            if st.button("Ouvrir", icon=":material/open_in_new:", key=f"open_{item['thread_id']}"):
                load_queued_thread(item["thread_id"])
                st.rerun()

    st.divider()
    st.subheader("Import Gmail", anchor=False)
    if st.button("Rechercher les e-mails non lus", icon=":material/mail:"):
        with st.spinner("Connexion à Gmail..."):
            try:
                st.session_state.gmail_service = gmail_reader.get_gmail_service()
                st.session_state.gmail_unread = gmail_reader.list_unread_emails(st.session_state.gmail_service)
            except Exception as e:
                st.error(f"Erreur de connexion Gmail : {e}", icon=":material/error:")

    if st.session_state.get("gmail_unread"):
        options = {f"{m['subject']} — {m['sender']}": m["id"] for m in st.session_state.gmail_unread}
        choice = st.selectbox("E-mails non lus", list(options.keys()))
        if st.button("Charger cet e-mail", icon=":material/download:"):
            with st.spinner("Chargement de l'e-mail..."):
                try:
                    email = gmail_reader.get_email(st.session_state.gmail_service, options[choice])
                    st.session_state.email_sender = email["sender"]
                    st.session_state.email_subject = email["subject"]
                    st.session_state.email_body = email["body"]
                    st.session_state.gmail_message_id = email["id"]
                    st.session_state.gmail_thread_id = email["gmail_thread_id"]
                    st.session_state.gmail_attachment_text = (
                        extract_text_from_pdf(email["attachment_pdf"]) if email["attachment_pdf"] else ""
                    )
                except Exception as e:
                    st.error(f"Erreur lors du chargement de l'e-mail : {e}", icon=":material/error:")
                else:
                    st.rerun()

    st.divider()
    st.subheader("Base de connaissances", anchor=False)
    st.caption("Alimenter la base (onglet Knowledge_Base) depuis un document — remplace un Vector DB.")
    kb_file = st.file_uploader(
        "Document (PDF, Markdown, texte)",
        type=["pdf", "md", "txt"],
        label_visibility="collapsed",
        key="kb_upload",
    )
    kb_replace = st.toggle("Remplacer le contenu existant", value=False)
    if st.button("Ingérer dans la base", icon=":material/library_add:", disabled=kb_file is None):
        with st.spinner("Découpage du document en Q/R et écriture dans Sheets..."):
            try:
                n = ingest.ingest_document(
                    kb_file.getvalue(), mode="replace" if kb_replace else "append"
                )
            except Exception as e:
                st.error(f"Erreur d'ingestion : {e}", icon=":material/error:")
            else:
                if n:
                    st.success(f"{n} ligne(s) ajoutée(s) à la base de connaissances.",
                               icon=":material/check_circle:")
                else:
                    st.warning("Aucune paire Q/R extraite du document.", icon=":material/warning:")

    st.divider()
    pending_rows = sheets.get_pending_knowledge_rows()
    st.subheader(f"FAQ en attente ({len(pending_rows)})", anchor=False)
    st.caption(
        "Réponses trouvées en ligne par l'agent Veille — invisibles du RAG jusqu'à validation "
        "humaine (contenu web non vérifié, cf. CLAUDE.md)."
    )
    for row in pending_rows:
        with st.container(border=True):
            st.markdown(f"**Q :** {row['question']}")
            st.caption(f"R : {row['reponse']}")
            col_ok, col_ko = st.columns(2)
            if col_ok.button("Valider", icon=":material/check:", key=f"approve_{row['row_index']}"):
                sheets.approve_knowledge_row(row["row_index"])
                st.rerun()
            if col_ko.button("Rejeter", icon=":material/close:", key=f"reject_{row['row_index']}"):
                sheets.reject_knowledge_row(row["row_index"])
                st.rerun()

    st.divider()
    st.caption(
        "Modèles : Llama 3.1 8B (routage) · Llama 3.3 70B (extraction/rédaction) · "
        "Gemini embeddings (RAG sémantique)"
    )

# Interface principale pour simuler/entrer la réception d'un e-mail
with st.container(border=True):
    st.subheader("Nouvel e-mail entrant", anchor=False)

    col1, col2 = st.columns(2)
    with col1:
        sender = st.text_input("Expéditeur", key="email_sender")
        subject = st.text_input("Objet", key="email_subject")
        body = st.text_area("Corps du message", height=150, key="email_body")

    with col2:
        st.markdown("**Pièce jointe (optionnel)**")
        uploaded_file = st.file_uploader(
            "Importer un PDF (cahier des charges, brief...)",
            type=["pdf"],
            label_visibility="collapsed",
        )
        if st.session_state.gmail_attachment_text:
            st.caption(":material/attach_file: Pièce jointe PDF récupérée automatiquement depuis Gmail.")

    launch = st.button("Lancer l'analyse IA", type="primary", icon=":material/bolt:")

# Bouton déclencheur de l'agent
if launch:
    # Extraction texte de la potentielle pièce jointe (upload manuel prioritaire sur l'import Gmail)
    attachment_text = ""
    if uploaded_file:
        with st.spinner("Extraction du texte du PDF..."):
            attachment_text = extract_text_from_pdf(uploaded_file.getvalue())
    elif st.session_state.gmail_attachment_text:
        attachment_text = st.session_state.gmail_attachment_text

    # Construction de l'entrée pour le graphe
    graphe_input = {
        "email_raw": {
            "sender": sender,
            "subject": subject,
            "body": body,
        },
        "attachment_text": attachment_text,
        # ID Gmail (ou None) : consommé par action_node pour marquer l'e-mail traité après validation
        "gmail_message_id": st.session_state.gmail_message_id,
        # Vrai threadId Gmail (ou None) : consommé après validation par followup_store.track()
        "gmail_thread_id": st.session_state.gmail_thread_id,
    }

    # Chaque analyse = un fil (thread) distinct pour le checkpointer (mémoire court terme).
    # Le graphe peut s'interrompre en route (clarification) puis avant 'action' (validation).
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.sender = sender
    advance_graph(graphe_input)

# Si on a un résultat d'analyse, on l'affiche et on demande validation (Human-in-the-loop)
if "result" in st.session_state:
    res = st.session_state.result

    st.subheader("Résultat de l'IA", anchor=False)

    # --- Clarification (interrupt dynamique) : l'agent pose une question AVANT de poursuivre ---
    pending = st.session_state.get("pending_clarification")
    if pending:
        st.warning(pending.get("question", "Une précision est nécessaire."), icon=":material/help:")
        clar_answer = st.text_input("Votre réponse à l'agent", key="clarif_answer")
        if st.button("Répondre à l'agent", type="primary", icon=":material/reply:"):
            if clar_answer.strip():
                advance_graph(Command(resume=clar_answer.strip()))
                st.rerun()
            else:
                st.error("Merci de saisir une réponse avant de continuer.")
        st.stop()  # on n'affiche pas la fiche / la validation tant que la question n'a pas de réponse

    classif = res.get("classification", "INCONNU")

    if classif == "SPAM":
        st.error("E-mail classé comme spam. Aucune action requise.", icon=":material/block:")

    elif classif == "AUTRE":
        st.info(
            "E-mail hors périmètre commercial (candidature, partenariat, question générale). "
            "Aucune fiche CRM n'est créée automatiquement.",
            icon=":material/help:",
        )

    else:
        color, icon = CATEGORY_STYLE.get(classif, ("gray", ":material/label:"))
        st.badge(classif.replace("_", " ").capitalize(), icon=icon, color=color)

        # --- Mémoire long terme : bandeaux client récurrent / doublon ---
        if res.get("sender_history"):
            st.info(res["sender_history"], icon=":material/history:")
        if res.get("is_duplicate"):
            st.warning(
                "Cet expéditeur existe déjà dans le CRM. Vérifiez avant d'ajouter une nouvelle "
                "ligne (risque de doublon).",
                icon=":material/warning:",
            )

        info = res.get("extracted_info", {})

        with st.container(border=True):
            st.markdown("##### Fiche prospect")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Entreprise", info.get("entreprise") or "—")
            col_b.metric("Contact", info.get("contact") or "—")
            urgence = (info.get("urgence") or "").lower()
            urgence_color = {"haute": "red", "moyenne": "orange", "basse": "green"}.get(urgence, "gray")
            with col_c:
                st.caption("Urgence")
                st.badge(urgence.capitalize() if urgence else "—", color=urgence_color)
            st.caption("Besoin principal")
            st.write(info.get("besoin_principal") or "—")
            if res.get("company_profile"):
                st.caption("Profil entreprise (agent Enrichissement)")
                st.write(res["company_profile"])

        # Trace de raisonnement de l'équipe d'agents (superviseur + workers)
        if res.get("reasoning_log"):
            with st.expander("Raisonnement de l'équipe d'agents", icon=":material/network_node:"):
                for line in res["reasoning_log"]:
                    st.markdown(f"- {line}")

        with st.container(border=True):
            st.markdown("##### Proposition rédigée")
            st.write(res.get("draft_response") or "Aucune proposition générée.")

        # --- PHASE 3 : VALIDATION HUMAINE (reprise du graphe interrompu) ---
        st.subheader("Validation humaine", anchor=False)
        st.caption("Si la recommandation de l'IA est correcte, validez pour envoyer dans le CRM.")

        if st.button("Valider et ajouter au CRM", type="primary", icon=":material/check_circle:"):
            with st.spinner("Reprise du graphe et écriture dans Google Sheets..."):
                try:
                    # Le graphe était en pause avant 'action'. On le reprend (invoke None) :
                    # action_node écrit dans Leads + marque l'e-mail Gmail comme traité.
                    graph_config = {"configurable": {"thread_id": st.session_state.thread_id}}
                    final = aca_graph.app.invoke(None, config=graph_config)
                    action_status = final.get("action_status", "Lead ajouté au CRM.")
                    st.success(action_status, icon=":material/check_circle:")
                    # Retire l'entrée de la file d'attente si elle en venait (no-op sinon).
                    queue_store.mark_validated(st.session_state.thread_id)
                    # Traçabilité minimale : qui a validé, quoi, quand (audit_log.py).
                    audit_log.log_validation(
                        st.session_state.thread_id, st.session_state.get("validator_name", ""),
                        res.get("classification", ""), res.get("email_raw", {}).get("sender", ""),
                    )
                    # Suivi des relances (no-op si pas de fil Gmail — saisie manuelle par exemple).
                    followup_store.track(
                        st.session_state.thread_id, res.get("gmail_thread_id"),
                        res.get("email_raw", {}).get("sender", ""),
                        res.get("email_raw", {}).get("subject", ""),
                    )
                    # On efface le résultat pour passer au suivant
                    st.session_state.gmail_attachment_text = ""
                    st.session_state.gmail_message_id = None
                    st.session_state.gmail_thread_id = None
                    st.session_state.pending_clarification = None
                    del st.session_state.result
                except Exception as e:
                    st.error(f"Erreur technique lors de la validation : {e}", icon=":material/error:")

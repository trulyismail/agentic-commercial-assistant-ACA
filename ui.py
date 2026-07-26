import hmac
import os
import time
import traceback
import uuid
import streamlit as st
from langgraph.types import Command
from langchain_core.callbacks import UsageMetadataCallbackHandler
from aca.storage import analytics_store, audit_log, config_store, followup_store, queue_store, user_store
from aca.core import app as aca_graph
from aca.core import demo
from aca.core import graph_topology
from aca.core import prod_check
from aca.core import session as aca_session
from aca.core.auth_lockout import lockout_remaining_seconds, next_lockout_seconds
from aca.integrations import gmail_reader, sheets
from aca.ingestion import ingest

st.set_page_config(
    page_title="ACA — Assistant commercial",
    page_icon=":material/smart_toy:",
    layout="wide",
)


DEV_USERNAME = "(dev)"  # identité de repli quand aucun compte n'existe (mode développement)


def _register_failure() -> None:
    """Incrémente le compteur d'échecs et arme le verrou progressif (US-41) le cas échéant."""
    st.session_state.auth_failed_attempts += 1
    lockout = next_lockout_seconds(st.session_state.auth_failed_attempts)
    if lockout > 0:
        st.session_state.auth_locked_until = time.time() + lockout
        st.error(
            f"Identifiants incorrects. Trop de tentatives : verrouillé {int(lockout)} s.",
            icon=":material/error:",
        )
    else:
        st.error("Identifiants incorrects.", icon=":material/error:")


def _open_session(username: str, role: str) -> None:
    """Ouvre la session et rejoue le script (le reste de la page s'affiche alors authentifié)."""
    st.session_state.session = aca_session.new_session(username, role, time.time())
    st.session_state.auth_failed_attempts = 0
    st.rerun()


def _check_auth() -> bool:
    """
    Contrôle d'accès de l'UI, en trois modes selon ce qui est configuré :

    1. **Comptes nominatifs** (`user_store.has_users()`) — identifiant + mot de passe haché, rôle
       `admin`/`operator` (§15.1.6). C'est le mode recommandé : le journal d'audit devient
       attribuable et les fonctions d'administration (réglages, curation de la base de
       connaissances, comptes) sont réellement séparées de la validation quotidienne.
    2. **Secret partagé** (`ACA_UI_PASSWORD`, aucun compte créé) — l'ancien gate, conservé pour ne
       pas casser les déploiements existants ; il ouvre une session `admin` anonyme.
    3. **Ouvert** (ni compte ni mot de passe) — mode développement, inchangé.

    §14 (US-41) : verrou progressif après plusieurs échecs (`aca.core.auth_lockout`), sans lui un
    bot pouvait tester des mots de passe aussi vite que Streamlit rejoue le script.
    §15.1.7 : la session porte désormais un TTL absolu et un délai d'inactivité
    (`aca.core.session`) — avant, `authed = True` restait vrai tant que l'onglet vivait.
    """
    now = time.time()
    current = st.session_state.get("session")
    if current:
        reason = aca_session.expiry_reason(current, now)
        if reason is None:
            aca_session.touch(current, now)
            return True
        st.session_state.session = None
        st.session_state.expired_reason = reason

    accounts_exist = user_store.has_users()
    shared_password = os.getenv("ACA_UI_PASSWORD")
    if not accounts_exist and not shared_password:
        st.session_state.session = aca_session.new_session(DEV_USERNAME, user_store.ROLE_ADMIN, now)
        return True

    st.session_state.setdefault("auth_failed_attempts", 0)
    st.session_state.setdefault("auth_locked_until", 0.0)

    st.title("Assistant commercial agentique (ACA)")

    expired = st.session_state.pop("expired_reason", None)
    if expired == "idle":
        st.info("Session fermée après une période d'inactivité. Reconnectez-vous.",
                icon=":material/timer_off:")
    elif expired == "absolute":
        st.info("Session expirée. Reconnectez-vous.", icon=":material/timer_off:")

    remaining = lockout_remaining_seconds(st.session_state.auth_locked_until, now)
    if remaining > 0:
        st.error(
            f"Trop de tentatives échouées. Réessayez dans {int(remaining) + 1} s.",
            icon=":material/lock_clock:",
        )
        return False

    if accounts_exist:
        username = st.text_input("Identifiant")
        pwd = st.text_input("Mot de passe", type="password")
        if st.button("Se connecter", type="primary"):
            # `verify_credentials` compare en temps constant ET consomme le même temps de calcul
            # sur un compte inexistant (cf. `_DUMMY_HASH`) : ni le mot de passe, ni la liste des
            # identifiants valides ne fuient par chronométrage.
            account = user_store.verify_credentials(username, pwd)
            if account:
                _open_session(account["username"], account["role"])
            else:
                _register_failure()
        return False

    pwd = st.text_input("Mot de passe", type="password")
    if st.button("Se connecter", type="primary"):
        # Comparaison à temps constant : un `==` sur un secret fuit sa longueur/préfixe par timing.
        if hmac.compare_digest(pwd.encode(), shared_password.encode()):
            _open_session(DEV_USERNAME, user_store.ROLE_ADMIN)
        else:
            _register_failure()
    return False


def _current_user() -> dict:
    """Session authentifiée courante (`username`, `role`, horodatages)."""
    return st.session_state.get("session") or {}


def _can(permission: str) -> bool:
    """L'utilisateur connecté détient-il `permission` ? (cf. `user_store.ROLE_PERMISSIONS`)"""
    return user_store.can(_current_user().get("role"), permission)


def _safe_error(message: str, exc: Exception) -> None:
    """
    Affiche un message d'erreur générique et journalise le détail côté serveur (§15.3.2).

    Interpoler `{exc}` dans l'UI, comme le faisait ce fichier, recopie à l'écran le texte brut de
    l'exception — qui, pour une erreur d'API, contient régulièrement l'URL appelée, des en-têtes,
    voire un fragment de clé. Le détail complet reste disponible dans la console qui a lancé
    Streamlit ; l'utilisateur, lui, voit une phrase actionnable.
    """
    print(f"[ACA] {message}: {exc.__class__.__name__}: {exc}")
    traceback.print_exc()
    st.error(f"{message}. Détail technique consigné côté serveur.", icon=":material/error:")


# §15.1.5 : en `ACA_ENV=production`, une configuration ouverte fait échouer le démarrage plutôt que
# de servir une UI sans garde. En développement (défaut), no-op total.
prod_check.enforce()

if not _check_auth():
    st.stop()

# Les faiblesses de configuration ne sont montrées qu'aux administrateurs : elles décrivent
# précisément ce qui n'est PAS protégé, une information à ne pas afficher à tout venant.
if _can(user_store.PERM_MANAGE_USERS):
    _security_problems = prod_check.check()
    if _security_problems:
        with st.expander(
            f"Sécurité : {len(_security_problems)} point(s) à corriger avant une mise en ligne",
            icon=":material/shield:",
        ):
            st.caption(
                "Sans conséquence en usage local (mode développement). À corriger avant toute "
                "exposition publique — cf. `docs/DEPLOYMENT_HARDENING.md`."
            )
            for _problem in _security_problems:
                st.markdown(f"- {_problem}")

st.title("Assistant commercial agentique (ACA)")
_subtitle = (
    "Pré-lecture et qualification des e-mails entrants — validation humaine avant écriture CRM."
)
if demo.is_enabled():
    # Badge permanent, pas seulement dans l'onglet : pendant une démonstration, l'écran est parfois
    # projeté ou partagé et il ne doit jamais y avoir d'ambiguïté sur le fait que rien n'est réel.
    _subtitle += "  ·  🧪 **MODE DÉMONSTRATION** (modèle simulé, écritures CRM bloquées)"
st.caption(_subtitle)

# Valeurs par défaut du formulaire (peuvent être écrasées par un import Gmail)
st.session_state.setdefault("email_sender", "client@entreprise.com")
st.session_state.setdefault("email_subject", "Cahier des charges pour refonte outil interne")
st.session_state.setdefault(
    "email_body",
    "Bonjour,\n\nJe suis intéressé par votre solution. Quel est le délai normal de livraison ?\n\nCordialement.",
)
st.session_state.setdefault("gmail_attachments_raw", [])
st.session_state.setdefault("gmail_message_id", None)
st.session_state.setdefault("gmail_thread_id", None)

# Étapes du graphe LangGraph — libellés affichés en direct pendant le stream (par nom de nœud).
NODE_STEPS = {
    "ingestion": ("Extraction des pièces jointes", ":material/attach_file:"),
    "classifier": ("Classification de l'e-mail", ":material/label:"),
    "memory_lookup": ("Mémoire CRM : historique de l'expéditeur", ":material/history:"),
    "extractor": ("Extraction des informations clés", ":material/data_object:"),
    "clarification": ("Vérification — besoin d'une précision ?", ":material/help:"),
    "supervisor": ("Le superviseur oriente l'équipe d'agents", ":material/hub:"),
    "enrichissement": ("Agent Enrichissement : profil entreprise", ":material/travel_explore:"),
    "connaissance": ("Agent Connaissance : RAG sémantique (base de connaissances)", ":material/search:"),
    "veille": ("Agent Veille : recherche web (FAQ sans correspondance)", ":material/travel_explore:"),
    "stratege": ("Agent Stratège : rédaction de la proposition", ":material/edit_note:"),
    "routing": ("Routage vers l'équipe compétente (SUPPORT/AUTRE)", ":material/forward_to_inbox:"),
    "notification": ("Notification de l'équipe commerciale", ":material/notifications_active:"),
}

CATEGORY_STYLE = {
    "DEMANDE_DEMO": ("green", ":material/videocam:"),
    "DEVIS": ("blue", ":material/request_quote:"),
}

# Graphe visuel (§12 item 5) : rendu via st.graphviz_chart (viz.js côté navigateur, aucune
# dépendance système Graphviz requise).
#
# §16.1.6 — la topologie n'est plus recopiée ici : elle est DÉRIVÉE du graphe compilé par
# [graph_topology.py](aca/core/graph_topology.py). La copie manuelle qui vivait à cet endroit avait
# déjà divergé du vrai graphe (il lui manquait l'arête `supervisor → routing`, le chemin FINISH du
# superviseur), exactement le risque signalé au §12bis : le schéma affiché montrait un superviseur
# sans issue vers la suite du pipeline. Ajouter un nœud dans `app.py` suffit désormais à le voir
# apparaître ici, sans rien resynchroniser.
GRAPH_FIXED_NODES = graph_topology.FIXED_NODES


def _build_graph_dot(current: str = None, done: set = frozenset()) -> str:
    """Construit le graphe LangGraph en DOT, nœud actif en surbrillance, nœuds déjà passés en vert."""
    return graph_topology.to_dot(current=current, done=done)


def _completed_graph_nodes(res: dict) -> set:
    """Nœuds déjà traversés pour l'état courant (post-pause) — pipeline fixe + workers effectivement appelés."""
    done = set(GRAPH_FIXED_NODES)
    completed_agents = res.get("completed_agents") or []
    done.update(a for a in completed_agents if a in graph_topology.NODE_LABELS)
    if "stratege" in completed_agents:
        done.add("reflection")
    return done


def advance_graph(payload):
    """
    Fait avancer le graphe (entrée initiale, ou `Command(resume=...)` après une clarification),
    en affichant la progression nœud par nœud, puis met à jour l'état de session :
    - `result` : valeurs courantes de l'état ;
    - `pending_clarification` : la question en attente (si le graphe s'est arrêté sur un interrupt
      dynamique), sinon None (le graphe est alors en pause avant `action`, prêt pour « Valider »).
    """
    # Callback de comptage de tokens (§13 item 4, "Quota Usage Tracker" des PDF "ACAM v2
    # Blueprint") : partagé par tous les appels LLM de ce segment de graphe (fast_llm/smart_llm/
    # creative_llm), agrégé et journalisé une fois le stream terminé.
    usage_handler = UsageMetadataCallbackHandler()
    config = {"configurable": {"thread_id": st.session_state.thread_id}, "callbacks": [usage_handler]}
    with st.status("Analyse en cours...", expanded=True) as status:
        graph_slot = st.empty()
        graph_slot.graphviz_chart(_build_graph_dot(), width="stretch")
        seen_nodes = set()  # le superviseur repasse plusieurs fois → chaque étape affichée une seule fois
        for step in aca_graph.app.stream(payload, config=config, stream_mode="updates"):
            node_name = next(iter(step))
            if node_name == "__interrupt__" or node_name in seen_nodes:
                continue
            seen_nodes.add(node_name)
            label, icon = NODE_STEPS.get(node_name, (node_name, ":material/bolt:"))
            st.write(f"{icon} {label}")
            graph_slot.graphviz_chart(_build_graph_dot(current=node_name, done=seen_nodes), width="stretch")
        graph_slot.graphviz_chart(_build_graph_dot(done=seen_nodes), width="stretch")
        status.update(label="Analyse terminée", state="complete")

    tokens_in, tokens_out = aca_graph.sum_usage(usage_handler.usage_metadata)
    analytics_store.record_tokens(st.session_state.thread_id, tokens_in, tokens_out)
    _sync_result(st.session_state.thread_id)


def _sync_result(thread_id: str) -> None:
    """Recharge `result`/`pending_clarification` depuis l'état courant du graphe pour ce thread."""
    config = {"configurable": {"thread_id": thread_id}}
    state = aca_graph.app.get_state(config)
    st.session_state.result = state.values
    # `state.interrupts` non vide = clarification dynamique en attente ; sinon pause avant action.
    st.session_state.pending_clarification = state.interrupts[0].value if state.interrupts else None

    # Tableau de bord (P2 §11.4 item 16) : enregistre la classification dès qu'elle est connue,
    # idempotent (INSERT OR IGNORE) donc rejouable à chaque resynchronisation (y compris après une
    # clarification résolue, où le premier appel a lieu avant que la proposition n'existe).
    values = state.values or {}
    if values.get("classification"):
        source = "gmail_import" if st.session_state.get("gmail_message_id") else "manuel"
        analytics_store.record_classification(
            thread_id, values["classification"], values.get("email_raw", {}).get("sender", ""), source,
        )
        if values.get("draft_response"):
            analytics_store.record_draft_ready(thread_id)


def load_queued_thread(thread_id: str) -> None:
    """
    Charge dans la session une analyse déjà traitée par le poller (poller.py) : le graphe a déjà
    tourné jusqu'à la pause de validation dans un autre processus, donc pas de `stream()` ici —
    juste une lecture de l'état persistant (`checkpoints.sqlite`).
    """
    st.session_state.thread_id = thread_id
    _sync_result(thread_id)


with st.sidebar:
    _user = _current_user()
    if _user.get("username") == DEV_USERNAME:
        # Mode développement / secret partagé : personne n'est identifié, on retombe donc sur
        # l'ancien champ libre pour que le journal d'audit ne soit pas vide.
        st.text_input(
            "Validé par", key="validator_name", placeholder="Ton nom (traçabilité)",
            help="Enregistré dans le journal d'audit local à chaque validation (audit_log.py). "
                 "Créez des comptes (`python -m aca.storage.user_store create …`) pour que ce champ "
                 "soit renseigné automatiquement et devienne réellement opposable.",
        )
    else:
        st.caption(f":material/account_circle: **{_user.get('username')}** · rôle « {_user.get('role')} »")
        if st.button("Se déconnecter", icon=":material/logout:"):
            st.session_state.session = None
            st.rerun()
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
                _safe_error("Erreur de connexion Gmail", e)

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
                    # Brut, non extrait ici : l'extraction se fait désormais dans le graphe
                    # (ingestion_node, §11.6) — plus besoin d'appeler extract_text_from_attachments
                    # avant même de savoir si cet e-mail sera soumis à l'analyse.
                    st.session_state.gmail_attachments_raw = email["attachments"]
                except Exception as e:
                    _safe_error("Erreur lors du chargement de l'e-mail", e)
                else:
                    st.rerun()

    # Curation de la base de connaissances (ingestion + validation des réponses trouvées par
    # `veille`) : réservée au rôle `admin` (§15.1.6). Ce qui entre ici devient un contexte RAG que
    # le Stratège citera dans de futures propositions commerciales — c'est une décision éditoriale,
    # pas une tâche de traitement quotidien comme valider un lead.
    if _can(user_store.PERM_CURATE_KNOWLEDGE):
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
                    _safe_error("Erreur d'ingestion", e)
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
    with st.expander("Confidentialité des données", icon=":material/policy:"):
        st.caption(
            "Résumé : votre e-mail et vos pièces jointes sont analysés pour qualifier votre "
            "demande, une validation humaine a toujours lieu avant tout enregistrement CRM, et "
            "vos données sont conservées 365 jours par défaut avant purge automatique. "
            "Politique complète : `docs/PRIVACY_POLICY.md`."
        )

    st.caption(
        "Modèles : Llama 3.1 8B (routage) · Llama 3.3 70B (extraction/rédaction) · "
        "Gemini embeddings (RAG sémantique)"
    )

tab_email, tab_dashboard, tab_history, tab_settings = st.tabs(
    ["Nouvel e-mail", "Tableau de bord", "Historique", "Réglages"]
)

with tab_email:
    # §16.3 — mode démonstration : jeu d'e-mails prêts à l'emploi, aucune clé d'API requise.
    # Sans ça, évaluer le produit imposait cinq comptes externes (Groq, Gemini, Tavily, compte de
    # service Google, OAuth Gmail) : on pouvait lire le code, pas l'essayer.
    if demo.is_enabled():
        st.info(
            "**Mode démonstration** — modèle simulé, aucune clé d'API utilisée. "
            "Le graphe, lui, est le vrai (mêmes agents, même pause de validation). "
            "Toute écriture CRM est **matériellement bloquée**.",
            icon=":material/science:",
        )
        demo_choice = st.selectbox(
            "Charger un e-mail de démonstration",
            options=range(len(demo.DEMO_EMAILS)),
            format_func=lambda i: demo.DEMO_EMAILS[i]["label"],
            index=None,
            placeholder="Choisir un exemple (démo, devis, spam, support, clause à risque…)",
        )
        if demo_choice is not None and st.button(
            "Charger cet exemple", icon=":material/download:",
        ):
            example = demo.DEMO_EMAILS[demo_choice]
            st.session_state.email_sender = example["sender"]
            st.session_state.email_subject = example["subject"]
            st.session_state.email_body = example["body"]
            st.rerun()

    # Interface principale pour simuler/entrer la réception d'un e-mail
    with st.container(border=True):
        st.subheader("Nouvel e-mail entrant", anchor=False)

        col1, col2 = st.columns(2)
        with col1:
            sender = st.text_input("Expéditeur", key="email_sender")
            subject = st.text_input("Objet", key="email_subject")
            body = st.text_area("Corps du message", height=150, key="email_body")

        with col2:
            st.markdown("**Pièces jointes (optionnel)**")
            uploaded_files = st.file_uploader(
                "Importer un ou plusieurs documents (cahier des charges, devis, tableur...)",
                type=["pdf", "docx", "xlsx"],
                accept_multiple_files=True,
                label_visibility="collapsed",
            )
            if st.session_state.gmail_attachments_raw:
                st.caption(":material/attach_file: Pièce(s) jointe(s) récupérée(s) automatiquement depuis Gmail.")

        launch = st.button("Lancer l'analyse IA", type="primary", icon=":material/bolt:")

    # Bouton déclencheur de l'agent
    if launch:
        # Pièces jointes brutes (upload manuel prioritaire sur l'import Gmail) — l'extraction du
        # texte se fait désormais DANS le graphe (ingestion_node, §11.6), plus ici : ui.py/poller.py
        # n'ont plus qu'à transmettre la liste brute, sans dupliquer l'appel à
        # extract_text_from_attachments() chacun de leur côté.
        if uploaded_files:
            attachments_raw = [(f.name, f.getvalue()) for f in uploaded_files]
        else:
            attachments_raw = st.session_state.gmail_attachments_raw

        # Construction de l'entrée pour le graphe
        graphe_input = {
            "email_raw": {
                "sender": sender,
                "subject": subject,
                "body": body,
            },
            "attachments_raw": attachments_raw,
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

        elif classif == "SUPPORT":
            st.warning(
                "E-mail classé SUPPORT : ce n'est pas un lead commercial, donc pas de proposition ni "
                "de fiche CRM. L'e-mail a été routé vers l'équipe support (alerte et/ou brouillon de "
                "transfert Gmail, selon la configuration).",
                icon=":material/support_agent:",
            )
            if res.get("reasoning_log"):
                with st.expander("Détail du routage", icon=":material/network_node:"):
                    st.graphviz_chart(
                        _build_graph_dot(done=_completed_graph_nodes(res)), width="stretch",
                    )
                    for line in res["reasoning_log"]:
                        st.markdown(f"- {line}")

        elif classif == "AUTRE":
            st.info(
                "E-mail hors périmètre commercial (candidature, partenariat, question générale) — "
                "routé vers les RH (alerte et/ou brouillon de transfert Gmail, selon la "
                "configuration). Aucune fiche CRM n'est créée automatiquement.",
                icon=":material/help:",
            )
            if res.get("reasoning_log"):
                with st.expander("Détail du routage", icon=":material/network_node:"):
                    st.graphviz_chart(
                        _build_graph_dot(done=_completed_graph_nodes(res)), width="stretch",
                    )
                    for line in res["reasoning_log"]:
                        st.markdown(f"- {line}")

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

            # Alertes déterministes issues de l'audit §13 (PDF "ACAM v2 Blueprint") : clauses
            # contractuelles à risque (risk_scan_node) et lacune de connaissance (veille_node).
            if res.get("risk_flags"):
                st.error(
                    "Clause(s) contractuelle(s) à risque détectée(s) : " + ", ".join(res["risk_flags"])
                    + " — à faire relire par l'équipe juridique/la direction avant tout engagement.",
                    icon=":material/gpp_maybe:",
                )
            if res.get("knowledge_gap"):
                st.warning(
                    "Aucune réponse vérifiée (base de connaissances ni recherche web) pour ce besoin — "
                    "la proposition ci-dessous reste volontairement prudente. Relisez avant validation.",
                    icon=":material/help:",
                )
            # §15.1.4 : tentative d'injection de prompt (prompt_guard.py). Signalée, jamais
            # bloquante — c'est la personne qui valide qui doit savoir que le message entrant
            # essayait de piloter le modèle, sans quoi le brouillon lui paraît simplement plausible.
            if res.get("injection_flags"):
                st.error(
                    "Tentative(s) de manipulation de l'IA détectée(s) dans le message entrant : "
                    + ", ".join(res["injection_flags"])
                    + ". Le contenu reçu essaie de donner des instructions à l'assistant — relisez "
                    "la proposition avec méfiance et vérifiez qu'elle n'accorde aucune remise, "
                    "exception ou engagement inhabituel.",
                    icon=":material/security:",
                )

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
                    st.graphviz_chart(
                        _build_graph_dot(done=_completed_graph_nodes(res)), width="stretch",
                    )
                    for line in res["reasoning_log"]:
                        st.markdown(f"- {line}")

            # Proposition éditable (§13 item 3 — version réalisable du "Continuous Training Loop"
            # des PDF "ACAM v2 Blueprint") : un humain peut corriger le brouillon avant validation ;
            # la version envoyée au CRM/Gmail est celle du champ, et (original, édité) est capturé
            # comme futur corpus d'amélioration du few-shot prompting, pas pour du fine-tuning.
            original_draft = res.get("draft_response") or ""
            with st.container(border=True):
                st.markdown("##### Proposition rédigée")
                st.caption("Modifiable avant validation — la version ci-dessous est celle envoyée au CRM/Gmail.")
                edited_draft = st.text_area(
                    "Proposition", value=original_draft, height=180,
                    key=f"draft_edit_{st.session_state.thread_id}", label_visibility="collapsed",
                )

            # --- PHASE 3 : VALIDATION HUMAINE (reprise du graphe interrompu) ---
            st.subheader("Validation humaine", anchor=False)
            st.caption("Si la recommandation de l'IA est correcte, validez pour envoyer dans le CRM.")

            if not _can(user_store.PERM_VALIDATE_LEAD):
                st.info(
                    "Votre rôle ne permet pas de valider un lead (écriture CRM). Contactez un "
                    "administrateur.",
                    icon=":material/lock:",
                )
            elif st.button("Valider et ajouter au CRM", type="primary", icon=":material/check_circle:"):
                with st.spinner("Reprise du graphe et écriture dans Google Sheets..."):
                    try:
                        graph_config = {"configurable": {"thread_id": st.session_state.thread_id}}
                        # Si l'humain a modifié le brouillon, on met à jour l'état AVANT de reprendre
                        # le graphe : action_node lit draft_response depuis l'état, donc HubSpot/
                        # Sheets/le brouillon Gmail doivent recevoir la version éditée, pas l'originale.
                        if edited_draft != original_draft:
                            aca_graph.app.update_state(graph_config, {"draft_response": edited_draft})
                            analytics_store.record_edit(
                                st.session_state.thread_id, original_draft, edited_draft,
                            )
                        # Le graphe était en pause avant 'action'. On le reprend (invoke None) :
                        # action_node écrit dans Leads + marque l'e-mail Gmail comme traité.
                        final = aca_graph.app.invoke(None, config=graph_config)
                        action_status = final.get("action_status", "Lead ajouté au CRM.")
                        st.success(action_status, icon=":material/check_circle:")
                        # Retire l'entrée de la file d'attente si elle en venait (no-op sinon).
                        queue_store.mark_validated(st.session_state.thread_id)
                        # Traçabilité minimale : qui a validé, quoi, quand (audit_log.py).
                        # §15.1.6 : l'identité vient de la session authentifiée, plus du champ
                        # libre — un opérateur ne peut plus signer une validation au nom d'un
                        # autre. Le champ libre ne subsiste qu'en mode développement/secret
                        # partagé, où personne n'est identifié de toute façon.
                        _session_user = _current_user().get("username")
                        validated_by = (
                            st.session_state.get("validator_name", "")
                            if _session_user == DEV_USERNAME else _session_user
                        )
                        audit_log.log_validation(
                            st.session_state.thread_id, validated_by,
                            res.get("classification", ""), res.get("email_raw", {}).get("sender", ""),
                        )
                        # Tableau de bord : ferme la mesure de temps de réponse pour ce thread.
                        analytics_store.record_validation(st.session_state.thread_id)
                        # Suivi des relances (no-op si pas de fil Gmail — saisie manuelle par exemple).
                        followup_store.track(
                            st.session_state.thread_id, res.get("gmail_thread_id"),
                            res.get("email_raw", {}).get("sender", ""),
                            res.get("email_raw", {}).get("subject", ""),
                        )
                        # On efface le résultat pour passer au suivant
                        st.session_state.gmail_attachments_raw = []
                        st.session_state.gmail_message_id = None
                        st.session_state.gmail_thread_id = None
                        st.session_state.pending_clarification = None
                        del st.session_state.result
                    except Exception as e:
                        _safe_error("Erreur technique lors de la validation", e)

            # Rejet explicite : miroir de `_do_reject` de l'API (aca/api.py) — AUCUNE écriture CRM,
            # on ne reprend PAS le graphe. On retire le lead de la file d'attente (no-op si saisie
            # manuelle, non issue du poller) et on efface le résultat pour passer au suivant. Sans ce
            # bouton, un lead ne pouvait qu'être validé ou abandonné silencieusement — jamais rejeté
            # de façon traçable, alors que Streamlit est désormais la surface opérationnelle
            # principale. (Comme l'API, on ne journalise pas le rejet dans audit_log ; auditer les
            # rejets est un item §15 futur si besoin.)
            if _can(user_store.PERM_REJECT_LEAD) and st.button(
                "Rejeter (ne pas envoyer au CRM)", icon=":material/cancel:"
            ):
                queue_store.mark_rejected(st.session_state.thread_id)
                st.session_state.gmail_attachments_raw = []
                st.session_state.gmail_message_id = None
                st.session_state.gmail_thread_id = None
                st.session_state.pending_clarification = None
                del st.session_state.result
                st.info("Lead rejeté — non envoyé au CRM.", icon=":material/cancel:")

with tab_dashboard:
    st.caption(
        "Calculé à partir de `analytics_store.py` — TOUTES les analyses (y compris SPAM/AUTRE/"
        "SUPPORT, jamais validées), indépendamment de l'onglet Sheets Leads qui ne reçoit que les "
        "leads commerciaux validés."
    )
    days = st.segmented_control(
        "Période", options=[7, 30, 90], default=30, required=True,
        format_func=lambda d: f"{d} jours", key="dashboard_days",
    )

    volume = analytics_store.volume_by_category(days)
    daily = analytics_store.daily_volume(days)
    funnel = analytics_store.funnel_counts(days)
    resp_times = analytics_store.response_times(days)
    edits = analytics_store.edit_rate(days)
    tokens = analytics_store.token_stats(days)

    total = sum(v["count"] for v in volume)
    conversion_pct = round(100 * funnel["validés"] / total, 1) if total else 0
    median_minutes = None
    if resp_times:
        sorted_minutes = sorted(r["minutes"] for r in resp_times)
        median_minutes = sorted_minutes[len(sorted_minutes) // 2]

    if total == 0:
        st.info(
            "Aucune analyse enregistrée sur cette période — le tableau de bord se remplit au fur "
            "et à mesure des e-mails classés (onglet « Nouvel e-mail » ou poller.py).",
            icon=":material/info:",
        )
    else:
        with st.container(horizontal=True):
            st.metric("E-mails classés", total, border=True)
            st.metric("Taux de validation", f"{conversion_pct}%", border=True)
            st.metric(
                "Temps de réponse médian",
                f"{median_minutes:.0f} min" if median_minutes is not None else "—",
                border=True,
            )
            st.metric(
                "Brouillons édités avant validation", f"{edits['taux_pct']}%", border=True,
                help="§13 item 3 — % de propositions validées que l'humain a modifiées avant l'envoi.",
            )
            st.metric(
                "Tokens Groq / analyse (moyenne)",
                f"{tokens['moyenne_par_analyse']:.0f}" if tokens["analyses"] else "—",
                border=True,
                help="§13 item 4 — Quota Usage Tracker, purement informatif tant que Groq reste gratuit.",
            )

        col1, col2 = st.columns(2)
        with col1:
            with st.container(border=True):
                st.markdown("**Volume par catégorie**")
                st.bar_chart(volume, x="classification", y="count", x_label="", y_label="")

        with col2:
            with st.container(border=True):
                st.markdown("**Tendance quotidienne**")
                if daily:
                    st.line_chart(daily, x="jour", y="count", x_label="", y_label="")
                else:
                    st.caption("Pas assez de données pour une tendance.")

        with st.container(border=True):
            st.markdown("**Entonnoir de conversion**")
            st.bar_chart(
                [{"étape": k, "compte": v} for k, v in funnel.items()],
                x="étape", y="compte", x_label="", y_label="", horizontal=True, sort=False,
            )

        if resp_times:
            with st.expander("Détail des temps de réponse (leads validés)", icon=":material/schedule:"):
                st.dataframe(resp_times, hide_index=True, width="stretch")

with tab_history:
    st.caption(
        "Journal d'audit (`audit_log.py`, §12 item 2) — qui a validé quel lead, quand, et sous "
        "quelle classification. Ne couvre que les événements de validation (« Valider ») ; le "
        "Tableau de bord ci-dessus couvre lui *toutes* les classifications, y compris SPAM/AUTRE."
    )
    col_limit, col_search = st.columns([1, 3])
    with col_limit:
        history_limit = st.number_input(
            "Nombre d'entrées", min_value=10, max_value=500, value=100, step=10,
        )
    with col_search:
        history_query = st.text_input(
            "Rechercher une exécution passée",
            placeholder="expéditeur, classification, validé par, ID...",
        )

    history_rows = audit_log.list_recent(limit=int(history_limit))
    if history_query.strip():
        needle = history_query.strip().lower()
        history_rows = [
            r for r in history_rows if needle in " ".join(str(v) for v in r.values()).lower()
        ]

    if not history_rows:
        st.caption("Aucun événement de validation pour le moment (ou aucun résultat pour cette recherche).")
    else:
        st.dataframe(
            history_rows, hide_index=True, width="stretch",
            column_config={
                "thread_id": "ID d'analyse", "validated_by": "Validé par",
                "classification": "Classification", "sender": "Expéditeur",
                "validated_at": "Date de validation",
            },
        )

with tab_settings:
    # §15.1.6 : ces réglages pilotent où partent les alertes et ce que le Stratège promet
    # (lien Calendly, adresses de routage, cadence de relance) — un opérateur peut les consulter,
    # seul un administrateur les modifie.
    if not _can(user_store.PERM_EDIT_SETTINGS):
        st.info(
            "Les réglages sont réservés au rôle « admin ». Vous pouvez consulter la configuration "
            "courante ci-dessous, mais pas la modifier.",
            icon=":material/lock:",
        )
        st.dataframe(
            [{"Réglage": config_store.SETTINGS_SCHEMA.get(k, k), "Valeur": v}
             for k, v in config_store.get_all_settings().items()] or [{"Réglage": "—", "Valeur": "—"}],
            hide_index=True, width="stretch",
        )
    else:
        st.caption(
            "Réglages du tenant courant (§12 item 7) — éditables ici sans toucher au fichier `.env`. "
            "Un champ laissé vide retombe sur la valeur `.env`/par défaut existante ; rien n'est perdu "
            "en vidant un champ, l'ancien réglage est simplement effacé de cette surcouche."
        )
        current = config_store.get_all_settings()

        with st.form("settings_form"):
            st.markdown("**Lien de réservation**")
            calendly_url = st.text_input(
                "Lien Calendly (demandes de démo)",
                value=current.get("CALENDLY_URL", ""),
                placeholder=os.getenv("CALENDLY_URL", "(non configuré dans .env)"),
            )

            st.markdown("**Routage SUPPORT / AUTRE (RH)**")
            col_support, col_hr = st.columns(2)
            with col_support:
                support_email = st.text_input(
                    "E-mail support", value=current.get("SUPPORT_EMAIL", ""),
                    placeholder=os.getenv("SUPPORT_EMAIL", "(non configuré)"),
                )
                support_webhook = st.text_input(
                    "Webhook Slack support", value=current.get("SUPPORT_SLACK_WEBHOOK_URL", ""),
                    placeholder=os.getenv("SUPPORT_SLACK_WEBHOOK_URL", "(non configuré)"),
                )
            with col_hr:
                hr_email = st.text_input(
                    "E-mail RH", value=current.get("HR_EMAIL", ""),
                    placeholder=os.getenv("HR_EMAIL", "(non configuré)"),
                )
                hr_webhook = st.text_input(
                    "Webhook Slack RH", value=current.get("HR_SLACK_WEBHOOK_URL", ""),
                    placeholder=os.getenv("HR_SLACK_WEBHOOK_URL", "(non configuré)"),
                )

            st.markdown("**Cadence des relances**")
            col_days, col_rounds = st.columns(2)
            with col_days:
                relance_days = st.text_input(
                    "Jours avant relance", value=current.get("RELANCE_DAYS", ""),
                    placeholder=os.getenv("RELANCE_DAYS", "4"),
                )
            with col_rounds:
                relance_rounds = st.text_input(
                    "Nombre maximum de relances", value=current.get("RELANCE_MAX_ROUNDS", ""),
                    placeholder=os.getenv("RELANCE_MAX_ROUNDS", "3"),
                )

            submitted = st.form_submit_button(
                "Enregistrer les réglages", type="primary", icon=":material/save:"
            )
            if submitted:
                for key, value in {
                    "CALENDLY_URL": calendly_url,
                    "SUPPORT_EMAIL": support_email,
                    "SUPPORT_SLACK_WEBHOOK_URL": support_webhook,
                    "HR_EMAIL": hr_email,
                    "HR_SLACK_WEBHOOK_URL": hr_webhook,
                    "RELANCE_DAYS": relance_days,
                    "RELANCE_MAX_ROUNDS": relance_rounds,
                }.items():
                    if value.strip():
                        config_store.set_setting(key, value.strip())
                st.success(
                    "Réglages enregistrés — pris en compte à la prochaine analyse "
                    "(`CALENDLY_URL`/routage) ou au prochain cycle planifié (`relance.py`).",
                    icon=":material/check_circle:",
                )
                st.rerun()

        st.caption(
            "Ces réglages sont relus à chaque exécution de `relance.py` (processus planifié "
            "indépendant) et à chaque analyse d'e-mail — pas besoin de redémarrer un process pour "
            "qu'un changement prenne effet."
        )

    # ── Comptes et rôles (§15.1.6) ────────────────────────────────────────────────────────────
    if _can(user_store.PERM_MANAGE_USERS):
        st.divider()
        st.subheader("Comptes et rôles", anchor=False)
        st.caption(
            "Identités nominatives du tenant courant. Tant qu'aucun compte n'existe, l'UI reste "
            "sur le secret partagé `ACA_UI_PASSWORD` (mode développement) et le journal d'audit "
            "n'est pas attribuable. Un `operator` valide et rejette des leads ; un `admin` peut en "
            "plus régler la configuration, curer la base de connaissances et gérer les comptes."
        )

        accounts = user_store.list_users()
        if accounts:
            st.dataframe(
                accounts, hide_index=True, width="stretch",
                column_config={
                    "username": "Identifiant", "role": "Rôle", "created_at": "Créé le",
                    "disabled": st.column_config.CheckboxColumn("Désactivé"),
                },
            )
        else:
            st.caption("Aucun compte pour l'instant — créez le premier ci-dessous.")

        with st.form("create_user_form"):
            col_name, col_role = st.columns([2, 1])
            new_username = col_name.text_input("Identifiant")
            new_role = col_role.selectbox("Rôle", options=list(user_store.ROLES))
            new_password = st.text_input(
                "Mot de passe", type="password",
                help=f"{user_store.MIN_PASSWORD_LENGTH} caractères minimum. Stocké haché "
                     "(PBKDF2-HMAC-SHA256, sel par utilisateur), jamais en clair.",
            )
            if st.form_submit_button("Créer le compte", type="primary", icon=":material/person_add:"):
                try:
                    user_store.create_user(new_username, new_password, role=new_role)
                except (user_store.UserExists, user_store.PasswordTooWeak, ValueError) as e:
                    # Erreurs de saisie volontairement affichées telles quelles : elles ne
                    # contiennent que ce que l'utilisateur vient de taper, aucune donnée interne
                    # (contrairement à `_safe_error`, réservé aux exceptions techniques).
                    st.error(str(e), icon=":material/error:")
                else:
                    st.success(f"Compte « {new_username} » créé.", icon=":material/check_circle:")
                    st.rerun()

        if accounts:
            with st.expander("Modifier un compte", icon=":material/manage_accounts:"):
                target = st.selectbox(
                    "Compte", options=[a["username"] for a in accounts], key="user_admin_target",
                )
                current_account = next(a for a in accounts if a["username"] == target)
                col_role_edit, col_state = st.columns(2)
                with col_role_edit:
                    wanted_role = st.selectbox(
                        "Rôle", options=list(user_store.ROLES),
                        index=list(user_store.ROLES).index(current_account["role"]),
                        key="user_admin_role",
                    )
                    if st.button("Appliquer le rôle", icon=":material/badge:"):
                        user_store.set_role(target, wanted_role)
                        st.rerun()
                with col_state:
                    # Désactiver plutôt que supprimer : le journal d'audit référence l'identifiant,
                    # l'effacer rendrait les validations passées non attribuables.
                    label = "Réactiver" if current_account["disabled"] else "Désactiver"
                    if st.button(label, icon=":material/block:"):
                        user_store.set_disabled(target, not current_account["disabled"])
                        st.rerun()
                reset_password = st.text_input(
                    "Nouveau mot de passe", type="password", key="user_admin_password",
                )
                if st.button("Réinitialiser le mot de passe", icon=":material/key:"):
                    try:
                        user_store.set_password(target, reset_password)
                    except user_store.PasswordTooWeak as e:
                        st.error(str(e), icon=":material/error:")
                    else:
                        st.success("Mot de passe mis à jour.", icon=":material/check_circle:")

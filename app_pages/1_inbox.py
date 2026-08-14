"""Onglet « Nouvel e-mail » (§18) — extrait de `ui.py`. Voir `aca/ui/shared.py` pour le contexte
de la découpe en pages `st.navigation`. Page par défaut du routeur."""
import uuid
from datetime import datetime, time as dt_time, timedelta

import streamlit as st
from langgraph.types import Command

from aca.core import app as aca_graph
from aca.core import demo
from aca.core import ui_kit
from aca.integrations import pdf_export
from aca.storage import (
    activity_log, analytics_store, audit_log, followup_store, queue_store, task_store, user_store,
)
from aca.ui.shared import (
    CATEGORY_STYLE,
    DEV_USERNAME,
    advance_graph,
    audit as _audit,
    build_graph_dot as _build_graph_dot,
    can as _can,
    completed_graph_nodes as _completed_graph_nodes,
    current_user as _current_user,
    safe_error as _safe_error,
    t,
)

# §16.3 — mode démonstration : jeu d'e-mails prêts à l'emploi, aucune clé d'API requise.
# Sans ça, évaluer le produit imposait cinq comptes externes (Groq, Gemini, Tavily, compte de
# service Google, OAuth Gmail) : on pouvait lire le code, pas l'essayer.
if demo.is_enabled():
    st.info(t("inbox.demo_banner"), icon=":material/science:")
    demo_choice = st.selectbox(
        t("inbox.demo_select"),
        options=range(len(demo.DEMO_EMAILS)),
        format_func=lambda i: demo.DEMO_EMAILS[i]["label"],
        index=None,
        placeholder=t("inbox.demo_select_placeholder"),
    )
    if demo_choice is not None and st.button(
        t("inbox.demo_load_button"), icon=":material/download:",
    ):
        example = demo.DEMO_EMAILS[demo_choice]
        st.session_state.email_sender = example["sender"]
        st.session_state.email_subject = example["subject"]
        st.session_state.email_body = example["body"]
        st.rerun()

# Interface principale pour simuler/entrer la réception d'un e-mail
with st.container(border=True):
    st.subheader(t("inbox.form_header"), anchor=False)

    col1, col2 = st.columns(2)
    with col1:
        sender = st.text_input(t("inbox.sender"), key="email_sender")
        subject = st.text_input(t("inbox.subject"), key="email_subject")
        body = st.text_area(t("inbox.body"), height=150, key="email_body")

    with col2:
        st.markdown(f"**{t('inbox.attachments_label')}**")
        uploaded_files = st.file_uploader(
            t("inbox.attachments_uploader"),
            type=["pdf", "docx", "xlsx"],
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        if st.session_state.gmail_attachments_raw:
            st.caption(f":material/attach_file: {t('inbox.attachments_from_gmail')}")

    launch = st.button(t("inbox.launch_button"), type="primary", icon=":material/bolt:")

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
    _audit(
        activity_log.ACTION_ANALYSIS_STARTED, target_type="thread",
        target_id=st.session_state.thread_id,
        details={"expéditeur": sender, "objet": subject,
                 "source": "gmail" if st.session_state.gmail_message_id else "manuel",
                 "pièces_jointes": len(attachments_raw)},
    )
    advance_graph(graphe_input)

# Si on a un résultat d'analyse, on l'affiche et on demande validation (Human-in-the-loop)
if "result" in st.session_state:
    res = st.session_state.result

    st.subheader(t("inbox.result_header"), anchor=False)

    # --- Clarification (interrupt dynamique) : l'agent pose une question AVANT de poursuivre ---
    pending = st.session_state.get("pending_clarification")
    if pending:
        st.warning(pending.get("question", "Une précision est nécessaire."), icon=":material/help:")
        clar_answer = st.text_input(t("inbox.clarification_reply_label"), key="clarif_answer")
        if st.button(t("inbox.clarification_reply_button"), type="primary", icon=":material/reply:"):
            if clar_answer.strip():
                # La réponse humaine est fusionnée dans `extracted_info` et influence toute la
                # suite du raisonnement : elle appartient à la traçabilité de l'analyse.
                _audit(
                    activity_log.ACTION_CLARIFICATION_ANSWERED, target_type="thread",
                    target_id=st.session_state.thread_id,
                    details={"question": pending.get("question", ""),
                             "réponse": clar_answer.strip()},
                )
                advance_graph(Command(resume=clar_answer.strip()))
                st.rerun()
            else:
                st.error(t("inbox.clarification_empty_error"))
        st.stop()  # on n'affiche pas la fiche / la validation tant que la question n'a pas de réponse

    classif = res.get("classification", "INCONNU")

    if classif == "SPAM":
        st.error(t("inbox.spam_message"), icon=":material/block:")

    elif classif == "SUPPORT":
        st.warning(t("inbox.support_message"), icon=":material/support_agent:")
        if res.get("reasoning_log"):
            with st.expander(t("inbox.routing_detail_expander"), icon=":material/network_node:"):
                st.graphviz_chart(
                    _build_graph_dot(done=_completed_graph_nodes(res)), width="stretch",
                )
                for line in res["reasoning_log"]:
                    st.markdown(f"- {line}")

    elif classif == "AUTRE":
        st.info(t("inbox.autre_message"), icon=":material/help:")
        if res.get("reasoning_log"):
            with st.expander(t("inbox.routing_detail_expander"), icon=":material/network_node:"):
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
            st.warning(t("inbox.duplicate_warning"), icon=":material/warning:")

        info = res.get("extracted_info", {})

        # Alertes déterministes issues de l'audit §13 (PDF "ACAM v2 Blueprint") : clauses
        # contractuelles à risque (risk_scan_node) et lacune de connaissance (veille_node).
        if res.get("risk_flags"):
            st.error(
                t("inbox.risk_flags_message", flags=", ".join(res["risk_flags"])),
                icon=":material/gpp_maybe:",
            )
        if res.get("knowledge_gap"):
            st.warning(t("inbox.knowledge_gap_message"), icon=":material/help:")
        # §15.1.4 : tentative d'injection de prompt (prompt_guard.py). Signalée, jamais
        # bloquante — c'est la personne qui valide qui doit savoir que le message entrant
        # essayait de piloter le modèle, sans quoi le brouillon lui paraît simplement plausible.
        if res.get("injection_flags"):
            st.error(
                t("inbox.injection_flags_message", flags=", ".join(res["injection_flags"])),
                icon=":material/security:",
            )

        with st.container(border=True):
            st.markdown(f"##### {t('inbox.fiche_header')}")
            col_a, col_b, col_c = st.columns(3)
            col_a.metric(t("inbox.company"), info.get("entreprise") or "—")
            col_b.metric(t("inbox.contact"), info.get("contact") or "—")
            urgence = (info.get("urgence") or "").lower()
            urgence_color = {"haute": "red", "moyenne": "orange", "basse": "green"}.get(urgence, "gray")
            with col_c:
                st.caption(t("inbox.urgency"))
                st.badge(urgence.capitalize() if urgence else "—", color=urgence_color)
            st.caption(t("inbox.main_need"))
            st.write(info.get("besoin_principal") or "—")
            if res.get("company_profile"):
                st.caption(t("inbox.company_profile_caption"))
                st.write(res["company_profile"])

        # Trace de raisonnement de l'équipe d'agents (superviseur + workers)
        if res.get("reasoning_log"):
            with st.expander(t("inbox.reasoning_expander"), icon=":material/network_node:"):
                st.graphviz_chart(
                    _build_graph_dot(done=_completed_graph_nodes(res)), width="stretch",
                )
                for line in res["reasoning_log"]:
                    st.markdown(f"- {line}")

        # Historique de ce lead (§18, recap #5) : « reçu, classé, clarifié, réécrit, validé » —
        # les données vivaient déjà dans le journal d'activité, `activity_log.lead_timeline()`
        # les remet simplement dans l'ordre. Le meilleur rapport valeur/effort de la liste des
        # améliorations suggérées : aucune nouvelle donnée, une seule vue à écrire.
        with st.expander(t("inbox.history_expander"), icon=":material/history:"):
            _events = []
            for _row in activity_log.lead_timeline(st.session_state.thread_id):
                _tone = {
                    activity_log.ACTION_LEAD_VALIDATED: "ok",
                    activity_log.ACTION_LEAD_REJECTED: "warn",
                    activity_log.ACTION_DRAFT_EDITED: "warn",
                    activity_log.ACTION_CLARIFICATION_ANSWERED: "info",
                }.get(_row["action"], "")
                if _row["outcome"] != activity_log.OUTCOME_SUCCESS:
                    _tone = "danger"
                # §4 item 3 des suggestions : « validation depuis un poste jamais vu » est une
                # comparaison d'ensembles (pas de l'apprentissage automatique) — déjà bâtie dans
                # activity_log.is_new_device, il ne manquait que la surface qui l'affiche.
                _detail = ""
                if _row["device_id"] and activity_log.is_new_device(
                    _row["actor"], _row["device_id"], before_id=_row["id"],
                ):
                    _detail = "poste inédit pour cette personne à cet instant"
                _events.append({
                    "when": _row["occurred_at"], "who": _row["actor"],
                    "what": _row["action_label"], "detail": _detail, "tone": _tone,
                })
            st.html(ui_kit.timeline(_events))

            # Différentiel avant/après (§4 item 2) : le journal ne consignait que des longueurs de
            # caractères (« 340 → 412 »), qui ne disent rien de CE QUI a changé — un responsable
            # veut savoir si l'IA a promis un prix qu'il a fallu corriger.
            _edit = analytics_store.get_draft_edit(st.session_state.thread_id)
            if _edit:
                st.caption(t("inbox.diff_caption"))
                st.html(ui_kit.diff(_edit["original"], _edit["edited"]))

        # Proposition éditable (§13 item 3 — version réalisable du "Continuous Training Loop"
        # des PDF "ACAM v2 Blueprint") : un humain peut corriger le brouillon avant validation ;
        # la version envoyée au CRM/Gmail est celle du champ, et (original, édité) est capturé
        # comme futur corpus d'amélioration du few-shot prompting, pas pour du fine-tuning.
        original_draft = res.get("draft_response") or ""
        with st.container(border=True):
            st.markdown(f"##### {t('inbox.draft_header')}")
            st.caption(t("inbox.draft_caption"))
            edited_draft = st.text_area(
                "Proposition", value=original_draft, height=180,
                key=f"draft_edit_{st.session_state.thread_id}", label_visibility="collapsed",
            )
            # Export PDF à la marque (§18, recap #8) : un commercial qui veut envoyer la
            # proposition autrement que par le brouillon Gmail n'a plus à la recopier dans Word.
            # `build_proposal_pdf` ne lève jamais (même contrat que notify.py/hubspot.py) — un
            # bouton absent (PDF `None`) est un no-op silencieux, pas une erreur affichée.
            _pdf_bytes = pdf_export.build_proposal_pdf(
                edited_draft, info, classif, res.get("email_raw", {}).get("sender", ""),
            )
            if _pdf_bytes:
                if st.download_button(
                    t("inbox.pdf_download_button"), data=_pdf_bytes,
                    file_name=pdf_export.proposal_filename(info, classif),
                    mime="application/pdf", icon=":material/picture_as_pdf:",
                ):
                    # Même raisonnement que l'export CSV du journal d'activité : un PDF contient
                    # des données personnelles du prospect (nom, contact) — c'est une sortie de
                    # données hors de l'application, donc consignée.
                    _audit(
                        activity_log.ACTION_DATA_EXPORTED, target_type="proposition-pdf",
                        target_id=st.session_state.thread_id,
                        details={"classification": classif},
                    )

        # --- PHASE 3 : VALIDATION HUMAINE (reprise du graphe interrompu) ---
        st.subheader(t("inbox.validation_header"), anchor=False)
        st.caption(t("inbox.validation_caption"))

        # §19 — le cartouche de signature. Il remplace deux boutons posés sous une zone de texte,
        # qui ne disaient rien de ce que la validation engage NI de qui l'engage. Les effets sont
        # énoncés avant le geste : valider sans savoir ce que ça écrit n'est pas un consentement
        # éclairé, et c'est justement la garantie que ce produit revendique.
        _signer = _current_user().get("username") or "—"
        _sender_label = res.get("email_raw", {}).get("sender", "")
        _lead_label = (info.get("entreprise") or _sender_label or "Lead sans entreprise")
        _has_gmail = bool(res.get("gmail_message_id"))
        _effects = [("cloud_upload", "Écriture de la fiche dans le CRM (Sheets, et HubSpot si actif)")]
        if _has_gmail:
            _effects.append(("drafts", "Création du brouillon de réponse dans le fil Gmail d'origine"))
            _effects.append(("mark_email_read", "Marquage de l'e-mail d'origine comme traité"))
        else:
            _effects.append(("edit_note", "Saisie manuelle : aucun brouillon Gmail ne sera créé"))
        st.html(ui_kit.signoff(
            _lead_label, _signer, datetime.now().strftime("%d/%m/%Y à %H:%M"), _effects,
        ))

        # §19 — « programmer un envoi ». Ce n'est PAS une entorse à la promesse du produit : la
        # personne lit le brouillon, le corrige si besoin, puis décide elle-même qu'il partira à
        # telle heure. L'autorisation humaine existe, elle est simplement antérieure à l'exécution
        # — comme l'envoi différé de n'importe quelle messagerie. Ce qui part est le brouillon
        # Gmail créé à la validation, donc exactement le texte relu ; s'il est modifié ou supprimé
        # dans Gmail entre-temps, c'est la volonté de l'humain qui l'emporte (cf. gmail_reader.
        # send_draft).
        _send_mode = t("inbox.send_now")
        _due_epoch = None
        if _can(user_store.PERM_VALIDATE_LEAD) and _has_gmail:
            _send_mode = st.radio(
                t("inbox.send_mode_label"),
                options=[t("inbox.send_now"), t("inbox.send_scheduled")],
                horizontal=True, key=f"send_mode_{st.session_state.thread_id}",
                help=t("inbox.send_mode_help"),
            )
            if _send_mode == t("inbox.send_scheduled"):
                # Proposition par défaut : demain 9 h. Un envoi commercial différé vise presque
                # toujours le prochain créneau ouvrable, pas « dans une heure » ; proposer l'heure
                # la plus probable évite trois manipulations dans le cas courant.
                _default = (datetime.now() + timedelta(days=1)).replace(
                    hour=9, minute=0, second=0, microsecond=0,
                )
                _col_d, _col_h = st.columns(2)
                _d = _col_d.date_input(
                    t("inbox.send_date"), value=_default.date(),
                    min_value=datetime.now().date(),
                    key=f"send_date_{st.session_state.thread_id}",
                )
                _h = _col_h.time_input(
                    t("inbox.send_time"), value=dt_time(9, 0), step=900,
                    key=f"send_time_{st.session_state.thread_id}",
                )
                _due = datetime.combine(_d, _h)
                _due_epoch = _due.timestamp()
                if _due <= datetime.now():
                    # Refus explicite plutôt que « on envoie tout de suite » : une date passée est
                    # presque toujours une saisie erronée, et deviner à la place de la personne
                    # ferait partir un message qu'elle croyait avoir différé.
                    st.warning(t("inbox.send_past_warning"), icon=":material/schedule:")
                    _due_epoch = None
                else:
                    st.caption(t("inbox.send_confirm", when=_due.strftime("%d/%m/%Y à %H:%M")))

        _validate_label = (
            t("inbox.validate_and_schedule_button") if _due_epoch
            else t("inbox.validate_button")
        )

        if not _can(user_store.PERM_VALIDATE_LEAD):
            st.info(t("inbox.no_validate_permission"), icon=":material/lock:")
        elif st.button(_validate_label, type="primary", icon=":material/check_circle:"):
            # Le brouillon édité est comparé AVANT la reprise du graphe : après `invoke`,
            # l'état porte déjà la version modifiée et la comparaison ne dirait plus rien.
            _draft_was_edited = edited_draft != original_draft
            with st.spinner("Reprise du graphe et écriture dans Google Sheets..."):
                try:
                    graph_config = {"configurable": {"thread_id": st.session_state.thread_id}}
                    # Si l'humain a modifié le brouillon, on met à jour l'état AVANT de reprendre
                    # le graphe : action_node lit draft_response depuis l'état, donc HubSpot/
                    # Sheets/le brouillon Gmail doivent recevoir la version éditée, pas l'originale.
                    if _draft_was_edited:
                        aca_graph.app.update_state(graph_config, {"draft_response": edited_draft})
                        analytics_store.record_edit(
                            st.session_state.thread_id, original_draft, edited_draft,
                        )
                        # Consigné séparément de la validation : « qui a réécrit ce que l'IA
                        # proposait, et de combien » est une question d'audit distincte de
                        # « qui a autorisé l'écriture CRM ».
                        _audit(
                            activity_log.ACTION_DRAFT_EDITED, target_type="thread",
                            target_id=st.session_state.thread_id,
                            details={"caractères_avant": len(original_draft),
                                     "caractères_après": len(edited_draft)},
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
                    # §17 — la même validation, dans le journal d'activité : `audit_log` reste
                    # le registre restreint et durable des engagements CRM (jamais purgé), ce
                    # journal-ci porte le contexte technique (poste, IP, session) et fait
                    # voisiner l'événement avec tout le reste de ce qu'a fait cette personne.
                    _audit(
                        activity_log.ACTION_LEAD_VALIDATED, actor=validated_by or None,
                        target_type="thread", target_id=st.session_state.thread_id,
                        details={"classification": res.get("classification", ""),
                                 "expéditeur": res.get("email_raw", {}).get("sender", ""),
                                 "brouillon_modifié": _draft_was_edited},
                    )
                    # Tableau de bord : ferme la mesure de temps de réponse pour ce thread.
                    analytics_store.record_validation(st.session_state.thread_id)
                    # Suivi des relances (no-op si pas de fil Gmail — saisie manuelle par exemple).
                    followup_store.track(
                        st.session_state.thread_id, res.get("gmail_thread_id"),
                        res.get("email_raw", {}).get("sender", ""),
                        res.get("email_raw", {}).get("subject", ""),
                    )
                    # §19 — l'envoi programmé est créé APRÈS la reprise du graphe, parce que
                    # `action_node` est ce qui crée le brouillon Gmail : son identifiant n'existe
                    # pas avant. Programmer l'envoi d'un brouillon inexistant produirait une tâche
                    # condamnée à échouer à l'échéance, sans que personne le sache d'ici là.
                    _draft_id = final.get("gmail_draft_id")
                    if _due_epoch and _draft_id:
                        task_store.schedule_send(
                            _due_epoch, thread_id=st.session_state.thread_id,
                            gmail_draft_id=_draft_id,
                            gmail_thread_id=res.get("gmail_thread_id"),
                            label=_lead_label, created_by=validated_by,
                        )
                        _when = datetime.fromtimestamp(_due_epoch).strftime("%d/%m/%Y à %H:%M")
                        st.success(t("inbox.send_scheduled_ok", when=_when),
                                   icon=":material/schedule_send:")
                        _audit(
                            activity_log.ACTION_TASK_SCHEDULED, actor=validated_by or None,
                            target_type="envoi", target_id=st.session_state.thread_id,
                            details={"échéance": _when, "destinataire": _sender_label},
                        )
                    elif _due_epoch and not _draft_id:
                        # Le cas se produit si la création du brouillon Gmail a échoué juste avant
                        # (quota, jeton expiré). Le dire tout de suite : sinon la personne repart
                        # convaincue que son message partira demain matin.
                        st.warning(t("inbox.send_scheduled_no_draft"), icon=":material/error:")
                    # On efface le résultat pour passer au suivant
                    st.session_state.gmail_attachments_raw = []
                    st.session_state.gmail_message_id = None
                    st.session_state.gmail_thread_id = None
                    st.session_state.pending_clarification = None
                    del st.session_state.result
                except Exception as e:
                    _safe_error(t("inbox.validation_error"), e)
                    # Une validation qui échoue en cours de route est l'événement le plus utile
                    # à retrouver plus tard : elle peut avoir écrit dans Sheets sans écrire
                    # dans HubSpot, ou l'inverse. Le journal en garde la trace horodatée.
                    _audit(
                        activity_log.ACTION_LEAD_VALIDATED,
                        outcome=activity_log.OUTCOME_FAILURE, target_type="thread",
                        target_id=st.session_state.thread_id,
                        details={"erreur": e.__class__.__name__},
                    )

        # Rejet explicite : miroir de `_do_reject` de l'API (aca/api.py) — AUCUNE écriture CRM,
        # on ne reprend PAS le graphe. On retire le lead de la file d'attente (no-op si saisie
        # manuelle, non issue du poller) et on efface le résultat pour passer au suivant. Sans ce
        # bouton, un lead ne pouvait qu'être validé ou abandonné silencieusement — jamais rejeté
        # de façon traçable, alors que Streamlit est désormais la surface opérationnelle
        # principale. (Comme l'API, on ne journalise pas le rejet dans audit_log ; auditer les
        # rejets est un item §15 futur si besoin.)
        if _can(user_store.PERM_REJECT_LEAD) and st.button(
            t("inbox.reject_button"), icon=":material/cancel:"
        ):
            queue_store.mark_rejected(st.session_state.thread_id)
            # Le rejet n'était tracé NULLE PART avant §17 : `audit_log` ne connaît que les
            # validations, si bien qu'un lead écarté était indistinguable d'un lead jamais
            # traité. C'est pourtant la décision commerciale qu'on veut pouvoir expliquer.
            _audit(
                activity_log.ACTION_LEAD_REJECTED, target_type="thread",
                target_id=st.session_state.thread_id,
                details={"classification": res.get("classification", ""),
                         "expéditeur": res.get("email_raw", {}).get("sender", "")},
            )
            st.session_state.gmail_attachments_raw = []
            st.session_state.gmail_message_id = None
            st.session_state.gmail_thread_id = None
            st.session_state.pending_clarification = None
            del st.session_state.result
            st.info(t("inbox.rejected_info"), icon=":material/cancel:")

        # §19 — rappel personnel daté sur ce lead. Délibérément INDÉPENDANT de la validation :
        # « je m'en occupe mardi » est une intention qui existe qu'on valide, qu'on rejette ou
        # qu'on laisse en attente. Le lier au bouton Valider l'aurait rendu inaccessible dans
        # exactement les cas où il sert le plus (un lead qu'on ne sait pas encore trancher).
        # Ce n'est pas une relance : rien ne part vers le prospect, la notification revient à
        # l'équipe (cf. `relance.py` pour la relance commerciale, qui est un autre objet).
        if "result" in st.session_state:
            with st.expander(t("inbox.reminder_expander"), icon=":material/alarm:"):
                st.caption(t("inbox.reminder_caption"))
                # `st.form` plutôt que trois widgets libres : les trois champs décrivent UNE seule
                # intention (« rappelle-moi ceci, à ce moment »), et hors formulaire chacun
                # déclenche un rerun à la moindre frappe — la validation par Entrée dans la note
                # relançait le script et repliait l'accordéon avant qu'on ait pu cliquer.
                # Trouvé en vérifiant le parcours dans un vrai navigateur, pas en relisant le code.
                with st.form(f"reminder_form_{st.session_state.thread_id}"):
                    _note = st.text_input(
                        t("inbox.reminder_note"),
                        placeholder=t("inbox.reminder_placeholder"),
                    )
                    _rc1, _rc2 = st.columns(2)
                    _rd = _rc1.date_input(
                        t("inbox.reminder_date"),
                        value=(datetime.now() + timedelta(days=2)).date(),
                        min_value=datetime.now().date(),
                    )
                    _rh = _rc2.time_input(
                        t("inbox.reminder_time"), value=dt_time(9, 0), step=900,
                    )
                    _rem_submitted = st.form_submit_button(
                        t("inbox.reminder_button"), icon=":material/add_alert:",
                    )
                if _rem_submitted:
                    _when_dt = datetime.combine(_rd, _rh)
                    if not _note.strip():
                        # Un rappel sans texte arriverait dans deux jours sans dire de quoi il
                        # s'agit : autant ne pas le créer.
                        st.warning(t("inbox.reminder_needs_note"), icon=":material/edit:")
                    elif _when_dt <= datetime.now():
                        st.warning(t("inbox.reminder_past"), icon=":material/schedule:")
                    else:
                        task_store.add_reminder(
                            _when_dt.timestamp(), _note.strip(),
                            thread_id=st.session_state.thread_id,
                            label=(info.get("entreprise")
                                   or res.get("email_raw", {}).get("sender", "")),
                            created_by=_current_user().get("username") or "",
                        )
                        _audit(
                            activity_log.ACTION_TASK_SCHEDULED, target_type="rappel",
                            target_id=st.session_state.thread_id,
                            details={"échéance": _when_dt.strftime("%d/%m/%Y à %H:%M")},
                        )
                        st.success(
                            t("inbox.reminder_ok", when=_when_dt.strftime("%d/%m/%Y à %H:%M")),
                            icon=":material/check_circle:",
                        )

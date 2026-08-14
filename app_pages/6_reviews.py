"""
Onglet « Relectures » (§20) — un opérateur transmet à un administrateur des e-mails précis qu'il ne
veut pas trancher seul, plusieurs d'un seul geste.

Voir `aca/storage/review_store.py` pour le raisonnement métier (pourquoi un registre distinct des
tâches datées) et `aca/ui/shared.py` pour le contexte de la découpe en pages `st.navigation`.

La page sert **deux rôles à la fois**, et l'ordre des blocs suit cette réalité : ce qu'on a reçu
d'abord (c'est une file d'attente, elle bloque quelqu'un d'autre), ce qu'on envoie ensuite, ce qu'on
a déjà envoyé en dernier. Deux pages séparées auraient obligé un administrateur — qui reçoit ET
envoie — à changer d'écran pour un même sujet.
"""
from datetime import datetime, timedelta

import streamlit as st

from aca.core import ui_kit
from aca.storage import activity_log, analytics_store, queue_store, review_store, user_store
from aca.ui.shared import (
    audit as _audit,
    can as _can,
    current_user as _current_user,
    load_queued_thread,
    safe_error as _safe_error,
    t,
)

_user = _current_user()
_username = _user.get("username") or ""
_role = _user.get("role") or ""

st.caption(t("reviews.caption"))

# ── Ce qui m'attend ───────────────────────────────────────────────────────────────────────────
received = review_store.list_for(_username, _role)
st.html(ui_kit.section(
    t("reviews.received_header", n=len(received)), t("reviews.received_sub"), icon="inbox",
))

if not received:
    st.html(ui_kit.empty_state(
        t("reviews.received_empty_title"), t("reviews.received_empty_body"), icon="task_alt",
    ))
else:
    for batch in review_store.group_by_batch(received):
        _urgent = batch["priority"] == review_store.PRIORITY_HIGH
        with st.container(border=True):
            st.html(ui_kit.chip_row([
                (t("reviews.chip_from", who=batch["requester"]), "info", "person"),
                (batch["created_at"], "", "schedule"),
                *([("Prioritaire", "danger", "priority_high")] if _urgent else []),
                (t("reviews.chip_count", n=len(batch["items"])), "", "mail"),
            ]))
            if batch["note"]:
                st.markdown(f"**{batch['note']}**")

            for item in batch["items"]:
                with st.container(border=True):
                    st.markdown(f"**{item['subject'] or '(sans objet)'}**")
                    st.caption(" · ".join(x for x in (
                        item["sender"], item["classification"], item["created_at"]) if x))
                    col_open, col_ok, col_no = st.columns(3)
                    if col_open.button(t("reviews.open_lead"), icon=":material/open_in_new:",
                                       key=f"rv_open_{item['id']}"):
                        # `mark_seen` AVANT la bascule de page : après `st.switch_page`, le reste
                        # de ce script ne s'exécute pas.
                        review_store.mark_seen(item["id"])
                        try:
                            load_queued_thread(item["thread_id"])
                        except Exception as e:  # noqa: BLE001
                            # Un lead purgé par la rétention n'a plus d'état dans le checkpointer.
                            # La demande, elle, garde son intitulé (`review_store` recopie l'objet
                            # et l'expéditeur exprès pour ce cas) : on le dit et on reste ici
                            # plutôt que d'envoyer la personne sur un écran vide.
                            _safe_error(t("reviews.open_failed"), e)
                        else:
                            _audit(
                                activity_log.ACTION_QUEUE_OPENED, target_type="relecture",
                                target_id=item["thread_id"],
                                details={"demandeur": item["requester"]},
                            )
                            st.switch_page("app_pages/1_inbox.py")

                    _note_key = f"rv_note_{item['id']}"
                    if col_ok.button(t("reviews.resolve"), icon=":material/check_circle:",
                                     type="primary", key=f"rv_ok_{item['id']}"):
                        if review_store.resolve(item["id"], _username,
                                                st.session_state.get(_note_key, "")):
                            _audit(
                                activity_log.ACTION_REVIEW_RESOLVED, target_type="relecture",
                                target_id=str(item["id"]),
                                details={"demandeur": item["requester"],
                                         "objet": item["subject"],
                                         "réponse": st.session_state.get(_note_key, "")},
                            )
                        st.rerun()
                    if col_no.button(t("reviews.dismiss"), icon=":material/do_not_disturb_on:",
                                     key=f"rv_no_{item['id']}"):
                        if review_store.dismiss(item["id"], _username,
                                                st.session_state.get(_note_key, "")):
                            _audit(
                                activity_log.ACTION_REVIEW_DISMISSED, target_type="relecture",
                                target_id=str(item["id"]),
                                details={"demandeur": item["requester"],
                                         "objet": item["subject"]},
                            )
                        st.rerun()
                    st.text_input(t("reviews.answer_label"), key=_note_key,
                                  placeholder=t("reviews.answer_placeholder"))

            if len(batch["items"]) > 1:
                # Miroir exact de l'envoi groupé : ce qui a été transmis en un geste doit pouvoir
                # être répondu en un geste quand la réponse vaut pour tout le lot.
                _batch_note_key = f"rv_batch_note_{batch['batch_id']}"
                col_all, col_all_note = st.columns([1, 2])
                if col_all.button(t("reviews.resolve_batch", n=len(batch["items"])),
                                  icon=":material/done_all:",
                                  key=f"rv_all_{batch['batch_id']}"):
                    closed = review_store.resolve_batch(
                        batch["batch_id"], _username,
                        st.session_state.get(_batch_note_key, ""),
                    )
                    _audit(
                        activity_log.ACTION_REVIEW_RESOLVED, target_type="lot-relecture",
                        target_id=batch["batch_id"],
                        details={"demandeur": batch["requester"], "clôturées": closed},
                    )
                    st.rerun()
                col_all_note.text_input(t("reviews.answer_label"), key=_batch_note_key,
                                        placeholder=t("reviews.answer_batch_placeholder"),
                                        label_visibility="collapsed")

st.divider()

# ── Transmettre des e-mails ───────────────────────────────────────────────────────────────────
# Réservé à qui traite réellement des leads. Un rôle `viewer` consulte : lui permettre de distribuer
# du travail à un administrateur dépasserait ce que « lecture seule » annonce.
if not _can(user_store.PERM_VALIDATE_LEAD) and not _can(user_store.PERM_REJECT_LEAD):
    st.info(t("reviews.no_send_permission"), icon=":material/lock:")
else:
    st.html(ui_kit.section(t("reviews.send_header"), t("reviews.send_sub"), icon="send"))

    source = st.segmented_control(
        t("reviews.source_label"),
        options=["queue", "recent"],
        default="queue",
        format_func=lambda key: {"queue": t("reviews.source_queue"),
                                 "recent": t("reviews.source_recent")}[key],
        key="rv_source",
    )

    if source == "recent":
        # Les e-mails déjà analysés, même validés : un lead tranché la semaine dernière peut
        # parfaitement mériter un second regard, et s'en tenir à la file d'attente interdirait
        # précisément le cas « j'ai un doute sur ce que j'ai fait hier ».
        events = analytics_store.list_events(
            start=datetime.now() - timedelta(days=30), limit=200)
        candidates = [{"thread_id": e["thread_id"], "sender": e["sender"],
                       "subject": "", "classification": e["classification"],
                       "created_at": e["classified_at"]} for e in events]
    else:
        candidates = [{"thread_id": item["thread_id"], "sender": item["sender"],
                       "subject": item["subject"], "classification": "",
                       "created_at": item["created_at"]}
                      for item in queue_store.list_pending()]

    if not candidates:
        st.html(ui_kit.empty_state(
            t("reviews.no_candidates_title"), t("reviews.no_candidates_body"),
            icon="mark_email_read",
        ))
    else:
        st.caption(t("reviews.pick_hint"))
        table = st.dataframe(
            [{"Objet": c["subject"] or "(sans objet)", "Expéditeur": c["sender"],
              "Catégorie": c["classification"] or "—", "Date": c["created_at"]}
             for c in candidates],
            hide_index=True, width="stretch",
            on_select="rerun", selection_mode="multi-row", key="rv_picker",
        )
        picked = [candidates[index] for index in table.selection.rows]

        # Destinataires : « tous les administrateurs » par défaut, ou une personne nommée. Le défaut
        # compte — dans une petite équipe, adresser à la fonction évite qu'une demande reste bloquée
        # derrière l'absence d'une seule personne.
        admins = [account["username"] for account in user_store.list_users()
                  if account["role"] == user_store.ROLE_ADMIN and not account["disabled"]]
        options = [review_store.RECIPIENT_ADMINS] + admins
        with st.form("rv_send_form"):
            recipient = st.selectbox(
                t("reviews.recipient_label"), options=options,
                format_func=lambda value: (t("reviews.recipient_all_admins")
                                           if value == review_store.RECIPIENT_ADMINS else value),
            )
            note = st.text_area(t("reviews.note_label"), placeholder=t("reviews.note_placeholder"),
                                height=80)
            urgent = st.toggle(t("reviews.priority_label"), value=False,
                               help=t("reviews.priority_help"))
            submitted = st.form_submit_button(
                t("reviews.send_button", n=len(picked)), type="primary",
                icon=":material/forward_to_inbox:",
            )
        if submitted:
            if not picked:
                st.warning(t("reviews.none_picked"), icon=":material/rule:")
            elif not admins and recipient == review_store.RECIPIENT_ADMINS:
                # Cas réel d'un déploiement en mode secret partagé : aucun compte nominatif n'existe,
                # donc personne ne verra jamais la demande. Le dire tout de suite vaut mieux
                # qu'écrire une ligne que rien n'affichera.
                st.error(t("reviews.no_admin_account"), icon=":material/person_off:")
            else:
                result = review_store.create_batch(
                    picked, requester=_username or "(anonyme)", recipient=recipient, note=note,
                    priority=(review_store.PRIORITY_HIGH if urgent
                              else review_store.PRIORITY_NORMAL),
                )
                _audit(
                    activity_log.ACTION_REVIEW_REQUESTED, target_type="lot-relecture",
                    target_id=result["batch_id"],
                    details={"destinataire": recipient, "e-mails": result["count"],
                             "priorité": "haute" if urgent else "normale", "note": note},
                )
                st.success(
                    t("reviews.sent_ok", n=result["count"],
                      who=(t("reviews.recipient_all_admins")
                           if recipient == review_store.RECIPIENT_ADMINS else recipient)),
                    icon=":material/check_circle:",
                )

st.divider()

# ── Ce que j'ai transmis ──────────────────────────────────────────────────────────────────────
sent = review_store.list_sent_by(_username)
st.html(ui_kit.section(t("reviews.sent_header"), t("reviews.sent_sub"), icon="outbox"))
if not sent:
    st.caption(t("reviews.sent_empty"))
else:
    st.dataframe(
        [{"Date": r["created_at"], "Objet": r["subject"] or r["sender"] or "—",
          "Destinataire": (t("reviews.recipient_all_admins")
                           if r["recipient"] == review_store.RECIPIENT_ADMINS else r["recipient"]),
          "Statut": r["status_label"], "Vu le": r["seen_at"] or "—",
          "Réponse": r["resolution_note"] or "—"}
         for r in sent],
        hide_index=True, width="stretch",
    )

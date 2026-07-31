import streamlit as st
from aca.storage import activity_log, queue_store, user_store
from aca.core import branding
from aca.core import demo
from aca.core import prod_check
from aca.ui.shared import (
    DEV_USERNAME,
    audit as _audit,
    can as _can,
    check_auth as _check_auth,
    current_user as _current_user,
    language_switcher,
    load_queued_thread,
    safe_error as _safe_error,
    t,
)
from aca.integrations import gmail_reader, sheets
from aca.ingestion import ingest

# §17 — l'apparence est résolue AVANT `set_page_config`, qui doit rester la première commande
# Streamlit du script : le titre d'onglet et l'icône font partie de la marque au même titre que les
# couleurs. `branding.resolve()` n'importe pas Streamlit, l'appeler ici est donc sans effet de bord.
BRAND = branding.resolve()

st.set_page_config(
    page_title=BRAND["BRAND_NAME"],
    page_icon=":material/smart_toy:",
    layout="wide",
)

# Feuille de style de marque + animations. Injectée à chaque rerun (c'est ce qui rend un changement
# de couleur immédiat, sans redémarrage) ; la couche `config.toml` complémentaire, elle, s'applique
# au rechargement de la page — cf. la docstring de `aca/core/branding.py`.
st.html(branding.css(BRAND))
st.logo(branding.logo_for_streamlit(BRAND), size="large")

# Langue de l'interface — lue et appliquée AVANT le contrôle d'accès : l'écran de connexion
# lui-même est du chrome principal (Identifiant/Mot de passe/Se connecter, cf. aca/ui/shared.py),
# donc doit pouvoir être basculé AVANT que quiconque soit authentifié, pas seulement après. Placer
# ce bloc après `st.stop()` (comme une première version l'avait fait) rendrait le sélecteur
# invisible tant que personne n'est connecté — exactement l'écran où il sert le plus.
with st.sidebar:
    language_switcher()

# §15.1.5 : en `ACA_ENV=production`, une configuration ouverte fait échouer le démarrage plutôt que
# de servir une UI sans garde. En développement (défaut), no-op total.
prod_check.enforce()

if not _check_auth(BRAND):
    st.stop()

# §18 tangent (retour utilisateur) : le bandeau de sécurité vivait ici, au-dessus du contenu de
# CHAQUE page, poussant tout le reste vers le bas en permanence — visible mais rarement utile à cet
# instant précis (personne ne règle une exposition publique depuis l'onglet « Nouvel e-mail »).
# Déplacé dans « Réglages » (`app_pages/5_settings.py`), l'endroit où un administrateur va déjà
# chercher une information de configuration, sans disparaître : toujours admin-only, toujours basé
# sur le même `prod_check.check()`.

# En-tête de marque (§17) : remplace le couple `st.title`/`st.caption`. Les pastilles portent le
# contexte que l'utilisateur doit avoir sous les yeux en permanence — qui il est, combien
# d'analyses l'attendent, et si l'on est en démonstration.
_pills = []
_signed_in = _current_user()
if _signed_in.get("username") and _signed_in["username"] != DEV_USERNAME:
    _pills.append((f"{_signed_in['username']} · {_signed_in['role']}", "normal"))
_pending_count = len(queue_store.list_pending())
if _pending_count:
    _pills.append((t("hero.pending_pill", n=_pending_count), "normal"))
# §5.5 des suggestions — notification dans l'application : « N nouvelles analyses depuis votre
# dernière visite ». Le compteur existait déjà ; il manquait la notion de « déjà vu ». Portée
# volontairement modeste (par session, pas par compte persistant) : `setdefault` ne fixe la
# référence qu'UNE fois, à la première page authentifiée après connexion — donc « depuis que
# vous vous êtes connecté(e) », jusqu'à ce que le bouton « Marquer comme vu » de la barre
# latérale la remette à jour.
st.session_state.setdefault("_seen_pending_count", _pending_count)
_new_since_login = max(0, _pending_count - st.session_state["_seen_pending_count"])
if _new_since_login:
    _pills.append((t("hero.new_since_login_pill", n=_new_since_login), "alert"))
if demo.is_enabled():
    # Pastille permanente, pas seulement dans l'onglet : pendant une démonstration, l'écran est
    # parfois projeté ou partagé et il ne doit jamais y avoir d'ambiguïté sur le fait que rien
    # n'est réel. En rouge sur fond clair, c'est la seule pastille qu'on ne peut pas manquer.
    _pills.append((t("hero.demo_mode_pill"), "alert"))
st.html(branding.hero_html(BRAND, _pills))

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

# Routeur multi-pages (§18) — remplace l'ancien `st.tabs`/`with tab_x:` : chaque page vit désormais
# dans son propre fichier (`app_pages/*.py`), `st.session_state` restant partagé entre elles comme
# documenté dans `aca/ui/shared.py`. Position "top" (peu de pages, 3-5) : la barre latérale reste
# dédiée au contenu propre à ACA (file d'attente, import Gmail, curation) plutôt que de se partager
# avec le menu de navigation. Le « Journal d'activité » (§17) n'apparaît que pour un administrateur :
# il énumère les tentatives de connexion échouées, les adresses IP et les postes de chaque collègue.
# Le montrer à un opérateur reviendrait à faire de la surveillance mutuelle un dispositif ouvert, ce
# qui n'est ni le besoin exprimé (« que l'admin voie ») ni acceptable vis-à-vis des personnes tracées.
_pages = [
    st.Page("app_pages/1_inbox.py", title=t("nav.inbox"), icon=":material/mail:", default=True),
    st.Page("app_pages/2_dashboard.py", title=t("nav.dashboard"), icon=":material/insights:"),
    st.Page("app_pages/3_history.py", title=t("nav.history"), icon=":material/history:"),
]
if _can(user_store.PERM_MANAGE_USERS):
    _pages.append(
        st.Page("app_pages/4_activity.py", title=t("nav.activity"), icon=":material/policy:"),
    )
_pages.append(st.Page("app_pages/5_settings.py", title=t("nav.settings"), icon=":material/settings:"))
pg = st.navigation(_pages, position="top")

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
        if st.button(t("sidebar.logout"), icon=":material/logout:"):
            _audit(activity_log.ACTION_LOGOUT)
            st.session_state.session = None
            st.rerun()
    st.divider()
    pending_queue = queue_store.list_pending()
    st.subheader(t("sidebar.queue_header", n=len(pending_queue)), anchor=False)
    if _new_since_login:
        st.caption(f":material/notifications_active: {t('sidebar.new_since_login_caption', n=_new_since_login)}")
        if st.button(t("sidebar.mark_seen"), icon=":material/done_all:"):
            st.session_state["_seen_pending_count"] = _pending_count
            st.rerun()
    st.caption(
        "E-mails traités automatiquement par le poller en arrière-plan (`poller.py`), en attente "
        "de validation humaine."
    )
    if not pending_queue:
        st.caption(t("sidebar.queue_empty"))
    for item in pending_queue:
        with st.container(border=True):
            st.markdown(f"**{item['subject']}**")
            st.caption(f"{item['sender']} · {item['created_at']}")
            if st.button(t("sidebar.open_button"), icon=":material/open_in_new:", key=f"open_{item['thread_id']}"):
                load_queued_thread(item["thread_id"])
                _audit(
                    activity_log.ACTION_QUEUE_OPENED, target_type="thread",
                    target_id=item["thread_id"], details={"expéditeur": item["sender"]},
                )
                # Bascule sur la page « Nouvel e-mail » (§18) : avec l'ancien `st.tabs`, TOUS les
                # onglets s'exécutaient à chaque rerun, donc le résultat chargé apparaissait quel
                # que soit l'onglet visuellement actif. `st.navigation` n'exécute que la page
                # sélectionnée — sans ce changement de page explicite, le résultat resterait
                # invisible tant que l'utilisateur ne clique pas lui-même sur « Nouvel e-mail ».
                st.switch_page("app_pages/1_inbox.py")

    st.divider()
    st.subheader(t("sidebar.gmail_header"), anchor=False)
    if st.button(t("sidebar.gmail_search"), icon=":material/mail:"):
        with st.spinner("Connexion à Gmail..."):
            try:
                st.session_state.gmail_service = gmail_reader.get_gmail_service()
                st.session_state.gmail_unread = gmail_reader.list_unread_emails(st.session_state.gmail_service)
            except Exception as e:
                _safe_error("Erreur de connexion Gmail", e)

    if st.session_state.get("gmail_unread"):
        options = {f"{m['subject']} — {m['sender']}": m["id"] for m in st.session_state.gmail_unread}
        choice = st.selectbox(t("sidebar.gmail_unread_label"), list(options.keys()))
        if st.button(t("sidebar.gmail_load"), icon=":material/download:"):
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
                    _audit(
                        activity_log.ACTION_GMAIL_IMPORTED, target_type="e-mail",
                        target_id=email["id"],
                        details={"expéditeur": email["sender"], "objet": email["subject"],
                                 "pièces_jointes": len(email["attachments"])},
                    )
                    # Même raison que le bouton « Ouvrir » ci-dessus : l'e-mail chargé doit
                    # apparaître dans le formulaire de la page « Nouvel e-mail », qui ne s'exécute
                    # plus automatiquement à chaque rerun sous `st.navigation`.
                    st.switch_page("app_pages/1_inbox.py")

    # Curation de la base de connaissances (ingestion + validation des réponses trouvées par
    # `veille`) : réservée au rôle `admin` (§15.1.6). Ce qui entre ici devient un contexte RAG que
    # le Stratège citera dans de futures propositions commerciales — c'est une décision éditoriale,
    # pas une tâche de traitement quotidien comme valider un lead. Écrans admin/curation : laissés
    # en français dans cette passe (chrome principal traduit, cf. aca/core/i18n.py).
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
                    # Un échec d'ingestion est consigné au même titre qu'un succès : sans ça, le
                    # journal laisserait croire que personne n'a touché à la base de connaissances
                    # ce jour-là, alors que quelqu'un a bien tenté d'y écrire.
                    _audit(
                        activity_log.ACTION_KNOWLEDGE_INGESTED,
                        outcome=activity_log.OUTCOME_FAILURE, target_type="document",
                        target_id=kb_file.name, details={"mode": "replace" if kb_replace else "append"},
                    )
                else:
                    _audit(
                        activity_log.ACTION_KNOWLEDGE_INGESTED, target_type="document",
                        target_id=kb_file.name,
                        details={"lignes": n, "mode": "replace" if kb_replace else "append"},
                    )
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
                    # Approuver une réponse trouvée en ligne la rend citable par le Stratège dans
                    # de futures propositions commerciales : c'est une décision éditoriale
                    # engageante, donc la question consignée compte autant que l'auteur.
                    _audit(
                        activity_log.ACTION_KNOWLEDGE_APPROVED, target_type="faq",
                        target_id=str(row["row_index"]), details={"question": row["question"]},
                    )
                    st.rerun()
                if col_ko.button("Rejeter", icon=":material/close:", key=f"reject_{row['row_index']}"):
                    sheets.reject_knowledge_row(row["row_index"])
                    _audit(
                        activity_log.ACTION_KNOWLEDGE_REJECTED, target_type="faq",
                        target_id=str(row["row_index"]), details={"question": row["question"]},
                    )
                    st.rerun()

    st.divider()
    with st.expander("Confidentialité des données", icon=":material/policy:"):
        st.caption(
            "Résumé : votre e-mail et vos pièces jointes sont analysés pour qualifier votre "
            "demande, une validation humaine a toujours lieu avant tout enregistrement CRM, et "
            "vos données sont conservées 365 jours par défaut avant purge automatique. "
            "Politique complète : `docs/PRIVACY_POLICY.md`."
        )

    st.caption(t("sidebar.models_caption"))

# Exécute la page sélectionnée (§18) — chaque `with tab_x:` d'origine vit maintenant dans son
# propre fichier sous app_pages/, voir aca/ui/shared.py pour le contexte de la découpe.
pg.run()

# ── Pied de page de marque (§17) ──────────────────────────────────────────────────────────────
# Porte l'attribution du client final quand elle est configurée. Placé hors des onglets : un pied
# de page qui n'apparaît que dans un onglet n'est pas un pied de page.
_footer_bits = [BRAND["BRAND_NAME"]]
if BRAND["BRAND_COMPANY"]:
    _footer_bits.append(t("footer.deployed_for", company=BRAND["BRAND_COMPANY"]))
_footer_bits.append(t("footer.human_validation"))
st.html(f'<div class="aca-footer">{" · ".join(_footer_bits)}</div>')

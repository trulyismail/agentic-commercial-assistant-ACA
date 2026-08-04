"""Onglet « Réglages » (§18) — extrait de `ui.py`. Voir `aca/ui/shared.py` pour le contexte de la
découpe en pages `st.navigation`. Accessible à tout rôle authentifié ; `_can(...)` contrôle
localement ce qui est éditable (comme dans `ui.py` d'origine, pas de garde au niveau de la page)."""
import os
from datetime import datetime

import streamlit as st

from aca.core import branding, intake_window, prod_check, ui_kit
from aca.storage import activity_log, config_store, task_store, user_store
from aca.ui.shared import audit as _audit, audit_denied as _audit_denied, can as _can, safe_error as _safe_error, t

BRAND = branding.resolve()

# §18 tangent (retour utilisateur) : déplacé depuis `ui.py`, où ce bandeau s'affichait au-dessus du
# contenu de CHAQUE page (poussant tout le reste vers le bas en permanence) même si rien n'y
# invitait à agir dessus dans l'instant. « Réglages » est l'endroit où un administrateur va déjà
# chercher une information de configuration — même garde (`PERM_MANAGE_USERS`), même
# `prod_check.check()`, juste un emplacement qui ne s'impose plus partout.
if _can(user_store.PERM_MANAGE_USERS):
    _security_problems = prod_check.check()
    if _security_problems:
        with st.expander(
            f"Sécurité : {len(_security_problems)} point(s) à corriger avant une mise en ligne",
            icon=":material/shield:", key="security_banner",
        ):
            st.caption(
                "Sans conséquence en usage local (mode développement). À corriger avant toute "
                "exposition publique — cf. `docs/DEPLOYMENT_HARDENING.md`."
            )
            for _problem in _security_problems:
                st.markdown(f"- {_problem}")
    st.divider()

# §15.1.6 : ces réglages pilotent où partent les alertes et ce que le Stratège promet
# (lien Calendly, adresses de routage, cadence de relance) — un opérateur peut les consulter,
# seul un administrateur les modifie.
if not _can(user_store.PERM_EDIT_SETTINGS):
    _audit_denied(user_store.PERM_EDIT_SETTINGS, "onglet Réglages")
    st.info(t("settings.readonly_notice"), icon=":material/lock:")
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
            t("settings.save_button"), type="primary", icon=":material/save:"
        )
        if submitted:
            _changes = {}
            for key, value in {
                "CALENDLY_URL": calendly_url,
                "SUPPORT_EMAIL": support_email,
                "SUPPORT_SLACK_WEBHOOK_URL": support_webhook,
                "HR_EMAIL": hr_email,
                "HR_SLACK_WEBHOOK_URL": hr_webhook,
                "RELANCE_DAYS": relance_days,
                "RELANCE_MAX_ROUNDS": relance_rounds,
            }.items():
                if value.strip() and value.strip() != current.get(key):
                    config_store.set_setting(key, value.strip())
                    _changes[key] = value.strip()
            if _changes:
                # Ces réglages décident où partent les alertes commerciales et ce que le
                # Stratège promet aux prospects : les consigner avec l'ancienne ET la nouvelle
                # valeur permet de constater un détournement (une adresse de support remplacée
                # discrètement) au lieu de seulement savoir que « quelqu'un a modifié quelque
                # chose ».
                _audit(
                    activity_log.ACTION_SETTINGS_CHANGED, target_type="réglages",
                    target_id=", ".join(sorted(_changes)),
                    details={key: {"avant": current.get(key) or "(non réglé)", "après": value}
                             for key, value in _changes.items()},
                )
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

# ── Réception automatique (§19) ─────────────────────────────────────────────────────────────
# La barre latérale disait « traités automatiquement par le poller en arrière-plan (`poller.py`) »
# et rien n'était réglable : ni l'activation, ni les horaires, ni la fréquence. C'était donc à la
# fois opaque (un nom de fichier en guise d'explication) et figé (tout passait par `.env` puis un
# redémarrage). Ce panneau expose les quatre choses qu'une équipe veut réellement décider — est-ce
# que ça tourne, quels jours, quelles heures, à quelle fréquence — et `poller.py` les relit à
# chaque cycle, donc un changement prend effet sans redémarrer quoi que ce soit.
if _can(user_store.PERM_EDIT_SETTINGS):
    st.divider()
    st.subheader(t("settings.intake_header"), anchor=False)
    st.caption(t("settings.intake_caption"))

    _cfg = intake_window.current_config()
    _now = datetime.now()
    _open_now = intake_window.is_open(_now, _cfg)
    _next = intake_window.next_opening(_now, _cfg)
    st.html(ui_kit.readout([
        (t("settings.intake_status"),
         t("settings.intake_status_open") if _open_now else t("settings.intake_status_closed"),
         "on" if _open_now else "off"),
        (t("settings.intake_next"),
         (t("settings.intake_now") if _open_now
          else (_next.strftime("%d/%m %H:%M") if _next else "—")), ""),
    ]))

    with st.form("intake_form"):
        _enabled = st.toggle(
            t("settings.intake_enabled"), value=_cfg["enabled"],
            help=t("settings.intake_enabled_help"),
        )
        _days = st.multiselect(
            t("settings.intake_days"), options=list(range(7)), default=list(_cfg["days"]),
            format_func=lambda d: intake_window.DAY_LABELS[d],
            help=t("settings.intake_days_help"),
        )
        _c1, _c2, _c3 = st.columns(3)
        _start = _c1.time_input(t("settings.intake_start"), value=_cfg["start"], step=900)
        _end = _c2.time_input(t("settings.intake_end"), value=_cfg["end"], step=900)
        # Exprimé en MINUTES dans l'interface, stocké en secondes : personne ne raisonne en
        # « 300 secondes », tout le monde raisonne en « toutes les 5 minutes ».
        _every = _c3.number_input(
            t("settings.intake_every"),
            min_value=max(1, intake_window.MIN_INTERVAL_SECONDS // 60),
            max_value=intake_window.MAX_INTERVAL_SECONDS // 60,
            value=max(1, _cfg["interval_seconds"] // 60), step=1,
            help=t("settings.intake_every_help"),
        )
        if st.form_submit_button(t("settings.intake_save"), type="primary",
                                 icon=":material/schedule:"):
            _before = intake_window.describe(_cfg)
            config_store.set_setting(intake_window.SETTING_ENABLED, "1" if _enabled else "0")
            config_store.set_setting(
                intake_window.SETTING_DAYS,
                ",".join(str(d) for d in sorted(_days)) if _days else "",
            )
            config_store.set_setting(intake_window.SETTING_START, _start.strftime("%H:%M"))
            config_store.set_setting(intake_window.SETTING_END, _end.strftime("%H:%M"))
            config_store.set_setting(intake_window.SETTING_INTERVAL, str(int(_every) * 60))
            # L'avant/après en clair : « qui a changé quelque chose » ne suffit pas quand le
            # réglage décide si les e-mails entrants sont lus ou non.
            _audit(
                activity_log.ACTION_SETTINGS_CHANGED, target_type="réception",
                target_id="intake",
                details={"avant": _before, "après": intake_window.describe()},
            )
            st.success(t("settings.intake_saved"), icon=":material/check_circle:")
            st.rerun()

# ── Tâches programmées (§19) ────────────────────────────────────────────────────────────────
# Un envoi différé qu'on ne peut pas retrouver est un envoi qu'on ne peut pas annuler : sans cette
# liste, la seule façon d'empêcher un message programmé de partir serait de supprimer le brouillon
# dans Gmail — en espérant se souvenir qu'il existe. Visible par tout rôle qui valide des leads,
# pas seulement l'administrateur : c'est l'opérateur qui programme ces envois.
if _can(user_store.PERM_VALIDATE_LEAD):
    st.divider()
    st.subheader(t("settings.tasks_header"), anchor=False)
    st.caption(t("settings.tasks_caption"))

    _pending_tasks = task_store.list_pending()
    if not _pending_tasks:
        st.caption(t("settings.tasks_empty"))
    for _task in _pending_tasks:
        with st.container(border=True):
            _left, _right = st.columns([5, 1])
            with _left:
                # `chip_row` attend des DICTS (`chip(**item)`), contrairement à `readout` qui
                # prend des tuples positionnels — deux conventions voisines dans le même module,
                # d'où la confusion qui a produit ici un TypeError à l'affichage.
                st.html(ui_kit.chip_row([
                    {"label": _task["kind_label"], "tone": "info",
                     "icon": ("schedule_send" if _task["kind"] == task_store.KIND_SEND
                              else "alarm")},
                    {"label": _task["due_at"], "tone": "warn", "icon": "event"},
                ]))
                st.markdown(f"**{_task['label'] or _task['thread_id'] or '—'}**")
                if _task["note"]:
                    st.caption(_task["note"])
                if _task["created_by"]:
                    st.caption(t("settings.tasks_by", who=_task["created_by"]))
            with _right:
                if st.button(t("settings.tasks_cancel"), key=f"cancel_task_{_task['id']}",
                             icon=":material/cancel:"):
                    task_store.cancel(_task["id"], "Annulé depuis les réglages.")
                    _audit(
                        activity_log.ACTION_TASK_CANCELLED, target_type=_task["kind"],
                        target_id=str(_task["id"]),
                        details={"échéance": _task["due_at"], "lead": _task["label"]},
                    )
                    st.rerun()

# ── Apparence / marque blanche (§17) ────────────────────────────────────────────────────────
if _can(user_store.PERM_EDIT_SETTINGS):
    st.divider()
    st.subheader(t("settings.appearance_header"), anchor=False)
    st.caption(
        "Adapte l'application au cahier des charges du client — logo, couleurs, police, "
        "densité, animations — **sans modifier le code ni redémarrer**. Les réglages sont "
        "stockés par tenant (`config_store`), comme le reste de cette page ; un champ laissé "
        "au défaut n'est pas enregistré."
    )

    _brand_stored = {
        key: value for key, value in config_store.get_all_settings().items()
        if key.startswith("BRAND_")
    }

    with st.form("branding_form"):
        preset = st.selectbox(
            "Préréglage de départ", options=list(branding.PRESETS),
            index=list(branding.PRESETS).index(BRAND[branding.PRESET_KEY])
            if BRAND[branding.PRESET_KEY] in branding.PRESETS else 0,
            help="Un point de départ cohérent. Chaque couleur ci-dessous reste modifiable "
                 "ensuite et l'emporte sur le préréglage.",
        )

        # Le formulaire est CONSTRUIT à partir de `branding.TOKENS` : ajouter un paramètre
        # d'apparence dans ce module le fait apparaître ici sans toucher à `ui.py` (même esprit
        # que `ROUTING_DESTINATIONS` ou la table `JOBS` du planificateur).
        _inputs = {}
        _groups = {}
        for key, spec in branding.TOKENS.items():
            _groups.setdefault(spec["group"], []).append((key, spec))

        for group, entries in _groups.items():
            st.markdown(f"**{group}**")
            if group in ("Couleurs", "Couleurs d'état"):
                # Les couleurs vont par rangées de quatre : un sélecteur de couleur par ligne
                # rendrait la page interminable et empêcherait de comparer la palette d'un œil.
                for start in range(0, len(entries), 4):
                    for column, (key, spec) in zip(
                        st.columns(4), entries[start:start + 4],
                    ):
                        with column:
                            _inputs[key] = st.color_picker(
                                spec["label"], value=BRAND[key], help=spec.get("help"),
                                key=f"brand_{key}",
                            )
                continue
            for key, spec in entries:
                if spec["kind"] == branding.KIND_CHOICE:
                    _inputs[key] = st.selectbox(
                        spec["label"], options=spec["choices"],
                        index=spec["choices"].index(BRAND[key])
                        if BRAND[key] in spec["choices"] else 0,
                        help=spec.get("help"), key=f"brand_{key}",
                    )
                elif spec["kind"] == branding.KIND_IMAGE:
                    _inputs[key] = None  # traité hors formulaire de texte, cf. plus bas
                    st.file_uploader(
                        spec["label"], type=sorted(branding.LOGO_MIME_TYPES),
                        help=spec.get("help"), key=f"brand_upload_{key}",
                    )
                else:
                    _inputs[key] = st.text_input(
                        spec["label"], value=BRAND[key], help=spec.get("help"),
                        key=f"brand_{key}",
                    )

        brand_submitted = st.form_submit_button(
            t("settings.apply_appearance_button"), type="primary", icon=":material/palette:",
        )

    if brand_submitted:
        _brand_changes = {}
        _logo_error = None

        uploaded_logo = st.session_state.get("brand_upload_BRAND_LOGO")
        if uploaded_logo is not None:
            try:
                encoded = branding.encode_logo(uploaded_logo.name, uploaded_logo.getvalue())
            except branding.LogoRejected as exc:
                _logo_error = str(exc)
            else:
                config_store.set_setting("BRAND_LOGO", encoded)
                _brand_changes["BRAND_LOGO"] = f"{uploaded_logo.name} " \
                                               f"({len(uploaded_logo.getvalue()) // 1024} Ko)"

        if preset != BRAND[branding.PRESET_KEY]:
            config_store.set_setting(branding.PRESET_KEY, preset)
            _brand_changes[branding.PRESET_KEY] = preset

        for key, value in _inputs.items():
            if value is None or key == "BRAND_LOGO":
                continue
            value = str(value).strip()
            # On n'enregistre que ce qui DIFFÈRE de la valeur effective courante : sans ce
            # filtre, le simple fait d'ouvrir le formulaire et de le soumettre figerait les 20
            # jetons dans `config_store`, et le préréglage n'aurait plus jamais aucun effet.
            if value and value != BRAND.get(key):
                config_store.set_setting(key, value)
                _brand_changes[key] = value

        if _logo_error:
            st.error(_logo_error, icon=":material/broken_image:")
        if _brand_changes:
            _audit(
                activity_log.ACTION_BRANDING_CHANGED, target_type="apparence",
                target_id=", ".join(sorted(_brand_changes)), details=_brand_changes,
            )
            st.success(
                f"{len(_brand_changes)} réglage(s) d'apparence appliqué(s).",
                icon=":material/check_circle:",
            )
            st.rerun()
        elif not _logo_error:
            st.info("Aucun changement à enregistrer.", icon=":material/info:")

    # Aperçu de la palette effective + contrôles d'accessibilité, hors formulaire pour refléter
    # ce qui est RÉELLEMENT appliqué (et non ce qui est en cours de saisie).
    _swatches = "".join(
        f'<div class="aca-swatch" style="background:{BRAND[key]}" title="{key}"></div>'
        for key in ("BRAND_PRIMARY", "BRAND_ACCENT", "BRAND_BACKGROUND", "BRAND_SURFACE",
                    "BRAND_SIDEBAR", "BRAND_TEXT", "BRAND_SUCCESS", "BRAND_WARNING",
                    "BRAND_DANGER", "BRAND_INFO")
    )
    st.html(f'<div class="aca-swatch-row">{_swatches}</div>')

    for problem in branding.accessibility_report(BRAND):
        # Avertir, jamais refuser : c'est la charte graphique du client. Mais la lui livrer
        # illisible sans rien dire serait notre faute, pas la sienne.
        st.warning(problem, icon=":material/contrast:")

    _custom = branding.customised_tokens(BRAND)
    col_native, col_reset = st.columns(2)
    with col_native:
        if st.button(
            "Appliquer aussi au thème natif", icon=":material/format_paint:",
            help="Réécrit la section [theme] de `.streamlit/config.toml` pour que les "
                 "composants internes de Streamlit (menus déroulants ouverts, en-têtes de "
                 "tableaux, graphiques) suivent la marque. Les autres sections du fichier sont "
                 "préservées. Effet au rechargement de la page (F5).",
        ):
            try:
                written = branding.write_config_toml(BRAND)
            except OSError as exc:
                _safe_error("Impossible d'écrire le thème natif", exc)
            else:
                _audit(
                    activity_log.ACTION_BRANDING_CHANGED, target_type="fichier",
                    target_id=written, details={"couche": "thème natif config.toml"},
                )
                st.success(
                    f"`{written}` mis à jour. Rechargez la page (F5) pour voir l'effet sur les "
                    "composants internes de Streamlit.",
                    icon=":material/check_circle:",
                )
    with col_reset:
        if st.button(
            "Réinitialiser l'apparence", icon=":material/restart_alt:",
            disabled=not _brand_stored,
            help="Efface les réglages d'apparence enregistrés et revient à la palette ACA "
                 "par défaut. N'affecte aucune donnée métier.",
        ):
            for key in _brand_stored:
                config_store.set_setting(key, "")
            _audit(
                activity_log.ACTION_BRANDING_RESET, target_type="apparence",
                target_id="tous", details={"jetons_effacés": sorted(_brand_stored)},
            )
            st.rerun()

    if _custom:
        st.caption(
            f"{len(_custom)} personnalisation(s) active(s) : "
            + ", ".join(sorted(_custom)) + "."
        )
    else:
        st.caption("Apparence par défaut (aucune personnalisation enregistrée).")

# ── Comptes et rôles (§15.1.6) ──────────────────────────────────────────────────────────────
if _can(user_store.PERM_MANAGE_USERS):
    st.divider()
    st.subheader(t("settings.accounts_header"), anchor=False)
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
        if st.form_submit_button(t("settings.create_account_button"), type="primary", icon=":material/person_add:"):
            try:
                user_store.create_user(new_username, new_password, role=new_role)
            except (user_store.UserExists, user_store.PasswordTooWeak, ValueError) as e:
                # Erreurs de saisie volontairement affichées telles quelles : elles ne
                # contiennent que ce que l'utilisateur vient de taper, aucune donnée interne
                # (contrairement à `_safe_error`, réservé aux exceptions techniques).
                st.error(str(e), icon=":material/error:")
            else:
                _audit(
                    activity_log.ACTION_USER_CREATED, target_type="compte",
                    target_id=new_username, details={"rôle": new_role},
                )
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
                    # Une élévation de privilège est l'événement le plus sensible de toute
                    # l'application : l'ancien rôle est consigné avec le nouveau, sans quoi
                    # « rôle modifié » ne dirait pas si quelqu'un vient de se faire admin.
                    _audit(
                        activity_log.ACTION_USER_ROLE_CHANGED, target_type="compte",
                        target_id=target,
                        details={"avant": current_account["role"], "après": wanted_role},
                    )
                    st.rerun()
            with col_state:
                # Désactiver plutôt que supprimer : le journal d'audit référence l'identifiant,
                # l'effacer rendrait les validations passées non attribuables.
                label = "Réactiver" if current_account["disabled"] else "Désactiver"
                if st.button(label, icon=":material/block:"):
                    user_store.set_disabled(target, not current_account["disabled"])
                    _audit(
                        activity_log.ACTION_USER_ENABLED if current_account["disabled"]
                        else activity_log.ACTION_USER_DISABLED,
                        target_type="compte", target_id=target,
                    )
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
                    # Aucun détail : le fait qu'un administrateur ait réinitialisé le mot de
                    # passe d'un tiers est l'information d'audit ; le secret lui-même n'a
                    # évidemment rien à faire dans un journal, pas même sa longueur.
                    _audit(
                        activity_log.ACTION_USER_PASSWORD_RESET, target_type="compte",
                        target_id=target,
                    )
                    st.success("Mot de passe mis à jour.", icon=":material/check_circle:")

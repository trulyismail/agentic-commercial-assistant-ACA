"""
Onglet « Journal d'activité » (§18) — extrait de `ui.py`. Réservé au rôle `admin` : cette page
n'apparaît dans la liste passée à `st.navigation()` (donc n'est routable) que si
`_can(PERM_MANAGE_USERS)` est vrai au moment de la construction du routeur dans `ui.py` — même
garde que l'ancien `if tab_activity is not None:`, pas de second contrôle ici pour rester au plus
près du comportement d'origine.
"""
import csv
import io
import os

import streamlit as st

from aca.storage import activity_log, audit_log
from aca.ui.shared import audit as _audit, t

# ── Journal d'activité (§17) ──────────────────────────────────────────────────────────────────
# Répond à trois questions dans cet ordre : « qui travaille et comment », « que s'est-il passé
# exactement », « ce journal est-il digne de foi ».
st.caption(t("activity.caption"))

act_days = st.segmented_control(
    t("activity.period_label"), options=[1, 7, 30, 90], default=7, required=True,
    format_func=lambda d: t("activity.period_24h") if d == 1 else t("activity.period_days", d=d),
    key="activity_days",
)

summary_rows = activity_log.actors_summary(days=act_days)
recent_all = activity_log.list_recent(limit=1000, days=act_days)
incidents = [r for r in recent_all if r["outcome"] != activity_log.OUTCOME_SUCCESS]

if not recent_all:
    st.info(t("activity.empty_state"), icon=":material/info:")
else:
    with st.container(horizontal=True):
        st.metric("Actions", len(recent_all), border=True)
        st.metric("Personnes actives", len(summary_rows), border=True)
        st.metric(
            "Incidents", len(incidents), border=True,
            help="Échecs de connexion, verrouillages, accès refusés et actions ayant "
                 "échoué techniquement.",
        )
        st.metric(
            "Postes distincts",
            len({r["device_id"] for r in recent_all if r["device_id"]}, ), border=True,
            help="Empreintes (adresse IP + navigateur) vues sur la période. Une empreinte "
                 "inhabituelle mérite une vérification.",
        )

    # ── Qui fait quoi ─────────────────────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(f"##### {t('activity.per_person_header')}")
        st.dataframe(
            summary_rows, hide_index=True, width="stretch",
            column_config={
                "actor": "Utilisateur", "role": "Rôle", "actions": "Actions",
                "validations": "Validations", "rejets": "Rejets",
                "incidents": st.column_config.NumberColumn(
                    "Incidents", help="Échecs et refus — une valeur non nulle mérite un "
                                      "coup d'œil au détail ci-dessous.",
                ),
                "postes": st.column_config.NumberColumn(
                    "Postes", help="Nombre d'empreintes machine distinctes.",
                ),
                "première_activité": "Première activité",
                "dernière_activité": "Dernière activité",
            },
        )

    # ── Fiche d'audit d'une personne ─────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(f"##### {t('activity.audit_sheet_header')}")
        st.caption(
            "Ce qu'une personne a fait sur la période, et **depuis quels postes**. "
            "L'empreinte machine dérive de l'adresse IP et du navigateur : elle regroupe "
            "les actions d'un même poste, elle ne prouve pas une identité matérielle."
        )
        focus = st.selectbox(
            "Utilisateur", options=[row["actor"] for row in summary_rows],
            key="activity_focus", index=0 if summary_rows else None,
        )
        if focus:
            profile = activity_log.actor_profile(focus, days=act_days)
            with st.container(horizontal=True):
                st.metric("Actions", profile["actions"], border=True)
                st.metric("Incidents", profile["incidents"], border=True)
                st.metric("Postes", len(profile["postes"]), border=True)
            col_actions, col_devices = st.columns(2)
            with col_actions:
                st.caption("Répartition des actions")
                st.dataframe(
                    [{"Action": row["label"], "Compte": row["compte"]}
                     for row in profile["par_action"]] or [{"Action": "—", "Compte": 0}],
                    hide_index=True, width="stretch",
                )
            with col_devices:
                st.caption("Postes utilisés")
                st.dataframe(
                    [{"Poste": d["poste"], "Adresse IP": d["ip"], "Actions": d["actions"],
                      "Vu le": d["dernière_activité"]} for d in profile["postes"]]
                    or [{"Poste": "—", "Adresse IP": "—", "Actions": 0, "Vu le": "—"}],
                    hide_index=True, width="stretch",
                )

    # ── Frise détaillée ──────────────────────────────────────────────────────────────────────
    with st.container(border=True):
        st.markdown(f"##### {t('activity.events_detail_header')}")
        col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
        with col_f1:
            filter_actor = st.selectbox(
                "Utilisateur", options=[None] + activity_log.distinct_values("actor"),
                format_func=lambda v: "Tous" if v is None else v, key="act_filter_actor",
            )
        with col_f2:
            filter_action = st.selectbox(
                "Type d'action", options=[None] + activity_log.distinct_values("action"),
                format_func=lambda v: "Toutes" if v is None
                else activity_log.ACTION_LABELS.get(v, v),
                key="act_filter_action",
            )
        with col_f3:
            only_sensitive = st.toggle(
                "Sensibles", value=False, key="act_filter_sensitive",
                help="Ne garder que les actions qui changent les droits, la configuration "
                     "ou ce que l'IA affirmera aux prospects.",
            )
        filter_incidents = st.toggle(
            "Incidents seulement (échecs, refus, verrouillages)", value=False,
            key="act_filter_incidents",
        )
        search = st.text_input(
            "Rechercher", placeholder="utilisateur, action, expéditeur, IP, poste, ID...",
            key="act_search",
        )

        rows = activity_log.list_recent(
            limit=500, days=act_days, actor=filter_actor, action=filter_action,
            sensitive_only=only_sensitive,
        )
        if filter_incidents:
            rows = [r for r in rows if r["outcome"] != activity_log.OUTCOME_SUCCESS]
        if search.strip():
            needle = search.strip().lower()
            rows = [
                r for r in rows
                if needle in " ".join(str(v) for v in r.values()).lower()
            ]

        display = [
            {
                "Quand": r["occurred_at"],
                "Qui": r["actor"],
                "Rôle": r["actor_role"] or "—",
                "Action": r["action_label"],
                "Issue": {"success": "OK", "denied": "Refusé",
                          "failure": "Échec"}.get(r["outcome"], r["outcome"]),
                "Cible": f"{r['target_type']} {r['target_id']}".strip() or "—",
                "Détail": r["details"] or "—",
                "Poste": r["device_label"] or "—",
                "Adresse IP": r["ip_address"] or "—",
                "Serveur": r["server_host"] or "—",
                "Origine": r["source"],
                "Session": r["session_id"] or "—",
            }
            for r in rows
        ]
        if not display:
            st.caption("Aucun événement pour ces filtres.")
        else:
            st.dataframe(display, hide_index=True, width="stretch", height=380)
            # Export : un audit se relit souvent hors de l'outil (tableur, transmission au
            # DPO). L'export est lui-même consigné — c'est une sortie de données
            # personnelles hors de l'application.
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(display[0]))
            writer.writeheader()
            writer.writerows(display)
            if st.download_button(
                t("activity.export_csv_button"), data=buffer.getvalue().encode("utf-8-sig"),
                file_name=f"journal-activite-{act_days}j.csv", mime="text/csv",
                icon=":material/download:",
            ):
                _audit(
                    activity_log.ACTION_DATA_EXPORTED, target_type="journal",
                    target_id="activity", details={"lignes": len(display),
                                                   "période_jours": act_days},
                )

# ── Intégrité ────────────────────────────────────────────────────────────────────────────────
with st.expander(t("activity.integrity_expander"), icon=":material/verified_user:"):
    st.caption(
        "Chaque ligne porte l'empreinte de la précédente : modifier ou supprimer une entrée "
        "ancienne casse toutes les suivantes. C'est **tamper-evident, pas tamper-proof** — "
        "sans `ACA_AUDIT_HMAC_KEY`, qui peut écrire dans le fichier peut recalculer toute la "
        "chaîne. Une purge RGPD (`retention.py`) casse volontairement la chaîne au point de "
        "coupe : ce n'est pas une falsification."
    )
    if st.button(t("activity.verify_now_button"), icon=":material/fact_check:"):
        for name, result in (
            ("Journal d'activité", activity_log.verify_chain()),
            ("Journal d'audit des validations", audit_log.verify_chain()),
        ):
            if result["ok"]:
                st.success(f"{name} — {result['detail']}", icon=":material/check_circle:")
            else:
                st.error(f"{name} — {result['detail']}", icon=":material/gpp_bad:")
    if not os.getenv("ACA_AUDIT_HMAC_KEY"):
        st.warning(
            "`ACA_AUDIT_HMAC_KEY` n'est pas définie : le chaînage utilise un SHA-256 "
            "simple, recalculable par quiconque a accès en écriture au fichier. Définissez "
            "cette clé (hors de la base, cf. `docs/DEPLOYMENT_HARDENING.md`) pour que "
            "forger un journal cohérent devienne réellement difficile.",
            icon=":material/key_off:",
        )

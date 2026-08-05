"""
Onglet « Rapports » (§20) — composer un rapport d'activité PDF, et retrouver les rapports mensuels
produits automatiquement.

Voir `aca/core/reporting.py` pour le moteur (sections, comparaison, préréglages) et
`aca/integrations/report_pdf.py` pour le rendu. Cette page ne calcule rien : elle compose une
spécification et l'exécute.

**Deux gardes d'accès, pour une seule raison.** Les sections « Actions enregistrées » et « Activité
par personne » reproduisent le contenu du Journal d'activité, réservé aux administrateurs depuis le
§17 (y donner accès à un opérateur ferait de la surveillance mutuelle un dispositif ouvert). Un
rapport qui les inclurait sans le même contrôle serait un contournement pur et simple de cette
règle — la garde est donc portée ici aussi, sur les sections **et** sur la liste des rapports
mensuels archivés, qui les contiennent.
"""
import os
from datetime import date, datetime, time as dt_time, timedelta

import streamlit as st

from aca.core import branding, reporting, ui_kit
from aca.storage import activity_log, analytics_store, user_store
from aca.ui.shared import (
    audit as _audit, audit_denied as _audit_denied, can as _can, current_user as _current_user, t,
)

BRAND = branding.resolve()
REPORT_DIR = os.getenv("ACA_REPORT_DIR", "data/reports")

# Sections reproduisant le Journal d'activité — cf. docstring du module.
_ADMIN_ONLY_SECTIONS = {"activity", "actors"}

if not _can(user_store.PERM_VIEW_DASHBOARD):
    _audit_denied(user_store.PERM_VIEW_DASHBOARD, "onglet Rapports")
    st.info(t("reports.no_permission"), icon=":material/lock:")
    st.stop()

_is_admin = _can(user_store.PERM_MANAGE_USERS)
_available = {key: spec for key, spec in reporting.SECTIONS.items()
              if _is_admin or key not in _ADMIN_ONLY_SECTIONS}

st.caption(t("reports.caption"))

# ── Préréglages ───────────────────────────────────────────────────────────────────────────────
# Un rapport qu'il faut recomposer case par case chaque mois ne sera composé qu'une fois.
_presets = reporting.list_presets()
if _presets:
    col_preset, col_load, col_drop = st.columns([3, 1, 1])
    chosen = col_preset.selectbox(t("reports.preset_label"), options=sorted(_presets),
                                  key="rp_preset_pick")
    if col_load.button(t("reports.preset_load"), icon=":material/download_for_offline:"):
        preset = _presets[chosen]
        # On écrit dans `session_state` puis on rejoue le script : les widgets ne sont pas encore
        # instanciés à ce stade du prochain passage, donc ils reprennent ces valeurs. Les modifier
        # après leur création lèverait une exception Streamlit.
        st.session_state["rp_title"] = preset.get("title", "")
        st.session_state["rp_sections"] = [s for s in preset.get("sections", [])
                                           if s in _available]
        st.session_state["rp_columns"] = preset.get("columns") or []
        st.session_state["rp_compare"] = bool(preset.get("compare", True))
        st.session_state["rp_note"] = preset.get("note", "")
        st.session_state["rp_maxrows"] = int(preset.get("max_rows", 500))
        filters = preset.get("filters") or {}
        st.session_state["rp_classifications"] = filters.get("classifications") or []
        st.session_state["rp_sender"] = filters.get("sender_contains", "")
        st.session_state["rp_validated"] = bool(filters.get("validated_only"))
        st.rerun()
    if col_drop.button(t("reports.preset_delete"), icon=":material/delete:"):
        reporting.delete_preset(chosen)
        st.rerun()

# ── Période ───────────────────────────────────────────────────────────────────────────────────
st.html(ui_kit.section(t("reports.period_header"), t("reports.period_sub"), icon="date_range"))

_today = date.today()
_last_year, _last_month = reporting.last_completed_month()
PERIOD_PRESETS = {
    "last_month": t("reports.period_last_month"),
    "this_month": t("reports.period_this_month"),
    "last_7": t("reports.period_last_days", d=7),
    "last_30": t("reports.period_last_days", d=30),
    "last_90": t("reports.period_last_days", d=90),
    "custom": t("reports.period_custom"),
}
period_choice = st.segmented_control(
    t("reports.period_label"), options=list(PERIOD_PRESETS), default="last_month", required=True,
    format_func=lambda key: PERIOD_PRESETS[key], key="rp_period",
)

if period_choice == "custom":
    col_from, col_to = st.columns(2)
    from_date = col_from.date_input(t("reports.from_date"), value=_today - timedelta(days=30),
                                    key="rp_from")
    to_date = col_to.date_input(t("reports.to_date"), value=_today, key="rp_to")
    # Borne haute EXCLUE côté moteur, mais la personne a choisi « jusqu'au 15 » en pensant « le 15
    # compris ». On ajoute donc un jour ici : sans ça, tout ce qui s'est passé le dernier jour
    # choisi manquerait au rapport, silencieusement.
    start = datetime.combine(from_date, dt_time.min)
    end = datetime.combine(to_date + timedelta(days=1), dt_time.min)
elif period_choice == "this_month":
    start, end = reporting.month_bounds(_today.year, _today.month)
    end = min(end, datetime.combine(_today + timedelta(days=1), dt_time.min))
elif period_choice == "last_month":
    start, end = reporting.month_bounds(_last_year, _last_month)
else:
    days = int(period_choice.split("_")[1])
    start = datetime.combine(_today - timedelta(days=days), dt_time.min)
    end = datetime.combine(_today + timedelta(days=1), dt_time.min)

if end <= start:
    st.error(t("reports.period_invalid"), icon=":material/event_busy:")
    st.stop()

_previous = reporting.previous_period(start, end)
st.html(ui_kit.readout([
    (t("reports.readout_period"), reporting.period_label(start, end), "on"),
    (t("reports.readout_compared"), reporting.period_label(*_previous), ""),
]))

# ── Contenu ───────────────────────────────────────────────────────────────────────────────────
st.html(ui_kit.section(t("reports.content_header"), t("reports.content_sub"), icon="tune"))

# Les sections sont présentées par famille, dans l'ordre du rapport lui-même : la personne compose
# le document dans l'ordre où il se lira, pas dans l'ordre où le code les a déclarées.
selected_sections = list(st.session_state.get("rp_sections", reporting.DEFAULT_SECTIONS))
_new_selection = []
for group in reporting.GROUP_ORDER:
    keys = [key for key, spec in _available.items() if spec["group"] == group]
    if not keys:
        continue
    with st.container(border=True):
        st.markdown(f"**{group}**")
        for key in keys:
            spec = _available[key]
            if st.checkbox(spec["label"], value=key in selected_sections, key=f"rp_sec_{key}",
                           help=spec["description"]):
                _new_selection.append(key)
st.session_state["rp_sections"] = _new_selection

if not _new_selection:
    st.warning(t("reports.no_section"), icon=":material/rule:")

# ── Options du détail e-mail ──────────────────────────────────────────────────────────────────
# N'apparaissent que si la section correspondante est demandée : afficher en permanence des filtres
# qui ne s'appliquent à rien fait douter de ce qu'ils filtrent.
if "emails" in _new_selection:
    with st.container(border=True):
        st.markdown(f"**{t('reports.email_options_header')}**")
        st.caption(t("reports.email_options_sub"))
        columns = st.multiselect(
            t("reports.columns_label"), options=list(analytics_store.EVENT_COLUMNS),
            default=st.session_state.get("rp_columns")
            or ["classified_at", "sender", "classification"],
            format_func=lambda key: analytics_store.EVENT_COLUMNS[key], key="rp_columns",
        )
        col_cat, col_sender = st.columns(2)
        classifications = col_cat.multiselect(
            t("reports.filter_categories"),
            options=["DEMANDE_DEMO", "DEVIS", "SUPPORT", "AUTRE", "SPAM"],
            default=st.session_state.get("rp_classifications", []), key="rp_classifications",
        )
        sender_contains = col_sender.text_input(
            t("reports.filter_sender"), value=st.session_state.get("rp_sender", ""),
            placeholder="exemple.fr", key="rp_sender",
        )
        col_only, col_max = st.columns(2)
        validated_only = col_only.toggle(
            t("reports.filter_validated"), value=st.session_state.get("rp_validated", False),
            key="rp_validated",
        )
        max_rows = col_max.number_input(
            t("reports.max_rows"), min_value=10, max_value=2000,
            value=int(st.session_state.get("rp_maxrows", 500)), step=50, key="rp_maxrows",
            help=t("reports.max_rows_help"),
        )
else:
    columns = st.session_state.get("rp_columns") or None
    classifications = st.session_state.get("rp_classifications") or []
    sender_contains = st.session_state.get("rp_sender", "")
    validated_only = bool(st.session_state.get("rp_validated", False))
    max_rows = int(st.session_state.get("rp_maxrows", 500))

# ── Présentation ──────────────────────────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown(f"**{t('reports.presentation_header')}**")
    title = st.text_input(t("reports.title_label"),
                          value=st.session_state.get("rp_title", "") or t("reports.title_default"),
                          key="rp_title")
    note = st.text_area(t("reports.note_label"), value=st.session_state.get("rp_note", ""),
                        placeholder=t("reports.note_placeholder"), height=80, key="rp_note",
                        help=t("reports.note_help"))
    compare = st.toggle(t("reports.compare_label"),
                        value=bool(st.session_state.get("rp_compare", True)), key="rp_compare",
                        help=t("reports.compare_help"))

spec = reporting.new_spec(
    start, end, title=title, sections=_new_selection, columns=columns,
    filters={"classifications": classifications, "sender_contains": sender_contains,
             "validated_only": validated_only},
    compare=compare, note=note, generated_by=_current_user().get("username") or "",
    max_rows=int(max_rows),
)

# ── Génération ────────────────────────────────────────────────────────────────────────────────
col_build, col_save = st.columns([2, 1])
if col_build.button(t("reports.build_button"), type="primary", icon=":material/picture_as_pdf:",
                    disabled=not _new_selection):
    with st.spinner(t("reports.building")):
        from aca.integrations import report_pdf

        report = reporting.collect(spec)
        payload = report_pdf.build_report_pdf(report, BRAND)
    if not payload:
        # `build_report_pdf` ne lève jamais : sans ce message, un échec de rendu se traduirait par
        # un bouton qui ne fait rien, ce qui est le pire des retours.
        st.error(t("reports.build_failed"), icon=":material/error:")
    else:
        st.session_state["rp_result"] = {
            "payload": payload, "report": report,
            "filename": reporting.report_filename(spec),
        }

with col_save.popover(t("reports.preset_save"), icon=":material/bookmark_add:"):
    preset_name = st.text_input(t("reports.preset_name"), key="rp_preset_name")
    if st.button(t("reports.preset_save_button"), key="rp_preset_save_btn"):
        try:
            saved = reporting.save_preset(preset_name, spec)
        except ValueError as exc:
            st.warning(str(exc), icon=":material/edit:")
        else:
            st.success(t("reports.preset_saved", name=saved), icon=":material/check_circle:")

_result = st.session_state.get("rp_result")
if _result:
    st.divider()
    st.html(ui_kit.section(t("reports.result_header"), t("reports.result_sub"), icon="description"))

    # Aperçu à l'écran AVANT le téléchargement : la personne voit ce que contient le document
    # plutôt que de devoir l'ouvrir pour le découvrir, et constate tout de suite qu'une section
    # est vide (donc qu'il faut peut-être élargir la période) au lieu de le lire dans le PDF.
    _chart_colour = branding.chart_colors(BRAND)[0]
    for group in _result["report"]["groups"]:
        with st.expander(group["title"], icon=":material/folder:"):
            for block in group["blocks"]:
                st.markdown(f"**{block['title']}**")
                st.caption(block.get("context", ""))
                if block["type"] == "kpis":
                    st.html(ui_kit.stat_row([
                        (item["label"],
                         f"{item['value']}{item.get('suffix', '')}"
                         if item.get("value") is not None else "—",
                         (f"{item['comparison']['delta']:+g} vs période précédente"
                          if item.get("comparison") else item.get("hint", "")),
                         "")
                        for item in block["items"]
                    ]))
                elif block["type"] == "bars":
                    if block["items"]:
                        st.bar_chart(
                            [{"libellé": i["label"], "valeur": i["value"]} for i in block["items"]],
                            x="libellé", y="valeur", x_label="", y_label="", horizontal=True,
                            sort=False, color=_chart_colour,
                        )
                    else:
                        st.caption(t("reports.block_empty"))
                elif block["type"] == "line":
                    if block["points"]:
                        st.line_chart(
                            [{"jour": p["label"], "valeur": p["value"]} for p in block["points"]],
                            x="jour", y="valeur", x_label="", y_label="", color=_chart_colour,
                        )
                    else:
                        st.caption(t("reports.block_empty"))
                elif block["type"] == "table":
                    if block["rows"]:
                        st.dataframe(
                            [dict(zip(block["columns"], row)) for row in block["rows"][:50]],
                            hide_index=True, width="stretch",
                        )
                        if len(block["rows"]) > 50:
                            st.caption(t("reports.table_truncated", n=len(block["rows"])))
                    else:
                        st.caption(t("reports.block_empty"))
                else:
                    st.caption(block.get("body", ""))

    if st.download_button(
        t("reports.download_button"), data=_result["payload"], file_name=_result["filename"],
        mime="application/pdf", type="primary", icon=":material/download:",
    ):
        # Même raisonnement que l'export CSV du journal et le PDF de proposition : un rapport fait
        # sortir de l'application des noms de prospects et des volumes commerciaux.
        _audit(
            activity_log.ACTION_REPORT_GENERATED, target_type="rapport",
            target_id=_result["filename"],
            details={"période": _result["report"]["meta"]["period_label"],
                     "sections": ", ".join(_result["report"]["meta"]["sections"])},
        )

# ── Rapports mensuels archivés ────────────────────────────────────────────────────────────────
st.divider()
st.html(ui_kit.section(t("reports.archive_header"), t("reports.archive_sub"), icon="inventory_2"))

if not _is_admin:
    # Les rapports mensuels automatiques contiennent les sections nominatives (« Activité par
    # personne »). Les rendre téléchargeables ici contournerait la garde du Journal d'activité.
    st.info(t("reports.archive_admin_only"), icon=":material/lock:")
else:
    from aca.integrations import report_pdf

    archives = report_pdf.list_reports(REPORT_DIR)
    if not archives:
        st.html(ui_kit.empty_state(
            t("reports.archive_empty_title"),
            t("reports.archive_empty_body", dir=REPORT_DIR),
            icon="inventory_2",
        ))
    else:
        for entry in archives:
            with st.container(border=True):
                col_name, col_dl = st.columns([3, 1])
                col_name.markdown(f"**{entry['name']}**")
                col_name.caption(f"{entry['modified']} · {entry['bytes'] // 1024} Ko")
                try:
                    with open(entry["path"], "rb") as handle:
                        data = handle.read()
                except OSError:
                    col_dl.caption(t("reports.archive_unreadable"))
                    continue
                if col_dl.download_button(
                    t("reports.download_button"), data=data, file_name=entry["name"],
                    mime="application/pdf", key=f"rp_dl_{entry['name']}",
                    icon=":material/download:",
                ):
                    _audit(
                        activity_log.ACTION_DATA_EXPORTED, target_type="rapport-archivé",
                        target_id=entry["name"], details={"octets": entry["bytes"]},
                    )
    st.caption(t("reports.archive_hint", dir=REPORT_DIR))

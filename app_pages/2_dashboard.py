"""Onglet « Tableau de bord » (§18) — extrait de `ui.py`. Voir `aca/ui/shared.py` pour le contexte
de la découpe en pages `st.navigation`."""
import streamlit as st

from aca.core import branding, demo
from aca.storage import analytics_store
from aca.ui.shared import t

BRAND = branding.resolve()

st.caption(t("dashboard.caption"))
days = st.segmented_control(
    t("dashboard.period_label"), options=[7, 30, 90], default=30, required=True,
    format_func=lambda d: t("dashboard.period_days", d=d), key="dashboard_days",
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
    st.info(t("dashboard.empty_state"), icon=":material/info:")
    # §5.6 des suggestions — état vide actionnable : en mode démonstration, un lien direct vers
    # un exemple prêt à l'emploi transforme un cul-de-sac en invitation, plutôt que de laisser la
    # personne deviner qu'il faut aller chercher elle-même l'onglet « Nouvel e-mail ».
    if demo.is_enabled() and st.button(
        t("dashboard.demo_link_button"), icon=":material/science:",
    ):
        st.switch_page("app_pages/1_inbox.py")
else:
    with st.container(horizontal=True):
        st.metric(t("dashboard.metric_classified"), total, border=True)
        st.metric(t("dashboard.metric_conversion"), f"{conversion_pct}%", border=True)
        st.metric(
            t("dashboard.metric_median_response"),
            f"{median_minutes:.0f} min" if median_minutes is not None else "—",
            border=True,
        )
        st.metric(
            t("dashboard.metric_edited"), f"{edits['taux_pct']}%", border=True,
            help="§13 item 3 — % de propositions validées que l'humain a modifiées avant l'envoi.",
        )
        st.metric(
            t("dashboard.metric_tokens"),
            f"{tokens['moyenne_par_analyse']:.0f}" if tokens["analyses"] else "—",
            border=True,
            help="§13 item 4 — Quota Usage Tracker, purement informatif tant que Groq reste gratuit.",
        )

    # Les graphiques suivent la marque (§17) : sans `color=`, ils resteraient au bleu Streamlit
    # et un client aux couleurs orange verrait un tableau de bord bleu au milieu de son thème.
    _brand_chart = branding.chart_colors(BRAND)[0]

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown(f"**{t('dashboard.chart_volume')}**")
            st.bar_chart(volume, x="classification", y="count", x_label="", y_label="",
                         color=_brand_chart)

    with col2:
        with st.container(border=True):
            st.markdown(f"**{t('dashboard.chart_daily')}**")
            if daily:
                st.line_chart(daily, x="jour", y="count", x_label="", y_label="",
                              color=_brand_chart)
            else:
                st.caption(t("dashboard.chart_no_trend"))

    with st.container(border=True):
        st.markdown(f"**{t('dashboard.chart_funnel')}**")
        st.bar_chart(
            [{"étape": k, "compte": v} for k, v in funnel.items()],
            x="étape", y="compte", x_label="", y_label="", horizontal=True, sort=False,
            color=_brand_chart,
        )

    if resp_times:
        with st.expander(t("dashboard.response_times_expander"), icon=":material/schedule:"):
            st.dataframe(resp_times, hide_index=True, width="stretch")

"""Onglet « Historique » (§18) — extrait de `ui.py`. Voir `aca/ui/shared.py` pour le contexte de
la découpe en pages `st.navigation`."""
import streamlit as st

from aca.storage import audit_log
from aca.ui.shared import t

st.caption(t("history.caption"))
col_limit, col_search = st.columns([1, 3])
with col_limit:
    history_limit = st.number_input(
        t("history.limit_label"), min_value=10, max_value=500, value=100, step=10,
    )
with col_search:
    history_query = st.text_input(
        t("history.search_label"),
        placeholder=t("history.search_placeholder"),
    )

history_rows = audit_log.list_recent(limit=int(history_limit))
if history_query.strip():
    needle = history_query.strip().lower()
    history_rows = [
        r for r in history_rows if needle in " ".join(str(v) for v in r.values()).lower()
    ]

if not history_rows:
    st.caption(t("history.empty_state"))
else:
    st.dataframe(
        history_rows, hide_index=True, width="stretch",
        column_config={
            "thread_id": "ID d'analyse", "validated_by": "Validé par",
            "classification": "Classification", "sender": "Expéditeur",
            "validated_at": "Date de validation",
        },
    )

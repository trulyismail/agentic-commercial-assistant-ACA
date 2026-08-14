"""
Onglet « Tableau de bord » (§18, enrichi §22) — extrait de `ui.py`. Voir `aca/ui/shared.py` pour le
contexte de la découpe en pages `st.navigation`.

§22 — ce que cette page savait faire et ne faisait pas. Elle montrait un ÉTAT : cinq compteurs et
trois graphes sur « les N derniers jours ». Elle ne répondait donc à aucune des trois questions
qu'on lui pose réellement en ouvrant l'onglet — *est-ce que ça va mieux qu'avant ?*, *est-ce qu'on
répond assez vite ?*, *est-ce que la réception automatique sert à quelque chose ?* Les données
existaient toutes ; il manquait les lectures et la comparaison.

Trois partis pris de fond :

1. **La comparaison réutilise `reporting.previous_period()`**, elle ne la réimplémente pas. Le
   rapport mensuel PDF et cet écran doivent donner le même chiffre pour la même période, sinon l'un
   des deux sera un jour désavoué par l'autre — et personne ne saura lequel croire.
2. **Chaque indicateur déclare quel sens lui est favorable.** Un délai de réponse qui augmente est
   une DÉGRADATION ; le colorier en vert parce que la valeur monte produirait un tableau flatteur
   et faux. C'est déjà la règle de `reporting._kpi(better=…)`, reprise ici telle quelle.
3. **Aucun filtre global.** Un filtre « catégorie » aurait été facile à poser au-dessus de la page,
   mais il ne pourrait pas s'appliquer à l'entonnoir, aux délais ni aux tokens sans réécrire cinq
   requêtes : la personne filtrerait, la moitié de l'écran ne bougerait pas, et elle ne saurait plus
   ce qu'elle regarde.
"""
from datetime import datetime, timedelta

import streamlit as st

from aca.core import branding, demo, reporting
from aca.storage import analytics_store
from aca.ui.shared import t

BRAND = branding.resolve()

#: Sens favorable de chaque indicateur -> `delta_color` de `st.metric`.
#: « normal » = vert quand ça monte, « inverse » = vert quand ça descend, « off » = gris.
#: La table existe pour que le choix soit DÉCLARÉ plutôt que dispersé dans cinq appels.
_DELTA_COLOR = {"up": "normal", "down": "inverse", "neutral": "off"}

st.caption(t("dashboard.caption"))


def _delta(current, previous, *, digits: int = 0, suffix: str = ""):
    """
    Écart affichable, ou `None` quand il n'y a rien d'honnête à afficher.

    `None` (et non « 0 ») quand la période précédente n'a pas de valeur : afficher « +0 » pour une
    grandeur qui n'existait pas laisserait croire à une stabilité, alors qu'il n'y a aucun point de
    comparaison. C'est la même prudence que `reporting._delta`, qui refuse un pourcentage quand la
    base précédente est nulle.
    """
    if current is None or previous is None:
        return None
    gap = round(current - previous, digits)
    if digits == 0:
        gap = int(gap)
    return f"{gap:+g}{suffix}"


@st.fragment
def dashboard() -> None:
    """
    Corps interactif de la page, isolé dans un `st.fragment`.

    Sans lui, changer la période rejouerait tout le script du routeur : barre latérale, file
    d'attente, rappels, relevé de réception — c'est-à-dire une dizaine de lectures SQLite sans
    rapport avec le geste demandé. Le fragment borne le rerun à ce qui change vraiment.
    """
    with st.container(horizontal=True, vertical_alignment="bottom"):
        days = st.segmented_control(
            t("dashboard.period_label"), options=[7, 30, 90], default=30, required=True,
            format_func=lambda d: t("dashboard.period_days", d=d), key="dashboard_days",
        )
        compare = st.toggle(t("dashboard.compare_label"), value=True, key="dashboard_compare",
                            help=t("dashboard.compare_help"))

    # Fenêtres explicites des deux côtés. La page passait auparavant `days=`, ce qui laisse la borne
    # haute ouverte ; en comparant deux périodes il faut que les DEUX soient bornées de la même
    # façon, sinon la période courante inclut l'instant présent et l'autre non.
    end = datetime.now()
    start = end - timedelta(days=days)
    prev_start, prev_end = reporting.previous_period(start, end)

    volume = analytics_store.volume_by_category(start=start, end=end)
    daily = analytics_store.daily_volume(start=start, end=end)
    funnel = analytics_store.funnel_counts(start=start, end=end)
    resp_times = analytics_store.response_times(start=start, end=end)
    edits = analytics_store.edit_rate(start=start, end=end)
    tokens = analytics_store.token_stats(start=start, end=end)
    sources = analytics_store.by_source(start=start, end=end)
    hours = analytics_store.hourly_volume(start=start, end=end)
    senders = analytics_store.top_senders(start=start, end=end)
    buckets = analytics_store.bucket_response_times(resp_times)

    total = funnel["classifiés"]
    if total == 0:
        st.info(t("dashboard.empty_state"), icon=":material/info:")
        # §5.6 des suggestions — état vide actionnable : en mode démonstration, un lien direct vers
        # un exemple prêt à l'emploi transforme un cul-de-sac en invitation, plutôt que de laisser
        # la personne deviner qu'il faut aller chercher elle-même l'onglet « Nouvel e-mail ».
        if demo.is_enabled() and st.button(
            t("dashboard.demo_link_button"), icon=":material/science:",
        ):
            st.switch_page("app_pages/1_inbox.py")
        return

    def median(values):
        ordered = sorted(values)
        return ordered[len(ordered) // 2] if ordered else None

    def conversion(counts):
        return round(100 * counts["validés"] / counts["classifiés"], 1) if counts["classifiés"] else 0.0

    median_minutes = median([r["minutes"] for r in resp_times])

    # Les valeurs de comparaison ne sont lues que si la case est cochée : cinq requêtes de plus par
    # rerun ne se justifient pas quand personne ne regarde le résultat.
    if compare:
        prev_funnel = analytics_store.funnel_counts(start=prev_start, end=prev_end)
        prev_median = median([r["minutes"] for r in
                              analytics_store.response_times(start=prev_start, end=prev_end)])
        prev_edits = analytics_store.edit_rate(start=prev_start, end=prev_end)
        prev_tokens = analytics_store.token_stats(start=prev_start, end=prev_end)
    else:
        prev_funnel = prev_edits = prev_tokens = None
        prev_median = None

    # ── Indicateurs ───────────────────────────────────────────────────────────────────────────
    # QUATRE indicateurs, pas cinq. Le compteur de tokens est une mesure d'exploitation, pas une
    # mesure commerciale : à cette hauteur d'écran il volait une place aux chiffres qu'un
    # responsable regarde vraiment, et faisait retomber la rangée à « 4 + 1 » — une carte seule sur
    # une deuxième ligne, ce qui se lit comme un défaut d'alignement plutôt que comme un choix. Il
    # est déplacé plus bas, à côté de l'entonnoir.
    #
    # Pas de courbe miniature non plus, alors que `st.metric` sait en afficher : elle n'existait que
    # pour un indicateur sur cinq (le seul disposant d'une vraie série quotidienne), ce qui rendait
    # sa carte plus HAUTE que ses voisines et donnait une rangée en dents de scie. La même série est
    # de toute façon tracée en grand juste en dessous, en « Tendance quotidienne ».
    with st.container(horizontal=True):
        st.metric(
            t("dashboard.metric_classified"), total, border=True,
            delta=_delta(total, prev_funnel["classifiés"] if prev_funnel else None),
            delta_color=_DELTA_COLOR["up"],
        )
        st.metric(
            t("dashboard.metric_conversion"), f"{conversion(funnel)}%", border=True,
            delta=_delta(conversion(funnel), conversion(prev_funnel) if prev_funnel else None,
                         digits=1, suffix=" pts"),
            delta_color=_DELTA_COLOR["up"],
        )
        st.metric(
            t("dashboard.metric_median_response"),
            f"{median_minutes:.0f} min" if median_minutes is not None else "—", border=True,
            delta=_delta(median_minutes, prev_median, suffix=" min"),
            # `inverse` : sur un délai, descendre est une bonne nouvelle. Sans cette ligne, une
            # dégradation du temps de réponse s'afficherait en vert.
            delta_color=_DELTA_COLOR["down"],
        )
        st.metric(
            t("dashboard.metric_edited"), f"{edits['taux_pct']}%", border=True,
            delta=_delta(edits["taux_pct"], prev_edits["taux_pct"] if prev_edits else None,
                         digits=1, suffix=" pts"),
            # Ni bon ni mauvais en soi : un taux d'édition élevé signale des brouillons perfectibles,
            # un taux nul peut signaler qu'on valide sans relire. Le colorer serait un jugement que
            # la donnée ne permet pas.
            delta_color=_DELTA_COLOR["neutral"],
            help="§13 item 3 — % de propositions validées que l'humain a modifiées avant l'envoi.",
        )
    if compare:
        st.caption(t("dashboard.compare_caption", d=days))

    # §25 — UNE TEINTE PAR GRAPHE, et non plus une seule pour tout le tableau de bord.
    #
    # Le §22 n'utilisait que `chart_colors(BRAND)[0]`, au motif qu'une couleur par barre serait un
    # encodage redondant : les catégories sont déjà nommées sur l'axe. Le raisonnement reste juste
    # *à l'intérieur* d'un graphe — et il a fait manquer l'échelle du dessus. Six blocs mesurant six
    # choses différentes se dessinaient tous dans le même bleu, si bien que rien ne séparait
    # visuellement « quels sujets arrivent » de « à quelle vitesse on répond ». Signalé par un
    # utilisateur, ce qu'aucun test ne pouvait révéler : aucune règle n'était fausse.
    #
    # La couleur encode donc le BLOC, pas la catégorie : elle distingue des mesures réellement
    # différentes au lieu de répéter une étiquette déjà lisible. Les teintes viennent de
    # `chart_colors()`, palette déjà dédoublonnée au §21 et bâtie sur les jetons sémantiques du
    # client — donc toujours la palette de la marque, jamais un arc-en-ciel générique plaqué dessus.
    _palette = branding.chart_colors(BRAND)

    def hue_for(index: int) -> str:
        return _palette[index % len(_palette)]

    # Hauteurs de graphe accordées à la hauteur de carte. Sans elles, Vega garde sa hauteur par
    # défaut (~350 px pour une série courte) pendant que la carte, elle, est fixée par rangée : d'où
    # les grandes zones vides sous « Volume par catégorie » et « Rapidité de réponse » visibles à
    # l'écran. On retire l'en-tête (titre + phrase d'explication éventuelle) et les marges internes.
    def chart_height(card_height: int, has_help: bool = False) -> int:
        return card_height - (118 if has_help else 78)

    def card(title: str, help_text: str = "", height: int = None):
        """
        Carte de graphe. `height` est fixé PAR RANGÉE, pas par carte.

        `st.columns` dimensionne chaque colonne sur son propre contenu : deux graphes côte à côte
        dont l'un porte une phrase d'explication et l'autre non produisent donc deux cartes de
        hauteurs différentes, et la grille part en dents de scie. Une hauteur commune par rangée
        rétablit l'alignement. Le contenu qui dépasse défile à l'intérieur de la carte — rien n'est
        perdu, y compris si le client choisit une densité « aérée » ou une police plus grande.
        """
        box = st.container(border=True, height=height)
        with box:
            st.markdown(f"**{title}**")
            if help_text:
                st.caption(help_text)
        return box

    # Étiquettes lisibles des origines. `source` stocke des identifiants techniques ; les afficher
    # bruts obligerait la personne à connaître le nom des composants pour lire son tableau de bord.
    sources = [{**row, "source": t(f"dashboard.source_{row['source']}")} for row in sources]

    # ── Répartitions ──────────────────────────────────────────────────────────────────────────
    left, right = st.columns(2)
    with left:
        with card(t("dashboard.chart_volume"), height=340):
            # Barres HORIZONTALES : « DEMANDE_DEMO » est long, et en vertical Vega fait pivoter les
            # étiquettes à 90°, ce qui oblige à pencher la tête pour lire son propre tableau de bord.
            st.bar_chart(volume, x="classification", y="count", x_label="", y_label="",
                         color=hue_for(0), horizontal=True, height=chart_height(340))
    with right:
        with card(t("dashboard.chart_daily"), height=340):
            if len(daily) > 1:
                st.area_chart(daily, x="jour", y="count", x_label="", y_label="",
                              color=hue_for(1), height=chart_height(340))
            else:
                st.caption(t("dashboard.chart_no_trend"))

    left, right = st.columns(2)
    with left:
        with card(t("dashboard.chart_buckets"), t("dashboard.chart_buckets_help"), height=400):
            if resp_times:
                # `sort=False` : les tranches sont ORDINALES (de la plus rapide à la plus lente).
                # Les trier par effectif décroissant, comme le ferait le tri par défaut, détruirait
                # l'échelle et rendrait le graphe illisible.
                st.bar_chart(buckets, x="tranche", y="count", x_label="", y_label="",
                             color=hue_for(2), horizontal=True, sort=False,
                             height=chart_height(400, has_help=True))
            else:
                st.caption(t("dashboard.no_data"))
    with right:
        with card(t("dashboard.chart_source"), t("dashboard.chart_source_help"), height=400):
            st.bar_chart(sources, x="source", y="count", x_label="", y_label="",
                         color=hue_for(3), horizontal=True,
                         height=chart_height(400, has_help=True))

    left, right = st.columns(2)
    with left:
        with card(t("dashboard.chart_hours"), t("dashboard.chart_hours_help"), height=430):
            st.bar_chart(hours, x="heure", y="count", x_label="", y_label="", color=hue_for(4),
                         sort=False, height=chart_height(430, has_help=True))
    with right:
        with card(t("dashboard.chart_senders"), height=430):
            if senders:
                st.dataframe(
                    senders, hide_index=True, width="stretch",
                    column_config={
                        "expéditeur": st.column_config.TextColumn(t("dashboard.col_sender")),
                        # Une barre plutôt qu'un nombre : le rang relatif se lit d'un coup d'œil,
                        # ce qui est toute la question qu'on pose à ce bloc.
                        "e-mails": st.column_config.ProgressColumn(
                            t("dashboard.col_emails"), format="%d",
                            min_value=0, max_value=max(s["e-mails"] for s in senders),
                        ),
                        "validés": st.column_config.NumberColumn(t("dashboard.col_validated"),
                                                                 format="%d"),
                    },
                )
            else:
                st.caption(t("dashboard.no_data"))

    # L'entonnoir prend les deux tiers : c'est un graphe à barres horizontales, donc la largeur est
    # sa dimension utile. Le compteur de tokens l'accompagne — deux mesures d'exploitation, loin de
    # la rangée commerciale du haut.
    wide, narrow = st.columns([2, 1])
    with wide:
        with card(t("dashboard.chart_funnel"), height=300):
            st.bar_chart(
                [{"étape": k, "compte": v} for k, v in funnel.items()],
                x="étape", y="compte", x_label="", y_label="", horizontal=True, sort=False,
                color=hue_for(5), height=chart_height(300),
            )
    with narrow:
        with st.container(border=True, height=300):
            st.metric(
                t("dashboard.metric_tokens"),
                f"{tokens['moyenne_par_analyse']:.0f}" if tokens["analyses"] else "—",
                delta=_delta(
                    tokens["moyenne_par_analyse"] if tokens["analyses"] else None,
                    prev_tokens["moyenne_par_analyse"]
                    if prev_tokens and prev_tokens["analyses"] else None,
                ),
                delta_color=_DELTA_COLOR["down"],
                help="§13 item 4 — Quota Usage Tracker, purement informatif tant que Groq reste "
                     "gratuit.",
            )

    with st.expander(t("dashboard.response_times_expander"), icon=":material/schedule:"):
        if resp_times:
            st.dataframe(resp_times, hide_index=True, width="stretch")
        else:
            st.caption(t("dashboard.no_data"))


dashboard()

"""
Moteur de rapport d'activité (§20) — ce qui s'est passé sur une période, et en quoi ça diffère de la
période précédente.

**Le manque comblé.** Toutes les données existaient : `analytics_store` compte les e-mails classés,
`audit_log` les validations, `activity_log` les gestes de chaque personne, `task_store` les envois
programmés, `review_store` les relectures demandées. Mais elles n'existaient que **à l'écran, en
« N derniers jours », et jamais comparées**. Personne ne pouvait répondre à « qu'est-ce que cet
outil nous a apporté en juillet, par rapport à juin ? » — qui est pourtant la seule question que
pose un responsable, et la seule qui justifie de reconduire l'outil. Un tableau de bord montre un
état ; un rapport raconte une évolution.

**Un seul moteur pour deux fonctionnalités.** Le rapport mensuel automatique (`scheduler.py`,
travail `report`) et le rapport paramétrable de l'interface ne sont pas deux mécanismes : c'est le
même, appelé avec deux `spec` différentes. Le mensuel est simplement une spécification figée (mois
civil écoulé, presque toutes les sections, comparaison activée). Écrire deux chemins aurait garanti
qu'un jour l'un des deux affiche un chiffre que l'autre ne connaît pas.

**Séparation stricte entre collecte et rendu.** Ce module ne sait pas ce qu'est un PDF : il produit
une structure neutre (blocs typés), que `aca/integrations/report_pdf.py` dessine. C'est ce qui rend
le contenu testable hors ligne sans ouvrir un document, et ce qui permettrait d'en faire un jour un
export HTML ou CSV sans retoucher une seule requête.

**Toujours avec le contexte.** Chaque bloc porte un `context` : une phrase qui dit d'où vient le
chiffre et sur quoi il porte. Un rapport sorti de son écran d'origine circule par e-mail, se
retrouve dans une réunion trois semaines plus tard, et un nombre sans son mode de calcul y devient
au mieux inutile, au pire trompeur. C'est une exigence de fond, pas de mise en page.

**Comparaison honnête.** Une variation n'est pas « bonne » parce qu'elle monte : un délai de réponse
médian qui augmente est une dégradation, un volume qui augmente ne l'est pas. Chaque indicateur
comparé déclare donc le sens qui lui est favorable (`better`), et le rendu s'y conforme — colorier
toute hausse en vert produirait un rapport flatteur et faux.
"""
import json
import statistics
from datetime import datetime, timedelta

from aca.storage import (
    activity_log, analytics_store, audit_log, queue_store, review_store, task_store,
)

_TS = "%Y-%m-%d %H:%M:%S"

# ── Périodes ──────────────────────────────────────────────────────────────────────────────────
MONTH_NAMES = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def month_bounds(year: int, month: int) -> tuple:
    """Bornes `[début, fin)` d'un mois civil. Fin exclue — cf. `analytics_store._window`."""
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def last_completed_month(now: datetime = None) -> tuple:
    """
    Dernier mois civil ENTIÈREMENT écoulé — jamais le mois en cours, qui n'a pas fini de recevoir
    des lignes. Même règle et même raison que `scheduler._last_completed_month` (l'archive du
    journal) : un rapport « du mois » produit le 12 ne porterait que sur onze jours et se
    comparerait à un mois plein, ce qui inventerait une chute d'activité qui n'a pas eu lieu.
    """
    now = now or datetime.now()
    return (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)


def previous_period(start: datetime, end: datetime) -> tuple:
    """
    Période de comparaison : la fenêtre **de même durée** qui précède immédiatement `start`.

    Volontairement générique plutôt que « le mois d'avant » : la comparaison doit rester juste
    quand la personne demande un rapport sur 12 jours ou sur un trimestre, et non seulement sur un
    mois. Pour un mois civil, cela ne redonne pas exactement le mois précédent (28 ≠ 31 jours) — et
    c'est le comportement voulu : comparer 31 jours à 28 jours ferait apparaître février en recul
    de 10 % chaque année sans qu'il s'y passe quoi que ce soit.
    """
    span = end - start
    return start - span, start


def month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES[month - 1]} {year}"


def _is_whole_month(start: datetime, end: datetime) -> bool:
    return (start.day == 1 and start.hour == 0 and start.minute == 0
            and end == month_bounds(start.year, start.month)[1])


def period_label(start: datetime, end: datetime) -> str:
    """
    Libellé lisible d'une période. La borne haute étant exclue, on affiche le **dernier jour
    inclus** : écrire « au 1er août » pour un rapport de juillet ferait douter de ce qu'il contient.
    """
    if _is_whole_month(start, end):
        return month_label(start.year, start.month)
    last_day = end - timedelta(seconds=1)
    return f"du {start.strftime('%d/%m/%Y')} au {last_day.strftime('%d/%m/%Y')}"


# ── Petits calculs ────────────────────────────────────────────────────────────────────────────
def _median(values) -> float:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _delta(current, previous) -> dict:
    """
    Écart absolu et relatif entre deux valeurs, ou `None` si la comparaison n'a pas de sens.

    Le cas « période précédente à zéro » est traité explicitement : afficher « +100 % » quand on
    passe de 0 à 3 raconterait une progression qui n'a pas de base. On renvoie alors l'écart absolu
    sans pourcentage, ce qui est la seule chose vraie.
    """
    if current is None or previous is None:
        return None
    delta = current - previous
    pct = round(100 * delta / previous, 1) if previous else None
    direction = "flat" if abs(delta) < 1e-9 else ("up" if delta > 0 else "down")
    return {"delta": delta, "pct": pct, "direction": direction, "previous": previous}


def _kpi(label: str, value, *, previous=None, suffix: str = "", better: str = "up",
         hint: str = "") -> dict:
    """
    Un indicateur du rapport. `better` dit quel sens est favorable (`up`/`down`/`neutral`) — sans
    quoi le rendu colorierait en vert un délai de réponse qui se dégrade (cf. docstring du module).
    """
    return {
        "label": label, "value": value, "suffix": suffix, "better": better, "hint": hint,
        "comparison": _delta(value, previous),
    }


# ── Sections ──────────────────────────────────────────────────────────────────────────────────
# Regroupement des sections. Répond au « classe-les d'une façon qui rende la fonctionnalité utile » :
# un rapport qui aligne quinze tableaux dans l'ordre où le code les a produits ne se lit pas. Ces
# quatre familles correspondent à quatre lecteurs différents — le commercial, le responsable
# qualité, l'administrateur, l'exploitant.
GROUP_COMMERCIAL = "Activité commerciale"
GROUP_QUALITY = "Qualité et intervention humaine"
GROUP_TRACE = "Traçabilité et conformité"
GROUP_OPS = "Exploitation"

GROUP_ORDER = (GROUP_COMMERCIAL, GROUP_QUALITY, GROUP_TRACE, GROUP_OPS)


def _s_summary(window, previous, spec) -> dict:
    funnel = analytics_store.funnel_counts(start=window[0], end=window[1])
    prev_funnel = analytics_store.funnel_counts(start=previous[0], end=previous[1])
    times = analytics_store.response_times(start=window[0], end=window[1])
    prev_times = analytics_store.response_times(start=previous[0], end=previous[1])

    def conversion(f):
        return round(100 * f["validés"] / f["classifiés"], 1) if f["classifiés"] else 0.0

    median_now = _median([t["minutes"] for t in times])
    median_before = _median([t["minutes"] for t in prev_times])
    return {
        "type": "kpis",
        "title": "Vue d'ensemble",
        "context": "Chiffres clés de la période, comparés à la période précédente de même durée. "
                   "Un e-mail est « traité » dès qu'ACA l'a analysé, « validé » seulement après "
                   "qu'une personne a cliqué Valider — aucune écriture CRM n'a lieu avant.",
        "items": [
            _kpi("E-mails traités", funnel["classifiés"], previous=prev_funnel["classifiés"]),
            _kpi("Propositions rédigées", funnel["proposition rédigée"],
                 previous=prev_funnel["proposition rédigée"]),
            _kpi("Leads validés", funnel["validés"], previous=prev_funnel["validés"]),
            _kpi("Taux de conversion", conversion(funnel), previous=conversion(prev_funnel),
                 suffix=" %", hint="validés / traités"),
            _kpi("Délai médian de validation",
                 round(median_now) if median_now is not None else None,
                 previous=round(median_before) if median_before is not None else None,
                 suffix=" min", better="down",
                 hint="entre l'analyse et la décision humaine"),
        ],
    }


def _s_categories(window, previous, spec) -> dict:
    current = {row["classification"]: row["count"]
               for row in analytics_store.volume_by_category(start=window[0], end=window[1])}
    before = {row["classification"]: row["count"]
              for row in analytics_store.volume_by_category(start=previous[0], end=previous[1])}
    # Union des deux périodes, pas seulement de la courante : une catégorie qui a disparu ce mois-ci
    # est une information (un canal qui s'est tari), et l'omettre ferait lire le rapport comme si
    # elle n'avait jamais existé.
    keys = sorted(set(current) | set(before), key=lambda k: -current.get(k, 0))
    return {
        "type": "bars",
        "title": "Répartition par catégorie",
        "context": "Ce qu'ACA a reçu, par nature de demande. DEMANDE_DEMO et DEVIS sont les seules "
                   "catégories qui produisent une proposition ; SUPPORT et AUTRE sont routées vers "
                   "l'équipe concernée, SPAM est écarté.",
        "items": [
            {"label": key.replace("_", " ").capitalize(), "value": current.get(key, 0),
             "previous": before.get(key, 0)}
            for key in keys
        ],
    }


def _s_trend(window, previous, spec) -> dict:
    daily = analytics_store.daily_volume(start=window[0], end=window[1])
    return {
        "type": "line",
        "title": "Volume quotidien",
        "context": "Nombre d'e-mails analysés par jour sur la période. Les creux correspondent "
                   "normalement aux week-ends et aux jours fériés.",
        "points": [{"label": row["jour"], "value": row["count"]} for row in daily],
    }


def _s_funnel(window, previous, spec) -> dict:
    funnel = analytics_store.funnel_counts(start=window[0], end=window[1])
    prev_funnel = analytics_store.funnel_counts(start=previous[0], end=previous[1])
    return {
        "type": "bars",
        "title": "Entonnoir de traitement",
        "context": "Du message reçu à la décision humaine. L'écart entre « proposition rédigée » "
                   "et « validés » représente les leads encore en attente de relecture ou écartés.",
        "items": [
            {"label": step.capitalize(), "value": value, "previous": prev_funnel.get(step, 0)}
            for step, value in funnel.items()
        ],
    }


# Tranches de délai de réponse. Le choix des bornes n'est pas neutre : « moins d'une heure » est le
# seuil commercial qui compte réellement (un prospect qui a écrit à trois fournisseurs retient
# souvent celui qui répond le premier), et « plus de 24 h » est le seuil au-delà duquel la réponse
# arrive après que la décision a été prise ailleurs.
RESPONSE_BUCKETS = (
    ("Moins d'1 h", 0, 60),
    ("1 h à 4 h", 60, 240),
    ("4 h à 24 h", 240, 1440),
    ("Plus de 24 h", 1440, None),
)


def _s_response(window, previous, spec) -> dict:
    times = analytics_store.response_times(start=window[0], end=window[1])
    prev_times = analytics_store.response_times(start=previous[0], end=previous[1])

    def bucketise(rows):
        counts = {label: 0 for label, _, _ in RESPONSE_BUCKETS}
        for row in rows:
            for label, low, high in RESPONSE_BUCKETS:
                if row["minutes"] >= low and (high is None or row["minutes"] < high):
                    counts[label] += 1
                    break
        return counts

    current, before = bucketise(times), bucketise(prev_times)
    return {
        "type": "bars",
        "title": "Réactivité",
        "context": "Temps écoulé entre l'analyse automatique et la validation humaine, par tranche. "
                   "Ce délai mesure la disponibilité de l'équipe, pas la vitesse de l'IA — "
                   "l'analyse elle-même prend moins d'une minute.",
        "items": [
            {"label": label, "value": current[label], "previous": before[label]}
            for label, _, _ in RESPONSE_BUCKETS
        ],
    }


def _s_emails(window, previous, spec) -> dict:
    columns = [c for c in (spec.get("columns") or list(analytics_store.EVENT_COLUMNS))
               if c in analytics_store.EVENT_COLUMNS]
    if not columns:
        columns = ["classified_at", "sender", "classification"]
    filters = spec.get("filters") or {}
    rows = analytics_store.list_events(
        start=window[0], end=window[1],
        classifications=filters.get("classifications") or None,
        sender_contains=filters.get("sender_contains") or "",
        validated_only=bool(filters.get("validated_only")),
        limit=int(spec.get("max_rows") or 500),
    )

    def render(row, column):
        value = row.get(column)
        if column == "has_draft":
            return "oui" if value else "non"
        if column == "thread_id":
            return (value or "")[:8]
        return value if value not in (None, "") else "—"

    context = ("Détail e-mail par e-mail sur la période, le plus récent d'abord. "
               "Les colonnes affichées sont celles demandées lors de la génération.")
    if filters.get("classifications"):
        context += " Filtré sur : " + ", ".join(filters["classifications"]) + "."
    if filters.get("sender_contains"):
        context += f" Expéditeur contenant « {filters['sender_contains']} »."
    if filters.get("validated_only"):
        context += " Leads validés uniquement."
    return {
        "type": "table",
        "title": "Détail des e-mails",
        "context": context,
        "columns": [analytics_store.EVENT_COLUMNS[c] for c in columns],
        "rows": [[render(row, c) for c in columns] for row in rows],
        "total": len(rows),
    }


def _s_validations(window, previous, spec) -> dict:
    # `audit_log.list_recent` n'a pas de bornes : c'est le registre restreint des engagements CRM,
    # lu jusqu'ici uniquement en « N derniers ». On filtre ici plutôt que d'élargir sa signature —
    # ce journal n'est jamais purgé et reste volontairement minimal côté requêtes.
    start_text, end_text = window[0].strftime(_TS), window[1].strftime(_TS)
    rows = [
        row for row in audit_log.list_recent(limit=1000)
        if start_text <= (row["validated_at"] or "") < end_text
    ]
    return {
        "type": "table",
        "title": "Validations enregistrées",
        "context": "Registre d'audit des écritures CRM : qui a engagé quoi, et quand. Ce registre "
                   "est chaîné par empreinte et n'est jamais purgé — c'est la pièce opposable en "
                   "cas de contestation.",
        "columns": ["Date", "Validé par", "Catégorie", "Expéditeur"],
        "rows": [[r["validated_at"], r["validated_by"] or "—", r["classification"] or "—",
                  r["sender"] or "—"] for r in rows],
        "total": len(rows),
    }


def _s_activity(window, previous, spec) -> dict:
    start_text, end_text = window[0].strftime(_TS), window[1].strftime(_TS)
    prev_start, prev_end = previous[0].strftime(_TS), previous[1].strftime(_TS)
    current = activity_log.action_counts(start_text, end_text)
    before = {row["action"]: row["count"]
              for row in activity_log.action_counts(prev_start, prev_end)}
    return {
        "type": "bars",
        "title": "Actions enregistrées",
        "context": "Ce que le journal d'activité a consigné sur la période, par type de geste — "
                   "humains et travaux planifiés confondus.",
        "items": [
            {"label": row["action_label"], "value": row["count"],
             "previous": before.get(row["action"], 0)}
            for row in current
        ],
    }


def _s_actors(window, previous, spec) -> dict:
    rows = activity_log.actors_between(window[0].strftime(_TS), window[1].strftime(_TS))
    return {
        "type": "table",
        "title": "Activité par personne",
        "context": "Volume d'actions par compte sur la période. La colonne « incidents » compte "
                   "les refus de permission et les échecs techniques, pas les rejets de leads — "
                   "rejeter un lead est une décision commerciale normale.",
        "columns": ["Personne", "Actions", "Validations", "Rejets", "Incidents"],
        "rows": [[r["actor"] or "—", r["actions"], r["validations"], r["rejets"], r["incidents"]]
                 for r in rows],
        "total": len(rows),
    }


def _s_reviews(window, previous, spec) -> dict:
    rows = review_store.list_between(window[0].strftime(_TS), window[1].strftime(_TS))
    resolved = [r for r in rows if r["status"] == review_store.STATUS_RESOLVED]
    pending = [r for r in rows if r["status"] == review_store.STATUS_PENDING]
    return {
        "type": "table",
        "title": "Demandes de relecture",
        "context": f"{len(rows)} demande(s) créée(s) sur la période, dont {len(resolved)} traitée(s) "
                   f"et {len(pending)} encore en attente. Une demande de relecture est un lead "
                   "qu'un opérateur n'a pas voulu trancher seul.",
        "columns": ["Date", "Demandeur", "Destinataire", "Objet", "Statut"],
        "rows": [[r["created_at"], r["requester"], r["recipient"],
                  (r["subject"] or r["sender"] or "—")[:60], r["status_label"]] for r in rows],
        "total": len(rows),
    }


def _s_tasks(window, previous, spec) -> dict:
    start_text, end_text = window[0].strftime(_TS), window[1].strftime(_TS)
    rows = [
        task for task in task_store.list_recent(limit=500)
        if start_text <= (task["created_at"] or "") < end_text
    ]
    return {
        "type": "table",
        "title": "Envois programmés et rappels",
        "context": "Tâches datées posées par une personne. Un envoi programmé part du brouillon "
                   "Gmail déjà relu et validé — il n'y a pas d'envoi sans lecture humaine "
                   "préalable, seulement une exécution différée.",
        "columns": ["Échéance", "Nature", "Créée par", "Statut", "Détail"],
        "rows": [[t["due_at"], t["kind_label"], t["created_by"] or "—", t["status_label"],
                  (t["note"] or t["label"] or "—")[:60]] for t in rows],
        "total": len(rows),
    }


def _s_tokens(window, previous, spec) -> dict:
    current = analytics_store.token_stats(start=window[0], end=window[1])
    before = analytics_store.token_stats(start=previous[0], end=previous[1])
    return {
        "type": "kpis",
        "title": "Consommation des modèles",
        "context": "Jetons consommés par les modèles de langage sur la période. Informatif : la "
                   "stack actuelle (Groq, Gemini) est en offre gratuite — ce chiffre sert de base "
                   "d'estimation si le volume impose un jour un fournisseur payant.",
        "items": [
            _kpi("Analyses mesurées", current["analyses"], previous=before["analyses"],
                 better="neutral"),
            _kpi("Jetons en entrée", current["total_entree"], previous=before["total_entree"],
                 better="neutral"),
            _kpi("Jetons en sortie", current["total_sortie"], previous=before["total_sortie"],
                 better="neutral"),
            _kpi("Moyenne par analyse", current["moyenne_par_analyse"],
                 previous=before["moyenne_par_analyse"], better="down"),
        ],
    }


def _s_quality(window, previous, spec) -> dict:
    current = analytics_store.edit_rate(start=window[0], end=window[1])
    before = analytics_store.edit_rate(start=previous[0], end=previous[1])
    return {
        "type": "kpis",
        "title": "Qualité des propositions",
        "context": "Part des propositions que la personne a corrigées avant de valider. Un taux qui "
                   "baisse signifie que les brouillons demandent moins de retouches ; un taux nul "
                   "sur un petit volume ne prouve rien.",
        "items": [
            _kpi("Propositions validées", current["validés"], previous=before["validés"]),
            _kpi("Propositions corrigées", current["édités"], previous=before["édités"],
                 better="down"),
            _kpi("Taux de correction", current["taux_pct"], previous=before["taux_pct"],
                 suffix=" %", better="down"),
        ],
    }


def _s_queue(window, previous, spec) -> dict:
    return {
        "type": "kpis",
        "title": "État à la date de génération",
        "context": "Contrairement au reste du rapport, cette section décrit l'instant présent et "
                   "non la période : c'est ce qui attend une personne au moment où le document a "
                   "été produit.",
        "items": [
            _kpi("Analyses en attente de validation", len(queue_store.list_pending()),
                 better="down"),
            _kpi("Tâches programmées à venir", len(task_store.list_pending()), better="neutral"),
        ],
    }


# Registre déclaratif des sections — ajouter une section au rapport = une entrée ici, rien d'autre
# (même esprit que `ROUTING_DESTINATIONS` dans app.py et `JOBS` dans scheduler.py). `label` et
# `description` alimentent directement les cases à cocher de l'interface : la personne qui compose
# son rapport lit la même phrase que celle qui figurera dans le document.
SECTIONS = {
    "summary": {"label": "Vue d'ensemble", "group": GROUP_COMMERCIAL, "fn": _s_summary,
                "description": "Chiffres clés et évolution vs la période précédente."},
    "categories": {"label": "Répartition par catégorie", "group": GROUP_COMMERCIAL,
                   "fn": _s_categories,
                   "description": "Volume par nature de demande (devis, démo, support…)."},
    "trend": {"label": "Volume quotidien", "group": GROUP_COMMERCIAL, "fn": _s_trend,
              "description": "Courbe jour par jour sur la période."},
    "funnel": {"label": "Entonnoir de traitement", "group": GROUP_COMMERCIAL, "fn": _s_funnel,
               "description": "Reçu → proposition rédigée → validé."},
    "emails": {"label": "Détail des e-mails", "group": GROUP_COMMERCIAL, "fn": _s_emails,
               "description": "La liste ligne à ligne, avec les colonnes de votre choix."},
    "response": {"label": "Réactivité", "group": GROUP_QUALITY, "fn": _s_response,
                 "description": "Délai de validation réparti par tranche (< 1 h, > 24 h…)."},
    "quality": {"label": "Qualité des propositions", "group": GROUP_QUALITY, "fn": _s_quality,
                "description": "Part des brouillons corrigés avant envoi."},
    "reviews": {"label": "Demandes de relecture", "group": GROUP_QUALITY, "fn": _s_reviews,
                "description": "Leads transmis à un administrateur pour un second avis."},
    "validations": {"label": "Validations enregistrées", "group": GROUP_TRACE,
                    "fn": _s_validations,
                    "description": "Registre d'audit des écritures CRM."},
    "activity": {"label": "Actions enregistrées", "group": GROUP_TRACE, "fn": _s_activity,
                 "description": "Ce que le journal d'activité a consigné, par type de geste."},
    "actors": {"label": "Activité par personne", "group": GROUP_TRACE, "fn": _s_actors,
               "description": "Volume d'actions, validations et incidents par compte."},
    "tasks": {"label": "Envois programmés et rappels", "group": GROUP_OPS, "fn": _s_tasks,
              "description": "Tâches datées posées par l'équipe sur la période."},
    "tokens": {"label": "Consommation des modèles", "group": GROUP_OPS, "fn": _s_tokens,
               "description": "Jetons consommés — informatif, la stack est gratuite."},
    "queue": {"label": "État à la date de génération", "group": GROUP_OPS, "fn": _s_queue,
              "description": "Ce qui attend une personne à l'instant du rapport."},
}

# Sections du rapport mensuel automatique : tout sauf le détail e-mail par e-mail. Ce dernier est
# volontairement exclu du document produit sans qu'on l'ait demandé — il peut faire des dizaines de
# pages, et il recopie des adresses de prospects dans un fichier qui circulera. Il reste disponible
# en un clic dans le rapport paramétrable, où c'est une décision consciente.
MONTHLY_SECTIONS = tuple(key for key in SECTIONS if key != "emails")

DEFAULT_SECTIONS = ("summary", "categories", "trend", "funnel", "response", "quality")


def new_spec(start: datetime, end: datetime, *, title: str = "", sections=None, columns=None,
             filters: dict = None, compare: bool = True, note: str = "",
             generated_by: str = "", max_rows: int = 500) -> dict:
    """
    Spécification d'un rapport. Un simple dict (et non une classe) parce qu'il est sérialisé tel
    quel en préréglage JSON dans `config_store` : une classe imposerait une conversion aller-retour
    dont la seule conséquence serait qu'un préréglage enregistré par une version antérieure cesse
    un jour de se relire.
    """
    return {
        "title": title or "Rapport d'activité",
        "start": start, "end": end,
        "sections": list(sections) if sections is not None else list(DEFAULT_SECTIONS),
        "columns": list(columns) if columns else None,
        "filters": filters or {},
        "compare": bool(compare),
        "note": note or "",
        "generated_by": generated_by or "",
        "max_rows": max_rows,
    }


def monthly_spec(year: int, month: int, *, generated_by: str = "(planificateur)") -> dict:
    """Spécification figée du rapport mensuel automatique (§20, travail `report` du planificateur)."""
    start, end = month_bounds(year, month)
    return new_spec(
        start, end, title=f"Rapport mensuel — {month_label(year, month)}",
        sections=MONTHLY_SECTIONS, compare=True, generated_by=generated_by,
        note="Rapport généré automatiquement à la fin du mois. Les écarts sont calculés par "
             "rapport à la période précédente de même durée.",
    )


def collect(spec: dict) -> dict:
    """
    Exécute une spécification et renvoie `{"meta": …, "groups": [{"title", "blocks"}]}`.

    Une section qui échoue **n'interrompt pas le rapport** : elle est remplacée par un bloc de texte
    qui dit ce qui a manqué. Même raisonnement que la dégradation gracieuse du reste du projet — un
    rapport mensuel amputé d'une section reste utile, un rapport qui ne se génère pas ne l'est pas ;
    et le taire serait pire, puisque le lecteur croirait qu'il n'y avait rien à dire.
    """
    start, end = spec["start"], spec["end"]
    window = (start, end)
    compare = spec.get("compare", True)
    previous = previous_period(start, end) if compare else (start, end)

    blocks_by_group = {}
    for key in spec.get("sections") or []:
        section = SECTIONS.get(key)
        if section is None:
            continue
        try:
            block = section["fn"](window, previous, spec)
        except Exception as exc:  # noqa: BLE001 — cf. docstring
            block = {
                "type": "text", "title": section["label"],
                "context": "Section indisponible pour cette période.",
                "body": f"Les données de cette section n'ont pas pu être lues "
                        f"({exc.__class__.__name__}). Le reste du rapport n'est pas affecté.",
            }
        block["key"] = key
        if not compare:
            # Sans comparaison demandée, `previous` vaut la période elle-même : laisser les écarts
            # affichés donnerait partout « 0 %  », c'est-à-dire une comparaison fausse présentée
            # comme vraie. On les retire plutôt que de les laisser mentir.
            for item in block.get("items", []):
                item.pop("comparison", None)
                item.pop("previous", None)
        blocks_by_group.setdefault(section["group"], []).append(block)

    groups = [
        {"title": group, "blocks": blocks_by_group[group]}
        for group in GROUP_ORDER if group in blocks_by_group
    ]
    return {
        "meta": {
            "title": spec.get("title") or "Rapport d'activité",
            "period_label": period_label(start, end),
            "period_start": start.strftime(_TS),
            "period_end": end.strftime(_TS),
            "comparison_label": period_label(*previous_period(start, end)) if compare else "",
            "generated_at": datetime.now().strftime("%d/%m/%Y à %H:%M"),
            "generated_by": spec.get("generated_by") or "",
            "note": spec.get("note") or "",
            "sections": [k for k in (spec.get("sections") or []) if k in SECTIONS],
        },
        "groups": groups,
    }


def report_filename(spec: dict) -> str:
    """Nom de fichier stable et triable : `rapport-2026-07.pdf`, `rapport-2026-07-01_2026-07-15.pdf`."""
    start, end = spec["start"], spec["end"]
    if _is_whole_month(start, end):
        return f"rapport-{start.strftime('%Y-%m')}.pdf"
    last_day = end - timedelta(seconds=1)
    return f"rapport-{start.strftime('%Y-%m-%d')}_{last_day.strftime('%Y-%m-%d')}.pdf"


# ── Préréglages (§20) ─────────────────────────────────────────────────────────────────────────
# Un rapport qu'il faut recomposer case par case chaque mois ne sera composé qu'une fois. Stockés
# dans `config_store` sous le préfixe ci-dessous, comme les profils de marque du §18 — même
# mécanisme, donc même comportement multi-tenant et même sauvegarde, sans nouvelle table.
PRESET_PREFIX = "REPORT_PRESET_"


def _preset_payload(spec: dict) -> str:
    """
    Sérialise la partie **reconductible** d'une spécification : tout sauf les dates.

    Les bornes sont volontairement exclues : un préréglage nommé « Revue mensuelle direction »
    décrit un contenu, pas un mois. Y figer juillet 2026 en ferait un préréglage inutilisable dès
    août, ce que personne ne comprendrait avant de l'avoir constaté.
    """
    return json.dumps({
        "title": spec.get("title", ""),
        "sections": spec.get("sections") or [],
        "columns": spec.get("columns"),
        "filters": spec.get("filters") or {},
        "compare": bool(spec.get("compare", True)),
        "note": spec.get("note", ""),
        "max_rows": spec.get("max_rows", 500),
    }, ensure_ascii=False)


def save_preset(name: str, spec: dict) -> str:
    """Enregistre un préréglage et renvoie le nom retenu (assaini). Lève si le nom est vide."""
    from aca.storage import config_store

    clean = "".join(c for c in (name or "").strip() if c.isalnum() or c in " -_").strip()
    if not clean:
        raise ValueError("Un préréglage doit avoir un nom.")
    config_store.set_setting(PRESET_PREFIX + clean, _preset_payload(spec))
    return clean


def list_presets() -> dict:
    """
    Préréglages enregistrés pour le tenant courant : `{nom: contenu}`.

    Une entrée illisible (JSON corrompu, ou vidée par `delete_preset`) est ignorée plutôt que de
    faire tomber l'écran : un préréglage abîmé est une gêne, une page de rapports inaccessible est
    une panne.
    """
    from aca.storage import config_store

    presets = {}
    for key, value in config_store.get_all_settings().items():
        if not key.startswith(PRESET_PREFIX):
            continue
        try:
            presets[key[len(PRESET_PREFIX):]] = json.loads(value)
        except (ValueError, TypeError):
            continue
    return presets


def delete_preset(name: str) -> None:
    """
    Efface un préréglage. `config_store` ne sait qu'écrire une valeur : on y met une chaîne vide,
    que `list_presets` écarte. La ligne subsiste dans la table des réglages, sans effet visible —
    ajouter une suppression au magasin pour ce seul cas coûterait plus que ça ne rapporte.
    """
    from aca.storage import config_store

    config_store.set_setting(PRESET_PREFIX + name, "")

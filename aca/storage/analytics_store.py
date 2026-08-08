"""
Journal léger de TOUTES les classifications (P2 §11.4 item 16, volet tableau de bord).

Contrairement à l'onglet Sheets `Leads` (qui ne reçoit que les DEMANDE_DEMO/DEVIS validés) et à
`audit_log.py` (qui ne trace que les événements de validation), ce registre local capture CHAQUE
e-mail classé — y compris SPAM/AUTRE/SUPPORT, qui ne sont jamais validés — pour que le tableau de
bord affiche un volume par catégorie complet et un vrai temps de réponse (classification → clic
« Valider »), quelle que soit la source (saisie manuelle, import Gmail ponctuel, ou poller.py).

Fondation multi-tenant (§12 item 3 / §14.3) : chaque table porte un `org_id` (défaut : tenant
courant, cf. aca.core.tenant) et chaque lecture est scopée dessus par défaut — un seul tenant
"default" aujourd'hui, mais le tableau de bord d'un futur tenant ne verrait déjà que ses propres
données sans changement de code supplémentaire.
"""
import os
import sqlite3
from datetime import datetime, timedelta
from .sqlite_retry import with_sqlite_retry
from aca.core.tenant import current_org_id

DB_PATH = os.getenv("ACA_ANALYTICS_DB", "data/analytics.sqlite")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "thread_id TEXT PRIMARY KEY, classification TEXT NOT NULL, sender TEXT, source TEXT, "
        "has_draft INTEGER NOT NULL DEFAULT 0, classified_at TEXT NOT NULL, validated_at TEXT, "
        "org_id TEXT NOT NULL DEFAULT 'default')"
    )
    # Capture (brouillon original, brouillon édité) au clic « Valider » (§13 item 3, audit des PDF
    # "ACAM v2 Blueprint" — version réalisable de leur "Continuous Training Loop" : pas de
    # fine-tuning sur la stack gratuite actuelle, mais les paires avant/après sont le futur corpus
    # few-shot/éval). Une ligne par édition, sans contrainte d'unicité sur thread_id : ce module ne
    # doit pas supposer qu'un thread n'est validé qu'une seule fois dans toute son histoire.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS draft_edits ("
        "thread_id TEXT NOT NULL, original TEXT NOT NULL, edited TEXT NOT NULL, edited_at TEXT NOT NULL, "
        "org_id TEXT NOT NULL DEFAULT 'default')"
    )
    # Usage de tokens par analyse (§13 item 4 / §12 item 4 — "Quota Usage Tracker" des PDF, première
    # marche gratuite : logger les tokens Groq déjà présents dans les réponses API, avant tout
    # branchement Stripe qui n'a de sens qu'en phase commerciale).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS token_usage ("
        "thread_id TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, "
        "recorded_at TEXT NOT NULL, org_id TEXT NOT NULL DEFAULT 'default')"
    )
    # Migration idempotente (fondation multi-tenant) : ajoute `org_id` aux bases créées avant cette
    # colonne, sans perdre l'historique existant (reporté sur le tenant "default").
    for table in ("events", "draft_edits", "token_usage"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "org_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'")
    conn.commit()
    return conn


@with_sqlite_retry
def record_classification(
    thread_id: str, classification: str, sender: str, source: str, org_id: str = None
) -> None:
    """
    Enregistre l'événement de classification, une seule fois par thread (`INSERT OR IGNORE`).
    Idempotent par design : appelé à chaque resynchronisation de l'état (y compris après une
    clarification résolue), donc rejouable sans créer de doublon. `source` ∈
    {"manuel", "gmail_import", "poller"}.
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events (thread_id, classification, sender, source, classified_at, org_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, classification, sender, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             org_id or current_org_id()),
        )
        conn.commit()


@with_sqlite_retry
def record_draft_ready(thread_id: str) -> None:
    """
    Marque qu'une proposition a été rédigée pour ce thread. Appel séparé de `record_classification`
    (plutôt qu'un seul INSERT) car la classification est connue AVANT la proposition (le Stratège
    peut tourner après une clarification qui a déjà mis la première ligne en base) — un simple
    `INSERT OR IGNORE` figerait `has_draft=0` pour toujours si on l'y intégrait directement.
    """
    with _connect() as conn:
        conn.execute("UPDATE events SET has_draft = 1 WHERE thread_id = ?", (thread_id,))
        conn.commit()


@with_sqlite_retry
def record_validation(thread_id: str) -> None:
    """Renseigne `validated_at` pour un thread déjà classé (appelé au clic « Valider » dans l'UI)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE events SET validated_at = ? WHERE thread_id = ? AND validated_at IS NULL",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), thread_id),
        )
        conn.commit()


@with_sqlite_retry
def record_edit(thread_id: str, original: str, edited: str, org_id: str = None) -> None:
    """
    Enregistre une paire (brouillon original, brouillon édité) quand un humain modifie la
    proposition du Stratège avant de cliquer « Valider » (cf. ui.py). No-op si `edited == original`
    (rien n'a changé — pas la peine d'encombrer la table). Ce n'est PAS un fine-tuning : la stack
    reste gratuite (Groq free tier) et rien ne réentraîne automatiquement les modèles. C'est un
    corpus brut pour un futur enrichissement manuel du few-shot prompting ou de `eval_dataset.json`.
    """
    if edited == original:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO draft_edits (thread_id, original, edited, edited_at, org_id) VALUES (?, ?, ?, ?, ?)",
            (thread_id, original, edited, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), org_id or current_org_id()),
        )
        conn.commit()


@with_sqlite_retry
def get_draft_edit(thread_id: str, org_id: str = None) -> dict:
    """
    Dernière paire (original, édité) enregistrée pour ce thread, ou `None` — §18, recap #5/§4 item 2.

    `record_edit()` n'a délibérément pas de contrainte d'unicité sur `thread_id` ; cette fonction lit
    donc la ligne la plus récente (`rowid` décroissant), celle qui correspond à la validation
    effectivement envoyée. Alimente `ui_kit.diff()` dans la frise chronologique d'un lead : le §17 ne
    consignait que des longueurs de caractères (« 340 → 412 »), qui ne disent rien de CE QUI a
    changé — le texte complet vivait déjà dans cette table, il ne manquait que la lecture.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT original, edited, edited_at FROM draft_edits "
            "WHERE thread_id = ? AND org_id = ? ORDER BY rowid DESC LIMIT 1",
            (thread_id, org_id or current_org_id()),
        ).fetchone()
    if row is None:
        return None
    return {"original": row[0], "edited": row[1], "edited_at": row[2]}


@with_sqlite_retry
def record_tokens(thread_id: str, input_tokens: int, output_tokens: int, org_id: str = None) -> None:
    """
    Enregistre la consommation de tokens Groq d'une analyse complète (tous les appels LLM du
    graphe, agrégés via le callback `UsageMetadataCallbackHandler` de langchain_core — cf.
    `_run_with_usage_tracking()` dans ui.py/poller.py). No-op silencieux si les deux compteurs sont
    à zéro (ex. un e-mail SPAM court-circuité avant le premier appel LLM ne produit aucune donnée
    utile à journaliser).
    """
    if not input_tokens and not output_tokens:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO token_usage (thread_id, input_tokens, output_tokens, recorded_at, org_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, input_tokens, output_tokens, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             org_id or current_org_id()),
        )
        conn.commit()


def _cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _as_text(moment) -> str:
    """Accepte un `datetime` ou une chaîne déjà au format de la base, renvoie la chaîne."""
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    return str(moment)


def _window(column: str, days: int, start=None, end=None) -> tuple:
    """
    Fenêtre temporelle d'une lecture : renvoie `(fragment_sql, paramètres)`.

    Le tableau de bord raisonne en « N derniers jours » ; les rapports (§20) doivent pouvoir viser
    un mois civil précis ou une plage choisie à la main. Plutôt que de dupliquer chaque fonction en
    une variante `*_between`, toutes acceptent désormais `start`/`end` (un `datetime` ou une chaîne
    au format de la base) qui, **lorsqu'ils sont fournis, l'emportent sur `days`**. Aucun appel
    existant ne change : sans ces arguments, la fenêtre est exactement celle d'avant.

    La borne haute est **exclue**. Deux périodes consécutives ne doivent pas compter deux fois
    l'événement qui tombe pile à la frontière — sinon la comparaison mois précédent / mois courant,
    qui est la raison d'être du rapport, gonflerait des deux côtés au lieu de se répartir.
    """
    lower = _as_text(start) if start is not None else _cutoff(days)
    fragment = f"{column} >= ?"
    params = [lower]
    upper = _as_text(end)
    if upper:
        fragment += f" AND {column} < ?"
        params.append(upper)
    return fragment, params


@with_sqlite_retry
def volume_by_category(days: int = 30, org_id: str = None, start=None, end=None) -> list[dict]:
    """Nombre d'e-mails classés par catégorie du tenant courant sur la période, décroissant."""
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT classification, COUNT(*) FROM events WHERE {window} AND org_id = ? "
            "GROUP BY classification ORDER BY COUNT(*) DESC",
            (*params, org_id or current_org_id()),
        ).fetchall()
    return [{"classification": r[0], "count": r[1]} for r in rows]


@with_sqlite_retry
def daily_volume(days: int = 30, org_id: str = None, start=None, end=None) -> list[dict]:
    """Volume quotidien total du tenant courant (toutes catégories confondues), pour un graphe de tendance."""
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT substr(classified_at, 1, 10) AS jour, COUNT(*) FROM events "
            f"WHERE {window} AND org_id = ? GROUP BY jour ORDER BY jour",
            (*params, org_id or current_org_id()),
        ).fetchall()
    return [{"jour": r[0], "count": r[1]} for r in rows]


@with_sqlite_retry
def response_times(days: int = 30, org_id: str = None, start=None, end=None) -> list[dict]:
    """
    Durée (en minutes) entre classification et validation, pour chaque lead validé du tenant
    courant sur la période — matière première du graphe de latence (répondre < 1h vs > 24h, cf.
    ACAM_roadmap.md §11.4). Ignore les threads jamais validés (SPAM/AUTRE/SUPPORT routés, ou encore
    en attente).
    """
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, classification, classified_at, validated_at FROM events "
            f"WHERE validated_at IS NOT NULL AND {window} AND org_id = ?",
            (*params, org_id or current_org_id()),
        ).fetchall()
    results = []
    for thread_id, classification, classified_at, validated_at in rows:
        delta = (
            datetime.strptime(validated_at, "%Y-%m-%d %H:%M:%S")
            - datetime.strptime(classified_at, "%Y-%m-%d %H:%M:%S")
        )
        results.append({
            "thread_id": thread_id,
            "classification": classification,
            "minutes": round(delta.total_seconds() / 60, 1),
        })
    return results


@with_sqlite_retry
def funnel_counts(days: int = 30, org_id: str = None, start=None, end=None) -> dict:
    """Compte classé → proposition rédigée → validé du tenant courant, sur la période."""
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        tenant = org_id or current_org_id()
        classified = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {window} AND org_id = ?", (*params, tenant)
        ).fetchone()[0]
        drafted = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {window} AND has_draft = 1 AND org_id = ?",
            (*params, tenant),
        ).fetchone()[0]
        validated = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {window} AND validated_at IS NOT NULL AND org_id = ?",
            (*params, tenant),
        ).fetchone()[0]
    return {"classifiés": classified, "proposition rédigée": drafted, "validés": validated}


@with_sqlite_retry
def edit_rate(days: int = 30, org_id: str = None, start=None, end=None) -> dict:
    """
    % de brouillons validés qui ont été modifiés par un humain avant validation, sur la période,
    pour le tenant courant — KPI "taux d'édition" du tableau de bord (§13 item 3). Le dénominateur
    est le nombre de leads VALIDÉS (pas classés) : seul un brouillon qui a atteint la validation
    pouvait être édité.
    """
    events_window, events_params = _window("classified_at", days, start, end)
    edits_window, edits_params = _window("edited_at", days, start, end)
    with _connect() as conn:
        tenant = org_id or current_org_id()
        validated = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {events_window} AND validated_at IS NOT NULL "
            "AND org_id = ?",
            (*events_params, tenant),
        ).fetchone()[0]
        edited = conn.execute(
            f"SELECT COUNT(DISTINCT thread_id) FROM draft_edits WHERE {edits_window} AND org_id = ?",
            (*edits_params, tenant),
        ).fetchone()[0]
    pct = round(100 * edited / validated, 1) if validated else 0.0
    return {"validés": validated, "édités": edited, "taux_pct": pct}


@with_sqlite_retry
def token_stats(days: int = 30, org_id: str = None, start=None, end=None) -> dict:
    """
    Tokens Groq consommés sur la période par le tenant courant : total entrée/sortie et moyenne
    par analyse — KPI "Quota Usage Tracker" du tableau de bord (§13 item 4). Purement informatif
    tant que Groq reste gratuit ; sert de base théorique de coût si la stack migre un jour vers un
    fournisseur payant (aussi la base du futur suivi de facturation par organisation, §12 item 4).
    """
    window, params = _window("recorded_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) "
            f"FROM token_usage WHERE {window} AND org_id = ?",
            (*params, org_id or current_org_id()),
        ).fetchone()
    count, total_in, total_out = rows
    avg = round((total_in + total_out) / count, 0) if count else 0
    return {"analyses": count, "total_entree": total_in, "total_sortie": total_out, "moyenne_par_analyse": avg}


# ── Répartitions du tableau de bord (§22) ─────────────────────────────────────────────────────
# Quatre agrégats qui répondent chacun à une question que le tableau de bord ne savait pas poser.
# Aucun ne demande de nouvelle donnée : tout était déjà dans `events` depuis l'origine, seule la
# lecture manquait — même forme de manque que `get_draft_edit` (§18) et `list_events` (§20).

#: Bornes des tranches de délai, en minutes. Ordonnées et **ordinales** : ce n'est pas une liste de
#: catégories interchangeables mais une échelle de gravité, ce qui impose au graphe un dégradé d'une
#: seule teinte plutôt qu'une palette catégorielle.
RESPONSE_BUCKETS = (
    ("moins d'1 h", 0, 60),
    ("1 à 4 h", 60, 240),
    ("4 à 24 h", 240, 1440),
    ("plus de 24 h", 1440, None),
)


def bucket_response_times(rows: list[dict]) -> list[dict]:
    """
    Répartit des durées de validation dans `RESPONSE_BUCKETS`.

    Fonction **pure**, séparée de la lecture SQL : c'est la seule partie qui porte un jugement
    métier (« au-delà de 24 h, on a perdu la main sur le prospect »), donc celle qui mérite d'être
    testable sans base. Les tranches vides sont conservées — une tranche « plus de 24 h » absente
    du graphe se lit comme « pas de données », alors qu'elle veut dire « aucun retard », c'est-à-dire
    exactement l'inverse et la meilleure nouvelle du tableau.
    """
    counts = {label: 0 for label, _, _ in RESPONSE_BUCKETS}
    for row in rows:
        minutes = row.get("minutes")
        if minutes is None:
            continue
        for label, low, high in RESPONSE_BUCKETS:
            if minutes >= low and (high is None or minutes < high):
                counts[label] += 1
                break
    return [{"tranche": label, "count": counts[label]} for label, _, _ in RESPONSE_BUCKETS]


@with_sqlite_retry
def by_source(days: int = 30, org_id: str = None, start=None, end=None) -> list[dict]:
    """
    Volume par origine (`manuel`, `gmail_import`, `poller`…) — la mesure d'ADOPTION.

    La colonne `source` est enregistrée depuis toujours et n'a jamais été affichée nulle part. Elle
    répond pourtant à la question qui décide du renouvellement de l'outil : est-ce que la réception
    automatique fait le travail, ou est-ce que les gens ressaisissent encore les e-mails à la main ?
    Un produit « automatique » dont 90 % du volume est manuel n'est pas adopté, et rien dans
    l'interface ne le disait.
    """
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(source, ''), 'inconnu'), COUNT(*) FROM events "
            f"WHERE {window} AND org_id = ? GROUP BY 1 ORDER BY COUNT(*) DESC",
            (*params, org_id or current_org_id()),
        ).fetchall()
    return [{"source": r[0], "count": r[1]} for r in rows]


@with_sqlite_retry
def hourly_volume(days: int = 30, org_id: str = None, start=None, end=None) -> list[dict]:
    """
    Volume par heure d'arrivée (0-23), heure locale telle qu'enregistrée.

    Utile parce qu'un réglage l'attend : §19 laisse choisir une fenêtre de réception (jours et
    heures). Sans cette courbe, ce réglage se choisit au jugé. Les 24 heures sont TOUJOURS
    renvoyées, y compris à zéro — un histogramme horaire troué se lit comme une courbe, alors que
    les creux sont précisément l'information recherchée.
    """
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT CAST(substr(classified_at, 12, 2) AS INTEGER), COUNT(*) FROM events "
            f"WHERE {window} AND org_id = ? GROUP BY 1",
            (*params, org_id or current_org_id()),
        ).fetchall()
    counts = {int(hour): count for hour, count in rows if hour is not None}
    return [{"heure": f"{h:02d} h", "count": counts.get(h, 0)} for h in range(24)]


@with_sqlite_retry
def top_senders(days: int = 30, org_id: str = None, start=None, end=None,
                limit: int = 8) -> list[dict]:
    """
    Correspondants les plus fréquents de la période, avec leur nombre de leads validés.

    `limit` est borné et bas volontairement : ce bloc sert à reconnaître d'un coup d'œil les
    interlocuteurs récurrents, pas à exporter un carnet d'adresses — c'est le rôle du rapport
    paramétrable (§20), qui lui descend au détail e-mail par e-mail.
    """
    window, params = _window("classified_at", days, start, end)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT sender, COUNT(*), SUM(CASE WHEN validated_at IS NOT NULL THEN 1 ELSE 0 END) "
            f"FROM events WHERE {window} AND org_id = ? AND sender IS NOT NULL AND sender != '' "
            "GROUP BY sender ORDER BY COUNT(*) DESC, sender LIMIT ?",
            (*params, org_id or current_org_id(), max(1, int(limit))),
        ).fetchall()
    return [{"expéditeur": r[0], "e-mails": r[1], "validés": r[2] or 0} for r in rows]


# ── Détail e-mail par e-mail (§20) ────────────────────────────────────────────────────────────
# Toutes les fonctions ci-dessus AGRÈGENT ; le rapport paramétrable demande aussi de pouvoir
# descendre à la ligne (« la classification et le nom des e-mails seulement »). La donnée existait
# depuis toujours dans `events`, seule la lecture manquait — même forme de manque que
# `get_draft_edit` au §18.
EVENT_COLUMNS = {
    "classified_at": "Date",
    "sender": "Expéditeur",
    "classification": "Catégorie",
    "source": "Source",
    "has_draft": "Proposition rédigée",
    "validated_at": "Validé le",
    "thread_id": "Identifiant d'analyse",
}


@with_sqlite_retry
def list_events(days: int = 30, org_id: str = None, start=None, end=None, *,
                classifications=None, sender_contains: str = "", validated_only: bool = False,
                limit: int = 2000) -> list[dict]:
    """
    Les e-mails classés de la période, un dict par e-mail (toutes les colonnes d'`EVENT_COLUMNS`).

    Le choix des colonnes à afficher appartient à l'appelant, pas à la requête : c'est lui qui sait
    si la personne a demandé « catégorie et expéditeur seulement » ou le détail complet, et filtrer
    ici obligerait à une requête par combinaison. `limit` est haut mais réel — un rapport n'est pas
    un export de base, et une année entière de trafic ne tiendrait de toute façon pas dans un PDF
    lisible ; la borne évite qu'un tenant volumineux fasse gonfler le document sans que personne
    l'ait voulu.
    """
    window, params = _window("classified_at", days, start, end)
    clauses = [window, "org_id = ?"]
    params = [*params, org_id or current_org_id()]
    if classifications:
        clauses.append(f"classification IN ({','.join('?' * len(classifications))})")
        params.extend(classifications)
    if sender_contains:
        clauses.append("sender LIKE ?")
        params.append(f"%{sender_contains}%")
    if validated_only:
        clauses.append("validated_at IS NOT NULL")
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE {' AND '.join(clauses)} "
            "ORDER BY classified_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(zip(EVENT_COLUMNS, row)) for row in rows]

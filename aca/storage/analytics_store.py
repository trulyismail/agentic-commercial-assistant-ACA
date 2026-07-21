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


@with_sqlite_retry
def volume_by_category(days: int = 30, org_id: str = None) -> list[dict]:
    """Nombre d'e-mails classés par catégorie du tenant courant sur les `days` derniers jours, décroissant."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT classification, COUNT(*) FROM events WHERE classified_at >= ? AND org_id = ? "
            "GROUP BY classification ORDER BY COUNT(*) DESC",
            (_cutoff(days), org_id or current_org_id()),
        ).fetchall()
    return [{"classification": r[0], "count": r[1]} for r in rows]


@with_sqlite_retry
def daily_volume(days: int = 30, org_id: str = None) -> list[dict]:
    """Volume quotidien total du tenant courant (toutes catégories confondues), pour un graphe de tendance."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT substr(classified_at, 1, 10) AS jour, COUNT(*) FROM events "
            "WHERE classified_at >= ? AND org_id = ? GROUP BY jour ORDER BY jour",
            (_cutoff(days), org_id or current_org_id()),
        ).fetchall()
    return [{"jour": r[0], "count": r[1]} for r in rows]


@with_sqlite_retry
def response_times(days: int = 30, org_id: str = None) -> list[dict]:
    """
    Durée (en minutes) entre classification et validation, pour chaque lead validé du tenant
    courant sur la période — matière première du graphe de latence (répondre < 1h vs > 24h, cf.
    ACAM_roadmap.md §11.4). Ignore les threads jamais validés (SPAM/AUTRE/SUPPORT routés, ou encore
    en attente).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, classification, classified_at, validated_at FROM events "
            "WHERE validated_at IS NOT NULL AND classified_at >= ? AND org_id = ?",
            (_cutoff(days), org_id or current_org_id()),
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
def funnel_counts(days: int = 30, org_id: str = None) -> dict:
    """Compte classé → proposition rédigée → validé du tenant courant, sur les `days` derniers jours."""
    with _connect() as conn:
        cutoff = _cutoff(days)
        tenant = org_id or current_org_id()
        classified = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND org_id = ?", (cutoff, tenant)
        ).fetchone()[0]
        drafted = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND has_draft = 1 AND org_id = ?",
            (cutoff, tenant),
        ).fetchone()[0]
        validated = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND validated_at IS NOT NULL AND org_id = ?",
            (cutoff, tenant),
        ).fetchone()[0]
    return {"classifiés": classified, "proposition rédigée": drafted, "validés": validated}


@with_sqlite_retry
def edit_rate(days: int = 30, org_id: str = None) -> dict:
    """
    % de brouillons validés qui ont été modifiés par un humain avant validation, sur `days` jours,
    pour le tenant courant — KPI "taux d'édition" du tableau de bord (§13 item 3). Le dénominateur
    est le nombre de leads VALIDÉS (pas classés) : seul un brouillon qui a atteint la validation
    pouvait être édité.
    """
    with _connect() as conn:
        cutoff = _cutoff(days)
        tenant = org_id or current_org_id()
        validated = conn.execute(
            "SELECT COUNT(*) FROM events WHERE classified_at >= ? AND validated_at IS NOT NULL AND org_id = ?",
            (cutoff, tenant),
        ).fetchone()[0]
        edited = conn.execute(
            "SELECT COUNT(DISTINCT thread_id) FROM draft_edits WHERE edited_at >= ? AND org_id = ?",
            (cutoff, tenant),
        ).fetchone()[0]
    pct = round(100 * edited / validated, 1) if validated else 0.0
    return {"validés": validated, "édités": edited, "taux_pct": pct}


@with_sqlite_retry
def token_stats(days: int = 30, org_id: str = None) -> dict:
    """
    Tokens Groq consommés sur `days` jours par le tenant courant : total entrée/sortie et moyenne
    par analyse — KPI "Quota Usage Tracker" du tableau de bord (§13 item 4). Purement informatif
    tant que Groq reste gratuit ; sert de base théorique de coût si la stack migre un jour vers un
    fournisseur payant (aussi la base du futur suivi de facturation par organisation, §12 item 4).
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) "
            "FROM token_usage WHERE recorded_at >= ? AND org_id = ?",
            (_cutoff(days), org_id or current_org_id()),
        ).fetchone()
    count, total_in, total_out = rows
    avg = round((total_in + total_out) / count, 0) if count else 0
    return {"analyses": count, "total_entree": total_in, "total_sortie": total_out, "moyenne_par_analyse": avg}

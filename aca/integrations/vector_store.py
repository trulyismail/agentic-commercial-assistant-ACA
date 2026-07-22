"""
pgvector-backed semantic search for the FAQ/Knowledge_Base (P2 item 14 — vector DB migration,
brought forward from the "later, if triggered" plan in ACAM_roadmap.md §11.1 at the user's explicit
request, ahead of the scale thresholds actually firing).

Replaces `sheets.py`'s per-process in-memory embedding cache (`_faq_embedding_cache`) with a real
Postgres table on Supabase: shared across every process (`ui.py` + `poller.py` no longer keep their
own separate copies) and queried with pgvector's native cosine-distance operator instead of a
Python loop over rows. FAQ AUTHORING IS UNCHANGED — still Google Sheets (`ingest.py`, `veille`
staging, human approve/reject in the UI's sidebar). This module only mirrors the currently visible
(validated) rows' embeddings into Postgres.

Graceful degradation, same principle as Tavily/Slack/Calendly: absent `DATABASE_URL` = this module
is inert, `sheets.py` keeps its existing in-memory cosine-similarity path unchanged.

Fondation multi-tenant + RLS (§12 item 3, audité §14.3 de docs/ACAM_roadmap.md) : chaque ligne porte
un `org_id` (défaut : tenant du process courant, cf. aca.core.tenant — un déploiement ACA = un
tenant, exactement comme `DATABASE_URL`/`GOOGLE_SHEETS_ID` sont déjà des réglages par déploiement,
pas par requête) et la table a `ROW LEVEL SECURITY` activée. Ce projet ne passe jamais par
PostgREST/une clé anon (connexion directe `psycopg` via `DATABASE_URL` uniquement — vérifié, cf.
§14.3), donc RLS ne peut pas s'appuyer sur `auth.uid()` côté Supabase : la politique compare
`org_id` à une variable de session Postgres (`app.current_org_id`), positionnée via
`set_config()` au tout début de chaque connexion empruntée au pool, AVANT toute requête — y
compris quand la même connexion physique est réutilisée pour un tenant différent ensuite (le
pool est petit et partagé), ce qui garantit qu'aucune requête ne peut lire les données d'un autre
tenant même en cas d'erreur applicative ultérieure.
"""
import os

from aca.core.tenant import current_org_id

# Lu dynamiquement (pas figé dans une constante au niveau module) : geler `DATABASE_URL` ici au
# moment de l'import désactiverait silencieusement pgvector pour tout le process si ce module est
# importé avant qu'un `load_dotenv()` quelconque n'ait tourné ailleurs dans le programme — bug
# réel trouvé le 2026-07-11 (voir docs/PROJECT_JOURNAL.md) : `sheets.py` importait ce module avant
# son propre `load_dotenv()`, ce qui gelait `DATABASE_URL` à "" et faisait retomber tout le RAG sur
# le cache en mémoire par process sans aucune erreur ni avertissement.

# gemini-embedding-001's native output size (no output_dimensionality override in sheets.py) —
# confirmed empirically, not from Gemini's docs table, since the model's default can change.
EMBEDDING_DIM = 3072

_pool = None


def is_enabled() -> bool:
    return bool(os.getenv("DATABASE_URL", ""))


def _get_pool():
    """Connexion (pool) paresseuse — un seul pool pour tout le process, table créée si absente."""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            conninfo=os.getenv("DATABASE_URL", ""), max_size=5, kwargs={"autocommit": True},
        )
        with _pool.connection() as conn:
            from pgvector.psycopg import register_vector

            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS faq_embeddings ("
                "id SERIAL PRIMARY KEY, question TEXT NOT NULL, reponse TEXT NOT NULL, "
                f"embedding VECTOR({EMBEDDING_DIM}) NOT NULL, updated_at TIMESTAMPTZ DEFAULT now(), "
                "org_id TEXT NOT NULL DEFAULT 'default')"
            )
            # Migration idempotente (fondation multi-tenant, §12 item 3) : ajoute `org_id` à une
            # table créée avant cette colonne, sans perdre les embeddings déjà synchronisés.
            existing_cols = {
                row[0] for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'faq_embeddings'"
                ).fetchall()
            }
            # Migrations de schéma + policy RLS : opérations réservées au PROPRIÉTAIRE de la table
            # (§14.3 — depuis le passage au rôle applicatif restreint `aca_app`, sans BYPASSRLS,
            # DATABASE_URL n'est plus le propriétaire de `faq_embeddings`). Ces étapes sont de
            # l'administration ponctuelle (exécutée une fois par un rôle admin type `postgres`),
            # pas quelque chose que chaque process applicatif doit refaire à froid : un rôle
            # restreint qui ne peut pas les exécuter n'est donc pas une erreur — la policy existe
            # déjà, c'est justement CE QUI REND la restriction du rôle utile.
            try:
                if "org_id" not in existing_cols:
                    conn.execute(
                        "ALTER TABLE faq_embeddings ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'"
                    )
                # RLS (§14.3) : ce projet se connecte uniquement via `DATABASE_URL` (psycopg direct,
                # jamais PostgREST/anon key), donc la politique s'appuie sur une variable de session
                # (`app.current_org_id`, positionnée par `_scope_to_tenant()` avant chaque requête) et
                # non sur `auth.uid()`. `DROP POLICY IF EXISTS` + `CREATE POLICY` est idempotent
                # indépendamment de la version de Postgres (contrairement à un hypothétique
                # `CREATE POLICY IF NOT EXISTS`, non supporté).
                conn.execute("ALTER TABLE faq_embeddings ENABLE ROW LEVEL SECURITY")
                # FORCE, pas seulement ENABLE : sans elle, Postgres exempte le PROPRIÉTAIRE de la
                # table de ses propres politiques RLS — la protection serait silencieusement
                # inactive pour une connexion qui se ferait passer pour le propriétaire.
                conn.execute("ALTER TABLE faq_embeddings FORCE ROW LEVEL SECURITY")
                conn.execute("DROP POLICY IF EXISTS tenant_isolation ON faq_embeddings")
                conn.execute(
                    "CREATE POLICY tenant_isolation ON faq_embeddings FOR ALL "
                    "USING (org_id = current_setting('app.current_org_id', true)) "
                    "WITH CHECK (org_id = current_setting('app.current_org_id', true))"
                )
            except Exception as e:
                if "must be owner" in str(e).lower():
                    # Pas d'accent/emoji ici : un print qui échoue sous cp1252 (console Windows,
                    # cf. le même bug corrigé dans hubspot.py) romprait précisément la dégradation
                    # gracieuse que ce bloc est censé fournir.
                    print(
                        "[vector_store] Role applicatif restreint (pas proprietaire de "
                        "faq_embeddings) : migration de schema/policy RLS ignoree, deja en place "
                        "cote administration."
                    )
                else:
                    raise
    return _pool


def _scope_to_tenant(conn, org_id: str) -> None:
    """
    Positionne la variable de session Postgres lue par la politique RLS `tenant_isolation`, sur LA
    connexion physique empruntée au pool — appelé en tout début de chaque `sync_embeddings()`/
    `search()`, avant toute autre requête sur cette connexion, pour qu'une connexion réutilisée
    plus tard par un tenant différent ne puisse jamais hériter du tenant précédent par oubli.
    `set_config(..., false)` = portée session (pas seulement la transaction courante), suffisant
    ici car le pool est en `autocommit=True` (pas de transaction explicite à cheval sur l'appel).
    """
    conn.execute("SELECT set_config('app.current_org_id', %s, false)", (org_id,))


def sync_embeddings(pairs: list[tuple[str, str]], embed_documents, org_id: str = None) -> None:
    """
    Remplace intégralement les embeddings du TENANT COURANT (`org_id`, défaut : tenant du process,
    cf. aca.core.tenant) dans `faq_embeddings` par les paires (question, réponse) actuellement
    visibles dans la FAQ Sheets — resynchronisation complète plutôt qu'un diff incrémental (la FAQ
    reste petite : un remplacement complet coûte un seul aller-retour Gemini, pas plus que l'ancien
    cache en mémoire qu'il remplace). `embed_documents(pairs)` est fourni par `sheets.py` (qui
    détient déjà le client Gemini) et renvoie une liste de vecteurs, un par paire. La politique RLS
    `tenant_isolation` (§12 item 3 / §14.3) borne déjà le DELETE/INSERT au tenant courant une fois
    `_scope_to_tenant()` appelé ; `org_id` est aussi écrit explicitement par ligne pour que la
    donnée reste correcte même si RLS était un jour désactivée par erreur.
    """
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    tenant = org_id or current_org_id()
    pool = _get_pool()
    vectors = embed_documents(pairs)
    with pool.connection() as conn:
        _scope_to_tenant(conn, tenant)
        register_vector(conn)
        with conn.transaction():
            conn.execute("DELETE FROM faq_embeddings")
            for (question, reponse), vector in zip(pairs, vectors):
                conn.execute(
                    "INSERT INTO faq_embeddings (question, reponse, embedding, org_id) VALUES (%s, %s, %s, %s)",
                    (question, reponse, Vector(vector), tenant),
                )


def search(query_vector, top_n: int = 3, org_id: str = None) -> list[tuple[str, str, float]]:
    """
    Renvoie les `top_n` paires (question, réponse, distance) du TENANT COURANT les plus proches de
    `query_vector` par distance cosinus pgvector (opérateur `<=>`), non filtrées par seuil de
    confiance — le seuil et la fusion avec la recherche par mots-clés (RRF) sont décidés par
    l'appelant (`sheets.py`), pas ici, pour garder une seule source de vérité sur les constantes de
    seuil (ancien défaut `max_distance=0.35`, dupliqué avec le `0.65` de similarité côté
    `sheets.py`, retiré). pgvector renvoie une DISTANCE (0 = identique), pas une similarité :
    `distance = 1 - similarité`.

    Isolation tenant (§12 item 3 / §14.3) : la politique RLS `tenant_isolation` filtre déjà par
    `org_id` une fois `_scope_to_tenant()` appelé, mais le `WHERE org_id = %s` explicite ci-dessous
    documente l'intention en clair et reste correct même si RLS était un jour désactivée par erreur
    (défense en profondeur, pas une dépendance à RLS seule).

    Sans index (`ivfflat`/`hnsw`) volontairement : à l'échelle actuelle de la FAQ (quelques
    dizaines/centaines de lignes), un scan séquentiel avec l'opérateur `<=>` est exact et déjà
    sous la milliseconde — ajouter un index approximatif maintenant n'apporterait rien et
    compliquerait la maintenance pour rien (cf. ACAM_roadmap.md §11.1 : ne pas construire pour un
    volume hypothétique). Repère pour plus tard : au-delà de quelques milliers de lignes, ajouter
    un index `hnsw`/`ivfflat` (ou `halfvec` si la dimension 3072 pose souci d'indexation).
    """
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    tenant = org_id or current_org_id()
    pool = _get_pool()
    with pool.connection() as conn:
        _scope_to_tenant(conn, tenant)
        register_vector(conn)
        rows = conn.execute(
            "SELECT question, reponse, embedding <=> %s AS distance FROM faq_embeddings "
            "WHERE org_id = %s ORDER BY distance ASC LIMIT %s",
            (Vector(query_vector), tenant, top_n),
        ).fetchall()
    return [(question, reponse, distance) for question, reponse, distance in rows]

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
"""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "")

# gemini-embedding-001's native output size (no output_dimensionality override in sheets.py) —
# confirmed empirically, not from Gemini's docs table, since the model's default can change.
EMBEDDING_DIM = 3072

_pool = None


def is_enabled() -> bool:
    return bool(DATABASE_URL)


def _get_pool():
    """Connexion (pool) paresseuse — un seul pool pour tout le process, table créée si absente."""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            conninfo=DATABASE_URL, max_size=5, kwargs={"autocommit": True},
        )
        with _pool.connection() as conn:
            from pgvector.psycopg import register_vector

            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS faq_embeddings ("
                "id SERIAL PRIMARY KEY, question TEXT NOT NULL, reponse TEXT NOT NULL, "
                f"embedding VECTOR({EMBEDDING_DIM}) NOT NULL, updated_at TIMESTAMPTZ DEFAULT now())"
            )
    return _pool


def sync_embeddings(pairs: list[tuple[str, str]], embed_documents) -> None:
    """
    Remplace intégralement la table `faq_embeddings` par les paires (question, réponse)
    actuellement visibles dans la FAQ Sheets — resynchronisation complète plutôt qu'un diff
    incrémental (la FAQ reste petite : un remplacement complet coûte un seul aller-retour Gemini,
    pas plus que l'ancien cache en mémoire qu'il remplace). `embed_documents(pairs)` est fourni par
    `sheets.py` (qui détient déjà le client Gemini) et renvoie une liste de vecteurs, un par paire.
    """
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    pool = _get_pool()
    vectors = embed_documents(pairs)
    with pool.connection() as conn:
        register_vector(conn)
        with conn.transaction():
            conn.execute("DELETE FROM faq_embeddings")
            for (question, reponse), vector in zip(pairs, vectors):
                conn.execute(
                    "INSERT INTO faq_embeddings (question, reponse, embedding) VALUES (%s, %s, %s)",
                    (question, reponse, Vector(vector)),
                )


def search(query_vector, top_n: int = 3, max_distance: float = 0.5) -> list[tuple[str, str]]:
    """
    Renvoie les `top_n` paires (question, réponse) les plus proches de `query_vector` par distance
    cosinus pgvector (opérateur `<=>`), filtrées à `max_distance`. pgvector renvoie une DISTANCE
    (0 = identique), pas une similarité — l'ancien seuil `similarité > 0.5` équivaut ici à
    `distance < 0.5` (cosine_distance = 1 - cosine_similarity).

    Sans index (`ivfflat`/`hnsw`) volontairement : à l'échelle actuelle de la FAQ (quelques
    dizaines/centaines de lignes), un scan séquentiel avec l'opérateur `<=>` est exact et déjà
    sous la milliseconde — ajouter un index approximatif maintenant n'apporterait rien et
    compliquerait la maintenance pour rien (cf. ACAM_roadmap.md §11.1 : ne pas construire pour un
    volume hypothétique). Repère pour plus tard : au-delà de quelques milliers de lignes, ajouter
    un index `hnsw`/`ivfflat` (ou `halfvec` si la dimension 3072 pose souci d'indexation).
    """
    from pgvector import Vector
    from pgvector.psycopg import register_vector

    pool = _get_pool()
    with pool.connection() as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT question, reponse, embedding <=> %s AS distance FROM faq_embeddings "
            "ORDER BY distance ASC LIMIT %s",
            (Vector(query_vector), top_n),
        ).fetchall()
    return [(question, reponse) for question, reponse, distance in rows if distance < max_distance]

import os
import re
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Charger les variables d'environnement AVANT d'importer vector_store : ce module lit
# `DATABASE_URL` au niveau module (`vector_store.DATABASE_URL = os.getenv("DATABASE_URL", "")`),
# une seule fois, à l'import — l'importer avant load_dotenv() gèle silencieusement cette valeur à
# "" pour tout le process, désactivant pgvector sans erreur ni avertissement (repli automatique
# sur le cache en mémoire, qui reste fonctionnel mais n'est plus partagé entre process — le bug
# que la migration pgvector devait justement résoudre). Bug trouvé et corrigé le 2026-07-11 : voir
# docs/PROJECT_JOURNAL.md.
load_dotenv()

from . import vector_store

# Configuration pour accéder à l'API Google Sheets et Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Onglet servant de base de connaissances (RAG "database-less").
# Conceptuellement la "Knowledge_Base" du projet ; l'onglet réel s'appelle "FAQ".
KNOWLEDGE_TAB = "FAQ"

# Onglet de mémoire long terme de l'agent Enrichissement : profils d'entreprise déjà recherchés,
# indexés par domaine, pour éviter de rappeler l'API web à chaque fois. Créé à la volée si absent.
ENRICHMENT_CACHE_TAB = "Enrichissement_Cache"

# Modèle d'embeddings pour le RAG sémantique. Groq n'expose pas d'API d'embeddings
# (uniquement chat/audio/TTS/vision) ; on utilise donc Gemini, dont le niveau gratuit
# couvre largement le volume d'une FAQ interne (10M tokens/min).
EMBEDDING_MODEL = "gemini-embedding-001"

# Cache mémoire des embeddings de la FAQ (recalculé seulement si son contenu change),
# pour éviter un appel Gemini par ligne à chaque e-mail traité.
_faq_embedding_cache = {"signature": None, "pairs": [], "vectors": []}

# --- Sécurité : neutralisation de l'injection de formule (CSV / Google Sheets injection) ---
# Une valeur issue d'un e-mail entrant (nom, besoin, brouillon, contenu web de la veille…) est du
# texte NON fiable. Si elle commence par =, +, -, @ (ou une tabulation / un retour chariot), Google
# Sheets l'interprète comme une FORMULE à l'ouverture par un humain — p.ex. `=IMPORTXML(url;...)`
# exfiltre discrètement des données de la feuille, `=HYPERLINK(...)` piège un clic. On préfixe donc
# tout déclencheur de formule d'une apostrophe, qui force Sheets à traiter la cellule comme du texte
# (l'apostrophe elle-même reste invisible à l'affichage : "-5" reste affiché "-5"). Appliqué
# UNIQUEMENT aux champs d'origine non fiable, jamais aux valeurs générées par le système (date,
# catégorie — un `Literal` de notre propre code).
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value) -> str:
    """Préfixe une apostrophe si `value` commence par un déclencheur de formule Sheets/CSV."""
    text = "" if value is None else str(value)
    if text and text[0] in _FORMULA_TRIGGERS:
        return "'" + text
    return text


def get_sheet():
    """
    Se connecte à l'API Google Sheets en utilisant le compte de service
    et retourne l'onglet 'Leads' du tableau défini dans .env.
    """
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    
    if not creds_file or not sheet_id:
        raise ValueError("Missing GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SHEETS_ID in .env")
        
    try:
        # Authentification avec le fichier JSON du compte de service
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        
        # Ouverture du tableur par ID
        spreadsheet = client.open_by_key(sheet_id)
        
        # Récupération du premier onglet (qui doit s'appeler "Leads")
        worksheet = spreadsheet.worksheet("Leads")
        return worksheet
    except Exception as e:
        print(f"❌ Erreur de connexion à Google Sheets : {e}")
        raise e


def _row_qr(row: dict):
    """
    Extrait le couple (question/sujet, réponse/information) d'une ligne, en tolérant
    la casse et les accents des en-têtes (Question/Réponse ou Sujet/Information).
    """
    q = row.get("Question", row.get("question", row.get("Sujet", row.get("sujet", ""))))
    r = row.get("Réponse", row.get("reponse", row.get("réponse",
        row.get("Information", row.get("information", "")))))
    return str(q).strip(), str(r).strip()


def _tokenize(text: str) -> set:
    """Découpe un texte en mots-clés minuscules (>=3 lettres) pour la recherche par recouvrement."""
    return {w for w in re.findall(r"\w+", str(text).lower()) if len(w) >= 3}


def _get_knowledge_worksheet():
    """
    Ouvre l'onglet Knowledge_Base (FAQ) et renvoie le worksheet gspread, ou None si la config
    est absente ou l'accès échoue. Point d'accès unique partagé par la lecture (RAG) et
    l'écriture (ingestion), pour éviter la duplication.
    """
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not creds_file or not sheet_id:
        return None
    try:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client.open_by_key(sheet_id).worksheet(KNOWLEDGE_TAB)
    except Exception as e:
        print(f"⚠️ Impossible d'ouvrir la Knowledge_Base : {e}")
        return None


def _get_knowledge_records() -> list:
    """
    Renvoie les lignes VALIDÉES de l'onglet Knowledge_Base (liste de dicts), ou [] si indisponible.
    Exclut les lignes en attente de validation humaine (Statut = "à valider", écrites par l'agent
    Veille — contenu web non vérifié) ou rejetées ("rejeté") ; les lignes sans colonne Statut
    (contenu historique / ingestion documentaire, implicitement fiable) restent visibles.
    """
    ws = _get_knowledge_worksheet()
    if not ws:
        return []
    records = ws.get_all_records()
    return [r for r in records if str(r.get("Statut", "")).strip().lower() not in ("à valider", "rejeté")]


def _keyword_candidates(query: str, records: list, limit: int = 10) -> list:
    """
    Score chaque ligne de la Knowledge_Base par recouvrement de tokens avec `query` (question ET
    réponse, pour capter les tarifs/délais évoqués côté réponse). Renvoie les `limit` meilleures
    (score, question, réponse), triées par score décroissant — factorisé hors de
    `search_knowledge_base` pour être réutilisé comme voie "éparse" (sparse) de la fusion RRF dans
    `search_knowledge_base_semantic`.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for row in records:
        q, r = _row_qr(row)
        if not q or not r:
            continue
        overlap = len(query_tokens & _tokenize(f"{q} {r}"))
        if overlap > 0:
            scored.append((overlap, q, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def _rrf_fuse(*ranked_lists, top_n: int, k: int = 60) -> list:
    """
    Reciprocal Rank Fusion : combine plusieurs listes de (question, réponse) déjà triées par
    pertinence en un seul classement, à partir du RANG de chaque paire dans chaque liste (pas de
    son score brut) — nécessaire car la similarité cosinus (échelle ~0-1) et le recouvrement de
    tokens (échelle = nombre de mots) ne sont pas comparables directement. `k=60` est la
    constante standard de la formule RRF (Cormack et al., 2009) : assez grande pour lisser l'effet
    d'un rang 1 isolé, assez petite pour que le rang continue de compter.
    """
    scores: dict = {}
    for ranked in ranked_lists:
        for rank, pair in enumerate(ranked, start=1):
            scores[pair] = scores.get(pair, 0.0) + 1.0 / (k + rank)
    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [pair for pair, _ in fused[:top_n]]


def search_knowledge_base(query: str, top_n: int = 3) -> str:
    """
    RAG "database-less" : recherche par mots-clés dans l'onglet Knowledge_Base (FAQ), formatée
    pour l'injection dans le prompt. Renvoie "" si rien ne correspond. Sert de repli quand le RAG
    sémantique (Gemini) est indisponible, et de voie "sparse" fusionnée avec la voie sémantique
    dans `search_knowledge_base_semantic` (recherche hybride).
    """
    records = _get_knowledge_records()
    if not records:
        return ""

    scored = _keyword_candidates(query, records, limit=top_n)
    if not scored:
        return ""

    return "\n".join(f"- Q: {q}\n  R: {r}" for _, q, r in scored)


def _get_genai_client():
    """Client Gemini si GOOGLE_API_KEY est configurée dans .env, sinon None."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)


def _cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embed_documents(client, pairs: list) -> list:
    """Embeddings Gemini pour une liste de paires (question, réponse) — un appel, plusieurs docs."""
    from google.genai import types

    docs = [f"title: {q} | text: {r}" for q, r in pairs]
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=docs,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return [e.values for e in result.embeddings]


def _embed_query(client, query: str) -> list:
    """Embedding Gemini pour la requête (type RETRIEVAL_QUERY, distinct de RETRIEVAL_DOCUMENT)."""
    from google.genai import types

    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return result.embeddings[0].values


# Seuils de confiance sur le score de similarité DENSE (sémantique) le plus haut du lot — calibrés
# empiriquement le 2026-07-11 sur les embeddings Gemini réels d'une FAQ de 74 paires : questions
# reformulées mais pertinentes 0.73-0.80, questions hors-sujet 0.56-0.61 (voir docs/PROJECT_JOURNAL.md).
# Ajout du 2026-07-12 : plutôt qu'une coupure unique à 0.65, une "zone ambre" entre les deux bornes —
# le contexte est quand même transmis (mieux qu'un rejet sec sur un cas limite réel), mais marqué
# comme peu fiable pour que le Stratège nuance et que l'humain le voie dans le raisonnement affiché.
_DENSE_HIGH_CONFIDENCE = 0.72
_DENSE_LOW_CONFIDENCE = 0.62

# Sentinelle préfixée au résultat quand la meilleure correspondance dense est en zone ambre.
# `connaissance_node` (aca/core/app.py) la détecte, la retire avant d'injecter le contexte dans le
# prompt du Stratège, et l'utilise pour logger un avertissement visible côté humain.
LOW_CONFIDENCE_MARKER = "[FAQ_CONFIANCE_FAIBLE]"


def search_knowledge_base_semantic(query: str, top_n: int = 3) -> str:
    """
    RAG hybride : fusionne une recherche DENSE (embeddings Gemini + similarité cosinus/pgvector —
    capte les reformulations, "tarif" ~ "prix") et une recherche SPARSE (recouvrement de mots-clés,
    `_keyword_candidates`) via Reciprocal Rank Fusion (`_rrf_fuse`). La voie sémantique seule rate
    les correspondances exactes sur du texte alphanumérique (référence produit, "99.9%" de SLA...)
    que la voie mots-clés retrouve directement ; la fusion prend le meilleur des deux plutôt que
    d'utiliser la seconde uniquement en repli d'échec. Se rabat entièrement sur
    `search_knowledge_base` si GOOGLE_API_KEY est absente ou si l'appel Gemini échoue.

    Stockage vectoriel : `vector_store.py` (pgvector/Supabase) si `DATABASE_URL` est configurée —
    partagé entre tous les process (`ui.py`/`poller.py`), sinon repli sur le cache en mémoire par
    process (`_faq_embedding_cache`) + similarité cosinus calculée ici. Les deux chemins partagent
    la même logique d'invalidation (`signature` = contenu FAQ visible actuel).
    """
    client = _get_genai_client()
    if client is None:
        return search_knowledge_base(query, top_n)

    records = _get_knowledge_records()
    if not records:
        return ""

    pairs = [pair for pair in (_row_qr(row) for row in records) if pair[0] and pair[1]]
    if not pairs:
        return ""

    signature = tuple(pairs)
    pool_n = max(top_n * 3, 10)  # bassin plus large que top_n pour laisser la fusion RRF arbitrer

    dense_ranked: list = []
    dense_top_score = 0.0

    if vector_store.is_enabled():
        try:
            if _faq_embedding_cache["signature"] != signature:
                vector_store.sync_embeddings(pairs, lambda p: _embed_documents(client, p))
                _faq_embedding_cache["signature"] = signature
            q_vector = _embed_query(client, query)
            raw = vector_store.search(q_vector, top_n=pool_n)
            dense_ranked = [(q, r) for q, r, _ in raw]
            dense_top_score = 1 - raw[0][2] if raw else 0.0
        except Exception as e:
            print(f"⚠️ Échec pgvector (FAQ), repli sur la recherche par mots-clés : {e}")
            return search_knowledge_base(query, top_n)
    else:
        if _faq_embedding_cache["signature"] != signature:
            try:
                _faq_embedding_cache["vectors"] = _embed_documents(client, pairs)
                _faq_embedding_cache["signature"] = signature
                _faq_embedding_cache["pairs"] = pairs
            except Exception as e:
                print(f"⚠️ Échec des embeddings Gemini (FAQ), repli sur la recherche par mots-clés : {e}")
                return search_knowledge_base(query, top_n)

        try:
            q_vector = _embed_query(client, query)
        except Exception as e:
            print(f"⚠️ Échec de l'embedding de la requête, repli sur la recherche par mots-clés : {e}")
            return search_knowledge_base(query, top_n)

        scored = sorted(
            (
                (_cosine_similarity(q_vector, vec), q, r)
                for (q, r), vec in zip(_faq_embedding_cache["pairs"], _faq_embedding_cache["vectors"])
            ),
            key=lambda x: x[0], reverse=True,
        )
        dense_ranked = [(q, r) for _, q, r in scored[:pool_n]]
        dense_top_score = scored[0][0] if scored else 0.0

    sparse_ranked = [(q, r) for _, q, r in _keyword_candidates(query, records, limit=pool_n)]

    # Sous le seuil bas, sans même une correspondance mot-clé pour compenser : vraiment hors sujet.
    # On renvoie "" (déclenche l'agent Veille) plutôt que de forcer un contexte non pertinent.
    if dense_top_score < _DENSE_LOW_CONFIDENCE and not sparse_ranked:
        return ""

    fused = _rrf_fuse(dense_ranked, sparse_ranked, top_n=top_n)
    if not fused:
        return ""

    body = "\n".join(f"- Q: {q}\n  R: {r}" for q, r in fused)
    low_confidence = _DENSE_LOW_CONFIDENCE <= dense_top_score < _DENSE_HIGH_CONFIDENCE
    return f"{LOW_CONFIDENCE_MARKER}\n{body}" if low_confidence else body


def write_knowledge_rows(pairs: list, mode: str = "append", statut: str = "validé") -> int:
    """
    Écrit des paires (question, réponse) dans l'onglet Knowledge_Base (point d'entrée de l'ingestion
    doc/PDF → Sheets, le "remplacement du Vector DB"), avec une colonne `Statut` :
    - statut="validé"    (défaut) : visible immédiatement du RAG — utilisé par l'ingestion documentaire
      (`ingest.py`), où le contenu est fourni par un humain et donc implicitement fiable.
    - statut="à valider" : invisible du RAG jusqu'à approbation humaine — utilisé par l'agent Veille
      (contenu web non vérifié, cf. `get_pending_knowledge_rows`/`approve_knowledge_row`).
    - mode="append"  : ajoute les lignes à la suite (crée l'en-tête si l'onglet est vide ; étend
      l'en-tête legacy à 2 colonnes vers 3 sans toucher aux données existantes).
    - mode="replace" : efface l'onglet et réécrit l'en-tête + les lignes.
    Invalide le cache d'embeddings de la FAQ (recalcul au prochain RAG sémantique). Renvoie le nombre
    de lignes écrites, ou 0 en cas d'échec.
    """
    clean = [(str(q).strip(), str(r).strip()) for q, r in pairs if str(q).strip() and str(r).strip()]
    if not clean:
        return 0

    ws = _get_knowledge_worksheet()
    if ws is None:
        return 0

    try:
        # q/r peuvent provenir de la veille web (non fiable) → échappement anti-formule ; `statut`
        # est une constante de notre code.
        rows = [[_escape_formula(q), _escape_formula(r), statut] for q, r in clean]
        if mode == "replace":
            ws.clear()
            ws.update(range_name="A1", values=[["Question", "Réponse", "Statut"], *rows])
        else:  # append
            existing = ws.get_all_values()
            if not existing:
                rows = [["Question", "Réponse", "Statut"], *rows]
            elif len(existing[0]) < 3:
                ws.update(range_name="C1", values=[["Statut"]])  # en-tête legacy 2 colonnes → 3
            ws.append_rows(rows)
    except Exception as e:
        print(f"⚠️ Échec de l'écriture dans la Knowledge_Base : {e}")
        return 0

    # Le contenu FAQ a changé → invalider le cache d'embeddings (recalcul au prochain RAG sémantique).
    _faq_embedding_cache["signature"] = None
    print(f"✅ [Knowledge_Base] {len(clean)} ligne(s) écrite(s) (mode={mode}, statut={statut}).")
    return len(clean)


def get_pending_knowledge_rows() -> list:
    """
    Lignes de la Knowledge_Base en attente de validation humaine (Statut = "à valider"), écrites
    automatiquement par l'agent Veille (contenu web non vérifié). Renvoie une liste de dicts
    {row_index, question, reponse} — `row_index` = numéro de ligne réel (1-based, en-tête compris),
    requis par `approve_knowledge_row`/`reject_knowledge_row`.
    """
    ws = _get_knowledge_worksheet()
    if ws is None:
        return []
    try:
        records = ws.get_all_records()
    except Exception as e:
        print(f"⚠️ Lecture des lignes en attente échouée : {e}")
        return []

    pending = []
    for i, row in enumerate(records, start=2):  # ligne 1 = en-tête
        if str(row.get("Statut", "")).strip().lower() == "à valider":
            q, r = _row_qr(row)
            if q and r:
                pending.append({"row_index": i, "question": q, "reponse": r})
    return pending


def _set_knowledge_status(row_index: int, statut: str) -> bool:
    """Met à jour la colonne Statut (C) d'une ligne de la Knowledge_Base par son numéro de ligne."""
    ws = _get_knowledge_worksheet()
    if ws is None:
        return False
    try:
        ws.update_cell(row_index, 3, statut)
    except Exception as e:
        print(f"⚠️ Mise à jour du statut (ligne {row_index}) échouée : {e}")
        return False
    _faq_embedding_cache["signature"] = None  # la visibilité RAG change → invalider le cache
    return True


def approve_knowledge_row(row_index: int) -> bool:
    """Valide une ligne en attente (agent Veille) : elle devient visible du RAG sémantique/mots-clés."""
    return _set_knowledge_status(row_index, "validé")


def reject_knowledge_row(row_index: int) -> bool:
    """Rejette une ligne en attente (agent Veille) : reste dans la feuille pour audit, invisible du RAG."""
    return _set_knowledge_status(row_index, "rejeté")


def _open_spreadsheet():
    """Ouvre le tableur Google et renvoie l'objet spreadsheet gspread, ou None si indisponible."""
    creds_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.getenv("GOOGLE_SHEETS_ID")
    if not creds_file or not sheet_id:
        return None
    try:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client.open_by_key(sheet_id)
    except Exception as e:
        print(f"⚠️ Impossible d'ouvrir le tableur : {e}")
        return None


def _get_cache_worksheet():
    """Onglet Enrichissement_Cache (créé avec son en-tête s'il n'existe pas), ou None si indisponible."""
    ss = _open_spreadsheet()
    if ss is None:
        return None
    try:
        return ss.worksheet(ENRICHMENT_CACHE_TAB)
    except gspread.WorksheetNotFound:
        try:
            ws = ss.add_worksheet(title=ENRICHMENT_CACHE_TAB, rows=200, cols=3)
            ws.update(range_name="A1", values=[["Domaine", "Profil", "Date"]])
            return ws
        except Exception as e:
            print(f"⚠️ Impossible de créer l'onglet {ENRICHMENT_CACHE_TAB} : {e}")
            return None
    except Exception as e:
        print(f"⚠️ Impossible d'ouvrir l'onglet {ENRICHMENT_CACHE_TAB} : {e}")
        return None


def get_cached_profile(domain: str) -> str:
    """Mémoire long terme Enrichissement : profil déjà connu pour ce domaine, ou "" sinon."""
    if not domain:
        return ""
    ws = _get_cache_worksheet()
    if ws is None:
        return ""
    try:
        target = domain.strip().lower()
        for row in ws.get_all_records():
            if str(row.get("Domaine", "")).strip().lower() == target:
                return str(row.get("Profil", "")).strip()
    except Exception as e:
        print(f"⚠️ Lecture du cache d'enrichissement échouée : {e}")
    return ""


def cache_profile(domain: str, profile: str) -> None:
    """Écrit (ou ré-écrit) un profil d'entreprise dans le cache d'enrichissement."""
    if not domain or not profile:
        return
    ws = _get_cache_worksheet()
    if ws is None:
        return
    try:
        # `profile` provient de Tavily/LLM (non fiable) → échappement anti-formule ; le domaine est
        # normalisé (minuscules) mais échappé aussi par prudence (un `-` en tête suffirait).
        ws.append_row([
            _escape_formula(domain.strip().lower()),
            _escape_formula(profile),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ])
    except Exception as e:
        print(f"⚠️ Écriture du cache d'enrichissement échouée : {e}")


def find_leads_by_sender(sender: str) -> list:
    """
    Mémoire long terme (CRM) : renvoie les lignes existantes de l'onglet 'Leads' dont
    l'expéditeur correspond (insensible à la casse). Sert à détecter un client récurrent
    et les doublons avant l'écriture. Renvoie [] en cas d'erreur ou d'absence.
    """
    if not sender:
        return []
    try:
        records = get_sheet().get_all_records()
    except Exception as e:
        print(f"⚠️ Impossible de lire l'historique des leads : {e}")
        return []

    target = sender.strip().lower()
    matches = []
    for row in records:
        existing = str(row.get("Expéditeur", row.get("Expediteur", ""))).strip().lower()
        if existing and existing == target:
            matches.append(row)
    return matches

def append_lead(email_classification: str, extracted_info: dict, sender: str, draft: str):
    """
    Ajoute une nouvelle ligne dans l'onglet Leads.
    Format des colonnes : Date | Expéditeur | Entreprise | Contact | Urgence | Besoin | Catégorie | Brouillon
    """
    worksheet = get_sheet()
    
    # Préparation des données de la nouvelle ligne
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entreprise = extracted_info.get("entreprise", "N/A")
    contact = extracted_info.get("contact", "N/A")
    urgence = extracted_info.get("urgence", "N/A")
    besoin = extracted_info.get("besoin_principal", "N/A")
    
    # Champs d'origine non fiable (expéditeur + tout ce qui vient de l'e-mail / du LLM) échappés
    # contre l'injection de formule ; Date et Catégorie sont générées par notre code (sûres).
    row_data = [
        date_str,                        # A: Date (système)
        _escape_formula(sender),         # B: Expéditeur
        _escape_formula(entreprise),     # C: Entreprise
        _escape_formula(contact),        # D: Contact
        _escape_formula(urgence),        # E: Urgence
        _escape_formula(besoin),         # F: Besoin
        email_classification,            # G: Catégorie (Literal de notre code)
        _escape_formula(draft),          # H: Brouillon
    ]
    
    print(f"\n📊 [Sheets] Ajout d'une ligne pour {sender} ({entreprise})...")
    # Insertion à la fin du tableau
    worksheet.append_row(row_data)
    print("✅ [Sheets] Ligne ajoutée avec succès !")

if __name__ == "__main__":
    # Test script direct pour vérifier la connexion seule
    print("Vérification de la connexion Google Sheets...")
    sheet = get_sheet()
    print(f"Connexion réussie ! Nom du tableur : {sheet.spreadsheet.title}, Onglet : {sheet.title}")

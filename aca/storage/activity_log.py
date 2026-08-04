"""
Journal d'activité (§17) — qui a fait quoi, quand, depuis quel poste, et avec quel résultat.

**Le manque que ce module comble.** Le projet avait déjà un journal d'audit
([audit_log.py](aca/storage/audit_log.py), §12 item 2 / §15.2.7), mais il ne consigne qu'UN seul
type d'événement : une validation de lead. Tout le reste de ce qu'un humain peut faire dans l'UI
ne laissait aucune trace :

- une connexion réussie, et surtout une **connexion échouée** (le verrou progressif de
  `auth_lockout.py` bloquait un attaquant sans que personne ne puisse jamais constater la
  tentative) ;
- un **rejet** de lead — l'action miroir de la validation, ajoutée à l'UI sans journalisation ;
- un changement de **réglages** (le lien Calendly, les adresses de routage : de quoi rediriger
  discrètement les alertes commerciales d'une entreprise vers une autre adresse) ;
- la **curation de la base de connaissances** (approuver une réponse venue du web la rend citable
  par le Stratège dans de futures propositions commerciales — une décision éditoriale) ;
- la **gestion des comptes** (créer un admin, changer un rôle, réinitialiser un mot de passe) ;
- un **refus de permission** — un opérateur qui tente d'atteindre une surface d'administration.

Autrement dit : le rôle `operator` existait (§15.1.6) mais rien ne permettait à un administrateur
de répondre à « qu'est-ce que cette personne a fait cette semaine ? ». C'est l'objet de ce module.

**« Depuis quel poste », honnêtement.** Une application web ne peut pas identifier une machine ;
prétendre le contraire dans un journal d'audit serait pire que ne rien écrire. Ce qui est
réellement enregistré, et ce que ça vaut :

- `ip_address` — vue par le serveur (`st.context.ip_address`). Derrière un reverse proxy, c'est
  celle du proxy tant que `X-Forwarded-For` n'est pas propagé (cf. docs/DEPLOYMENT_HARDENING.md).
- `user_agent` / `device_label` — déclaratifs, donc falsifiables par qui le veut ; utiles pour
  distinguer « poste bureau Windows » d'« iPhone », pas pour prouver quoi que ce soit.
- `device_id` — empreinte courte et STABLE de (IP, user-agent) : elle regroupe les actions d'un même
  poste sans stocker d'identifiant traçant. Deux collègues derrière le même NAT avec le même
  navigateur partagent la même empreinte : c'est une aide au regroupement, pas une identité.
- `server_host` — nom de la machine qui exécute ACA. En déploiement « Solo » (`run_solo.py` sur le
  portable d'un commercial), c'est justement LE poste ; en déploiement serveur, c'est le serveur.

**Inviolabilité.** Mêmes empreintes chaînées que `audit_log.py`, via le module partagé
[tamper_chain.py](aca/storage/tamper_chain.py) : tamper-evident (HMAC si `ACA_AUDIT_HMAC_KEY` est
réglée), pas tamper-proof. `verify_chain()` localise la première rupture.

**Ne lève jamais.** `log()` est appelé depuis les gestionnaires de boutons de `ui.py`, juste avant
ou juste après une écriture CRM : une exception ici ferait échouer une validation légitime pour
cause de journal indisponible. Même contrat que `webhook.emit()`. Le compromis est assumé et non
silencieux : l'échec est imprimé côté serveur et `log()` renvoie `ok=False`, pour qu'un appelant qui
tient à le savoir (l'UI affiche un avertissement) le puisse. Le cas réaliste — un verrou SQLite
entre `ui.py` et `poller.py` — est déjà absorbé par `with_sqlite_retry`.
"""
import hashlib
import json
import os
import socket
import sqlite3
from datetime import datetime, timedelta

from aca.core.tenant import current_org_id

from .sqlite_retry import with_sqlite_retry
from .tamper_chain import chain_hash, digest

DB_PATH = os.getenv("ACA_ACTIVITY_DB", "data/activity.sqlite")

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── Issues ────────────────────────────────────────────────────────────────────────────────────
OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"    # l'utilisateur n'avait pas le droit / identifiants faux
OUTCOME_FAILURE = "failure"  # action légitime, mais qui a échoué techniquement
OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_FAILURE)

# ── Surfaces d'origine ────────────────────────────────────────────────────────────────────────
SOURCE_STREAMLIT = "streamlit"
SOURCE_API = "api"
SOURCE_SLACK = "slack"
SOURCE_CLI = "cli"
SOURCE_POLLER = "poller"

# ── Actions ───────────────────────────────────────────────────────────────────────────────────
# Nommées `objet.action` comme les événements de `webhook.py`, pour qu'un filtre (UI ou n8n) puisse
# router sur le préfixe : tout `auth.*` est un événement de sécurité, tout `lead.*` un événement
# métier. Ajouter une action = une constante + une entrée dans `ACTION_LABELS`.
ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_LOCKED_OUT = "auth.locked_out"
ACTION_SESSION_EXPIRED = "auth.session_expired"
ACTION_PERMISSION_DENIED = "auth.permission_denied"

ACTION_ANALYSIS_STARTED = "analysis.started"
ACTION_CLARIFICATION_ANSWERED = "analysis.clarification_answered"
ACTION_QUEUE_OPENED = "analysis.queue_opened"

ACTION_LEAD_VALIDATED = "lead.validated"
ACTION_LEAD_REJECTED = "lead.rejected"
ACTION_DRAFT_EDITED = "lead.draft_edited"

ACTION_SETTINGS_CHANGED = "settings.changed"
ACTION_BRANDING_CHANGED = "branding.changed"
ACTION_BRANDING_RESET = "branding.reset"

ACTION_KNOWLEDGE_INGESTED = "knowledge.ingested"
ACTION_KNOWLEDGE_APPROVED = "knowledge.approved"
ACTION_KNOWLEDGE_REJECTED = "knowledge.rejected"

ACTION_USER_CREATED = "user.created"
ACTION_USER_ROLE_CHANGED = "user.role_changed"
ACTION_USER_DISABLED = "user.disabled"
ACTION_USER_ENABLED = "user.enabled"
ACTION_USER_PASSWORD_RESET = "user.password_reset"

ACTION_GMAIL_IMPORTED = "gmail.imported"
ACTION_DATA_EXPORTED = "data.exported"
ACTION_DATA_PURGED = "data.purged"

# §18 — actions machine (`poller.py`/`scheduler.py`/`relance.py`/`retention.py`), tournant sans
# acteur humain. `ACTION_ANALYSIS_STARTED` ci-dessus est réutilisée pour le poller (même geste que
# le formulaire manuel, seule la `source` change) ; ces deux-là sont propres aux travaux planifiés.
ACTION_FOLLOWUP_DRAFTED = "lead.followup_drafted"
ACTION_JOB_RAN = "scheduler.job_ran"
# §19 — tâches datées posées par un humain (envoi programmé, rappel). Tracées séparément de
# `ACTION_JOB_RAN` : celui-ci dit « le planificateur est passé », ceux-ci disent « telle personne a
# décidé qu'un message partirait à telle heure » — une décision imputable, pas un tick de boucle.
ACTION_TASK_SCHEDULED = "task.scheduled"
ACTION_TASK_CANCELLED = "task.cancelled"
ACTION_TASK_EXECUTED = "task.executed"

# Libellés français affichés dans l'onglet « Journal d'activité » — un journal qu'un manager ne sait
# pas lire n'est pas consulté, donc pas un contrôle.
ACTION_LABELS = {
    ACTION_LOGIN: "Connexion",
    ACTION_LOGIN_FAILED: "Échec de connexion",
    ACTION_LOGOUT: "Déconnexion",
    ACTION_LOCKED_OUT: "Compte verrouillé (trop d'échecs)",
    ACTION_SESSION_EXPIRED: "Session expirée",
    ACTION_PERMISSION_DENIED: "Accès refusé (permission manquante)",
    ACTION_ANALYSIS_STARTED: "Analyse lancée",
    ACTION_CLARIFICATION_ANSWERED: "Réponse à une clarification",
    ACTION_QUEUE_OPENED: "Ouverture d'une analyse en file",
    ACTION_LEAD_VALIDATED: "Lead validé (écriture CRM)",
    ACTION_LEAD_REJECTED: "Lead rejeté",
    ACTION_DRAFT_EDITED: "Proposition modifiée avant envoi",
    ACTION_SETTINGS_CHANGED: "Réglages modifiés",
    ACTION_BRANDING_CHANGED: "Identité visuelle modifiée",
    ACTION_BRANDING_RESET: "Identité visuelle réinitialisée",
    ACTION_KNOWLEDGE_INGESTED: "Document ingéré dans la base de connaissances",
    ACTION_KNOWLEDGE_APPROVED: "Réponse de veille approuvée",
    ACTION_KNOWLEDGE_REJECTED: "Réponse de veille rejetée",
    ACTION_USER_CREATED: "Compte créé",
    ACTION_USER_ROLE_CHANGED: "Rôle modifié",
    ACTION_USER_DISABLED: "Compte désactivé",
    ACTION_USER_ENABLED: "Compte réactivé",
    ACTION_USER_PASSWORD_RESET: "Mot de passe réinitialisé",
    ACTION_GMAIL_IMPORTED: "E-mail importé depuis Gmail",
    ACTION_DATA_EXPORTED: "Export de données",
    ACTION_DATA_PURGED: "Données purgées (rétention ou effacement RGPD)",
    ACTION_FOLLOWUP_DRAFTED: "Relance automatique créée (brouillon)",
    ACTION_JOB_RAN: "Travail planifié exécuté",
    ACTION_TASK_SCHEDULED: "Tâche programmée (envoi ou rappel)",
    ACTION_TASK_CANCELLED: "Tâche programmée annulée",
    ACTION_TASK_EXECUTED: "Tâche programmée exécutée",
}

# Actions qu'un administrateur doit voir en priorité : elles changent qui peut faire quoi, où
# partent les données, ou ce que l'IA affirmera aux prospects. Sert au filtre « Sensibles seulement »
# et à la mise en évidence dans l'UI — jamais à un contrôle d'accès.
SENSITIVE_ACTIONS = frozenset({
    ACTION_LOGIN_FAILED, ACTION_LOCKED_OUT, ACTION_PERMISSION_DENIED,
    ACTION_SETTINGS_CHANGED, ACTION_BRANDING_CHANGED, ACTION_USER_CREATED,
    ACTION_USER_ROLE_CHANGED, ACTION_USER_DISABLED, ACTION_USER_ENABLED,
    ACTION_USER_PASSWORD_RESET, ACTION_KNOWLEDGE_INGESTED, ACTION_KNOWLEDGE_APPROVED,
    ACTION_DATA_EXPORTED, ACTION_DATA_PURGED,
    # §19 : programmer un envoi, c'est décider qu'un message partira vers un prospect en l'absence
    # de son auteur. Cette trace-là doit survivre à la purge courante, au même titre qu'un
    # changement de rôle — d'où sa présence ici plutôt que dans le bruit d'usage quotidien.
    ACTION_TASK_SCHEDULED, ACTION_TASK_CANCELLED,
})

# Ordre des champs entrant dans l'empreinte chaînée. C'EST le contrat d'intégrité de cette table :
# le modifier invaliderait toutes les lignes déjà écrites, qui apparaîtraient comme falsifiées.
_CHAINED_FIELDS = (
    "occurred_at", "actor", "actor_role", "action", "target_type", "target_id", "summary",
    "details", "outcome", "source", "session_id", "ip_address", "device_id", "device_label",
    "user_agent", "server_host", "org_id",
)


# ── Description du poste (fonctions pures, testables hors ligne) ──────────────────────────────
# Familles reconnues dans le user-agent, du plus spécifique au plus générique : « Edg » contient
# « Chrome », « Chrome » contient « Safari ». L'ordre de ces tuples est donc significatif.
_BROWSERS = (
    ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_PLATFORMS = (
    ("Windows NT 10.0", "Windows 10/11"), ("Windows", "Windows"), ("iPhone", "iPhone"),
    ("iPad", "iPad"), ("Android", "Android"), ("Mac OS X", "macOS"), ("Macintosh", "macOS"),
    ("CrOS", "ChromeOS"), ("Linux", "Linux"),
)


def describe_user_agent(user_agent: str) -> str:
    """
    Résume un user-agent en « Plateforme · Navigateur » (ex. `Windows 10/11 · Chrome`).

    Volontairement grossier : l'objectif est qu'un administrateur reconnaisse d'un coup d'œil
    « ce n'est pas le poste habituel de cette personne », pas de faire du fingerprinting. Un
    user-agent absent ou inconnu renvoie `"Poste inconnu"` plutôt qu'une chaîne vide, pour que la
    colonne reste lisible dans le tableau.
    """
    if not user_agent:
        return "Poste inconnu"
    platform = next((label for token, label in _PLATFORMS if token in user_agent), None)
    browser = next((label for token, label in _BROWSERS if token in user_agent), None)
    if platform and browser:
        return f"{platform} · {browser}"
    return platform or browser or "Poste inconnu"


def device_fingerprint(ip_address: str, user_agent: str) -> str:
    """
    Empreinte courte et stable du couple (IP, user-agent), préfixée `d-`.

    Sert à REGROUPER les actions d'un même poste dans le journal sans stocker d'identifiant
    persistant chez l'utilisateur (pas de cookie de traçage, rien de plus à déclarer au RGPD que
    l'IP déjà consignée). Tronquée à 12 caractères hexadécimaux : suffisant pour distinguer les
    quelques postes d'une équipe commerciale, trop court pour servir d'identifiant global.
    """
    raw = f"{ip_address or ''}|{user_agent or ''}"
    return "d-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _server_host() -> str:
    """Nom de la machine qui exécute ACA. Ne lève jamais : un hostname indisponible n'est pas une
    raison de perdre une ligne de journal."""
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001
        return ""


# Un user-agent réel dépasse rarement 200 caractères ; le plafond borne ce qu'un client hostile peut
# faire écrire dans le journal (même principe que les bornes de charge utile de l'API, §15.1.4 : ne
# jamais laisser un tiers décider de la taille de ce qu'on stocke).
MAX_USER_AGENT_CHARS = 400


def normalise_ip(value) -> str:
    """
    Renvoie `value` si c'est une adresse IP valide, `""` sinon.

    Deux raisons, l'une de qualité de données et l'autre de sécurité. (1) Une colonne « Adresse IP »
    d'un journal d'audit qui contient parfois du texte quelconque n'est plus exploitable : on ne sait
    plus si une valeur étrange est un incident ou un artefact. (2) Derrière un reverse proxy, cette
    valeur provient d'un en-tête `X-Forwarded-For` — donc du client, donc falsifiable : la valider
    empêche d'y écrire une chaîne arbitraire (cf. docs/DEPLOYMENT_HARDENING.md). Perdre une IP
    illisible est sans conséquence ; conserver une IP inventée en donnerait une.
    """
    import ipaddress

    text = _text(value).strip()
    if not text:
        return ""
    # Un `X-Forwarded-For` porte une liste « client, proxy1, proxy2 » : seule la première entrée
    # décrit le client.
    text = text.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def build_context(ip_address: str = None, user_agent: str = None, session_id: str = None,
                  server_host: str = None) -> dict:
    """
    Assemble le contexte « d'où vient cette action » attendu par `log(context=…)`.

    Fonction pure et sans dépendance Streamlit (même posture que `auth_lockout.py`/`session.py`) :
    c'est `ui.py` qui lit `st.context.ip_address`/`st.context.headers` et les passe ici, ce qui
    permet de tester la journalisation sans navigateur ni serveur.
    """
    # Tout est ramené en texte dès l'entrée : `describe_user_agent`/`device_fingerprint` font des
    # `in` et des concaténations, qui échoueraient bruyamment sur autre chose qu'une chaîne.
    ip_address = normalise_ip(ip_address)
    user_agent = _text(user_agent)[:MAX_USER_AGENT_CHARS]
    return {
        "ip_address": ip_address,
        "user_agent": user_agent,
        "device_label": describe_user_agent(user_agent),
        "device_id": device_fingerprint(ip_address, user_agent),
        "session_id": _text(session_id),
        "server_host": _text(server_host) or _server_host(),
    }


# ── Écriture ──────────────────────────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS activity ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL DEFAULT 'default', "
        "occurred_at TEXT NOT NULL, actor TEXT, actor_role TEXT, action TEXT NOT NULL, "
        "target_type TEXT, target_id TEXT, summary TEXT, details TEXT, "
        "outcome TEXT NOT NULL DEFAULT 'success', source TEXT NOT NULL DEFAULT 'streamlit', "
        "session_id TEXT, ip_address TEXT, device_id TEXT, device_label TEXT, user_agent TEXT, "
        "server_host TEXT, prev_hash TEXT, row_hash TEXT)"
    )
    # Index sur les trois axes de lecture réels de l'onglet « Journal d'activité » : la frise
    # chronologique, la fiche d'un opérateur, et le filtre par type d'action. Sans eux, chaque
    # ouverture de l'onglet balaie toute la table — acceptable au volume actuel, plus du tout après
    # quelques mois d'usage quotidien à plusieurs opérateurs.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_time ON activity (org_id, occurred_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity (org_id, actor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_action ON activity (org_id, action)")
    conn.commit()
    return conn


@with_sqlite_retry
def init_db() -> None:
    """Crée la table et ses index si nécessaire."""
    _connect().close()


@with_sqlite_retry
def _insert(row: dict) -> int:
    """Insère une ligne déjà normalisée, chaînée à la précédente du même tenant."""
    with _connect() as conn:
        previous = conn.execute(
            "SELECT row_hash FROM activity WHERE org_id = ? AND row_hash IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (row["org_id"],),
        ).fetchone()
        prev_hash = previous[0] if previous else ""
        row_hash = chain_hash(prev_hash, [row[field] for field in _CHAINED_FIELDS])
        cursor = conn.execute(
            "INSERT INTO activity (org_id, occurred_at, actor, actor_role, action, target_type, "
            "target_id, summary, details, outcome, source, session_id, ip_address, device_id, "
            "device_label, user_agent, server_host, prev_hash, row_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["org_id"], row["occurred_at"], row["actor"], row["actor_role"], row["action"],
             row["target_type"], row["target_id"], row["summary"], row["details"], row["outcome"],
             row["source"], row["session_id"], row["ip_address"], row["device_id"],
             row["device_label"], row["user_agent"], row["server_host"], prev_hash, row_hash),
        )
        conn.commit()
        return cursor.lastrowid


def _text(value) -> str:
    """
    Normalise n'importe quelle valeur en texte pour l'écriture SQLite.

    Défense au niveau du magasin, et pas seulement chez l'appelant, parce que c'est ici qu'un
    manquement se paie : SQLite refuse de lier un type qu'il ne connaît pas, `log()` attrape
    l'exception (contrat « ne lève jamais ») et la ligne d'audit disparaît **en silence**. Un
    journal de sécurité qui perd des entrées sans le dire est plus dangereux qu'un journal absent,
    puisqu'on le croit complet.

    Constaté pour de vrai : `_client_context()` de `ui.py` lit `st.context.ip_address`, dont rien ne
    garantit qu'il s'agisse d'une chaîne (objet fantôme en exécution headless, évolution possible de
    Streamlit). Le premier essai de bout en bout a ainsi perdu l'entrée « analyse lancée » avec un
    `Error binding parameter 13` réduit à une ligne de log. Les appelants sont nombreux et le
    resteront : la garantie appartient à ce module.
    """
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _serialise_details(details: dict) -> str:
    """
    Sérialise `details` de façon déterministe (`sort_keys=True`), en dernier recours via `str()`.

    `default=str` plutôt qu'un abandon : un appelant qui passe par mégarde un objet non JSON (un
    `datetime`, un `Path`, une exception) obtient une trace un peu moins propre mais **conserve la
    ligne de journal**. Perdre entièrement l'entrée « rôle modifié » parce qu'un champ accessoire
    n'était pas sérialisable serait le pire des deux mondes pour un journal d'audit.
    """
    if not details:
        return ""
    return json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)


def log(action: str, actor: str = None, actor_role: str = None, *, target_type: str = None,
        target_id: str = None, summary: str = None, details: dict = None,
        outcome: str = OUTCOME_SUCCESS, source: str = SOURCE_STREAMLIT, context: dict = None,
        org_id: str = None) -> dict:
    """
    Consigne une action. Renvoie `{"ok": bool, "id": int|None, "error": str|None}` — **ne lève
    jamais** (cf. docstring du module : ce code s'intercale dans le chemin d'une écriture CRM).

    `details` est sérialisé avec `sort_keys=True` : sans ça, deux exécutions produisant le même
    dictionnaire écriraient un JSON d'ordre différent, donc une empreinte différente, et une
    revérification parfaitement honnête pourrait ressembler à une falsification.
    """
    ctx = context or {}
    try:
        # La sérialisation est DANS le try, et non dans la construction de la ligne au-dessus : un
        # `details` non sérialisable (objet quelconque passé par un appelant) lèverait sinon avant
        # même d'entrer dans la protection, ce qui vidait de son sens le contrat « ne lève jamais ».
        # Bug trouvé par `test_log_ne_leve_pas_sur_des_details_non_serialisables`.
        row = {
            "org_id": _text(org_id or current_org_id()),
            "occurred_at": datetime.now().strftime(TIMESTAMP_FORMAT),
            "actor": _text(actor) or "(anonyme)",
            "actor_role": _text(actor_role),
            "action": _text(action),
            "target_type": _text(target_type),
            "target_id": _text(target_id),
            "summary": _text(summary) or ACTION_LABELS.get(action, _text(action)),
            "details": _serialise_details(details),
            "outcome": outcome if outcome in OUTCOMES else OUTCOME_SUCCESS,
            "source": _text(source),
            "session_id": _text(ctx.get("session_id")),
            "ip_address": _text(ctx.get("ip_address")),
            "device_id": _text(ctx.get("device_id")),
            "device_label": _text(ctx.get("device_label")),
            "user_agent": _text(ctx.get("user_agent")),
            "server_host": _text(ctx.get("server_host")),
        }
        return {"ok": True, "id": _insert(row), "error": None}
    except Exception as exc:  # noqa: BLE001 — voir docstring du module : jamais bloquant
        print(f"[ACA] Journal d'activite indisponible ({exc.__class__.__name__}: {exc}) - "
              f"action '{action}' non consignee.")
        return {"ok": False, "id": None, "error": str(exc)}


# ── Lecture ───────────────────────────────────────────────────────────────────────────────────
_COLUMNS = (
    "id", "occurred_at", "actor", "actor_role", "action", "target_type", "target_id", "summary",
    "details", "outcome", "source", "session_id", "ip_address", "device_id", "device_label",
    "user_agent", "server_host",
)


def _row_to_dict(row) -> dict:
    entry = dict(zip(_COLUMNS, row))
    entry["action_label"] = ACTION_LABELS.get(entry["action"], entry["action"])
    entry["sensitive"] = entry["action"] in SENSITIVE_ACTIONS
    return entry


def _since(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime(TIMESTAMP_FORMAT)


@with_sqlite_retry
def list_recent(limit: int = 100, *, actor: str = None, action: str = None, outcome: str = None,
                days: int = None, sensitive_only: bool = False, org_id: str = None) -> list:
    """
    Événements du tenant courant, les plus récents d'abord, filtrés côté SQL.

    Le filtrage textuel libre est laissé à l'appelant (`ui.py` filtre en mémoire sur la page
    affichée, comme le fait déjà l'onglet « Historique ») : les filtres ci-dessous sont ceux qui
    réduisent réellement le volume lu, pas ceux qui affinent l'affichage.
    """
    clauses = ["org_id = ?"]
    params = [org_id or current_org_id()]
    if actor:
        clauses.append("actor = ?")
        params.append(actor)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome)
    if days:
        clauses.append("occurred_at >= ?")
        params.append(_since(days))
    if sensitive_only:
        clauses.append(f"action IN ({','.join('?' * len(SENSITIVE_ACTIONS))})")
        params.extend(sorted(SENSITIVE_ACTIONS))
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM activity WHERE {' AND '.join(clauses)} "
            f"ORDER BY id DESC LIMIT ?", params,
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


@with_sqlite_retry
def distinct_values(column: str, days: int = 90, org_id: str = None) -> list:
    """Valeurs distinctes d'une colonne (pour peupler les listes déroulantes de filtres)."""
    if column not in {"actor", "action", "outcome", "source", "device_label", "device_id"}:
        raise ValueError(f"Colonne non filtrable : {column!r}.")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM activity WHERE org_id = ? AND occurred_at >= ? "
            f"AND {column} != '' ORDER BY {column}",
            (org_id or current_org_id(), _since(days)),
        ).fetchall()
    return [row[0] for row in rows]


@with_sqlite_retry
def actors_summary(days: int = 30, org_id: str = None) -> list:
    """
    Une ligne par personne : volume d'actions, validations, rejets, incidents (refus/échecs),
    postes distincts, première et dernière activité.

    C'est la vue « qui fait quoi » qu'un administrateur ouvre en premier ; les fiches détaillées
    (`actor_profile`) viennent ensuite.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT actor, MAX(actor_role), COUNT(*), "
            "SUM(CASE WHEN action = ? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN action = ? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN outcome != ? THEN 1 ELSE 0 END), "
            "COUNT(DISTINCT device_id), MIN(occurred_at), MAX(occurred_at) "
            "FROM activity WHERE org_id = ? AND occurred_at >= ? GROUP BY actor "
            "ORDER BY COUNT(*) DESC",
            (ACTION_LEAD_VALIDATED, ACTION_LEAD_REJECTED, OUTCOME_SUCCESS,
             org_id or current_org_id(), _since(days)),
        ).fetchall()
    return [
        {"actor": r[0], "role": r[1] or "—", "actions": r[2], "validations": r[3] or 0,
         "rejets": r[4] or 0, "incidents": r[5] or 0, "postes": r[6],
         "première_activité": r[7], "dernière_activité": r[8]}
        for r in rows
    ]


@with_sqlite_retry
def actor_profile(actor: str, days: int = 30, org_id: str = None) -> dict:
    """
    Fiche d'audit d'une personne : totaux, répartition par action, et postes utilisés (avec le
    nombre d'actions et la dernière vue par poste).

    Les postes sont la partie qui répond littéralement à « depuis quelle machine » : un opérateur
    qui valide habituellement depuis un seul `device_id` et qui en fait apparaître un second, une
    nuit, depuis une autre IP, est exactement ce qu'un administrateur doit pouvoir repérer.
    """
    org = org_id or current_org_id()
    since = _since(days)
    with _connect() as conn:
        totals = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN outcome != ? THEN 1 ELSE 0 END), "
            "MIN(occurred_at), MAX(occurred_at) "
            "FROM activity WHERE org_id = ? AND actor = ? AND occurred_at >= ?",
            (OUTCOME_SUCCESS, org, actor, since),
        ).fetchone()
        by_action = conn.execute(
            "SELECT action, COUNT(*) FROM activity WHERE org_id = ? AND actor = ? "
            "AND occurred_at >= ? GROUP BY action ORDER BY COUNT(*) DESC",
            (org, actor, since),
        ).fetchall()
        devices = conn.execute(
            "SELECT device_id, MAX(device_label), MAX(ip_address), COUNT(*), MAX(occurred_at) "
            "FROM activity WHERE org_id = ? AND actor = ? AND occurred_at >= ? "
            "GROUP BY device_id ORDER BY COUNT(*) DESC",
            (org, actor, since),
        ).fetchall()
    return {
        "actor": actor,
        "actions": totals[0] or 0,
        "incidents": totals[1] or 0,
        "première_activité": totals[2],
        "dernière_activité": totals[3],
        "par_action": [
            {"action": a, "label": ACTION_LABELS.get(a, a), "compte": n} for a, n in by_action
        ],
        "postes": [
            {"device_id": d[0], "poste": d[1] or "Poste inconnu", "ip": d[2] or "—",
             "actions": d[3], "dernière_activité": d[4]}
            for d in devices
        ],
    }


@with_sqlite_retry
def lead_timeline(thread_id: str, org_id: str = None) -> list:
    """
    Histoire complète d'un lead, du plus ancien au plus récent (§18).

    Le journal contenait déjà tout le nécessaire : `target_id` porte le `thread_id`. Il manquait
    seulement la vue qui remet les lignes en ordre — or « raconte-moi ce qui est arrivé à ce
    prospect » est la question qu'on pose réellement, pas « liste-moi les entrées de la table ».
    Ordre chronologique **croissant** ici, à l'inverse de `list_recent()` : on lit une histoire du
    début, on consulte un journal par la fin.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM activity WHERE org_id = ? AND target_id = ? "
            f"ORDER BY id", (org_id or current_org_id(), thread_id),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


@with_sqlite_retry
def known_devices(actor: str, before_id: int = None, org_id: str = None) -> set:
    """
    Empreintes de poste déjà vues pour cette personne, éventuellement avant une ligne donnée.

    `before_id` sert à répondre à « ce poste était-il connu *au moment* de cette action ? » — sans
    lui, l'empreinte de l'action qu'on examine ferait elle-même partie des postes « connus » et
    aucune première apparition ne serait jamais détectée.
    """
    clauses = ["org_id = ?", "actor = ?", "device_id != ''"]
    params = [org_id or current_org_id(), actor]
    if before_id is not None:
        clauses.append("id < ?")
        params.append(before_id)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT device_id FROM activity WHERE {' AND '.join(clauses)}", params,
        ).fetchall()
    return {row[0] for row in rows}


def is_new_device(actor: str, device_id: str, before_id: int = None, org_id: str = None) -> bool:
    """
    Ce poste est-il inédit pour cette personne ?

    Base de la détection de comportement inhabituel recommandée après le §17 : une validation depuis
    un poste jamais vu est exactement ce qu'un administrateur doit remarquer. C'est une comparaison
    d'ensembles, pas de l'apprentissage automatique — et volontairement : un modèle qui « apprend »
    les habitudes de trois commerciaux produirait surtout des faux positifs inexplicables.

    Une empreinte vide (exécution sans navigateur, appel API) n'est jamais « nouvelle » : la signaler
    remplirait l'écran d'alertes pour des actions machine parfaitement normales.
    """
    if not device_id:
        return False
    seen = known_devices(actor, before_id=before_id, org_id=org_id)
    return device_id not in seen


@with_sqlite_retry
def verify_chain(org_id: str = None) -> dict:
    """
    Recalcule la chaîne d'empreintes et signale la PREMIÈRE rupture (§15.2.7, même contrat que
    `audit_log.verify_chain`).

    Deux contrôles distincts, pour la même raison qu'ailleurs : l'empreinte doit correspondre au
    contenu de SA ligne, et le `prev_hash` déclaré doit correspondre à la ligne réellement
    précédente — sans le second, supprimer une ligne du milieu passerait inaperçu (chaque ligne
    survivante resterait individuellement cohérente).
    """
    org = org_id or current_org_id()
    # `_CHAINED_FIELDS` se termine par `org_id`, qui vaut `org` pour toutes les lignes lues : on ne
    # le relit donc pas par ligne, on le rajoute en fin de liste avant le recalcul.
    per_row_fields = _CHAINED_FIELDS[:-1]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, {', '.join(per_row_fields)}, prev_hash, row_hash FROM activity "
            f"WHERE org_id = ? ORDER BY id", (org,),
        ).fetchall()

    checked = 0
    expected_prev = ""
    for row in rows:
        row_id, prev_hash, row_hash = row[0], row[-2], row[-1]
        chained_values = list(row[1:-2]) + [org]
        expected = chain_hash(prev_hash or "", chained_values)
        if row_hash != expected or (prev_hash or "") != expected_prev:
            return {
                "ok": False, "checked": checked, "first_invalid_id": row_id,
                "detail": f"Rupture de chaîne à la ligne {row_id} : contenu modifié, ligne "
                          "supprimée avant celle-ci, ou empreintes recalculées sans la clé HMAC.",
            }
        expected_prev = row_hash
        checked += 1

    return {
        "ok": True, "checked": checked, "first_invalid_id": None,
        "detail": f"{checked} ligne(s) vérifiée(s), chaîne intacte.",
    }


@with_sqlite_retry
def purge_older_than(days: int, sensitive_days: int = None, org_id: str = None) -> int:
    """
    Supprime les entrées antérieures à `days` jours et renvoie le nombre de lignes effacées.

    **Rétention à deux vitesses** (§18) : `sensitive_days`, s'il est fourni, s'applique aux actions
    de `SENSITIVE_ACTIONS` — échecs de connexion, verrouillages, changements de rôle, modifications
    de réglages. Avant, tout partait à la même échéance, ce qui obligeait à choisir entre garder six
    mois de bruit d'usage courant ou jeter au bout de trois mois la seule trace d'une tentative
    d'intrusion. Les deux besoins sont distincts : le premier documente l'activité, le second sert à
    enquêter — et une enquête commence toujours après coup.

    Prévu pour `retention.py` : ce journal contient des adresses IP, donc des données personnelles au
    sens du RGPD, et ne peut pas croître indéfiniment « au cas où ». Purger CASSE volontairement la
    chaîne d'empreintes au point de coupe — c'est une suppression légitime, pas une falsification ;
    `verify_chain()` repartira de la première ligne restante, dont le `prev_hash` ne correspondra
    plus à rien. Écrit ici pour qu'une alerte d'intégrité juste après une purge ne soit pas prise
    pour un incident.
    """
    org = org_id or current_org_id()
    sensitive = sorted(SENSITIVE_ACTIONS)
    placeholders = ",".join("?" * len(sensitive))
    with _connect() as conn:
        if sensitive_days is None:
            cursor = conn.execute(
                "DELETE FROM activity WHERE org_id = ? AND occurred_at < ?", (org, _since(days)),
            )
            deleted = cursor.rowcount
        else:
            # Deux suppressions distinctes plutôt qu'un CASE : le SQL reste lisible, et surtout on
            # ne risque pas qu'une erreur de parenthésage applique la durée courte aux événements
            # sensibles — l'inverse exact de l'intention.
            ordinary = conn.execute(
                f"DELETE FROM activity WHERE org_id = ? AND occurred_at < ? "
                f"AND action NOT IN ({placeholders})",
                [org, _since(days)] + sensitive,
            ).rowcount
            flagged = conn.execute(
                f"DELETE FROM activity WHERE org_id = ? AND occurred_at < ? "
                f"AND action IN ({placeholders})",
                [org, _since(sensitive_days)] + sensitive,
            ).rowcount
            deleted = ordinary + flagged
        conn.commit()
        return deleted


# ── Export et archivage (§18) ─────────────────────────────────────────────────────────────────
EXPORT_COLUMNS = (
    "occurred_at", "actor", "actor_role", "action", "outcome", "source", "target_type",
    "target_id", "summary", "details", "device_label", "ip_address", "server_host", "session_id",
)


def csv_export(rows) -> str:
    """
    Sérialise des entrées de journal en CSV.

    `utf-8-sig` est laissé au point d'écriture, pas imposé ici : cette fonction rend du texte, et
    c'est l'appelant (téléchargement navigateur ou fichier d'archive) qui décide de l'encodage.
    Excel francophone a besoin du BOM, un `grep` s'en passe très bien.
    """
    import csv
    import io as _io

    buffer = _io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(EXPORT_COLUMNS), extrasaction="ignore", lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in EXPORT_COLUMNS})
    return buffer.getvalue()


@with_sqlite_retry
def rows_for_period(year: int, month: int, org_id: str = None) -> list:
    """Toutes les entrées d'un mois civil, dans l'ordre chronologique (pour l'archivage)."""
    start = f"{year:04d}-{month:02d}-01 00:00:00"
    end = f"{year + (month == 12):04d}-{(month % 12) + 1:02d}-01 00:00:00"
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM activity WHERE org_id = ? "
            f"AND occurred_at >= ? AND occurred_at < ? ORDER BY id",
            (org_id or current_org_id(), start, end),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def archive_period(directory: str, year: int, month: int, org_id: str = None) -> dict:
    """
    Écrit l'archive CSV d'un mois **et son empreinte**, puis renvoie `{path, digest, lines, skipped}`.

    **Le problème résolu.** Rien n'empêchait la purge RGPD d'effacer précisément la période qu'un
    auditeur demandera : les guides SOC 2 rappellent qu'un auditeur réclame des échantillons à des
    dates imposées (« semaine 28 ») et qu'une absence de preuve devient une exception au rapport. Une
    archive mensuelle déposée hors de la base survit à la purge et répond à cette demande.

    L'empreinte (`.sha256`, HMAC si `ACA_AUDIT_HMAC_KEY` est réglée — le même mécanisme partagé que
    la chaîne du journal) rend l'archive vérifiable : sans elle, un CSV posé dans un dossier est un
    fichier modifiable comme un autre, donc sans valeur probante.

    Idempotent : une archive déjà présente n'est pas réécrite (`skipped=True`). Réexécuter le travail
    planifié ne doit pas pouvoir remplacer une archive existante par une version amputée après une
    purge — ce serait détruire la preuve qu'on prétend conserver.
    """
    os.makedirs(directory, exist_ok=True)
    base = os.path.join(directory, f"activite-{year:04d}-{month:02d}")
    csv_path, digest_path = base + ".csv", base + ".csv.sha256"
    if os.path.exists(csv_path):
        with open(digest_path, "r", encoding="utf-8") as handle:
            existing = handle.read().split()[0] if os.path.exists(digest_path) else ""
        return {"path": csv_path, "digest": existing, "lines": None, "skipped": True}

    rows = rows_for_period(year, month, org_id)
    payload = csv_export(rows)
    fingerprint = digest(payload)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        handle.write(payload)
    with open(digest_path, "w", encoding="utf-8") as handle:
        handle.write(f"{fingerprint}  {os.path.basename(csv_path)}\n")
    return {"path": csv_path, "digest": fingerprint, "lines": len(rows), "skipped": False}


def _main() -> None:
    """`python -m aca.storage.activity_log` — vérifie l'intégrité du journal du tenant courant."""
    result = verify_chain()
    print(result["detail"])
    if not os.getenv("ACA_AUDIT_HMAC_KEY"):
        print("Note : ACA_AUDIT_HMAC_KEY non definie - chainage SHA-256 simple, recalculable par "
              "qui peut ecrire dans la base (cf. docstring du module).")
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()

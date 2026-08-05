"""
Demandes de relecture (§20) : un opérateur signale à un administrateur des e-mails précis qu'il
veut faire revoir avant d'engager quoi que ce soit.

**Le manque comblé.** Jusqu'ici, un opérateur devant un lead qui le dépassait — une clause
contractuelle inhabituelle, un prix qu'il ne se sent pas d'arrêter seul, un prospect stratégique —
n'avait aucune façon de le dire *dans l'outil*. Il pouvait valider (donc écrire au CRM), rejeter
(donc faire disparaître le lead de la file de tout le monde), ou ne rien faire — et prévenir son
responsable par un autre canal, où l'information sort du produit et cesse d'être traçable. Le
troisième geste manquait : « je ne tranche pas, quelqu'un doit regarder ». C'est exactement ce que
ce module enregistre.

**Pourquoi un registre distinct de `task_store.py`**, qui porte déjà des « tâches posées par un
humain sur un lead » — la distinction est la raison d'être du module, comme elle l'était pour
`task_store` face à `followup_store`/`schedule_store` :

- Une tâche de `task_store` est **datée** : c'est une échéance que le planificateur exécute
  (`list_due()` interroge une horloge). Une demande de relecture n'a pas d'échéance choisie ; elle
  est **adressée**, et c'est la connexion du destinataire qui la fait apparaître, pas le temps qui
  passe. La requête n'est pas « qu'est-ce qui est échu » mais « qu'est-ce qui m'attend ».
- Une tâche se termine toute seule (l'envoi part, le rappel est poussé). Une demande de relecture se
  termine par une **décision humaine du destinataire**, qui peut y joindre une réponse
  (`resolution_note`) — un cycle de vie que `status` seul ne portait pas dans l'autre table.

Les fusionner obligerait `list_due()` à exclure ce type, le planificateur à le sauter, la purge à le
traiter à part : trois exceptions au cœur d'une table dont la docstring vante justement l'uniformité.

**Un lot, pas un envoi à l'unité.** Une demande porte sur *un* e-mail, mais la personne en désigne
généralement plusieurs d'un coup (« ces quatre-là, regarde-les avant demain »). Chaque e-mail a donc
sa ligne — il se traite, se commente et se clôt individuellement — mais toutes les lignes d'un même
geste partagent un `batch_id`, une note et une priorité. C'est ce qui permet de les présenter
groupées au destinataire (« Marie vous a transmis 4 e-mails ») sans perdre la granularité au moment
d'y répondre.

**Destinataire résolu à la lecture, pas à l'écriture.** `RECIPIENT_ADMINS` désigne « les
administrateurs » en tant que rôle, et non la liste des administrateurs existant à l'instant de
l'envoi : un administrateur recruté demain doit voir ce qui a été adressé à sa fonction hier. Écrire
une ligne par administrateur au moment de l'envoi aurait figé cette liste, et multiplié les lignes à
clore pour une seule demande.

Même forme que les autres registres locaux : SQLite, scopé par tenant (`org_id`), et enveloppé par
`with_sqlite_retry` puisque ces écritures ont lieu **hors du graphe**, donc hors du `RETRY_POLICY`
de LangGraph.
"""
import os
import sqlite3
import uuid
from datetime import datetime

from aca.core.tenant import current_org_id
from .sqlite_retry import with_sqlite_retry

DB_PATH = os.getenv("ACA_REVIEW_DB", "data/reviews.sqlite")

# Destinataire « la fonction, pas la personne » — cf. docstring. Le préfixe `@` ne peut pas entrer en
# collision avec un identifiant de compte (l'UI ne propose que des comptes existants ou cette
# valeur), et se lit tout seul dans la base pour qui l'inspecte à la main.
RECIPIENT_ADMINS = "@admins"

STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_DISMISSED = "dismissed"

PRIORITY_NORMAL = "normale"
PRIORITY_HIGH = "haute"
PRIORITIES = (PRIORITY_NORMAL, PRIORITY_HIGH)

STATUS_LABELS = {
    STATUS_PENDING: "À relire",
    STATUS_RESOLVED: "Traitée",
    STATUS_DISMISSED: "Écartée",
}

_TS = "%Y-%m-%d %H:%M:%S"

_FIELDS = ("id", "batch_id", "thread_id", "subject", "sender", "classification", "requester",
           "recipient", "note", "priority", "created_at", "status", "seen_at", "resolved_at",
           "resolved_by", "resolution_note")
_SELECT = f"SELECT {', '.join(_FIELDS)} FROM review_requests"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_requests ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, batch_id TEXT NOT NULL, "
        "thread_id TEXT NOT NULL, subject TEXT, sender TEXT, classification TEXT, "
        "requester TEXT NOT NULL, recipient TEXT NOT NULL, note TEXT, priority TEXT NOT NULL, "
        "created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', seen_at TEXT, "
        "resolved_at TEXT, resolved_by TEXT, resolution_note TEXT)"
    )
    # La requête chaude est « qu'est-ce qui m'attend » : elle tourne à chaque rerun Streamlit du
    # destinataire (donc à chaque frappe dans un champ), c'est la seule dont la fréquence justifie
    # un index.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_inbox "
        "ON review_requests (org_id, recipient, status)"
    )
    return conn


def _row(record) -> dict:
    request = dict(zip(_FIELDS, record))
    request["status_label"] = STATUS_LABELS.get(request["status"], request["status"])
    return request


@with_sqlite_retry
def init_db() -> None:
    _connect().close()


@with_sqlite_retry
def create_batch(leads, *, requester: str, recipient: str = RECIPIENT_ADMINS, note: str = "",
                 priority: str = PRIORITY_NORMAL, org_id: str = None) -> dict:
    """
    Enregistre en **un seul geste** une demande de relecture portant sur plusieurs e-mails.

    `leads` est une séquence de dicts `{"thread_id", "subject", "sender", "classification"}` — la
    forme que renvoient déjà `queue_store.list_pending()` et `analytics_store.list_events()`, pour
    que l'appelant n'ait rien à retraduire.

    Renvoie `{"batch_id", "count", "ids"}`. Une séquence vide renvoie `count = 0` **sans écrire ni
    lever** : le cas se produit quand la personne clique « Envoyer » sans avoir coché de ligne, et
    faire tomber la page pour ça serait disproportionné — l'appelant affiche un avertissement à
    partir du compte renvoyé.

    Les métadonnées de l'e-mail (objet, expéditeur, classification) sont **recopiées** ici plutôt que
    relues au moment de l'affichage : le destinataire doit pouvoir lire de quoi il s'agit même si le
    lead a entre-temps été purgé par la rétention RGPD, et une demande dont l'intitulé s'évapore ne
    peut plus être ni comprise ni close.
    """
    leads = list(leads or [])
    if not leads:
        return {"batch_id": "", "count": 0, "ids": []}
    if not (requester or "").strip():
        # Une demande anonyme serait inexploitable : le destinataire ne saurait ni à qui répondre,
        # ni qui relancer. Refusé bruyamment, contrairement au lot vide qui est une simple maladresse.
        raise ValueError("Une demande de relecture doit porter le nom de son auteur.")
    if not (recipient or "").strip():
        raise ValueError("Une demande de relecture doit avoir un destinataire.")
    if priority not in PRIORITIES:
        priority = PRIORITY_NORMAL

    batch_id = uuid.uuid4().hex[:12]
    created_at = datetime.now().strftime(_TS)
    tenant = org_id or current_org_id()
    ids = []
    with _connect() as conn:
        for lead in leads:
            cursor = conn.execute(
                "INSERT INTO review_requests (org_id, batch_id, thread_id, subject, sender, "
                "classification, requester, recipient, note, priority, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tenant, batch_id, lead.get("thread_id") or "", lead.get("subject") or "",
                 lead.get("sender") or "", lead.get("classification") or "", requester.strip(),
                 recipient.strip(), (note or "").strip(), priority, created_at, STATUS_PENDING),
            )
            ids.append(cursor.lastrowid)
        conn.commit()
    return {"batch_id": batch_id, "count": len(ids), "ids": ids}


def _audience(username: str, role: str) -> tuple:
    """
    Destinataires qu'une personne « incarne » : elle-même, et sa fonction si celle-ci est adressable.

    Séparé de la requête pour que la règle soit lisible d'un seul endroit : une demande adressée aux
    administrateurs appartient à tous les administrateurs, présents comme futurs (cf. docstring du
    module), et une demande nominative n'appartient qu'à son destinataire.
    """
    from aca.storage import user_store  # import différé : évite un cycle au chargement des stores

    targets = [username or ""]
    if role == user_store.ROLE_ADMIN:
        targets.append(RECIPIENT_ADMINS)
    return tuple(t for t in targets if t)


@with_sqlite_retry
def list_for(username: str, role: str, *, status: str = STATUS_PENDING, limit: int = 200,
             org_id: str = None) -> list:
    """Demandes adressées à cette personne (ou à sa fonction), les plus urgentes/anciennes d'abord."""
    targets = _audience(username, role)
    if not targets:
        return []
    clauses = ["org_id = ?", f"recipient IN ({','.join('?' * len(targets))})"]
    params = [org_id or current_org_id(), *targets]
    if status:
        clauses.append("status = ?")
        params.append(status)
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE {' AND '.join(clauses)} "
            # Les demandes « hautes » remontent, puis l'ordre d'arrivée : une file de relecture se
            # traite dans l'ordre où elle est arrivée, sauf pour ce qui a été signalé urgent.
            "ORDER BY CASE priority WHEN 'haute' THEN 0 ELSE 1 END, id LIMIT ?",
            params,
        ).fetchall()
    return [_row(r) for r in rows]


def count_for(username: str, role: str, org_id: str = None) -> int:
    """Nombre de demandes en attente pour cette personne — alimente la pastille d'en-tête."""
    return len(list_for(username, role, org_id=org_id))


@with_sqlite_retry
def list_sent_by(requester: str, *, limit: int = 200, org_id: str = None) -> list:
    """
    Demandes qu'une personne a envoyées, tous statuts — pour qu'un opérateur sache ce qu'il attend
    encore et ce qui a été tranché, plutôt que d'envoyer dans le vide.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE org_id = ? AND requester = ? ORDER BY id DESC LIMIT ?",
            (org_id or current_org_id(), requester or "", limit),
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def list_between(start: str, end: str, org_id: str = None) -> list:
    """Demandes créées dans une fenêtre `[start, end)` — matière première des rapports (§20)."""
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE org_id = ? AND created_at >= ? AND created_at < ? ORDER BY id",
            (org_id or current_org_id(), start, end),
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def get_request(request_id: int, org_id: str = None) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE id = ? AND org_id = ?", (request_id, org_id or current_org_id()),
        ).fetchone()
    return _row(row) if row else None


@with_sqlite_retry
def mark_seen(request_id: int, org_id: str = None) -> bool:
    """
    Horodate la première consultation, sans changer le statut.

    « Vu » et « traité » sont deux événements distincts, pour la même raison que
    `task_store.acknowledged_at` : une demande ouverte puis laissée de côté reste à traiter, et
    écraser l'un par l'autre ferait disparaître de la file une demande dont personne ne s'est
    occupé. `seen_at IS NULL` garde la PREMIÈRE consultation, pas la dernière.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE review_requests SET seen_at = ? WHERE id = ? AND org_id = ? "
            "AND seen_at IS NULL",
            (datetime.now().strftime(_TS), request_id, org_id or current_org_id()),
        )
        conn.commit()
        return cursor.rowcount > 0


@with_sqlite_retry
def _close(request_id: int, status: str, resolved_by: str, resolution_note: str = "",
           org_id: str = None) -> bool:
    """
    Clôture, **uniquement depuis « en attente »** — la condition est portée par le SQL, donc
    atomique. Même raisonnement que `task_store._set_status` : deux administrateurs peuvent
    parfaitement ouvrir la même file au même moment, et une demande déjà tranchée ne doit pas être
    retranchée en écrasant la réponse du premier.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE review_requests SET status = ?, resolved_at = ?, resolved_by = ?, "
            "resolution_note = ? WHERE id = ? AND org_id = ? AND status = ?",
            (status, datetime.now().strftime(_TS), resolved_by or "", (resolution_note or "").strip(),
             request_id, org_id or current_org_id(), STATUS_PENDING),
        )
        conn.commit()
        return cursor.rowcount > 0


def resolve(request_id: int, resolved_by: str, resolution_note: str = "", org_id: str = None) -> bool:
    """Le destinataire a regardé et tranché — sa réponse revient à l'auteur de la demande."""
    return _close(request_id, STATUS_RESOLVED, resolved_by, resolution_note, org_id)


def dismiss(request_id: int, resolved_by: str, resolution_note: str = "", org_id: str = None) -> bool:
    """Le destinataire estime qu'il n'y avait pas lieu de le solliciter — tracé, pas supprimé."""
    return _close(request_id, STATUS_DISMISSED, resolved_by, resolution_note, org_id)


@with_sqlite_retry
def resolve_batch(batch_id: str, resolved_by: str, resolution_note: str = "",
                  org_id: str = None) -> int:
    """
    Clôt d'un coup toutes les demandes encore en attente d'un lot — le miroir exact de
    `create_batch` : ce qui a été transmis en un geste doit pouvoir être répondu en un geste quand
    la réponse est la même pour tout le lot (« vu, allez-y sur les quatre »).
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE review_requests SET status = ?, resolved_at = ?, resolved_by = ?, "
            "resolution_note = ? WHERE batch_id = ? AND org_id = ? AND status = ?",
            (STATUS_RESOLVED, datetime.now().strftime(_TS), resolved_by or "",
             (resolution_note or "").strip(), batch_id, org_id or current_org_id(), STATUS_PENDING),
        )
        conn.commit()
        return cursor.rowcount


def group_by_batch(requests) -> list:
    """
    Regroupe des demandes par lot pour l'affichage, en conservant l'ordre reçu.

    Pure (aucun accès base) : c'est une mise en forme, et la garder hors du magasin permet de la
    tester sans base et de l'appliquer indifféremment à une liste reçue ou envoyée.
    """
    batches = {}
    for request in requests:
        batch = batches.setdefault(request["batch_id"], {
            "batch_id": request["batch_id"], "requester": request["requester"],
            "recipient": request["recipient"], "note": request["note"],
            "priority": request["priority"], "created_at": request["created_at"], "items": [],
        })
        batch["items"].append(request)
    return list(batches.values())


@with_sqlite_retry
def cancel_for_thread(thread_id: str, org_id: str = None) -> int:
    """
    Écarte les demandes en attente portant sur un lead effacé (droit à l'oubli,
    `retention.purge_subject`) : garder à l'écran une demande de relecture sur un prospect dont on
    vient d'effacer les données reviendrait à conserver son nom précisément là où on l'a retiré.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE review_requests SET status = ?, resolved_at = ?, resolution_note = ? "
            "WHERE thread_id = ? AND org_id = ? AND status = ?",
            (STATUS_DISMISSED, datetime.now().strftime(_TS), "Lead effacé (droit à l'oubli).",
             thread_id, org_id or current_org_id(), STATUS_PENDING),
        )
        conn.commit()
        return cursor.rowcount


@with_sqlite_retry
def purge_older_than(days: int, org_id: str = None) -> int:
    """
    Efface les demandes **closes** antérieures à `days` jours (rétention RGPD : l'objet et
    l'expéditeur d'un e-mail sont des données personnelles). Une demande encore en attente n'est
    jamais purgée, quelle que soit son ancienneté — une relecture oubliée depuis un an reste une
    relecture due, et l'effacer silencieusement ferait disparaître la trace d'un lead non traité.
    """
    cutoff = datetime.now().timestamp() - days * 86400
    cutoff_text = datetime.fromtimestamp(cutoff).strftime(_TS)
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM review_requests WHERE org_id = ? AND status != ? AND created_at < ?",
            (org_id or current_org_id(), STATUS_PENDING, cutoff_text),
        )
        conn.commit()
        return cursor.rowcount

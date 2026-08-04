"""
Tâches datées posées par un humain sur un lead (§19) : **envois programmés** et **rappels**.

Pourquoi un registre de plus, alors que `followup_store.py` et `schedule_store.py` existent déjà —
la distinction est la raison d'être du module :

- `schedule_store.py` retient quand un travail **système** périodique est passé (« la purge a tourné
  à 3 h »). Aucune ligne par lead, aucune échéance choisie, rien à annuler.
- `followup_store.py` suit les leads éligibles à la relance **automatique** : c'est la machine qui
  décide du moment (`RELANCE_DAYS`), pas la personne, et il n'y a ni texte libre ni heure choisie.
- Ce module stocke ce qu'**une personne a décidé**, pour **un lead**, à **un instant qu'elle a
  choisi**. C'est un autre objet métier, avec un cycle de vie propre (annulable, traçable, avec un
  auteur nommé).

Deux natures dans une seule table, volontairement : un envoi programmé et un rappel ont exactement
la même forme (quelque chose d'attaché à un lead, dû à une date, créé par quelqu'un, annulable) et
le même cycle de vie. Deux tables imposeraient deux purges, deux listes, deux requêtes « qu'est-ce
qui est en retard » — et la seconde finirait par diverger de la première.

**L'envoi programmé n'affaiblit pas la garantie centrale du produit.** ACA ne laisse jamais partir
un message qu'aucun humain n'a lu : ici, la personne a lu le brouillon, l'a éventuellement corrigé,
puis a explicitement demandé « pars à 9 h ». L'autorisation humaine existe bien, elle est simplement
antérieure à l'exécution — comme l'envoi différé de n'importe quelle messagerie. Ce qui est
programmé est le **brouillon Gmail déjà créé** (`gmail_draft_id`) : si la personne le modifie ou le
supprime dans Gmail avant l'échéance, c'est sa version qui part, ou rien. La main reste à l'humain
jusqu'à la dernière seconde.

Même forme que les autres registres locaux : SQLite, scopé par tenant (`org_id`), et enveloppé par
`with_sqlite_retry` puisque ces écritures ont lieu **hors du graphe**, donc hors du `RETRY_POLICY`
de LangGraph. L'horodatage est stocké deux fois pour la même raison que dans `schedule_store.py` :
`due_epoch` (REAL) pour l'arithmétique d'échéance, `due_at` (TEXT) pour qu'un humain inspectant la
base comprenne ce qu'il lit.
"""
import os
import sqlite3
from datetime import datetime

from aca.core.tenant import current_org_id
from .sqlite_retry import with_sqlite_retry

DB_PATH = os.getenv("ACA_TASK_DB", "data/tasks.sqlite")

KIND_SEND = "send"
KIND_REMINDER = "reminder"
KINDS = (KIND_SEND, KIND_REMINDER)

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

KIND_LABELS = {KIND_SEND: "Envoi programmé", KIND_REMINDER: "Rappel"}
STATUS_LABELS = {
    STATUS_PENDING: "En attente", STATUS_DONE: "Effectué",
    STATUS_CANCELLED: "Annulé", STATUS_FAILED: "Échec",
}

_TS = "%Y-%m-%d %H:%M:%S"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, kind TEXT NOT NULL, "
        "thread_id TEXT, gmail_thread_id TEXT, gmail_draft_id TEXT, note TEXT, label TEXT, "
        "due_epoch REAL NOT NULL, due_at TEXT NOT NULL, created_by TEXT, created_at TEXT, "
        "status TEXT NOT NULL DEFAULT 'pending', executed_at TEXT, detail TEXT)"
    )
    # Index sur l'échéance : `list_due()` est appelée à chaque tick du planificateur, c'est la seule
    # requête de ce module dont la fréquence justifie un index.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks (org_id, status, due_epoch)"
    )
    # Migration idempotente (même procédé que la colonne `totp_secret` de `user_store.py`).
    #
    # `acknowledged_at` répond à une question que `status` ne peut pas porter : **notifier** et
    # **être vu** sont deux événements distincts. Le planificateur marque un rappel « effectué »
    # dès qu'il l'a poussé vers Slack ou l'e-mail, dans la minute qui suit l'échéance. Si
    # l'affichage dans l'application se fiait à `status`, un rappel n'y apparaîtrait qu'entre son
    # échéance et le passage du planificateur — quelques secondes, autant dire jamais. La date
    # d'accusé de réception est donc portée à part, et seul un humain la renseigne.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "acknowledged_at" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN acknowledged_at TEXT")
    return conn


_FIELDS = ("id", "kind", "thread_id", "gmail_thread_id", "gmail_draft_id", "note", "label",
           "due_epoch", "due_at", "created_by", "created_at", "status", "executed_at", "detail",
           "acknowledged_at")
_SELECT = f"SELECT {', '.join(_FIELDS)} FROM tasks"


def _row(record) -> dict:
    task = dict(zip(_FIELDS, record))
    task["kind_label"] = KIND_LABELS.get(task["kind"], task["kind"])
    task["status_label"] = STATUS_LABELS.get(task["status"], task["status"])
    return task


@with_sqlite_retry
def init_db() -> None:
    _connect().close()


@with_sqlite_retry
def add_task(kind: str, due_epoch: float, *, thread_id: str = None, gmail_thread_id: str = None,
             gmail_draft_id: str = None, note: str = "", label: str = "",
             created_by: str = "", org_id: str = None) -> int:
    """
    Enregistre une tâche à échéance et renvoie son identifiant.

    `kind` est validé ici plutôt que laissé libre : une valeur inconnue ne serait jamais exécutée
    par le planificateur, et la tâche resterait « en attente » pour toujours sans que personne
    comprenne pourquoi — une panne silencieuse, le mode d'échec que ce projet refuse partout.
    """
    if kind not in KINDS:
        raise ValueError(f"Nature de tâche inconnue : {kind!r} (attendu : {', '.join(KINDS)})")
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (org_id, kind, thread_id, gmail_thread_id, gmail_draft_id, note, "
            "label, due_epoch, due_at, created_by, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id or current_org_id(), kind, thread_id, gmail_thread_id, gmail_draft_id,
             note or "", label or "", float(due_epoch),
             datetime.fromtimestamp(due_epoch).strftime(_TS), created_by or "",
             datetime.now().strftime(_TS), STATUS_PENDING),
        )
        conn.commit()
        return cursor.lastrowid


def schedule_send(due_epoch: float, *, thread_id: str, gmail_draft_id: str,
                  gmail_thread_id: str = None, label: str = "", created_by: str = "",
                  org_id: str = None) -> int:
    """Programme l'envoi d'un brouillon Gmail **déjà relu et validé** par un humain."""
    return add_task(KIND_SEND, due_epoch, thread_id=thread_id, gmail_thread_id=gmail_thread_id,
                    gmail_draft_id=gmail_draft_id, label=label, created_by=created_by,
                    org_id=org_id)


def add_reminder(due_epoch: float, note: str, *, thread_id: str = None, label: str = "",
                 created_by: str = "", org_id: str = None) -> int:
    """Note personnelle datée sur un lead — rappelée le moment venu, jamais envoyée au prospect."""
    return add_task(KIND_REMINDER, due_epoch, thread_id=thread_id, note=note, label=label,
                    created_by=created_by, org_id=org_id)


@with_sqlite_retry
def list_due(now_epoch: float, org_id: str = None, limit: int = 50) -> list:
    """Tâches en attente dont l'échéance est passée — ce que le planificateur doit exécuter."""
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE org_id = ? AND status = ? AND due_epoch <= ? "
            "ORDER BY due_epoch LIMIT ?",
            (org_id or current_org_id(), STATUS_PENDING, float(now_epoch), limit),
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def list_pending(org_id: str = None, kind: str = None, thread_id: str = None,
                 limit: int = 200) -> list:
    """Tâches encore à venir — alimente le panneau « Tâches programmées » et la fiche d'un lead."""
    clauses, params = ["org_id = ?", "status = ?"], [org_id or current_org_id(), STATUS_PENDING]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if thread_id:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE {' AND '.join(clauses)} ORDER BY due_epoch LIMIT ?", params,
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def list_recent(org_id: str = None, limit: int = 100) -> list:
    """Historique récent, toutes natures et tous statuts — pour comprendre après coup."""
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE org_id = ? ORDER BY due_epoch DESC LIMIT ?",
            (org_id or current_org_id(), limit),
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def list_due_reminders(now_epoch: float, org_id: str = None, limit: int = 20) -> list:
    """
    Rappels échus que personne n'a encore acquittés — ce que l'application doit afficher.

    Volontairement indépendant de `status` : un rappel poussé vers Slack par le planificateur est
    « effectué » côté machine, mais tant qu'aucun humain n'a cliqué « Vu », il reste à voir. C'est
    aussi ce qui rend l'affichage utile **sans aucun service externe** — sur un poste local sans
    Slack ni e-mail sortant, cette liste est le seul canal, et elle suffit.

    Les rappels annulés sont exclus : les annuler est déjà une décision humaine.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE org_id = ? AND kind = ? AND due_epoch <= ? "
            "AND acknowledged_at IS NULL AND status != ? ORDER BY due_epoch LIMIT ?",
            (org_id or current_org_id(), KIND_REMINDER, float(now_epoch),
             STATUS_CANCELLED, limit),
        ).fetchall()
    return [_row(r) for r in rows]


@with_sqlite_retry
def acknowledge(task_id: int, org_id: str = None) -> bool:
    """
    Marque un rappel comme vu par un humain. N'altère jamais `status` : savoir qu'un rappel a bien
    été notifié (ou a échoué faute de canal) reste une information distincte, et l'écraser ferait
    perdre la trace d'une notification qui n'a atteint personne.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET acknowledged_at = ? WHERE id = ? AND org_id = ? "
            "AND acknowledged_at IS NULL",
            (datetime.now().strftime(_TS), task_id, org_id or current_org_id()),
        )
        conn.commit()
        return cursor.rowcount > 0


@with_sqlite_retry
def get_task(task_id: int, org_id: str = None) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE id = ? AND org_id = ?", (task_id, org_id or current_org_id()),
        ).fetchone()
    return _row(row) if row else None


@with_sqlite_retry
def _set_status(task_id: int, status: str, detail: str = "", org_id: str = None) -> bool:
    """
    Passage d'état, **uniquement depuis « en attente »**.

    La clause `status = 'pending'` n'est pas décorative : sans elle, un planificateur lent et une
    annulation humaine simultanée pourraient se croiser, et une tâche annulée par une personne
    repasserait à « effectué » — c'est-à-dire qu'un e-mail partirait après une annulation
    explicite. La condition est portée par la requête SQL elle-même, donc atomique.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = ?, executed_at = ?, detail = ? "
            "WHERE id = ? AND org_id = ? AND status = ?",
            (status, datetime.now().strftime(_TS), detail or "", task_id,
             org_id or current_org_id(), STATUS_PENDING),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_done(task_id: int, detail: str = "", org_id: str = None) -> bool:
    return _set_status(task_id, STATUS_DONE, detail, org_id)


def mark_failed(task_id: int, detail: str = "", org_id: str = None) -> bool:
    return _set_status(task_id, STATUS_FAILED, detail, org_id)


def cancel(task_id: int, detail: str = "", org_id: str = None) -> bool:
    """Annule une tâche en attente. Renvoie `False` si elle a déjà été exécutée ou annulée."""
    return _set_status(task_id, STATUS_CANCELLED, detail, org_id)


@with_sqlite_retry
def cancel_for_thread(thread_id: str, detail: str = "", org_id: str = None) -> int:
    """
    Annule toutes les tâches en attente d'un lead — appelé quand ce lead est effacé (droit à
    l'oubli, `retention.purge_subject`) : laisser un envoi programmé survivre à l'effacement des
    données d'une personne enverrait un message à quelqu'un qui a précisément demandé l'inverse.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = ?, executed_at = ?, detail = ? "
            "WHERE thread_id = ? AND org_id = ? AND status = ?",
            (STATUS_CANCELLED, datetime.now().strftime(_TS), detail or "", thread_id,
             org_id or current_org_id(), STATUS_PENDING),
        )
        conn.commit()
        return cursor.rowcount


@with_sqlite_retry
def purge_older_than(days: int, org_id: str = None) -> int:
    """
    Efface les tâches **terminées** antérieures à `days` jours (rétention RGPD : une note de rappel
    peut nommer un prospect). Les tâches encore en attente ne sont jamais purgées, quelle que soit
    leur ancienneté : une échéance lointaine reste une intention valide de la personne.
    """
    cutoff = datetime.now().timestamp() - days * 86400
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE org_id = ? AND status != ? AND due_epoch < ?",
            (org_id or current_org_id(), STATUS_PENDING, cutoff),
        )
        conn.commit()
        return cursor.rowcount

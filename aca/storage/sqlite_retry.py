"""
Retry local pour les accès SQLite exécutés HORS du graphe LangGraph (§11.6 item 3 de
docs/ACAM_roadmap.md) : le `RETRY_POLICY` de app.py ne couvre que les nœuds du graphe pendant
`app.invoke()` ; les appels directs des registres locaux (`enqueue`, `mark_ready`,
`log_validation`, `record_classification`...) faits par poller.py et ui.py — deux process qui
peuvent ouvrir les mêmes fichiers .sqlite en même temps — pouvaient encore lever une
`sqlite3.OperationalError` ("database is locked") sur un conflit de verrou.

SQLite absorbe déjà les conflits courts (timeout de connexion par défaut : 5 s) ; ce décorateur
couvre le cas résiduel d'un verrou tenu plus longtemps, avec quelques tentatives espacées plutôt
qu'un échec immédiat. Toute autre exception (erreur de programmation, corruption...) est propagée
telle quelle, sans retry — la rejouer à l'identique ne la corrigerait pas (même principe que le
prédicat `_retry_on` du graphe).
"""
import functools
import sqlite3
import time

MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.2


def with_sqlite_retry(fn):
    """Rejoue `fn` jusqu'à MAX_ATTEMPTS fois sur `sqlite3.OperationalError` (backoff linéaire)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError:
                if attempt == MAX_ATTEMPTS:
                    raise
                time.sleep(BASE_DELAY_SECONDS * attempt)

    return wrapper

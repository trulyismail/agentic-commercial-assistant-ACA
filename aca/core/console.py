"""
Sortie console tolérante à l'UTF-8 (correctif du 2026-07-26).

Le projet journalise abondamment avec des emoji (« ⚡ », « ⚠️ », « → »…) : 68 appels `print()`
répartis dans 13 modules. Sur cette machine Windows, `sys.stdout` est en **cp1252** dès que la
sortie est redirigée (service, fichier de log, `uvicorn` lancé en arrière-plan). Un simple
`print("⚡ …")` y lève alors `UnicodeEncodeError`.

Ce n'est pas cosmétique : ces `print()` se produisent **à l'intérieur des nœuds du graphe**. Or
chaque nœud à appel externe est enveloppé par `RETRY_POLICY` — une exception non rattrapée y
déclenche donc jusqu'à 3 réexécutions du nœud, puis fait remonter l'erreur jusqu'à `app.invoke()`.
Autrement dit, une ligne de journal décorative pouvait faire échouer une analyse complète, voire —
pour un nœud qui écrit — provoquer une double écriture CRM. Exactement le scénario déjà rencontré
et corrigé ponctuellement dans `hubspot.py` (cf. « Known gaps » de CLAUDE.md, 2026-07-12), où le
correctif local consistait à try/excepter deux `print()`. Ce motif-là ne passe pas à l'échelle de
68 appels.

La correction est donc placée à la **frontière du processus** plutôt que sur chaque appel :
`sys.stdout`/`sys.stderr` sont reconfigurés une fois en UTF-8 avec `errors="replace"`. Deux
propriétés importantes :

- `errors="replace"` garantit qu'aucun caractère, quel qu'il soit, ne peut plus faire lever un
  `print()` — au pire il s'affiche « ? ». Un journal dégradé vaut infiniment mieux qu'une analyse
  perdue.
- La fonction est idempotente et ne lève jamais : si le flux ne supporte pas `reconfigure()`
  (flux capturé par pytest, `StringIO`, environnement embarqué), on ne fait rien.

Appelée depuis `aca/__init__.py`, donc active pour **toute** entrée du projet (API, poller,
Streamlit, scripts CLI) sans que chaque point d'entrée ait à y penser.
"""
import sys


def enable_utf8_console() -> bool:
    """
    Reconfigure `stdout`/`stderr` en UTF-8 tolérant. Renvoie True si au moins un flux a été
    reconfiguré, False sinon (aucun effet, jamais d'exception).
    """
    reconfigured = False
    for stream in (sys.stdout, sys.stderr):
        # `reconfigure` n'existe que sur les `TextIOWrapper` : un flux capturé par pytest ou
        # remplacé par un `StringIO` n'en dispose pas, et n'en a pas besoin.
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            reconfigured = True
        except (ValueError, OSError):
            # Flux détaché ou déjà fermé : la journalisation n'est jamais une raison d'échouer.
            continue
    return reconfigured

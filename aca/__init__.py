"""
Paquet ACA.

Ce fichier était vide ; il ne porte volontairement qu'un seul effet : rendre la console tolérante
à l'UTF-8 dès qu'un module `aca.*` est importé (cf. `aca/core/console.py`). Le placer ici plutôt
que dans chaque point d'entrée (API, poller, Streamlit, scripts CLI) évite qu'un futur point
d'entrée oublie l'appel — et c'est précisément ce genre d'oubli qui a fait échouer `POST /threads`
en HTTP 500 sur une simple ligne de journal contenant un emoji, sous la console cp1252 de Windows.

Effet de bord assumé à l'import : c'est une application, pas une bibliothèque réutilisable, et la
reconfiguration est sûre (`errors="replace"`, jamais d'exception, idempotente).
"""
from aca.core.console import enable_utf8_console

enable_utf8_console()

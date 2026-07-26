# -*- coding: utf-8 -*-
"""
Lanceur du palier « Solo » (§16.0 de docs/ACAM_roadmap.md) — ACA entièrement autonome, SANS n8n.

Le palier Solo n'est pas un mode dégradé « à boutons » : il est déjà automatisé de bout en bout.
Il lui manquait seulement d'être lançable simplement. Quatre processus doivent tourner ensemble :

    api        uvicorn aca.api:api          — le cerveau exposé en HTTP (dashboard, Slack, n8n plus tard)
    ui         streamlit run ui.py          — la console opérateur (validation humaine)
    poller     python -m aca.core.poller    — ingestion Gmail + traitement passif 24/7
    scheduler  python -m aca.core.scheduler — relances, purge RGPD, maintenance

Sans ce script il fallait quatre terminaux et se souvenir de quatre commandes, ce qui suffit en
pratique à ce que le poller et le planificateur ne soient jamais lancés — donc à ce que le produit
paraisse « manuel » alors qu'il ne l'est pas. C'est exactement le malentendu que le §16.0 corrige.

Volontairement sans aucun import `aca.*` ni dépendance tierce : ce fichier vit hors du package et
se lance en exécution directe (`python scripts/run_solo.py`), qui ne place pas la racine du dépôt
sur `sys.path` — même raison que [setup_faq.py](setup_faq.py). Les processus enfants, eux, sont
lancés avec `cwd` = racine du dépôt, donc leurs imports `aca.*` fonctionnent normalement.

Lancement :
    python scripts/run_solo.py                     # les 4 services
    python scripts/run_solo.py --without ui        # sans Streamlit (serveur sans écran)
    python scripts/run_solo.py --only api,ui       # démo rapide, sans automatisation de fond
    python scripts/run_solo.py --api-port 9000

Ctrl+C arrête proprement tous les services. Pour le palier « Enterprise » (avec n8n), voir
docker-compose.yml (`--profile enterprise`).
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _enable_utf8_console() -> None:
    """
    Équivalent local de `aca.core.console.enable_utf8_console()`, recopié en 6 lignes plutôt
    qu'importé : ce script s'interdit tout import `aca.*` (cf. docstring du module). Sans lui, la
    console cp1252 de Windows affiche « s�par�s » jusque dans l'aide d'argparse — ce fichier
    documente ce piège, il serait embarrassant qu'il y tombe.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # flux détaché/fermé : la journalisation n'est jamais une raison d'échouer

# Couleurs ANSI pour distinguer les services dans un flux de sortie fusionné. Windows 10+ les
# interprète nativement ; si le terminal ne les gère pas, ce sont quelques caractères parasites en
# début de ligne — jamais une erreur, et `--no-color` les retire.
COLORS = {"api": "\033[36m", "ui": "\033[35m", "poller": "\033[32m", "scheduler": "\033[33m"}
RESET = "\033[0m"


def _services(args) -> dict:
    """Table déclarative des services — ajouter un service = une entrée ici."""
    return {
        "api": [
            sys.executable, "-m", "uvicorn", "aca.api:api",
            "--host", args.api_host, "--port", str(args.api_port),
        ],
        "ui": [
            sys.executable, "-m", "streamlit", "run", "ui.py",
            "--server.port", str(args.ui_port),
            # Coupe la télémétrie et l'invite d'e-mail au 1er lancement : sur une démo client, une
            # question interactive dans le terminal bloque le démarrage sans que personne ne
            # comprenne pourquoi la page ne répond pas.
            "--browser.gatherUsageStats", "false",
        ],
        "poller": [sys.executable, "-m", "aca.core.poller"],
        "scheduler": [sys.executable, "-m", "aca.core.scheduler"],
    }


def _gmail_configured() -> bool:
    """
    Le poller et les relances n'ont de sens qu'avec Gmail authentifié. Le fichier d'identifiants
    OAuth est le prérequis minimal (le jeton, lui, se crée au premier consentement navigateur).
    Existence seule — le fichier n'est jamais ouvert ni lu ici.
    """
    creds = os.getenv("GMAIL_CREDENTIALS_FILE") or os.path.join("credentials", "gmail_credentials.json")
    return os.path.exists(os.path.join(ROOT, creds)) or os.path.exists(creds)


def _pump(name: str, stream, use_color: bool) -> None:
    """Recopie la sortie d'un enfant en préfixant chaque ligne par le nom du service."""
    prefix = f"{COLORS.get(name, '')}[{name}]{RESET} " if use_color else f"[{name}] "
    for line in iter(stream.readline, ""):
        sys.stdout.write(prefix + line)
        sys.stdout.flush()
    stream.close()


def main() -> int:
    _enable_utf8_console()
    parser = argparse.ArgumentParser(
        description="Lance le palier Solo d'ACA (API + Streamlit + poller + planificateur), sans n8n.",
    )
    parser.add_argument("--only", help="ne lancer que ces services (séparés par des virgules)")
    parser.add_argument("--without", help="lancer tout sauf ces services (séparés par des virgules)")
    parser.add_argument("--api-host", default="127.0.0.1",
                        help="interface d'écoute de l'API (défaut : 127.0.0.1, boucle locale — "
                             "cf. docs/DEPLOYMENT_HARDENING.md avant d'exposer publiquement)")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=8501)
    parser.add_argument("--no-color", action="store_true", help="désactive les couleurs ANSI")
    args = parser.parse_args()

    services = _services(args)
    selected = list(services)
    if args.only:
        selected = [s.strip() for s in args.only.split(",") if s.strip()]
    if args.without:
        excluded = {s.strip() for s in args.without.split(",") if s.strip()}
        selected = [s for s in selected if s not in excluded]

    unknown = [s for s in selected if s not in services]
    if unknown:
        print(f"Service(s) inconnu(s) : {', '.join(unknown)}. Connus : {', '.join(services)}.")
        return 2
    if not selected:
        print("Aucun service à lancer.")
        return 2

    # Dégradation gracieuse, comme partout dans le projet : sans identifiants Gmail, le poller et
    # le planificateur tourneraient en boucle d'erreurs. On le dit clairement plutôt que de laisser
    # défiler des traces incompréhensibles pendant une démo.
    if not _gmail_configured():
        skipped = [s for s in ("poller", "scheduler") if s in selected]
        if skipped:
            print(f"⚠️  Gmail non configuré (credentials/gmail_credentials.json absent) — "
                  f"{', '.join(skipped)} non lancé(s).")
            print("    L'API et l'interface fonctionnent normalement (saisie manuelle).")
            selected = [s for s in selected if s not in skipped]
        if not selected:
            return 2

    env = dict(os.environ)
    # La sortie des enfants est redirigée dans un tuyau : sous Windows elle repasserait en cp1252 et
    # le moindre emoji lèverait UnicodeEncodeError (cf. aca/core/console.py).
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    use_color = not args.no_color
    processes = {}
    print(f"▶ Palier Solo — démarrage de : {', '.join(selected)} (Ctrl+C pour tout arrêter)")
    for name in selected:
        processes[name] = subprocess.Popen(
            services[name], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(
            target=_pump, args=(name, processes[name].stdout, use_color), daemon=True,
        ).start()

    if "api" in processes:
        print(f"   API        → http://{args.api_host}:{args.api_port}  (santé : /health)")
    if "ui" in processes:
        print(f"   Interface  → http://localhost:{args.ui_port}")

    try:
        while True:
            for name, process in list(processes.items()):
                code = process.poll()
                if code is not None:
                    print(f"⚠️  Le service « {name} » s'est arrêté (code {code}).")
                    del processes[name]
            if not processes:
                print("Tous les services sont arrêtés.")
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹  Arrêt en cours...")
    finally:
        for name, process in processes.items():
            if process.poll() is None:
                process.terminate()
        deadline = time.time() + 10
        for name, process in processes.items():
            remaining = max(0, deadline - time.time())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                print(f"   « {name} » ne répond pas — arrêt forcé.")
                process.kill()
    return 0


if __name__ == "__main__":
    # Sous Windows, Ctrl+C dans une console envoie l'événement à tout le groupe de processus ; le
    # `KeyboardInterrupt` ci-dessus suffit à déclencher l'arrêt ordonné dans les deux cas.
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, signal.default_int_handler)
    sys.exit(main())

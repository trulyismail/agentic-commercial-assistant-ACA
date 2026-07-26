# Image unique pour les quatre services ACA (§16.1.5 de docs/ACAM_roadmap.md).
#
# Un seul Dockerfile, quatre commandes : l'API, l'interface Streamlit, le poller d'ingestion et le
# planificateur partagent exactement le même code et les mêmes dépendances — ce sont des points
# d'entrée différents du même paquet, pas des applications différentes. `docker-compose.yml` choisit
# la commande service par service.
#
# C'est aussi ce qui rend démontrable la promesse du §16.0 : le palier « Solo » (sans n8n) et le
# palier « Enterprise » (avec n8n) tournent sur **la même image**, seul le profil compose change.

FROM python:3.14-slim

# - PYTHONUNBUFFERED : les journaux doivent sortir en direct, sinon `docker logs` reste muet
#   pendant qu'une analyse tourne.
# - PYTHONIOENCODING : le projet journalise avec des emoji ; sans UTF-8 forcé, un flux redirigé
#   repasse en encodage système et un simple print() peut lever UnicodeEncodeError à l'intérieur
#   d'un nœud sous RETRY_POLICY (cf. aca/core/console.py — incident réel du 2026-07-12).
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Les dépendances d'abord, séparément du code : cette couche est mise en cache et n'est reconstruite
# que si requirements.txt change — pas à chaque modification d'un fichier Python.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aca/ ./aca/
COPY scripts/ ./scripts/
COPY .streamlit/ ./.streamlit/
COPY ui.py ./

# `data/` accueille les registres SQLite (file d'attente, analytics, audit, planificateur…).
# docker-compose.yml y monte un volume nommé : sans lui, valider un lead puis redémarrer le
# conteneur perdrait le journal d'audit et l'historique de planification.
#
# `credentials/` n'est VOLONTAIREMENT pas copié dans l'image : il est monté en lecture seule au
# lancement. Un secret gravé dans une couche d'image y reste pour toujours, même supprimé ensuite
# (cf. docs/DEPLOYMENT_HARDENING.md §3).
RUN mkdir -p data

# Utilisateur non privilégié : rien ici n'a besoin de root, et une image qui tourne en root est le
# premier reproche d'une revue de sécurité.
RUN useradd --create-home --uid 10001 aca && chown -R aca:aca /app
USER aca

EXPOSE 8000 8501

# Sonde native : `/health` ne joint aucun service externe (§16.1.3), elle peut donc être appelée
# toutes les 30 s sans consommer de quota Groq/Gemini ni tomber en panne pour cause de panne d'un
# tiers optionnel. `urllib` évite d'installer curl dans l'image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Commande par défaut : l'API. Les autres services surchargent `command` dans docker-compose.yml.
CMD ["uvicorn", "aca.api:api", "--host", "0.0.0.0", "--port", "8000"]

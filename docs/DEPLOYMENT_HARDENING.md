# Durcissement au déploiement — TLS, secrets, exposition publique

Référence opérationnelle pour la **première mise en ligne** d'ACA (§15.1.8, §15.1.9 et le « bloc
exposition publique » de §15.5 dans [ACAM_roadmap.md](ACAM_roadmap.md)).

Ce document existe parce que deux items de la checklist sécurité ne sont pas du code : rien n'est
hébergé aujourd'hui, donc ni TLS ni coffre de secrets ne peuvent être « implémentés » — ils se
configurent le jour du déploiement. Ce qui *pouvait* être codé l'a été et se trouve dans le dépôt
(`aca/core/prod_check.py`, `aca/storage/user_store.py`, `aca/core/session.py`, garde `/metrics`,
validation stricte des entrées) ; ce qui reste est ici, sous forme exécutable plutôt que
d'intention.

> **Public visé** : la personne qui déploie. Pour la notice destinée aux personnes dont les données
> sont traitées, voir [PRIVACY_POLICY.md](PRIVACY_POLICY.md).

---

## 0. En un coup d'œil : le contrôle automatique

Avant tout déploiement :

```bash
ACA_ENV=production python -m aca.core.prod_check
```

Le script liste chaque protection manquante et sort en code 1 s'il en reste. Les mêmes contrôles
sont rejoués **au démarrage** de `aca/api.py` et de `ui.py` via `prod_check.enforce()` : en
`ACA_ENV=production`, une configuration ouverte fait échouer le lancement au lieu de servir une API
sans authentification. C'est délibérément l'inverse du contrat de dégradation gracieuse qui régit le
reste du projet — ce contrat est le bon défaut en développement et le mauvais en production.

| Variable | Rôle | Conséquence si absente en production |
|---|---|---|
| `ACA_ENV=production` | Active tous les contrôles ci-dessous | Le mode développement reste actif : rien n'est imposé |
| `ACA_API_KEY` | Garde de toutes les routes de `aca/api.py` | Démarrage refusé (sinon : écriture CRM ouverte) |
| `ACA_METRICS_TOKEN` | Garde de `GET /metrics` | Démarrage refusé (sinon : volumétrie et tenants publics) |
| `ACA_RATE_LIMIT` | Limite de débit par client | Démarrage refusé (sinon : rafales et brute-force non absorbés) |
| Comptes ou `ACA_UI_PASSWORD` | Accès à l'UI Streamlit | Démarrage refusé (sinon : UI ouverte) |
| `SLACK_SIGNING_SECRET` | Signature des boutons Valider/Rejeter | Signalé ; l'endpoint échoue **fermé** (503), donc pas de faille |
| `ACA_ENABLE_DOCS` | Ré-expose `/docs` et `/openapi.json` | Doit rester **non défini** en production |
| `ACA_AUDIT_HMAC_KEY` | Rend le journal d'audit infalsifiable sans la clé | Chaînage SHA-256 simple, recalculable par qui peut écrire dans la base |

---

## 1. Créer les comptes avant la première connexion

Tant qu'aucun compte n'existe, l'UI retombe sur le secret partagé `ACA_UI_PASSWORD` : personne n'est
identifié et le journal d'audit n'est pas opposable. Créer au moins un administrateur :

```bash
python -m aca.storage.user_store create prenom.nom --role admin
python -m aca.storage.user_store create commercial1 --role operator
python -m aca.storage.user_store list
```

Rôles : `operator` valide et rejette des leads ; `admin` peut en plus modifier les réglages, curer
la base de connaissances et gérer les comptes. Retirer ensuite `ACA_UI_PASSWORD` de
l'environnement — le laisser maintiendrait une porte d'entrée anonyme en parallèle des comptes.

Un départ se traite par **désactivation**, jamais par suppression : le journal d'audit référence
l'identifiant, l'effacer rendrait des validations passées non attribuables.

```bash
python -m aca.storage.user_store disable prenom.nom
```

---

## 2. TLS / HTTPS (§15.1.9)

Ni Streamlit ni Uvicorn ne doivent être exposés directement sur Internet. Les deux se placent
derrière un reverse-proxy qui termine TLS et renouvelle les certificats automatiquement.

### Caddy (recommandé — certificat Let's Encrypt automatique, zéro cron)

```caddyfile
aca.exemple.fr {
    encode gzip
    # En-têtes de sécurité : HSTS force HTTPS au navigateur pour 2 ans ; les trois autres
    # bloquent le sniffing MIME, l'inclusion en iframe (clickjacking) et la fuite de referer.
    header {
        Strict-Transport-Security "max-age=63072000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "DENY"
        Referrer-Policy           "strict-origin-when-cross-origin"
        -Server
    }

    # UI Streamlit (WebSocket requis : Caddy le gère nativement)
    reverse_proxy 127.0.0.1:8501

    # API — utile seulement si un n8n ou un dashboard externe doit l'atteindre.
    handle_path /api/* {
        reverse_proxy 127.0.0.1:8000
    }

    # /metrics porte déjà son propre jeton (ACA_METRICS_TOKEN) ; restreindre en plus par IP
    # évite d'exposer publiquement une surface qui n'intéresse que le scrapeur.
    @metrics path /api/metrics
    handle @metrics {
        @autorise remote_ip 10.0.0.0/8
        handle @autorise {
            reverse_proxy 127.0.0.1:8000
        }
        respond 403
    }
}
```

### Nginx (si l'hébergeur l'impose)

```nginx
server {
    listen 443 ssl http2;
    server_name aca.exemple.fr;

    ssl_certificate     /etc/letsencrypt/live/aca.exemple.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aca.exemple.fr/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options    "nosniff" always;
    add_header X-Frame-Options           "DENY" always;

    location / {
        proxy_pass http://127.0.0.1:8501;
        # Streamlit communique en WebSocket : sans ces deux en-têtes, l'UI se charge mais
        # reste figée (« Connecting… ») — la panne la plus fréquente sur ce montage.
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name aca.exemple.fr;
    return 301 https://$host$request_uri;
}
```

Renouvellement : automatique avec Caddy ; avec Nginx, `certbot renew` via timer systemd
(`systemctl list-timers | grep certbot` pour vérifier qu'il est bien armé — un certificat expiré
rend le service inaccessible d'un coup, sans avertissement préalable côté serveur).

**Écouter sur la boucle locale uniquement**, pour que rien ne contourne le proxy :

```bash
streamlit run ui.py --server.address 127.0.0.1 --server.port 8501
uvicorn aca.api:api --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

`X-Forwarded-For` a une conséquence directe côté application : la limite de débit
(`ACA_RATE_LIMIT`) identifie les clients par IP source quand aucune clé API n'est fournie. Derrière
un proxy mal configuré, toutes les requêtes semblent venir de `127.0.0.1` et partagent donc un seul
quota — d'où les options `--proxy-headers --forwarded-allow-ips` ci-dessus.

---

## 3. Gestion des secrets (§15.1.8)

Aujourd'hui : un fichier `.env` côté serveur, gitignoré. C'est acceptable pour un déploiement
unique et un opérateur ; ça ne l'est plus dès qu'il y a plusieurs environnements, plusieurs
personnes, ou une obligation de rotation.

**Règles minimales, quel que soit le support**

1. `.env` en permissions `600`, propriétaire = l'utilisateur qui exécute le service.
2. Jamais de secret en argument de ligne de commande (visible dans `ps`) ni dans une image Docker.
3. Jamais de secret dans un journal — c'est la raison d'être de `_safe_error()` dans `ui.py` et du
   handler d'exception générique de `aca/api.py` : le texte brut d'une erreur d'API contient
   régulièrement l'URL appelée, des en-têtes, voire un fragment de clé.
4. Secrets **distincts par environnement**. Un secret de production qui a servi en recette est
   compromis par construction.

**Passage à un coffre** (Vault, Doppler, AWS/GCP Secrets Manager) — pertinent uniquement en phase
hébergée multi-client. Le code n'a pas besoin de changer : tous les modules lisent leur
configuration via `os.getenv()` **dynamiquement**, jamais figée à l'import (leçon du bug
`DATABASE_URL` du 2026-07-11, cf. [PROJECT_JOURNAL.md](PROJECT_JOURNAL.md)). Un agent de coffre qui
injecte les variables dans l'environnement du processus suffit donc, sans adaptateur applicatif.

### Rotation

| Secret | Fréquence conseillée | Effet de la rotation |
|---|---|---|
| `ACA_API_KEY` | 90 jours, et à tout départ | Les clients (dashboard, n8n) doivent être mis à jour ensemble |
| `ACA_METRICS_TOKEN` | 90 jours | Mettre à jour le `scrape_config` Prometheus |
| `DASHBOARD_SESSION_SECRET` | À la demande | **Invalide instantanément toutes les sessions dashboard** — c'est le levier de déconnexion globale |
| `SLACK_SIGNING_SECRET` | Si soupçon de fuite | Régénérer côté application Slack ; les boutons échouent fermé entre-temps |
| Mots de passe des comptes | À tout soupçon | `python -m aca.storage.user_store passwd <identifiant>` |
| `ACA_AUDIT_HMAC_KEY` | **Ne pas faire tourner à la légère** | Voir ci-dessous |

⚠️ `ACA_AUDIT_HMAC_KEY` est le seul secret dont la rotation **casse** une vérification : toutes les
empreintes déjà écrites ont été calculées avec l'ancienne clé, `verify_chain()` les déclarera donc
rompues. Si la rotation est nécessaire, vérifier et archiver la chaîne existante *avant*
(`python -m aca.storage.audit_log`), en conservant la preuve que le journal était intact au moment
du changement.

---

## 4. Base de données et isolation

Si Supabase est utilisé (`DATABASE_URL` défini) :

```bash
python scripts/verify_rls.py
```

Le script vérifie que **chaque** table du schéma `public` a la RLS activée avec au moins une
politique, **et** que le rôle de connexion ne la contourne pas. Ce second point est le plus
important : le rôle `postgres` fourni par défaut dans le `DATABASE_URL` de Supabase porte
`rolbypassrls = true`, ce qui rend l'isolation inopérante alors que le SQL semble correct — piège
réel rencontré le 2026-07-21. L'application doit se connecter avec un rôle restreint (`aca_app`,
ni `SUPERUSER` ni `BYPASSRLS`) ; `postgres` reste réservé à l'administration et aux migrations.

Dernière exécution vérifiée (2026-07-26) : 5 tables, 0 sans politique, rôle de connexion `aca_app`
restreint.

---

## 5. Dépendances (§15.3.8)

```bash
pip install -r requirements.txt
python -m pip_audit
```

À exécuter avant chaque déploiement et périodiquement (une fois par mois suffit à ce volume). Le
scan du 2026-07-26 a trouvé 17 vulnérabilités connues dans 3 paquets — dont deux **transitifs**
(`gitpython` via Streamlit, `pyasn1` via `google-auth`), que personne n'aurait pensé à surveiller
puisque le projet ne les importe pas. D'où les planchers `>=` explicites ajoutés dans
`requirements.txt` : sans eux, une installation neuve pouvait réintroduire les versions vulnérables.

---

## 6. Sauvegardes et données personnelles

- Les registres SQLite locaux (`data/*.sqlite`) contiennent des données personnelles (expéditeurs,
  corps d'e-mails dans les checkpoints). Ils doivent être sauvegardés **chiffrés** et soumis à la
  même durée de conservation que la production.
- Purge périodique : `python -m aca.core.retention` (à planifier, par exemple hebdomadaire).
- Demande d'effacement d'une personne (RGPD art. 17) :
  `python -m aca.core.retention --oublier adresse@exemple.fr`.
  Le journal d'audit est volontairement conservé (intérêt légitime, cf. la docstring de
  `purge_subject` et [PRIVACY_POLICY.md](PRIVACY_POLICY.md)).
- Vérifier périodiquement l'intégrité du journal : `python -m aca.storage.audit_log`.

---

## 7. Limites connues, énoncées franchement

Ce qui suit n'est pas couvert et ne doit pas être présenté comme tel :

- **Le chaînage du journal d'audit est *tamper-evident*, pas *tamper-proof*.** Sans
  `ACA_AUDIT_HMAC_KEY`, qui peut écrire dans le fichier peut recalculer toute la chaîne. Une vraie
  inviolabilité demanderait un stockage append-only ou un ancrage externe.
- **La limite de débit est en mémoire, mono-processus.** Exacte avec un seul worker Uvicorn ;
  plusieurs workers multiplient mécaniquement le quota. Un déploiement à plusieurs workers exige un
  backend partagé (Redis).
- **Les rôles sont applicatifs.** Ils gouvernent l'UI et l'API, pas un accès direct au fichier
  SQLite ou à la base : quiconque a un shell sur le serveur passe outre.
- **La détection d'injection de prompt signale, ne bloque pas** — et un filtre par expressions
  régulières se contourne. La protection réelle reste le gate humain
  (`interrupt_before=["action"]`) ; le drapeau sert à ce que cet humain décide en connaissance de
  cause.
- **Pas d'authentification multi-facteurs, pas de SSO.** Comptes locaux avec mot de passe haché
  uniquement.
- **Le dashboard Next.js reste sur un secret partagé** (`DASHBOARD_PASSWORD`), sans comptes
  nominatifs : il est *parqué* (cf. §12bis de la roadmap), Streamlit étant la surface
  opérationnelle. Ne pas l'exposer publiquement en l'état.

"""
Contrôle de posture de sécurité au démarrage (§15.1.5 / §15.3.3 / §15.5 « bloc exposition
publique » de docs/ACAM_roadmap.md).

Tout le projet est construit sur une **dégradation gracieuse** : chaque protection est optionnelle
et son absence fait simplement retomber le code sur un chemin dégradé (pas de clé API ⇒ pas de
garde, pas de mot de passe ⇒ UI ouverte…). C'est le bon défaut en développement — et exactement le
mauvais le jour du premier hébergement public, où « absent = ouvert » devient « exposé à
l'Internet sans authentification ». L'audit §15.5 le formule ainsi : rendre l'auth API
*obligatoire* (pas optionnelle) en production.

D'où un interrupteur explicite, `ACA_ENV` :

- `ACA_ENV` absent ou `development` (défaut) : comportement strictement inchangé, aucun contrôle,
  aucune régression pour les usages locaux et la suite de tests ;
- `ACA_ENV=production` : les protections deviennent **obligatoires**. `enforce()` lève au
  démarrage plutôt que de servir une API ouverte — un échec bruyant au lancement vaut mieux qu'un
  endpoint d'écriture CRM accessible sans en-tête.

`check()` renvoie la liste des problèmes sans jamais lever : c'est ce que `ui.py` affiche en
bandeau et ce que `python -m aca.core.prod_check` imprime pour un contrôle avant déploiement.
Toutes les variables sont lues DYNAMIQUEMENT (jamais figées à l'import — même leçon que
`DATABASE_URL`/`ACA_ORG_ID` ailleurs dans le projet).
"""
import os

PRODUCTION = "production"


class InsecureConfiguration(RuntimeError):
    """Levée par `enforce()` quand `ACA_ENV=production` et qu'une protection requise manque."""


def is_production() -> bool:
    """Le process tourne-t-il en mode production (`ACA_ENV=production`) ?"""
    return (os.getenv("ACA_ENV") or "").strip().lower() == PRODUCTION


def _rate_limit_active() -> bool:
    """`ACA_RATE_LIMIT` est-elle réglée à une valeur strictement positive ?"""
    try:
        return int(os.getenv("ACA_RATE_LIMIT") or "0") > 0
    except ValueError:
        return False


def _has_accounts() -> bool:
    """
    Un compte nominatif existe-t-il ? Import différé : `user_store` ouvre un fichier SQLite, inutile
    de payer ça quand `check()` est appelé en mode développement.
    """
    try:
        from aca.storage import user_store

        return user_store.has_users()
    except Exception:
        return False


def check() -> list:
    """
    Liste des faiblesses de configuration au regard d'un déploiement exposé publiquement.

    Renvoie toujours une liste (vide = rien à signaler) et ne lève jamais : appelable depuis une
    UI, un test ou un script de pré-déploiement sans try/except.
    """
    problems = []

    if not os.getenv("ACA_API_KEY"):
        problems.append(
            "ACA_API_KEY n'est pas définie : l'API (aca/api.py) accepte toute requête, y compris "
            "POST /threads/{id}/valider qui écrit dans le CRM."
        )
    if not os.getenv("ACA_UI_PASSWORD") and not _has_accounts():
        problems.append(
            "Aucun compte (aca/storage/user_store.py) ni ACA_UI_PASSWORD : l'interface Streamlit "
            "est ouverte à quiconque atteint le port."
        )
    if not _rate_limit_active():
        problems.append(
            "ACA_RATE_LIMIT n'est pas définie (ou vaut 0) : aucune limite de débit par client, "
            "donc rien n'absorbe une rafale de requêtes ou un brute-force."
        )
    if os.getenv("SLACK_WEBHOOK_URL") and not os.getenv("SLACK_SIGNING_SECRET"):
        problems.append(
            "SLACK_WEBHOOK_URL est configurée sans SLACK_SIGNING_SECRET : les boutons "
            "« Valider »/« Rejeter » seront postés mais tout clic échouera (503). L'endpoint "
            "échoue fermé, ce n'est donc pas une faille — mais la boucle d'approbation Slack est "
            "inopérante."
        )
    if os.getenv("ACA_ENABLE_DOCS") == "1":
        problems.append(
            "ACA_ENABLE_DOCS=1 expose /docs et /openapi.json : la surface d'API complète est "
            "publiée, y compris les routes d'écriture."
        )
    if not os.getenv("ACA_METRICS_TOKEN"):
        problems.append(
            "ACA_METRICS_TOKEN n'est pas défini : /metrics est lisible sans authentification "
            "(volumétrie, nombre de leads validés, tenants)."
        )

    return problems


def enforce() -> None:
    """
    En production, refuse de démarrer si `check()` remonte quoi que ce soit ; ailleurs, no-op
    silencieux. Appelée à l'import de `aca/api.py` et au démarrage de `ui.py`.
    """
    if not is_production():
        return
    problems = check()
    if problems:
        raise InsecureConfiguration(
            "ACA_ENV=production mais la configuration n'est pas sûre :\n  - "
            + "\n  - ".join(problems)
            + "\n\nCorrigez ces points (cf. docs/DEPLOYMENT_HARDENING.md) ou repassez en "
            "ACA_ENV=development pour un usage local."
        )


def _main() -> None:
    """`python -m aca.core.prod_check` — contrôle avant déploiement, code de sortie 1 si problème."""
    problems = check()
    print(f"Mode ACA_ENV : {'production' if is_production() else 'developpement'}")
    if not problems:
        print("OK - aucune faiblesse de configuration detectee.")
        return
    print(f"{len(problems)} point(s) a corriger avant une exposition publique :")
    for problem in problems:
        print(f"  - {problem}")
    raise SystemExit(1)


if __name__ == "__main__":
    _main()

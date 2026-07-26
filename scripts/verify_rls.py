"""
Balayage de couverture RLS sur Supabase (§15.2.2 de docs/ACAM_roadmap.md).

L'audit §15.2.2 posait une question de vérification, pas d'implémentation : « confirmer que toute
table `public` a au moins une politique — 0 table sans politique ». Elle était restée ouverte parce
qu'y répondre demande d'interroger la vraie base, pas de relire le code. Ce script est cette
réponse, rejouable : il liste chaque table du schéma `public`, dit si la RLS y est activée
(`relrowsecurity`), forcée (`relforcerowsecurity`) et combien de politiques la couvrent.

Pourquoi les deux drapeaux comptent, et pas seulement `ENABLE` : `FORCE ROW LEVEL SECURITY` est ce
qui applique la politique au **propriétaire** de la table — sans lui, le rôle propriétaire lit tout.
Et même `FORCE` ne contraint pas un rôle portant `BYPASSRLS` ou `SUPERUSER` : c'est exactement le
piège rencontré le 2026-07-21, où le rôle `postgres` fourni par défaut dans le `DATABASE_URL` de
Supabase a `rolbypassrls = true`, ce qui rendait l'isolation inopérante alors que le SQL semblait
correct (cf. docs/PROJECT_JOURNAL.md). Le script vérifie donc AUSSI le rôle de connexion — sans
quoi un rapport « tout est vert » pourrait être entièrement trompeur.

Trois verdicts par table :
- **OK**        : RLS activée + forcée + au moins une politique.
- **ATTENTION** : configuration partielle (activée sans être forcée, ou forcée sans politique —
                  ce qui, en Postgres, revient à tout refuser).
- **EXPOSEE**   : aucune RLS du tout.

Lecture seule, aucune écriture. Lancement : `python scripts/verify_rls.py`
(nécessite `DATABASE_URL` ; sans elle le script le dit et sort proprement — ce projet fonctionne
volontairement sans Supabase).
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Tables connues sans `org_id`, volontairement couvertes par une politique permissive : LangGraph
# gère lui-même son cloisonnement par `thread_id` à l'intérieur de ses propres requêtes, et ses
# tables n'ont pas de colonne de tenant à comparer. Les signaler comme un défaut à chaque exécution
# entraînerait à ignorer le rapport — le bruit tue la vérification.
EXPECTED_PERMISSIVE = {
    "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations",
}

TABLES_QUERY = """
    SELECT c.relname,
           c.relrowsecurity,
           c.relforcerowsecurity,
           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policy_count
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind = 'r'
     ORDER BY c.relname
"""

ROLE_QUERY = """
    SELECT current_user, r.rolsuper, r.rolbypassrls
      FROM pg_roles r
     WHERE r.rolname = current_user
"""


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL absente : ce deploiement n'utilise pas Supabase (SqliteSaver + cache "
              "memoire). Rien a verifier, la RLS Postgres ne s'applique pas.")
        return 0

    try:
        import psycopg
    except ImportError:
        print("psycopg n'est pas installe (pip install -r requirements.txt).")
        return 1

    with psycopg.connect(database_url) as conn:
        user, is_super, bypasses_rls = conn.execute(ROLE_QUERY).fetchone()
        rows = conn.execute(TABLES_QUERY).fetchall()

    print(f"Role de connexion : {user}")
    if is_super or bypasses_rls:
        # Ce n'est pas un avertissement de forme : dans cet etat, TOUTES les lignes « OK »
        # ci-dessous sont sans effet pour l'application elle-meme.
        print("  !! Ce role contourne la RLS (SUPERUSER ou BYPASSRLS). Les politiques ci-dessous "
              "ne s'appliquent PAS a ce role : l'application doit se connecter avec un role "
              "restreint (cf. `aca_app`, docs/PROJECT_JOURNAL.md 2026-07-21).")
    else:
        print("  Role restreint (ni SUPERUSER ni BYPASSRLS) : les politiques s'appliquent bien.")
    print()

    if not rows:
        print("Aucune table dans le schema public.")
        return 0

    exposed, warnings = [], []
    print(f"{'Table':<28} {'RLS':<6} {'FORCE':<7} {'Politiques':<11} Verdict")
    print("-" * 78)
    for name, rls_enabled, rls_forced, policy_count in rows:
        expected_permissive = name in EXPECTED_PERMISSIVE
        if not rls_enabled:
            verdict = "EXPOSEE"
            exposed.append(name)
        elif policy_count == 0:
            # RLS activee sans aucune politique = tout est refuse, application comprise. C'est un
            # defaut de configuration meme si, litteralement, rien ne fuit.
            verdict = "ATTENTION (aucune politique)"
            warnings.append(name)
        elif expected_permissive:
            # `FORCE` ne contraint QUE le proprietaire de la table. L'application se connecte avec
            # `aca_app`, qui n'est proprietaire d'aucune des tables LangGraph : la politique
            # permissive s'y applique donc deja pleinement, et exiger `FORCE` ici produirait une
            # alerte permanente sans rien renforcer.
            verdict = "OK (permissive attendue)"
        elif not rls_forced:
            verdict = "ATTENTION (non forcee)"
            warnings.append(name)
        else:
            verdict = "OK"
        print(f"{name:<28} {'oui' if rls_enabled else 'non':<6} "
              f"{'oui' if rls_forced else 'non':<7} {policy_count:<11} {verdict}")

    print()
    if exposed:
        print(f"{len(exposed)} table(s) SANS RLS : {', '.join(exposed)}")
        print("  -> Supabase active la RLS automatiquement sur toute nouvelle table `public` ; une "
              "table sans RLS ici a donc ete creee autrement (migration manuelle, outil tiers).")
    if warnings:
        print(f"{len(warnings)} table(s) a configuration partielle : {', '.join(warnings)}")
        print("  -> RLS activee mais non forcee (le proprietaire lit tout), ou forcee sans "
              "politique (tout est refuse, y compris a l'application).")
    if not exposed and not warnings:
        print("Toutes les tables du schema public sont couvertes (RLS activee + forcee + politique).")

    return 1 if exposed else 0


if __name__ == "__main__":
    sys.exit(main())

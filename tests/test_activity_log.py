"""
Tests du journal d'activité (§17, `aca/storage/activity_log.py`).

Ordre de priorité assumé — les deux premières propriétés valent plus que toutes les autres :

1. **`log()` ne lève jamais.** Ce module s'intercale dans le chemin d'une écriture CRM (le
   gestionnaire du bouton « Valider »). Une exception ici ferait échouer une validation légitime
   pour cause de journal indisponible : le remède serait pire que le mal.
2. **La chaîne détecte une altération.** Un journal d'audit qu'on peut modifier sans laisser de
   trace ne prouve rien ; c'est toute la raison d'être du chaînage (§15.2.7). On vérifie qu'une
   ligne MODIFIÉE **et** une ligne SUPPRIMÉE sont détectées — la seconde est le cas qu'un contrôle
   naïf rate, chaque ligne survivante restant individuellement cohérente.
3. Le cloisonnement par tenant, la fiche par opérateur, la description de poste, et la purge RGPD.
"""
import sqlite3

import pytest

from aca.core.tenant import current_org_id
from aca.storage import activity_log


@pytest.fixture(autouse=True)
def _journal_vierge():
    """
    Vide la table avant chaque test.

    Le chemin de la base est déjà redirigé vers un répertoire temporaire par `conftest.py`, mais il
    est partagé par toute la session pytest : sans ce nettoyage, les compteurs (`actors_summary`,
    `list_recent`) dépendraient de l'ordre d'exécution des tests, et la chaîne d'empreintes
    accumulerait les lignes des tests précédents.
    """
    activity_log.init_db()
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("DELETE FROM activity")
        conn.commit()
    yield


def _ctx(ip="203.0.113.7", ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"):
    return activity_log.build_context(ip_address=ip, user_agent=ua, session_id="s-test",
                                      server_host="POSTE-TEST")


# ── Contrat « ne lève jamais » ────────────────────────────────────────────────────────────────
def test_log_ne_leve_jamais_meme_si_la_base_est_inaccessible(monkeypatch):
    """La propriété la plus importante du module : mieux vaut perdre une ligne de journal que
    faire échouer une validation CRM déjà effectuée."""
    monkeypatch.setattr(activity_log, "DB_PATH", "/chemin/inexistant/interdit/activity.sqlite")
    result = activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin")
    assert result["ok"] is False
    assert result["error"]  # l'échec est rapporté, pas caché


def test_details_non_serialisables_nempechent_pas_la_journalisation():
    """
    `details` vient d'appelants variés ; un objet non JSON ne doit ni lever ni faire perdre la ligne.

    Ce test a trouvé un vrai défaut : la sérialisation avait lieu HORS du `try` de `log()`, si bien
    qu'un `details` invalide levait avant d'entrer dans la protection — le contrat « ne lève jamais »
    était donc faux dans exactement le cas où il compte. Corrigé en sérialisant dans le `try`, avec
    `default=str` : mieux vaut un détail approximatif qu'une entrée d'audit manquante.
    """
    result = activity_log.log(
        activity_log.ACTION_SETTINGS_CHANGED, "alice", "admin", details={"objet": object()},
    )
    assert result["ok"] is True
    assert activity_log.list_recent(limit=1)[0]["action"] == activity_log.ACTION_SETTINGS_CHANGED


def test_contexte_non_textuel_est_quand_meme_journalise():
    """
    Régression : une entrée d'audit ne doit JAMAIS être perdue silencieusement.

    Trouvé lors de la première vérification de bout en bout dans l'UI. `ui.py` lit
    `st.context.ip_address`, dont rien ne garantit que ce soit une chaîne ; la valeur descendait
    jusqu'à SQLite (`Error binding parameter 13`), `log()` attrapait l'exception au titre de son
    contrat « ne lève jamais »… et la ligne « analyse lancée » disparaissait avec, en ne laissant
    qu'une ligne de log serveur. Un journal de sécurité qu'on croit complet et qui ne l'est pas est
    plus dangereux qu'un journal absent.
    """
    class _Opaque:
        """Imite un objet non textuel renvoyé par `st.context` en exécution headless."""

        def __str__(self):
            return "10.1.2.3"

    result = activity_log.log(
        activity_log.ACTION_LOGIN, "alice", "admin",
        context=activity_log.build_context(ip_address=_Opaque(), user_agent=_Opaque()),
    )
    assert result["ok"] is True
    row = activity_log.list_recent(limit=1)[0]
    assert row["ip_address"] == "10.1.2.3"  # convertie, puis validée comme IP
    assert activity_log.verify_chain()["ok"] is True


def test_contexte_opaque_non_ip_ne_pollue_pas_la_colonne():
    """Un objet dont la représentation n'est pas une IP laisse la colonne vide plutôt que d'y
    écrire un `<MagicMock id=…>` — observé tel quel lors de la vérification headless."""
    class _Bruit:
        def __str__(self):
            return "<MagicMock id='140234'>"

    result = activity_log.log(
        activity_log.ACTION_LOGIN, "alice", "admin",
        context=activity_log.build_context(ip_address=_Bruit()),
    )
    assert result["ok"] is True
    assert activity_log.list_recent(limit=1)[0]["ip_address"] == ""


def test_log_reussi_renvoie_un_identifiant():
    result = activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    assert result["ok"] is True and isinstance(result["id"], int)


# ── Chaîne d'empreintes (inviolabilité) ───────────────────────────────────────────────────────
def _remplir(n=5):
    for index in range(n):
        activity_log.log(
            activity_log.ACTION_LEAD_VALIDATED, "alice", "admin", target_type="thread",
            target_id=f"t-{index}", context=_ctx(),
        )


def test_chaine_intacte_apres_plusieurs_ecritures():
    _remplir(5)
    result = activity_log.verify_chain()
    assert result["ok"] is True
    assert result["checked"] == 5


def test_chaine_detecte_une_ligne_modifiee():
    _remplir(5)
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET actor = 'bob' WHERE id = (SELECT MIN(id) FROM activity)")
        conn.commit()
    result = activity_log.verify_chain()
    assert result["ok"] is False
    assert result["first_invalid_id"] is not None


def test_chaine_detecte_une_ligne_supprimee():
    """Le cas qu'un contrôle naïf rate : chaque ligne restante demeure individuellement cohérente,
    seul le raccord `prev_hash` ↔ ligne réellement précédente révèle le trou."""
    _remplir(5)
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        milieu = conn.execute("SELECT id FROM activity ORDER BY id LIMIT 1 OFFSET 2").fetchone()[0]
        conn.execute("DELETE FROM activity WHERE id = ?", (milieu,))
        conn.commit()
    assert activity_log.verify_chain()["ok"] is False


def test_chaine_detecte_un_detail_modifie():
    """`details` entre dans l'empreinte : sinon on pourrait réécrire l'« avant/après » d'un
    changement de réglage sans que rien ne le signale."""
    activity_log.log(
        activity_log.ACTION_SETTINGS_CHANGED, "alice", "admin",
        details={"SUPPORT_EMAIL": {"avant": "a@x.fr", "après": "b@y.fr"}}, context=_ctx(),
    )
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET details = '{}'")
        conn.commit()
    assert activity_log.verify_chain()["ok"] is False


def test_cle_hmac_change_les_empreintes(monkeypatch):
    """Avec la clé, forger une chaîne cohérente exige un secret qui vit hors de la base."""
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        sans_cle = conn.execute("SELECT row_hash FROM activity").fetchone()[0]
    monkeypatch.setenv("ACA_AUDIT_HMAC_KEY", "un-secret-de-test")
    # Les empreintes existantes ne se recalculent plus à l'identique : la vérification échoue, ce
    # qui est le comportement attendu et documenté (faire tourner cette clé = perdre la
    # vérifiabilité de l'existant, cf. docs/DEPLOYMENT_HARDENING.md).
    assert activity_log.verify_chain()["ok"] is False
    activity_log.log(activity_log.ACTION_LOGIN, "bob", "operator", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        avec_cle = conn.execute(
            "SELECT row_hash FROM activity ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert sans_cle != avec_cle


def test_details_serialises_de_maniere_deterministe():
    """Sans `sort_keys=True`, deux exécutions produisant le même dictionnaire écriraient un JSON
    d'ordre différent — une revérification honnête ressemblerait à une falsification."""
    activity_log.log(
        activity_log.ACTION_SETTINGS_CHANGED, "alice", "admin",
        details={"z": 1, "a": 2, "m": 3}, context=_ctx(),
    )
    rows = activity_log.list_recent(limit=1)
    assert rows[0]["details"] == '{"a": 2, "m": 3, "z": 1}'
    assert activity_log.verify_chain()["ok"] is True


# ── Description du poste (fonctions pures) ────────────────────────────────────────────────────
@pytest.mark.parametrize("user_agent,attendu", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
     "Windows 10/11 · Chrome"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605 Version/17 Safari/605",
     "macOS · Safari"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1", "iPhone · Safari"),
    ("Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0", "Linux · Firefox"),
    # « Edg/ » contient « Chrome/ » : l'ordre de détection compte, et c'est Edge qu'il faut nommer.
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36 Edg/120", "Windows 10/11 · Edge"),
])
def test_description_du_poste(user_agent, attendu):
    assert activity_log.describe_user_agent(user_agent) == attendu


@pytest.mark.parametrize("user_agent", ["", None, "curl/8.4.0"])
def test_poste_inconnu_reste_lisible(user_agent):
    """Une chaîne vide rendrait la colonne du tableau illisible ; « Poste inconnu » est honnête."""
    assert activity_log.describe_user_agent(user_agent) == "Poste inconnu"


def test_empreinte_de_poste_stable_et_discriminante():
    ua = "Mozilla/5.0 (Windows NT 10.0) Chrome/120"
    assert activity_log.device_fingerprint("10.0.0.1", ua) == \
           activity_log.device_fingerprint("10.0.0.1", ua)
    assert activity_log.device_fingerprint("10.0.0.1", ua) != \
           activity_log.device_fingerprint("10.0.0.2", ua)
    assert activity_log.device_fingerprint("10.0.0.1", ua).startswith("d-")


def test_build_context_remplit_les_colonnes_attendues():
    ctx = _ctx()
    assert ctx["device_label"] == "Windows 10/11 · Chrome"
    assert ctx["ip_address"] == "203.0.113.7"
    assert ctx["server_host"] == "POSTE-TEST"
    assert ctx["device_id"].startswith("d-")


@pytest.mark.parametrize("brut,attendu", [
    ("203.0.113.7", "203.0.113.7"),
    ("2001:db8::1", "2001:db8::1"),
    # Un `X-Forwarded-For` porte « client, proxy1, proxy2 » : seule la première entrée est le client.
    ("203.0.113.7, 70.41.3.18, 150.172.238.178", "203.0.113.7"),
    ("  203.0.113.7  ", "203.0.113.7"),
    # Valeurs refusées : cette colonne provient d'un en-tête client, donc falsifiable.
    ("<script>alert(1)</script>", ""),
    ("pas-une-ip", ""),
    ("999.999.999.999", ""),
    ("", ""),
    (None, ""),
])
def test_normalisation_des_adresses_ip(brut, attendu):
    """Une colonne « Adresse IP » qui contient parfois du texte quelconque n'est plus exploitable :
    on ne saurait plus distinguer un incident d'un artefact."""
    assert activity_log.normalise_ip(brut) == attendu


def test_user_agent_est_borne():
    """Ne jamais laisser un tiers décider de la taille de ce qu'on stocke (même principe que les
    bornes de charge utile de l'API, §15.1.4)."""
    ctx = activity_log.build_context(user_agent="A" * 5000)
    assert len(ctx["user_agent"]) == activity_log.MAX_USER_AGENT_CHARS


def test_build_context_sans_rien_ne_leve_pas():
    ctx = activity_log.build_context()
    assert ctx["device_label"] == "Poste inconnu"
    assert ctx["ip_address"] == ""


# ── Lecture et filtres ────────────────────────────────────────────────────────────────────────
def test_list_recent_du_plus_recent_au_plus_ancien():
    for index in range(3):
        activity_log.log(activity_log.ACTION_LOGIN, f"user-{index}", "operator", context=_ctx())
    assert [r["actor"] for r in activity_log.list_recent()] == ["user-2", "user-1", "user-0"]


def test_filtres_par_acteur_action_et_issue():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    activity_log.log(activity_log.ACTION_LOGIN_FAILED, "bob", outcome=activity_log.OUTCOME_DENIED,
                     context=_ctx())
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin", context=_ctx())

    assert len(activity_log.list_recent(actor="alice")) == 2
    assert len(activity_log.list_recent(action=activity_log.ACTION_LOGIN_FAILED)) == 1
    assert len(activity_log.list_recent(outcome=activity_log.OUTCOME_DENIED)) == 1


def test_filtre_sensibles_seulement():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    activity_log.log(activity_log.ACTION_USER_ROLE_CHANGED, "alice", "admin", context=_ctx())
    sensibles = activity_log.list_recent(sensitive_only=True)
    assert [r["action"] for r in sensibles] == [activity_log.ACTION_USER_ROLE_CHANGED]


def test_libelle_lisible_et_drapeau_sensible_sont_calcules():
    activity_log.log(activity_log.ACTION_USER_ROLE_CHANGED, "alice", "admin", context=_ctx())
    row = activity_log.list_recent(limit=1)[0]
    assert row["action_label"] == "Rôle modifié"
    assert row["sensitive"] is True


def test_toute_action_declaree_possede_un_libelle():
    """Un journal qu'un manager ne sait pas lire n'est pas consulté, donc pas un contrôle : ce test
    échoue si une action est ajoutée sans son libellé français."""
    actions = {
        value for name, value in vars(activity_log).items()
        if name.startswith("ACTION_") and isinstance(value, str)
    }
    assert actions == set(activity_log.ACTION_LABELS)


def test_distinct_values_refuse_une_colonne_non_filtrable():
    """La colonne est interpolée dans le SQL : cette liste blanche est ce qui empêche l'injection."""
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    assert activity_log.distinct_values("actor") == ["alice"]
    with pytest.raises(ValueError):
        activity_log.distinct_values("row_hash; DROP TABLE activity")


# ── Fiche d'audit par opérateur ───────────────────────────────────────────────────────────────
def test_actors_summary_compte_validations_rejets_et_incidents():
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "operator", context=_ctx())
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "operator", context=_ctx())
    activity_log.log(activity_log.ACTION_LEAD_REJECTED, "alice", "operator", context=_ctx())
    activity_log.log(activity_log.ACTION_LOGIN_FAILED, "alice",
                     outcome=activity_log.OUTCOME_DENIED, context=_ctx())

    ligne = next(r for r in activity_log.actors_summary() if r["actor"] == "alice")
    assert ligne["validations"] == 2
    assert ligne["rejets"] == 1
    assert ligne["incidents"] == 1
    assert ligne["actions"] == 4


def test_actor_profile_liste_les_postes_utilises():
    """La réponse littérale à « depuis quelle machine » : deux postes doivent apparaître
    séparément, avec leur IP et leur volume d'actions."""
    bureau = _ctx("10.0.0.5", "Mozilla/5.0 (Windows NT 10.0) Chrome/120")
    mobile = _ctx("192.0.2.9", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1")
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "operator", context=bureau)
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "operator", context=bureau)
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "operator", context=mobile)

    profile = activity_log.actor_profile("alice")
    assert profile["actions"] == 3
    assert len(profile["postes"]) == 2
    principal = profile["postes"][0]  # trié par volume décroissant
    assert principal["poste"] == "Windows 10/11 · Chrome"
    assert principal["ip"] == "10.0.0.5"
    assert principal["actions"] == 2


def test_actor_profile_sur_un_inconnu_ne_leve_pas():
    profile = activity_log.actor_profile("personne-inexistante")
    assert profile["actions"] == 0 and profile["postes"] == []


# ── Cloisonnement multi-tenant ────────────────────────────────────────────────────────────────
def test_journaux_cloisonnes_par_tenant(monkeypatch):
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    activity_log.log(activity_log.ACTION_LOGIN, "alice-acme", "admin", context=_ctx())
    monkeypatch.setenv("ACA_ORG_ID", "globex")
    activity_log.log(activity_log.ACTION_LOGIN, "bob-globex", "admin", context=_ctx())

    assert [r["actor"] for r in activity_log.list_recent()] == ["bob-globex"]
    monkeypatch.setenv("ACA_ORG_ID", "acme")
    assert [r["actor"] for r in activity_log.list_recent()] == ["alice-acme"]
    # Chaque tenant a sa propre chaîne : celle d'acme reste vérifiable malgré l'écriture de globex.
    assert activity_log.verify_chain()["ok"] is True
    assert current_org_id() == "acme"


# ── Purge RGPD ────────────────────────────────────────────────────────────────────────────────
def test_purge_ne_touche_pas_les_entrees_recentes():
    _remplir(3)
    assert activity_log.purge_older_than(30) == 0
    assert len(activity_log.list_recent()) == 3


def test_purge_supprime_les_entrees_anciennes():
    _remplir(3)
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2020-01-01 00:00:00'")
        conn.commit()
    assert activity_log.purge_older_than(30) == 3
    assert activity_log.list_recent() == []


def test_purge_a_deux_vitesses_garde_plus_longtemps_les_actions_sensibles():
    """§18 — un échec de connexion ancien reste la seule trace d'une tentative d'intrusion : il ne
    doit pas partir à la même échéance qu'une validation de routine."""
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin", context=_ctx())
    activity_log.log(activity_log.ACTION_LOGIN_FAILED, "bob", "", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2020-01-01 00:00:00'")
        conn.commit()

    # `sensitive_days` largement ouvert : seule l'entrée non sensible doit partir.
    deleted = activity_log.purge_older_than(30, sensitive_days=3650)
    assert deleted == 1
    remaining = activity_log.list_recent()
    assert len(remaining) == 1
    assert remaining[0]["action"] == activity_log.ACTION_LOGIN_FAILED


def test_purge_sans_sensitive_days_traite_tout_pareil():
    """Comportement historique préservé quand l'appelant ne demande pas la rétention à deux
    vitesses (ex. `retention.py` avant migration)."""
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin", context=_ctx())
    activity_log.log(activity_log.ACTION_LOGIN_FAILED, "bob", "", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2020-01-01 00:00:00'")
        conn.commit()
    assert activity_log.purge_older_than(30) == 2


# ── Frise chronologique par lead (§18, recap #5) ─────────────────────────────────────────────
def test_lead_timeline_ordre_croissant_et_filtre_sur_le_thread():
    activity_log.log(activity_log.ACTION_ANALYSIS_STARTED, "alice", "admin", target_type="thread",
                     target_id="t-1", context=_ctx())
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin", target_type="thread",
                     target_id="t-1", context=_ctx())
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "bob", "admin", target_type="thread",
                     target_id="t-2", context=_ctx())  # un autre lead, doit être exclu

    rows = activity_log.lead_timeline("t-1")
    assert [r["action"] for r in rows] == [
        activity_log.ACTION_ANALYSIS_STARTED, activity_log.ACTION_LEAD_VALIDATED,
    ]
    assert all(r["target_id"] == "t-1" for r in rows)


def test_lead_timeline_lead_inconnu_renvoie_une_liste_vide():
    assert activity_log.lead_timeline("jamais-vu") == []


# ── Détection de poste inhabituel (§18, recap #3 du §4) ──────────────────────────────────────
def test_is_new_device_vrai_a_la_premiere_apparition():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin",
                     context=_ctx(ip="203.0.113.9"))
    row = activity_log.list_recent(limit=1)[0]
    assert activity_log.is_new_device("alice", row["device_id"], before_id=row["id"]) is True


def test_is_new_device_faux_pour_un_poste_deja_vu():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx(ip="203.0.113.9"))
    activity_log.log(activity_log.ACTION_LEAD_VALIDATED, "alice", "admin",
                     context=_ctx(ip="203.0.113.9"))
    rows = activity_log.list_recent()
    dernier = rows[0]  # le plus récent, même IP/UA que le premier
    assert activity_log.is_new_device("alice", dernier["device_id"], before_id=dernier["id"]) is False


def test_is_new_device_empreinte_vide_nest_jamais_signalee():
    """Un appel API/machine sans navigateur ne doit pas remplir l'écran d'alertes pour un cas
    parfaitement normal."""
    assert activity_log.is_new_device("alice", "", before_id=999) is False


def test_known_devices_regroupe_par_acteur():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx(ip="203.0.113.9"))
    activity_log.log(activity_log.ACTION_LOGIN, "bob", "operator", context=_ctx(ip="198.51.100.1"))
    alice_row = next(r for r in activity_log.list_recent() if r["actor"] == "alice")
    assert alice_row["device_id"] in activity_log.known_devices("alice")
    assert alice_row["device_id"] not in activity_log.known_devices("bob")


# ── Export CSV ────────────────────────────────────────────────────────────────────────────────
def test_csv_export_entete_et_lignes():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    rows = activity_log.list_recent()
    csv_text = activity_log.csv_export(rows)
    lines = csv_text.strip().split("\n")
    assert lines[0].split(",")[0] == "occurred_at"
    assert "alice" in lines[1]


def test_csv_export_liste_vide_ne_produit_que_lentete():
    csv_text = activity_log.csv_export([])
    assert len(csv_text.strip().split("\n")) == 1


# ── Archive mensuelle signée (§18, recap #7) ─────────────────────────────────────────────────
def test_rows_for_period_filtre_par_mois_civil():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2026-06-15 10:00:00'")
        conn.commit()
    assert len(activity_log.rows_for_period(2026, 6)) == 1
    assert len(activity_log.rows_for_period(2026, 7)) == 0


def test_rows_for_period_gere_le_changement_dannee():
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2025-12-31 23:59:59'")
        conn.commit()
    assert len(activity_log.rows_for_period(2025, 12)) == 1


def test_archive_period_ecrit_le_csv_et_son_empreinte(tmp_path):
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2026-05-10 08:00:00'")
        conn.commit()

    result = activity_log.archive_period(str(tmp_path), 2026, 5)
    assert result["skipped"] is False
    assert result["lines"] == 1
    assert (tmp_path / "activite-2026-05.csv").exists()
    assert (tmp_path / "activite-2026-05.csv.sha256").exists()

    from aca.storage.tamper_chain import digest
    payload = (tmp_path / "activite-2026-05.csv").read_text(encoding="utf-8-sig")
    assert (tmp_path / "activite-2026-05.csv.sha256").read_text(encoding="utf-8").split()[0] \
        == digest(payload)


def test_archive_period_est_idempotent(tmp_path):
    """Un travail planifié qui repasse deux fois ne doit jamais remplacer une archive existante par
    une version amputée après une purge — la preuve serait détruite."""
    activity_log.log(activity_log.ACTION_LOGIN, "alice", "admin", context=_ctx())
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("UPDATE activity SET occurred_at = '2026-05-10 08:00:00'")
        conn.commit()

    first = activity_log.archive_period(str(tmp_path), 2026, 5)
    original_csv = (tmp_path / "activite-2026-05.csv").read_text(encoding="utf-8-sig")

    # Purge simulée entre les deux passages planifiés : la ligne d'origine disparaît de la base.
    with sqlite3.connect(activity_log.DB_PATH) as conn:
        conn.execute("DELETE FROM activity")
        conn.commit()

    second = activity_log.archive_period(str(tmp_path), 2026, 5)
    assert second["skipped"] is True
    assert (tmp_path / "activite-2026-05.csv").read_text(encoding="utf-8-sig") == original_csv

"""
Aides partagées par toutes les pages de l'UI Streamlit (§18).

Extrait de `ui.py`, qui vivait jusqu'ici comme un fichier unique de 1700 lignes portant à la fois
la logique de session/audit et le contenu de chaque onglet. Le manque : rien n'empêchait deux
onglets de diverger sur « qui est connecté » ou « qui a le droit de faire quoi » puisque chacun
relisait `st.session_state` directement. Un point d'entrée unique (`check_auth`/`can`/`audit`) rend
cette logique testable indépendamment du rendu et partageable par les futures pages de
`app_pages/*.py` sans dupliquer le gate d'authentification.

`st.session_state` est partagé par TOUTES les pages d'une même session `st.navigation` — ces
fonctions restent donc valides telles quelles après la découpe en pages, sans adaptation.
"""
import hmac
import os
import time
import traceback
import uuid

import streamlit as st
import streamlit.components.v1 as components
from langchain_core.callbacks import UsageMetadataCallbackHandler

from aca.core import app as aca_graph
from aca.core import branding
from aca.core import device_trust
from aca.core import graph_topology
from aca.core import i18n
from aca.core import session as aca_session
from aca.core.auth_lockout import lockout_remaining_seconds, next_lockout_seconds
from aca.storage import activity_log, analytics_store, device_trust_store, user_store

DEV_USERNAME = "(dev)"  # identité de repli quand aucun compte n'existe (mode développement)


# ── Langue de l'interface (chrome principal, sur demande explicite) ─────────────────────────
def current_language() -> str:
    """
    Langue courante de l'UI. Par session (`st.session_state`), pas par compte : un compte
    partagé (mode développement/secret partagé) n'a de toute façon pas de ligne `user_store` où
    stocker une préférence, et une bascule par onglet reste le choix le plus prévisible pour un
    poste multi-utilisateurs (le prochain rerun repart sur la langue par défaut sans y penser).
    """
    return st.session_state.get("_lang", i18n.DEFAULT_LANGUAGE)


def t(key: str, **kwargs) -> str:
    """Raccourci de traduction pour les pages : `t("nav.inbox")`. Lit la langue courante et
    délègue à `i18n.translate` — voir sa docstring pour le contrat sur les clés absentes."""
    return i18n.translate(key, current_language(), **kwargs)


def language_switcher() -> None:
    """
    Sélecteur FR/EN. Appelé depuis `ui.py` **avant** la construction de la liste de pages
    (`st.Page(title=t("nav.…"))`) pour que le libellé des onglets de navigation reflète
    immédiatement le choix — pas seulement le contenu des pages, rendu après coup.
    """
    current = current_language()
    choice = st.segmented_control(
        t("lang.switch_label"), options=list(i18n.LANGUAGES),
        format_func=lambda code: i18n.LANGUAGE_LABELS.get(code, code),
        default=current, key="_lang_switcher", label_visibility="collapsed",
    )
    if choice and choice != current:
        st.session_state["_lang"] = choice
        st.rerun()


def client_context() -> dict:
    """
    Contexte « d'où vient cette action » pour le journal d'activité (§17) : IP vue par le serveur,
    user-agent, et un identifiant d'onglet propre à cette session Streamlit.

    `st.context` n'est renseigné que pendant l'exécution d'un script et peut être vide selon le
    mode de lancement (exécution headless, hors navigateur) : tout est enveloppé, car une
    journalisation qui échouerait faute d'en-tête `User-Agent` serait un comble.
    """
    ip = user_agent = ""
    try:
        # `str(...)` explicite : rien ne garantit que `st.context` renvoie des chaînes (objet
        # fantôme en exécution headless, évolution possible de l'API). Sans cette conversion, la
        # valeur descend jusqu'à SQLite qui refuse de la lier, `activity_log.log()` attrape
        # l'exception au titre de son contrat « ne lève jamais », et l'entrée d'audit disparaît en
        # silence — constaté lors de la première vérification de bout en bout. Le magasin se
        # protège aussi de son côté ; la double garantie est voulue pour un journal de sécurité.
        ip = str(st.context.ip_address or "")
        user_agent = str((st.context.headers or {}).get("User-Agent", "") or "")
    except Exception:  # noqa: BLE001
        ip = user_agent = ""
    # Identifiant d'onglet : recréé à chaque nouvelle session de navigateur, il relie entre elles
    # les actions d'une même connexion sans rien déposer chez l'utilisateur.
    if "client_session_id" not in st.session_state:
        st.session_state.client_session_id = "s-" + uuid.uuid4().hex[:12]
    return activity_log.build_context(
        ip_address=ip, user_agent=user_agent, session_id=st.session_state.client_session_id,
    )


def audit(action: str, *, actor: str = None, role: str = None, **kwargs) -> None:
    """
    Consigne une action au journal d'activité avec l'identité de la session courante.

    Enveloppe volontairement fine autour de `activity_log.log` : elle évite seulement de répéter
    « qui » et « d'où » à chacun des points d'appel de ce fichier. Le contrat « ne lève jamais » est
    celui du magasin lui-même, pas une garantie ajoutée ici.
    """
    session = st.session_state.get("session") or {}
    activity_log.log(
        action,
        actor=actor or session.get("username") or "(anonyme)",
        actor_role=role or session.get("role"),
        context=client_context(),
        source=activity_log.SOURCE_STREAMLIT,
        **kwargs,
    )


def _register_failure(username: str = None) -> None:
    """
    Incrémente le compteur d'échecs, arme le verrou progressif (US-41) le cas échéant, et **consigne
    la tentative** (§17).

    Cette journalisation est la principale raison d'être du journal d'activité côté sécurité :
    jusqu'ici le verrou de `auth_lockout.py` bloquait un attaquant en silence, sans que personne ne
    puisse jamais constater qu'une série de tentatives avait eu lieu — donc sans qu'un incident
    puisse être détecté, daté, ni rattaché à une adresse.
    """
    st.session_state.auth_failed_attempts += 1
    attempts = st.session_state.auth_failed_attempts
    lockout = next_lockout_seconds(attempts)
    # L'identifiant essayé est consigné, jamais le mot de passe : savoir QUEL compte est visé est
    # l'information utile ; le secret tenté ne l'est pas et n'a rien à faire dans une base.
    audit(
        activity_log.ACTION_LOGIN_FAILED, actor=(username or "").strip() or "(inconnu)", role="",
        outcome=activity_log.OUTCOME_DENIED, target_type="compte",
        target_id=(username or "").strip(), details={"tentatives_consécutives": attempts},
    )
    if lockout > 0:
        st.session_state.auth_locked_until = time.time() + lockout
        audit(
            activity_log.ACTION_LOCKED_OUT, actor=(username or "").strip() or "(inconnu)", role="",
            outcome=activity_log.OUTCOME_DENIED,
            details={"secondes": int(lockout), "tentatives_consécutives": attempts},
        )
        st.error(t("auth.incorrect_locked", seconds=int(lockout)), icon=":material/error:")
    else:
        st.error(t("auth.incorrect"), icon=":material/error:")


def _open_session(username: str, role: str) -> None:
    """Ouvre la session, consigne la connexion (§17) et rejoue le script (le reste de la page
    s'affiche alors authentifié)."""
    st.session_state.session = aca_session.new_session(username, role, time.time())
    st.session_state.auth_failed_attempts = 0
    audit(activity_log.ACTION_LOGIN, actor=username, role=role)
    st.rerun()


# §18, recap #6 — avertir AVANT l'expiration silencieuse, pas seulement l'expliquer après coup :
# `session.py` connaît déjà les deux bornes, il manquait le rappel. Un `st.toast` est éphémère et
# Streamlit rejoue tout le script à CHAQUE interaction : sans throttle, taper un caractère dans un
# champ toasterait à nouveau, noyant l'avertissement dans du bruit au lieu d'aider.
_EXPIRY_WARNING_SECONDS = 300  # avertir dans les 5 dernières minutes
_EXPIRY_TOAST_INTERVAL_SECONDS = 60  # ne pas re-rappeler plus d'une fois par minute


def _warn_before_expiry(session: dict, now: float) -> None:
    remaining = aca_session.seconds_until_expiry(session, now)
    if remaining is None or remaining > _EXPIRY_WARNING_SECONDS:
        return
    last_toast = st.session_state.get("_expiry_toast_at", 0)
    if now - last_toast < _EXPIRY_TOAST_INTERVAL_SECONDS:
        return
    st.session_state["_expiry_toast_at"] = now
    minutes = max(1, int(remaining // 60))
    st.toast(t("auth.expiry_warning", minutes=minutes), icon=":material/timer:")


# §18, recap #9 — second facteur TOTP pour les comptes `admin`. `check_auth` ne l'impose qu'aux
# comptes NOMINATIFS (`accounts_exist`) : le mode secret partagé ouvre une identité anonyme
# `DEV_USERNAME` sans ligne `user_store` propre, contre laquelle il n'y a rien de sensé à stocker.
def _clear_totp_pending() -> None:
    for key in ("_totp_pending_username", "_totp_pending_role", "_totp_enroll_secret"):
        st.session_state.pop(key, None)


# ── §24 : appareils de confiance ────────────────────────────────────────────────────────────────
# Le raisonnement de sécurité complet est dans `aca/core/device_trust.py`. Ici, uniquement la
# plomberie Streamlit : lire le cookie, le poser, et décider s'il faut afficher l'écran du code.

def _device_token() -> str:
    """
    Jeton d'appareil, `""` s'il n'y en a pas. DEUX sources, et il en faut bien deux.

    `st.context.cookies` est figé au HANDSHAKE de la session : un cookie déposé pendant la session
    en cours y reste invisible, même après plusieurs reruns — mesuré, pas supposé (une page sonde
    montre le cookie présent côté navigateur, absent côté serveur, et visible seulement après un
    rechargement complet de la page). C'est très exactement le cas « je me déconnecte puis je me
    reconnecte » sans recharger l'onglet : même session Streamlit, donc cookie illisible, donc code
    redemandé alors que la case avait bien été cochée. C'était le défaut signalé.

    D'où le repli sur `st.session_state`, qui survit à la déconnexion (celle-ci ne vide que la clé
    `session`) : il couvre le retour dans le MÊME onglet, là où le cookie ne peut rien ; le cookie,
    lui, couvre le rechargement, le nouvel onglet et le redémarrage du navigateur, là où
    `session_state` a disparu. Les deux ensemble couvrent tous les cas ; aucun des deux seul n'y
    suffit.

    Le jeton conservé en `session_state` ne quitte jamais le serveur : il n'ajoute aucune exposition
    et reste, comme le cookie, sans valeur sans le mot de passe.
    """
    from_state = st.session_state.get("_device_token") or ""
    if from_state:
        return str(from_state)
    try:
        return str(st.context.cookies.get(device_trust.COOKIE_NAME, "") or "")
    except Exception:  # noqa: BLE001
        # `st.context` peut être vide selon le mode de lancement (headless, hors navigateur) : ne
        # pas réussir à lire un cookie doit mener à redemander le code, jamais à faire échouer la
        # connexion.
        return ""


def flush_device_cookie() -> None:
    """
    Exécute l'écriture (ou l'effacement) de cookie mise en attente par la passe précédente.

    Streamlit n'offre aucune écriture de cookie côté Python ; une iframe `components.html` hérite
    en revanche de l'origine de la page et peut écrire dans le `document.cookie` du parent —
    vérifié en conditions réelles avant d'écrire ce code (cf. `docs/PROJECT_JOURNAL.md`), et non
    déduit de la documentation.

    POURQUOI UNE MISE EN ATTENTE plutôt qu'une écriture au moment du clic : `_open_session()` et le
    bouton de déconnexion appellent tous deux `st.rerun()` immédiatement après, ce qui interrompt
    le script et jette le rendu en cours. Un composant émis juste avant ne serait donc jamais
    exécuté par le navigateur — la case aurait été cochée, l'enregistrement écrit côté serveur, et
    le cookie n'aurait jamais existé : une panne parfaitement muette, du genre que seul un essai
    réel révèle. On repousse donc l'émission à la passe suivante, qui elle va jusqu'au bout.

    `height=0` : ces composants n'ont rien à montrer, ils agissent.
    """
    token = st.session_state.pop("_device_cookie_pending", None)
    if token:
        components.html(device_trust.cookie_script(token, device_trust.trust_seconds()), height=0)
    if st.session_state.pop("_device_cookie_clear", False):
        components.html(device_trust.forget_script(), height=0)


def forget_device_on_this_browser() -> None:
    """Révoque l'autorisation de CE navigateur (déconnexion « oublier cet appareil »)."""
    token = _device_token()
    if token:
        device_trust_store.revoke_token(token)
    # Les deux sources, sinon « oublier cet appareil » n'oublierait que la moitié : le cookie part,
    # et la copie en session continuerait de faire sauter le code jusqu'à la fermeture de l'onglet.
    st.session_state.pop("_device_token", None)
    st.session_state["_device_cookie_clear"] = True


def _remember_this_device(username: str, user_agent: str, ip: str) -> None:
    """Émet un jeton, l'enregistre côté serveur, met le cookie en attente et consigne le tout."""
    token = device_trust.new_token()
    # Étiquette purement descriptive, pour qu'une ligne de « Appareils de confiance » soit
    # reconnaissable par son propriétaire. Volontairement grossière : le navigateur et l'adresse
    # suffisent à répondre « est-ce mon poste du bureau ? », et rien de plus fin n'aiderait.
    label = f"{(user_agent or '?').split(')')[0][:60]} · {ip or 'IP inconnue'}"
    expiry = device_trust_store.remember(
        username,
        token,
        auth_fingerprint=user_store.auth_state_fingerprint(username),
        user_agent=user_agent,
        label=label,
    )
    st.session_state["_device_cookie_pending"] = token
    # Gardé aussi côté session : le cookie qu'on vient de poser sera illisible jusqu'au prochain
    # chargement de page (cf. `_device_token`), donc sans cette ligne une déconnexion suivie d'une
    # reconnexion dans le même onglet redemanderait le code.
    st.session_state["_device_token"] = token
    audit(
        activity_log.ACTION_DEVICE_TRUSTED,
        actor=username,
        details={"jours": device_trust.trust_days(), "appareil": label},
    )
    st.session_state["_device_trusted_until"] = expiry


def _trusted_device_accepted(username: str) -> bool:
    """
    Ce navigateur est-il déjà autorisé à sauter le code pour ce compte ?

    Un refus est silencieux et sans conséquence : on affiche simplement l'écran du code. Aucun
    compteur d'échec n'est touché — un cookie périmé n'est pas une tentative d'intrusion, et les
    confondre verrouillerait des utilisateurs parfaitement légitimes (cf.
    `device_trust_store.verify`).
    """
    if not device_trust.is_enabled():
        return False
    token = _device_token()
    if not token:
        return False
    return device_trust_store.verify(
        username,
        token,
        auth_fingerprint=user_store.auth_state_fingerprint(username),
        user_agent=client_context().get("user_agent", ""),
    )


def _totp_qr_png(uri: str) -> bytes:
    """
    QR code (PNG bytes) for a TOTP provisioning URI.

    Retyping a 32-character base32 secret by hand is exactly the kind of friction that makes people
    abandon a second factor — every real authenticator app expects to scan a code instead.
    `segno` (pinned in requirements.txt) is pure Python with **zero dependencies**: it encodes and
    rasterises the QR itself, so this doesn't pull in Pillow — present in the venv, but only as a
    transitive dependency of Streamlit, the same trap §15.3.8 already flagged once for
    `cryptography`. Deliberately kept out of `totp.py`, whose whole point is staying stdlib-only for
    the actual crypto; rendering a QR code is a presentation concern of the UI layer, not the TOTP
    algorithm itself.
    """
    import io

    import segno

    buffer = io.BytesIO()
    segno.make(uri, error="m").save(buffer, kind="png", scale=6, border=2)
    return buffer.getvalue()


def _handle_totp_step(username: str, role: str, brand: dict, now: float) -> bool:
    """
    Deuxième étape de connexion pour un compte dont le rôle exige un second facteur.

    Deux chemins distincts, jamais confondus : un compte déjà inscrit voit un simple champ de
    code (`has_totp`) ; un compte qui ne l'est pas encore est mis en inscription forcée avant
    l'ouverture de la session — imposer TOTP sans jamais faire inscrire personne serait une
    protection de façade.
    """
    from aca.core import totp as totp_module  # import différé, même raison que user_store.verify_totp

    if user_store.has_totp(username):
        st.info(t("totp.verify_prompt", username=username), icon=":material/verified_user:")
        code = st.text_input(t("totp.code_label"), key="totp_code_input")
        # §24 — la case n'apparaît que si le déploiement l'autorise (`ACA_TOTP_TRUST_DAYS=0` la
        # retire entièrement plutôt que de l'afficher désactivée : une case cochable sans effet est
        # pire qu'une case absente). Non cochée par défaut — un compromis de sécurité se choisit,
        # il ne se subit pas par distraction.
        remember = False
        if device_trust.is_enabled():
            remember = st.checkbox(
                t("totp.remember_device", days=device_trust.trust_days()),
                key="totp_remember_device",
                help=t("totp.remember_help"),
            )
        col_verify, col_cancel = st.columns(2)
        if col_verify.button(t("totp.verify_button"), type="primary"):
            if user_store.verify_totp(username, code, now=now):
                # Mémorisé APRÈS un code valide, jamais avant : sans quoi un mot de passe seul
                # suffirait à se faire mémoriser, et la case supprimerait le second facteur au
                # lieu de l'espacer.
                if remember and device_trust.is_enabled():
                    context = client_context()
                    _remember_this_device(
                        username, context.get("user_agent", ""), context.get("ip_address", ""),
                    )
                _clear_totp_pending()
                _open_session(username, role)
            else:
                # Même verrou progressif que le mot de passe (§14, US-41) : un second facteur
                # qu'on peut essayer sans limite n'en est plus un.
                _register_failure(username)
        if col_cancel.button(t("totp.cancel_button")):
            _clear_totp_pending()
            st.rerun()
        return False

    # Rôle exigeant TOTP mais compte pas encore inscrit : inscription forcée avant d'ouvrir la
    # session. Le secret est généré une fois puis conservé en session tant qu'il n'est pas
    # confirmé par un premier code valide — il n'est écrit dans `user_store` qu'à ce moment-là,
    # jamais avant (un secret généré mais jamais confirmé ne doit pas protéger un compte que son
    # titulaire n'a en réalité jamais configuré).
    secret = st.session_state.get("_totp_enroll_secret")
    if not secret:
        secret = totp_module.generate_secret()
        st.session_state["_totp_enroll_secret"] = secret
    st.warning(t("totp.enroll_prompt"), icon=":material/security:")
    uri = totp_module.provisioning_uri(secret, username, brand.get("BRAND_NAME", "ACA"))
    st.image(_totp_qr_png(uri), width=220)
    with st.expander(t("totp.manual_entry_expander")):
        st.code(totp_module.grouped_secret(secret), language=None)
    code = st.text_input(t("totp.code_label"), key="totp_enroll_code_input")
    col_activate, col_cancel = st.columns(2)
    if col_activate.button(t("totp.activate_button"), type="primary"):
        if totp_module.verify(secret, code, now=now):
            user_store.set_totp_secret(username, secret)
            _clear_totp_pending()
            _open_session(username, role)
        else:
            st.error(t("totp.invalid_code"), icon=":material/error:")
    if col_cancel.button(t("totp.cancel_enrollment_button")):
        _clear_totp_pending()
        st.rerun()
    return False


def check_auth(brand: dict) -> bool:
    """
    Contrôle d'accès de l'UI, en trois modes selon ce qui est configuré :

    1. **Comptes nominatifs** (`user_store.has_users()`) — identifiant + mot de passe haché, rôle
       `admin`/`operator` (§15.1.6). C'est le mode recommandé : le journal d'audit devient
       attribuable et les fonctions d'administration (réglages, curation de la base de
       connaissances, comptes) sont réellement séparées de la validation quotidienne.
    2. **Secret partagé** (`ACA_UI_PASSWORD`, aucun compte créé) — l'ancien gate, conservé pour ne
       pas casser les déploiements existants ; il ouvre une session `admin` anonyme.
    3. **Ouvert** (ni compte ni mot de passe) — mode développement, inchangé.

    §14 (US-41) : verrou progressif après plusieurs échecs (`aca.core.auth_lockout`), sans lui un
    bot pouvait tester des mots de passe aussi vite que Streamlit rejoue le script.
    §15.1.7 : la session porte désormais un TTL absolu et un délai d'inactivité
    (`aca.core.session`) — avant, `authed = True` restait vrai tant que l'onglet vivait.

    `brand` est passé par l'appelant (déjà résolu une fois par rerun dans `ui.py`) plutôt que
    résolu ici une seconde fois : une seule source de vérité pour la palette affichée sur cet écran.
    """
    now = time.time()
    # §24 — une écriture de cookie mise en attente par la passe précédente s'exécute ici, en tête :
    # c'est le seul point traversé aussi bien par un utilisateur authentifié que par l'écran de
    # connexion, donc le seul qui couvre à la fois « mémoriser » et « oublier ».
    flush_device_cookie()
    current = st.session_state.get("session")
    if current:
        reason = aca_session.expiry_reason(current, now)
        if reason is None:
            # AVANT `touch()` : celui-ci repousse `last_seen` à `now`, ce qui remettrait toujours
            # le compteur d'inactivité au maximum et rendrait l'avertissement d'inactivité
            # inatteignable (la borne TTL absolue, elle, n'est pas affectée par `touch()`).
            _warn_before_expiry(current, now)
            aca_session.touch(current, now)
            return True
        audit(
            activity_log.ACTION_SESSION_EXPIRED,
            actor=current.get("username"), role=current.get("role"),
            details={"motif": "inactivité" if reason == "idle" else "durée maximale"},
        )
        st.session_state.session = None
        st.session_state.expired_reason = reason

    accounts_exist = user_store.has_users()
    shared_password = os.getenv("ACA_UI_PASSWORD")
    if not accounts_exist and not shared_password:
        st.session_state.session = aca_session.new_session(DEV_USERNAME, user_store.ROLE_ADMIN, now)
        return True

    st.session_state.setdefault("auth_failed_attempts", 0)
    st.session_state.setdefault("auth_locked_until", 0.0)

    # L'écran de connexion porte la même marque que l'application : c'est le premier écran qu'un
    # client voit, et un bandeau générique y annulerait tout le paramétrage d'apparence.
    st.html(branding.hero_html(brand))

    # §28 — la signature de l'agence, juste sous le bandeau. Placée ICI et non en bas de la
    # fonction parce que l'écran de connexion a plusieurs sorties anticipées (verrouillage, second
    # facteur) : en bas, elle manquerait précisément sur les écrans où l'on reste le plus longtemps.
    # C'est aussi le seul écran que TOUS les rôles voient avant quoi que ce soit d'autre.
    _mark = branding.agency_mark_html(branding.resolve_agency(), prefix=t("footer.installed_by"))
    if _mark:
        st.html('<div class="aca-footer" style="border-top:none;margin-top:0;padding-top:.2rem">'
                f'{_mark}</div>')

    expired = st.session_state.pop("expired_reason", None)
    if expired == "idle":
        st.info(t("auth.session_idle_expired"), icon=":material/timer_off:")
    elif expired == "absolute":
        st.info(t("auth.session_expired"), icon=":material/timer_off:")

    remaining = lockout_remaining_seconds(st.session_state.auth_locked_until, now)
    if remaining > 0:
        st.error(t("auth.locked_out", seconds=int(remaining) + 1), icon=":material/lock_clock:")
        return False

    if accounts_exist:
        # Étape 2/2 (§18, recap #9) : un mot de passe déjà vérifié attend son second facteur.
        # Reste en session tant que non résolu — Streamlit rejoue tout le script à chaque
        # interaction, donc c'est le seul moyen de « se souvenir » qu'on est entre les deux étapes.
        pending_username = st.session_state.get("_totp_pending_username")
        if pending_username:
            return _handle_totp_step(
                pending_username, st.session_state.get("_totp_pending_role"), brand, now,
            )

        username = st.text_input(t("auth.username"))
        pwd = st.text_input(t("auth.password"), type="password")
        if st.button(t("auth.login_button"), type="primary"):
            # `verify_credentials` compare en temps constant ET consomme le même temps de calcul
            # sur un compte inexistant (cf. `_DUMMY_HASH`) : ni le mot de passe, ni la liste des
            # identifiants valides ne fuient par chronométrage.
            account = user_store.verify_credentials(username, pwd)
            if account:
                if user_store.totp_required(account["role"]):
                    # §24 — le mot de passe vient d'être vérifié ; si ce navigateur porte une
                    # autorisation valide, on saute le CODE, jamais le mot de passe. Le saut est
                    # consigné : c'est la contrepartie du confort, et la seule façon qu'un
                    # administrateur puisse constater après coup depuis où le second facteur n'a
                    # pas été demandé.
                    if _trusted_device_accepted(account["username"]):
                        audit(
                            activity_log.ACTION_TOTP_SKIPPED,
                            actor=account["username"], role=account["role"],
                            details={"motif": "appareil de confiance"},
                        )
                        _open_session(account["username"], account["role"])
                    else:
                        st.session_state["_totp_pending_username"] = account["username"]
                        st.session_state["_totp_pending_role"] = account["role"]
                        st.rerun()
                else:
                    _open_session(account["username"], account["role"])
            else:
                _register_failure(username)
        return False

    pwd = st.text_input(t("auth.password"), type="password")
    if st.button(t("auth.login_button"), type="primary"):
        # Comparaison à temps constant : un `==` sur un secret fuit sa longueur/préfixe par timing.
        if hmac.compare_digest(pwd.encode(), shared_password.encode()):
            _open_session(DEV_USERNAME, user_store.ROLE_ADMIN)
        else:
            _register_failure(DEV_USERNAME)
    return False


def current_user() -> dict:
    """Session authentifiée courante (`username`, `role`, horodatages)."""
    return st.session_state.get("session") or {}


def can(permission: str) -> bool:
    """L'utilisateur connecté détient-il `permission` ? (cf. `user_store.ROLE_PERMISSIONS`)"""
    return user_store.can(current_user().get("role"), permission)


def audit_denied(permission: str, surface: str) -> None:
    """
    Consigne UNE FOIS par session qu'un utilisateur a atteint une surface interdite à son rôle.

    La déduplication n'est pas une optimisation, c'est ce qui rend l'événement lisible : Streamlit
    réexécute le corps de TOUS les onglets à chaque interaction, donc un `log()` posé directement
    dans la branche « réservé aux administrateurs » écrirait une ligne à chaque clic n'importe où
    dans l'application. Le journal se remplirait de centaines d'entrées identiques et l'information
    utile — « cette personne s'est heurtée à cette limite » — y serait noyée.
    """
    seen = st.session_state.setdefault("audited_denials", set())
    if permission in seen:
        return
    seen.add(permission)
    audit(
        activity_log.ACTION_PERMISSION_DENIED, outcome=activity_log.OUTCOME_DENIED,
        target_type="surface", target_id=surface, details={"permission_requise": permission},
    )


def safe_error(message: str, exc: Exception) -> None:
    """
    Affiche un message d'erreur générique et journalise le détail côté serveur (§15.3.2).

    Interpoler `{exc}` dans l'UI, comme le faisait ce fichier, recopie à l'écran le texte brut de
    l'exception — qui, pour une erreur d'API, contient régulièrement l'URL appelée, des en-têtes,
    voire un fragment de clé. Le détail complet reste disponible dans la console qui a lancé
    Streamlit ; l'utilisateur, lui, voit une phrase actionnable.
    """
    print(f"[ACA] {message}: {exc.__class__.__name__}: {exc}")
    traceback.print_exc()
    st.error(f"{message}. Détail technique consigné côté serveur.", icon=":material/error:")


# ── Interaction avec le graphe LangGraph (§18) ───────────────────────────────────────────────────
# Partagées par la page « Nouvel e-mail » (app_pages/1_inbox.py) ET par le bouton « Ouvrir » de la
# file d'attente dans la barre latérale de `ui.py` — ce dernier reste dans le routeur principal
# (rendu sur toutes les pages), donc `load_queued_thread` doit être importable depuis les deux.

# Étapes du graphe LangGraph — libellés affichés en direct pendant le stream (par nom de nœud).
NODE_STEPS = {
    "ingestion": ("Extraction des pièces jointes", ":material/attach_file:"),
    "classifier": ("Classification de l'e-mail", ":material/label:"),
    "memory_lookup": ("Mémoire CRM : historique de l'expéditeur", ":material/history:"),
    "extractor": ("Extraction des informations clés", ":material/data_object:"),
    "clarification": ("Vérification — besoin d'une précision ?", ":material/help:"),
    "supervisor": ("Le superviseur oriente l'équipe d'agents", ":material/hub:"),
    "enrichissement": ("Agent Enrichissement : profil entreprise", ":material/travel_explore:"),
    "connaissance": ("Agent Connaissance : RAG sémantique (base de connaissances)", ":material/search:"),
    "veille": ("Agent Veille : recherche web (FAQ sans correspondance)", ":material/travel_explore:"),
    "stratege": ("Agent Stratège : rédaction de la proposition", ":material/edit_note:"),
    "routing": ("Routage vers l'équipe compétente (SUPPORT/AUTRE)", ":material/forward_to_inbox:"),
    "notification": ("Notification de l'équipe commerciale", ":material/notifications_active:"),
}

CATEGORY_STYLE = {
    "DEMANDE_DEMO": ("green", ":material/videocam:"),
    "DEVIS": ("blue", ":material/request_quote:"),
}

# Graphe visuel (§12 item 5) : rendu via st.graphviz_chart (viz.js côté navigateur, aucune
# dépendance système Graphviz requise).
#
# §16.1.6 — la topologie n'est plus recopiée ici : elle est DÉRIVÉE du graphe compilé par
# [graph_topology.py](aca/core/graph_topology.py). La copie manuelle qui vivait à cet endroit avait
# déjà divergé du vrai graphe (il lui manquait l'arête `supervisor → routing`, le chemin FINISH du
# superviseur), exactement le risque signalé au §12bis : le schéma affiché montrait un superviseur
# sans issue vers la suite du pipeline. Ajouter un nœud dans `app.py` suffit désormais à le voir
# apparaître ici, sans rien resynchroniser.
GRAPH_FIXED_NODES = graph_topology.FIXED_NODES


def build_graph_dot(current: str = None, done: set = frozenset()) -> str:
    """Construit le graphe LangGraph en DOT, nœud actif en surbrillance, nœuds déjà passés en vert."""
    return graph_topology.to_dot(current=current, done=done)


def completed_graph_nodes(res: dict) -> set:
    """Nœuds déjà traversés pour l'état courant (post-pause) — pipeline fixe + workers effectivement appelés."""
    done = set(GRAPH_FIXED_NODES)
    completed_agents = res.get("completed_agents") or []
    done.update(a for a in completed_agents if a in graph_topology.NODE_LABELS)
    if "stratege" in completed_agents:
        done.add("reflection")
    return done


def advance_graph(payload):
    """
    Fait avancer le graphe (entrée initiale, ou `Command(resume=...)` après une clarification),
    en affichant la progression nœud par nœud, puis met à jour l'état de session :
    - `result` : valeurs courantes de l'état ;
    - `pending_clarification` : la question en attente (si le graphe s'est arrêté sur un interrupt
      dynamique), sinon None (le graphe est alors en pause avant `action`, prêt pour « Valider »).
    """
    # Callback de comptage de tokens (§13 item 4, "Quota Usage Tracker" des PDF "ACAM v2
    # Blueprint") : partagé par tous les appels LLM de ce segment de graphe (fast_llm/smart_llm/
    # creative_llm), agrégé et journalisé une fois le stream terminé.
    usage_handler = UsageMetadataCallbackHandler()
    config = {"configurable": {"thread_id": st.session_state.thread_id}, "callbacks": [usage_handler]}
    with st.status("Analyse en cours...", expanded=True) as status:
        graph_slot = st.empty()
        graph_slot.graphviz_chart(build_graph_dot(), width="stretch")
        # Pastille pulsée aux couleurs de la marque (§17) : le graphe Graphviz se redessine par
        # à-coups à chaque nœud, ce qui donne l'impression que rien ne se passe entre deux étapes.
        # Une animation continue montre que le travail est en cours pendant les appels au 70B, qui
        # sont les plus longs.
        pulse_slot = st.empty()
        pulse_slot.html(
            '<div style="display:flex;align-items:center;gap:.5rem;font-size:.85rem;">'
            '<span class="aca-pulse"></span><span>L\'équipe d\'agents travaille…</span></div>'
        )
        seen_nodes = set()  # le superviseur repasse plusieurs fois → chaque étape affichée une seule fois
        for step in aca_graph.app.stream(payload, config=config, stream_mode="updates"):
            node_name = next(iter(step))
            if node_name == "__interrupt__" or node_name in seen_nodes:
                continue
            seen_nodes.add(node_name)
            label, icon = NODE_STEPS.get(node_name, (node_name, ":material/bolt:"))
            st.write(f"{icon} {label}")
            graph_slot.graphviz_chart(build_graph_dot(current=node_name, done=seen_nodes), width="stretch")
        graph_slot.graphviz_chart(build_graph_dot(done=seen_nodes), width="stretch")
        pulse_slot.empty()
        status.update(label="Analyse terminée", state="complete")

    tokens_in, tokens_out = aca_graph.sum_usage(usage_handler.usage_metadata)
    analytics_store.record_tokens(st.session_state.thread_id, tokens_in, tokens_out)
    sync_result(st.session_state.thread_id)


def sync_result(thread_id: str) -> None:
    """Recharge `result`/`pending_clarification` depuis l'état courant du graphe pour ce thread."""
    config = {"configurable": {"thread_id": thread_id}}
    state = aca_graph.app.get_state(config)
    st.session_state.result = state.values
    # `state.interrupts` non vide = clarification dynamique en attente ; sinon pause avant action.
    st.session_state.pending_clarification = state.interrupts[0].value if state.interrupts else None

    # Tableau de bord (P2 §11.4 item 16) : enregistre la classification dès qu'elle est connue,
    # idempotent (INSERT OR IGNORE) donc rejouable à chaque resynchronisation (y compris après une
    # clarification résolue, où le premier appel a lieu avant que la proposition n'existe).
    values = state.values or {}
    if values.get("classification"):
        source = "gmail_import" if st.session_state.get("gmail_message_id") else "manuel"
        analytics_store.record_classification(
            thread_id, values["classification"], values.get("email_raw", {}).get("sender", ""), source,
        )
        if values.get("draft_response"):
            analytics_store.record_draft_ready(thread_id)


def load_queued_thread(thread_id: str) -> None:
    """
    Charge dans la session une analyse déjà traitée par le poller (poller.py) : le graphe a déjà
    tourné jusqu'à la pause de validation dans un autre processus, donc pas de `stream()` ici —
    juste une lecture de l'état persistant (`checkpoints.sqlite`).
    """
    st.session_state.thread_id = thread_id
    sync_result(thread_id)

"""
Identité visuelle paramétrable (§17) — « marque blanche » pour l'entreprise qui reçoit ACA.

**Le problème.** Jusqu'ici l'apparence vivait entièrement dans `.streamlit/config.toml` : un fichier
statique, versionné, à éditer à la main puis à redéployer. Livrer ACA à une entreprise dont le
cahier des charges impose son logo et ses couleurs supposait donc de *modifier le produit* pour
chaque client — exactement ce qu'un produit ne doit pas demander. Ce module fait de l'apparence une
**donnée** : des jetons (`BRAND_*`) résolus à l'exécution, éditables depuis l'onglet « Réglages »
par un administrateur, sans redémarrage et sans toucher au dépôt.

**Deux couches, parce qu'aucune ne suffit seule.**

1. *CSS vivante* (`css()`), injectée à chaque rerun. Prend effet immédiatement, porte les
   animations, l'en-tête de marque et tout ce que Streamlit n'expose pas nativement. C'est la
   couche qui rend le changement de couleur instantané pour la personne qui l'édite.
2. *Thème natif* (`config_toml()` → `.streamlit/config.toml`, sur action explicite). Seule cette
   couche atteint l'intérieur des composants React de Streamlit (le fond d'un `st.selectbox`
   ouvert, la palette d'un graphique Vega, le rendu d'un `st.dataframe`). Elle exige un rechargement
   de page, ce que l'UI annonce au lieu de le cacher.

Le projet a la doctrine inverse par défaut (« pas de CSS, tout dans config.toml », cf. la skill
`developing-with-streamlit`) et elle reste la bonne pour un thème figé. Elle est écartée ici en
connaissance de cause : un thème qui se change à l'exécution, par tenant, ne peut pas être un
fichier statique lu au démarrage du serveur.

**Rien n'est figé à l'import.** `resolve()` relit `config_store` et l'environnement à chaque appel —
même raison que `DATABASE_URL` dans `vector_store.py` et `ACA_ORG_ID` dans `tenant.py` : un réglage
enregistré doit s'appliquer au rerun suivant, pas au prochain redémarrage.

**Dégradation gracieuse, comme partout ailleurs.** Aucun jeton réglé ⇒ palette ACA par défaut,
identique à l'apparence actuelle. Un `config_store` indisponible ⇒ repli sur `.env` puis sur les
valeurs par défaut, sans exception : une base de réglages verrouillée ne doit pas empêcher l'écran
de connexion de s'afficher.

Aucun import Streamlit ici (même posture que `risk_scan.py`, `session.py`, `graph_topology.py`) :
tout est pur et testable hors ligne, `ui.py` se charge du rendu.
"""
import colorsys
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

# ── Jetons de marque ──────────────────────────────────────────────────────────────────────────
# Table déclarative : ajouter un paramètre d'apparence = UNE entrée ici, et le formulaire de
# l'onglet « Réglages » le fait apparaître tout seul (même esprit que `ROUTING_DESTINATIONS` dans
# app.py et `JOBS` dans scheduler.py). `kind` pilote le widget utilisé côté UI.
KIND_TEXT = "text"
KIND_COLOR = "color"
KIND_CHOICE = "choice"
KIND_IMAGE = "image"

TOKENS = {
    # Identité
    "BRAND_NAME": {
        "label": "Nom de l'application", "kind": KIND_TEXT, "group": "Identité",
        "default": "Assistant commercial agentique",
        "help": "Affiché dans l'en-tête, l'onglet du navigateur et l'écran de connexion.",
    },
    "BRAND_TAGLINE": {
        "label": "Accroche", "kind": KIND_TEXT, "group": "Identité",
        "default": "Pré-lecture et qualification des e-mails entrants — validation humaine avant "
                   "écriture CRM.",
        "help": "Une phrase sous le titre. Laisser vide pour n'afficher que le nom.",
    },
    "BRAND_COMPANY": {
        "label": "Entreprise", "kind": KIND_TEXT, "group": "Identité", "default": "",
        "help": "Nom du client final, affiché en pied de page (« Déployé pour … »).",
    },
    "BRAND_LOGO": {
        "label": "Logo", "kind": KIND_IMAGE, "group": "Identité", "default": "",
        "help": "PNG, JPEG, SVG ou WebP, 512 Ko maximum. Vide = icône Material par défaut.",
    },
    # Couleurs
    #
    # §19 — palette par défaut refondue. L'ancienne (#0078D4 / #8764B8) était le bleu Fluent de
    # Microsoft plus un violet : exactement le duo que produit n'importe quel gabarit de tableau de
    # bord, donc rien qui distingue ce produit-ci. Le parti pris qui la remplace tient en une
    # phrase : **le travail de la machine est froid, la décision humaine est chaude.** Le pétrole
    # profond porte tout ce que la machine a préparé (chrome, liens, états actifs) ; l'ambre brûlé
    # est réservé au moment où une personne doit trancher. La palette dit donc quelque chose de vrai
    # sur le produit au lieu de le décorer. Le fond reste FROID, ce qui évite au passage le trio
    # crème / serif / terracotta qu'on retrouve sur toutes les maquettes générées.
    #
    # Tout reste surchargeable par client : ce ne sont que des valeurs par défaut.
    "BRAND_PRIMARY": {
        "label": "Couleur principale", "kind": KIND_COLOR, "group": "Couleurs",
        "default": "#125E6B",
        "help": "Boutons d'action, liens, éléments actifs. Doit rester lisible sous du texte blanc.",
    },
    "BRAND_ACCENT": {
        "label": "Couleur d'accent", "kind": KIND_COLOR, "group": "Couleurs",
        "default": "#B4622A",
        "help": "Réservée au moment de décision (validation en attente). À garder distincte de la "
                "couleur principale : c'est ce contraste qui fait ressortir l'action à mener.",
    },
    "BRAND_BACKGROUND": {
        "label": "Fond principal", "kind": KIND_COLOR, "group": "Couleurs", "default": "#F1F4F5",
    },
    "BRAND_SURFACE": {
        "label": "Fond des cartes", "kind": KIND_COLOR, "group": "Couleurs", "default": "#FFFFFF",
        "help": "Doit se distinguer du fond principal, sinon les cartes disparaissent. Ici les "
                "cartes sont plus CLAIRES que la page : des documents posés sur un plan de travail.",
    },
    "BRAND_SIDEBAR": {
        "label": "Fond de la barre latérale", "kind": KIND_COLOR, "group": "Couleurs",
        "default": "#E9EEEF",
    },
    "BRAND_TEXT": {
        "label": "Texte", "kind": KIND_COLOR, "group": "Couleurs", "default": "#12171C",
    },
    "BRAND_BORDER": {
        "label": "Bordures", "kind": KIND_COLOR, "group": "Couleurs", "default": "#D5DDE0",
    },
    "BRAND_SUCCESS": {
        "label": "Succès", "kind": KIND_COLOR, "group": "Couleurs d'état", "default": "#1F6F4A",
    },
    "BRAND_WARNING": {
        "label": "Avertissement", "kind": KIND_COLOR, "group": "Couleurs d'état",
        "default": "#B4622A",
    },
    "BRAND_DANGER": {
        "label": "Erreur / risque", "kind": KIND_COLOR, "group": "Couleurs d'état",
        "default": "#A32C1E",
    },
    "BRAND_INFO": {
        "label": "Information", "kind": KIND_COLOR, "group": "Couleurs d'état", "default": "#125E6B",
    },
    # Mise en forme
    "BRAND_FONT": {
        "label": "Police du texte", "kind": KIND_CHOICE, "group": "Mise en forme",
        "default": "Inter",
        "choices": ["Inter", "Open Sans", "Roboto", "Lato", "Montserrat", "Poppins",
                    "IBM Plex Sans", "Source Sans 3", "Nunito", "Système"],
        "help": "La voix de l'outil : formulaires, tableaux, corps de texte. Chargée depuis Google "
                "Fonts. « Système » n'appelle aucun serveur externe — à choisir si le réseau du "
                "client bloque les CDN.",
    },
    # §19 — un troisième rôle typographique, et il est mérité plutôt que décoratif. Ce produit
    # fabrique des DOCUMENTS commerciaux qu'un client finit par signer : la serif porte la voix du
    # document (titres, en-tête de marque), la sans porte la voix de l'outil (formulaires,
    # tableaux), et le monospace porte les valeurs de la machine (compteurs, horodatages,
    # identifiants — cf. `--aca-mono`, non paramétrable car ce n'est pas un choix de marque mais
    # une exigence de lisibilité : des chiffres tabulaires dans une file d'attente s'alignent).
    # Fraunces par défaut plutôt qu'une serif de gabarit : axes optiques et « wonk », donc une
    # personnalité que Playfair ou Lora n'ont pas.
    "BRAND_FONT_DISPLAY": {
        "label": "Police des titres", "kind": KIND_CHOICE, "group": "Mise en forme",
        "default": "Fraunces",
        "choices": ["Fraunces", "Instrument Serif", "Archivo", "Space Grotesk", "Identique au texte"],
        "help": "Employée uniquement en grand : en-tête de marque et titres de page. « Identique "
                "au texte » supprime le contraste et donne une allure plus neutre.",
    },
    "BRAND_RADIUS": {
        "label": "Arrondi des angles", "kind": KIND_CHOICE, "group": "Mise en forme",
        "default": "12px", "choices": ["0px", "4px", "8px", "12px", "16px", "24px"],
    },
    "BRAND_DENSITY": {
        "label": "Densité", "kind": KIND_CHOICE, "group": "Mise en forme", "default": "confortable",
        "choices": ["compacte", "confortable", "aérée"],
        "help": "Compacte affiche plus d'informations par écran ; aérée respire davantage.",
    },
    "BRAND_MODE": {
        "label": "Mode", "kind": KIND_CHOICE, "group": "Mise en forme", "default": "clair",
        "choices": ["clair", "sombre"],
        "help": "Change les valeurs par défaut des couleurs. Un fond explicitement réglé "
                "ci-dessus reste prioritaire.",
    },
    # Animations
    "BRAND_ANIMATIONS": {
        "label": "Animations", "kind": KIND_CHOICE, "group": "Animations", "default": "complet",
        "choices": ["complet", "sobre", "aucune"],
        "help": "« Sobre » ne garde que les fondus courts. Quel que soit ce réglage, le système "
                "d'exploitation gagne : « réduire les animations » est toujours respecté.",
    },
    "BRAND_HERO": {
        "label": "En-tête de marque", "kind": KIND_CHOICE, "group": "Animations",
        "default": "dégradé animé",
        "choices": ["dégradé animé", "dégradé fixe", "sobre", "masqué"],
    },
    # §26.3 — le fond d'ambiance devient réglable. Il était figé depuis le §21 (deux voiles
    # radiaux), puis §26 y a ajouté des blocs, sans jamais aucun moyen d'y toucher sans modifier le
    # code. Sur un produit livré en marque blanche c'est l'incohérence la plus voyante : tout le
    # reste de l'apparence se règle depuis l'écran « Apparence », et c'est justement l'élément qui
    # couvre le plus de surface à l'écran.
    "BRAND_AMBIENT": {
        "label": "Fond d'ambiance", "kind": KIND_CHOICE, "group": "Animations",
        "default": "particules",
        "choices": ["particules", "voile", "grille", "cadre", "aucun"],
        "help": "« Particules » : la trame de blocs, le motif de la page de présentation. "
                "« Voile » : les dégradés seuls. « Grille » : un quadrillage fin. "
                "« Cadre » : les dégradés plus un filet autour du plan de travail. "
                "« Aucun » : fond uni.",
    },
    "BRAND_AMBIENT_INTENSITY": {
        "label": "Intensité du fond", "kind": KIND_CHOICE, "group": "Animations",
        "default": "normal",
        "choices": ["discret", "normal", "marqué"],
        "help": "Agit sur les voiles ET sur la trame, pour qu'un seul réglage suffise.",
    },
    "BRAND_AMBIENT_COLOR": {
        "label": "Couleur du fond", "kind": KIND_COLOR, "group": "Animations",
        # Vide = suit `BRAND_PRIMARY`, et c'est le défaut le plus sûr : sans lui, un client qui
        # change sa couleur principale garderait un fond dans l'ancienne, et le réglage censé
        # unifier l'identité produirait exactement l'incohérence qu'il doit empêcher.
        "default": "",
        "help": "Vide = suit la couleur principale. Une couleur fixée ici ne bouge plus quand la "
                "couleur principale change.",
    },
}

# Palettes prêtes à l'emploi : un cahier des charges arrive rarement avec des codes hexadécimaux,
# plus souvent avec « nos couleurs sont le bleu marine et l'or ». Un préréglage donne un point de
# départ cohérent que l'on ajuste ensuite jeton par jeton.
#
# §21 — LES ACCENTS ONT ÉTÉ REVUS, et c'est une correction de fond, pas un ajustement de goût.
# Ces palettes ont été écrites en §17/§18, quand `BRAND_ACCENT` voulait encore dire « la deuxième
# couleur de la marque ». §19 lui a donné UN rôle exclusif — *une personne doit trancher ici* — sans
# revenir sur les palettes déjà livrées. Résultat mesuré par `signal_separation()` sur les dix-huit :
# quatre d'entre elles donnaient un accent de la même famille que la couleur principale (turquoise
# sur turquoise, bleu clair sur bleu foncé…), c'est-à-dire qu'elles effaçaient le seul signal qui
# distingue « en attente de vous » du reste de l'écran. Constaté en conditions réelles : l'instance
# de test tournait sous « Azur corporate », et pas un pixel d'ambre n'apparaissait nulle part.
#
# La règle que ces valeurs respectent désormais est la SÉPARATION et la RÉSERVE, pas une température
# fixe. « Le travail de la machine est froid, la décision est chaude » reste la formulation par
# défaut, mais lorsqu'un client a une couleur de marque déjà chaude (« Corail »), c'est la couleur
# froide qui devient celle qu'on réserve : ce qui compte est qu'on ne puisse pas la confondre avec
# le reste, et qu'elle ne serve à rien d'autre.
#
PRESETS = {
    "ACA (défaut)": {},
    # ── Palettes génériques ───────────────────────────────────────────────────────────────────
    "Azur corporate": {
        # Accent : #3E8FD0 -> #A65A11. L'ancien n'était que la couleur principale éclaircie
        # (séparation 0,08 sur 1) : le cartouche de signature disparaissait dans la page.
        "BRAND_PRIMARY": "#0F4C81", "BRAND_ACCENT": "#A65A11", "BRAND_SURFACE": "#F2F6FB",
        "BRAND_SIDEBAR": "#EDF3F9", "BRAND_BORDER": "#D8E3EF", "BRAND_RADIUS": "8px",
    },
    "Émeraude": {
        # Accent : #37C39B -> #A94A28 (séparation 0,11 -> 0,79). Terre cuite contre émeraude.
        "BRAND_PRIMARY": "#0B7A5E", "BRAND_ACCENT": "#A94A28", "BRAND_SURFACE": "#F1F8F5",
        "BRAND_SIDEBAR": "#ECF5F1", "BRAND_BORDER": "#D5E8E0", "BRAND_SUCCESS": "#0B7A5E",
    },
    "Ardoise & or": {
        "BRAND_PRIMARY": "#1F2933", "BRAND_ACCENT": "#C9A227", "BRAND_SURFACE": "#F4F5F7",
        "BRAND_SIDEBAR": "#EDEFF2", "BRAND_BORDER": "#DDE1E6", "BRAND_RADIUS": "4px",
    },
    "Corail": {
        # Le seul cas où la couleur réservée est FROIDE, et il est instructif : la couleur de marque
        # étant déjà chaude, un accent chaud (l'ancien saumon #F0956A) restait de la même famille —
        # 0,27, à la limite basse. Le sarcelle profond ne peut pas être confondu avec le corail, et
        # c'est le seul critère qui compte : l'invariant est « réservée et distincte », pas « chaude ».
        "BRAND_PRIMARY": "#D64545", "BRAND_ACCENT": "#1F6F73", "BRAND_SURFACE": "#FDF4F2",
        "BRAND_SIDEBAR": "#FBEFEC", "BRAND_BORDER": "#F2DCD6", "BRAND_RADIUS": "16px",
    },
    "Violet nuit (sombre)": {
        "BRAND_MODE": "sombre", "BRAND_PRIMARY": "#8B7CF6", "BRAND_ACCENT": "#22D3EE",
        "BRAND_BACKGROUND": "#0F1117", "BRAND_SURFACE": "#181B24", "BRAND_SIDEBAR": "#12141C",
        "BRAND_TEXT": "#E6E8EF", "BRAND_BORDER": "#282C38", "BRAND_RADIUS": "12px",
    },
    "Carbone (sombre)": {
        "BRAND_MODE": "sombre", "BRAND_PRIMARY": "#4EA1FF", "BRAND_ACCENT": "#5AD1B0",
        "BRAND_BACKGROUND": "#101214", "BRAND_SURFACE": "#181B1E", "BRAND_SIDEBAR": "#141618",
        "BRAND_TEXT": "#E4E6E8", "BRAND_BORDER": "#262A2E", "BRAND_RADIUS": "6px",
    },
    # ── Profils sectoriels (§18) ──────────────────────────────────────────────────────────────
    # Un cahier des charges ne dit presque jamais « #0F4C81 » ; il dit « nous sommes un cabinet
    # d'expertise comptable » ou « nous vendons du matériel de chantier ». Ces profils partent donc
    # du métier du client plutôt que d'une teinte, et font varier la police, l'arrondi et la densité
    # en même temps que la palette — un secteur a une allure, pas seulement une couleur.
    "Industrie & BTP": {
        "BRAND_PRIMARY": "#B4530A", "BRAND_ACCENT": "#31465A", "BRAND_SURFACE": "#F5F4F1",
        "BRAND_SIDEBAR": "#EFEDE9", "BRAND_BORDER": "#DAD6CF", "BRAND_TEXT": "#22282E",
        "BRAND_RADIUS": "0px", "BRAND_FONT": "Roboto", "BRAND_DENSITY": "compacte",
    },
    "Santé & médical": {
        # Accent : #5BC0BE -> #B8503A. Deux turquoises, séparation 0,07 : c'était la palette la
        # plus atteinte des dix-huit. Le corail est par ailleurs un classique du secteur médical.
        "BRAND_PRIMARY": "#00747C", "BRAND_ACCENT": "#B8503A", "BRAND_SURFACE": "#F2F9F9",
        "BRAND_SIDEBAR": "#EAF4F5", "BRAND_BORDER": "#CFE5E7", "BRAND_INFO": "#00747C",
        "BRAND_RADIUS": "16px", "BRAND_FONT": "Source Sans 3", "BRAND_DENSITY": "aérée",
    },
    "Finance & conseil": {
        "BRAND_PRIMARY": "#14294B", "BRAND_ACCENT": "#8A6D2F", "BRAND_SURFACE": "#F4F5F8",
        "BRAND_SIDEBAR": "#EDEFF4", "BRAND_BORDER": "#D9DDE6", "BRAND_TEXT": "#151A22",
        "BRAND_RADIUS": "4px", "BRAND_FONT": "IBM Plex Sans", "BRAND_ANIMATIONS": "sobre",
    },
    "Tech & SaaS": {
        "BRAND_PRIMARY": "#4F46E5", "BRAND_ACCENT": "#06B6D4", "BRAND_SURFACE": "#F5F5FF",
        "BRAND_SIDEBAR": "#EFEFFC", "BRAND_BORDER": "#DDDDF2", "BRAND_RADIUS": "16px",
        "BRAND_FONT": "Inter",
    },
    "Luxe & retail": {
        "BRAND_PRIMARY": "#171717", "BRAND_ACCENT": "#A8874B", "BRAND_SURFACE": "#F7F6F4",
        "BRAND_SIDEBAR": "#F1EFEC", "BRAND_BORDER": "#E0DCD5", "BRAND_TEXT": "#141414",
        "BRAND_RADIUS": "0px", "BRAND_FONT": "Montserrat", "BRAND_DENSITY": "aérée",
        "BRAND_HERO": "dégradé fixe",
    },
    "Éducation & secteur public": {
        "BRAND_PRIMARY": "#1D4E89", "BRAND_ACCENT": "#2E933C", "BRAND_SURFACE": "#F3F6FA",
        "BRAND_SIDEBAR": "#ECF1F7", "BRAND_BORDER": "#D6DFEA", "BRAND_RADIUS": "8px",
        "BRAND_FONT": "Source Sans 3",
    },
    "Agroalimentaire": {
        "BRAND_PRIMARY": "#4F6F1F", "BRAND_ACCENT": "#D19A25", "BRAND_SURFACE": "#F6F7F1",
        "BRAND_SIDEBAR": "#F0F2E9", "BRAND_BORDER": "#DCE0CE", "BRAND_SUCCESS": "#4F6F1F",
        "BRAND_RADIUS": "12px", "BRAND_FONT": "Nunito",
    },
    "Immobilier": {
        "BRAND_PRIMARY": "#1F4235", "BRAND_ACCENT": "#B08D57", "BRAND_SURFACE": "#F4F6F4",
        "BRAND_SIDEBAR": "#EDF0ED", "BRAND_BORDER": "#D8DFD9", "BRAND_RADIUS": "8px",
        "BRAND_FONT": "Lato",
    },
    "Énergie (sombre)": {
        "BRAND_MODE": "sombre", "BRAND_PRIMARY": "#2DD4BF", "BRAND_ACCENT": "#FACC15",
        "BRAND_BACKGROUND": "#0B1220", "BRAND_SURFACE": "#141C2B", "BRAND_SIDEBAR": "#0E1524",
        "BRAND_TEXT": "#E2E8F0", "BRAND_BORDER": "#22304A", "BRAND_RADIUS": "8px",
        "BRAND_FONT": "IBM Plex Sans",
    },
    "Transport & logistique": {
        "BRAND_PRIMARY": "#0B4F9E", "BRAND_ACCENT": "#F59E0B", "BRAND_SURFACE": "#F3F6FB",
        "BRAND_SIDEBAR": "#EBF1F9", "BRAND_BORDER": "#D5E0EF", "BRAND_RADIUS": "4px",
        "BRAND_FONT": "Roboto", "BRAND_DENSITY": "compacte",
    },
    "Accessibilité renforcée": {
        # Contrastes poussés et mouvement réduit : pour un client dont le cahier des charges impose
        # le RGAA/WCAG AA, ou une équipe travaillant sur des écrans de mauvaise qualité.
        # Accent : #6B21A8 -> #B45309. Celui-ci passait pourtant le contrôle automatique (0,44) :
        # `signal_separation()` modélise une vision normale des couleurs, or bleu et violet sont
        # précisément la paire que confondent les deutéranopes. Sur une palette dont le nom promet
        # l'accessibilité, s'en remettre à la mesure générique aurait été le pire endroit. Bleu et
        # ambre est le couple de référence, distinguable sous deutéranopie comme sous protanopie.
        "BRAND_PRIMARY": "#00408A", "BRAND_ACCENT": "#B45309", "BRAND_SURFACE": "#F0F2F5",
        "BRAND_SIDEBAR": "#E8EBF0", "BRAND_BORDER": "#B9C0CC", "BRAND_TEXT": "#0B0F14",
        "BRAND_RADIUS": "4px", "BRAND_ANIMATIONS": "sobre", "BRAND_DENSITY": "aérée",
    },
}

PRESET_KEY = "BRAND_PRESET"

# Préfixe des profils enregistrés par un administrateur depuis l'UI. Stockés dans `config_store`
# comme le reste des réglages du tenant, en JSON, sous `BRAND_PROFILE_<nom>`.
PROFILE_PREFIX = "BRAND_PROFILE_"

# Défauts appliqués quand `BRAND_MODE = "sombre"` sans couleur explicite : un mode sombre obtenu en
# ne changeant que le fond donnerait du texte noir sur fond noir. Ces valeurs vont ensemble.
_DARK_DEFAULTS = {
    "BRAND_BACKGROUND": "#0F1117", "BRAND_SURFACE": "#181B24",
    "BRAND_SIDEBAR": "#12141C", "BRAND_TEXT": "#E6E8EF", "BRAND_BORDER": "#282C38",
}

# Polices Google : famille -> URL de feuille de style. « Système » n'y figure volontairement pas.
_GOOGLE_FONTS = {
    "Inter": "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap",
    "Open Sans": "https://fonts.googleapis.com/css2?family=Open+Sans:wght@300;400;500;600;700&display=swap",
    "Roboto": "https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap",
    "Lato": "https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700&display=swap",
    "Montserrat": "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap",
    "Poppins": "https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap",
    "IBM Plex Sans": "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap",
    "Source Sans 3": "https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;500;600;700&display=swap",
    "Nunito": "https://fonts.googleapis.com/css2?family=Nunito:wght@400;500;600;700&display=swap",
    # §19 — familles de titrage. Séparées des précédentes dans `DISPLAY_FONTS` ci-dessous, mais
    # présentes ici aussi pour que `config_toml()` sache résoudre une URL quelle que soit la famille.
    "Fraunces": "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&display=swap",
    "Instrument Serif": "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap",
    "Archivo": "https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&display=swap",
    "Space Grotesk": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap",
}
_SYSTEM_FONT_STACK = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', "
                      "Arial, sans-serif")

# Repli de titrage : une serif système, pour que le contraste titre/texte survive même si Google
# Fonts est injoignable (réseau d'entreprise fermé) — sinon la hiérarchie s'effondre en silence.
_DISPLAY_FALLBACK = "Georgia, 'Times New Roman', serif"

# Face de données, NON paramétrable — contrairement aux deux autres, ce n'est pas un choix de
# marque. Compteurs, horodatages, identifiants de fil et quotas sont des valeurs machine : elles
# demandent des chiffres tabulaires qui s'alignent verticalement dans une file d'attente. Laisser
# un client la remplacer par une display casserait cet alignement sans rien gagner.
_MONO_IMPORT = ("https://fonts.googleapis.com/css2"
                "?family=IBM+Plex+Mono:wght@400;500;600&display=swap")
_MONO_STACK = "'IBM Plex Mono', ui-monospace, 'SFMono-Regular', Consolas, monospace"

# Densité -> (espacement vertical entre blocs, padding interne des cartes, marge haute de page).
_DENSITY = {
    "compacte": ("0.55rem", "0.85rem", "1.4rem"),
    "confortable": ("0.95rem", "1.15rem", "2.2rem"),
    "aérée": ("1.5rem", "1.6rem", "3rem"),
}

# Logo : formats acceptés et plafond de taille. Le plafond n'est pas cosmétique — le logo est stocké
# en base64 dans `config.sqlite` et réinjecté à CHAQUE rerun de Streamlit ; un PNG de 5 Mo
# alourdirait chaque interaction de l'application.
MAX_LOGO_BYTES = 512 * 1024
LOGO_MIME_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "svg": "image/svg+xml", "webp": "image/webp",
}

DEFAULT_LOGO_ICON = ":material/smart_toy:"

# §29 — répertoire des artefacts générés par `scripts/build_brand_assets.py`. Utilisé UNIQUEMENT en
# repli, quand aucun client n'a téléversé son propre logo : avant, ce repli était l'icône Material
# générique ci-dessus, visible dans l'onglet du navigateur et la barre latérale de CETTE installation
# tant que personne ne l'a personnalisée — soit exactement le cas de l'instance de démonstration
# d'acami elle-même. `parent.parent.parent` : ce fichier est `aca/core/branding.py`, donc trois
# niveaux remontent à la racine du dépôt, là où vit `static/`.
_BRAND_ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "brand"


@lru_cache(maxsize=1)
def _default_favicon_bytes():
    """
    Octets PNG de l'étoile acami (favicon rond), ou `None` si le fichier est absent.

    PNG et non SVG, pour la même raison que documentée sur `favicon_for_streamlit` plus bas :
    `st.image` ne rend pas les SVG de façon fiable comme icône d'onglet.
    """
    try:
        return (_BRAND_ASSET_DIR / "acami-favicon-32.png").read_bytes()
    except OSError:
        return None


@lru_cache(maxsize=2)
def _default_lockup_bytes(dark: bool):
    """
    Octets PNG (fond transparent) du mot-symbole acami (étoile + « acami » dessiné), clair ou sombre.

    §29 — CORRECTION : cette fonction rendait d'abord les octets `.svg`, et `st.logo()` les rejette
    avec `StreamlitAPIException` (« The image passed to st.logo is invalid ») — confirmé en
    interrogeant directement `image_utils.image_to_url()`, qui lève `UnidentifiedImageError` sur un
    SVG brut. `st.logo()`/`st.set_page_config(page_icon=…)` ouvrent l'image reçue via PIL en
    interne pour la réencoder ; PIL ne sait pas ouvrir un SVG. C'est la MÊME contrainte que
    `favicon_for_streamlit` documente déjà plus bas — simplement oubliée ici lors du premier câblage
    du repli. Un `<img src="…">` injecté par `st.html()` (`hero_html`, `agency_mark_html`) n'a pas
    cette contrainte : c'est le navigateur qui charge l'image, jamais le code Python de Streamlit.
    """
    name = "acami-lockup-dark.png" if dark else "acami-lockup.png"
    try:
        return (_BRAND_ASSET_DIR / name).read_bytes()
    except OSError:
        return None


def _default_logo_bytes(tokens: dict):
    """
    Repli de `logo_for_streamlit` : le mot-symbole acami plutôt que l'icône Material générique.

    Choisit le tracé clair ou sombre selon la luminance de `BRAND_SIDEBAR` — le mot-symbole est une
    encre fixe (pas de `currentColor`, cf. `build_brand_assets.py`), donc l'encre sombre par défaut
    deviendrait illisible sur une barre latérale sombre si rien ne s'adaptait.
    """
    sidebar = tokens.get("BRAND_SIDEBAR") or ""
    dark_bg = is_valid_hex(sidebar) and relative_luminance(sidebar) < 0.45
    return _default_lockup_bytes(dark_bg) or DEFAULT_LOGO_ICON


# ── Identité de l'agence (§28) ────────────────────────────────────────────────────────────────
# Table VOLONTAIREMENT SÉPARÉE de `TOKENS`, et la séparation est toute la raison d'être du bloc.
#
#   `BRAND_*`  décrit le CLIENT. Il change à chaque tenant, le client l'édite lui-même depuis
#              « Apparence », et il couvre l'interface entière.
#   `AGENCY_*` décrit acami, qui a installé l'outil. Réglé une fois, il doit survivre à un client
#              qui reprend toute la charte, et il ne couvre qu'une mention discrète en pied de page.
#
# Les fusionner laisserait un client effacer la signature du prestataire en changeant simplement de
# préréglage — c'est-à-dire reproduire sur l'identité le défaut exact que `signal_separation()` a été
# écrit pour rattraper sur la couleur : un réglage d'apparence qui emporte avec lui, sans le dire,
# quelque chose qui n'était pas de l'apparence.
#
# Trois couches de noms cohabitent, et elles ne s'écrivent pas pareil (cf. docs/BRAND.md §1) :
#   acami   l'entité commerciale — toujours en minuscules, y compris en début de phrase ;
#   ACAM    le moteur multi-agents ;
#   ACA     le système déployé chez le client.
AGENCY_TOKENS = {
    "AGENCY_NAME": {
        "label": "Nom de l'agence", "kind": KIND_TEXT, "group": "Agence",
        "default": "acami",
        "help": "Affiché en pied de page et sur l'écran de connexion. En minuscules : c'est ce qui "
                "le distingue des acronymes techniques ACA et ACAM.",
    },
    "AGENCY_URL": {
        "label": "Site de l'agence", "kind": KIND_TEXT, "group": "Agence", "default": "",
        "help": "Vide = la mention s'affiche sans lien. Aucun domaine n'est encore déposé.",
    },
    "AGENCY_LOGO": {
        "label": "Logo de l'agence", "kind": KIND_IMAGE, "group": "Agence", "default": "",
        "help": "Vide = la marque acami intégrée. Même plafond de 512 Ko que le logo client.",
    },
    "AGENCY_SHOW": {
        "label": "Afficher la mention", "kind": KIND_CHOICE, "group": "Agence",
        "default": "oui", "choices": ["oui", "non"],
        "help": "« non » retire complètement la signature de l'agence. Prévu pour un client dont le "
                "cahier des charges interdit la mention d'un prestataire.",
    },
}

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


# ── Utilitaires de couleur (purs) ─────────────────────────────────────────────────────────────
def is_valid_hex(value: str) -> bool:
    """`#abc` ou `#aabbcc`. Garde indispensable avant toute écriture dans le CSS : une valeur non
    validée irait directement dans une feuille de style injectée."""
    return bool(value and _HEX_RE.match(value.strip()))


def _to_rgb(hex_color: str) -> tuple:
    value = hex_color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgb_string(hex_color: str) -> str:
    """`"#0078D4"` -> `"0, 120, 212"`, pour composer des `rgba()` en CSS à partir d'un seul jeton."""
    return ", ".join(str(channel) for channel in _to_rgb(hex_color))


def relative_luminance(hex_color: str) -> float:
    """Luminance relative WCAG (0 = noir, 1 = blanc)."""
    channels = []
    for channel in _to_rgb(hex_color):
        srgb = channel / 255
        channels.append(srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs (1 à 21). 4,5 est le seuil AA pour du texte."""
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable_text_on(hex_color: str) -> str:
    """
    Noir ou blanc, selon ce qui se lit le mieux sur `hex_color`.

    Utilisé pour le texte de l'en-tête de marque : un client qui choisit un jaune vif comme couleur
    principale obtiendrait sinon du blanc sur jaune, illisible — et ce serait *notre* défaut, pas
    son mauvais goût.
    """
    return "#FFFFFF" if relative_luminance(hex_color) < 0.45 else "#111111"


def signal_separation(primary: str, accent: str) -> float:
    """
    Écart perceptif entre la couleur principale et la couleur d'accent, de 0 (indistinguables) à 1.

    **Pourquoi cette mesure existe.** Depuis §19, l'accent n'est plus « la deuxième couleur de la
    marque » : il a UN sens, un seul, et c'est le plus important du produit — *quelque chose attend
    une décision humaine*. Il porte le cartouche de signature, la pastille d'alerte de l'en-tête et
    le terminus du rail de décision. Si l'accent se confond avec la couleur principale, ce signal
    disparaît : tout l'écran devient « de la marque », et plus rien ne dit où regarder.

    **Pourquoi pas `contrast_ratio`.** Le contraste WCAG mesure une différence de LUMINANCE, pas de
    teinte. Le couple par défaut (pétrole #125E6B / ambre #B4622A) n'obtient que 1,66:1 alors qu'il
    est évidemment lisible comme deux couleurs différentes ; à l'inverse un bleu foncé et un bleu
    clair obtiennent un bon contraste tout en restant « du bleu ». Utiliser le contraste ici aurait
    donc signalé le bon couple et laissé passer le mauvais — exactement à l'envers.

    **Comment.** Distance dans le PLAN CHROMATIQUE (a*, b*) de CIELAB, la clarté L* étant
    délibérément ignorée. Une première version combinait teinte et saturation en TSL et se trompait
    dans les deux sens, ce que le classement des dix-huit palettes livrées a rendu visible
    immédiatement : « Santé & médical » (#00747C → #5BC0BE, deux turquoises) passait pour correcte
    grâce au seul écart de saturation, tandis qu'« Immobilier » (vert profond → sable) était
    signalée alors qu'elle se lit parfaitement. Ignorer L* est exactement ce qui corrige les deux :
    un bleu clair et un bleu foncé restent « du bleu » quelle que soit leur différence de clarté,
    alors qu'un neutre et une couleur vive se distinguent sans partager aucune teinte — et dans le
    plan a*b*, un neutre est proche de l'origine, donc naturellement loin de tout ce qui est vif.

    Renvoie une distance normalisée : < 0,25 ⇒ le signal de décision est perdu.
    """
    def to_lab_ab(value: str):
        # sRGB -> linéaire -> XYZ (D65) -> L*a*b*. ~15 lignes de stdlib, contre une dépendance
        # « colour science » pour un seul calcul : même arbitrage que `totp.py` et `slack_verify.py`.
        channels = []
        for raw in _to_rgb(value):
            component = raw / 255
            channels.append(component / 12.92 if component <= 0.04045
                            else ((component + 0.055) / 1.055) ** 2.4)
        red, green, blue = channels
        x = (red * 0.4124 + green * 0.3576 + blue * 0.1805) / 0.95047
        y = (red * 0.2126 + green * 0.7152 + blue * 0.0722) / 1.00000
        z = (red * 0.0193 + green * 0.1192 + blue * 0.9505) / 1.08883

        def f(t):
            return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

        fx, fy, fz = f(x), f(y), f(z)
        return 500 * (fx - fy), 200 * (fy - fz)

    a_1, b_1 = to_lab_ab(primary)
    a_2, b_2 = to_lab_ab(accent)
    distance = ((a_1 - a_2) ** 2 + (b_1 - b_2) ** 2) ** 0.5
    # ~100 unités a*b* séparent deux couleurs vives opposées ; on y ramène l'échelle 0-1.
    return round(min(1.0, distance / 100.0), 3)


def mix(hex_color: str, other: str, ratio: float) -> str:
    """Mélange linéaire de deux couleurs (`ratio` = part de `other`), en hexadécimal."""
    ratio = max(0.0, min(1.0, ratio))
    blended = (round(a + (b - a) * ratio) for a, b in zip(_to_rgb(hex_color), _to_rgb(other)))
    return "#" + "".join(f"{channel:02X}" for channel in blended)


# ── Résolution des jetons ─────────────────────────────────────────────────────────────────────
def _stored_settings() -> dict:
    """
    Réglages `BRAND_*` du tenant courant, ou `{}` si le magasin est indisponible.

    Import local et volontaire : `branding` est appelé depuis l'écran de connexion, avant toute
    session ; le faire dépendre à l'import d'un module de stockage ferait échouer l'affichage d'un
    formulaire de connexion pour cause de base verrouillée.
    """
    try:
        from aca.storage import config_store

        return {
            key: value for key, value in config_store.get_all_settings().items()
            if key.startswith("BRAND_")
        }
    except Exception:  # noqa: BLE001 — l'apparence ne bloque jamais l'application
        return {}


def saved_profiles(stored: dict = None) -> dict:
    """
    Profils enregistrés par un administrateur : `{nom: {jeton: valeur}}`.

    Complète les préréglages livrés (`PRESETS`). Un intégrateur qui déploie ACA chez plusieurs
    clients règle la charte une fois, l'enregistre sous le nom du client, et la retrouve dans la
    liste déroulante au déploiement suivant — sans repasser par le code ni par un fichier.

    Une valeur illisible (JSON corrompu à la main dans la base) est **ignorée** plutôt que de faire
    échouer le rendu : l'apparence ne bloque jamais l'application.
    """
    import json

    stored = stored if stored is not None else _stored_settings()
    profiles = {}
    for key, raw_value in stored.items():
        if not key.startswith(PROFILE_PREFIX) or not raw_value:
            continue
        try:
            tokens = json.loads(raw_value)
        except (ValueError, TypeError):
            continue
        if isinstance(tokens, dict):
            profiles[key[len(PROFILE_PREFIX):]] = {
                token: value for token, value in tokens.items() if token in TOKENS
            }
    return profiles


def all_profiles(stored: dict = None) -> dict:
    """
    Tous les profils sélectionnables : préréglages livrés **puis** profils enregistrés.

    L'ordre compte pour la liste déroulante : les profils propres au client apparaissent après les
    palettes fournies, donc à un endroit stable, au lieu de se mêler à elles par ordre alphabétique.
    """
    return {**PRESETS, **saved_profiles(stored)}


def profile_payload(tokens: dict) -> str:
    """
    Sérialise les jetons d'apparence courants pour enregistrement comme profil réutilisable.

    Le logo n'est **pas** inclus : il pèse jusqu'à 512 Ko en base64 et une liste de dix profils
    embarquant chacun son logo transformerait la table de réglages en dépôt d'images. Le logo reste
    un réglage à part, téléversé pour le tenant courant.
    """
    import json

    payload = {
        key: value for key, value in tokens.items()
        if key in TOKENS and key != "BRAND_LOGO" and value not in (None, "")
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def resolve(overrides: dict = None) -> dict:
    """
    Valeur effective de chaque jeton, par ordre de priorité décroissant :

    1. `overrides` — prévisualisation en direct dans le formulaire (avant enregistrement) ;
    2. `config_store` — ce qu'un administrateur a réglé dans l'UI, par tenant ;
    3. variable d'environnement de même nom (`BRAND_PRIMARY=…` dans `.env`) — permet de livrer une
       image Docker déjà aux couleurs du client, sans base de réglages ;
    4. préréglage sélectionné (`BRAND_PRESET`) ;
    5. défauts du mode (clair/sombre), puis défaut du jeton.

    Ainsi une couleur explicitement choisie n'est jamais écrasée par un préréglage ni par le
    passage en mode sombre : elle a été choisie, elle gagne.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    stored = _stored_settings()

    def raw(key: str):
        if key in overrides:
            return overrides[key]
        if stored.get(key):
            return stored[key]
        return os.getenv(key) or None

    preset_name = raw(PRESET_KEY) or "ACA (défaut)"
    # `all_profiles` et non `PRESETS` : un profil enregistré par l'administrateur (§18) doit être
    # sélectionnable exactement comme une palette livrée, sinon « enregistrer » ne servirait à rien.
    preset = all_profiles(stored).get(preset_name, {})

    mode = raw("BRAND_MODE") or preset.get("BRAND_MODE") or TOKENS["BRAND_MODE"]["default"]
    mode_defaults = _DARK_DEFAULTS if mode == "sombre" else {}

    tokens = {PRESET_KEY: preset_name, "BRAND_MODE": mode}
    for key, spec in TOKENS.items():
        if key == "BRAND_MODE":
            continue
        value = raw(key) or preset.get(key) or mode_defaults.get(key) or spec["default"]
        # Une couleur corrompue (saisie manuelle, .env mal recopié) retombe sur le défaut plutôt
        # que d'être injectée telle quelle dans la feuille de style.
        if spec["kind"] == KIND_COLOR and not is_valid_hex(value):
            value = mode_defaults.get(key) or spec["default"]
        tokens[key] = value
    return tokens


def customised_tokens(tokens: dict) -> dict:
    """
    Jetons s'écartant de la palette ACA par défaut — ce qu'il est utile de consigner au journal
    d'activité (§17) et d'afficher comme « personnalisations actives » dans le panneau Apparence.
    """
    reference = {PRESET_KEY: "ACA (défaut)", "BRAND_MODE": TOKENS["BRAND_MODE"]["default"]}
    reference.update({key: spec["default"] for key, spec in TOKENS.items()})
    return {key: value for key, value in tokens.items() if reference.get(key) != value}


# ── Identité de l'agence : résolution et rendu (§28) ──────────────────────────────────────────
def resolve_agency(overrides: dict = None) -> dict:
    """
    Valeur effective de chaque jeton `AGENCY_*`, par ordre de priorité décroissant :
    `overrides` → `config_store` → variable d'environnement → défaut du jeton.

    Volontairement PLUS COURT que `resolve()` : ni préréglage, ni défauts de mode sombre. Un
    préréglage est une palette proposée au client ; la signature de l'agence n'a pas à en dépendre,
    et c'est exactement ce qu'on cherche à empêcher (cf. le commentaire sur `AGENCY_TOKENS`).
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    stored = _stored_settings()

    resolved = {}
    for key, spec in AGENCY_TOKENS.items():
        value = overrides.get(key) or stored.get(key) or os.getenv(key) or spec["default"]
        resolved[key] = value
    return resolved


def agency_enabled(tokens: dict) -> bool:
    """Vrai si la mention doit être rendue. Tolérant sur la valeur, faux uniquement sur un « non »."""
    return str(tokens.get("AGENCY_SHOW", "oui")).strip().lower() not in ("non", "no", "0", "false")


@lru_cache(maxsize=None)
def _brand_png_data_uri(filename: str) -> str:
    """
    URI `data:image/png;base64,…` d'un PNG de `static/brand/`, mise en cache — vide si absent.

    §29 — CORRECTION D'UNE PREMIÈRE CORRECTION. La version précédente injectait un `<svg>` EN LIGNE
    dans le HTML envoyé par `st.html()` (`agency_mark_svg()`/`agency_lockup_svg()`, supprimées).
    Streamlit « sanitize[s] HTML with DOMPurify » CÔTÉ NAVIGATEUR — sa propre documentation le dit
    explicitement — et le profil par défaut de DOMPurify élimine l'espace de noms SVG. Le message
    envoyé restait syntaxiquement correct, donc INVISIBLE pour un test Python type `AppTest` (qui
    n'inspecte que le proto envoyé, jamais le DOM après sanitisation par le navigateur) — ce qui a
    fait passer cette régression une première fois. Elle n'a été trouvée qu'en regardant une vraie
    capture d'écran de navigateur : le titre et la mention de pied de page rendaient une balise
    vide. `<img src="data:…">` n'a pas ce problème : DOMPurify autorise `img[src]`, le mécanisme
    déjà utilisé pour un logo client personnalisé (`aca-agency__glyph`, plus bas).
    """
    import base64

    try:
        data = (_BRAND_ASSET_DIR / filename).read_bytes()
    except OSError:
        return ""
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


def _brand_mark_uri(tokens: dict, kind: str, background_key: str) -> str:
    """
    URI de données du PNG « mark » (étoile seule) ou « lockup » (étoile + mot), clair ou sombre
    selon la luminance du jeton de fond `background_key` — même logique que `_default_logo_bytes`
    pour la barre latérale, appliquée ici à l'en-tête de marque et au pied de page, qui ne
    s'affichent pas nécessairement sur le même fond que la barre latérale.
    """
    background = tokens.get(background_key) or ""
    dark_bg = is_valid_hex(background) and relative_luminance(background) < 0.45
    suffix = "-dark" if dark_bg else ""
    return _brand_png_data_uri(f"acami-{kind}{suffix}.png")


def agency_mark_html(tokens: dict, prefix: str = "") -> str:
    """
    Mention « installé par <agence> », prête à être injectée.

    Renvoie une chaîne VIDE si la mention est désactivée : l'appelant peut concaténer sans tester,
    et un client qui a coupé la signature n'obtient pas un séparateur orphelin en pied de page.

    Tout ce qui vient d'un réglage passe par `escape` — ces valeurs sont saisies dans un formulaire
    d'administration, donc du contenu non fiable au sens de `prompt_guard.py`, même si la personne
    qui les saisit est de confiance.
    """
    from html import escape

    if not agency_enabled(tokens):
        return ""

    name = escape((tokens.get("AGENCY_NAME") or "").strip())
    if not name:
        return ""

    logo = tokens.get("AGENCY_LOGO") or ""
    label = f'{escape(prefix)} ' if prefix else ""

    if logo.startswith("data:"):
        # Un revendeur en marque blanche a téléversé SON logo : on ne connaît pas son tracé, donc
        # icône fournie + nom tapé, comme avant.
        glyph = f'<img src="{escape(logo)}" alt="" class="aca-agency__glyph">'
        inner = f'{glyph}<span class="aca-agency__name">{name}</span>'
    elif (tokens.get("AGENCY_NAME") or "").strip().lower() == "acami":
        # §29 — le cas par défaut (aucun revendeur n'a remplacé la mention) : « acami » dans son
        # tracé réel plutôt que reconstruit en icône + texte système.
        uri = _brand_mark_uri(tokens, "lockup", "BRAND_BACKGROUND")
        inner = (f'<img src="{uri}" alt="acami" class="aca-agency__glyph" '
                 f'style="width:auto; height:16px;">' if uri
                 else f'<span class="aca-agency__name">{name}</span>')
    else:
        uri = _brand_mark_uri(tokens, "mark", "BRAND_BACKGROUND")
        glyph = (f'<img src="{uri}" alt="" class="aca-agency__glyph">' if uri else "")
        inner = f'{glyph}<span class="aca-agency__name">{name}</span>'

    url = (tokens.get("AGENCY_URL") or "").strip()
    # Seuls http(s) sont acceptés : un réglage contenant « javascript: » deviendrait un lien
    # exécutable sur toutes les pages de l'application.
    if url.lower().startswith(("http://", "https://")):
        inner = (f'<a class="aca-agency__link" href="{escape(url)}" target="_blank" '
                 f'rel="noopener noreferrer">{inner}</a>')

    return f'<span class="aca-agency">{label}{inner}</span>'


# ── Logo ──────────────────────────────────────────────────────────────────────────────────────
class LogoRejected(ValueError):
    """Levée par `encode_logo` sur un format non géré, un fichier vide ou trop lourd."""


def encode_logo(filename: str, content: bytes) -> str:
    """
    Encode un logo téléversé en URI de données (`data:image/png;base64,…`), stockable tel quel.

    Choisi plutôt qu'un fichier sur disque : le réglage reste dans `config.sqlite` avec le reste de
    la configuration du tenant, donc il suit les sauvegardes et le cloisonnement `org_id` déjà en
    place — là où un chemin de fichier casse dès que l'application change de machine ou tourne dans
    un conteneur au système de fichiers éphémère.
    """
    import base64

    extension = (filename or "").rsplit(".", 1)[-1].lower()
    if extension not in LOGO_MIME_TYPES:
        raise LogoRejected(
            f"Format non géré : « {extension or filename} ». "
            f"Formats acceptés : {', '.join(sorted(LOGO_MIME_TYPES))}."
        )
    if not content:
        raise LogoRejected("Fichier vide.")
    if len(content) > MAX_LOGO_BYTES:
        raise LogoRejected(
            f"Logo trop lourd ({len(content) // 1024} Ko) — maximum {MAX_LOGO_BYTES // 1024} Ko. "
            "Il est réinjecté à chaque interaction : un fichier lourd ralentirait toute "
            "l'application."
        )
    return f"data:{LOGO_MIME_TYPES[extension]};base64,{base64.b64encode(content).decode('ascii')}"


def decode_logo(data_uri: str):
    """
    Octets d'un logo encodé, ou `None` si la valeur n'est pas une URI de données exploitable.

    `st.logo()` accepte tout ce que `st.image()` accepte, y compris des octets : décoder ici évite
    de dépendre du support — variable selon les versions — des URI `data:` par le rendu Streamlit.
    """
    import base64

    if not data_uri or not data_uri.startswith("data:"):
        return None
    try:
        return base64.b64decode(data_uri.split(",", 1)[1])
    except (IndexError, ValueError):
        return None


def logo_for_streamlit(tokens: dict):
    """
    Valeur à passer à `st.logo()` : octets du logo client si configuré, sinon le mot-symbole acami.

    §29 — le repli n'est plus l'icône Material générique : tant qu'aucun client n'a téléversé son
    propre logo (le cas de cette installation elle-même), la barre latérale doit porter la marque
    réelle d'acami plutôt qu'un robot générique qui ne veut rien dire.
    """
    return decode_logo(tokens.get("BRAND_LOGO")) or _default_logo_bytes(tokens)


def favicon_for_streamlit(tokens: dict):
    """
    Valeur à passer à `st.set_page_config(page_icon=…)` : le logo du client, ou l'icône par défaut.

    `page_icon` accepte tout ce que `st.image` accepte, donc les octets décodés conviennent. Détail
    minuscule et très visible : un onglet de navigateur portant le logo Streamlit dans une
    application censée être aux couleurs du client annule une partie du travail de marque blanche —
    c'est le premier repère visuel de quelqu'un qui a dix onglets ouverts.

    Les SVG sont volontairement écartés : `st.image` ne les rend pas de façon fiable comme favicon,
    et un onglet vide serait pire que l'icône par défaut. Le SVG reste parfaitement utilisable comme
    logo dans la barre latérale.

    §29 — même repli qu'au-dessus : à défaut d'un logo client, l'onglet porte le disque rond
    d'acami (`_default_favicon_bytes`, un PNG — donc compatible avec la restriction ci-dessus) plutôt
    que l'icône Material générique.
    """
    if (tokens.get("BRAND_LOGO") or "").startswith("data:image/svg"):
        return _default_favicon_bytes() or DEFAULT_LOGO_ICON
    return decode_logo(tokens.get("BRAND_LOGO")) or _default_favicon_bytes() or DEFAULT_LOGO_ICON


# ── Feuille de style ──────────────────────────────────────────────────────────────────────────
def font_stack(tokens: dict) -> str:
    """Pile de polices CSS correspondant au jeton `BRAND_FONT`."""
    family = tokens.get("BRAND_FONT", "Inter")
    if family == "Système" or family not in _GOOGLE_FONTS:
        return _SYSTEM_FONT_STACK
    return f"'{family}', {_SYSTEM_FONT_STACK}"


def display_font_stack(tokens: dict) -> str:
    """
    Pile de titrage. « Identique au texte » renvoie la pile de texte : un client dont la charte
    n'a qu'une seule police doit pouvoir supprimer le contraste plutôt que de le subir.
    """
    family = tokens.get("BRAND_FONT_DISPLAY", "Fraunces")
    if family in ("", "Identique au texte", None):
        return font_stack(tokens)
    return f"'{family}', {_DISPLAY_FALLBACK}"


def font_import(tokens: dict) -> str:
    """
    Règles `@import` Google Fonts pour les trois rôles (texte, titrage, données).

    Chaque famille n'est demandée qu'une fois, et « Système » / « Identique au texte » n'ajoutent
    rien : sur un réseau qui bloque les CDN, la page reste lisible avec les piles de repli, et une
    installation qui a choisi la police système ne doit émettre aucun appel réseau — c'était déjà
    le contrat de cette fonction avant que le titrage et le monospace s'y ajoutent.
    """
    body = tokens.get("BRAND_FONT", "Inter")
    # « Système » est une promesse sur le RÉSEAU, pas sur une police : elle est choisie quand le
    # réseau du client bloque les CDN. Elle doit donc couper les trois imports, pas seulement celui
    # du texte — sinon ajouter le titrage et le monospace (§19) rouvrirait discrètement deux appels
    # externes sur les installations qui avaient précisément demandé qu'il n'y en ait aucun. Un test
    # existant l'a attrapé immédiatement ; les piles de repli (serif système, monospace système)
    # conservent la hiérarchie sans rien télécharger.
    if body not in _GOOGLE_FONTS:
        return ""

    urls = []
    for url in (_GOOGLE_FONTS.get(body),
                _GOOGLE_FONTS.get(tokens.get("BRAND_FONT_DISPLAY", "Fraunces")),
                _MONO_IMPORT):
        if url and url not in urls:
            urls.append(url)
    return "".join(f"@import url('{url}');\n" for url in urls)


def _variables(tokens: dict) -> str:
    """Bloc `:root` — le seul endroit où les jetons deviennent des valeurs CSS."""
    primary = tokens["BRAND_PRIMARY"]
    accent = tokens["BRAND_ACCENT"]
    background = tokens["BRAND_BACKGROUND"]
    text = tokens["BRAND_TEXT"]
    gap, pad, top = _DENSITY.get(tokens.get("BRAND_DENSITY"), _DENSITY["confortable"])
    dark = tokens.get("BRAND_MODE") == "sombre"
    # Les tons dérivés (survol, ombres, voiles) sont CALCULÉS à partir des deux couleurs de marque
    # plutôt que d'être des jetons supplémentaires : réclamer douze couleurs à un client dans son
    # cahier des charges est le meilleur moyen d'obtenir une palette incohérente.
    return f"""
:root {{
  --aca-primary: {primary};
  --aca-primary-rgb: {rgb_string(primary)};
  --aca-primary-hover: {mix(primary, "#FFFFFF" if dark else "#000000", 0.14)};
  --aca-primary-soft: {mix(primary, background, 0.88)};
  /* §25 — l'onglet de navigation ACTIF. La primaire pure (#0F4C81 sur ce déploiement) était jugée
     trop sombre en pleine surface : sur une barre claire, une pastille aussi dense se lit comme un
     bloc posé là plutôt que comme « vous êtes ici ». On l'éclaircit de 26 % — assez pour respirer,
     pas assez pour la confondre avec le survol, bien plus pâle (`--aca-primary-soft`, 88 %) :
     l'inversion de hiérarchie corrigée au §21 tient donc toujours.
     La couleur du texte est RECALCULÉE sur cette teinte-là plutôt qu'héritée de la primaire : sur
     une marque déjà claire, éclaircir encore le fond ferait passer un texte blanc sous le seuil de
     contraste, et l'onglet courant deviendrait le moins lisible de la barre. */
  --aca-nav-active: {mix(primary, "#000000" if dark else "#FFFFFF", 0.26)};
  --aca-nav-active-text: {readable_text_on(mix(primary, "#000000" if dark else "#FFFFFF", 0.26))};
  --aca-accent: {accent};
  --aca-accent-rgb: {rgb_string(accent)};
  --aca-bg: {background};
  --aca-surface: {tokens["BRAND_SURFACE"]};
  --aca-sidebar: {tokens["BRAND_SIDEBAR"]};
  --aca-text: {text};
  /* 0.34 et non 0.42 : à 0.42 le gris secondaire tombait à 4,2:1 sur les cartes — sous le seuil
     WCAG AA (4,5:1) — et c'est précisément la couleur des accroches, des relevés et des libellés
     d'indicateurs, c'est-à-dire du texte PETIT, celui pour lequel le seuil existe.
     La valeur a été calibrée sur les DIX-HUIT palettes livrées, pas sur un échantillon : un premier
     réglage à 0.38 passait sur les quatre que j'avais mesurées à la main et échouait sur
     « Industrie & BTP » (4,15:1), la seule dont le jeton `BRAND_TEXT` est plus clair que le défaut
     — le test paramétré sur toutes les palettes l'a rattrapé immédiatement. 0.34 laisse 4,73:1 au
     pire cas, donc de la marge quel que soit le fond choisi par le client. */
  --aca-muted: {mix(text, background, 0.34)};
  --aca-border: {tokens["BRAND_BORDER"]};
  --aca-success: {tokens["BRAND_SUCCESS"]};
  --aca-warning: {tokens["BRAND_WARNING"]};
  --aca-danger: {tokens["BRAND_DANGER"]};
  --aca-info: {tokens["BRAND_INFO"]};
  --aca-radius: {tokens["BRAND_RADIUS"]};
  --aca-radius-lg: calc({tokens["BRAND_RADIUS"]} * 1.5);
  --aca-gap: {gap};
  --aca-pad: {pad};
  --aca-top: {top};
  --aca-font: {font_stack(tokens)};
  --aca-display: {display_font_stack(tokens)};
  --aca-mono: {_MONO_STACK};
  --aca-on-primary: {readable_text_on(primary)};
  --aca-on-accent: {readable_text_on(accent)};
  --aca-shadow: 0 1px 2px rgba(16, 24, 40, {"0.35" if dark else "0.06"}),
                0 4px 16px rgba(16, 24, 40, {"0.30" if dark else "0.05"});
  --aca-shadow-lift: 0 10px 30px rgba(var(--aca-primary-rgb), {"0.30" if dark else "0.16"});
  /* §19 — hauteur réservée à la barre d'en-tête de Streamlit. La barre est en `position:
     absolute` avec un `z-index` de 999990 : tout ce que la page place au-dessus de cette hauteur
     passe DESSOUS. C'est la cause exacte du chevauchement signalé (en-tête 52,5 px contre
     30,8 px de marge haute). Exprimée en variable pour que la marge de la page et le fond de la
     barre ne puissent plus diverger.

     §21 — exprimée en PIXELS, plus en `rem`. `3.5rem` supposait une racine à 16 px ; or
     `config.toml` fixe `baseFontSize = 14`, donc 3,5 rem ne valait que 49 px face à une barre
     mesurée à 52,5 px sur le DOM réel. La variable censée ÊTRE la hauteur de la barre était donc
     plus courte qu'elle, et seul le `+ 1rem` du `max()` sauvait le dégagement. Une valeur qui
     prétend décrire une mesure doit décrire cette mesure : en px, elle ne dépend plus d'un réglage
     de taille de police qui vit dans un autre fichier. */
  --aca-header-h: 54px;

  /* §21 — vocabulaire de mouvement. Avant, chaque règle portait sa propre courbe écrite à la main
     (`ease`, `ease-out`, quatre `cubic-bezier` différents) : cinq dialectes pour une seule
     application, donc aucune cohérence perceptible entre deux éléments qui jouent le même rôle.
     Les courbes natives de CSS sont par ailleurs trop molles pour de l'interface — elles manquent
     l'attaque qui fait qu'un mouvement se lit comme une réponse et non comme un délai.

     `--aca-ease-out` pour ce qui ENTRE ou SORT (départ franc = réponse immédiate) ;
     `--aca-ease-in-out` pour ce qui SE DÉPLACE d'un point à un autre à l'écran ;
     `ease` reste implicite pour les simples changements de couleur.
     `ease-in` n'apparaît nulle part, et c'est délibéré : il démarre lentement, donc il retarde
     précisément l'instant que l'œil regarde le plus. */
  --aca-ease-out: cubic-bezier(.22, 1, .36, 1);
  --aca-ease-in-out: cubic-bezier(.77, 0, .175, 1);
  /* Intensité du fond d'ambiance, plus faible en mode sombre : sur fond clair le voile ASSOMBRIT
     légèrement la page, ce qui augmente le contraste du texte foncé ; sur fond sombre il l'ÉCLAIRE,
     ce qui le réduit. La même valeur dans les deux modes aurait donc été prudente d'un côté et
     risquée de l'autre. */
  --aca-veil-1: {"9%" if dark else "14%"};
  --aca-veil-2: {"6%" if dark else "10%"};
  /* Durées nommées plutôt que semées dans la feuille : une interface se règle d'un endroit.
     Bornées à 220 ms — au-delà, un outil ouvert huit heures par jour se met à sembler lent. */
  --aca-t-press: .12s;
  --aca-t-hover: .16s;
  --aca-t-enter: .22s;
}}
"""


# Animations. Séparées du reste pour que le niveau « aucune » se contente de ne pas les émettre,
# plutôt que de les émettre puis de les neutraliser — un `animation: none !important` laisserait le
# navigateur composer des couches inutiles à chaque rerun.
_KEYFRAMES = """
@keyframes aca-rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes aca-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes aca-pop { 0% { opacity: 0; transform: scale(.9); } 60% { transform: scale(1.03); } 100% { opacity: 1; transform: scale(1); } }
@keyframes aca-slide-in { from { opacity: 0; transform: translateX(-12px); } to { opacity: 1; transform: none; } }
@keyframes aca-drift { 0% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } 100% { background-position: 0% 50%; } }
@keyframes aca-sheen { 0% { transform: translateX(-120%); } 100% { transform: translateX(220%); } }
@keyframes aca-halo { 0%, 100% { opacity: .55; transform: scale(1); } 50% { opacity: .95; transform: scale(1.35); } }
@keyframes aca-float { 0%, 100% { transform: translate3d(0,0,0); } 50% { transform: translate3d(12px,-14px,0); } }
@keyframes aca-progress { 0% { background-position: 0 0; } 100% { background-position: 42px 0; } }
@keyframes aca-draw { from { transform: scaleY(0); } to { transform: scaleY(1); } }
@keyframes aca-ring { 0% { box-shadow: 0 0 0 0 rgba(var(--aca-primary-rgb), .45); } 70% { box-shadow: 0 0 0 9px rgba(var(--aca-primary-rgb), 0); } 100% { box-shadow: 0 0 0 0 rgba(var(--aca-primary-rgb), 0); } }
@keyframes aca-tick { from { opacity: 0; transform: translateY(6px) scale(.96); } to { opacity: 1; transform: none; } }
@keyframes aca-warn-glow { 0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--aca-warning) 35%, transparent); } 50% { box-shadow: 0 0 16px 2px color-mix(in srgb, var(--aca-warning) 55%, transparent); } }
/* Dérive du fond d'ambiance. `translate3d` + `scale` uniquement : ces deux propriétés sont
   composées par le GPU, donc la boucle ne déclenche ni calcul de disposition ni repeinture, ce qui
   compte pour la seule animation de la feuille qui ne s'arrête jamais. Animer
   `background-position` aurait été plus court à écrire et aurait repeint la page entière à chaque
   image. */
@keyframes aca-ambient {
  0%   { transform: translate3d(0, 0, 0) scale(1); }
  50%  { transform: translate3d(2.5%, -2%, 0) scale(1.06); }
  100% { transform: translate3d(0, 0, 0) scale(1); }
}
/* §26.2 — la TEXTURE a sa propre dérive, et c'est un écart délibéré au §21.

   Le voile dégradé partageait la boucle ci-dessus : 2,5 % d'un calque d'environ 1650 px, soit
   41 px en 24 s — 1,7 px/s. Sur un dégradé énorme et sans contour, c'est exactement ce qu'on veut
   (une matière dont on ne peut pas dire ce qui a changé). Sur une texture RÉPÉTÉE tous les 96 px,
   c'est invisible : un motif périodique translaté de moins d'une période, à cette vitesse, se lit
   comme immobile — rapporté comme tel après rendu, pas supposé.

   Trois amplitudes plutôt que deux, sur une période plus courte, et une trajectoire qui ne revient
   pas sur ses pas : ce qui rend un mouvement perceptible n'est pas sa vitesse mais son CHANGEMENT
   DE DIRECTION. La texture dérive donc contre le voile au lieu d'avec lui, et le grain se lit comme
   des particules qui traversent le fond — ce que fait précisément la masse de la page de
   présentation. Toujours `transform` seul, donc toujours composé par le GPU : aucune disposition,
   aucune repeinture. Animer `background-position` aurait fait scintiller les blocs un par un, plus
   proche encore de la page de présentation, au prix d'une repeinture plein écran à chaque image
   pour la seule boucle de la feuille qui ne s'arrête jamais — refusé pour un outil ouvert huit
   heures par jour. */
@keyframes aca-grain {
  0%   { transform: translate3d(0, 0, 0) scale(1.02); }
  34%  { transform: translate3d(-3.6%, 2.4%, 0) scale(1.07); }
  67%  { transform: translate3d(2.9%, -1.7%, 0) scale(1.03); }
  100% { transform: translate3d(0, 0, 0) scale(1.02); }
}
"""

_ANIMATIONS_FULL = """
/* §21 — DURÉES RAMENÉES SOUS 300 ms et courbes unifiées sur les jetons `--aca-ease-*`.
   Avant : 0,42 s d'entrée plus 0,17 s de décalage, soit près de 0,6 s avant qu'un écran soit
   stable. Une animation d'interface se juge à la vitesse à laquelle elle rend la main, pas à sa
   durée ; au-delà de ~300 ms elle cesse d'être perçue comme une réponse et devient une attente.

   Vérifié avant de toucher à quoi que ce soit, parce que l'hypothèse de départ était fausse :
   on pouvait croire que ces entrées se rejouaient à CHAQUE rerun Streamlit (donc des dizaines de
   fois par heure, ce qui aurait imposé de les supprimer purement et simplement). Mesure faite sur
   le DOM réel — `getAnimations()` avant et après un rerun provoqué par un widget — les animations
   restaient à `currentTime = 500 ms, playState = "finished"` de part et d'autre : React réconcilie
   les nœuds, l'animation ne redémarre pas. Elles ne jouent donc qu'au MONTAGE (premier affichage,
   changement de page), ce qui est exactement le cas d'usage légitime d'une animation d'entrée.
   Conclusion : on les garde, on les raccourcit. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] { animation: aca-rise .24s var(--aca-ease-out) both; }
/* Décalages resserrés dans la bande 30-80 ms : assez pour lire une cascade, trop court pour
   qu'on attende le dernier bloc. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(1) { animation-delay: 0s; }
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(2) { animation-delay: .04s; }
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(3) { animation-delay: .08s; }
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(n+4) { animation-delay: .12s; }

/* §25 — CHAQUE CARTE À SON APPARITION, pas toutes au montage.
   La cascade ci-dessus se joue une fois, au chargement : sur un tableau de bord de dix cartes dont
   sept sont sous la ligne de flottaison, la moitié de l'animation est donc jouée pour personne, et
   ce qu'on découvre en faisant défiler arrive déjà figé. `animation-timeline: view()` lie la
   progression à la position de l'élément dans la fenêtre : la carte se révèle quand on l'atteint,
   au rythme du défilement, et non sur une horloge.
   Zéro JavaScript, zéro IntersectionObserver, et le calcul reste hors du fil principal.
   Sous `@supports` : c'est du Chrome 115+/Edge, et un navigateur qui l'ignore garde simplement la
   cascade au montage — la carte s'affiche dans tous les cas, jamais masquée par une animation
   qu'on ne sait pas jouer (le piège classique du reveal au défilement).
   `range: entry 0% cover 22%` : terminé bien avant que la carte soit centrée, sinon on lit un
   graphe qui bouge encore. */
@supports (animation-timeline: view()) {
  @media (prefers-reduced-motion: no-preference) {
    [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
      /* La durée est IGNORÉE dès qu'une timeline de défilement est attachée — la progression suit
         la position dans la fenêtre, pas une horloge. On la laisse néanmoins sous les 300 ms de la
         règle maison : elle sert de repli exact si `animation-timeline` venait à être retiré, et
         une valeur qui contredirait la règle sans effet visible serait un piège pour la relecture
         suivante. */
      animation: aca-rise .24s var(--aca-ease-out) both;
      animation-timeline: view();
      animation-range: entry 0% cover 22%;
      animation-delay: 0s;
    }
  }
}

/* Fond d'ambiance : 48 s par cycle, et cette lenteur EST le réglage. À 10 s on suit le mouvement
   des yeux ; à 48 s l'écran n'est jamais tout à fait le même sans qu'on puisse dire ce qui a
   changé — c'est-à-dire une matière, pas un objet en déplacement. Bornée au niveau « complet » :
   un client qui a choisi « sobre » demande le calme, et le dégradé statique lui reste acquis
   (cf. `_SURFACES`). `prefers-reduced-motion` gèle la boucle en gardant le dégradé, ce qui est
   exactement le comportement attendu — moins de mouvement, pas moins d'interface.

   §26.2 — la texture de blocs a sa PROPRE boucle (`aca-grain`, 26 s), et non celle-ci. Partagée,
   elle était invisible : voir le commentaire de l'image-clé. Ce qui vaut pour un dégradé énorme ne
   vaut pas pour un motif qui se répète tous les 96 px. */
[data-testid="stAppViewContainer"]::before {
  animation: aca-ambient 48s var(--aca-ease-in-out) infinite;
  will-change: transform;
}
[data-testid="stAppViewContainer"]::after {
  animation: aca-grain 26s var(--aca-ease-in-out) infinite;
  will-change: transform;
}

[data-testid="stMetric"] { animation: aca-pop .26s var(--aca-ease-out) both; }
[data-testid="stAlert"] { animation: aca-pop .2s var(--aca-ease-out) both; }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] { animation: aca-slide-in .22s var(--aca-ease-out) both; }

/* Barre de navigation supérieure : entrée au chargement, puis chaque lien apparaît en léger
   décalage — le même principe d'entrée échelonnée que le rail de décision, appliqué à la
   navigation elle-même. `.rc-overflow`/`.rc-overflow-item` (bibliothèque tierce que Streamlit
   utilise pour cette rangée, classes stables car définies par la bibliothèque elle-même plutôt
   que hachées par Streamlit à chaque version) sont le vrai conteneur et le vrai élément répété —
   `[data-testid="stTopNavLinkContainer"]` est un **enfant unique** de son propre wrapper interne,
   donc `:nth-child` sur lui-même ne différenciait jamais rien (toujours 1er de son parent) : c'est
   `.rc-overflow-item:nth-child(N)` qui compte réellement la position parmi les liens. Bug trouvé
   en inspectant le DOM réellement rendu (Playwright), invisible en ne relisant que le CSS. */
.rc-overflow:has([data-testid="stTopNavLinkContainer"]) { animation: aca-rise .24s var(--aca-ease-out) both; }
[data-testid="stTopNavLinkContainer"] { animation: aca-tick .2s var(--aca-ease-out) both; }
.rc-overflow-item:nth-child(1) [data-testid="stTopNavLinkContainer"] { animation-delay: .02s; }
.rc-overflow-item:nth-child(2) [data-testid="stTopNavLinkContainer"] { animation-delay: .05s; }
.rc-overflow-item:nth-child(3) [data-testid="stTopNavLinkContainer"] { animation-delay: .08s; }
.rc-overflow-item:nth-child(n+4) [data-testid="stTopNavLinkContainer"] { animation-delay: .11s; }

/* Bannière de sécurité : lueur qui respire, pour qu'un point de configuration manquant avant une
   mise en ligne ne se noie pas visuellement parmi les autres accordéons de la page. */
.st-key-security_banner [data-testid="stExpander"] details { animation: aca-warn-glow 2.6s ease-in-out infinite; }

/* Reflet qui balaie les boutons principaux au survol : le seul mouvement purement décoratif
   retenu, parce qu'il porte sur l'action la plus importante de l'écran (« Valider »). */
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] { position: relative; overflow: hidden; }
.stButton button[kind="primary"]::after, .stFormSubmitButton button[kind="primary"]::after {
  content: ""; position: absolute; top: 0; left: 0; width: 45%; height: 100%;
  background: linear-gradient(100deg, transparent, rgba(255,255,255,.34), transparent);
  transform: translateX(-120%);
}
.stButton button[kind="primary"]:hover::after, .stFormSubmitButton button[kind="primary"]:hover::after { animation: aca-sheen .85s ease-out; }

[data-testid="stSpinner"] > div { animation: aca-fade .25s ease-out both; }
.stProgress > div > div > div {
  background-image: linear-gradient(115deg, rgba(255,255,255,.28) 25%, transparent 25%, transparent 50%, rgba(255,255,255,.28) 50%, rgba(255,255,255,.28) 75%, transparent 75%);
  background-size: 42px 42px; animation: aca-progress .9s linear infinite;
}
.aca-pulse::after { animation: aca-halo 1.6s ease-in-out infinite; }

/* ── Animations de la trousse de composants (§18) ────────────────────────────────────────────
   Chacune porte une information, aucune n'est décorative — c'est le critère retenu pour un outil
   qu'un opérateur garde ouvert huit heures par jour. */

/* Le rail se DESSINE de haut en bas : le mouvement raconte la séquence du traitement, et l'œil
   arrive naturellement sur le dernier maillon, qui est la décision demandée. */
/* Le rail garde le décalage le plus long de la feuille, et c'est le seul endroit où c'est mérité :
   ici la cascade N'EST PAS décorative, elle raconte l'ordre réel du traitement et amène l'œil sur
   le dernier maillon, qui est la décision demandée. La durée par étape passe quand même sous la
   barre des 300 ms — c'est la séquence qui doit être lisible, pas chaque étape qui doit être lente. */
.aca-rail__step { animation: aca-tick .26s var(--aca-ease-out) both; }
.aca-rail__step:nth-child(1) { animation-delay: .02s; }
.aca-rail__step:nth-child(2) { animation-delay: .07s; }
.aca-rail__step:nth-child(3) { animation-delay: .12s; }
.aca-rail__step:nth-child(4) { animation-delay: .17s; }
.aca-rail__step:nth-child(5) { animation-delay: .22s; }
.aca-rail__step:nth-child(n+6) { animation-delay: .27s; }
.aca-rail__step:not(:last-child)::before { transform-origin: top; animation: aca-draw .24s var(--aca-ease-out) both; animation-delay: .16s; }
/* L'étape en cours respire : elle signale « c'est ici que ça se passe » sans texte supplémentaire. */
.aca-rail__step--active .aca-rail__marker { animation: aca-ring 2s ease-out infinite; }

/* La frise apparaît dans l'ordre chronologique, ce qui est le sens de lecture attendu. */
.aca-tl__item { animation: aca-tick .22s var(--aca-ease-out) both; }
.aca-tl__item:nth-child(1) { animation-delay: .02s; }
.aca-tl__item:nth-child(2) { animation-delay: .05s; }
.aca-tl__item:nth-child(3) { animation-delay: .08s; }
.aca-tl__item:nth-child(n+4) { animation-delay: .11s; }

.aca-stat { animation: aca-pop .26s var(--aca-ease-out) both; }
.aca-stat:nth-child(2) { animation-delay: .04s; }
.aca-stat:nth-child(3) { animation-delay: .08s; }
.aca-stat:nth-child(4) { animation-delay: .12s; }
.aca-stat:nth-child(n+5) { animation-delay: .16s; }

/* §21 — l'icône d'état vide ne flotte plus en boucle. C'était le seul mouvement PERPÉTUEL et
   purement décoratif de la feuille : dans un outil qu'un opérateur garde ouvert toute la journée,
   une animation infinie en périphérie du regard attire l'œil sans jamais rien signaler, et finit
   par se disputer l'attention avec les deux boucles qui, elles, signalent vraiment quelque chose
   (le pouls d'une analyse en cours, la lueur de la bannière de sécurité). Retirer un accessoire
   rend les deux autres audibles. L'apparition en fondu, elle, reste : elle a une fin. */
.aca-empty { animation: aca-fade .22s var(--aca-ease-out) both; }
"""

_ANIMATIONS_SUBTLE = """
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] { animation: aca-fade .2s var(--aca-ease-out) both; }
[data-testid="stAlert"] { animation: aca-fade .2s var(--aca-ease-out) both; }
.rc-overflow:has([data-testid="stTopNavLinkContainer"]) { animation: aca-fade .2s var(--aca-ease-out) both; }
"""

# Polish visuel indépendant du niveau d'animation : bordures, ombres, états de survol. Ce sont des
# TRANSITIONS (réaction à une action de l'utilisateur), pas des animations autonomes — elles restent
# donc en mode « aucune », où seul le bloc `prefers-reduced-motion` les réduit à zéro.
_SURFACES = """
[data-testid="stAppViewContainer"] { background: var(--aca-bg); }

/* §26.3 — les deux voiles d'ambiance ne sont plus ici. Ils dépendent désormais de trois réglages
   (style, intensité, couleur) et vivent donc dans `_ambient()`, qui est calculé ; une constante ne
   peut pas en tenir compte. Ce qui reste ici est structurel, pas décoratif.

   Le voile est en `position: fixed` dans le même contexte d'empilement que le contenu : sans cette
   ligne, il passerait DEVANT la page au lieu de derrière. Règle structurelle, pas décorative. */
[data-testid="stMain"], [data-testid="stSidebar"], [data-testid="stHeader"] {
  position: relative;
  z-index: 1;
}

/* §19 — CORRECTION DU CHEVAUCHEMENT. La barre d'en-tête de Streamlit (qui contient la navigation
   haute) est `position: absolute`, `z-index: 999990`, fond transparent, et mesure ~52 px : elle est
   donc DESSINÉE PAR-DESSUS le début de la page. L'ancienne marge haute valait `var(--aca-top)`,
   soit 2,2 rem (~35 px) en densité confortable — plus courte que la barre, d'où l'en-tête de marque
   passant sous la navigation (capture d'écran de l'utilisateur). Deux corrections complémentaires :

   1. `max()` garantit un dégagement au moins égal à la hauteur de barre, quelle que soit la
      densité choisie. Un `max()` plutôt qu'une valeur fixe : la densité « aérée » doit pouvoir
      ajouter de l'air, jamais en retirer sous le seuil de sécurité.
   2. La barre reçoit un vrai fond opaque. Sans lui, le contenu défilant passerait en transparence
      derrière la navigation — et une barre sans matière lisait comme une pastille flottante posée
      au hasard, ce qui est aussi un défaut de design, pas seulement de position. */
[data-testid="stHeader"] {
  background: var(--aca-bg);
  border-bottom: 1px solid var(--aca-border);
}
[data-testid="stMain"] .block-container {
  padding-top: max(var(--aca-top), calc(var(--aca-header-h) + 1rem));
  max-width: 1500px;
}
[data-testid="stMain"] [data-testid="stVerticalBlock"] { gap: var(--aca-gap); }
html, body, [data-testid="stAppViewContainer"], .stMarkdown, .stMarkdown p { font-family: var(--aca-font); }

/* Trois rôles typographiques (§19). Les titres passent à la face de titrage : c'est le contraste
   serif/sans qui porte la hiérarchie, plutôt qu'une simple différence de graisse dans une seule
   famille — laquelle donne à toutes les pages le même aplat indifférencié.

   §21 — RÈGLE MORTE, corrigée. `h1, h2, h3 { … }` a une spécificité de (0,0,1) et perdait contre
   la règle interne de Streamlit `.st-emotion-cache-XXXX h1, … h6` (0,1,1), qui réimpose la police
   de texte sur TOUS les titres. Constaté sur le DOM réel : un `h3` de page calculait
   « Segoe UI, Open Sans », jamais la serif. Conséquence — la moitié de la thèse typographique de
   §19 (« la serif porte la voix du document ») n'existait que dans la feuille de style : le seul
   titre réellement en serif était l'en-tête de marque, et par accident, parce qu'il est ciblé par
   une CLASSE (`.aca-hero__title`, 0,1,0) et non par son nom d'élément.

   Le correctif ancre les titres sur les conteneurs que Streamlit pose lui-même autour d'eux
   (`stMarkdownContainer`, `stHeadingWithActionElements`) : (0,1,1) contre (0,1,1), et la nôtre
   vient après dans la feuille. Pas de `!important` — une montée de version doit pouvoir reprendre
   la main sans qu'on ait à démonter une surenchère de priorités. */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stHeadingWithActionElements"] h1,
[data-testid="stHeadingWithActionElements"] h2,
[data-testid="stHeadingWithActionElements"] h3,
h1, h2, h3 {
  font-family: var(--aca-display);
  letter-spacing: -.015em;
  font-weight: 600;
}
/* h4-h6 sont des sous-titres de formulaire, pas la voix du document : ils restent en sans, sinon
   la serif cesse d'être un signal et devient le style par défaut de tout ce qui est un titre. */
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6,
h4, h5, h6 { font-family: var(--aca-font); letter-spacing: -.008em; }

/* Valeurs machine en chiffres tabulaires : dans une file d'attente, des compteurs qui ne s'alignent
   pas verticalement se comparent mal — c'est de la lisibilité, pas du style. */
[data-testid="stMetricValue"], .aca-mono, code, [data-testid="stCode"] {
  font-family: var(--aca-mono);
  font-variant-numeric: tabular-nums;
}

/* Cartes : le conteneur borduré est l'unité de composition de cette application (fiche prospect,
   proposition, KPI, entrée de file d'attente). Une seule règle les met toutes d'accord. */
[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
  border-radius: var(--aca-radius-lg);
  /* §21 — une ombre TRÈS basse, en permanence. Le parti pris « les cartes sont des documents posés
     sur un plan de travail » reposait uniquement sur la différence entre `--aca-surface` et
     `--aca-bg` ; or rien n'oblige un client à les choisir distinctes, et plusieurs palettes
     livrées les rendent quasi identiques (mesuré : #F2F6FB sur #F5F5F5, soit 1,01:1 — les cartes
     ne tenaient plus que par leur filet de 1 px). Une ombre portée ne dépend d'aucune des deux
     couleurs : la séparation devient une propriété du système, pas un coup de chance de palette. */
  box-shadow: 0 1px 2px rgba(16, 24, 40, .04);
  transition: box-shadow var(--aca-t-hover) ease,
              border-color var(--aca-t-hover) ease;
}
@media (hover: hover) and (pointer: fine) {
  [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: rgba(var(--aca-primary-rgb), .38);
    box-shadow: 0 2px 8px rgba(16, 24, 40, .07);
  }
}

/* §19 — dégradés retirés d'ici. Ils étaient partout (en-tête, boutons, cartes de KPI, navigation),
   et un effet appliqué à tout ne hiérarchise rien : c'est la marque la plus reconnaissable d'une
   interface produite au gabarit. Un seul dégradé subsiste dans toute l'application, sur le bloc de
   décision (`.aca-signoff`) — le seul endroit où quelqu'un doit agir. */
[data-testid="stMetric"] {
  background: var(--aca-surface);
  border: 1px solid var(--aca-border); border-radius: var(--aca-radius-lg);
  padding: calc(var(--aca-pad) * .9) var(--aca-pad); position: relative; overflow: hidden;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
/* §22 — RANGÉES D'INDICATEURS À HAUTEUR ÉGALE. Streamlit pose `align-items: start` sur ses blocs
   horizontaux (relevé sur le DOM), si bien que chaque carte se dimensionne sur son propre contenu :
   il suffit qu'un indicateur n'ait pas d'écart à afficher — faute de période de comparaison — pour
   qu'il soit 22 px plus court que ses voisins et que la rangée parte en dents de scie. Le défaut
   n'apparaît donc que sur certains jeux de données, ce qui est la meilleure façon de ne jamais le
   corriger.

   Le `:has()` restreint la règle aux rangées qui contiennent VRAIMENT des indicateurs : les autres
   blocs horizontaux (une barre de contrôles alignée en bas, une ligne de boutons) doivent garder
   l'alignement demandé par le code appelant. */
[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { align-items: stretch; }
/* `align-items: stretch` ne suffit PAS seul, et la raison est une subtilité de flexbox qui coûte
   une demi-heure quand on ne la connaît pas : un élément flex dont la taille transversale est
   DÉFINIE ignore l'étirement. Streamlit fixe une hauteur calculée sur ces conteneurs, si bien que
   la règle ci-dessus s'appliquait sans aucun effet visible (constaté sur le DOM : la rangée passait
   bien à `stretch`, les cartes gardaient 144 / 144 / 122 / 144). Il faut donc rendre la hauteur
   `auto` avant de pouvoir l'étirer. */
[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > [data-testid="stElementContainer"] {
  height: auto;
  align-self: stretch;
}
[data-testid="stHorizontalBlock"] [data-testid="stMetric"] { height: 100%; }

[data-testid="stMetric"]::before {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 2px;
  background: var(--aca-primary); opacity: .55;
}
[data-testid="stMetric"]:hover { transform: translateY(-2px); box-shadow: var(--aca-shadow); border-color: rgba(var(--aca-primary-rgb), .45); }
[data-testid="stMetricValue"] { font-weight: 600; letter-spacing: -.02em; }
[data-testid="stMetricLabel"] {
  color: var(--aca-muted); font-weight: 500; font-size: .72rem;
  text-transform: uppercase; letter-spacing: .07em;
}

/* Boutons — aplats francs. La couleur principale suffit à désigner l'action ; le dégradé ne
   faisait qu'ajouter du bruit à un élément déjà saillant par sa forme et son contraste. */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {
  border-radius: var(--aca-radius); font-weight: 550;
  transition: transform var(--aca-t-press) var(--aca-ease-out),
              box-shadow var(--aca-t-hover) ease,
              background var(--aca-t-hover) ease,
              border-color var(--aca-t-hover) ease;
}
/* §21 — le survol est réservé aux pointeurs fins. Sur un écran tactile, `:hover` se déclenche au
   toucher et RESTE actif après : le bouton qu'on vient d'utiliser garde son état de survol, ce qui
   se lit comme « toujours sélectionné ». Un état visuel qui ment sur ce qui est en cours coûte plus
   cher que l'effet qu'il apporte. */
@media (hover: hover) and (pointer: fine) {
  .stButton button:hover, .stFormSubmitButton button:hover, .stDownloadButton button:hover {
    transform: translateY(-1px);
  }
}
/* §21 — RETOUR D'APPUI. Avant, `:active` se contentait d'annuler le décalage du survol : appuyer
   ne produisait donc aucun signal propre, seulement l'absence d'un autre. Un enfoncement franc
   (0.97) est la confirmation la moins chère qu'une interface puisse donner — elle dit « c'est
   entendu » avant même que le serveur ait répondu, ce qui compte d'autant plus ici où chaque clic
   déclenche un rerun Streamlit complet. `scale` plutôt que `translateY` parce que l'échelle
   emporte aussi le contenu du bouton : c'est le bouton entier qui s'enfonce, pas une étiquette qui
   glisse. */
.stButton button:active, .stFormSubmitButton button:active, .stDownloadButton button:active {
  transform: scale(.97);
  transition-duration: var(--aca-t-press);
}

/* §22 — les CONTRÔLES DE SÉLECTION reçoivent enfin le même retour d'appui que les boutons. Le §21
   n'avait couvert que `.stButton`, `.stFormSubmitButton` et `.stDownloadButton` ; or un
   `st.segmented_control` et un `st.pills` rendent un `[data-testid="stButtonGroup"]`, qui ne
   correspond à aucun des trois. Les deux commandes principales du tableau de bord (la période et
   la bascule de comparaison) étaient donc les seuls éléments cliquables de l'application à
   n'accuser aucun enfoncement — précisément ceux qu'on actionne le plus souvent sur cet écran.

   Au passage, Streamlit anime `all` sur ces boutons. `all` inclut la géométrie : à chaque rerun,
   une largeur qui change d'un pixel devient une transition visible, et le composant paraît
   « mou ». On restreint aux propriétés réellement concernées. */
[data-testid="stButtonGroup"] button {
  transition: background-color var(--aca-t-hover) ease,
              color var(--aca-t-hover) ease,
              border-color var(--aca-t-hover) ease,
              transform var(--aca-t-press) var(--aca-ease-out);
}
[data-testid="stButtonGroup"] button:active { transform: scale(.97); }
[data-testid="stButtonGroup"] button:focus-visible {
  outline: 2px solid var(--aca-primary);
  outline-offset: 2px;
}

/* §21 — visibilité au clavier. Les champs avaient déjà un anneau de marque ; les BOUTONS et les
   LIENS n'avaient rien, donc une personne qui navigue au clavier ne pouvait pas savoir où elle se
   trouvait — sur l'écran de validation, cela veut dire ne pas savoir quel bouton on est sur le
   point d'actionner. `:focus-visible` et non `:focus` : la bague n'apparaît qu'à la navigation
   clavier, jamais après un clic souris. */
.stButton button:focus-visible,
.stFormSubmitButton button:focus-visible,
.stDownloadButton button:focus-visible,
[data-testid="stTopNavLink"]:focus-visible,
summary:focus-visible,
a:focus-visible {
  outline: 2px solid var(--aca-primary);
  outline-offset: 2px;
  border-radius: var(--aca-radius);
}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {
  background: var(--aca-primary);
  border: none; color: var(--aca-on-primary); box-shadow: 0 1px 2px rgba(16,24,40,.16);
}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primary"]:hover {
  background: var(--aca-primary-hover); box-shadow: 0 4px 14px rgba(var(--aca-primary-rgb), .30);
}

/* Barre de navigation supérieure (§18, `st.navigation(position="top")`) — remplace l'ancien
   `st.tabs()` du fichier unique pré-restructuration. `stTopNavLinkContainer`/`stTopNavLink` sont les
   véritables identifiants que Streamlit 1.59 pose sur ce composant (confirmés dans son bundle
   compilé, `.stTabs`/`tab-baseweb` ne le touchent plus du tout depuis la découpe en pages — un
   sélecteur mort qui laissait la barre sans habillage). **Bug corrigé** (retour utilisateur : la
   barre n'était ni visible ni centrée — vérifié en inspectant le DOM réellement rendu via
   Playwright, pas en relisant seulement le CSS) : `*:has(> [data-testid="stTopNavLinkContainer"])`
   ne ciblait PAS la rangée flexbox qui aligne les quatre liens. `stTopNavLinkContainer` est un
   enfant **unique** de son propre wrapper Streamlit interne (classe hachée, privée à cette
   version) — ce sélecteur n'atteignait donc que ce petit wrapper individuel, un par lien : ni le
   fond, ni la bordure, ni le centrage ne pouvaient jamais s'appliquer à la barre dans son
   ensemble, seulement à chaque lien pris isolément (d'où une barre plate malgré le CSS). Le vrai
   conteneur qui aligne les quatre liens est fourni par `rc-overflow`, la bibliothèque tierce que
   Streamlit utilise pour cette rangée — `.rc-overflow`/`.rc-overflow-item` sont des classes
   stables (définies par la bibliothèque elle-même, pas hachées par Streamlit à chaque version),
   donc un ciblage direct est plus fiable ici qu'un `:has()` devinant la structure. */
.rc-overflow:has([data-testid="stTopNavLinkContainer"]) {
  display: flex;
  justify-content: center;
  background: linear-gradient(135deg, rgba(var(--aca-primary-rgb), .1), var(--aca-surface));
  border: 1px solid rgba(var(--aca-primary-rgb), .28);
  border-radius: var(--aca-radius-lg);
  padding: .35rem .5rem;
  box-shadow: var(--aca-shadow-lift);
}
[data-testid="stTopNavLinkContainer"] { margin: 0 .35rem; }
[data-testid="stTopNavLink"] {
  border-radius: var(--aca-radius);
  font-weight: 600;
  /* `transform`/`filter` listés explicitement : sans eux l'enfoncement ci-dessous serait instantané
     au clic puis reviendrait sec au relâchement. Jamais `all` — on n'anime que ce qu'on nomme. */
  transition: background var(--aca-t-hover) ease, color var(--aca-t-hover) ease,
              transform var(--aca-t-press) var(--aca-ease-out),
              filter var(--aca-t-press) var(--aca-ease-out),
              box-shadow var(--aca-t-hover) ease;
}
/* §21 — la PAGE COURANTE porte la marque. Relevé sur le DOM réel : l'onglet actif recevait
   `rgba(173,173,173,.25)`, un gris de Streamlit, tandis que le survol recevait la couleur de
   marque, une élévation et une ombre. L'état le plus fort désignait donc l'endroit où le curseur
   passe, pas l'endroit où l'on se trouve — la hiérarchie était littéralement inversée, et sur une
   barre à sept entrées c'est la seule information qui compte. `aria-current` est posé par
   Streamlit lui-même : on s'ancre sur la sémantique, pas sur une classe de version. */
/* §25 — l'onglet actif était un aplat de la couleur de marque : juste, mais plat, et il se lisait
   comme une pastille posée là plutôt que comme une touche. Un dégradé très court (haut plus clair,
   bas plus sombre) et un liseré intérieur clair lui donnent de la matière — le vieux procédé du
   bouton « glossy », dosé pour rester sobre : ~10 % d'écart, pas un reflet de vitrine. La
   lisibilité du texte n'en dépend pas (`--aca-on-primary` est calculé sur la primaire elle-même),
   donc aucune palette client ne peut la casser par ce biais.
   Le raccourci `background` est posé D'ABORD, puis `background-image` par-dessus : l'aplat reste
   la déclaration de base (un navigateur qui ignorerait le dégradé garde la bonne couleur), et
   l'invariant « la page courante porte la couleur de marque » reste lisible tel quel. */
[data-testid="stTopNavLink"][aria-current],
[data-testid="stTopNavLinkContainer"]:has([aria-current]) [data-testid="stTopNavLink"] {
  background: var(--aca-nav-active);
  background-image: linear-gradient(180deg,
                    rgba(255,255,255,.22) 0%, rgba(255,255,255,0) 48%, rgba(0,0,0,.07) 100%);
  color: var(--aca-nav-active-text);
  box-shadow: 0 1px 3px rgba(var(--aca-primary-rgb), .35),
              inset 0 1px 0 rgba(255,255,255,.28);
}
/* Retour au clic. Un lien de navigation déclenche un changement de page : sans réponse immédiate,
   la personne clique une deuxième fois pendant que Streamlit rejoue le script. L'enfoncement se
   voit donc AVANT que la page ne réponde — c'est tout le rôle de cet état. */
[data-testid="stTopNavLink"]:active {
  transform: scale(.97);
  filter: brightness(1.06);
}
[data-testid="stTopNavLink"][aria-current]:active,
[data-testid="stTopNavLinkContainer"]:has([aria-current]) [data-testid="stTopNavLink"]:active {
  box-shadow: inset 0 2px 5px rgba(0,0,0,.22);
}
/* Le survol reste volontairement DISCRET : il indique une cible atteignable, pas une position.
   Plus d'élévation ni d'ombre ici — c'était ce qui le faisait passer devant l'état actif. */
@media (hover: hover) and (pointer: fine) {
  [data-testid="stTopNavLink"]:hover:not([aria-current]) {
    background: var(--aca-primary-soft);
    color: var(--aca-primary);
  }
}

/* Bannière de sécurité (§18, `key="security_banner"` dans ui.py — un ancrage stable indépendant du
   texte affiché, que le nombre de points à corriger fait varier). Un point de configuration
   manquant en production est le genre d'alerte qu'on ne veut pas pouvoir manquer en balayant la
   page des yeux — d'où un traitement « avertissement », pas un simple accordéon parmi d'autres. */
.st-key-security_banner [data-testid="stExpander"] details {
  background: linear-gradient(135deg, color-mix(in srgb, var(--aca-warning) 12%, var(--aca-surface)), var(--aca-surface));
  border: 1px solid color-mix(in srgb, var(--aca-warning) 45%, var(--aca-border));
  border-radius: var(--aca-radius-lg);
}
.st-key-security_banner summary { font-weight: 650; }
.st-key-security_banner [data-testid="stExpanderIconSpan"], .st-key-security_banner svg { color: var(--aca-warning); }

/* Barre latérale */
[data-testid="stSidebar"] { background: var(--aca-sidebar); border-right: 1px solid var(--aca-border); }
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] { border-radius: var(--aca-radius); }

/* Champs : anneau de focus aux couleurs de la marque plutôt que le bleu Streamlit */
input:focus, textarea:focus, [data-baseweb="select"] > div:focus-within {
  border-color: var(--aca-primary) !important;
  box-shadow: 0 0 0 3px rgba(var(--aca-primary-rgb), .18) !important;
}
[data-testid="stExpander"] details { border-radius: var(--aca-radius); border-color: var(--aca-border); }
[data-testid="stAlert"] { border-radius: var(--aca-radius); }
[data-testid="stDataFrame"] { border-radius: var(--aca-radius); overflow: hidden; }
"""

_HERO = """
/* §19 — l'en-tête n'est plus un pavé dégradé à orbes floutées. C'était littéralement la réponse
   type d'un gabarit (grand bloc coloré, titre, accroche, pastilles), et elle occupait le haut de
   CHAQUE page sans jamais rien apprendre à personne après la première lecture.
   Ce qui la remplace est un CARTOUCHE de document : filet vertical à gauche comme une marge de
   dossier, titre en serif de titrage, et surtout des pastilles devenues des relevés — police
   monospace, chiffres tabulaires — parce que « 2 analyses en attente » est une valeur qui change,
   pas un ornement. Le produit est un poste de tri : son en-tête doit se lire comme un cadran, pas
   comme une bannière marketing. */
.aca-hero {
  position: relative; overflow: hidden;
  border-radius: var(--aca-radius);
  border: 1px solid var(--aca-border);
  border-left: 3px solid var(--aca-primary);
  padding: calc(var(--aca-pad) * .95) calc(var(--aca-pad) * 1.15);
  margin-bottom: var(--aca-gap);
  color: var(--aca-text);
  background: var(--aca-surface);
}
.aca-hero__title {
  font-family: var(--aca-display);
  font-size: 1.6rem; font-weight: 600; letter-spacing: -.02em; margin: 0; line-height: 1.15;
  color: var(--aca-text);
}
.aca-hero__tagline {
  margin: .3rem 0 0; font-size: .86rem; max-width: 74ch; color: var(--aca-muted);
}
.aca-hero__pills {
  display: flex; flex-wrap: wrap; gap: .45rem; margin-top: .8rem;
  padding-top: .7rem; border-top: 1px solid var(--aca-border);
}
.aca-hero__pill {
  display: inline-flex; align-items: center; gap: .35rem;
  font-family: var(--aca-mono); font-variant-numeric: tabular-nums;
  font-size: .72rem; font-weight: 500; letter-spacing: .01em;
  padding: .2rem .5rem; border-radius: 4px;
  background: var(--aca-bg); color: var(--aca-muted);
  border: 1px solid var(--aca-border);
}
/* Une seule pastille peut réclamer l'attention, et elle le fait avec la couleur d'accent —
   celle qui, dans toute l'application, ne signifie qu'une chose : quelque chose attend une
   décision humaine. */
.aca-hero__pill--alert {
  background: color-mix(in srgb, var(--aca-accent) 14%, var(--aca-surface));
  color: var(--aca-accent);
  border-color: color-mix(in srgb, var(--aca-accent) 45%, transparent);
  font-weight: 600;
}
.aca-hero__orb { display: none; }
"""

_HERO_ANIMATED = """
.aca-hero { animation: aca-drift 14s ease-in-out infinite; }
.aca-hero__orb--a { animation: aca-float 11s ease-in-out infinite; }
.aca-hero__orb--b { animation: aca-float 15s ease-in-out infinite reverse; }
"""

# Variantes d'en-tête. Depuis §19 la base est le cartouche sur papier ; ces deux blocs restent les
# options qu'un client peut préférer — un bandeau plein à ses couleurs, ou rien du tout. Chacun
# redéfinit la couleur du texte ET celle des pastilles : changer le fond sans elles produirait du
# texte sombre sur fond sombre, c'est-à-dire un en-tête illisible réglable depuis l'interface.
_HERO_FLAT = """
.aca-hero {
  background: var(--aca-primary); color: var(--aca-on-primary);
  border-color: transparent; border-left-color: var(--aca-accent);
}
.aca-hero__title { color: var(--aca-on-primary); }
.aca-hero__tagline { color: var(--aca-on-primary); opacity: .88; }
.aca-hero__pills { border-top-color: rgba(255,255,255,.22); }
.aca-hero__pill {
  background: rgba(255,255,255,.14); color: var(--aca-on-primary);
  border-color: rgba(255,255,255,.26);
}
.aca-hero__pill--alert {
  background: var(--aca-accent); color: var(--aca-on-accent); border-color: transparent;
}
"""

_HERO_PLAIN = """
.aca-hero {
  background: transparent; color: var(--aca-text); box-shadow: none;
  border: none; border-bottom: 1px solid var(--aca-border); border-radius: 0;
  padding-left: 0; padding-right: 0;
}
.aca-hero__pill { background: var(--aca-surface); border-color: var(--aca-border); color: var(--aca-muted); }
"""

# ── Trousse de composants (§18) ───────────────────────────────────────────────────────────────
# Styles de `aca/core/ui_kit.py`. Ils vivent ici, et non dans `ui_kit.py`, pour qu'il n'existe qu'UN
# endroit d'où sorte la feuille de style : deux sources de CSS finiraient par se contredire sur la
# spécificité des sélecteurs, et le débogage d'un padding qui « ne prend pas » coûte des heures.
_UI_KIT = """
/* Glyphes Material en ligne. `:material/x:` ne fonctionne que dans les libellés Streamlit, pas
   dans du HTML injecté ; la police est déjà chargée par la page, donc aucune requête ajoutée. */
.aca-i {
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined';
  font-weight: normal; font-style: normal; line-height: 1;
  font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
  -webkit-font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased;
  vertical-align: -.15em; user-select: none;
}

/* En-têtes de section : une seule hiérarchie pour toute l'application. */
.aca-section { margin: 0 0 .55rem; }
.aca-section__eyebrow {
  display: inline-block; font-size: .68rem; font-weight: 700; letter-spacing: .1em;
  text-transform: uppercase; color: var(--aca-primary); margin-bottom: .2rem;
}
.aca-section__title {
  display: flex; align-items: center; gap: .45rem; margin: 0;
  font-size: 1.02rem; font-weight: 640; letter-spacing: -.012em; color: var(--aca-text);
}
.aca-section__title .aca-i { font-size: 1.15rem; color: var(--aca-primary); }
.aca-section__sub { margin: .22rem 0 0; font-size: .82rem; color: var(--aca-muted); max-width: 78ch; }

/* ── Rail de décision : le composant signature ──────────────────────────────────────────────
   Le métier est une séquence qui se termine par une décision humaine. La numérotation encode donc
   une information réelle, et le trait vertical relie visuellement le travail de la machine au geste
   demandé — au lieu de présenter la validation comme une case à cocher détachée. */
.aca-rail { list-style: none; margin: .2rem 0 0; padding: 0; position: relative; }
.aca-rail__step {
  position: relative; display: flex; gap: .7rem; padding: 0 0 .85rem 0; align-items: flex-start;
}
/* Le trait ne descend pas sous le dernier maillon : la chaîne s'arrête à la décision. */
.aca-rail__step:not(:last-child)::before {
  content: ""; position: absolute; left: 12px; top: 26px; bottom: 0; width: 2px;
  background: var(--aca-border);
}
.aca-rail__marker {
  flex: 0 0 26px; width: 26px; height: 26px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--aca-surface); border: 2px solid var(--aca-border);
  font-size: .72rem; font-weight: 700; color: var(--aca-muted); z-index: 1;
}
.aca-rail__marker .aca-i { font-size: .95rem; }
.aca-rail__body { display: flex; flex-direction: column; gap: .1rem; padding-top: .18rem; min-width: 0; }
.aca-rail__label { font-size: .875rem; font-weight: 570; color: var(--aca-text); }
.aca-rail__detail { font-size: .78rem; color: var(--aca-muted); overflow-wrap: anywhere; }

.aca-rail__step--done .aca-rail__marker {
  background: var(--aca-primary); border-color: var(--aca-primary); color: var(--aca-on-primary);
}
.aca-rail__step--done:not(:last-child)::before { background: var(--aca-primary); opacity: .45; }
.aca-rail__step--active .aca-rail__marker {
  background: var(--aca-bg); border-color: var(--aca-primary); color: var(--aca-primary);
  box-shadow: 0 0 0 4px rgba(var(--aca-primary-rgb), .16);
}
.aca-rail__step--active .aca-rail__label { color: var(--aca-primary); font-weight: 650; }
.aca-rail__step--alert .aca-rail__marker {
  background: var(--aca-bg); border-color: var(--aca-danger); color: var(--aca-danger);
}
.aca-rail__step--alert .aca-rail__label { color: var(--aca-danger); }
.aca-rail__step--todo .aca-rail__label { color: var(--aca-muted); font-weight: 500; }

/* Indicateurs */
.aca-stat-row { display: flex; flex-wrap: wrap; gap: .6rem; margin: .1rem 0 .3rem; }
.aca-stat {
  flex: 1 1 150px; display: flex; flex-direction: column; gap: .1rem;
  padding: .7rem .85rem; border-radius: var(--aca-radius-lg);
  border: 1px solid var(--aca-border); background: var(--aca-surface);
  border-left: 3px solid var(--aca-primary);
  transition: transform .18s ease, box-shadow .18s ease;
}
.aca-stat:hover { transform: translateY(-2px); box-shadow: var(--aca-shadow-lift); }
.aca-stat__label { font-size: .72rem; font-weight: 600; letter-spacing: .02em; color: var(--aca-muted); text-transform: uppercase; }
.aca-stat__value { font-size: 1.5rem; font-weight: 660; letter-spacing: -.028em; color: var(--aca-text); line-height: 1.15; }
.aca-stat__hint { font-size: .72rem; color: var(--aca-muted); }
.aca-stat--ok { border-left-color: var(--aca-success); }
.aca-stat--warn { border-left-color: var(--aca-warning); }
.aca-stat--danger { border-left-color: var(--aca-danger); }
.aca-stat--danger .aca-stat__value { color: var(--aca-danger); }

/* Pastilles */
.aca-chip-row { display: flex; flex-wrap: wrap; gap: .32rem; margin: .15rem 0; }
.aca-chip2 {
  display: inline-flex; align-items: center; gap: .28rem; font-size: .74rem; font-weight: 600;
  padding: .18rem .55rem; border-radius: 999px; border: 1px solid var(--aca-border);
  background: var(--aca-surface); color: var(--aca-muted);
}
.aca-chip2 .aca-i { font-size: .9rem; }
.aca-chip2--ok { color: var(--aca-success); border-color: color-mix(in srgb, var(--aca-success) 38%, transparent); }
.aca-chip2--warn { color: var(--aca-warning); border-color: color-mix(in srgb, var(--aca-warning) 38%, transparent); }
.aca-chip2--danger { color: var(--aca-danger); border-color: color-mix(in srgb, var(--aca-danger) 38%, transparent); }
.aca-chip2--info { color: var(--aca-primary); border-color: rgba(var(--aca-primary-rgb), .38); }

/* États vides : orienter, pas constater. */
.aca-empty {
  display: flex; flex-direction: column; align-items: center; gap: .3rem; text-align: center;
  padding: 2.1rem 1.2rem; border-radius: var(--aca-radius-lg);
  border: 1px dashed var(--aca-border); background: var(--aca-surface);
}
.aca-empty__icon { color: var(--aca-primary); opacity: .8; }
.aca-empty__icon .aca-i { font-size: 2rem; }
.aca-empty__title { font-size: .95rem; font-weight: 620; color: var(--aca-text); }
.aca-empty__body { font-size: .82rem; color: var(--aca-muted); max-width: 58ch; }
.aca-empty__hint {
  font-size: .78rem; font-weight: 600; color: var(--aca-primary); margin-top: .25rem;
  padding: .2rem .6rem; border-radius: 999px; background: var(--aca-primary-soft);
}

/* Chronologie d'un lead */
.aca-tl { list-style: none; margin: .2rem 0 0; padding: 0; }
.aca-tl__item {
  position: relative; display: grid; grid-template-columns: auto 1fr; column-gap: .65rem;
  padding: 0 0 .8rem 0;
}
.aca-tl__item:not(:last-child)::before {
  content: ""; position: absolute; left: 4px; top: 14px; bottom: 0; width: 2px; background: var(--aca-border);
}
.aca-tl__dot {
  grid-row: 1 / span 4; width: 10px; height: 10px; border-radius: 50%; margin-top: .32rem;
  background: var(--aca-primary); z-index: 1; box-shadow: 0 0 0 3px var(--aca-bg);
}
.aca-tl__item--ok .aca-tl__dot { background: var(--aca-success); }
.aca-tl__item--warn .aca-tl__dot { background: var(--aca-warning); }
.aca-tl__item--danger .aca-tl__dot { background: var(--aca-danger); }
.aca-tl__when { font-size: .72rem; color: var(--aca-muted); font-variant-numeric: tabular-nums; }
.aca-tl__what { font-size: .86rem; font-weight: 570; color: var(--aca-text); }
.aca-tl__who { font-size: .76rem; color: var(--aca-primary); font-weight: 600; }
.aca-tl__detail { font-size: .76rem; color: var(--aca-muted); overflow-wrap: anywhere; }

/* Différentiel */
.aca-diff {
  border: 1px solid var(--aca-border); border-radius: var(--aca-radius);
  overflow: auto; max-height: 340px; background: var(--aca-surface);
  font-family: ui-monospace, 'Cascadia Code', 'Fira Code', monospace; font-size: .76rem;
}
.aca-diff__line { padding: .1rem .6rem; white-space: pre-wrap; overflow-wrap: anywhere; }
.aca-diff__line--add { background: color-mix(in srgb, var(--aca-success) 14%, transparent); color: var(--aca-success); }
.aca-diff__line--del { background: color-mix(in srgb, var(--aca-danger) 12%, transparent); color: var(--aca-danger); }
.aca-diff__line--hunk { color: var(--aca-primary); font-weight: 700; }
.aca-diff__line--meta { color: var(--aca-muted); }
.aca-diff__none { font-size: .82rem; color: var(--aca-muted); margin: .2rem 0; }

/* ── Bloc de signature : LE moment du produit (§19) ────────────────────────────────────────────
   Tout le reste de l'application est délibérément froid et sobre ; l'audace tient ici, et nulle
   part ailleurs. C'est le seul dégradé de toute la feuille de style, et le seul emploi de la
   couleur d'accent en aplat.

   Pourquoi cette forme : la promesse du produit est qu'une PERSONNE NOMMÉE engage sa
   responsabilité avant qu'un message parte ou qu'une ligne atteigne le CRM. Deux boutons flottant
   sous une zone de texte ne disaient rien de cela. Le cartouche reprend la pratique commerciale
   française du « Bon pour accord » : qui signe, quand, et ce que la signature déclenche. La forme
   encode la responsabilité — elle ne la décore pas.

   Le filet supérieur en accent fonctionne comme un onglet de dossier : on repère le bloc de
   décision d'un coup d'œil en faisant défiler, même sans lire. */
.aca-signoff {
  position: relative; overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--aca-accent) 38%, var(--aca-border));
  border-top: 3px solid var(--aca-accent);
  border-radius: var(--aca-radius-lg);
  padding: calc(var(--aca-pad) * .95) var(--aca-pad);
  margin: .2rem 0 .1rem;
  background: linear-gradient(168deg,
              color-mix(in srgb, var(--aca-accent) 9%, var(--aca-surface)),
              var(--aca-surface) 62%);
}
.aca-signoff__eyebrow {
  display: flex; align-items: center; gap: .4rem;
  font-family: var(--aca-mono); font-size: .68rem; font-weight: 600;
  letter-spacing: .12em; text-transform: uppercase; color: var(--aca-accent);
}
.aca-signoff__title {
  font-family: var(--aca-display); font-size: 1.12rem; font-weight: 600;
  letter-spacing: -.012em; color: var(--aca-text); margin: .3rem 0 .1rem;
}
.aca-signoff__who { font-size: .82rem; color: var(--aca-muted); }
.aca-signoff__who strong { color: var(--aca-text); font-weight: 620; }
/* Ce que la signature déclenche, énoncé AVANT le geste. Une validation dont on découvre les effets
   après coup n'est pas un consentement éclairé. */
.aca-signoff__effects {
  list-style: none; margin: .6rem 0 0; padding: .55rem .7rem;
  border-radius: var(--aca-radius); background: var(--aca-bg);
  border: 1px solid var(--aca-border);
}
.aca-signoff__effects li {
  display: flex; align-items: flex-start; gap: .4rem;
  font-size: .78rem; color: var(--aca-muted); padding: .12rem 0;
}
.aca-signoff__effects li .aca-i { font-size: .95rem; color: var(--aca-primary); }
.aca-signoff__effects li strong { color: var(--aca-text); font-weight: 600; }

/* Relevé d'état en monospace (fenêtre de réception, dernière relève…). Les valeurs machine
   partagent la même face partout dans l'application — c'est ce qui les rend reconnaissables
   comme telles sans avoir à les étiqueter. */
.aca-readout {
  display: flex; flex-wrap: wrap; gap: .35rem .9rem; align-items: center;
  font-family: var(--aca-mono); font-variant-numeric: tabular-nums;
  font-size: .74rem; color: var(--aca-muted);
}
.aca-readout__k { text-transform: uppercase; letter-spacing: .08em; font-size: .66rem; opacity: .8; }
.aca-readout__v { color: var(--aca-text); font-weight: 500; }
.aca-readout__v--on { color: var(--aca-success); }
.aca-readout__v--off { color: var(--aca-muted); }
.aca-readout__v--due { color: var(--aca-accent); font-weight: 600; }

/* Raccourcis clavier */
.aca-keys { display: flex; flex-wrap: wrap; gap: .75rem; margin: .35rem 0 .1rem; }
.aca-keys__pair { display: inline-flex; align-items: center; gap: .3rem; font-size: .72rem; color: var(--aca-muted); }
.aca-keys kbd {
  font-family: ui-monospace, monospace; font-size: .68rem; font-weight: 700;
  padding: .1rem .34rem; border-radius: 4px; background: var(--aca-bg);
  border: 1px solid var(--aca-border); border-bottom-width: 2px; color: var(--aca-text);
}
"""

# Éléments de marque réutilisés par ui.py.
_COMPONENTS = """
.aca-pulse { position: relative; display: inline-flex; width: 9px; height: 9px; border-radius: 50%; background: var(--aca-primary); }
.aca-pulse::after { content: ""; position: absolute; inset: -5px; border-radius: 50%; background: rgba(var(--aca-primary-rgb), .35); }
.aca-footer { color: var(--aca-muted); font-size: .78rem; text-align: center; padding: 1.4rem 0 .4rem; border-top: 1px solid var(--aca-border); margin-top: 1.6rem; }
/* Signature de l'agence (§28). Elle emprunte `--aca-muted` et JAMAIS `--aca-accent` : l'ambre veut
   dire « une personne doit trancher ici », et une signature permanente dans cette couleur viderait
   le signal partout ailleurs. Volontairement plus discrete que le pied de page qui la contient. */
.aca-agency { display: inline-flex; align-items: center; gap: .3rem; opacity: .78; }
.aca-agency__glyph { width: 14px; height: 14px; object-fit: contain; vertical-align: -.12em; }
.aca-agency__name { font-weight: 600; letter-spacing: .01em; }
.aca-agency__link { color: inherit; text-decoration: none; display: inline-flex; align-items: center; gap: .3rem; border-bottom: 1px solid transparent; }
@media (hover: hover) and (pointer: fine) { .aca-agency__link:hover { color: var(--aca-text); border-bottom-color: currentColor; } }
.aca-agency__link:focus-visible { outline: 2px solid var(--aca-primary); outline-offset: 3px; border-radius: 2px; }
.aca-swatch-row { display: flex; gap: .4rem; flex-wrap: wrap; margin: .3rem 0 .1rem; }
.aca-swatch { width: 34px; height: 34px; border-radius: 8px; border: 1px solid var(--aca-border); box-shadow: var(--aca-shadow); }
.aca-chip { display: inline-flex; align-items: center; gap: .3rem; font-size: .74rem; font-weight: 600; padding: .16rem .5rem; border-radius: 999px; border: 1px solid var(--aca-border); background: var(--aca-surface); color: var(--aca-muted); }
"""

# Le système d'exploitation a le dernier mot. Une personne qui a activé « réduire les animations »
# le fait souvent pour une raison médicale (troubles vestibulaires, migraines) : un réglage
# applicatif ne doit jamais pouvoir le contredire — d'où les seuls `!important` du fichier.
_REDUCED_MOTION = """
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important; animation-iteration-count: 1 !important;
    transition-duration: .001ms !important; scroll-behavior: auto !important;
  }
}
"""


def _scatter_cells(size: int, density: float) -> list:
    """
    Cellules allumées d'une tuile `size`×`size`, choisies par un hachage des coordonnées.

    DÉTERMINISTE mais non ordonné, et le choix mérite d'être expliqué parce qu'il diffère de la
    page de présentation. Là-bas le tramage est une matrice de Bayer, seuillée contre une densité
    qui VARIE le long de la forme : c'est ce dégradé qui casse la régularité de la matrice. Ici la
    densité est uniforme, et une matrice de Bayer seuillée à une seule valeur produit des lignes —
    à 20/64 ses rangées font 4, 2, 4, 0 cellules, ce qui se lit comme un tissage avec des coutures
    de tuile bien visibles (mesuré : essayé, rendu, rejeté).

    Un hachage des coordonnées donne la même reproductibilité — aucun `random`, aucun état, la
    même tuile à chaque appel et sur chaque machine — sans axe privilégié. Et comme cette texture
    est STATIQUE, l'argument qui impose l'ordonné sur la page de présentation (un tirage aléatoire
    refait à chaque image scintille) ne s'applique pas ici.
    """
    def noise(x: int, y: int) -> int:
        h = ((x + 1) * 73856093) ^ ((y + 1) * 19349663)
        return ((h ^ (h >> 13)) * 1274126177 >> 7) % 4096

    threshold = int(density * 4096)
    chosen = {(x, y) for y in range(size) for x in range(size) if noise(x, y) < threshold}

    # Aucune rangée ni colonne entièrement vide. Un tirage à 30 % laisse une rangée de seize
    # cellules vide environ une fois sur trois cents, ce qui est arrivé au premier essai — et une
    # rangée vide dans une tuile répétée tous les 96 px, ce n'est plus du bruit, c'est une couture
    # horizontale que l'œil finit par suivre sur une grande surface. On complète en prenant les
    # cellules de plus faible bruit de la ligne ou de la colonne concernée : toujours déterministe,
    # et le motif reste le même sur toutes les machines.
    for index in range(size):
        row = [(x, index) for x in range(size)]
        col = [(index, y) for y in range(size)]
        for band in (row, col):
            missing = 2 - sum(1 for cellule in band if cellule in chosen)
            if missing > 0:
                spare = sorted(
                    (cellule for cellule in band if cellule not in chosen),
                    key=lambda c: noise(*c),
                )
                chosen.update(spare[:missing])
    return sorted(chosen, key=lambda c: (c[1], c[0]))


@lru_cache(maxsize=32)
def _dither_tile(color: str, alpha: float, cell: int = 6, size: int = 16, density: float = 0.3) -> str:
    """
    Tuile SVG de blocs tramés, en `data:` URI — la texture du fond d'ambiance (§26).

    Mise en cache parce que `css()` est réinjecté à CHAQUE rerun de Streamlit : la chaîne ne
    dépend que de la couleur et de l'opacité, donc elle est construite une fois par processus.

    `quote(..., safe="")` encode tout, et le `#` du code hexadécimal en particulier : laissé tel
    quel dans une `url()`, il serait lu comme un fragment et la tuile entière disparaîtrait sans
    la moindre erreur — exactement le genre de panne muette que cette base de code a déjà payée
    plusieurs fois.
    """
    path = "".join(
        f"M{x * cell} {y * cell}h{cell}v{cell}H{x * cell}z"
        for x, y in _scatter_cells(size, density)
    )
    side = size * cell
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{side}' height='{side}'>"
        f"<path fill='{color}' fill-opacity='{alpha:.3f}' d='{path}'/></svg>"
    )
    return "data:image/svg+xml," + quote(svg, safe="")


_AMBIENT_STYLES = ("particules", "voile", "grille", "cadre", "aucun")
# Multiplicateurs appliqués À LA FOIS aux voiles et à la trame : un fond se règle d'un seul
# curseur, sinon on obtient des combinaisons où le dégradé crie pendant que le grain chuchote.
_AMBIENT_INTENSITY = {"discret": 0.55, "normal": 1.0, "marqué": 1.8}


def _ambient(tokens: dict) -> str:
    """
    §26 — le fond d'ambiance reprend le motif de la page de présentation : des blocs tramés.

    POURQUOI CETTE COUCHE EXISTE. `static/landing.html` et l'application vendaient le même produit
    dans deux langages visuels sans rapport : d'un côté des champs de blocs typographiques
    (░ ▒ ▓, tramage ordonné, halo par flou), de l'autre deux voiles radiaux lisses. Quelqu'un qui
    passe de la page de vente à l'outil doit reconnaître le même produit ; c'est le seul argument
    ici, et il suffit.

    CE QUE C'EST TECHNIQUEMENT, et pourquoi ce n'est pas le moteur de la page de présentation. Là-bas
    les blocs sont du VRAI TEXTE régénéré à chaque image par un canvas. Ici il n'y a pas de
    JavaScript — `branding.py` n'émet que du CSS, et c'est une contrainte qu'on ne lève pas pour une
    décoration. La texture est donc une tuile SVG de blocs tramés (matrice de Bayer 8×8, 20/64
    cellules, aucun aléatoire — le même tramage ordonné que la page), répétée et **découpée par deux
    dégradés radiaux** placés exactement là où sont les voiles. Le résultat lu de loin est le même :
    des amas de pixels dans la couleur de marque qui s'éclaircissent vers les bords.

    UNE DIFFÉRENCE, dite plutôt que masquée : le masque fait FONDRE les blocs vers les bords, alors
    qu'un vrai tramage les RARÉFIE. À 9 % d'opacité sur une tuile couverte à 31 %, l'encre moyenne
    est de 3 % et l'écart n'est pas perceptible — mais c'est une approximation, pas la même chose.

    Les trois contraintes du §21 sont reprises telles quelles, et aucune n'est négociable :
      1. **`--aca-primary`, jamais `--aca-accent`** : l'ambre ne veut dire qu'une chose dans toute
         l'application, « une personne doit trancher ». L'utiliser en décor viderait le signal.
      2. **Sous le seuil de l'attention** : opacité basse, cellules de 4 px, aucun contour.
      3. **Le plan de travail seul** : la barre latérale et l'en-tête ont des fonds opaques et
         passent par-dessus.

    Et comme pour le dégradé : la texture est POSÉE ICI (statique), seul le MOUVEMENT est
    conditionné au niveau d'animation. « Animations : aucune » garde donc la matière et perd la
    dérive, et `prefers-reduced-motion` fige la boucle sans effacer le fond.

    Jamais bloquant : une couleur invalide retombe sur un gris neutre plutôt que de faire disparaître
    la couche — une décoration n'a pas à décider si un fond existe (leçon du §20, où une coquille
    dans un hexadécimal a silencieusement supprimé le rapport mensuel).
    """
    style = tokens.get("BRAND_AMBIENT") or "particules"
    if style not in _AMBIENT_STYLES:
        style = "particules"
    if style == "aucun":
        return ""

    force = _AMBIENT_INTENSITY.get(tokens.get("BRAND_AMBIENT_INTENSITY"), 1.0)
    dark = tokens.get("BRAND_MODE") == "sombre"

    # Vide = suit la primaire, pour qu'un client qui change sa marque n'obtienne pas un fond resté
    # dans l'ancienne couleur. Une valeur invalide retombe sur un gris neutre plutôt que de faire
    # disparaître la couche : une décoration n'a pas à décider si un fond existe.
    colour = (tokens.get("BRAND_AMBIENT_COLOR") or "").strip() or tokens.get("BRAND_PRIMARY", "")
    if not is_valid_hex(colour):
        colour = "#888888"

    # Les deux voiles. Portés ici et non plus dans `_SURFACES` parce qu'ils dépendent désormais de
    # trois réglages ; une constante ne peut pas en tenir compte.
    veil1 = round((9 if dark else 14) * force, 1)
    veil2 = round((6 if dark else 10) * force, 1)
    # `MASK` est repris à l'identique par la trame et par la grille : les motifs n'existent que là
    # où il y a déjà de la couleur. Sans lui, ils couvriraient l'écran entier d'un quadrillage
    # régulier — ce qui ne se lit plus comme une matière mais comme du papier millimétré.
    mask = ("radial-gradient(70vmax 70vmax at 22% 18%, #000, transparent 62%),\n"
            "    radial-gradient(58vmax 58vmax at 84% 88%, #000 20%, transparent 60%)")

    blocks = [f"""
/* ── Fond d'ambiance (§21, réglable depuis le §26.3) ───────────────────────────────────────────
   Deux voiles radiaux très faibles sur le plan de travail. Ce qu'ils disent, et la seule raison
   de les accepter : **la machine tourne même quand personne ne regarde.** Le relevé d'e-mails
   tourne en tâche de fond, le planificateur aussi ; un écran parfaitement inerte quand la file est
   vide dit le contraire de ce que fait le produit.

   Contrainte non négociable, héritée du §21 : la couleur par défaut est la PRIMAIRE, jamais
   l'accent. L'ambre ne veut dire qu'une chose dans toute l'application, « une personne doit
   trancher » ; l'employer en décor viderait le signal. Un client peut fixer une autre teinte via
   `BRAND_AMBIENT_COLOR` — c'est sa marque —, et rien n'empêche alors d'y mettre l'accent : le
   réglage est explicite, le défaut est sûr, et c'est la bonne répartition.

   Rayons en `vmax` et non en `rem` : `42rem` valait 588 px fixes (racine à 14 px imposée par
   `config.toml`), donc sur un écran large les voiles devenaient deux petits îlots dans une page
   immense — relevé sur 1892 px, six points de fond sur sept étaient intacts. Une décoration de
   fond se mesure à la FENÊTRE, jamais à la taille du texte.

   Posé dans le bloc statique et non dans celui des animations : le dégradé apporte de la
   profondeur même immobile, donc « animations : aucune » garde un fond agréable au lieu d'un
   aplat — seul le MOUVEMENT est conditionnel. */
[data-testid="stAppViewContainer"]::before {{
  content: "";
  position: fixed;
  /* Débordement volontaire mais MODESTE : la dérive ne déplace le voile que de ~2,5 %, donc 10 %
     suffisent pour qu'aucun bord de dégradé n'entre dans le cadre. Un premier essai à 25 % rendait
     la couche 1,5 fois plus grande que l'écran, et les voiles — positionnés en pourcentage de
     CETTE couche — se retrouvaient rejetés hors du champ visible. */
  inset: -10%;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(78vmax 78vmax at 22% 18%,
      color-mix(in srgb, {colour} {veil1}%, transparent), transparent 58%),
    radial-gradient(66vmax 66vmax at 84% 88%,
      color-mix(in srgb, {colour} {veil2}%, transparent), transparent 56%);
}}
"""]

    if style == "particules":
        # 0,075 et non 0,05 : la trame était rapportée comme à peine visible. Deux couches, donc
        # cette valeur CHACUNE — là où elles se superposent on retrouve le double, ailleurs les
        # demi-teintes qui font la granularité.
        alpha = (0.048 if dark else 0.075) * force
        tile = _dither_tile(colour, round(alpha, 4))
        blocks.append(f"""
[data-testid="stAppViewContainer"]::after {{
  content: "";
  position: fixed;
  inset: -10%;
  z-index: 0;
  pointer-events: none;
  /* LA MÊME tuile posée deux fois, à deux échelles et deux décalages premiers entre eux. Une seule
     couche donnait un tissage parfaitement régulier — de la trame de papier, pas des blocs : la
     texture de la page de présentation n'est irrégulière que parce que la densité de sa source
     varie, ce qu'une tuile uniforme ne peut pas faire. Deux échelles se recouvrent selon un motif
     dont la période est leur PPCM, très au-delà de l'écran : l'œil n'y trouve pas de grille.
     Coût nul — c'est la même `data:` URI, le navigateur ne la décode qu'une fois. */
  background-image: url("{tile}"), url("{tile}");
  background-size: 96px 96px, 138px 138px;
  background-position: 0 0, 29px 47px;
  background-repeat: repeat, repeat;
  -webkit-mask-image: {mask};
  mask-image: {mask};
}}
""")

    elif style == "grille":
        # Deux dégradés répétés plutôt qu'une image : un quadrillage est une figure régulière, donc
        # exactement ce que le CSS sait faire sans rien télécharger. Le pas de 34 px n'est pas
        # arbitraire — plus fin, la grille moirait au défilement ; plus large, elle cessait de se
        # lire comme un fond et devenait un tableau.
        line = round((13 if dark else 20) * force, 1)
        blocks.append(f"""
[data-testid="stAppViewContainer"]::after {{
  content: "";
  position: fixed;
  inset: -10%;
  z-index: 0;
  pointer-events: none;
  background-image:
    repeating-linear-gradient(0deg,
      color-mix(in srgb, {colour} {line}%, transparent) 0 1px, transparent 1px 34px),
    repeating-linear-gradient(90deg,
      color-mix(in srgb, {colour} {line}%, transparent) 0 1px, transparent 1px 34px);
  -webkit-mask-image: {mask};
  mask-image: {mask};
}}
""")

    elif style == "cadre":
        # Le filet est tracé par `box-shadow: inset` sur le conteneur, et non par un pseudo-élément
        # bordé. Un pseudo-élément aurait dû se placer dans le contexte d'empilement de `stMain`,
        # où il serait passé soit derrière le fond, soit par-dessus le contenu selon l'ordre des
        # couches — une ombre interne n'a aucune de ces deux questions à trancher et ne peut pas
        # recouvrir un widget.
        edge = round(30 * force, 1)
        halo = round(16 * force, 1)
        blocks.append(f"""
[data-testid="stAppViewContainer"] {{
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, {colour} {edge}%, transparent),
    inset 0 0 90px -40px color-mix(in srgb, {colour} {halo}%, transparent);
}}
""")

    return "\n".join(blocks)


def css(tokens: dict = None) -> str:
    """
    Feuille de style complète à injecter (`st.html`), pour les jetons donnés.

    ⚠️ Les sélecteurs `data-testid`/`data-baseweb` sont ceux de Streamlit 1.59 : des détails
    d'implémentation, pas un contrat d'API. Une montée de version peut les invalider, et l'effet
    serait alors une page **moins jolie**, jamais cassée — chaque règle est décorative, aucune ne
    conditionne une fonctionnalité. C'est le prix assumé d'un thème réglable à chaud, et la raison
    pour laquelle la couche `config.toml` (elle, stable) existe en parallèle.
    """
    tokens = tokens or resolve()
    level = tokens.get("BRAND_ANIMATIONS", "complet")
    hero = tokens.get("BRAND_HERO", "dégradé animé")

    parts = [
        font_import(tokens),
        _variables(tokens),
        _SURFACES,
        # Le fond d'ambiance complet — voiles, et selon le style trame, quadrillage ou filet. Calculé
        # et non statique pour deux raisons : il dépend de trois réglages, et la couleur doit être
        # CUITE dans la tuile SVG, une `data:` URI ne pouvant pas lire une variable CSS.
        _ambient(tokens),
        _HERO,
        _COMPONENTS,
        _UI_KIT,
    ]
    if hero == "dégradé fixe":
        parts.append(_HERO_FLAT)
    elif hero == "sobre":
        parts.append(_HERO_PLAIN)

    if level != "aucune":
        parts.append(_KEYFRAMES)
        parts.append(_ANIMATIONS_FULL if level == "complet" else _ANIMATIONS_SUBTLE)
        if hero == "dégradé animé" and level == "complet":
            parts.append(_HERO_ANIMATED)
    parts.append(_REDUCED_MOTION)
    return "<style>\n" + "\n".join(part for part in parts if part) + "\n</style>"


def hero_html(tokens: dict, pills=()) -> str:
    """
    En-tête de marque : titre, accroche et pastilles d'état (utilisateur, rôle, mode démonstration).

    `pills` est une séquence de `(texte, "normal"|"alert")`. Le texte est échappé — une accroche ou
    un nom d'entreprise saisis dans un formulaire d'administration finissent ici dans du HTML.
    """
    from html import escape

    if tokens.get("BRAND_HERO") == "masqué":
        return ""
    rendered = "".join(
        f'<span class="aca-hero__pill{" aca-hero__pill--alert" if kind == "alert" else ""}">'
        f"{escape(str(text))}</span>"
        for text, kind in pills
    )
    tagline = tokens.get("BRAND_TAGLINE") or ""
    name = tokens.get("BRAND_NAME", "")
    # §29 — quand le nom de marque résolu est littéralement « acami » (le défaut, avant toute
    # personnalisation client — c'est le cas de cette installation elle-même), le titre porte le
    # mot-symbole RÉEL plutôt que le nom recomposé dans la police système : même distinction que
    # `agency_mark_html()` plus haut. Un CLIENT dont `BRAND_NAME` a été personnalisé continue de
    # voir SON nom en texte — jamais une image qu'il n'a pas fournie. `<img src="data:…">`, pas un
    # `<svg>` en ligne : voir `_brand_png_data_uri` pour la raison (DOMPurify).
    if name.strip().lower() == "acami":
        uri = _brand_mark_uri(tokens, "lockup", "BRAND_SURFACE")
        title_inner = (f'<img src="{uri}" alt="acami" style="height:37px; width:auto; '
                       f'display:block; margin-bottom:.2rem;">' if uri else escape(name))
    else:
        title_inner = escape(name)
    return (
        '<div class="aca-hero">'
        '<div class="aca-hero__orb aca-hero__orb--a"></div>'
        '<div class="aca-hero__orb aca-hero__orb--b"></div>'
        f'<h1 class="aca-hero__title">{title_inner}</h1>'
        + (f'<p class="aca-hero__tagline">{escape(tagline)}</p>' if tagline else "")
        + (f'<div class="aca-hero__pills">{rendered}</div>' if rendered else "")
        + "</div>"
    )


def chart_colors(tokens: dict) -> list:
    """
    Palette catégorielle dérivée des couleurs de marque, pour `st.bar_chart`/`st.line_chart`.

    Les couleurs d'état (succès/avertissement/danger/info) sont réutilisées telles quelles : elles
    ont déjà été choisies pour se distinguer entre elles, et les répéter dans les graphiques rend le
    tableau de bord cohérent avec les bandeaux d'alerte.

    §21 — **dédoublonnage**, et ce n'était pas cosmétique. Les six jetons sémantiques étaient
    concaténés tels quels, alors que rien n'impose qu'ils soient distincts : dans la palette par
    défaut `BRAND_INFO` vaut `BRAND_PRIMARY` (le pétrole) et `BRAND_WARNING` vaut `BRAND_ACCENT`
    (l'ambre), parce que c'est juste du point de vue du SENS. Aplatis en palette catégorielle, cela
    donnait `[…, #B4622A, #B4622A, #125E6B, …]` : deux catégories voisines du graphique « Volume par
    catégorie » se dessinaient dans la même couleur, et une légende à cinq entrées n'en distinguait
    que trois. Une palette catégorielle a une exigence propre — chaque série doit être séparable —
    qui ne découle pas de la cohérence sémantique. On garde donc l'ordre sémantique, on retire les
    répétitions, et on complète par des dérivés jusqu'à obtenir huit teintes réellement distinctes.
    """
    primary, accent = tokens["BRAND_PRIMARY"], tokens["BRAND_ACCENT"]
    preferred = [
        primary, tokens["BRAND_SUCCESS"], tokens["BRAND_WARNING"], accent,
        tokens["BRAND_INFO"], tokens["BRAND_DANGER"],
    ]
    # Repli : dérivés des deux couleurs de marque, suffisamment écartés pour rester lisibles côte à
    # côte même quand un client a réglé plusieurs jetons d'état sur la même valeur.
    fallback = [
        mix(primary, accent, 0.5),
        mix(primary, tokens["BRAND_BACKGROUND"], 0.45),
        mix(accent, tokens["BRAND_BACKGROUND"], 0.45),
        mix(primary, tokens["BRAND_TEXT"], 0.35),
        mix(accent, tokens["BRAND_TEXT"], 0.35),
    ]
    palette = []
    for color in preferred + fallback:
        if color.upper() not in {seen.upper() for seen in palette}:
            palette.append(color)
        if len(palette) == 8:
            break
    return palette


# ── Thème natif (config.toml) ─────────────────────────────────────────────────────────────────
_GENERATED_HEADER = (
    "# ─────────────────────────────────────────────────────────────────────────────\n"
    "# Section [theme] GÉNÉRÉE par aca/core/branding.py (onglet « Réglages » → Apparence).\n"
    "# Toute modification manuelle ici sera écrasée au prochain enregistrement de la marque.\n"
    "# Les autres sections du fichier (server, client, browser…) sont préservées telles quelles.\n"
    "# ─────────────────────────────────────────────────────────────────────────────\n"
)


def config_toml(tokens: dict) -> str:
    """
    Section `[theme]` correspondant aux jetons — la couche qui atteint l'INTÉRIEUR des composants
    Streamlit (menu déroulant ouvert, en-tête de `st.dataframe`, palette Vega), hors de portée de la
    CSS injectée. Prend effet au rechargement de la page.
    """
    font = tokens.get("BRAND_FONT", "Inter")
    font_line = (f'font = "{font}:{_GOOGLE_FONTS[font]}"' if font in _GOOGLE_FONTS
                 else 'font = "sans-serif"')
    radius = tokens["BRAND_RADIUS"]
    palette = ", ".join(f'"{color}"' for color in chart_colors(tokens))
    return f"""{_GENERATED_HEADER}[theme]
base = "{"dark" if tokens.get("BRAND_MODE") == "sombre" else "light"}"
primaryColor = "{tokens["BRAND_PRIMARY"]}"
backgroundColor = "{tokens["BRAND_BACKGROUND"]}"
secondaryBackgroundColor = "{tokens["BRAND_SURFACE"]}"
textColor = "{tokens["BRAND_TEXT"]}"
linkColor = "{tokens["BRAND_PRIMARY"]}"
borderColor = "{tokens["BRAND_BORDER"]}"
showWidgetBorder = true
showSidebarBorder = true
baseRadius = "{radius}"
buttonRadius = "{radius}"
{font_line}
baseFontSize = 14
linkUnderline = false
headingFontSizes = ["28px", "22px", "18px", "16px", "14px", "12px"]
headingFontWeights = [650, 640, 620, 600, 600, 600]
chartCategoricalColors = [{palette}]
blueColor = "{tokens["BRAND_PRIMARY"]}"
greenColor = "{tokens["BRAND_SUCCESS"]}"
redColor = "{tokens["BRAND_DANGER"]}"
orangeColor = "{tokens["BRAND_WARNING"]}"
violetColor = "{tokens["BRAND_ACCENT"]}"
dataframeBorderColor = "{tokens["BRAND_BORDER"]}"
dataframeHeaderBackgroundColor = "{tokens["BRAND_SURFACE"]}"

[theme.sidebar]
backgroundColor = "{tokens["BRAND_SIDEBAR"]}"
secondaryBackgroundColor = "{mix(tokens["BRAND_SIDEBAR"], tokens["BRAND_TEXT"], 0.06)}"
textColor = "{tokens["BRAND_TEXT"]}"
borderColor = "{tokens["BRAND_BORDER"]}"
primaryColor = "{tokens["BRAND_PRIMARY"]}"
"""


def merge_config_toml(existing: str, theme_section: str) -> str:
    """
    Remplace les sections `[theme…]` d'un `config.toml` existant en conservant TOUT le reste.

    Réécrire le fichier entier depuis les jetons serait plus simple et détruirait silencieusement un
    `[server]`, un `[client]` ou un `[browser]` ajoutés par l'opérateur au moment du déploiement —
    c'est-à-dire précisément la configuration dont dépend la mise en ligne (cf.
    docs/DEPLOYMENT_HARDENING.md). Fonction pure : elle prend du texte, elle rend du texte, et se
    teste sans toucher au disque.
    """
    kept, pending, skipping = [], [], False
    for line in (existing or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped.startswith("[theme")
            # Les commentaires qui PRÉCÈDENT une section documentent cette section : ils partent
            # avec elle. Sans cette mise en attente, l'en-tête « GÉNÉRÉE par branding.py » (et tout
            # commentaire d'un opérateur au-dessus de `[theme]`) s'empilerait à chaque
            # enregistrement, jusqu'à un fichier composé surtout d'en-têtes périmés.
            pending_before = pending
            pending = []
            if skipping:
                # L'en-tête `[theme]`/`[theme.sidebar]` lui-même est réémis par `theme_section` :
                # le conserver ici laissait une section vide en tête de fichier, et le résultat
                # n'était donc pas idempotent (défaut trouvé par `test_merge_est_idempotent`).
                continue
            kept.extend(pending_before)
            kept.append(line)
            continue
        if skipping:
            continue
        if stripped.startswith("#") or not stripped:
            pending.append(line)
            continue
        kept.extend(pending)
        pending = []
        kept.append(line)
    kept.extend(pending)
    preserved = "\n".join(kept).strip()
    return (preserved + "\n\n" if preserved else "") + theme_section


def write_config_toml(tokens: dict, path: str = ".streamlit/config.toml") -> str:
    """Écrit le thème natif en préservant les autres sections. Renvoie le chemin écrit."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    except FileNotFoundError:
        existing = ""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(merge_config_toml(existing, config_toml(tokens)))
    return path


def accessibility_report(tokens: dict) -> list:
    """
    Contrôles de contraste WCAG sur la palette choisie — liste de problèmes en clair.

    Affiché en direct dans le formulaire d'apparence : un client peut demander « notre jaune
    d'entreprise » comme couleur principale et rendre ses boutons illisibles sans s'en apercevoir.
    Le rôle de ce rapport est d'avertir, jamais de refuser : c'est sa marque, c'est son choix, et un
    produit qui interdit la charte graphique de son client se fait remplacer.
    """
    problems = []
    text_bg = contrast_ratio(tokens["BRAND_TEXT"], tokens["BRAND_BACKGROUND"])
    if text_bg < 4.5:
        problems.append(
            f"Texte sur fond principal : contraste {text_bg:.1f}:1 (minimum recommandé 4,5:1). "
            "Le corps de texte sera pénible à lire."
        )
    button = contrast_ratio(readable_text_on(tokens["BRAND_PRIMARY"]), tokens["BRAND_PRIMARY"])
    if button < 4.5:
        problems.append(
            f"Libellé des boutons principaux : contraste {button:.1f}:1. La couleur principale est "
            "trop moyenne — ni le blanc ni le noir ne s'y détachent nettement."
        )
    surface = contrast_ratio(tokens["BRAND_SURFACE"], tokens["BRAND_BACKGROUND"])
    if surface < 1.04:
        problems.append(
            "Fond des cartes presque identique au fond principal : les blocs bordurés "
            "(fiche prospect, proposition) ne se distingueront plus."
        )
    # §21 — le contrôle le plus important de cette liste, et le seul qui ne porte pas sur la
    # lisibilité d'un texte mais sur la LISIBILITÉ D'UN SIGNAL. La couleur d'accent ne sert qu'à une
    # chose dans toute l'application : marquer ce qui attend une décision humaine (cartouche « Bon
    # pour accord », pastille d'alerte, terminus du rail). Choisie trop proche de la couleur
    # principale, elle ne disparaît pas — elle devient indistinguable du décor, ce qui est pire :
    # l'écran a toujours l'air correct, et plus rien n'indique où il faut agir. Aucun contrôle
    # n'existait pour ça, et quatre des palettes livrées étaient dans ce cas.
    separation = signal_separation(tokens["BRAND_PRIMARY"], tokens["BRAND_ACCENT"])
    if separation < 0.25:
        problems.append(
            f"Couleur d'accent trop proche de la couleur principale (séparation {separation:.2f} "
            "sur 1). L'accent ne sert qu'à signaler ce qui attend une validation humaine : s'il "
            "appartient à la même famille que la couleur principale, ce repère disparaît. Une "
            "teinte franchement différente est préférable — chaude si la couleur principale est "
            "froide, froide si elle est chaude."
        )
    return problems

# Encaisser et faire réserver — Stripe + Calendly, pas à pas

Ce document répond à trois questions : **quand l'argent bouge**, **comment brancher Stripe en
production** (sans compte de test), et **comment brancher Calendly** pour que le calendrier de
`static/landing.html` cesse d'être décoratif.

Rien ici n'est du code : ce sont des réglages à faire une fois dans deux interfaces web, puis trois
URL à coller dans l'objet `CONFIG` en haut du `<script>` de `static/landing.html`.

---

## 1. Quand la facturation a lieu — et ce qui est le mieux

Réponse courte : **ça dépend du palier, et c'est voulu.** Un montant fixe peut se payer tout de
suite ; un montant qui dépend du périmètre ne le peut pas, et prétendre le contraire crée une dette
qu'on règle ensuite en litiges.

| Palier | Montant | Payable **avant** l'appel ? | Outil Stripe |
|---|---|---|---|
| **Démonstration** | 0 € | — rien à payer | aucun |
| **Solo** | 1 490 € fixe | **Oui** | Lien de paiement |
| **Enterprise** | à partir de 8 900 € | **Non** — devis après cadrage | Facture |
| **Suivi mensuel** | 290 €/mois | Non — démarre après livraison | Abonnement |

### Le parcours recommandé

```
Visiteur ─► Démonstration (gratuite, sans compte)
        │
        ├─► « Faire installer » (Solo, prix fixe connu)
        │        └─► Lien de paiement Stripe ─► redirection ─► Calendly ─► RDV de lancement
        │
        └─► « Cadrer votre déploiement » (Enterprise, périmètre inconnu)
                 └─► Calendly (30 min, gratuit) ─► devis ─► facture Stripe (acompte)
                          └─► travaux ─► livraison ─► solde ─► (option) abonnement mensuel
```

### Pourquoi l'appel de 30 minutes reste **gratuit**

C'est un appel de vente, pas une prestation. Le facturer à ce stade — sans références publiques, ni
avis clients, ni logos à montrer — supprime la quasi-totalité des prises de contact. Le modèle
« audit payant » du modèle de page d'origine fonctionne pour un cabinet déjà installé, dont le nom
justifie à lui seul qu'on paie pour lui parler. Ce n'est pas encore le cas ici, et le reconnaître
coûte moins cher que de le découvrir.

Il redeviendra pertinent le jour où vous refuserez des rendez-vous : l'audit payant sert alors de
filtre. Pas avant.

### Pourquoi Solo **peut** se payer d'avance

1 490 € est un prix fixe pour un périmètre fixe. Le client qui clique sait ce qu'il achète et vous
savez ce que vous livrez : c'est exactement la condition d'un paiement en libre-service. La
redirection après paiement de Stripe vers Calendly enchaîne alors *payer → réserver* sans le moindre
serveur de votre côté.

Deux garde-fous à écrire dans la description du lien de paiement :

- **ce que couvre l'installation** — la liste du palier Solo, mot pour mot ;
- **votre politique de remboursement** — recommandée : intégralement remboursable tant que
  l'installation n'a pas commencé. Elle ne coûte presque rien et lève l'objection principale d'un
  achat à 1 490 € sur une page qu'on découvre.

### Pourquoi Enterprise ne le peut **pas**

« À partir de 8 900 € » n'est pas un prix, c'est un plancher. Encaisser avant d'avoir vu les outils à
raccorder, c'est s'engager sur un périmètre inconnu à un tarif fixe : soit vous y perdez, soit vous
renégociez après encaissement — la plus mauvaise conversation possible avec un client qui a déjà
payé. Devis d'abord, acompte ensuite.

---

## 2. Stripe — pas à pas, en production directe

> Vous n'avez pas de compte de test, et ce n'est pas gênant : Stripe donne les **deux** modes sur le
> même compte, via le bandeau « Mode test » du tableau de bord. Faites les réglages en test,
> vérifiez, puis refaites-les en mode réel — **les objets ne sont pas partagés entre les deux**.
> C'est l'erreur classique : un lien créé en test renvoie une page d'erreur à un vrai client.

### 2.1 Ouvrir et activer le compte

1. `https://dashboard.stripe.com/register` — e-mail, mot de passe, pays.
2. **Activer les paiements** : statut (auto-entrepreneur / société), SIRET, pièce d'identité et
   **IBAN** pour les virements. Dix minutes, plus un délai de vérification de quelques minutes à
   deux jours ouvrés.
3. Sans activation vous pouvez tout préparer, mais rien encaisser.

### 2.2 Mettre Stripe aux couleurs de la page

`Paramètres → Personnalisation de la marque`. Ce réglage — et lui seul — habille les pages de
paiement, les liens de paiement, les factures et les e-mails de reçu. Valeurs exactes en §3.

### 2.3 Créer le produit « Installation Solo »

1. `Catalogue de produits → + Ajouter un produit`.
2. Nom : `Installation Solo — Assistant Commercial Agentique`.
3. Description : reprenez **les quatre lignes du palier Solo** de la page, mot pour mot. Le client
   doit lire la même chose des deux côtés ; une description qui diffère est une réclamation en
   préparation.
4. Tarif : `1 490,00 €`, **Ponctuel** (pas récurrent).

### 2.4 Créer le lien de paiement, et le faire mener à Calendly

1. `Liens de paiement → + Nouveau`, sélectionnez le produit ci-dessus.
2. Options utiles : **collecter l'adresse de facturation** (oui — obligatoire pour une facture
   française) ; **numéro de TVA** (oui si vous facturez des entreprises) ; **codes promo** (seulement
   si vous comptez en donner) ; **quantité ajustable** (non).
3. `Après le paiement` → **« Rediriger les clients vers votre site web »** → collez votre lien
   Calendly. **C'est l'étape qui fait la chaîne** : le client paie, Stripe l'envoie sur le
   calendrier, il réserve son créneau de lancement dans la foulée. Sans elle, il paie puis attend —
   et c'est vous qui devez le relancer.
4. Copiez l'URL (`https://buy.stripe.com/...`).

### 2.5 La coller dans la page

Dans `static/landing.html`, en haut du `<script>` :

```js
var CONFIG = {
  calendly: "https://calendly.com/<vous>/30min",
  contact:  "vous@exemple.fr",
  demo:     "",                       // instance de démonstration hébergée, si vous en publiez une
  pay: { demo: "", solo: "https://buy.stripe.com/xxxxxxxx", enterprise: "" }
};
```

Le bouton **« Faire installer »** mène alors directement au paiement. `enterprise` reste **vide** :
laissé vide, le bouton mène à la réservation, ce qui est le comportement voulu.

### 2.6 Enterprise — facturer après le devis

`Facturation → Factures → + Créer une facture`. Client, lignes, échéance (30 jours), envoi par
e-mail ; le client paie par carte ou virement depuis la facture. Pour l'acompte, créez **deux**
factures (par exemple 40 % à la signature, 60 % à la livraison) plutôt qu'une facture partielle :
chacune porte alors sa propre échéance et sa propre relance automatique.

### 2.7 Le suivi mensuel

Produit `Suivi mensuel`, tarif `290,00 €` **Récurrent / mensuel**. Ne créez **pas** de lien de
paiement public : le suivi ne se vend pas seul. Créez l'abonnement depuis la fiche client une fois la
livraison faite (`Clients → le client → Créer un abonnement`).

### 2.8 Ce qu'il ne faut pas confondre

`STRIPE_API_KEY` dans `.env` sert à [`billing.py`](../aca/integrations/billing.py), qui **compte les
jetons consommés** et n'encaisse rien. Les liens ci-dessus encaissent et ne comptent rien. Les deux
peuvent coexister ; aucun n'a besoin de l'autre.

---

## 3. Faire que Stripe ne casse pas le thème

**La limite d'abord, parce qu'elle est réelle :** les pages de paiement Stripe sont hébergées par
Stripe. Vous y réglez des couleurs, un logo, une police et un arrondi — **vous ne pouvez pas y porter
les animations de la page** (fond animé, révélations au défilement, paquet de cartes). Personne ne
le peut ; ce n'est pas une limite de ce projet.

Ce qui évite la sensation de rupture, ce sont la **couleur, le logo et la typographie**. Sur un écran
de paiement, un client ne cherche pas du mouvement — il cherche des raisons d'avoir confiance.
Réglez ces trois-là et la transition passe inaperçue.

### Valeurs à reporter, telles quelles

| Réglage Stripe | Valeur | D'où elle vient |
|---|---|---|
| Couleur de marque / accent | `#125E6B` | `--ink-accent` de la page |
| Couleur des boutons | `#125E6B` | le même bouton que « Faire installer » |
| Couleur d'arrière-plan | `#FDFDFD` | `--paper` |
| Couleur du texte | `#000000` | `--ink` |
| Logo | le carré 2×2 de la page | même marque en haut des deux écrans |
| Icône | idem, version carrée | icône d'onglet |
| Arrondi des formes | **Arrondi** (~18 px si le choix est libre) | `--radius: 18px` |
| Police | la plus proche de **Figtree** proposée par Stripe | `--font-sans` |

> ⚠️ Sur la police : Stripe impose sa propre liste et **je n'ai pas pu la vérifier depuis ici**.
> Figtree n'y figure probablement pas. Prenez la sans-serif géométrique la plus proche (Inter, Work
> Sans, Nunito Sans) — l'écart est invisible pour un client qui passe d'une page à l'autre, alors
> qu'une couleur fausse, elle, se voit immédiatement.
>
> N'utilisez **jamais** `#B4622A` (l'ambre `--signal`) comme couleur de marque Stripe. Dans toute
> l'application et sur toute la page, cette couleur veut dire une seule chose : « un humain doit
> décider ici ». S'en servir pour un bouton de paiement la viderait de son sens aux deux endroits.

---

## 4. Calendly — pas à pas

### 4.1 Créer l'événement

1. `https://calendly.com/signup` — le palier gratuit suffit (un seul type d'événement).
2. `Event Types → + New Event Type → One-on-One`.
3. Nom : **« Évaluation technique — 30 min »**, l'intitulé exact de la page. Deux noms différents
   pour un même rendez-vous font douter d'être au bon endroit.
4. Durée 30 min ; lieu Google Meet ou Zoom — Calendly génère le lien et le met dans l'invitation.

### 4.2 Disponibilités

`Availability` → vos plages réelles. Trois réglages qui évitent les rendez-vous ingérables :

- **délai minimum avant réservation** : 12 h (sinon quelqu'un réserve pour dans dix minutes) ;
- **fenêtre maximale** : 30 jours ;
- **temps tampon** : 15 min après chaque rendez-vous.

C'est cette page qui rend vraies les journées grisées : une journée pleine devient **grise et non
cliquable** dans le widget, parce que Calendly connaît votre agenda. La page, elle, ne l'a jamais su
et ne pouvait pas l'inventer.

### 4.3 Questions posées à l'inscription

Peu — chaque champ ajouté fait perdre des réservations. Trois suffisent : nom (natif), e-mail
(natif), et « Combien d'e-mails commerciaux recevez-vous par semaine ? ». Cette dernière est la seule
donnée qui vous permet de chiffrer, donc la seule qui mérite un champ.

### 4.4 Confirmations et rappels

`Workflows` → rappel par e-mail **24 h avant** et **1 h avant**. Sur le palier gratuit, la
confirmation avec le lien de visioconférence part de toute façon.

### 4.5 Coller le lien dans la page

`Copy link` → `https://calendly.com/<vous>/30min` → dans `CONFIG.calendly`.

Dès qu'il est renseigné, la carte de réservation change de branche toute seule : le sélecteur dessiné
disparaît, le widget Calendly s'insère à sa place — chargé seulement quand le visiteur approche de la
section — et la note passe à « disponibilités réelles ». Vide, la page reste exactement comme
aujourd'hui : un sélecteur qui annonce lui-même qu'il ne réserve rien.

### 4.6 Mettre Calendly aux mêmes couleurs

`Event Type → Branding` : couleur `#125E6B`, texte `#000000`, fond `#FDFDFD`. La page passe déjà ces
trois valeurs au widget par l'URL (`background_color`, `text_color`, `primary_color`) ; les régler
aussi côté Calendly couvre le cas où quelqu'un ouvre le lien directement, hors de la page.

---

## 5. Vérifier que tout marche vraiment

Une fois les trois URL collées, dans l'ordre :

1. **Le calendrier** — ouvrez la page, descendez jusqu'à « Nous contacter ». Le widget doit
   apparaître **à l'approche** de la section, pas au chargement. Bloquez une journée entière dans
   votre agenda, rechargez : elle doit être grise et non cliquable. *C'est la seule vérification que
   je n'ai pas pu faire à votre place — elle demande un vrai compte Calendly.*
2. **Le lien de paiement** — en **mode test**, payez avec la carte `4242 4242 4242 4242`, n'importe
   quelle date future, n'importe quel CVC : vous devez être redirigé sur Calendly. Refaites ensuite
   le lien en **mode réel** et payez 1 € avec votre propre carte pour vérifier la chaîne complète,
   puis remboursez-vous depuis le tableau de bord — le remboursement est gratuit, seuls les frais de
   transaction restent dus (quelques centimes).
3. **Le formulaire de repli** — videz `CONFIG.calendly`, rechargez, remplissez le formulaire : votre
   client e-mail doit s'ouvrir avec **votre adresse déjà remplie**, le palier choisi et le créneau
   dans le corps du message.
4. **Les deux langues** — basculez FR/EN sur la section de réservation. Le widget ne se recharge pas,
   volontairement : cela effacerait une réservation en cours de saisie.

---

## 6. Ce que ce document ne couvre pas

- **Aucun webhook Stripe n'est branché.** `POST /stripe/webhook` n'existe pas. Vous verrez donc les
  paiements dans le tableau de bord Stripe et dans vos e-mails, mais rien n'arrive dans
  l'application — suffisant pour les premiers clients. Le jour où ça ne l'est plus, l'endpoint doit
  se calquer sur `POST /slack/interactions` ([aca/api.py](../aca/api.py)) : hors de
  `require_api_key`, signature vérifiée sur le corps brut, **échec fermé** si le secret manque.
- **La TVA.** Auto-entrepreneur sous le seuil : mention « TVA non applicable, art. 293 B du CGI »
  dans la description du produit. Au-dessus, activez Stripe Tax. Ce n'est pas un conseil fiscal.
- **Les CGV.** Un lien de paiement public sans conditions de vente vous expose. Une page suffit :
  périmètre, délais, remboursement, propriété du code.


---

# Cal.com + envoi direct — la mise en route, pas à pas (§26.4)

Deux réglages rendent la page réellement opérationnelle : **le calendrier réserve pour de vrai**, et
**le bouton « Envoyer la demande » envoie pour de vrai**. Les deux sont facultatifs : laissés vides,
la page retombe sur le calendrier hors ligne et sur `mailto:`, exactement comme avant.

Tout se règle dans l'objet `CONFIG`, tout en haut du `<script>` de `static/landing.html` :

```js
var CONFIG = {
  booking:  "https://cal.com/VOTRE-NOM/30min",   // le calendrier
  contact:  "hajriismail7@gmail.com",            // déjà rempli
  form:     "https://api.web3forms.com/submit",  // l'envoi direct
  formKey:  "votre-clé-web3forms",
  demo:     "",
  pay: { demo: "", solo: "", enterprise: "" }
};
```

## A. Le calendrier (Cal.com) — 6 étapes

1. **Créer le compte** sur <https://cal.com>. Le plan gratuit suffit pour tout ce qui suit.
2. **Connecter Google Agenda** : *Settings → Apps → Google Calendar → Install*. C'est ce qui permet
   à Cal.com de connaître vos disponibilités réelles et donc de griser une journée pleine.
3. **Créer le type d'événement** : *Event Types → New*, 30 minutes, un titre du genre
   « Évaluation technique — 30 min ». L'URL obtenue (`cal.com/votre-nom/30min`) est celle à coller
   dans `CONFIG.booking`.
4. **Régler le lieu sur Google Meet** : dans le type d'événement, *Location → Google Meet*.
   ⚠️ **Sans cette étape, la confirmation part sans lien de visioconférence.** C'est l'oubli le plus
   fréquent, et la page ne peut rien y faire à votre place.
5. **Activer « Ajouter des invités »** (l'écran de la capture) : *Event Type → Advanced →
   Booking questions → **Add guests** → activer*. Le champ « Inviter des collègues » de la page
   pré-remplit cette liste, mais c'est ce réglage-ci qui la fait exister côté Cal.com.
6. **Coller l'URL** dans `CONFIG.booking`. Le calendrier maison disparaît, le vrai s'affiche.

Ce que Cal.com fait ensuite, sans que rien ne soit à écrire : le créneau est bloqué, la journée
pleine devient grise, et **la confirmation avec le lien Meet part automatiquement aux deux parties**.

**Deux limites, dites franchement.** L'expéditeur de cette confirmation est Cal.com (à votre nom),
pas votre adresse personnelle — l'expédition depuis votre propre domaine demande leur offre payante
ou un SMTP configuré chez eux. Et la page n'envoie rien elle-même : elle cède la carte à Cal.com,
c'est tout.

## B. L'envoi direct du formulaire — 4 étapes

Sans cela, « Envoyer la demande » ouvre le client e-mail du visiteur, qui doit encore appuyer sur
Envoyer. Le vrai problème n'est pas le clic en trop : **sur un téléphone d'entreprise, dans un
navigateur sans compte mail ou depuis un webmail, `mailto:` n'ouvre rien du tout** et la demande est
perdue sans que personne ne l'apprenne.

1. Créer un compte sur <https://web3forms.com> (gratuit, sans carte). *Formspree convient aussi.*
2. Y indiquer l'adresse de réception : `hajriismail7@gmail.com`.
3. Copier la **clé d'accès** fournie.
4. Renseigner `form: "https://api.web3forms.com/submit"` et `formKey: "…"`.

La demande arrive alors directement dans la boîte, avec le nom, la société, les invités, le palier
choisi et le créneau. Le visiteur voit « Demande envoyée » sans quitter la page.

**Si le service tombe, la demande n'est pas perdue** : l'échec bascule automatiquement sur `mailto:`
plutôt que d'afficher une erreur. Quelqu'un qui a pris la peine d'écrire ne doit pas voir son
message disparaître parce qu'un tiers est indisponible.

## C. Vérifier que ça marche

| À tester | Attendu |
|---|---|
| Ouvrir la page, aller à « Réserver » | Le calendrier Cal.com s'affiche, pas la grille maison |
| Remplir nom + e-mail **avant** de réserver | Ces champs sont déjà remplis dans Cal.com |
| Écrire deux adresses dans « Inviter des collègues » | Elles apparaissent dans « Ajouter des invités » |
| Réserver un créneau | Deux e-mails partent, avec le lien Meet |
| Rouvrir la page et regarder ce créneau | Il n'est plus proposé |
| Envoyer le formulaire | « Demande envoyée » s'affiche, l'e-mail arrive |

Si le lien Meet manque à l'étape 4, c'est l'étape **A.4** qui n'a pas été faite.

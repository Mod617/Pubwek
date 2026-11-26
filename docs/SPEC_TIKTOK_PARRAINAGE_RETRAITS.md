# Spécification fonctionnelle — Diffusion TikTok, vitrines partageurs, nouveau parrainage, retraits sous 48 h

> Document destiné au développeur de Pubwek. Il décrit **ce qu'il faut construire et pourquoi**, pas comment le coder ligne par ligne.
> Rédigé à partir de l'état actuel du code (`main.py`, `models.py`, gabarits) et de la vision du fondateur.

---

## 0. Pourquoi ce chantier, et les 4 règles à respecter partout

**Le contexte.** La concurrence s'installe : WhatsPay et d'autres plateformes font aussi de la pub sur les statuts WhatsApp. Pubwek doit offrir quelque chose que les autres n'ont pas.

**L'opportunité.** En Afrique, les comptes TikTok ne sont pas monétisés : un créateur avec des milliers d'abonnés ne gagne rien. Des escrocs en profitent en vendant de faux « comptes TikTok monétisés ». Pubwek peut devenir **la façon honnête de gagner de l'argent avec un compte TikTok** : le créateur diffuse les pubs des commerces de Pubwek et il est payé à chaque vrai clic.

**Les 4 règles à respecter dans chaque écran :**

1. **Simplicité d'abord.** L'annonceur, le partageur et le client final ne doivent jamais trouver ça compliqué. Une case à cocher plutôt qu'un formulaire, un formulaire court plutôt qu'un long. Ce qui ne concerne pas l'utilisateur reste caché.
2. **Tout est réglable par l'admin.** Aucun montant, pourcentage, seuil ou délai n'est écrit en dur dans le code. Tout passe par `SystemConfig`, comme les tarifs actuels, et se modifie depuis l'espace admin.
3. **La fraude est traitée dès la conception.** Chaque fonction qui rapporte de l'argent a ses garde-fous, listés dans chaque section et regroupés à la section 7.
4. **Rien n'est imposé aux annonceurs existants.** La diffusion TikTok est une option qu'ils activent s'ils le veulent.

---

## 1. Vue d'ensemble : le parcours TikTok en une phrase

```
Annonceur → coche « TikTok » sur sa campagne (vidéo ou images)
   → Partageur TikTok éligible (≥ 1 000 abonnés, compte vérifié) publie la vidéo sur son compte
       et invite ses abonnés à cliquer sur le lien de sa bio
   → Le lien de la bio ouvre SA VITRINE Pubwek (pubwek.com/@pseudo)
   → La vitrine liste les campagnes qu'il diffuse en ce moment
   → L'abonné touche « Contacter sur WhatsApp » sur une campagne
   → Clic suivi « source TikTok » → partageur payé, annonceur facturé
   → L'abonné arrive sur le WhatsApp de l'annonceur avec un message prérempli
      qui indique « Source : TikTok (@pseudo) »
```

**Pourquoi une vitrine plutôt qu'un lien par vidéo.** Sur TikTok, les liens ne sont pas cliquables dans les légendes : on ne peut en mettre qu'**un seul, dans la bio**. La vitrine résout ce problème. Un lien unique, toujours le même, donne accès à toutes les campagnes du partageur, et chaque clic reste attribué à la bonne campagne.

**Pourquoi 1 000 abonnés minimum.**
- En dessous, l'audience est trop faible pour qu'un annonceur y gagne.
- C'est aussi, dans de nombreux pays, le seuil à partir duquel TikTok autorise un lien cliquable dans la bio d'un compte personnel. Les comptes professionnels peuvent avoir ce lien plus tôt selon les régions. **Le développeur doit vérifier la règle actuelle de TikTok pour le Bénin**, car elle détermine si le parcours est possible.
- **Décision du fondateur : le seuil est le même pour les comptes TikTok professionnels et personnels.**
- Le seuil reste réglable par l'admin (`tiktok_abonnes_min`, 1 000 par défaut).

---

## 2. Côté annonceur

### 2.1 Choix des réseaux de diffusion (nouvelle campagne)

Dans le formulaire « Lancer une campagne », une nouvelle rubrique **« Où diffuser ? »** apparaît juste après le style de diffusion :

```
Où diffuser votre annonce ?
[✓] Statuts WhatsApp          (coché par défaut)
[ ] TikTok   — Nouveau        (disponible pour les campagnes Vidéo et Multi-statuts)
```

- L'annonceur peut choisir **un réseau, ou les deux**. Il faut garder au moins un réseau coché.
- **TikTok n'est proposé que pour les formats A (vidéo) et B (images).** Une campagne « texte seul » ne convient pas à TikTok, qui n'affiche que de la vidéo ou des photos. Si l'annonceur choisit C, la case TikTok est grisée avec l'explication « TikTok nécessite une vidéo ou des images ».
- **Prévoir l'architecture pour d'autres réseaux plus tard** (Facebook, Instagram…). Les réseaux doivent être une **liste**, pas un booléen « tiktok oui/non ». Voir le modèle de données, section 10.

### 2.2 Formulaire TikTok (affiché seulement si TikTok est coché)

Trois champs au maximum, sur un ton professionnel :

| Champ | Obligatoire ? | Détail |
|---|---|---|
| « Avez-vous un compte TikTok professionnel pour votre activité ? » | Oui (Oui / Non) | Si Non, aucun autre champ n'est demandé. La campagne est diffusée sur TikTok quand même. |
| « Lien de votre profil TikTok » | Non, affiché seulement si Oui | Format contrôlé : `https://www.tiktok.com/@nom`, ou `@nom` reconverti automatiquement. |
| « Nom à mentionner dans les vidéos » | Rempli automatiquement depuis le lien | Exemple : `@patisseriedouxmatin`. |

**Pourquoi demander le profil TikTok de l'annonceur.** Chaque partageur **mentionne (tague) l'annonceur** dans la légende de sa vidéo. L'annonceur gagne ainsi aussi des abonnés et de la visibilité, ce qui rend l'offre plus attractive. Ce n'est pas obligatoire, et le formulaire l'explique en une ligne : *« Facultatif : si vous l'indiquez, chaque partageur vous mentionnera dans sa vidéo. »*

### 2.3 Prix et budget

- **Pas de nouveau calcul compliqué.** L'objectif de clics reste **unique**, tous réseaux confondus. Un clic venu de TikTok compte comme un clic venu de WhatsApp.
- **Décision du fondateur : au lancement, TikTok coûte le même prix que WhatsApp.** L'annonceur paie le même tarif par clic, et le partageur gagne le même montant par clic.
- Pour garder de la souplesse plus tard, l'admin dispose d'un **supplément TikTok** : `tiktok_supplement_cout_pct` pour l'annonceur et `tiktok_bonus_gain_pct` pour le partageur, **réglés à 0 % au lancement**.
- Si un supplément est défini plus tard, le budget affiché est calculé au tarif le plus élevé des réseaux choisis. Pubwek garantit ainsi de ne jamais manquer de budget. La différence non consommée suit les règles de remboursement existantes.

### 2.4 Validation par l'équipe

La validation actuelle (« En analyse ») contrôle en plus, si TikTok est coché :
- que la vidéo est conforme aux règles de TikTok : pas de contenu interdit, durée et format compatibles ;
- que le lien TikTok de l'annonceur, s'il est fourni, est un vrai compte en rapport avec l'activité.

L'admin peut **valider la campagne pour WhatsApp et refuser TikTok seulement**, en indiquant un motif. La campagne n'est alors pas bloquée.

### 2.5 Statistiques par réseau

Sur la page « Partageurs & statistiques » existante :
- ajouter une répartition simple : **Clics WhatsApp / Clics TikTok / Clics site web** (le site web existe déjà) ;
- pour TikTok, afficher aussi les **vues de vitrine** et le **taux de clic**, pour que l'annonceur voie l'intérêt ;
- garder la notation par partageur (« De vrais clients / Peu de contacts / Aucun contact ») et le bouton « Signaler », qui s'appliquent aussi aux partageurs TikTok.

### 2.6 Campagnes déjà en cours : activation TikTok facultative

Toutes les campagnes actuelles peuvent profiter de la nouveauté, **sans obligation** :

1. **Annonce sur le tableau de bord annonceur**, avec le modèle `Annonce` existant et `public = annonceur` :
   *« Nouveau : diffusez aussi vos campagnes sur TikTok, sans frais supplémentaires. Activez-le en 1 minute sur vos campagnes en cours. »*
2. **Un bandeau sur chaque campagne en cours** éligible (format A ou B) dans « Mes campagnes » :
   *« Diffuser aussi sur TikTok ? »* avec les boutons **[Activer]** et **[Plus tard]**.
3. **[Activer]** ouvre le même mini-formulaire qu'en 2.2. L'envoi crée une **demande d'ajout du réseau TikTok**, contrôlée rapidement par l'admin selon les règles de 2.4.
4. Une fois l'ajout validé, la campagne apparaît chez les partageurs TikTok éligibles. Il n'y a **pas de nouveau paiement** : les clics TikTok consomment le même objectif.
5. **[Plus tard]** masque le bandeau pendant 7 jours (`rappel_tiktok_jours`, réglable). Il n'y a pas de relance agressive.
6. Une notification interne et un e-mail sont envoyés une seule fois à chaque annonceur concerné.

---

## 3. Côté partageur : devenir « Créateur TikTok Pubwek »

### 3.1 Inscription au programme TikTok (en 3 étapes)

Dans le tableau de bord partageur, une carte **« Gagne avec ton compte TikTok »** :

1. **Lier son compte.** Le partageur saisit son lien de profil TikTok.
2. **Prouver que le compte est à lui.** Pubwek affiche un **code unique**, par exemple `PW-7K4Q`, à placer temporairement dans sa bio TikTok. Puis il clique sur « Vérifier ». C'est la protection de base contre ceux qui déclarent le compte de quelqu'un d'autre.
3. **Montrer son nombre d'abonnés.** Il envoie une capture de son profil montrant ses abonnés et ses statistiques. L'admin valide.
   *Amélioration recommandée :* utiliser la connexion officielle TikTok (Login Kit) et l'API d'affichage pour lire automatiquement le nombre d'abonnés et confirmer la propriété du compte, sans capture à envoyer. Le développeur doit vérifier sur developers.tiktok.com les conditions d'accès et les autorisations nécessaires, en particulier la lecture des statistiques du profil.

Les statuts possibles sont : **En attente → Validé → (Suspendu / Refusé)**. Chaque refus est accompagné d'un motif.

**Conditions d'éligibilité, toutes réglables par l'admin :**

| Condition | Valeur par défaut |
|---|---|
| Abonnés minimum (même seuil pour les comptes professionnels et personnels) | 1 000 |
| Compte public, actif, avec au moins N vidéos publiées | N = 5 |
| Engagement minimum : vues moyennes ≥ X % des abonnés, pour repérer les abonnés achetés | X = 3 % (indicatif, l'admin juge) |
| Revérification périodique | tous les 60 jours |

### 3.2 La vitrine du partageur : sa page publique

**Adresse :** `pubwek.com/@pseudo`, ou `pubwek.com/v/pseudo` si le « @ » pose problème. C'est **le lien qu'il met dans sa bio TikTok**.

**Contenu, en une seule page légère, pensée pour le téléphone et le navigateur intégré à TikTok :**
- en haut : photo, pseudo, courte description de 150 caractères maximum, modérée, et le badge **« Créateur vérifié Pubwek »** ;
- en dessous : **les campagnes qu'il diffuse en ce moment**, sous forme de cartes avec visuel, nom du commerce, ville et bouton **[Contacter sur WhatsApp]** ;
- l'ordre des cartes : la plus récente en premier ; le partageur peut épingler une campagne en tête ;
- en bas : la mention **« Liens sponsorisés · Propulsé par Pubwek »**, pour rester transparent.

**Règles de conception :**
- **aucune inscription** demandée au visiteur ;
- chargement très rapide : pas de vidéo lourde en lecture automatique, seulement des miniatures ;
- **une seule action par carte**, pour éviter que le visiteur se perde ;
- les campagnes terminées, en pause ou épuisées disparaissent **automatiquement** de la vitrine ;
- si la vitrine est vide, afficher « Aucune offre en ce moment, reviens bientôt », jamais une page d'erreur.

### 3.3 Diffuser une campagne sur TikTok

Dans « Campagnes disponibles », les campagnes TikTok portent une étiquette **TikTok**. Le bouton **[Diffuser sur TikTok]** :

1. **ajoute la campagne à sa vitrine** et crée le partage avec un lien suivi propre au canal TikTok ;
2. propose le **téléchargement de la vidéo** (ou des images) de l'annonceur ;
3. génère une **légende TikTok prête à copier**, par exemple :
   ```
   🎂 Gâteaux d'anniversaire sur commande à Cotonou !
   Commande directement via le lien dans ma bio 👆
   @patisseriedouxmatin #Cotonou #Bénin #Pubwek #Partenariat
   ```
4. affiche un rappel important : **activer « Contenu commercial / Contenu de marque » dans TikTok** au moment de publier. C'est une règle de TikTok pour les publications rémunérées. Ne pas l'activer expose le compte du partageur à des sanctions de TikTok, donc le rappel est obligatoire dans le parcours.

Le nombre maximum de campagnes affichées en même temps sur une vitrine est réglable (`vitrine_campagnes_max`, 10 par défaut). Cela garde la page lisible.

### 3.4 Gains

- Mêmes gains par clic réel que WhatsApp (10 F vidéo, 5 F par image, 5 F texte), plus un éventuel bonus TikTok réglable (0 % par défaut).
- Les gains TikTok passent par une **période d'attente** avant d'être retirables (`tiktok_gain_attente_heures`, 24 h par défaut). Cela laisse le temps à l'anti-fraude d'annuler les faux clics avant qu'ils soient payés.

---

## 4. Côté client final (l'abonné TikTok)

1. Il voit la vidéo sur TikTok, avec la légende « Lien dans ma bio ».
2. Il ouvre la vitrine du créateur.
3. Il touche « Contacter sur WhatsApp » sur l'offre qui l'intéresse.
4. WhatsApp s'ouvre avec le message prérempli existant, complété par sa source :
   ```
   Bonjour,
   Je vous contacte suite à votre publicité vue sur Pubwek.
   Campagne : n°12
   Produit / service : Pâtisserie Doux Matin
   Partageur : Koffi229
   Source : TikTok
   Je suis intéressé(e), pouvez-vous me donner plus d'informations ?
   ```

Il y a **3 gestes au total, sans compte et sans formulaire.**

---

## 5. Traçabilité : savoir d'où vient chaque clic

C'est une exigence du fondateur : **chaque clic doit indiquer sa source**.

- **Lien suivi par canal.** Chaque partage est rattaché à un réseau : `CampaignShare.canal` vaut `whatsapp` ou `tiktok`. Le bouton de la vitrine pointe vers une route dédiée, par exemple `/t/<jeton>/tiktok`, qui enregistre le clic avec `CampaignClick.link_type = "tiktok"` puis redirige vers `wa.me`, sur le modèle exact de `/t/<jeton>/whatsapp`.
- **Événements de la vitrine.** Enregistrer, dans une table légère, `vitrine_vue` (une ouverture de page) et `vitrine_clic` (un clic sur une carte), avec la date, l'empreinte IP et le navigateur. Ces données servent les statistiques et l'anti-fraude.
- **Origine réelle.** Enregistrer le `Referer` et repérer le navigateur intégré à TikTok, reconnaissable dans le user-agent. Ce n'est **qu'un signal**, jamais une condition bloquante, car il peut être absent ou falsifié. L'admin voit la part de clics « venant visiblement de TikTok » par partageur. Une part anormalement basse est un indice de fraude.
- **Tableaux de bord.**
  - Annonceur : clics par réseau.
  - Partageur : vues de vitrine, clics et gains par réseau.
  - Admin : clics par réseau, par campagne et par créateur, avec export CSV.

---

## 6. Parrainage : nouvelle règle

### 6.1 Ce qui change

| Avant (aujourd'hui) | Après |
|---|---|
| Le parrain est crédité d'un montant fixe (200 F) **dès l'inscription** du filleul (`crediter_parrainage_partageur`) | **Plus rien à l'inscription.** On récompense l'activité réelle du filleul, pas une simple inscription. |

**Pourquoi.** Une prime à l'inscription pousse à créer de faux comptes, et le code doit déjà s'en protéger. Récompenser l'activité réelle supprime ce problème et pousse le parrain à **aider son filleul à partager tous les jours**.

### 6.2 Les deux récompenses du parrain (partageur → partageur)

**A. Prime d'activation (décision du fondateur : prime unique).** Quand le filleul obtient son **premier clic réel** (validé par l'anti-fraude) sur une campagne qu'il a partagée, le parrain reçoit **une prime, une seule fois par filleul**.
- **Montant défini par l'admin** : `parrainage_prime_activation`. Il n'a pas de valeur codée en dur. Tant que l'admin ne l'a pas fixé, la prime vaut 0 et aucun versement n'a lieu.
- Il n'y a **pas** de commission sur les clics suivants du filleul.
- La prime n'est versée qu'**une fois par filleul**, même si celui-ci obtient ensuite des milliers de clics. Le développeur doit l'enregistrer avec une date (`prime_activation_versee_le`) pour empêcher tout double versement.

**B. Bonus de régularité, sur le premier retrait du filleul.**
- Si le filleul est **régulier**, le parrain reçoit **10 % du montant du premier retrait** du filleul. Le taux est réglable : `parrainage_bonus_retrait_pct`.
- **C'est Pubwek qui paie ces 10 %.** Rien n'est retiré au filleul, qui reçoit 100 % de son retrait sur son Mobile Money.
- **Pas de retrait, pas de bonus.** Le bonus est versé au moment où le premier retrait du filleul est **effectivement payé**, pas au moment de la demande.
- **Définition de « régulier »**, réglable : le filleul a partagé ou republié des campagnes au moins `parrainage_jours_actifs_min` jours (par exemple 10) sur les `parrainage_fenetre_jours` derniers jours (par exemple 14) avant son premier retrait.
- Le parrain voit sur son tableau de bord la **progression de chaque filleul**, par exemple « Awa : 6 / 10 jours actifs ». Il sait ainsi qui encourager.

**Le parrainage des annonceurs** (pourcentage de la commission Pubwek) **reste inchangé**.

### 6.3 Garde-fous du parrainage

- Aucune récompense si le parrain et le filleul partagent la même IP ou le même appareil. Le contrôle existe déjà : il faut le réutiliser.
- Les clics du filleul qui comptent sont uniquement des clics **valides** selon l'anti-fraude.
- Plafond mensuel de récompenses par parrain, réglable.
- **Filleuls déjà inscrits avant la mise à jour :** les primes déjà versées restent acquises. Les nouvelles règles s'appliquent aux événements suivants, comme le premier clic ou le premier retrait.

---

## 7. Anti-fraude (priorité absolue)

Les fraudes augmentent. Chaque mesure ci-dessous doit être **paramétrable** et **journalisée** dans `FraudLog`.

**Comptes TikTok :**
- code de vérification à placer dans la bio, contre l'usurpation d'un compte ;
- contrôle de l'engagement : un compte de 20 000 abonnés dont les vidéos font 50 vues a probablement acheté ses abonnés, et il est refusé ;
- revérification périodique, avec suspension automatique si le compte passe sous le seuil d'abonnés ;
- un compte TikTok ne peut être lié qu'à **un seul** compte Pubwek.

**Clics :**
- réutiliser tous les garde-fous actuels : doublons par IP sur 24 h, plafonds par partage et par IP, délai minimum entre deux clics, appareils connus ;
- exclure les clics du partageur lui-même : sa propre IP et ses appareils connus, y compris sur sa vitrine ;
- **détection des pics.** Si un créateur fait soudain X fois sa moyenne, ses nouveaux clics passent **« en revue »**, sans gain, jusqu'à la décision de l'admin ;
- les gains TikTok restent en attente 24 h avant d'être retirables (section 3.4) ;
- croiser les clics avec la **notation des annonceurs** (« Aucun contact » répétés) et suspendre automatiquement au-delà d'un seuil réglable.

**Sanctions progressives, décidées par l'admin :** avertissement, puis gel des gains, puis suspension du programme TikTok, puis bannissement. Chaque sanction est notifiée avec son motif.

**Communication anti-arnaque, aussi un argument marketing :** sur la page du programme TikTok et dans la FAQ, écrire : *« Pubwek ne vend jamais de "compte monétisé". L'inscription est gratuite et sans frais. Méfiez-vous de quiconque vous demande de l'argent au nom de Pubwek. »*

---

## 8. Retraits des partageurs et remboursements des annonceurs : délai de 48 h maximum

Aujourd'hui, le message dit « Elle sera traitée sous peu », et la page d'accueil annonce des « retraits instantanés ». Les deux sont flous ou faux.

- **Décision du fondateur : le même délai s'applique aux retraits des partageurs et aux remboursements des annonceurs.**
- Nouveau paramètre unique `delai_traitement_paiements_heures`, **48 par défaut**, réglable par l'admin. Il remplace le « 48 h » aujourd'hui écrit en dur dans les messages de remboursement (`main.py`, `mes_campagnes.html`, page d'accueil) et le « sous peu » des retraits.
- L'utiliser **partout** :
  - message après la demande : *« Votre demande de retrait de X FCFA a été envoyée. Elle sera traitée sous 48 heures maximum. »* ;
  - page « Mes retraits » : statut et heure limite, par exemple « Traitement avant le 28/11 à 14 h 30 » ;
  - page d'accueil, FAQ et page partageurs : **remplacer « Retraits instantanés »** par « Retraits traités sous 48 h maximum ».
- Remboursements annonceurs : mêmes affichages (« Le virement sera effectué sous 48 heures maximum », date et heure limites sur la campagne).
- Côté admin : files des retraits **et des remboursements**, **triées par ancienneté**, avec un compte à rebours. Un retrait proche des 48 h apparaît en orange, un retrait qui les dépasse en rouge. Une alerte est envoyée à l'admin et aux sous-admins chargés des retraits ou des remboursements quand il reste moins de 6 h (réglable).

---

## 9. Espace admin : ce qu'il faut ajouter

1. **Paramètres** (dans l'écran existant des réglages), voir le tableau de la section 9.1.
2. **File « Créateurs TikTok »** : demandes en attente avec le lien du profil, la capture, l'état du code de bio, et des indicateurs (abonnés, vues moyennes, engagement). Actions : Valider, Refuser (avec motif) ou Suspendre.
3. **File « Ajouts TikTok aux campagnes »** : les demandes des annonceurs existants (section 2.6), avec Valider ou Refuser.
4. **Modération des vitrines** : descriptions et photos signalées. L'admin peut masquer une vitrine.
5. **Revue de fraude TikTok** : clics « en revue », créateurs dont les clics font un pic, comptes à faible engagement.
6. **Statistiques par réseau** : clics, gains versés et revenus, par jour, par campagne et par créateur, avec export CSV.
7. **Annonces** : réutiliser le module `Annonce` pour la communication de lancement aux annonceurs et aux partageurs.
8. **Suivi du parrainage** : primes et bonus versés, refusés pour fraude, et progression des filleuls.

### 9.1 Paramètres réglables (aucune valeur en dur)

| Paramètre | Rôle | Valeur par défaut |
|---|---|---|
| `tiktok_actif` | Activer ou couper tout le programme TikTok d'un coup | Oui |
| `tiktok_abonnes_min` | Abonnés minimum pour être éligible | 1 000 |
| `tiktok_videos_min` | Vidéos publiées minimum | 5 |
| `tiktok_engagement_min_pct` | Vues moyennes / abonnés (indicatif) | 3 % |
| `tiktok_reverification_jours` | Revérification du compte | 60 |
| `tiktok_supplement_cout_pct` | Supplément facturé à l'annonceur pour TikTok | 0 % (même prix que WhatsApp au lancement) |
| `tiktok_bonus_gain_pct` | Bonus de gain du partageur sur TikTok | 0 % |
| `tiktok_gain_attente_heures` | Délai avant qu'un gain TikTok soit retirable | 24 |
| `vitrine_campagnes_max` | Nombre maximum de campagnes sur une vitrine | 10 |
| `rappel_tiktok_jours` | Délai avant de réafficher « Plus tard » | 7 |
| `parrainage_prime_activation` | Montant de la prime unique, versée au premier clic réel du filleul | défini par l'admin (0 tant qu'il n'est pas fixé) |
| `parrainage_bonus_retrait_pct` | Bonus sur le premier retrait, payé par Pubwek | 10 % |
| `parrainage_jours_actifs_min` / `parrainage_fenetre_jours` | Définition de « régulier » | 10 / 14 |
| `parrainage_plafond_mensuel` | Récompenses maximum par parrain et par mois | à fixer |
| `delai_traitement_paiements_heures` | Délai affiché et suivi des retraits **et** des remboursements | 48 |
| `alerte_retrait_heures_restantes` | Seuil d'alerte admin | 6 |
| Seuils anti-fraude (pics, notations « Aucun contact »…) | Voir section 7 | à fixer |

---

## 10. Modèle de données (proposition)

- **`Campaign`** :
  - `reseaux` : liste, par exemple `["whatsapp", "tiktok"]` ;
  - `tiktok_compte_pro` (booléen), `tiktok_profil_url`, `tiktok_mention` ;
  - `tiktok_statut` : `non_demande`, `en_attente`, `valide` ou `refuse`, avec un motif.
- **`CampaignShare`** :
  - `canal` (`whatsapp` / `tiktok`) ;
  - `epingle_vitrine` (booléen).
- **`CampaignClick`** :
  - nouvelle valeur `link_type = "tiktok"` ;
  - `referer` ;
  - `statut_revue` : `ok`, `en_revue` ou `rejete` ;
  - `disponible_le`, la date à laquelle le gain devient retirable.
- **Nouvelle table `CompteTikTok`** :
  - `user_id`, `url`, `pseudo` (unique), `code_verification`, `verifie_le` ;
  - `abonnes`, `vues_moyennes` ;
  - `statut`, `motif`, `prochaine_reverification`.
- **Nouvelle table `Vitrine`** : `user_id`, `slug` (unique), `description`, `photo`, `masquee`.
- **Nouvelle table `EvenementVitrine`** : `vitrine_id`, `type` (`vue` / `clic`), `campaign_share_id`, `ip_hash`, `user_agent`, `cree_le`.
- **Parrainage** : une table ou des champs pour suivre `prime_activation_versee_le`, `bonus_retrait_verse_le` et le nombre de jours actifs du filleul.
- **`SystemConfig`** : tous les paramètres de la section 9.1, avec des migrations du même type que celles déjà en place (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`).

---

## 11. Idées supplémentaires pour booster TikTok

1. **Légende TikTok générée automatiquement**, avec des hashtags locaux (#Cotonou, #Calavi…), la mention de l'annonceur et « lien dans ma bio » (section 3.3).
2. **Badge « Créateur vérifié Pubwek »** sur la vitrine, avec des paliers (Bronze, Argent, Or) selon les clics réels générés. Cela crée de l'émulation et de la confiance.
3. **Classement mensuel des créateurs** par commune, avec une prime réglable pour les premiers. Ce classement doit reposer uniquement sur les **clics validés**.
4. **Vitrine réutilisable partout.** Le même lien fonctionne dans une bio Instagram ou Facebook, ou dans un statut WhatsApp. C'est un premier pas vers d'autres réseaux sans travail supplémentaire. Ajouter un **QR code** de la vitrine à télécharger.
5. **Alerte « Nouvelle campagne TikTok dans ta zone »** pour les créateurs validés.
6. **Rapport annonceur hebdomadaire** : vues de vitrine, clics par réseau et meilleurs créateurs.
7. **Créateurs « premium »** au-delà de N abonnés, avec un bonus de gain réglable, pour attirer les gros comptes.
8. **Kit du créateur** : 3 courtes vidéos tutoriels (lier son compte, mettre le lien en bio, publier en activant le contenu commercial).
9. **Campagne de lancement anti-arnaque** : « Ton compte TikTok peut enfin te rapporter, honnêtement. » C'est un message fort face aux faux vendeurs de « comptes monétisés ».

---

## 12. Ordre de réalisation conseillé

| Étape | Contenu | Pourquoi d'abord |
|---|---|---|
| 1 | Retraits sous 48 h (section 8) et paramètres réglables | Rapide, améliore tout de suite la confiance |
| 2 | Nouveau parrainage (section 6) | Supprime une source de fraude actuelle |
| 3 | Réseaux de diffusion côté annonceur, avec le formulaire TikTok et le suivi des clics TikTok (sections 2 et 5) | La base de tout le reste |
| 4 | Programme Créateur TikTok et vitrine (sections 3 et 4) | Le cœur de la nouveauté |
| 5 | Activation pour les campagnes existantes (section 2.6) et communication | Donne du volume dès le lancement |
| 6 | Revue de fraude TikTok avancée et idées supplémentaires (sections 7 et 11) | À affiner avec les premières données réelles |

---

## 13. Décisions du fondateur

1. **Parrainage** : une prime unique au premier clic réel du filleul, dont le montant est défini par l'admin.
2. **Prix TikTok** : le même prix que WhatsApp au lancement (supplément TikTok réglé à 0 %).
3. **Délai de 48 h** : il s'applique aux retraits des partageurs **et** aux remboursements des annonceurs, avec un seul paramètre réglable.
4. **Seuil d'abonnés** : 1 000 abonnés pour les comptes TikTok professionnels **et** personnels. Le développeur doit tout de même vérifier que TikTok permet un lien cliquable dans la bio à ce niveau au Bénin (section 1), car tout le parcours en dépend.

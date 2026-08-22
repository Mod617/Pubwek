# Pubwek — marche à suivre

Ce dossier contient **ton code du 21 août (branche `main`, commit `d8240ad`)**
avec les correctifs de l'audit, l'anti-fraude sur les clics, et la génération
vidéo retirée.

43 tests passent : `python test_correctifs_audit.py`

---

# Ce qu'il faut faire, dans l'ordre

Chaque étape suppose la précédente. La 3 avant la 4 en particulier : inverser
les deux fait planter le site.

## Étape 1 — Sécurité ✅ FAIT (vérifié le 21 août au soir)

Vérifié directement sur le dépôt : la branche `principal` a disparu, le `.env`
renvoie 404 sur les deux branches, et `instance/pubwek.db` a bien été supprimé
par le commit `7f0f02e8`. Les clés tierces ont été révoquées.

Ce qui reste de cette étape : **changer le mot de passe admin** (point 2
ci-dessous), qui ne se fait pas en modifiant une variable.

1. ~~Révoquer les six secrets~~ — fait. Pour mémoire : : `SECRET_KEY`, `MAIL_PASSWORD`,
   `ADMIN_PASSWORD`, `FEDAPAY_SECRET_KEY`, `FEDAPAY_PUBLIC_KEY`,
   `CREATOMATE_API_KEY`. Commence par Creatomate, la seule facturée à l'usage.
2. **Changer le mot de passe admin** — pas en modifiant `ADMIN_PASSWORD` !
   Cette variable ne sert QU'À la toute première création du compte ; s'il
   existe déjà, le code l'ignore. Utilise l'outil fourni :
   ```bash
   python changer_mot_de_passe.py
   ```
   Son empreinte actuelle est publique via `instance/pubwek.db`, versionné sur
   les deux branches, et `KOUDOGBOPIERRETTE` est un nom propre : cassable hors
   ligne en quelques minutes.
3. ~~Supprimer la branche `principal`~~ — fait.
4. ~~Sortir la base du dépôt~~ — fait (commit `7f0f02e8`). L'interface GitHub
   crée le commit automatiquement : il n'y avait rien de plus à faire.

## Étape 2 — Variables d'environnement

Sur Railway, dans les variables du projet :

| Variable | Valeur |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ENV` | `production` — active HTTPS forcé, cookies sécurisés, CSP |
| `FEDAPAY_WEBHOOK_SECRET` | à récupérer dans le tableau de bord FedaPay |
| `RESEND_API_KEY` | sinon la réinitialisation de mot de passe est désactivée |
| `REDIS_URL` | seulement si tu ajoutes un service Redis (facultatif) |

Gmail et Flask-Mail ont été retirés : l'envoi passe par l'API Resend, en HTTPS,
parce que Railway bloque le SMTP sortant. Les variables `MAIL_*` et `TWILIO_*`
ne servaient plus à rien et ont disparu. Vérifie que le domaine d'expédition
`noreply@pubwek.com` est bien validé côté Resend, sinon les envois sont
refusés sans que l'application le sache.

Puis, dans FedaPay, **déclarer l'URL du webhook** :
`https://<ton-domaine>/webhooks/fedapay`

Sans lui, un paiement n'est validé que si le client revient sur le site — et en
mobile money, beaucoup ferment l'onglet dès le SMS de confirmation.

`ENV=production` invalidera les sessions en cours : tout le monde devra se
reconnecter une fois. C'est normal.

## Étape 3 — Migrer la base, AVANT de déployer

Les nouvelles colonnes n'existent pas dans ta base. `db.create_all()` crée les
tables manquantes mais **jamais une colonne dans une table qui existe déjà**.

```bash
# 1. Sauvegarder (Railway → Data → Backup)
# 2. Puis :
psql $DATABASE_URL -f migration_audit.sql
```

Le fichier est idempotent : le relancer ne casse rien. Sans cette étape,
l'application plante au démarrage avec
`column users.last_seen_ip does not exist`.

## Étape 4 — Déployer le code

```bash
git checkout main && git pull
git checkout -b correctifs-audit
git apply /chemin/vers/correctifs-audit.patch
python test_correctifs_audit.py      # doit afficher 43 réussis, 0 échoués
git add -A
git commit -m "Audit : anti-fraude, retrait video, correctifs"
git push -u origin correctifs-audit
```

Ouvre une pull request vers `main` : ça te laisse relire chaque modification
avant de fusionner. Le correctif a été testé, il s'applique sur `d8240ad` sans
aucun conflit.

## Étape 5 — Une seule fois après le déploiement

```bash
python backfill_uploads.py
```

Rattache les logos et visuels existants à leur propriétaire. Sans lui, la
nouvelle protection de `/uploads/` refuse à tes annonceurs l'accès à leurs
propres fichiers.

## Étape 6 — Vérifier que tout marche

- Créer un compte partageur, le confirmer côté admin, partager une campagne.
  **Avant, ça plantait** (voir les trois bugs plus bas).
- Ouvrir le lien de tracking depuis un autre appareil : le clic doit être
  compté et le portefeuille crédité.
- Le rouvrir tout de suite : rien ne doit être crédité une seconde fois.
- Faire un paiement de test et fermer l'onglet avant le retour : la campagne
  doit quand même passer en payée, grâce au webhook.

## Étape 7 — Au bout de quelques jours, régler les seuils

```sql
SELECT rejection_reason, COUNT(*)
FROM campaign_clicks
WHERE clicked_at > NOW() - INTERVAL '7 days'
GROUP BY rejection_reason ORDER BY 2 DESC;
```

`robot` en tête est normal : ce sont les aperçus de lien WhatsApp. Si
`auto_clic` ou `plafond_partage` remontent souvent, regarde quels partageurs
sont concernés. Les seuils se règlent dans `SystemConfig`, sans redéployer.

---

# Trois bugs qui bloquaient tout

Tous les trois sont **dans ton code actuellement en ligne**, et corrigés ici.
Ils rendent la rémunération par clic — le cœur du nouveau modèle —
inutilisable de bout en bout.

**`models.py` utilisait `uuid` sans l'importer.** Le `tracking_token` de
`CampaignShare` appelle `uuid.uuid4()` par défaut. Dès qu'un partageur clique
sur « Partager cette campagne », ça lève `NameError`. Aucun partage n'a jamais
pu être créé.

**La route de tracking WhatsApp appelait `quote()` sans l'importer.** Partout
ailleurs tu écris `urllib.parse.quote`. L'appel étant hors du `try`, chaque
clic renvoyait une erreur 500 : le visiteur n'arrivait jamais sur WhatsApp.

**Les pages « partageurs » et « suivi de campagne » utilisaient `View` sans
l'importer** — 8 usages, aucun import. Les deux plantaient dès qu'une campagne
avait au moins un partageur. Sur une campagne vide, la fonction sortait avant :
c'est ce qui masquait le problème.

Ces trois erreurs sont invisibles quand on modifie le code dans l'interface
GitHub, puisque l'application n'est jamais lancée. `python test_correctifs_audit.py`
les aurait toutes attrapées en deux secondes. C'est le réflexe à prendre avant
chaque `push`.

---

# Anti-fraude sur les clics

C'était le point le plus grave : `/t/<token>/whatsapp` et `/t/<token>/site`
sont publics, et **chaque ouverture créditait le portefeuille du partageur en
argent réel**, sans le moindre contrôle. Ouvrir son propre lien en boucle
suffisait à se payer.

Désormais tous les clics sont enregistrés, mais seuls ceux qui passent
`evaluer_clic()` sont rémunérés. Le motif de refus reste sur la ligne, ce qui
permet de justifier un solde si un partageur le conteste.

| Contrôle | Ce qu'il empêche |
|---|---|
| Campagne non diffusable | Payer sur une campagne terminée ou impayée |
| Agents automatiques | **L'aperçu de lien WhatsApp**, qui visite le lien à chaque publication de statut et générait donc un clic payé fantôme. Plus les robots, `curl`, scripts |
| Absence d'IP | Un clic non déduplicable |
| Auto-clic | Le partageur qui ouvre son lien depuis l'appareil où il est connecté |
| Quota journalier | Dépasser ce que l'annonceur a acheté pour la journée |
| Déduplication (partage, IP) | La même machine payée plusieurs fois — **le garde-fou principal** |
| Anti-rafale | Deux clics payés à quelques secondes d'intervalle |
| Plafond par partage et par jour | Un partageur qui change d'IP pour multiplier ses gains |
| Plafond par IP et par jour | Une machine qui fait le tour de toutes les campagnes |

L'IP est lue via `request.remote_addr`, résolu par `ProxyFix`. Ton code lisait
l'en-tête `X-Forwarded-For` brut, que le client envoie lui-même : il suffisait
de le remplir au hasard à chaque requête pour faire sauter la déduplication.

**Ce que ça ne fait pas** : ça borne le gain d'un tricheur, ça ne l'élimine
pas. Quelqu'un avec plusieurs téléphones en 4G peut encore obtenir quelques
clics payés par jour.

## La vérification des numéros, faite à la main

Puisque la vérification automatique par SMS est écartée, c'est **ton contrôle
manuel qui devient la principale barrière anti-fraude**. Le circuit existe déjà
et il est bien fait : un partageur s'inscrit non confirmé, et tu disposes de
« Confirmer », « Refuser » et « Contacter » dans l'espace admin.

Trois réflexes qui font la différence :

- **Écrire réellement au numéro sur WhatsApp avant de confirmer.** Un numéro
  qui ne répond pas est un numéro inventé.
- **Refuser les doublons évidents** : même personne, plusieurs numéros. C'est
  le scénario que les plafonds par IP ne couvrent pas.
- **Recouper avec la commune déclarée.** Un partageur censé être à Parakou dont
  tous les clics viennent de la même IP à Cotonou mérite un coup d'œil.

J'ai supprimé `verify_phone.html` : ce gabarit n'était relié à aucune route et
son formulaire postait vers le vide. Il laissait croire qu'une vérification
automatique existait.

---

# Génération vidéo retirée

Creatomate, le diaporama et l'abonnement vidéo sont retirés du premier
lancement : environ 1 000 lignes et 7 fichiers en moins.

**Le téléversement direct d'une vidéo par l'annonceur (option A) n'est pas
touché** — c'est la génération automatique qui disparaît, pas les campagnes
vidéo. `git revert 832948e` remet tout en place le jour venu.

Les modèles `VideoGenerationConfig` et `UserSubscription` sont conservés, ils
contiennent des données. Dépendances retirées : `moviepy`, `numpy`, `celery`,
`mutagen`, `geopy`.

---

# Vocabulaire : des clics, plus des vues

L'interface annonçait des « vues » alors que la facturation, la rémunération et
les compteurs portent tous sur des **clics**. Tes propres CGU disaient déjà
« facturées exclusivement sur la base des clics réels et uniques ».

L'écart n'était pas que cosmétique : un annonceur achetant « 1 000 vues »
recevait en réalité un objectif de 1 000 clics — ni le même coût, ni la même
difficulté à atteindre. Corrigé dans 7 gabarits, le formulaire de campagne et
les messages d'erreur.

Au passage, la colonne « Vues générées » des pages partageurs a été supprimée :
elle lisait la table `View`, qui n'est alimentée nulle part, et affichait donc
invariablement zéro. Le tri se fait désormais sur le total des clics.

Les colonnes `whatsapp_views`, `views_today` et `views_per_day` gardent leur nom
en base — une migration serait risquée pour un gain purement cosmétique — mais
un commentaire dans `models.py` signale qu'elles comptent des clics.

---

# Autres améliorations

**Le lien de réinitialisation ne sert plus qu'une fois.** Il restait valable une
heure même après changement du mot de passe. Il porte maintenant une empreinte
du mot de passe : dès que celui-ci change, le lien est périmé.

**Changer de mot de passe ferme toutes les sessions.** Utile si un compte a été
compromis.

**Verrous sur le portefeuille.** `demander_retrait()` vérifiait le solde puis
débitait en deux temps : deux demandes simultanées pouvaient sortir le même
argent deux fois. Même chose sur les deux routes admin, où un double-clic
pouvait rembourser ou payer deux fois.

**Mots de passe : 10 caractères minimum partout** (c'était 6, 6 et 8 selon
l'endroit).

**ffmpeg rétabli** via `nixpacks.toml`. Sans lui, tout téléversement de vidéo
échoue en production.

---

# Ce que ton code fait bien

À dire, parce que c'est réel. Les retraits sont sérieusement construits : liste
blanche des moyens de paiement, vérification du solde, débit qui réserve le
montant, journal des mouvements, garde anti-doublon. Le système de permissions
des sous-admins est propre — `verifier_super_admin_strict()` empêche un
sous-admin de se promouvoir, et les permissions sont filtrées par liste
blanche. Les exports PDF et la vue partageurs sont correctement filtrés par
utilisateur : j'ai cherché des fuites entre comptes, il n'y en a pas. Le mot de
passe oublié est bien fait, avec anti-énumération et envoi asynchrone.

---

# Ce qui reste, plus tard

**Installer Flask-Migrate.** Tu viens de voir pourquoi à l'étape 3 : chaque
nouvelle colonne demande une intervention manuelle en base. Tant qu'il n'y a
pas de vrais utilisateurs, la migration initiale est indolore.

**Découper `main.py`.** 4 400 lignes, tout mélangé. Des blueprints (auth,
annonceur, partageur, admin) rendraient la suite bien plus confortable.

**Lancer les tests avant chaque `push`.** C'est ce qui aurait évité les trois
bugs bloquants.

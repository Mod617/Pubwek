"""Tests de non-régression des correctifs issus de l'audit.

Vérifie que les failles corrigées ne réapparaissent pas. Aucun paquet
supplémentaire n'est nécessaire : le fichier s'exécute directement.

    python test_correctifs_audit.py

Sortie : code 0 si tout passe, 1 sinon (utilisable en intégration continue).

Ces tests touchent la base configurée dans .env — utilisez une base de
développement, pas la production.
"""

import sys

import main
from main import app, db
from models import Campaign, User, UserSubscription, UploadedFile
from forms import numero_whatsapp_valide

app.config["WTF_CSRF_ENABLED"] = False

_resultats = {"ok": 0, "echec": 0}


def verifier(nom, condition, detail=""):
    if condition:
        _resultats["ok"] += 1
        print(f"  OK    {nom}")
    else:
        _resultats["echec"] += 1
        print(f"  ECHEC {nom} {detail}")


# =============================================================================
# Jeux d'essai
#
# Les tests créent eux-mêmes ce dont ils ont besoin, puis le suppriment. Sans
# cela, ils s'ignoraient silencieusement sur une base neuve — c'est-à-dire
# exactement au moment où l'on a le plus besoin qu'ils s'exécutent.
# =============================================================================

MARQUEUR = "audit-nonreg"


def _utilisateur_temporaire(role="annonceur"):
    """Crée un utilisateur jetable, ou renvoie celui déjà créé par un test."""
    email = f"{MARQUEUR}-{role}@exemple.invalid"
    u = User.query.filter_by(email=email).first()
    if u:
        return u, False
    u = User(
        email=email,
        password_hash="x" * 60,
        role=role,
        province="Littoral",
        commune="Cotonou",
        is_confirmed=True,
        pseudo=f"{MARQUEUR}{role}",
    )
    db.session.add(u)
    db.session.commit()
    return u, True


def _campagne_temporaire(owner):
    """Crée une campagne jetable, ou renvoie celle déjà créée par un test."""
    c = Campaign.query.filter_by(promotion_detail=MARQUEUR).first()
    if c:
        return c, False
    c = Campaign(
        user_id=owner.id,
        promotion_type="test",
        promotion_detail=MARQUEUR,
        provinces="Toutes",
        total_cost=1000.0,
        media_files="",
    )
    db.session.add(c)
    db.session.commit()
    return c, True


def nettoyer_jeux_dessai():
    """Supprime tout ce que les tests ont pu créer."""
    with app.app_context():
        Campaign.query.filter_by(promotion_detail=MARQUEUR).delete()
        UploadedFile.query.filter(UploadedFile.filename.like(f"{MARQUEUR}%")).delete(
            synchronize_session=False
        )
        for u in User.query.filter(User.email.like(f"{MARQUEUR}%")).all():
            UserSubscription.query.filter_by(user_id=u.id).delete()
            db.session.delete(u)
        db.session.commit()


def test_routes_webhooks():
    """S-04 / F-02 : webhooks authentifiés et présents."""
    print("\n[S-04, F-02] Webhooks")
    regles = {str(r) for r in app.url_map.iter_rules()}
    verifier("le webhook FedaPay existe", "/webhooks/fedapay" in regles)
    verifier("plus aucune route Creatomate",
             not any("creatomate" in r for r in regles))
    verifier("plus aucune route de génération vidéo",
             not any(m in r for r in regles
                     for m in ("preview_video", "video_progress", "annuler_generation",
                               "render-assets", "souscrire-abonnement", "video-settings")))

    client = app.test_client()
    r = client.post("/webhooks/fedapay", json={"id": 1})
    verifier("FedaPay : requête sans signature rejetée",
             r.status_code in (400, 503), f"(reçu {r.status_code})")


def test_site_accessible_sans_redis():
    """S-06 : une panne Redis ne doit pas mettre le site hors service."""
    print("\n[S-06] Disponibilité")
    client = app.test_client()
    verifier("page d'accueil", client.get("/").status_code == 200)
    verifier("page de connexion", client.get("/login").status_code == 200)
    verifier("page d'inscription", client.get("/register/annonceur").status_code == 200)


def test_acces_fichiers():
    """S-03 / S-08 : un utilisateur n'accède qu'à ses propres fichiers."""
    print("\n[S-03, S-08] Propriété des fichiers")
    client = app.test_client()
    r = client.get("/uploads/test.jpg")
    verifier("/uploads exige une connexion", r.status_code in (302, 401), f"(reçu {r.status_code})")

    with app.app_context():
        proprietaire, _ = _utilisateur_temporaire("annonceur")
        autre, _ = _utilisateur_temporaire("partageur")

        nom = f"{MARQUEUR}-fichier.jpg"
        UploadedFile.query.filter_by(filename=nom).delete()
        db.session.commit()

        main.enregistrer_upload(nom, proprietaire.id, kind="image")
        db.session.commit()

        verifier("le propriétaire accède à son fichier",
                 main.peut_acceder_au_fichier(proprietaire, nom))

        verifier("un autre utilisateur est refusé",
                 not main.peut_acceder_au_fichier(autre, nom))
        verifier("un fichier inconnu est refusé",
                 not main.peut_acceder_au_fichier(autre, "inexistant.jpg"))

        UploadedFile.query.filter_by(filename=nom).delete()
        db.session.commit()



def test_numeros_whatsapp():
    """F-07 : les numéros béninois actuels doivent être acceptés."""
    print("\n[F-07] Numéros WhatsApp")
    verifier("format actuel à 10 chiffres accepté", numero_whatsapp_valide("+2290197000000"))
    verifier("ancien format à 8 chiffres accepté", numero_whatsapp_valide("+22997000000"))
    verifier("numéro étranger refusé", not numero_whatsapp_valide("+33612345678"))
    verifier("valeur vide refusée", not numero_whatsapp_valide(""))
    verifier("texte arbitraire refusé", not numero_whatsapp_valide("+229abcdefgh"))


def test_nettoyage_epargne_les_fichiers_utilises():
    """Le nettoyage ne doit pas supprimer les médias d'une campagne en cours."""
    print("\n[Nettoyage] Fichiers encore utilisés")
    import os
    import shutil
    import tempfile
    import time

    with app.app_context():
        proprietaire, _ = _utilisateur_temporaire("annonceur")
        campagne, _ = _campagne_temporaire(proprietaire)

        # IMPORTANT : on travaille dans un dossier temporaire, JAMAIS dans le
        # vrai UPLOAD_FOLDER. nettoyer_fichiers_anciens() supprime réellement
        # des fichiers : lancé sur le dossier de production, ce test effacerait
        # les fichiers que la base courante ne référence pas.
        dossier = tempfile.mkdtemp(prefix="pubwek-test-nettoyage-")

        utilise = os.path.join(dossier, "audit-test-utilise.jpg")
        orphelin = os.path.join(dossier, "audit-test-orphelin.jpg")
        for chemin in (utilise, orphelin):
            with open(chemin, "wb") as f:
                f.write(b"test")
            # Antidaté à 30 jours : bien au-delà du seuil de nettoyage
            vieux = time.time() - 30 * 24 * 3600
            os.utime(chemin, (vieux, vieux))

        medias_initiaux = campagne.media_files
        campagne.media_files = ((medias_initiaux or "") + ",audit-test-utilise.jpg").lstrip(",")
        db.session.commit()

        try:
            main.nettoyer_fichiers_anciens(app, dossier)
            verifier("un média de campagne est épargné malgré son âge",
                     os.path.exists(utilise))
            verifier("un fichier orphelin ancien est supprimé",
                     not os.path.exists(orphelin))
        finally:
            campagne.media_files = medias_initiaux
            db.session.commit()
            shutil.rmtree(dossier, ignore_errors=True)


def test_antifraude_clics():
    """Les liens de tracking paient le partageur : vérifier chaque garde-fou."""
    print("\n[ANTI-FRAUDE] Rémunération des clics")
    from datetime import datetime, timedelta
    from models import SystemConfig, CampaignShare, CampaignClick

    NAVIGATEUR = ("Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")

    with app.app_context():
        config = SystemConfig.get_config()
        annonceur, _ = _utilisateur_temporaire("annonceur")
        partageur, _ = _utilisateur_temporaire("partageur")
        camp, _ = _campagne_temporaire(annonceur)

        # Campagne réellement diffusable, quota du jour large
        camp.paid = camp.validated = camp.is_active = True
        camp.media_type = "photo"
        camp.views_per_day = 100
        camp.views_today = 0
        camp.target_whatsapp_views = 1000
        camp.whatsapp_views = 0
        camp.last_quota_date = datetime.utcnow().date()
        camp.daily_quota_paused = False

        share = CampaignShare.query.filter_by(
            campaign_id=camp.id, sharer_id=partageur.id
        ).first()
        if not share:
            share = CampaignShare(campaign_id=camp.id, sharer_id=partageur.id)
            db.session.add(share)
        partageur.last_seen_ip = "10.0.0.99"
        db.session.commit()

        def evaluer(ip, ua=NAVIGATEUR):
            return main.evaluer_clic(share, camp, ip, ua, config)

        def poser_clic_paye(ip, quand=None):
            """Simule un clic déjà rémunéré dans l'historique."""
            c = CampaignClick(
                campaign_share_id=share.id, link_type="whatsapp", ip=ip,
                user_agent=NAVIGATEUR, is_paid=True,
                clicked_at=quand or datetime.utcnow(),
            )
            db.session.add(c)
            db.session.commit()
            return c

        # --- Robots et agents automatiques ---
        ok, motif = evaluer("41.85.1.1", "WhatsApp/2.23.20.79 A")
        verifier("l'aperçu de lien WhatsApp n'est pas payé",
                 not ok and motif == main.MOTIF_ROBOT, f"({motif})")

        ok, motif = evaluer("41.85.1.1", "curl/8.4.0")
        verifier("un appel curl n'est pas payé",
                 not ok and motif == main.MOTIF_ROBOT, f"({motif})")

        ok, motif = evaluer("41.85.1.1", "")
        verifier("un agent vide n'est pas payé",
                 not ok and motif == main.MOTIF_ROBOT, f"({motif})")

        # --- Absence d'adresse IP ---
        ok, motif = evaluer("")
        verifier("sans adresse IP, pas de rémunération",
                 not ok and motif == main.MOTIF_SANS_IP, f"({motif})")

        # --- Le partageur clique sur son propre lien ---
        ok, motif = evaluer("10.0.0.99")
        verifier("le partageur ne se paie pas lui-même",
                 not ok and motif == main.MOTIF_AUTO_CLIC, f"({motif})")

        # --- Clic légitime ---
        ok, motif = evaluer("41.85.10.20")
        verifier("un vrai visiteur est rémunéré", ok, f"({motif})")

        # --- Doublon : même IP, même partage ---
        poser_clic_paye("41.85.10.20")
        ok, motif = evaluer("41.85.10.20")
        verifier("la même IP n'est payée qu'une fois",
                 not ok and motif == main.MOTIF_DOUBLON_IP, f"({motif})")

        # Une autre IP reste payable... mais l'anti-rafale s'applique d'abord
        ok, motif = evaluer("41.85.10.21")
        verifier("deux clics trop rapprochés : le second attend",
                 not ok and motif == main.MOTIF_RAFALE, f"({motif})")

        # --- Hors de la fenêtre anti-rafale, une autre IP passe ---
        for c in CampaignClick.query.filter_by(campaign_share_id=share.id).all():
            c.clicked_at = datetime.utcnow() - timedelta(minutes=10)
        db.session.commit()
        ok, motif = evaluer("41.85.10.21")
        verifier("une autre IP est payée une fois la rafale passée", ok, f"({motif})")

        # --- Plafond par partage et par jour ---
        ancien_plafond = config.max_paid_clicks_per_share_per_day
        config.max_paid_clicks_per_share_per_day = 1
        db.session.commit()
        ok, motif = evaluer("41.85.10.30")
        verifier("plafond de clics payés par partage respecté",
                 not ok and motif == main.MOTIF_PLAFOND_PARTAGE, f"({motif})")
        config.max_paid_clicks_per_share_per_day = ancien_plafond
        db.session.commit()

        # --- Plafond par adresse IP, toutes campagnes confondues ---
        # Le clic déjà payé doit porter sur un AUTRE partage, sinon c'est la
        # déduplication qui répond en premier — et c'est bien le but du plafond
        # par IP : borner une machine qui fait le tour des campagnes.
        autre_camp = Campaign(
            user_id=annonceur.id, promotion_type="test",
            promotion_detail=f"{MARQUEUR}-2", provinces="Toutes",
            total_cost=1000.0, media_files="",
        )
        db.session.add(autre_camp)
        db.session.commit()
        autre_share = CampaignShare(campaign_id=autre_camp.id, sharer_id=partageur.id)
        db.session.add(autre_share)
        db.session.commit()

        db.session.add(CampaignClick(
            campaign_share_id=autre_share.id, link_type="whatsapp",
            ip="41.85.99.99", user_agent=NAVIGATEUR, is_paid=True,
            clicked_at=datetime.utcnow() - timedelta(minutes=5),
        ))
        ancien_ip = config.max_paid_clicks_per_ip_per_day
        config.max_paid_clicks_per_ip_per_day = 1
        db.session.commit()

        ok, motif = evaluer("41.85.99.99")
        verifier("plafond de clics payés par IP respecté (entre campagnes)",
                 not ok and motif == main.MOTIF_PLAFOND_IP, f"({motif})")

        config.max_paid_clicks_per_ip_per_day = ancien_ip
        CampaignClick.query.filter_by(campaign_share_id=autre_share.id).delete()
        db.session.delete(autre_share)
        db.session.delete(autre_camp)
        db.session.commit()

        # --- Campagne terminée ou non payée ---
        camp.is_active = False
        db.session.commit()
        ok, motif = evaluer("41.85.10.40")
        verifier("campagne inactive : aucun clic payé",
                 not ok and motif == main.MOTIF_CAMPAGNE_INACTIVE, f"({motif})")
        camp.is_active = True
        db.session.commit()

        # --- Quota journalier atteint ---
        camp.views_today = camp.views_per_day
        db.session.commit()
        ok, motif = evaluer("41.85.10.50")
        verifier("quota du jour atteint : aucun clic payé",
                 not ok and motif == main.MOTIF_QUOTA_JOUR, f"({motif})")
        camp.views_today = 0
        db.session.commit()

        # --- Le portefeuille est bien crédité, et une seule fois ---
        CampaignClick.query.filter_by(campaign_share_id=share.id).delete()
        partageur.wallet_balance = 0.0
        partageur.last_seen_ip = "10.0.0.99"
        camp.whatsapp_views = 0
        db.session.commit()

        with app.test_request_context(
            "/", environ_base={"REMOTE_ADDR": "41.85.55.1",
                               "HTTP_USER_AGENT": NAVIGATEUR}
        ):
            main.enregistrer_clic(share, camp, "whatsapp")
        db.session.refresh(partageur)
        solde_apres_un_clic = partageur.wallet_balance
        verifier("un clic valide crédite le portefeuille",
                 solde_apres_un_clic == config.reward_per_click_photo,
                 f"(solde={solde_apres_un_clic})")

        with app.test_request_context(
            "/", environ_base={"REMOTE_ADDR": "41.85.55.1",
                               "HTTP_USER_AGENT": NAVIGATEUR}
        ):
            main.enregistrer_clic(share, camp, "whatsapp")
        db.session.refresh(partageur)
        verifier("un second clic de la même IP ne crédite rien",
                 partageur.wallet_balance == solde_apres_un_clic,
                 f"(solde={partageur.wallet_balance})")

        # Les deux clics sont tracés, un seul est marqué payé
        total = CampaignClick.query.filter_by(campaign_share_id=share.id).count()
        payes = CampaignClick.query.filter_by(
            campaign_share_id=share.id, is_paid=True
        ).count()
        verifier("tous les clics sont enregistrés, un seul payé",
                 total == 2 and payes == 1, f"(total={total}, payés={payes})")

        CampaignClick.query.filter_by(campaign_share_id=share.id).delete()
        db.session.delete(share)
        db.session.commit()


def test_reinitialisation_mot_de_passe():
    """Un lien de réinitialisation ne doit servir qu'une fois."""
    print("\n[Mot de passe] Jeton de réinitialisation et sessions")
    from forms import LONGUEUR_MIN_MOT_DE_PASSE

    with app.app_context():
        utilisateur, _ = _utilisateur_temporaire("annonceur")
        utilisateur.password_hash = main.bcrypt.generate_password_hash(
            "MotDePasseInitial1"
        ).decode("utf-8")
        db.session.commit()

        jeton = main.creer_jeton_reset(utilisateur)
        verifier("un jeton fraîchement émis est accepté",
                 main.lire_jeton_reset(jeton) is not None)

        identifiant_session = utilisateur.get_id()
        verifier("l'identifiant de session porte une empreinte",
                 "|" in identifiant_session)

        # Changement de mot de passe : l'empreinte change
        utilisateur.password_hash = main.bcrypt.generate_password_hash(
            "UnAutreMotDePasse2"
        ).decode("utf-8")
        db.session.commit()

        verifier("le jeton ne vaut plus rien après changement du mot de passe",
                 main.lire_jeton_reset(jeton) is None)

        with app.test_request_context("/"):
            charge = app.login_manager._user_callback(identifiant_session)
        verifier("les sessions ouvertes sont invalidées", charge is None)

        with app.test_request_context("/"):
            encore_valide = app.login_manager._user_callback(utilisateur.get_id())
        verifier("une session ouverte après le changement reste valide",
                 encore_valide is not None)

        with app.test_request_context("/"):
            ancien_format = app.login_manager._user_callback(str(utilisateur.id))
        verifier("une session à l'ancien format est refusée", ancien_format is None)

        jeton_bidon = main.creer_jeton_reset(utilisateur) + "x"
        verifier("un jeton falsifié est refusé",
                 main.lire_jeton_reset(jeton_bidon) is None)

        verifier("longueur minimale de mot de passe unifiée à 10",
                 LONGUEUR_MIN_MOT_DE_PASSE == 10)


def test_schema():
    """La table de propriété des fichiers doit exister."""
    print("\n[Schéma]")
    with app.app_context():
        from sqlalchemy import inspect
        tables = inspect(db.engine).get_table_names()
        verifier("table uploaded_files présente", "uploaded_files" in tables)


def main_tests():
    print("=" * 52)
    print("Tests de non-régression — correctifs d'audit PubWek")
    print("=" * 52)

    for test in (
        test_routes_webhooks,
        test_site_accessible_sans_redis,
        test_acces_fichiers,
        test_numeros_whatsapp,
        test_antifraude_clics,
        test_reinitialisation_mot_de_passe,
        test_nettoyage_epargne_les_fichiers_utilises,
        test_schema,
    ):
        try:
            test()
        except Exception as e:
            _resultats["echec"] += 1
            print(f"  ERREUR dans {test.__name__} : {e}")

    nettoyer_jeux_dessai()

    print("\n" + "=" * 52)
    print(f"RÉSULTAT : {_resultats['ok']} réussis, {_resultats['echec']} échoués")
    print("=" * 52)
    return 1 if _resultats["echec"] else 0


if __name__ == "__main__":
    sys.exit(main_tests())

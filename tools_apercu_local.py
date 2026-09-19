# -*- coding: utf-8 -*-
"""Serveur d'apercu local (port 5001) pour la refonte du design.

Demarre Pubwek sur une base SQLite temporaire, remplie de comptes et de
campagnes de demonstration, et expose /_apercu/<role> pour se connecter
d'un clic (partageur, annonceur, admin). Sert uniquement a regarder les
pages migrees : n'affecte ni la base de travail locale ni la production.

    python tools_apercu_local.py
    http://localhost:5001/_apercu/partageur?next=/dashboard/partageur
"""
import os, sys, tempfile, datetime
PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)


def _port():
    """Port d'ecoute : --port <n>, puis PORT, puis 5001 par defaut."""
    if "--port" in sys.argv:
        return int(sys.argv[sys.argv.index("--port") + 1])
    return int(os.environ.get("PORT") or 5001)


PORT = _port()
# Base et dossier de travail distincts par port : deux apercus peuvent tourner
# en parallele sans que l'un verrouille ou efface les donnees de l'autre.
_suffixe = "" if PORT == 5001 else f"_{PORT}"
db = os.path.join(tempfile.gettempdir(), f"pubwek_preview{_suffixe}.db")
if os.environ.get("APERCU_RESET", "1") == "1" and os.path.exists(db):
    os.remove(db)
# Dossier de televersement separe : au demarrage, l'application lance un
# nettoyage des fichiers orphelins. Sans cette isolation, l'apercu supprimerait
# de vrais fichiers du dossier uploads_secure du projet.
APERCU_WORKDIR = os.path.join(tempfile.gettempdir(), f"pubwek_apercu{_suffixe}")
os.makedirs(APERCU_WORKDIR, exist_ok=True)

os.chdir(APERCU_WORKDIR)   # -> UPLOAD_FOLDER = <temp>/uploads_secure

os.environ.update({
    # Cle >= 32 caracteres : en dessous, config.py en tire une au hasard a
    # chaque demarrage, ce qui invalide sessions et liens de reinitialisation.
    "ENV": "development", "SECRET_KEY": "apercu_local_pubwek_cle_de_developpement",
    "DATABASE_URL": "sqlite:///" + db.replace("\\", "/"),
    "ADMIN_EMAIL": "admin@x.com", "ADMIN_PASSWORD": "AdminX123!",
})
import main
from models import db as _db, User, Campaign


app = main.app
with app.app_context():
    _db.create_all()
    if not User.query.filter_by(email="partageur@example.com").first():
        p = User(email="partageur@example.com", role="partageur", pseudo="Testeur",
                 province="Littoral", commune="Cotonou",
                 whatsapp_number="+2290157290905",
                 password_hash=main.bcrypt.generate_password_hash("MotDePasse123!").decode())
        a = User(email="annonceur@example.com", role="annonceur",
                 company_name="Delices Alapkgo",
                 password_hash=main.bcrypt.generate_password_hash("MotDePasse123!").decode())
        p.has_accepted_terms = True
        a.has_accepted_terms = True
        _db.session.add_all([p, a]); _db.session.commit()

        # Quelques campagnes de demonstration ciblant la zone du partageur
        demos = [
            ("Menu Burger + Frites a 3 500 FCFA",
             "Livraison gratuite a Cotonou. Commandez avant 20h.", "A", 1200),
            ("Promo rentree : -30% sur les sacs",
             "Sacs, cahiers et fournitures scolaires.", "B", 800),
            ("Salon de coiffure Elegance",
             "Ouvert 7j/7 de 8h a 20h, quartier Fidjrosse.", "C", 450),
            ("Offre speciale fin d'annee",
             "Visuel refuse par la moderation, a corriger.", "A", 600),
        ]
        # Une campagne par etat d'affichage, pour voir toutes les sections
        etats = [
            {"paid": False, "validated": False, "is_active": False, "status": "pending_review"},
            {"paid": True,  "validated": False, "is_active": False, "status": "pending_review"},
            {"paid": True,  "validated": True,  "is_active": True,  "status": "active"},
            {"paid": True,  "validated": False, "is_active": False, "status": "rejete",
             "admin_status": "rejected", "can_claim_refund": True,
             "rejection_reason": "Le visuel contient un texte illisible sur mobile."},
        ]
        for (detail, desc, opt, cible), etat in zip(demos, etats):
            _db.session.add(Campaign(
                user_id=a.id, promotion_type="produit", promotion_detail=detail,
                description=desc, display_option=opt, media_type="photo",
                provinces="Littoral", communes="Cotonou", duration_days=7,
                target_whatsapp_views=cible, views_per_day=cible // 7,
                total_cost=cible * 25, whatsapp_views=cible // 3,
                views_today=cible // 20, current_day_number=2,
                shared_to_partageurs=True, **etat))
        _db.session.commit()
    # L'admin cree au demarrage n'a pas accepte les CGU : la fenetre modale
    # bloquerait chaque page de l'apercu.
    admin = User.query.filter_by(email=os.environ["ADMIN_EMAIL"]).first()
    if admin and not admin.has_accepted_terms:
        admin.has_accepted_terms = True
        _db.session.commit()

    print("Comptes de demo : partageur@example.com / annonceur@example.com  (MotDePasse123!)")


# --- Raccourci d'apercu : connexion automatique (uniquement sur ce serveur local) ---
from flask import redirect, request
from flask_login import login_user

@app.route("/_apercu/<role>")
def _apercu_connexion(role):
    email = {"partageur": "partageur@example.com",
             "annonceur": "annonceur@example.com",
             "admin": os.environ["ADMIN_EMAIL"]}.get(role)
    u = User.query.filter_by(email=email).first()
    if not u:
        return "compte de demo introuvable : " + str(email), 404
    login_user(u, force=True)
    return redirect(request.args.get("next") or "/")

app.run(port=PORT, debug=False, threaded=True)

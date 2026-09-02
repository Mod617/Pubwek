import os
import re
import io
import hmac
import hashlib
import uuid
import uuid as uuidlib
import random
import logging
import urllib.parse
import threading
import time
import requests
import math
import subprocess
import json
import tempfile
import shutil
from datetime import datetime, UTC, timedelta
from xhtml2pdf import pisa
from io import BytesIO

from flask import (
    Flask, current_app, flash, jsonify, redirect, render_template, 
    request, url_for, abort, send_from_directory, session, make_response
)
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

from benin_communes import DEPARTEMENTS_COMMUNES, toutes_les_communes, commune_appartient_a

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from config import Config
from forms import (
    LoginForm,
    RegisterForm,
    LONGUEUR_MIN_MOT_DE_PASSE,
    MESSAGE_NUMERO_INVALIDE,
    numero_whatsapp_valide,
)
from models import (
    Campaign,
    CampaignClick,
    CampaignShare,
    Notification,
    RefundRequest,
    SystemConfig,
    Transaction,
    UploadedFile,
    CampaignShareProof,
    User,
    UserSubscription,
    WalletTransaction,
    WithdrawalRequest,
    db,
)

from fedapay_client import creer_transaction, generer_lien_paiement, verifier_transaction

import bleach
from PIL import Image as PILImage
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup, escape

# FIX: Protection contre les Decompression Bombs (images très compressées)
PILImage.MAX_IMAGE_PIXELS = 50_000_000

# =========================================================================
# 🔒 LOGGING SÉCURISÉ 
# =========================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# =========================================================================
# L'application Flask est construite par create_app() plus bas dans ce fichier.
#
# Une première instance était créée ici et recevait un @app.after_request ainsi
# qu'une route /render-assets, puis la variable `app` était réaffectée par
# create_app() : tout ce qui était enregistré ici était donc silencieusement
# perdu. Ne rien enregistrer sur `app` avant sa création effective.
# =========================================================================

# =========================================================================
# 🎬 Génération vidéo
# =========================================================================


# Verrou par utilisateur pour empêcher les générations simultanées
video_locks = {}
video_locks_mutex = threading.Lock()


# Bornes du nombre de clics achetables pour une campagne
MIN_CLICS_CAMPAGNE = 100
MAX_CLICS_CAMPAGNE = 1_000_000

# Taille maximale des images (pixels)
MAX_IMAGE_DIMENSION = 8000



# =========================================================================
# ⚙️ Configuration
# =========================================================================

bcrypt = Bcrypt()
csrf = CSRFProtect()
login_manager = LoginManager()

# Les compteurs vont dans Redis quand REDIS_URL est defini : en memoire, ils
# repartent a zero a chaque redemarrage et chaque worker a les siens, ce qui
# rend la limite inoperante des qu'il y a plus d'un processus.
limiter = Limiter(
    get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
    strategy="fixed-window",
    # Si Redis devient indisponible, on bascule sur un comptage en memoire au
    # lieu de laisser remonter l'erreur : sans cela, une panne Redis
    # transformerait chaque page du site en erreur 500.
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)


# =========================================================================
# 🧹 Nettoyage automatique des fichiers temporaires
# =========================================================================

FICHIERS_MAX_AGE_SECONDES = 7 * 24 * 3600  # 7 jours


def fichiers_encore_utilises():
    """Noms de fichiers référencés par un profil ou une campagne.

    Doit être appelé dans un contexte d'application.
    """
    utilises = set()

    for utilisateur in User.query.all():
        for nom in (utilisateur.logo, utilisateur.cover_photo, utilisateur.profile_picture):
            if nom:
                utilises.add(os.path.basename(nom.strip()))

    for campagne in Campaign.query.all():
        for nom in (campagne.media_files or "").split(","):
            if nom.strip():
                utilises.add(os.path.basename(nom.strip()))
        if campagne.generated_video:
            utilises.add(os.path.basename(campagne.generated_video.strip()))

    return utilises


def nettoyer_fichiers_anciens(application, upload_folder, max_age_secondes=FICHIERS_MAX_AGE_SECONDES):
    """Supprime les fichiers anciens ET devenus inutiles.

    L'ancienne version supprimait tout fichier de plus de 7 jours, sans
    vérifier s'il servait encore : les visuels d'une campagne de 30 jours
    disparaissaient donc en pleine diffusion. On épargne désormais tout
    fichier référencé par un profil ou une campagne, quel que soit son âge.
    """
    now = time.time()
    supprimes = 0

    try:
        with application.app_context():
            proteges = fichiers_encore_utilises()

            for nom in os.listdir(upload_folder):
                chemin = os.path.join(upload_folder, nom)
                if not os.path.isfile(chemin):
                    continue
                if nom in proteges:
                    continue
                if now - os.path.getmtime(chemin) <= max_age_secondes:
                    continue

                os.remove(chemin)
                UploadedFile.query.filter_by(filename=nom).delete()
                supprimes += 1
                logger.info("Fichier temporaire supprimé : %s", nom)

            if supprimes:
                db.session.commit()
                logger.info("Nettoyage : %d fichier(s) supprimé(s).", supprimes)
    except Exception as e:
        logger.warning("Erreur nettoyage fichiers : %s", e)

    return supprimes


def lancer_nettoyage_periodique(application, upload_folder, intervalle_secondes=3600):
    """Lance un thread de nettoyage automatique toutes les intervalle_secondes."""
    def _boucle():
        while True:
            time.sleep(intervalle_secondes)
            nettoyer_fichiers_anciens(application, upload_folder)
    t = threading.Thread(target=_boucle, daemon=True)
    t.start()

# =========================================================================
# 🔒 Validation des fichiers uploadés
# =========================================================================

ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.avif', '.bmp', '.tiff', '.gif', '.jfif'}
ALLOWED_IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/avif', 'image/bmp', 'image/tiff', 'image/gif'}

# Taille max globale des uploads : 50 Mo
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


def valider_image(file_storage):
    """Vérifie extension, MIME et contenu réel du fichier image via PIL."""
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return False, "Extension non autorisée."
    mime = file_storage.mimetype or ""
    if mime and mime not in ALLOWED_IMAGE_MIMES:
        return False, "Type MIME non autorisé."
    # Vérification du contenu réel avec PIL
    try:
        file_storage.stream.seek(0)
        with PILImage.open(file_storage.stream) as img:
            img.verify()
        file_storage.stream.seek(0)
    except Exception:
        return False, "Contenu du fichier invalide (non image)."
    return True, None




# =========================================================================
# 🔒 Validation stricte des vidéos uploadées directement par l'annonceur
# =========================================================================

ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm'}
ALLOWED_VIDEO_MIMES = {'video/mp4', 'video/quicktime', 'video/webm'}
MAX_VIDEO_DURATION_SECONDES = 30
MAX_VIDEO_SIZE_MO = 40


def valider_video(file_storage, max_duration=MAX_VIDEO_DURATION_SECONDES, max_size_mo=MAX_VIDEO_SIZE_MO):
    """
    Vérifie une vidéo uploadée en profondeur, PAS seulement par son extension :
      1. Extension et type MIME déclarés (première barrière, facilement falsifiable seule)
      2. Taille du fichier
      3. Analyse RÉELLE du contenu binaire via ffprobe (ffmpeg) : si le fichier n'est
         pas un vrai conteneur vidéo décodable, ffprobe échoue — peu importe son nom
         ou son extension. C'est ce qui bloque un fichier malveillant renommé en .mp4.
      4. Durée réelle du flux vidéo (pas la durée déclarée par le client) ≤ max_duration

    Retourne (ok: bool, message_erreur: str|None, chemin_temporaire: str|None).
    Si ok=True, chemin_temporaire pointe vers un fichier temporaire déjà validé,
    à déplacer par l'appelant vers son emplacement final (ou à supprimer si annulé).
    """
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return False, "Extension vidéo non autorisée (formats acceptés : mp4, mov, webm).", None

    mime = file_storage.mimetype or ""
    if mime and mime not in ALLOWED_VIDEO_MIMES:
        return False, "Type de fichier non reconnu comme vidéo valide.", None

    file_storage.stream.seek(0, 2)
    taille = file_storage.stream.tell()
    file_storage.stream.seek(0)
    max_bytes = max_size_mo * 1024 * 1024

    if taille == 0:
        return False, "Fichier vide.", None
    if taille > max_bytes:
        return False, f"Vidéo trop volumineuse (max {max_size_mo} Mo).", None

    # Écriture dans un fichier temporaire pour analyse réelle du contenu par ffprobe
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            tmp_file.write(file_storage.stream.read())
        file_storage.stream.seek(0)

        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "stream=codec_type",
                    "-show_entries", "format=duration",
                    "-of", "json",
                    tmp_path,
                ],
                capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError:
            os.remove(tmp_path)
            return False, "Analyse vidéo indisponible sur le serveur (ffprobe introuvable).", None
        except subprocess.TimeoutExpired:
            os.remove(tmp_path)
            return False, "Analyse du fichier trop longue — fichier suspect rejeté.", None

        if result.returncode != 0:
            os.remove(tmp_path)
            return False, "Contenu invalide : ce fichier n'est pas une vidéo exploitable.", None

        try:
            info = json.loads(result.stdout)
        except Exception:
            os.remove(tmp_path)
            return False, "Impossible d'analyser le contenu du fichier.", None

        streams = info.get("streams", [])
        if not any(s.get("codec_type") == "video" for s in streams):
            os.remove(tmp_path)
            return False, "Aucun flux vidéo valide détecté dans ce fichier.", None

        duration_str = info.get("format", {}).get("duration")
        try:
            duration_reelle = float(duration_str)
        except (TypeError, ValueError):
            os.remove(tmp_path)
            return False, "Durée de la vidéo introuvable ou fichier corrompu.", None

        if duration_reelle > max_duration + 0.5:  # petite tolérance d'encodage
            os.remove(tmp_path)
            return False, (
                f"La vidéo dépasse {max_duration} secondes "
                f"(durée détectée : {duration_reelle:.1f}s). Raccourcissez-la avant de réessayer."
            ), None

        return True, None, tmp_path

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise



def generer_nom_unique(filename):
    """Génère un nom de fichier unique basé sur UUID pour éviter les collisions."""
    ext = os.path.splitext(secure_filename(filename))[1].lower()
    return f"{uuid.uuid4().hex}{ext}"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # FIX: Limite globale de taille des uploads (50 Mo)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    # Les drapeaux SESSION_COOKIE_SECURE / REMEMBER_COOKIE_SECURE sont définis
    # dans Config et suivent ENV : actifs en production, inactifs en local.

    # L'application tourne derrière un tunnel (Cloudflare / ngrok) ou un reverse
    # proxy. Sans ProxyFix, request.remote_addr renvoie l'adresse du proxy —
    # identique pour tous les visiteurs — ce qui rend la limitation de débit
    # globale (5 échecs bloquent tout le monde) et les IP journalisées inutiles.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # 🆕 Configuration Gmail SMTP pour l'envoi d'emails (mot de passe oublié, etc.)
    app.config.update(
        MAIL_SERVER="smtp.gmail.com",
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
        MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER"),
    )

    db.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "login"

    # =========================================================================
    # 🔒 En-têtes de sécurité HTTP
    #
    # Le niveau suit ENV plutôt qu'un bloc à décommenter à la main : un bloc
    # commenté finit toujours par être oublié le jour du déploiement.
    # =========================================================================
    csp = {
        "default-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "style-src":   ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "script-src":  ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "img-src":     ["'self'", "data:", "blob:", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "media-src":   ["'self'", "blob:", "data:"],
        "font-src":    ["'self'", "data:", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
    }

    Talisman(
        app,
        content_security_policy=csp,
        # Nonce désactivé : la CSP ci-dessus s'appuie sur 'unsafe-inline', que
        # l'ajout d'un nonce annulerait.
        content_security_policy_nonce_in=[],
        force_https=Config.IS_PRODUCTION,
        strict_transport_security=Config.IS_PRODUCTION,
        strict_transport_security_max_age=31536000,
    )

    # FIX: Dossier d'upload hors de static/ pour éviter l'accès public direct
    UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads_secure")
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

    @login_manager.user_loader
    def load_user(identifiant):
        """Recharge l'utilisateur d'une session.

        L'identifiant de session vaut "<id>|<empreinte du mot de passe>" (voir
        User.get_id). Si l'empreinte ne correspond plus, c'est que le mot de
        passe a changé depuis l'ouverture de la session : on refuse, ce qui
        déconnecte partout après une réinitialisation.

        Les sessions au format ancien (identifiant numérique seul) sont
        également refusées : elles ne portent aucune empreinte.
        """
        if not identifiant or "|" not in str(identifiant):
            return None

        brut_id, _, empreinte = str(identifiant).partition("|")
        try:
            user = db.session.get(User, int(brut_id))
        except (TypeError, ValueError):
            return None

        if user is None or empreinte != user.empreinte_session():
            return None
        return user

    return app

app = create_app()


@app.before_request
def memoriser_ip_partageur():
    """Mémorise l'adresse IP de session des partageurs.

    Sert uniquement à repérer le partageur qui clique sur son propre lien de
    tracking (voir evaluer_clic). Limité à ce rôle pour éviter une écriture
    inutile à chaque requête des autres utilisateurs, et l'écriture n'a lieu
    que si l'adresse a réellement changé.
    """
    if not current_user.is_authenticated or current_user.role != "partageur":
        return

    ip = ip_client()
    if ip and current_user.last_seen_ip != ip:
        current_user.last_seen_ip = ip
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


# 🆕 Serializer pour signer/vérifier les tokens de réinitialisation de mot de passe
from itsdangerous import URLSafeTimedSerializer
reset_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

# Durée de validité d'un lien de réinitialisation
DUREE_LIEN_RESET_SECONDES = 3600


def empreinte_mot_de_passe(user):
    """Empreinte du mot de passe actuel (implémentée sur le modèle User)."""
    return user.empreinte_session()


def creer_jeton_reset(user):
    return reset_serializer.dumps(
        {"email": user.email, "e": empreinte_mot_de_passe(user)},
        salt="reset-password-salt",
    )


def lire_jeton_reset(token):
    """Retourne l'utilisateur visé par le jeton, ou None s'il n'est plus valable."""
    try:
        donnees = reset_serializer.loads(
            token, salt="reset-password-salt", max_age=DUREE_LIEN_RESET_SECONDES
        )
    except Exception:
        return None

    # Ancien format (simple chaîne e-mail) : refusé, il ne portait pas
    # d'empreinte et restait donc utilisable après changement du mot de passe.
    if not isinstance(donnees, dict):
        return None

    user = User.query.filter_by(email=donnees.get("email")).first()
    if not user:
        return None

    if donnees.get("e") != empreinte_mot_de_passe(user):
        # Le mot de passe a changé depuis l'émission : le lien est périmé.
        return None

    return user

# URL publique de l'application (tunnel en développement, domaine réel en
# production). Sert à construire les liens de tracking absolus insérés par les
# partageurs dans leur statut WhatsApp.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000")

with app.app_context():
    db.create_all()

with app.app_context():
    # FIX: Les deux variables sont obligatoires — aucune valeur par défaut codée en dur
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        logger.critical("ADMIN_EMAIL et ADMIN_PASSWORD doivent être définis en variables d'environnement.")
        raise RuntimeError("ADMIN_EMAIL and ADMIN_PASSWORD must be set as environment variables.")

    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            email=admin_email,
            password_hash=bcrypt.generate_password_hash(admin_password).decode("utf-8"),
            role="admin",
            is_confirmed=True,
            province="Admin"
        )
        db.session.add(admin)
        db.session.commit()
        logger.info("Administrateur créé.")
    else:
        logger.info("Administrateur déjà existant.")

# L'envoi d'e-mails conditionne la réinitialisation de mot de passe : sans clé
# Resend, un utilisateur qui perd son mot de passe ne peut plus rien faire seul.
if not app.config.get("RESEND_API_KEY"):
    logger.warning(
        "RESEND_API_KEY absente : la réinitialisation de mot de passe est "
        "désactivée. Les mots de passe devront être changés à la main avec "
        "changer_mot_de_passe.py."
    )

# Lancement du nettoyage automatique des fichiers anciens
with app.app_context():
    lancer_nettoyage_periodique(app, app.config["UPLOAD_FOLDER"])

@app.route('/sw.js')
def service_worker():
    return send_from_directory('.', 'sw.js', mimetype='application/javascript')
    
@app.route('/manifest.json')
def manifest():
    return send_from_directory('.', 'manifest.json', mimetype='application/manifest+json')


@app.route("/generer-description-auto", methods=["POST"])
@login_required  # Sécurité : Seul un utilisateur connecté peut appeler cette route
def generer_description_auto():
    data = request.get_json() or {}

    # Nettoyage des entrées avec bleach (que tu as importé en haut de ton fichier)
    promo_type = bleach.clean(
        data.get("promotion_type", "produit")
    ).lower()  # ex: vêtement, restaurant
    promo_detail = bleach.clean(
        data.get("promotion_detail", "")
    ).strip()  # ex: Chaussures de sport
    slogan = bleach.clean(data.get("slogan_video", "")).strip()

    # 🆕 Option choisie par l'annonceur (A, B ou C) — pour adapter longueur et ton
    display_option = bleach.clean(data.get("display_option", "A")).strip().upper()

    if not promo_detail:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Le nom du produit ou service est requis.",
                }
            ),
            400,
        )

    # 1. Banques de mots magiques (utilisées pour A/B ET comme base pour C)
    accroches = [
        "🔥 Alerte pépite !",
        "✨ Craquez pour notre",
        "📢 Ne manquez pas",
        "🎯 Le meilleur",
        "💎 Qualité premium :",
        "🚀 Envie de nouveauté ?",
        "⚡ Offre exclusive :",
        "🌟 Du nouveau chez nous :",
    ]

    appels_action = [
        "📥 Contactez-nous vite !",
        "📲 Infos & Commandes en DM !",
        "🛍️ Commandez le vôtre ici !",
        "📞 Dispo dès maintenant !",
        "👉 Cliquez pour commander !",
        "🔥 Faites vite votre choix !",
    ]

    qualificatifs = [
        "au top",
        "incontournable",
        "100% validé",
        "au meilleur prix",
        "sur-mesure",
        "qui fait la différence",
    ]

    # 2. Génération de suggestions de mots/tags contextuels
    mots_suggeres = ["#innovation", "#qualite", "#disponible"]

    texte_complet = f"{promo_type} {promo_detail} {slogan}".lower()

    if any(
        m in texte_complet
        for m in ["chaussure", "habit", "vetement", "mode", "sac", "robe"]
    ):
        mots_suggeres = ["#StyleDuJour", "#Mode", "#Tendance", "#Shopping"]
    elif any(
        m in texte_complet
        for m in ["manger", "plat", "resto", "cuisine", "gourmand", "burger"]
    ):
        mots_suggeres = ["#Gourmandise", "#Foodie", "#BonAppetit", "#Resto"]
    elif any(
        m in texte_complet
        for m in ["phone", "tel", "tech", "ordi", "pc", "ecouteur", "montre"]
    ):
        mots_suggeres = ["#HighTech", "#Innovation", "#Gadget", "#Indispensable"]
    elif any(
        m in texte_complet
        for m in ["promo", "solde", "reduction", "offert", "gratuit"]
    ):
        mots_suggeres = ["#BonPlan", "#Promo", "#OffreSpeciale", "#Affaire"]

    # =====================================================================
    # ✍️ OPTION C : texte long et persuasif, ton local béninois (≤ 500 car.)
    # =====================================================================
    if display_option == "C":
        accroches_longues = [
            "🔥 Chers clients, on a une pépite pour vous aujourd'hui !",
            "✨ Attention, ceci va vous plaire !",
            "📢 Grande nouvelle pour vous à Cotonou et partout au Bénin !",
            "🎯 On ne présente plus ça, venez découvrir !",
            "💎 Du sérieux, rien que du sérieux, pour vous aujourd'hui !",
        ]

        arguments = [
            f"Notre {promo_type} *{promo_detail}* est fait pour vous simplifier la vie et vous faire gagner en qualité.",
            f"Que vous soyez à Cotonou, Porto-Novo, Parakou ou ailleurs, *{promo_detail}* est disponible pour vous, {random.choice(qualificatifs)}.",
            f"Beaucoup de nos clients sont déjà satisfaits de *{promo_detail}* — c'est du solide, du vrai, pas de blabla.",
            f"Ici, pas de mauvaise surprise : *{promo_detail}* c'est la qualité qu'on vous promet, {random.choice(qualificatifs)}.",
        ]

        phrases_locales = [
            "N'hésitez surtout pas, c'est du sérieux et vous ne serez pas déçu(e) !",
            "Un problème, une question ? On est là pour vous, appelez sans hésiter !",
            "Faites vite, ça part très vite chez nous !",
            "On vous attend avec le sourire, venez comme vous êtes !",
        ]

        appels_action_longs = [
            "📞 Appelez-nous dès maintenant, on répond directement sur WhatsApp !",
            "📲 Un clic, un appel, et c'est réglé — contactez-nous tout de suite !",
            "👉 N'attendez plus, appelez-nous et on s'occupe du reste !",
            "🔥 Faites-nous confiance, appelez-nous dès à présent !",
        ]

        if slogan:
            texte_genere = (
                f"{random.choice(accroches_longues)}\n\n"
                f"{random.choice(arguments)} « {slogan} »\n\n"
                f"{random.choice(phrases_locales)}\n"
                f"{random.choice(appels_action_longs)}"
            )
        else:
            texte_genere = (
                f"{random.choice(accroches_longues)}\n\n"
                f"{random.choice(arguments)}\n\n"
                f"{random.choice(phrases_locales)}\n"
                f"{random.choice(appels_action_longs)}"
            )

        # Sécurité absolue sur la longueur (500 caractères max pour l'option C)
        if len(texte_genere) > 500:
            texte_genere = texte_genere[:497] + "..."

        return jsonify(
            {
                "success": True,
                "description": texte_genere,
                "suggestions": mots_suggeres,
            }
        )

    # =====================================================================
    # 🎬🖼️ OPTIONS A / B : texte court de statut (comportement inchangé)
    # =====================================================================

    # 3. Structures de messages variées
    structures = []

    # Structure 1 : Classique avec slogan
    if slogan:
        structures.append(
            f"{random.choice(accroches)} *{promo_detail}* !\n« {slogan} »\n{random.choice(appels_action)}"
        )

    # Structure 2 : Focus sur le produit/service + qualificatif
    structures.append(
        f"{random.choice(accroches)} {promo_type} *{promo_detail}* ({random.choice(qualificatifs)}). {random.choice(appels_action)}"
    )

    # Structure 3 : Direct et punchy
    structures.append(
        f"⚡ Besoin d'un {promo_type} ? Découvrez *{promo_detail}* ! {random.choice(qualificatifs)}. {random.choice(appels_action)}"
    )

    # Structure 4 : Style Recommandation
    structures.append(
        f"🌟 Testé et approuvé ! Découvrez notre {promo_type} *{promo_detail}*. {random.choice(appels_action)}"
    )

    # Choix aléatoire de la structure
    texte_genere = random.choice(structures)

    # 4. Sécurité absolue sur la longueur (150 caractères max pour WhatsApp)
    if len(texte_genere) > 150:
        texte_genere = texte_genere[:147] + "..."

    # On renvoie la description ET les suggestions de mots à l'interface
    return jsonify(
        {
            "success": True,
            "description": texte_genere,
            "suggestions": mots_suggeres,  # Python envoie les suggestions ici !
        }
    )



# =========================================================================
# 🔒 Propriété des fichiers téléversés et route de distribution
# =========================================================================


def campagne_cible_utilisateur(camp, user):
    """La campagne cible-t-elle la zone géographique de ce partageur ?

    Source unique de vérité pour le ciblage : utilisée à l'affichage du tableau
    de bord, à la confirmation de partage et au contrôle d'accès aux médias.
    Une commune précise l'emporte sur le département ; sans l'un ni l'autre, la
    campagne est ouverte à tous.
    """
    communes_ciblees = [c.strip() for c in camp.communes.split(",") if c.strip()] if camp.communes else []
    provinces_ciblees = (
        [p.strip() for p in camp.provinces.split(",") if p.strip()]
        if camp.provinces and camp.provinces != "Toutes" else []
    )

    if communes_ciblees:
        return user.commune in communes_ciblees
    if provinces_ciblees:
        return user.province in provinces_ciblees
    return True


def enregistrer_upload(filename, owner_id, kind=None):
    """Déclare un fichier comme appartenant à un utilisateur.

    À appeler systématiquement après chaque écriture dans UPLOAD_FOLDER :
    sans cet enregistrement, serve_upload() refusera l'accès au fichier.
    Le commit est laissé à l'appelant, pour rester dans sa transaction.
    """
    return UploadedFile.enregistrer(filename, owner_id, kind=kind)


def _campagnes_utilisant(safe_filename):
    """Campagnes dont le fichier fait partie des médias (recherche large puis exacte)."""
    candidates = Campaign.query.filter(
        db.or_(
            Campaign.media_files.contains(safe_filename),
            Campaign.generated_video == safe_filename,
        )
    ).all()

    resultat = []
    for camp in candidates:
        noms = [n.strip() for n in (camp.media_files or "").split(",") if n.strip()]
        if safe_filename in noms or camp.generated_video == safe_filename:
            resultat.append(camp)
    return resultat


def peut_acceder_au_fichier(user, safe_filename):
    """Détermine si `user` a le droit de télécharger `safe_filename`.

    Règles, de la plus large à la plus restrictive :
      1. L'administrateur accède à tout (modération des campagnes).
      2. Le propriétaire déclaré du fichier y accède.
      3. Un partageur accède aux médias d'une campagne qu'il a acceptée de
         diffuser, ou d'une campagne active qui cible sa zone.
      4. Tout le reste est refusé.
    """
    if user.role == "admin":
        return True

    enreg = UploadedFile.query.filter_by(filename=safe_filename).first()
    if enreg and enreg.owner_id == user.id:
        return True

    if user.role == "partageur":
        for camp in _campagnes_utilisant(safe_filename):
            partage = CampaignShare.query.filter_by(
                campaign_id=camp.id, sharer_id=user.id
            ).first()
            if partage:
                return True
            if camp.validated and camp.shared_to_partageurs and camp.is_active:
                if campagne_cible_utilisateur(camp, user):
                    return True

    return False


@app.route("/uploads/<path:filename>")
@login_required
@limiter.exempt
def serve_upload(filename):
    """Sert un fichier téléversé, après vérification du droit d'accès.
    Auparavant, tout utilisateur connecté pouvait télécharger n'importe quel
    fichier du dossier : il suffisait d'en connaître le nom. La propriété est
    désormais tracée par le modèle UploadedFile.
    """
    safe_filename = os.path.basename(filename)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, safe_filename)
    if not os.path.exists(filepath):
        abort(404)
    if not peut_acceder_au_fichier(current_user, safe_filename):
        logger.warning(
            "[SECURITE] Accès refusé au fichier %s pour l'utilisateur id=%s (role=%s)",
            safe_filename, current_user.id, current_user.role
        )
        # 404 plutôt que 403 : ne pas confirmer l'existence du fichier.
        abort(404)
    return send_from_directory(upload_folder, safe_filename)



# 🛣️ ROUTES

























# ==========================================
# ROUTE : NOUVELLE CAMPAGNE (CRÉATION)
# ==========================================
@app.route("/dashboard/annonceur/nouvelle_campagne", methods=["GET", "POST"])
@login_required
@limiter.limit("20 per hour")
def nouvelle_campagne():
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    from models import SystemConfig  # Importation de la configuration administrative
    config = SystemConfig.get_config()

    if request.method == "POST":
        display_option = request.form.get("display_option", "A")
        promotion_type = request.form.get("promotion_type")
        promotion_detail = request.form.get("promotion_detail", "")
        description = request.form.get("description", "")

        if description and len(description) > 500:
            flash("La description ne peut pas dépasser 500 caractères ⚠️", "warning")
            return redirect(url_for("dashboard_annonceur"))

        # Nettoyage XSS des champs texte libres
        description = bleach.clean(description)
        promotion_detail = bleach.clean(promotion_detail)

        provinces_list = request.form.getlist("provinces[]")

        # 1️⃣ VALIDATION DES CHAMPS NUMÉRIQUES
        # Un int() nu sur une saisie libre lève ValueError (erreur 500) sur toute
        # valeur non numérique, et accepte les nombres négatifs — ce qui produit
        # un coût négatif, refusé plus tard au paiement : la campagne devenait
        # définitivement impayable.
        try:
            target_views = int(request.form.get("whatsapp_views", 0))
            duration_days = int(request.form.get("duration_days", 7))
        except (TypeError, ValueError):
            flash("Le nombre de clics et la durée doivent être des nombres entiers. ⚠️", "danger")
            return redirect(url_for("dashboard_annonceur"))

        if target_views < MIN_CLICS_CAMPAGNE or target_views > MAX_CLICS_CAMPAGNE:
            flash(
                f"Le nombre de clics doit être compris entre {MIN_CLICS_CAMPAGNE} "
                f"et {MAX_CLICS_CAMPAGNE:,}. ⚠️".replace(",", " "),
                "danger"
            )
            return redirect(url_for("dashboard_annonceur"))

        if duration_days < 1 or duration_days > 30:
            flash("La durée de diffusion doit être comprise entre 1 et 30 jours maximum. ⚠️", "danger")
            return redirect(url_for("dashboard_annonceur"))

        # =========================================================================
        # 📞 === RECONSTRUCTION DU NUMÉRO WHATSAPP COMPLET ===
        # Le champ whatsapp_number du formulaire de campagne ne contient plus que
        # les 8 chiffres saisis par l'annonceur (le préfixe +22901 n'est plus
        # tapé). On reconstruit ici le numéro complet avant toute validation.
        # =========================================================================
        whatsapp_number = request.form.get("whatsapp_number")
        if whatsapp_number:
            whatsapp_number = "+22901" + whatsapp_number.strip()

        # Même règle que le formulaire d'inscription (forms.NUMERO_WHATSAPP_REGEX)
        if whatsapp_number and not numero_whatsapp_valide(whatsapp_number):
            flash(MESSAGE_NUMERO_INVALIDE, "danger")
            return redirect(url_for("dashboard_annonceur"))

        # --- Champ optionnel : site web / application web de la structure ---
        website_url = request.form.get("website_url", "").strip()
        if website_url:
            if not re.match(r"^https?://[^\s]+\.[^\s]{2,}$", website_url):
                flash("Le lien du site web semble invalide. Utilisez un format complet (ex: https://monsite.com).", "danger")
                return redirect(url_for("dashboard_annonceur"))
            website_url = bleach.clean(website_url)

        # =====================================================================
        # 🆕 VALIDATION DES COMMUNES (raffinement optionnel du ciblage)
        # =====================================================================
        # Sécurité : on n'accepte une commune QUE si son département a été coché.
        # Sans ça, quelqu'un pourrait manipuler le HTML et injecter une commune
        # d'un département non sélectionné.
        communes_recues = request.form.getlist("communes[]")
        communes_valides = []
        for commune in communes_recues:
            commune = bleach.clean(commune.strip())
            departement_trouve = None
            for dept, liste_communes in DEPARTEMENTS_COMMUNES.items():
                if commune in liste_communes:
                    departement_trouve = dept
                    break
            if departement_trouve and departement_trouve in provinces_list:
                communes_valides.append(commune)
            else:
                logger.warning(
                    "Commune rejetée (user %s) : '%s' invalide ou département non sélectionné",
                    current_user.id, commune
                )

        video_generee_nom = request.form.get("generated_video_name")
        cached_media = request.form.get("cached_media_files")

        # Sécurisation des fichiers déjà enregistrés en cache/serveur
        noms_fichiers = [os.path.basename(f.strip()) for f in cached_media.split(",") if f.strip()] if cached_media else []

        medias_pour_db = ""

        # =====================================================================
        # 🎬 OPTION A : Upload direct d'une vidéo déjà montée par l'annonceur
        # =====================================================================
        if display_option == "A":
            media_type = "video"

            video_file = request.files.get("video_file")
            if not video_file or not video_file.filename:
                flash("Veuillez téléverser une vidéo pour l'option A (durée maximale : 30 secondes). ⚠️", "danger")
                return redirect(url_for("dashboard_annonceur"))

            ok, err, tmp_path = valider_video(video_file)
            if not ok:
                logger.warning("Upload vidéo rejeté (user %s) : %s", current_user.id, err)
                flash(f"Vidéo refusée : {err}", "danger")
                return redirect(url_for("dashboard_annonceur"))

            video_generee_nom = f"video_{current_user.id}_{uuid.uuid4().hex}.mp4"
            output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], video_generee_nom)
            shutil.move(tmp_path, output_path)
            enregistrer_upload(video_generee_nom, current_user.id, kind="video")

            medias_pour_db = video_generee_nom
            cout_par_clic_base = config.cost_per_click_video

        # =====================================================================
        # ✍️ OPTION C : Texte seul, aucun fichier requis
        # =====================================================================
        elif display_option == "C":
            media_type = "texte"

            if not description or not description.strip():
                flash("Veuillez rédiger le texte de votre publicité pour l'option C. ⚠️", "danger")
                return redirect(url_for("dashboard_annonceur"))

            medias_pour_db = ""
            cout_par_clic_base = config.cost_per_click_text

        # =====================================================================
        # 🖼️ OPTION B : Plusieurs photos (comportement inchangé)
        # =====================================================================
        elif display_option == "B":
            media_type = "photo"
            fichiers_recus = [f for f in request.files.getlist("media_files") if f and f.filename]

            if not noms_fichiers and fichiers_recus:
                if len(fichiers_recus) > 25:
                    flash("Trop de fichiers. Maximum autorisé : 25.", "danger")
                    return redirect(url_for("dashboard_annonceur"))

                for fichier in fichiers_recus:
                    ok, err = valider_image(fichier)
                    if not ok:
                        logger.warning("Upload image rejeté (user %s) : %s", current_user.id, err)
                        continue
                    filename = generer_nom_unique(fichier.filename)
                    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
                    fichier.save(path)
                    enregistrer_upload(filename, current_user.id, kind="image")
                    if filename not in noms_fichiers:
                        noms_fichiers.append(filename)

            if len(noms_fichiers) > 25:
                flash("Trop de fichiers. Maximum autorisé : 25.", "danger")
                return redirect(url_for("dashboard_annonceur"))

            if not noms_fichiers:
                flash("Veuillez téléverser au moins une photo pour l'option B. ⚠️", "danger")
                return redirect(url_for("dashboard_annonceur"))

            medias_pour_db = ",".join(noms_fichiers)
            nombre_fichiers = len(noms_fichiers)
            cout_par_clic_base = config.cost_per_click_photo * nombre_fichiers

        # =====================================================================
        # ⚠️ Sécurité : toute autre valeur est rejetée explicitement
        # =====================================================================
        else:
            flash("Option de diffusion invalide. ⚠️", "danger")
            return redirect(url_for("dashboard_annonceur"))

        # 2️⃣ CALCULS DES COÛTS ET DES TARIFS
        base_clicks_cost = target_views * cout_par_clic_base
        commission_percentage = config.commission_rate / 100.0
        total_commission = base_clicks_cost * commission_percentage
        total_cost = round(base_clicks_cost + total_commission, 2)

        views_per_day = int(target_views / duration_days) if duration_days > 0 else target_views

        calculated_end_date = datetime.utcnow() + timedelta(days=duration_days)

        # 3️⃣ ENREGISTREMENT EN BASE DE DONNÉES
        new_campaign = Campaign(
            user_id=current_user.id,
            promotion_type=promotion_type or "Non spécifié",
            promotion_detail=promotion_detail or "",
            description=description,
            display_option=display_option,
            media_type=media_type,
            media_files=medias_pour_db,
            generated_video=video_generee_nom if display_option == "A" else None,
            website_url=website_url or None,
            provinces=",".join(provinces_list) if provinces_list else "Toutes",
            communes=",".join(communes_valides) if communes_valides else None,
            target_whatsapp_views=target_views,
            duration_days=duration_days,
            end_date=calculated_end_date,
            whatsapp_views=0,
            views_per_day=views_per_day,
            total_cost=total_cost,
            whatsapp_number=whatsapp_number or "",

            # --- MISES À JOUR DU WORKFLOW ET DES STATUTS ---
            status="non_payee",
            payment_status="unpaid",
            admin_status="pending_review",
            can_claim_refund=False,
            validated=False,
            paid=False,
            is_active=False
        )

        db.session.add(new_campaign)
        db.session.commit()

        session.pop('preview_video_url', None)

        flash(
            f"Campagne enregistrée ! Coût : {total_cost:.0f} FCFA. Veuillez procéder au paiement pour finaliser l'envoi. 💳",
            "info"
        )
        return redirect(url_for("payer_campagne", campaign_id=new_campaign.id))

    return redirect(url_for("dashboard_annonceur"))



# ==========================================
# ROUTE : MES CAMPAGNES (ESPACE ANNONCEUR)
# ==========================================
@app.route("/mes-campagnes")
@login_required
def mes_campagnes():
    if current_user.role != "annonceur":
        flash("Accès réservé aux annonceurs. 🚫", "danger")
        return redirect(url_for("index"))

    # Récupération de toutes les campagnes de l'utilisateur connecté
    user_campaigns = Campaign.query.filter_by(user_id=current_user.id).order_by(Campaign.created_at.desc()).all()

    # Tri par catégories pour l'affichage dans l'interface HTML
    refusees = [c for c in user_campaigns if c.status == "rejete" or c.admin_status == "rejected"]
    non_payees = [c for c in user_campaigns if (not c.paid or c.status == "non_payee") and c.status != "rejete"]
    en_attente = [c for c in user_campaigns if c.paid and (c.status == "en_attente" or not c.validated) and c.status != "rejete"]
    en_cours = [c for c in user_campaigns if c.paid and c.validated and (c.status == "active" or c.status == "valide") and c.is_active]
    terminees = [c for c in user_campaigns if c.status == "terminee"]

    return render_template(
        "mes_campagnes.html",
        refusees=refusees,
        non_payees=non_payees,
        en_attente=en_attente,
        en_cours=en_cours,
        terminees=terminees,
        departements_communes=DEPARTEMENTS_COMMUNES
    )


# ==========================================
# ROUTE : PARTAGEURS D'UNE CAMPAGNE (ESPACE ANNONCEUR)
# ==========================================
@app.route("/mes-campagnes/<int:campaign_id>/partageurs")
@login_required
def campagne_partageurs(campaign_id):
    if current_user.role != "annonceur":
        flash("Accès réservé aux annonceurs. 🚫", "danger")
        return redirect(url_for("index"))

    camp = db.session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # Sécurité supplémentaire : on ne montre les partageurs
    # que sur une campagne payée et validée par l'admin
    if not camp.paid or not camp.validated:
        flash("Les statistiques de partage ne sont disponibles qu'une fois la campagne validée. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    # 1️⃣ Liste des partageurs de cette campagne (pseudo + date + id du CampaignShare)
    shares = (
        db.session.query(
            CampaignShare.id,
            CampaignShare.sharer_id,
            CampaignShare.created_at,
            User.pseudo
        )
        .join(User, User.id == CampaignShare.sharer_id)
        .filter(CampaignShare.campaign_id == campaign_id)
        .all()
    )

    if not shares:
        return render_template(
            "campagne_partageurs.html",
            campaign=camp,
            partageurs=[],
            total_clics=0,
            total_clics_whatsapp=0,
            total_clics_site=0
        )

    share_ids = [s.id for s in shares]

    # 2️⃣ Comptage des clics, par CampaignShare et par type de lien.
    #
    # Il y avait ici un second comptage, celui des « vues », lu dans la table
    # View. Cette table n'est alimentée nulle part : la colonne affichait donc
    # invariablement zéro. Elle est retirée — la campagne se mesure en clics.
    clics_bruts = (
        db.session.query(
            CampaignClick.campaign_share_id,
            CampaignClick.link_type,
            func.count(CampaignClick.id)
        )
        .filter(CampaignClick.campaign_share_id.in_(share_ids))
        .group_by(CampaignClick.campaign_share_id, CampaignClick.link_type)
        .all()
    )

    clics_par_share = {}
    for share_id, link_type, nb in clics_bruts:
        clics_par_share.setdefault(share_id, {"whatsapp": 0, "website": 0})
        clics_par_share[share_id][link_type] = nb

    # 3️⃣ Construction de la liste exploitable par le gabarit
    partageurs = []
    for s in shares:
        clics = clics_par_share.get(s.id, {"whatsapp": 0, "website": 0})
        partageurs.append({
            "pseudo": s.pseudo or "Partageur anonyme",
            "clics_whatsapp": clics["whatsapp"],
            "clics_site": clics["website"],
            "total_clics": clics["whatsapp"] + clics["website"],
            "partage_le": s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else None,
        })

    # Les partageurs les plus efficaces d'abord
    partageurs.sort(key=lambda p: p["total_clics"], reverse=True)

    total_clics_whatsapp = sum(p["clics_whatsapp"] for p in partageurs)
    total_clics_site = sum(p["clics_site"] for p in partageurs)

    return render_template(
        "campagne_partageurs.html",
        campaign=camp,
        partageurs=partageurs,
        total_clics=total_clics_whatsapp + total_clics_site,
        total_clics_whatsapp=total_clics_whatsapp,
        total_clics_site=total_clics_site
    )



# ==========================================
# ROUTE : CAMPAGNES EN ATTENTE DE VALIDATION
# ==========================================
@app.route("/campagnes/en-attente")
@login_required
def campagne_en_attente():
    if current_user.role != "annonceur":
        flash("Accès réservé aux annonceurs. 🚫", "danger")
        return redirect(url_for("index"))

    # Récupère les campagnes payées mais non encore validées par l'admin
    campagnes = Campaign.query.filter_by(user_id=current_user.id).filter(
        (Campaign.status == "en_attente") | (Campaign.validated == False)
    ).order_by(Campaign.created_at.desc()).all()

    return render_template("campagne_en_attente.html", campagnes=campagnes)




# ==========================================
# ROUTE : PAYER / RELANCER PAIEMENT CAMPAGNE
# ==========================================
@app.route("/dashboard/annonceur/campagne/<int:campaign_id>/payer", methods=["GET"])
@login_required
def payer_campagne(campaign_id):
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    camp = db.session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # Vérification du statut de paiement
    if camp.paid or camp.payment_status == "paid" or camp.status == "active":
        flash("Cette campagne est déjà payée et traitée. ✅", "info")
        return redirect(url_for("mes_campagnes"))

    # Vérification du montant avant d'initier la transaction FedaPay
    if not camp.total_cost or camp.total_cost <= 0:
        flash("Montant de la campagne invalide. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # Réutilise une transaction 'pending' existante si elle existe déjà pour cette campagne
    existing = (
        Transaction.query.filter_by(
            campaign_id=camp.id,
            transaction_type="campaign_payment",
            status="pending"
        )
        .order_by(Transaction.created_at.desc())
        .first()
    )

    if existing and existing.fedapay_transaction_id:
        try:
            lien = generer_lien_paiement(existing.fedapay_transaction_id)
            if lien:
                return redirect(lien)
        except Exception as e:
            logger.warning("Réutilisation transaction impossible, création d'une nouvelle : %s", e)

    reference = f"CAMP-{camp.id}-{uuid.uuid4().hex[:10]}"

    try:
        fedapay_tx = creer_transaction(
            montant=camp.total_cost,
            description=f"Paiement campagne #{camp.id} - {camp.promotion_detail or camp.promotion_type}",
            metadata={
                "type": "campaign_payment",
                "campaign_id": str(camp.id),
                "user_id": str(current_user.id),
                "reference": reference,
            },
            customer_email=current_user.email,
            customer_phone=current_user.whatsapp_number,
        )

        # Extraction sécurisée de l'ID FedaPay
        if isinstance(fedapay_tx, dict):
            tx_id = fedapay_tx.get("id")
        elif hasattr(fedapay_tx, "id"):
            tx_id = fedapay_tx.id
        else:
            tx_id = fedapay_tx

        if not tx_id:
            raise ValueError("ID de transaction FedaPay introuvable dans la réponse.")

        lien_paiement = generer_lien_paiement(tx_id)

        if not lien_paiement:
            raise ValueError("Impossible de générer le lien de paiement.")

    except Exception as e:
        logger.error("Erreur création paiement FedaPay (campagne %d) : %s", camp.id, e)
        flash("Impossible de générer le paiement pour le moment. Réessayez. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # Enregistrement de la nouvelle transaction en attente
    transaction = Transaction(
        user_id=current_user.id,
        campaign_id=camp.id,
        reference=reference,
        fedapay_transaction_id=str(tx_id),
        amount=camp.total_cost,
        currency="XOF",
        transaction_type="campaign_payment",
        status="pending",
    )
    
    db.session.add(transaction)
    db.session.commit()

    return redirect(lien_paiement)


@app.route("/annonceur/campaign/<int:campaign_id>/resoumettre", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def resoumettre_campagne(campaign_id):
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    camp = db.session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    if camp.admin_status != "rejected" and camp.status != "rejete":
        flash("Seule une campagne rejetée peut être corrigée et resoumise. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    from models import SystemConfig
    config = SystemConfig.get_config()

    # 1️⃣ Champs texte de base
    promotion_detail = request.form.get("promotion_detail", "").strip()
    description = request.form.get("description", "").strip()

    if not promotion_detail or not description:
        flash("Veuillez remplir tous les champs obligatoires. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    max_len = 500 if camp.display_option == "C" else 150
    if len(description) > max_len:
        flash(f"La description ne peut pas dépasser {max_len} caractères pour cette option. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    camp.promotion_detail = bleach.clean(promotion_detail)
    camp.description = bleach.clean(description)

    # 2️⃣ Zones de diffusion
    provinces_list = request.form.getlist("provinces[]")
    communes_list = request.form.getlist("communes[]")
    if provinces_list:
        camp.provinces = ",".join(provinces_list)
    camp.communes = ",".join(communes_list) if communes_list else None

    # 3️⃣ Objectif de clics / durée
    target_views_raw = request.form.get("whatsapp_views")
    duration_days_raw = request.form.get("duration_days")

    if target_views_raw:
        try:
            camp.target_whatsapp_views = int(target_views_raw)
        except ValueError:
            flash("Objectif de clics invalide. ⚠️", "danger")
            return redirect(url_for("mes_campagnes"))

    if duration_days_raw:
        try:
            duration_days = int(duration_days_raw)
        except ValueError:
            flash("Durée invalide. ⚠️", "danger")
            return redirect(url_for("mes_campagnes"))
        if duration_days < 1 or duration_days > 30:
            flash("La durée de diffusion doit être comprise entre 1 et 30 jours maximum. ⚠️", "danger")
            return redirect(url_for("mes_campagnes"))
        camp.duration_days = duration_days

    # 4️⃣ Numéro WhatsApp / site web
    whatsapp_number = request.form.get("whatsapp_number", "").strip()
    if whatsapp_number:
        if not re.match(r"^\+?[0-9]{7,15}$", whatsapp_number):
            flash("Numéro WhatsApp invalide. Utilisez un format international (ex: +22960000000).", "danger")
            return redirect(url_for("mes_campagnes"))
        camp.whatsapp_number = whatsapp_number

    website_url = request.form.get("website_url", "").strip()
    if website_url:
        if not re.match(r"^https?://[^\s]+\.[^\s]{2,}$", website_url):
            flash("Le lien du site web semble invalide. Utilisez un format complet (ex: https://monsite.com).", "danger")
            return redirect(url_for("mes_campagnes"))
        camp.website_url = bleach.clean(website_url)
    elif website_url == "":
        camp.website_url = None

    # 5️⃣ Médias — remplacement optionnel, l'option A/B/C reste figée (celle d'origine de la campagne)
    if camp.display_option == "A":
        video_file = request.files.get("video_file")
        if video_file and video_file.filename:
            ok, err, tmp_path = valider_video(video_file)
            if not ok:
                logger.warning("Upload vidéo rejeté (user %s) : %s", current_user.id, err)
                flash(f"Vidéo refusée : {err}", "danger")
                return redirect(url_for("mes_campagnes"))
            video_generee_nom = f"video_{current_user.id}_{uuid.uuid4().hex}.mp4"
            output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], video_generee_nom)
            shutil.move(tmp_path, output_path)
            camp.media_files = video_generee_nom
            camp.generated_video = video_generee_nom
        cout_par_clic_base = config.cost_per_click_video

    elif camp.display_option == "B":
        fichiers_recus = [f for f in request.files.getlist("media_files") if f and f.filename]
        if fichiers_recus:
            if len(fichiers_recus) > 25:
                flash("Trop de fichiers. Maximum autorisé : 25.", "danger")
                return redirect(url_for("mes_campagnes"))
            noms_fichiers = []
            for fichier in fichiers_recus:
                ok, err = valider_image(fichier)
                if not ok:
                    logger.warning("Upload image rejeté (user %s) : %s", current_user.id, err)
                    continue
                filename = generer_nom_unique(fichier.filename)
                path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
                fichier.save(path)
                noms_fichiers.append(filename)
            if not noms_fichiers:
                flash("Aucune photo valide n'a pu être enregistrée. ⚠️", "danger")
                return redirect(url_for("mes_campagnes"))
            camp.media_files = ",".join(noms_fichiers)
            nombre_fichiers = len(noms_fichiers)
        else:
            nombre_fichiers = len(camp.media_files.split(",")) if camp.media_files else 1
        cout_par_clic_base = config.cost_per_click_photo * nombre_fichiers

    else:  # Option C : texte seul, aucun média
        cout_par_clic_base = config.cost_per_click_text

    # 6️⃣ Recalcul du coût total (les paramètres ont pu changer)
    base_clicks_cost = camp.target_whatsapp_views * cout_par_clic_base
    commission_percentage = config.commission_rate / 100.0
    total_commission = base_clicks_cost * commission_percentage
    camp.total_cost = round(base_clicks_cost + total_commission, 2)
    camp.views_per_day = int(camp.target_whatsapp_views / camp.duration_days) if camp.duration_days > 0 else camp.target_whatsapp_views
    camp.end_date = datetime.utcnow() + timedelta(days=camp.duration_days)

    # 🆕 6bis️⃣ Réinitialisation complète du quota journalier
    # Les paramètres (objectif de clics, durée) ont pu changer : on repart sur une diffusion fraîche.
    # whatsapp_views (compteur global) n'est PAS remis à zéro s'il y avait déjà des clics comptés
    # avant le rejet, pour ne pas perdre les clics déjà livrés et payés par l'annonceur.
    camp.views_today = 0
    camp.current_day_number = 0
    camp.last_quota_date = None
    camp.daily_quota_paused = False
    camp.daily_quota_alert_sent = False

    # 7️⃣ Réharmonisation des statuts
    camp.rejection_reason = None
    camp.can_claim_refund = False
    camp.is_active = False
    camp.admin_status = "pending_review"

    # 🐞 FIX : le paiement réel (camp.paid) décide seul du prochain statut — jamais une supposition
    if camp.paid:
        # Déjà payée avant son rejet (refusée pour un motif de contenu) : repart direct en file d'attente admin
        camp.status = "en_attente"
        db.session.commit()

        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                title="Campagne corrigée 🔄",
                message=f"L'annonceur a soumis les corrections pour la campagne #{camp.id}.",
                category="warning",
                link=url_for("admin_validate"),
                is_read=False
            )
            db.session.add(notif)
        db.session.commit()

        flash("Vos corrections ont été enregistrées. La campagne a été renvoyée à l'administration pour validation. 🚀", "success")
        redirect_target = url_for("mes_campagnes")
    else:
        # Jamais payée : doit repasser par le paiement AVANT de revenir dans la file d'attente admin
        camp.status = "non_payee"
        camp.payment_status = "unpaid"
        db.session.commit()

        flash("Vos corrections ont été enregistrées. Veuillez maintenant procéder au paiement pour envoyer votre campagne à l'administration. 💳", "info")
        redirect_target = url_for("payer_campagne", campaign_id=camp.id)

    logger.info(
        "[ACTION ANNONCEUR] Campagne #%d corrigée et resoumise par l'utilisateur id=%d (déjà payée=%s)",
        campaign_id, current_user.id, camp.paid
    )

    return redirect(redirect_target)

# ==========================================
# 🆕 ROUTE ADMIN : VUE GLOBALE DES TRANSACTIONS (paiements + retraits)
# ==========================================
@app.route("/admin/transactions")
@login_required
def admin_transactions_globales():
    verifier_droits_admin("voir_transactions")

    from models import WithdrawalRequest

    # =========================================================================
    # 1️⃣ FILTRES — PAIEMENTS DES ANNONCEURS
    # =========================================================================
    filtre_statut_paiement = request.args.get("statut_paiement", "").strip()
    recherche_paiement = request.args.get("recherche_paiement", "").strip()

    query_transactions = Transaction.query

    if filtre_statut_paiement:
        query_transactions = query_transactions.filter(Transaction.status == filtre_statut_paiement)

    if recherche_paiement:
        terme = f"%{recherche_paiement}%"
        query_transactions = query_transactions.join(User, User.id == Transaction.user_id).filter(
            db.or_(
                Transaction.reference.ilike(terme),
                Transaction.fedapay_transaction_id.ilike(terme),
                User.email.ilike(terme)
            )
        )

    transactions = query_transactions.order_by(Transaction.created_at.desc()).limit(200).all()

    # Association manuelle de l'utilisateur pour l'affichage
    for t in transactions:
        t.demandeur = db.session.get(User, t.user_id)

    total_paiements_approuves = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0.0))
        .filter(Transaction.status == "approved")
        .scalar()
    )

    # =========================================================================
    # 2️⃣ FILTRES — RETRAITS DES PARTAGEURS
    # =========================================================================
    filtre_statut_retrait = request.args.get("statut_retrait", "").strip()
    recherche_retrait = request.args.get("recherche_retrait", "").strip()

    query_retraits = WithdrawalRequest.query

    if filtre_statut_retrait:
        query_retraits = query_retraits.filter(WithdrawalRequest.status == filtre_statut_retrait)

    if recherche_retrait:
        terme = f"%{recherche_retrait}%"
        query_retraits = query_retraits.join(User, User.id == WithdrawalRequest.user_id).filter(
            db.or_(
                WithdrawalRequest.payout_phone.ilike(terme),
                User.email.ilike(terme),
                User.pseudo.ilike(terme)
            )
        )

    retraits = query_retraits.order_by(WithdrawalRequest.requested_at.desc()).limit(200).all()

    for d in retraits:
        d.demandeur = db.session.get(User, d.user_id)

    total_retraits_payes = (
        db.session.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0.0))
        .filter(WithdrawalRequest.status == "paid")
        .scalar()
    )

    return render_template(
        "admin_transactions_globales.html",
        transactions=transactions,
        retraits=retraits,
        total_paiements_approuves=total_paiements_approuves,
        total_retraits_payes=total_retraits_payes,
        filtre_statut_paiement=filtre_statut_paiement,
        recherche_paiement=recherche_paiement,
        filtre_statut_retrait=filtre_statut_retrait,
        recherche_retrait=recherche_retrait
    )

# ==========================================
# 🆕 ROUTE : MES TRANSACTIONS (ESPACE ANNONCEUR)
# ==========================================
@app.route("/annonceur/mes-transactions")
@login_required
def mes_transactions():
    if current_user.role != "annonceur":
        flash("Accès réservé aux annonceurs. 🚫", "danger")
        return redirect(url_for("index"))

    transactions = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(Transaction.created_at.desc())
        .all()
    )

    total_paye = sum(t.amount for t in transactions if t.status == "approved")

    return render_template(
        "mes_transactions.html",
        transactions=transactions,
        total_paye=total_paye
    )   


# ==========================================
# 🆕 UTILITAIRE : GÉNÉRATION PDF À PARTIR D'UN TEMPLATE HTML
# ==========================================


def generer_pdf_depuis_template(template_name, contexte, nom_fichier):
    """
    Génère un PDF à partir d'un template Jinja2 et le retourne en téléchargement.
    """
    html_rendu = render_template(template_name, **contexte)

    buffer = BytesIO()
    resultat = pisa.CreatePDF(html_rendu, dest=buffer, encoding="utf-8")

    if resultat.err:
        logger.error("Erreur génération PDF (%s) : %d erreur(s)", nom_fichier, resultat.err)
        raise ValueError("Erreur lors de la génération du PDF.")

    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={nom_fichier}"
    return response


# ==========================================
# 🆕 ROUTE : EXPORT PDF — MES RETRAITS (PARTAGEUR)
# ==========================================
@app.route("/partageur/mes-retraits/pdf")
@login_required
def mes_retraits_pdf():
    if current_user.role != "partageur":
        flash("Accès réservé aux partageurs. 🚫", "danger")
        return redirect(url_for("index"))

    from models import WithdrawalRequest, WalletTransaction

    demandes = (
        WithdrawalRequest.query
        .filter_by(user_id=current_user.id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .all()
    )
    mouvements = (
        WalletTransaction.query
        .filter_by(user_id=current_user.id)
        .order_by(WalletTransaction.created_at.desc())
        .all()
    )

    try:
        return generer_pdf_depuis_template(
            "mes_retraits_pdf.html",
            {
                "user": current_user,
                "demandes": demandes,
                "mouvements": mouvements,
                "solde_actuel": current_user.wallet_balance or 0.0,
                "date_generation": datetime.utcnow().strftime("%d/%m/%Y à %H:%M"),
            },
            f"pubwek_retraits_{current_user.id}.pdf"
        )
    except ValueError:
        flash("Impossible de générer le PDF pour le moment. Réessayez. ⚠️", "danger")
        return redirect(url_for("mes_retraits"))


# ==========================================
# 🆕 ROUTE : EXPORT PDF — MES TRANSACTIONS (ANNONCEUR)
# ==========================================
@app.route("/annonceur/mes-transactions/pdf")
@login_required
def mes_transactions_pdf():
    if current_user.role != "annonceur":
        flash("Accès réservé aux annonceurs. 🚫", "danger")
        return redirect(url_for("index"))

    transactions = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(Transaction.created_at.desc())
        .all()
    )
    total_paye = sum(t.amount for t in transactions if t.status == "approved")

    try:
        return generer_pdf_depuis_template(
            "mes_transactions_pdf.html",
            {
                "user": current_user,
                "transactions": transactions,
                "total_paye": total_paye,
                "date_generation": datetime.utcnow().strftime("%d/%m/%Y à %H:%M"),
            },
            f"pubwek_transactions_{current_user.id}.pdf"
        )
    except ValueError:
        flash("Impossible de générer le PDF pour le moment. Réessayez. ⚠️", "danger")
        return redirect(url_for("mes_transactions"))


@app.route('/accepter-cgu', methods=['POST'])
@login_required
def accepter_cgu():
    current_user.has_accepted_terms = True
    db.session.commit()
    return jsonify({'success': True})    


# =========================================================================
# 💳 APPLICATION D'UN PAIEMENT FEDAPAY
#
# Deux chemins mènent ici : le retour navigateur (paiement_callback) et le
# webhook FedaPay. Les deux appellent la même fonction, qui est idempotente :
# le premier arrivé applique les effets, le second ne fait rien.
# =========================================================================


def _statut_fedapay(details):
    """Extrait le statut quelle que soit la forme de la réponse FedaPay."""
    if isinstance(details, dict):
        return details.get("status")
    return getattr(details, "status", None)






def appliquer_paiement_confirme(transaction, details=None):
    """Applique les effets métier d'un paiement approuvé. Idempotent.

    Retourne l'un de : "deja_traite", "campagne", "abonnement", "autre".
    Ne fait aucun commit : l'appelant décide du moment.
    """
    if transaction.status == "approved" and transaction.verified_at:
        return "deja_traite"

    transaction.status = "approved"
    transaction.verified_at = datetime.utcnow()
    if isinstance(details, dict):
        transaction.raw_response = details

    if transaction.campaign_id:
        camp = db.session.get(Campaign, transaction.campaign_id)
        if camp:
            camp.paid = True
            camp.payment_status = "paid"

            # Si l'admin avait déjà validé la campagne avant paiement
            if camp.admin_status == "approved" or camp.validated:
                camp.is_active = True
                camp.status = "active"
            else:
                camp.is_active = False
                camp.status = "en_attente"  # Passe en attente de modération admin
        return "campagne"

    # Les abonnements vidéo ont été retirés du produit. Une transaction de ce
    # type ne peut plus être créée ; si une ancienne remontait encore, elle est
    # simplement marquée payée sans effet métier.
    return "autre"


def appliquer_paiement_echoue(transaction, statut):
    """Répercute une annulation ou un refus. Ne fait aucun commit."""
    transaction.status = statut
    if transaction.campaign_id:
        camp = db.session.get(Campaign, transaction.campaign_id)
        if camp and not camp.paid:
            camp.payment_status = "unpaid"
            camp.paid = False
            camp.is_active = False
            camp.status = "non_payee"


# ==========================================
# ROUTE : CALLBACK DE PAIEMENT FEDAPAY
# ==========================================
@app.route("/dashboard/annonceur/paiement/callback", methods=["GET"])
@login_required
def paiement_callback():
    """
    Route de retour après le parcours de paiement FedaPay.
    Vérifie le statut de la transaction directement auprès de FedaPay.

    Ce retour reste un confort d'affichage : la source de vérité est le webhook
    (/webhooks/fedapay), qui fonctionne même si le client ferme son navigateur.
    """
    # FedaPay passe l'ID sous le paramètre 'id' dans l'URL après redirection
    fedapay_id = request.args.get("id") or request.args.get("transaction_id")

    if not fedapay_id:
        flash("Aucun identifiant de transaction n'a été fourni. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    # 1. Recherche de la transaction locale correspondante
    transaction = Transaction.query.filter_by(
        fedapay_transaction_id=str(fedapay_id),
        user_id=current_user.id
    ).first()

    if not transaction:
        flash("Transaction introuvable dans notre système. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # Si la transaction a déjà été traitée (webhook plus rapide, ou rechargement)
    if transaction.status == "approved" and transaction.verified_at:
        flash("Votre paiement a déjà été validé avec succès ! ✅", "success")
        return redirect(url_for("mes_campagnes"))

    # 2. Vérification côté serveur via verifier_transaction()
    try:
        details = verifier_transaction(transaction.fedapay_transaction_id)
        status_fedapay = _statut_fedapay(details)

        if status_fedapay in ["approved", "transferred"]:
            resultat = appliquer_paiement_confirme(transaction, details)
            db.session.commit()

            if resultat == "campagne":
                flash("Paiement effectué avec succès ! Votre campagne a été transmise pour validation. 🎉", "success")
            elif resultat == "abonnement":
                flash("Félicitations ! Votre abonnement de génération vidéo est actif. 🚀", "success")
            else:
                flash("Paiement validé avec succès ! ✅", "success")

        elif status_fedapay in ["canceled", "declined"]:
            appliquer_paiement_echoue(transaction, status_fedapay)
            db.session.commit()
            flash("Le paiement a été annulé ou a échoué. Vous pouvez réessayer. ⚠️", "warning")

        else:  # Statut encore 'pending'
            flash("Le paiement est toujours en cours de traitement. Un moment svp... ⏳", "info")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur vérification paiement FedaPay (TX: %s) : %s", fedapay_id, e)
        flash("Erreur lors de la vérification de votre paiement. Réessayez plus tard.", "danger")

    return redirect(url_for("mes_campagnes"))


# ==========================================
# ROUTE : WEBHOOK FEDAPAY
# ==========================================
@app.route("/webhooks/fedapay", methods=["POST"])
@csrf.exempt
@limiter.exempt
def webhook_fedapay():
    """
    Notification serveur-à-serveur envoyée par FedaPay à chaque changement de
    statut d'une transaction.

    Sans cette route, une campagne n'était marquée payée que si le client
    revenait sur le site après le paiement. En mobile money, beaucoup ferment
    l'onglet dès la confirmation par SMS : l'argent était débité et la campagne
    restait bloquée en « non payée ».
    """
    secret = current_app.config.get("FEDAPAY_WEBHOOK_SECRET") or ""
    if not secret:
        logger.error("[SECURITE] Webhook FedaPay reçu mais FEDAPAY_WEBHOOK_SECRET n'est pas configuré.")
        abort(503)

    # FedaPay signe le corps de la requête (en-tête x-fedapay-signature,
    # format « t=<horodatage>,s=<signature> »).
    entete = request.headers.get("x-fedapay-signature", "")
    corps_brut = request.get_data()

    signature_fournie = None
    horodatage = None
    for partie in entete.split(","):
        cle, _, valeur = partie.strip().partition("=")
        if cle == "s":
            signature_fournie = valeur
        elif cle == "t":
            horodatage = valeur

    if not signature_fournie or not horodatage:
        logger.warning("[SECURITE] Webhook FedaPay sans signature exploitable.")
        abort(400)

    attendu = hmac.new(
        secret.encode("utf-8"),
        f"{horodatage}.".encode("utf-8") + corps_brut,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature_fournie, attendu):
        logger.warning("[SECURITE] Webhook FedaPay rejeté : signature invalide.")
        abort(400)

    data = request.get_json(silent=True) or {}
    entite = data.get("entity") or data.get("data") or {}
    fedapay_id = entite.get("id") or data.get("id")

    if not fedapay_id:
        logger.warning("Webhook FedaPay sans identifiant de transaction.")
        return jsonify({"ok": True}), 200

    transaction = Transaction.query.filter_by(
        fedapay_transaction_id=str(fedapay_id)
    ).first()

    if not transaction:
        logger.warning("Webhook FedaPay pour une transaction inconnue (id=%s).", fedapay_id)
        return jsonify({"ok": True}), 200

    # On ne fait jamais confiance au statut annoncé dans le webhook : on
    # réinterroge FedaPay, comme pour le retour navigateur.
    try:
        details = verifier_transaction(transaction.fedapay_transaction_id)
        statut = _statut_fedapay(details)

        if statut in ["approved", "transferred"]:
            resultat = appliquer_paiement_confirme(transaction, details)
            db.session.commit()
            logger.info(
                "[PAIEMENT] Webhook FedaPay appliqué (tx=%s, type=%s, resultat=%s)",
                fedapay_id, transaction.transaction_type, resultat
            )
        elif statut in ["canceled", "declined"]:
            appliquer_paiement_echoue(transaction, statut)
            db.session.commit()
            logger.info("[PAIEMENT] Webhook FedaPay : transaction %s → %s", fedapay_id, statut)

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur traitement webhook FedaPay (tx=%s) : %s", fedapay_id, e)
        # 500 : FedaPay réessaiera l'envoi.
        return jsonify({"ok": False}), 500

    return jsonify({"ok": True}), 200












@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            if not user.is_confirmed and user.role not in ("admin", "sous_admin"):
                flash("Votre compte doit être confirmé avant connexion 🚫", "danger")
                logger.warning("Tentative de connexion sur compte non confirmé : %s", form.email.data)
                return redirect(url_for("login"))

            # 🆕 Un sous-admin désactivé ne peut plus se connecter du tout
            if user.role == "sous_admin" and not user.is_active_admin:
                flash("Votre compte administrateur a été désactivé. Contactez l'administration. 🚫", "danger")
                logger.warning("Tentative de connexion sur compte sous-admin désactivé : %s", form.email.data)
                return redirect(url_for("login"))

            login_user(user, remember=form.remember.data)
            flash("Connexion réussie ✅", "success")
            logger.info("Connexion réussie pour l'utilisateur (role: %s)", user.role)

            # 🆕 admin ET sous_admin sont tous deux redirigés vers le panneau d'administration
            if user.role in ("admin", "sous_admin"):
                return redirect(url_for("admin_validate"))
            return redirect(url_for(f"dashboard_{user.role}"))
        flash("Email ou mot de passe invalide ❌", "danger")
        logger.warning("[AUTH] Échec de connexion depuis IP=%s", request.remote_addr)
    return render_template("login.html", form=form)



# ==========================================
# ROUTE : RÉCLAMER UN REMBOURSEMENT
# ==========================================
@app.route("/annonceur/campaign/<int:campaign_id>/reclamer_remboursement", methods=["POST"])
@login_required
def reclamer_remboursement(campaign_id):
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    camp = db.session.get(Campaign, campaign_id)
    if not camp or camp.user_id != current_user.id:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("mes_campagnes"))

    # 🐞 FIX : double vérification serveur — impossible de rembourser une campagne jamais payée,
    # même si can_claim_refund avait été forcé à True par erreur ailleurs, ou l'URL appelée directement.
    if not camp.paid:
        flash("Aucun remboursement n'est possible : cette campagne n'a jamais été payée. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    # Vérification de l'autorisation de remboursement
    if not camp.can_claim_refund:
        flash("L'option de remboursement n'est pas autorisée pour cette campagne. Veuillez contacter le support. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    phone_number = request.form.get("refund_phone", "").strip()
    payment_method = request.form.get("refund_method", "").strip()

    if not phone_number or not payment_method:
        flash("Veuillez renseigner le moyen et le numéro de téléphone pour le remboursement. ⚠️", "warning")
        return redirect(url_for("mes_campagnes"))

    # Sauvegarde et nettoyage XSS des détails
    clean_phone = bleach.clean(phone_number)
    clean_method = bleach.clean(payment_method)
    payment_info = f"Moyen : {clean_method} | Numéro : {clean_phone}"

    # Enregistrement de la demande. La garde `if 'RefundRequest' in globals()`
    # qui entourait ce bloc etait toujours fausse (le modele n'etait pas
    # importe) : les coordonnees de remboursement saisies n'etaient ecrites
    # nulle part.
    refund_req = RefundRequest(
        campaign_id=camp.id,
        user_id=current_user.id,
        reason=f"Demande suite au rejet de la campagne #{camp.id}",
        payment_method_details=payment_info,
        status="pending"
    )
    db.session.add(refund_req)

    # Notification aux administrateurs : sans elle, personne n'est prévenu
    # qu'un virement est à effectuer.
    for admin in User.query.filter_by(role="admin").all():
        db.session.add(Notification(
            user_id=admin.id,
            title="Remboursement à traiter 💸",
            message=f"L'annonceur de la campagne #{camp.id} a transmis ses coordonnées de remboursement.",
            category="warning",
            link=url_for("admin_validate"),
            is_read=False
        ))

    # --- MISE À JOUR DES STATUTS ---
    camp.status = "remboursement_demande"  # Permet un filtrage clair dans mes_campagnes
    camp.payment_status = "refund_requested"
    camp.can_claim_refund = False         # Désactive le bouton pour éviter les demandes multiples

    db.session.commit()

    logger.info(
        "[REMBOURSEMENT] Demande enregistrée pour la campagne #%d (User %d)", 
        camp.id, current_user.id
    )

    flash("Votre demande de remboursement a bien été transmise. Le virement sera effectué sous 48 heures au maximum. ⏳", "success")
    return redirect(url_for("mes_campagnes")) 



@app.route("/register/<role>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register(role):
    if role not in ["annonceur", "partageur"]:
        flash("Rôle invalide 🚫", "danger")
        return redirect(url_for("index"))

    # =========================================================================
    # 🔗 === PARRAINAGE : CAPTURE DU PARRAIN DEPUIS L'URL ===
    # Ex: /register/annonceur?ref=pseudo_du_parrain ou /register/annonceur?ref=ID
    # =========================================================================
    ref_param = request.args.get("ref")
    if ref_param:
        # On cherche si le parrain existe (soit par son Pseudo, soit par son ID)
        # ⚠️ User.id est un entier : on ne le compare que si ref_param est numérique,
        # sinon PostgreSQL lève une erreur de conversion et casse toute la requête.
        if ref_param.isdigit():
            referrer = User.query.filter(
                (User.pseudo == ref_param) | (User.id == int(ref_param))
            ).first()
        else:
            referrer = User.query.filter(User.pseudo == ref_param).first()

        if referrer:
            session["referrer_id"] = referrer.id
            logger.info("Parrain détecté et stocké en session : %s (ID: %s)", referrer.pseudo, referrer.id)

    form = RegisterForm()

    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data).first()

        # =========================================================================
        # 📞 === RECONSTRUCTION DU NUMÉRO WHATSAPP COMPLET ===
        # form.whatsapp_number.data ne contient que les 8 chiffres saisis par
        # l'utilisateur (le champ n'accepte plus le préfixe). On reconstruit ici
        # le numéro complet au format béninois avant toute validation/sauvegarde.
        # =========================================================================
        whatsapp_number = form.whatsapp_number.data
        if whatsapp_number:
            whatsapp_number = "+22901" + whatsapp_number.strip()

        # FIX: Anti-énumération — même message que l'email soit pris ou non
        email_ou_whatsapp_pris = False

        if existing_user:
            email_ou_whatsapp_pris = True

        if whatsapp_number and not email_ou_whatsapp_pris:
            # Même règle que le formulaire (forms.NUMERO_WHATSAPP_REGEX)
            if not numero_whatsapp_valide(whatsapp_number):
                flash(MESSAGE_NUMERO_INVALIDE, "danger")
                return render_template("register.html", form=form, role=role, departements_communes=DEPARTEMENTS_COMMUNES)
            if User.query.filter_by(whatsapp_number=whatsapp_number).first():
                email_ou_whatsapp_pris = True

        if email_ou_whatsapp_pris:
            # Message générique : ne révèle pas si c'est l'email ou le WhatsApp qui est pris
            flash("Un compte avec ces informations existe déjà. Vérifiez vos données ou connectez-vous.", "danger")
            return render_template("register.html", form=form, role=role, departements_communes=DEPARTEMENTS_COMMUNES)

        hashed_pw = bcrypt.generate_password_hash(form.password.data).decode("utf-8")
        is_confirmed = (role == "annonceur")

        pseudo_base = form.email.data.split("@")[0]
        pseudo = f"{pseudo_base}{random.randint(100, 999)}"

        logo_filename = None
        if role == "annonceur" and "logo_file" in request.files:
            logo_file = request.files["logo_file"]
            if logo_file and logo_file.filename:
                ok, err = valider_image(logo_file)
                if not ok:
                    flash(f"Logo invalide : {err}", "danger")
                    return render_template("register.html", form=form, role=role, departements_communes=DEPARTEMENTS_COMMUNES)
                logo_filename = generer_nom_unique(logo_file.filename)
                logo_file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], logo_filename))

        # =========================================================================
        # 🔗 === PARRAINAGE : RÉCUPÉRATION DU PARRAIN DEPUIS LA SESSION ===
        # =========================================================================
        referrer_id_to_save = session.get("referrer_id")

        new_user = User(
            email=form.email.data,
            password_hash=hashed_pw,
            role=role,
            company_name=form.company_name.data if role == "annonceur" else None,
            province=form.province.data or "Non spécifiée",
            commune=form.commune.data if role == "partageur" else None,  # 🆕
            whatsapp_number=whatsapp_number,
            pseudo=pseudo,
            is_confirmed=is_confirmed,
            logo=logo_filename,
            # Association du parrain
            referrer_id=referrer_id_to_save,
            has_launched_first_campaign=False
        )

        try:
            db.session.add(new_user)
            db.session.commit()

            # Le logo a été écrit sur disque avant que l'utilisateur n'existe :
            # on ne peut lui attribuer un propriétaire qu'une fois l'id connu.
            if logo_filename:
                enregistrer_upload(logo_filename, new_user.id, kind="logo")
                db.session.commit()

            # Une fois inscrit, on nettoie la session pour éviter les effets de bord
            session.pop("referrer_id", None)

            logger.info("Nouvel utilisateur inscrit (role: %s, parrainé_par: %s).", role, referrer_id_to_save)
            
            if role == "partageur":
                flash(f"Merci {pseudo} 🙏 Votre demande est enregistrée et en attente de validation.", "info")
                return redirect(url_for("index"))
            else:
                flash("Compte annonceur créé avec succès 🎉", "success")
                return redirect(url_for("login"))
        except Exception as e:
            db.session.rollback()
            flash("Une erreur est survenue lors de l'enregistrement. ⚠️", "danger")
            logger.error("Erreur DB inscription : %s", e)

    return render_template("register.html", form=form, role=role, departements_communes=DEPARTEMENTS_COMMUNES)


@app.route("/dashboard/annonceur")
@login_required
def dashboard_annonceur():
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))
        
    # Configuration globale (tarifs, commissions, garde-fous anti-fraude)
    config = SystemConfig.get_config()

    # L'annonceur voit uniquement ses campagnes
    campaigns = Campaign.query.filter_by(user_id=current_user.id).order_by(Campaign.created_at.desc()).all()

    return render_template(
        "dashboard_annonceur.html",
        campaigns=campaigns,
        config=config,
        departements_communes=DEPARTEMENTS_COMMUNES
    )


@app.route("/dashboard/partageur")
@login_required
def dashboard_partageur():
    if current_user.role != "partageur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))
        
    # =========================================================================
    # 📈 CALCULATEUR DE PARRAINAGE EXCLUSIF POUR LE PARTAGEUR
    # =========================================================================
    from models import SystemConfig, User, Campaign, Notification, WalletTransaction
    config = SystemConfig.get_config()
    
    # Récupération des filleuls (les annonceurs parrainés par ce partageur)
    filleuls = User.query.filter_by(referrer_id=current_user.id).all()
    total_filleuls = len(filleuls)

    # 🆕 Gains de parrainage déjà crédités (vraie donnée du portefeuille, plus de calcul dupliqué)
    gains_valides = (
        db.session.query(func.coalesce(func.sum(WalletTransaction.amount), 0.0))
        .filter(
            WalletTransaction.user_id == current_user.id,
            WalletTransaction.transaction_type == "referral_reward"
        )
        .scalar()
    )

    # Gains encore EN ATTENTE : filleuls dont la première campagne n'est pas encore payée+validée
    gains_en_attente = 0.0
    for filleul in filleuls:
        if filleul.has_launched_first_campaign:
            continue  # Déjà validée (et donc déjà créditée ci-dessus) — on ne recompte pas

        premiere_campagne = Campaign.query.filter_by(user_id=filleul.id).order_by(Campaign.created_at.asc()).first()
        if premiere_campagne and not (premiere_campagne.paid and premiere_campagne.validated):
            taux_commission = config.commission_rate / 100.0
            cout_base = premiere_campagne.total_cost / (1 + taux_commission)
            gain_parrain = round(cout_base * (config.referral_reward_rate / 100.0), 2)
            gains_en_attente += gain_parrain

    # =========================================================================
    # 🆕 CLICS VALIDES EN ATTENTE DE VALIDATION ADMIN (preuve de fin de journée
    # pas encore validée) — distinct des gains_en_attente ci-dessus, qui eux
    # concernent le parrainage. Ce montant n'est pas encore dans le portefeuille
    # retirable (wallet_balance) : il le rejoindra une fois la preuve validée.
    # =========================================================================
    clics_en_attente_validation = montant_en_attente_validation(current_user)

    # Lien d'affiliation unique du partageur (redirige vers l'inscription d'un annonceur avec sa réf)
    affiliate_link = url_for("register", role="annonceur", ref=current_user.pseudo or current_user.id, _external=True)

    # =========================================================================
    # 🆕 NOTIFICATIONS DU PARTAGEUR
    # =========================================================================
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    notifications_non_lues = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    # =========================================================================
    # 🆕 CAMPAGNES DISPONIBLES : validées, partagées, actives, ciblant la zone du partageur
    # =========================================================================
    campagnes_query = Campaign.query.filter_by(validated=True, shared_to_partageurs=True, is_active=True)

    # Recuperation en une fois de tous les CampaignShare de ce partageur
    # (evite une requete par campagne)
    mes_shares = {
        s.campaign_id: s
        for s in CampaignShare.query.filter_by(sharer_id=current_user.id).all()
    }

    campagnes_disponibles = []
    for camp in campagnes_query.order_by(Campaign.shared_at.desc()).all():
        # Ciblage geographique : meme regle qu'a la confirmation de partage et
        # qu'au controle d'acces aux medias (campagne_cible_utilisateur).
        if not campagne_cible_utilisateur(camp, current_user):
            continue

        deja_partagee = camp.id in mes_shares

        campagnes_disponibles.append({
            "campaign": camp,
            "deja_partagee": deja_partagee,
            # Statut du quota journalier, utile seulement si deja partagee
            "quota_atteint_aujourdhui": bool(deja_partagee and camp.daily_quota_paused),
            "jour_actuel": camp.current_day_number or 0,
            "duree_totale": camp.duration_days,
            "vues_aujourdhui": camp.views_today or 0,
            "quota_du_jour": camp.views_per_day or 0,
        })

    return render_template(
        "dashboard_partageur.html",
        total_filleuls=total_filleuls,
        gains_valides=round(gains_valides, 2),
        gains_en_attente=round(gains_en_attente, 2),
        clics_en_attente_validation=round(clics_en_attente_validation, 2),
        solde_portefeuille=current_user.wallet_balance or 0.0,
        affiliate_link=affiliate_link,
        notifications=notifications,
        notifications_non_lues=notifications_non_lues,
        campagnes_disponibles=campagnes_disponibles
    )



# 🛡️ ZONE ADMIN SECURISEE


def verifier_droits_admin(permission_requise=None):
    """
    Vérifie que l'utilisateur a le droit d'accéder à une section admin.
    - Le super-admin (role == "admin") passe toujours, quelle que soit la permission demandée.
    - Un sous-admin (role == "sous_admin") doit avoir explicitement la permission demandée
      ET être actif (is_active_admin), sinon il est bloqué avec un 403.
    - Si permission_requise est None, on vérifie simplement que l'utilisateur est admin OU sous-admin actif
      (utile pour des pages communes, s'il en existe).
    """
    if current_user.role not in ("admin", "sous_admin"):
        logger.warning(
            "Accès admin refusé pour l'utilisateur id=%s (role=%s).",
            current_user.id, current_user.role
        )
        abort(403)

    if current_user.role == "admin":
        return  # Le super-admin a toujours accès à tout

    # À partir d'ici, on sait que role == "sous_admin"
    if not current_user.is_active_admin:
        logger.warning("Accès refusé : sous-admin id=%s désactivé.", current_user.id)
        abort(403)

    if permission_requise and not current_user.has_permission(permission_requise):
        logger.warning(
            "Accès refusé : sous-admin id=%s n'a pas la permission '%s'.",
            current_user.id, permission_requise
        )
        abort(403)



# ==========================================
# 🆕 ROUTE ADMIN : SUIVI COMPLET D'UNE CAMPAGNE VALIDÉE



@app.route("/admin/campagne/<int:campaign_id>/suivi")
@login_required
def admin_suivi_campagne(campaign_id):
    verifier_droits_admin("suivre_campagnes")

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    if not camp.paid or not camp.validated:
        flash("Le suivi détaillé n'est disponible que pour les campagnes payées et validées. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 1️⃣ Liste des partageurs de cette campagne (pseudo + email + date + id du CampaignShare)
    shares = (
        db.session.query(
            CampaignShare.id,
            CampaignShare.sharer_id,
            CampaignShare.created_at,
            User.pseudo,
            User.email
        )
        .join(User, User.id == CampaignShare.sharer_id)
        .filter(CampaignShare.campaign_id == campaign_id)
        .all()
    )

    partageurs = []
    total_clics_valides = 0
    total_clics_frauduleux = 0
    clics_detail = []

    if shares:
        share_ids = [s.id for s in shares]
        shares_par_id = {s.id: s for s in shares}

        # 2️⃣ Comptage des clics par partage, type de lien ET validité —
        # les clics frauduleux ne sont plus mélangés aux clics valides.
        clics_bruts = (
            db.session.query(
                CampaignClick.campaign_share_id,
                CampaignClick.link_type,
                CampaignClick.is_paid,
                func.count(CampaignClick.id)
            )
            .filter(CampaignClick.campaign_share_id.in_(share_ids))
            .group_by(CampaignClick.campaign_share_id, CampaignClick.link_type, CampaignClick.is_paid)
            .all()
        )

        clics_par_share = {}
        for share_id, link_type, is_paid, nb in clics_bruts:
            entry = clics_par_share.setdefault(share_id, {
                "whatsapp_valides": 0, "whatsapp_faux": 0,
                "site_valides": 0, "site_faux": 0,
            })
            suffixe_type = "whatsapp" if link_type == "whatsapp" else "site"
            suffixe_validite = "valides" if is_paid else "faux"
            entry[f"{suffixe_type}_{suffixe_validite}"] = nb

        for s in shares:
            c = clics_par_share.get(s.id, {"whatsapp_valides": 0, "whatsapp_faux": 0, "site_valides": 0, "site_faux": 0})
            total_valides = c["whatsapp_valides"] + c["site_valides"]
            total_faux = c["whatsapp_faux"] + c["site_faux"]
            partageurs.append({
                "pseudo": s.pseudo or "Partageur anonyme",
                "email": s.email,
                "clics_whatsapp_valides": c["whatsapp_valides"],
                "clics_site_valides": c["site_valides"],
                "clics_frauduleux": total_faux,
                "total_clics_valides": total_valides,
                "partage_le": s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else None,
            })

        partageurs.sort(key=lambda p: p["total_clics_valides"], reverse=True)
        total_clics_valides = sum(p["total_clics_valides"] for p in partageurs)
        total_clics_frauduleux = sum(p["clics_frauduleux"] for p in partageurs)

        # 3️⃣ Détail clic par clic (les 300 plus récents) pour audit fin :
        # partageur, heure, type de lien, vrai/faux, motif précis du rejet.
        # Les totaux ci-dessus restent exacts sur l'ensemble des clics, cette
        # liste ne sert qu'à l'inspection détaillée.
        clics_bruts_detail = (
            CampaignClick.query
            .filter(CampaignClick.campaign_share_id.in_(share_ids))
            .order_by(CampaignClick.clicked_at.desc())
            .limit(300)
            .all()
        )
        for c in clics_bruts_detail:
            s = shares_par_id.get(c.campaign_share_id)
            clics_detail.append({
                "pseudo": (s.pseudo or "Partageur anonyme") if s else "Inconnu",
                "heure": c.clicked_at.strftime("%d/%m/%Y %H:%M:%S") if c.clicked_at else "—",
                "link_type": c.link_type,
                "is_paid": c.is_paid,
                "motif": MOTIFS_REJET_LIBELLES.get(c.rejection_reason, c.rejection_reason) if not c.is_paid else None,
                "ip": c.ip,
            })

    return render_template(
        "admin_suivi_campagne.html",
        campaign=camp,
        partageurs=partageurs,
        total_clics_valides=total_clics_valides,
        total_clics_frauduleux=total_clics_frauduleux,
        clics_detail=clics_detail,
    )



@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    verifier_droits_admin("configurer_tarifs")

    from models import SystemConfig
    config = SystemConfig.get_config()

    if request.method == "POST":
        try:
            # Récupération et conversion des nouvelles valeurs du formulaire
            cost_video = float(request.form.get("cost_per_click_video", 3.0))   # Option A: Vidéo
            cost_photo = float(request.form.get("cost_per_click_photo", 1.0))   # Option B: Image
            cost_text = float(request.form.get("cost_per_click_text", 1.0))     # Option C: Texte statut pro

            reward_video = float(request.form.get("reward_per_click_video", 1.0))
            reward_photo = float(request.form.get("reward_per_click_photo", 0.4))
            reward_text = float(request.form.get("reward_per_click_text", 0.3))

            comm_rate = float(request.form.get("commission_rate", 10.0))
            ref_rate = float(request.form.get("referral_reward_rate", 3.0))
            min_withdrawal = float(request.form.get("minimum_withdrawal_amount", 500.0))

            # Validations de sécurité de base
            valeurs_a_verifier = [cost_video, cost_photo, cost_text, reward_video, reward_photo, reward_text, comm_rate, ref_rate, min_withdrawal]
            if any(v < 0 for v in valeurs_a_verifier):
                flash("Les valeurs ne peuvent pas être négatives ⚠️", "danger")
                return redirect(url_for("admin_settings"))

            if comm_rate > 100 or ref_rate > 100:
                flash("Les taux de commission ou de parrainage ne peuvent pas dépasser 100% ⚠️", "danger")
                return redirect(url_for("admin_settings"))

            # 🆕 Sécurité métier : la récompense du partageur ne doit jamais dépasser
            # le prix facturé à l'annonceur pour le même type de contenu (marge négative sinon)
            if reward_video > cost_video or reward_photo > cost_photo or reward_text > cost_text:
                flash("La récompense par clic d'un partageur ne peut pas dépasser le prix facturé à l'annonceur pour le même type de contenu ⚠️", "danger")
                return redirect(url_for("admin_settings"))

            # Mise à jour de la configuration globale
            config.cost_per_click_video = cost_video
            config.cost_per_click_photo = cost_photo
            config.cost_per_click_text = cost_text
            config.reward_per_click_video = reward_video
            config.reward_per_click_photo = reward_photo
            config.reward_per_click_text = reward_text
            config.commission_rate = comm_rate
            config.referral_reward_rate = ref_rate
            config.minimum_withdrawal_amount = min_withdrawal

            db.session.commit()
            flash("Configurations mises à jour avec succès ! ⚙️✅", "success")
            return redirect(url_for("admin_settings"))

        except ValueError:
            flash("Veuillez entrer des nombres valides. ⚠️", "danger")
        except Exception as e:
            db.session.rollback()
            logger.error("Erreur lors de la mise à jour des paramètres admin : %s", e)
            flash("Une erreur système est survenue. ⚠️", "danger")

    return render_template("admin_settings.html", config=config)


@app.route("/admin/validate")
@login_required
def admin_validate():
    # 🆕 Accès à la page si l'utilisateur a AU MOINS UNE des deux permissions liées à cette page.
    # Le filtrage fin de ce qu'il voit réellement se fait juste après.
    if current_user.role == "admin":
        peut_voir_utilisateurs = True
        peut_voir_campagnes = True
    elif current_user.role == "sous_admin" and current_user.is_active_admin:
        peut_voir_utilisateurs = current_user.has_permission("valider_utilisateurs")
        peut_voir_campagnes = current_user.has_permission("valider_campagnes")
    else:
        peut_voir_utilisateurs = False
        peut_voir_campagnes = False

    if not peut_voir_utilisateurs and not peut_voir_campagnes:
        logger.warning(
            "Accès refusé à /admin/validate pour l'utilisateur id=%s (role=%s) : aucune permission liée.",
            current_user.id, current_user.role
        )
        abort(403)

    # 🆕 On ne charge et n'envoie au template QUE ce que l'utilisateur a le droit de voir
    users = []
    if peut_voir_utilisateurs:
        users = User.query.order_by(User.created_at.desc()).all()
        for u in users:
            if u.whatsapp_number:
                u.whatsapp_message = urllib.parse.quote(
                    f"Bonjour,\n\nNous vous remercions pour votre inscription sur Pubwek. "
                    f"Votre dossier est en cours de vérification.\n\n"
                    f"Merci de confirmer votre lieu de résidence.\n\n— L'équipe Pubwek"
                )
            else:
                u.whatsapp_message = ""

    campaigns = []
    if peut_voir_campagnes:
        campaigns = Campaign.query.order_by(Campaign.created_at.desc()).all()
        for camp in campaigns:
            camp.annonceur = db.session.get(User, camp.user_id)

    return render_template(
        "admin_validate.html",
        users=users,
        campaigns=campaigns,
        peut_voir_utilisateurs=peut_voir_utilisateurs,
        peut_voir_campagnes=peut_voir_campagnes
    )    







# ==========================================
# 🆕 ROUTE : LE PARTAGEUR CONFIRME LE PARTAGE D'UNE CAMPAGNE
# ==========================================
@app.route("/partageur/partager_campagne/<int:campaign_id>", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def partager_campagne_partageur(campaign_id):
    if current_user.role != "partageur":
        flash("Accès réservé aux partageurs. 🚫", "danger")
        return redirect(url_for("index"))

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    if not camp.validated or not camp.shared_to_partageurs or not camp.is_active:
        flash("Cette campagne n'est plus disponible au partage. ⚠️", "warning")
        return redirect(url_for("dashboard_partageur"))

    # Vérification de zone (sécurité : empêche de forcer l'URL d'une campagne
    # qui ne cible pas la zone du partageur)
    if not campagne_cible_utilisateur(camp, current_user):
        flash("Cette campagne ne cible pas votre zone. 🚫", "danger")
        return redirect(url_for("dashboard_partageur"))

    # Vérifie si déjà partagée par ce partageur (une seule fois autorisée)
    existing_share = CampaignShare.query.filter_by(campaign_id=camp.id, sharer_id=current_user.id).first()
    if existing_share:
        return redirect(url_for("instructions_partage", campaign_id=camp.id))

    new_share = CampaignShare(campaign_id=camp.id, sharer_id=current_user.id)
    try:
        db.session.add(new_share)
        db.session.commit()
        logger.info(
            "[PARTAGE] Campagne #%d partagée par partageur id=%d", camp.id, current_user.id
        )
    except IntegrityError:
        # Sécurité contre un double-clic rapide simultané (contrainte d'unicité en base)
        db.session.rollback()

    return redirect(url_for("instructions_partage", campaign_id=camp.id))


# ==========================================
# 🆕 ROUTE : PAGE D'INSTRUCTIONS POUR PUBLIER LE STATUT
# ==========================================
def etats_preuves_partage(share, camp):
    """Construit, pour l'affichage, l'état de la preuve de fin de journée
    pour chaque jour de diffusion déjà entamé (du jour 1 au jour courant).

    Un jour reste actionnable (formulaire d'envoi affiché) tant qu'il n'a pas
    de preuve validée ET qu'il est encore dans la fenêtre de rattrapage de
    Campaign.FENETRE_RATTRAPAGE_HEURES. Passé ce délai sans validation, le
    jour est marqué "perdue" : les clics de ce jour ne seront plus jamais
    crédités.

    Retourne une liste de dicts (un par jour), du plus ancien au plus récent :
    {"jour", "preuve", "statut", "reclamable", "deadline", "heures_restantes"}
    """
    jour_actuel = camp.jour_diffusion_campagne()
    preuves = {
        p.day_number: p
        for p in CampaignShareProof.query.filter_by(campaign_share_id=share.id).all()
    }

    maintenant = datetime.utcnow()
    jours = []

    for jour in range(1, jour_actuel + 1):
        preuve = preuves.get(jour)
        reclamable = camp.jour_encore_reclamable(jour, maintenant)
        deadline = camp.date_limite_preuve(jour)
        validee = bool(preuve and preuve.status == "validee")

        if validee:
            statut = "validee"
        elif not reclamable:
            statut = "perdue"
        elif preuve and preuve.status == "en_attente":
            statut = "en_attente"
        elif preuve and preuve.status == "rejetee":
            statut = "rejetee"
        else:
            statut = "a_envoyer"

        heures_restantes = max(0, int((deadline - maintenant).total_seconds() // 3600)) if reclamable else 0

        jours.append({
            "jour": jour,
            "preuve": preuve,
            "statut": statut,
            "reclamable": reclamable,
            "deadline": deadline,
            "heures_restantes": heures_restantes,
        })

    return jours


@app.route("/partageur/instructions_partage/<int:campaign_id>")
@login_required
def instructions_partage(campaign_id):
    if current_user.role != "partageur":
        flash("Accès réservé aux partageurs. 🚫", "danger")
        return redirect(url_for("index"))
    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))
    share = CampaignShare.query.filter_by(campaign_id=camp.id, sharer_id=current_user.id).first()
    if not share:
        flash('Veuillez d\'abord cliquer sur "Partager cette campagne" depuis votre tableau de bord. ⚠️', "warning")
        return redirect(url_for("dashboard_partageur"))
    media_urls = []
    if camp.media_files:
        for f in camp.media_files.split(","):
            media_urls.append(url_for("serve_upload", filename=f))
    # 🆕 Liens de tracking à insérer dans le statut (whatsapp + site web si disponibles)
    lien_whatsapp_tracking = url_for("tracking_redirect_whatsapp", token=share.tracking_token, _external=True) if camp.whatsapp_number else None
    lien_site_tracking = url_for("tracking_redirect_site", token=share.tracking_token, _external=True) if camp.website_url else None

    # 🆕 État des preuves de fin de journée, pour chaque jour déjà entamé,
    # avec gestion de la fenêtre de rattrapage de 48h.
    jour_actuel = camp.jour_diffusion_campagne()
    jours_preuves = etats_preuves_partage(share, camp)

    return render_template(
        "instructions_partage.html",
        camp=camp,
        share=share,
        media_urls=media_urls,
        lien_whatsapp_tracking=lien_whatsapp_tracking,
        lien_site_tracking=lien_site_tracking,
        jour_actuel=jour_actuel,
        jours_preuves=jours_preuves,
    )


# ==========================================
# ROUTE : REFUS D'UNE CAMPAGNE (ADMIN)
# ==========================================
@app.route("/admin/refuse_campaign/<int:campaign_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def refuse_campaign(campaign_id):
    verifier_droits_admin("valider_campagnes")

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    if camp.validated or camp.status == "valide":
        flash("Impossible de refuser une campagne déjà validée. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    # Récupération et nettoyage de la raison
    reason = request.form.get("rejection_reason")
    if reason:
        reason = bleach.clean(reason.strip())

    if not reason:
        flash("Veuillez obligatoirement fournir un motif de refus. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    annonceur = db.session.get(User, camp.user_id)

    # 1. Mise à jour des statuts de la campagne
    camp.validated = False
    camp.is_active = False
    camp.admin_status = "rejected"
    camp.status = "rejete"            # Statut principal harmonisé pour mes_campagnes
    camp.rejection_reason = reason
    camp.can_claim_refund = camp.paid  # 🐞 FIX : remboursement possible UNIQUEMENT si un paiement a réellement été effectué

    # 2. Création de la notification interne pour l'annonceur
    nom_campagne = camp.promotion_detail or f"#{camp.id}"
    notif_msg = f"Votre campagne '{nom_campagne}' a été refusée pour le motif suivant : {reason}."

    notif = Notification(
        user_id=camp.user_id,
        title="Campagne refusée ❌",
        message=notif_msg,
        category="danger",
        link=url_for("mes_campagnes"),
        is_read=False
    )
    db.session.add(notif)

    db.session.commit()

    logger.warning(
        "[ACTION ADMIN] Campagne #%d refusée (Motif: %s) par admin id=%d", 
        campaign_id, reason, current_user.id
    )

    # 3. Notification WhatsApp facultative avec lien direct
    if annonceur and annonceur.whatsapp_number:
        wa_message = (
            f"Bonjour, votre campagne #{camp.id} a été REFUSÉE ❌.\n"
            f"Motif : {reason}\n"
            f"Connectez-vous à votre espace pour corriger et relancer votre campagne ou demander un remboursement."
        )
        encoded = urllib.parse.quote(wa_message)
        wa_link = f"https://wa.me/{annonceur.whatsapp_number}?text={encoded}"
        
        flash(
            Markup(
                f'Campagne #{escape(camp.id)} refusée et enregistrée avec motif ❌. '
                f'<a href="{escape(wa_link)}" target="_blank" rel="noopener" '
                f'class="btn btn-sm btn-outline-danger ms-2">📱 Notification WhatsApp</a>'
            ),
            "warning"
        )
    else:
        flash(f"Campagne #{camp.id} refusée avec succès. Motif transmis à l'annonceur ❌", "warning")

    return redirect(url_for("admin_validate"))


@app.route("/admin/validate_campaign/<int:campaign_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def validate_campaign(campaign_id):
    verifier_droits_admin("valider_campagnes")

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    # 🔒 Blocage : impossible de valider une campagne dont le paiement n'a pas été confirmé par FedaPay
    if not camp.paid:
        flash("Impossible de valider une campagne dont le paiement n'a pas été confirmé. Le client doit d'abord payer. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    # Vérification si déjà validée
    if camp.validated or camp.admin_status == "approved" or camp.status == "valide":
        flash(f"La campagne #{camp.id} est déjà validée. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 1️⃣ Validation et mise à jour des statuts
    # ⚠️ payment_status / paid ne sont plus forcés ici : ils sont déjà "paid"/True (vérifié ci-dessus).
    # L'admin ne fait que valider le contenu, jamais le paiement.
    camp.validated = True
    camp.admin_status = "approved"
    camp.status = "valide"           # Statut principal harmonisé pour mes_campagnes
    camp.is_active = True            # Rend la campagne visible au public
    camp.rejection_reason = None     # Efface d'éventuels anciens motifs de rejet

    logger.warning(
        "[ACTION ADMIN] Campagne #%d validée par admin id=%d", campaign_id, current_user.id
    )

    # 2️⃣ Gestion de la commission de parrainage (uniquement premier achat)
    annonceur = db.session.get(User, camp.user_id)
    parrain_notifie_str = ""

    if annonceur:
        # Si l'annonceur a un parrain et n'a pas encore validé sa première campagne
        if annonceur.referrer_id and not annonceur.has_launched_first_campaign:
            from models import SystemConfig, WalletTransaction
            config = SystemConfig.get_config()

            # Calcul de la récompense sur le coût de base (HT commission)
            taux_commission = (config.commission_rate or 0) / 100.0
            cout_base = camp.total_cost / (1 + taux_commission) if taux_commission > 0 else camp.total_cost
            gain_parrain = round(cout_base * ((config.referral_reward_rate or 0) / 100.0), 2)

            # Marquage du premier achat
            annonceur.has_launched_first_campaign = True

            # Recherche du parrain pour créditer son portefeuille
            parrain = db.session.get(User, annonceur.referrer_id)
            if parrain and gain_parrain > 0:
                # 🆕 Crédit réel du portefeuille du parrain
                parrain.wallet_balance = (parrain.wallet_balance or 0.0) + gain_parrain
                db.session.add(WalletTransaction(
                    user_id=parrain.id,
                    amount=gain_parrain,
                    balance_after=parrain.wallet_balance,
                    transaction_type="referral_reward",
                    description=f"Parrainage : première campagne validée de {annonceur.pseudo or annonceur.email} (campagne #{camp.id})"
                ))

                # 🆕 Notification au parrain
                db.session.add(Notification(
                    user_id=parrain.id,
                    title="Gain de parrainage crédité 🎁",
                    message=(
                        f"Vous avez gagné {gain_parrain:.0f} FCFA suite au lancement de la première "
                        f"campagne de votre filleul {annonceur.pseudo or annonceur.email} ! "
                        f"Ce montant a été ajouté à votre portefeuille."
                    ),
                    category="success",
                    link=url_for("mes_retraits"),
                    is_read=False
                ))

                parrain_notifie_str = f" (Parrain {parrain.pseudo or parrain.email} récompensé de {gain_parrain:.0f} FCFA)"
                logger.info(
                    "[PARRAINAGE] %s gagne %s FCFA grâce au premier achat de %s.",
                    parrain.pseudo, gain_parrain, annonceur.pseudo or annonceur.email
                )
        else:
            # S'il n'a pas de parrain mais que c'est sa première campagne, on valide son statut
            if not annonceur.has_launched_first_campaign:
                annonceur.has_launched_first_campaign = True

        # 🆕 Notification interne pour l'annonceur
        notif = Notification(
            user_id=annonceur.id,
            title="Campagne validée ✅",
            message=f"Votre campagne « {camp.promotion_detail or f'#{camp.id}'} » a été validée et est maintenant active !",
            category="success",
            link=url_for("mes_campagnes"),
            is_read=False
        )
        db.session.add(notif)

    db.session.commit()

    # 3️⃣ Notification WhatsApp facultative
    if annonceur and annonceur.whatsapp_number:
        message = f"Bonjour, votre campagne #{camp.id} a été VALIDÉE ✅."
        encoded = urllib.parse.quote(message)
        wa_link = f"https://wa.me/{annonceur.whatsapp_number}?text={encoded}"
        # Markup() : ce message contient du HTML construit par nous. Le gabarit
        # échappe tout le reste par défaut.
        flash(
            Markup(
                f'Campagne #{escape(camp.id)} validée avec succès !{escape(parrain_notifie_str)} ✅ '
                f'<a href="{escape(wa_link)}" target="_blank" rel="noopener" '
                f'class="btn btn-sm btn-success ms-2">📱 Message WhatsApp</a>'
            ),
            "success"
        )
    else:
        flash(f"Campagne #{camp.id} validée{parrain_notifie_str} ✅", "success")

    return redirect(url_for("admin_validate"))


# ==========================================
# 🆕 ROUTE : PARTAGE MANUEL D'UNE CAMPAGNE VALIDÉE AUX PARTAGEURS CIBLÉS
# ==========================================
@app.route("/admin/partager_campagne/<int:campaign_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def partager_campagne_admin(campaign_id):
    verifier_droits_admin("valider_campagnes")

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    if not camp.validated or camp.admin_status != "approved":
        flash("Seule une campagne validée peut être partagée aux partageurs. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    if camp.shared_to_partageurs:
        flash(f"La campagne #{camp.id} a déjà été partagée aux partageurs. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 1️⃣ Détermination des zones ciblées par la campagne
    provinces_ciblees = (
        [p.strip() for p in camp.provinces.split(",") if p.strip()]
        if camp.provinces and camp.provinces != "Toutes" else []
    )
    communes_ciblees = (
        [c.strip() for c in camp.communes.split(",") if c.strip()]
        if camp.communes else []
    )

    # 2️⃣ Recherche des partageurs confirmés dans ces zones
    query = User.query.filter(User.role == "partageur", User.is_confirmed == True)

    if communes_ciblees:
        query = query.filter(User.commune.in_(communes_ciblees))
    elif provinces_ciblees:
        query = query.filter(User.province.in_(provinces_ciblees))
    # Sinon : provinces == "Toutes" et aucune commune précisée -> tous les partageurs confirmés

    partageurs_cibles = query.all()

    if not partageurs_cibles:
        flash(f"Aucun partageur trouvé dans les zones ciblées pour la campagne #{camp.id}. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 3️⃣ Création d'une notification pour chaque partageur ciblé
    titre_notif = "🚀 Nouvelle campagne disponible !"
    message_notif = (
        f"Une nouvelle campagne « {camp.promotion_detail or 'Sans nom'} » est disponible dans votre zone. "
        f"Faites vite, d'autres partageurs de votre région peuvent en profiter avant vous ! 🔥"
    )

    for partageur in partageurs_cibles:
        notif = Notification(
            user_id=partageur.id,
            title=titre_notif,
            message=message_notif,
            category="success",
            link=url_for("dashboard_partageur"),
            is_read=False
        )
        db.session.add(notif)

    # 4️⃣ Marquage de la campagne comme partagée (empêche les doublons)
    camp.shared_to_partageurs = True
    camp.shared_at = datetime.utcnow()
    db.session.commit()

    logger.warning(
        "[ACTION ADMIN] Campagne #%d partagée à %d partageur(s) par admin id=%d",
        campaign_id, len(partageurs_cibles), current_user.id
    )

    flash(f"Campagne #{camp.id} partagée avec succès à {len(partageurs_cibles)} partageur(s) ! 📤", "success")
    return redirect(url_for("admin_validate"))   




# ==========================================
# ROUTE : AUTORISER LE REMBURSEMENT (ADMIN)
# ==========================================
@app.route("/admin/campaign/<int:campaign_id>/autoriser_remboursement", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def autoriser_remboursement_admin(campaign_id):
    verifier_droits_admin("valider_campagnes")

    camp = db.session.get(Campaign, campaign_id)
    if not camp:
        flash("Campagne introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    if camp.admin_status != "rejected" and camp.status != "rejete":
        flash("Seule une campagne rejetée peut faire l'objet d'une autorisation de remboursement. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # Activation de la possibilité pour l'annonceur de demander son remboursement
    camp.can_claim_refund = True

    # Notification interne pour l'annonceur
    db.session.add(Notification(
        user_id=camp.user_id,
        title="Remboursement disponible 💰",
        message=f"L'administration a activé l'option de remboursement pour votre campagne #{camp.id}. Vous pouvez désormais soumettre vos coordonnées.",
        category="info",
        link=url_for("mes_campagnes"),
        is_read=False
    ))

    db.session.commit()

    logger.info(
        "[ACTION ADMIN] Bouton de remboursement activé pour la campagne #%d par admin id=%d", 
        campaign_id, current_user.id
    )

    flash(f"L'option de remboursement a été activée avec succès pour l'annonceur sur la campagne #{camp.id}. 🟢", "success")
    return redirect(url_for("admin_validate"))






@app.route("/admin/confirm_user/<int:user_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def confirm_user(user_id):
    verifier_droits_admin("valider_utilisateurs")
    user = db.session.get(User, user_id)
    if not user:
        flash("Utilisateur introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    # 🆕 Seul celui qui a envoyé le message de vérification peut confirmer (le vrai admin passe toujours)
    if current_user.role != "admin" and user.contacted_by_id and user.contacted_by_id != current_user.id:
        flash("Ce dossier est pris en charge par un autre administrateur. Vous ne pouvez pas le traiter. 🚫", "danger")
        return redirect(url_for("admin_validate"))

    user.is_confirmed = True
    db.session.commit()
    logger.warning(
        "[ACTION ADMIN] Utilisateur id=%d (%s) confirmé par admin id=%d",
        user_id, user.email, current_user.id
    )

    pseudo_or_name = user.pseudo or user.email.split("@")[0]
    message = f"Bonjour {pseudo_or_name}, votre compte Pubwek a été VALIDÉ ✅."

    if user.whatsapp_number:
        numero_propre = re.sub(r"\D", "", user.whatsapp_number)
        if numero_propre:
            encoded = urllib.parse.quote(message)
            wa_link = f"https://wa.me/{numero_propre}?text={encoded}"
            flash(
                Markup(
                    'Utilisateur {email} confirmé ✅. '
                    '<a href="{link}" target="_blank" rel="noopener noreferrer" '
                    'class="btn btn-sm btn-success ms-2">📱 Message de confirmation</a>'
                ).format(email=escape(user.email), link=wa_link),
                "success"
            )
        else:
            flash(f"Utilisateur {escape(user.email)} confirmé ✅ (numéro WhatsApp invalide).", "success")
    else:
        flash(f"Utilisateur {escape(user.email)} confirmé ✅", "success")

    return redirect(url_for("admin_validate"))


@app.route("/admin/refuse_user/<int:user_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def refuse_user(user_id):
    verifier_droits_admin("valider_utilisateurs")
    user = db.session.get(User, user_id)
    if not user:
        flash("Utilisateur introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))

    # 🆕 Seul celui qui a envoyé le message de vérification peut refuser (le vrai admin passe toujours)
    if current_user.role != "admin" and user.contacted_by_id and user.contacted_by_id != current_user.id:
        flash("Ce dossier est pris en charge par un autre administrateur. Vous ne pouvez pas le traiter. 🚫", "danger")
        return redirect(url_for("admin_validate"))

    whatsapp = user.whatsapp_number
    email_log = user.email
    db.session.delete(user)
    db.session.commit()
    logger.warning(
        "[ACTION ADMIN] Utilisateur supprimé : email=%s id=%d par admin id=%d",
        email_log, user_id, current_user.id
    )

    if whatsapp:
        numero_propre = re.sub(r"\D", "", whatsapp)
        if numero_propre:
            message = "Bonjour, votre demande d'inscription Pubwek a été REFUSÉE."
            encoded = urllib.parse.quote(message)
            wa_link = f"https://wa.me/{numero_propre}?text={encoded}"
            flash(
                Markup(
                    'Utilisateur {email} supprimé ❌. '
                    '<a href="{link}" target="_blank" rel="noopener noreferrer" '
                    'class="btn btn-sm btn-outline-danger ms-2">📱 Notification WhatsApp</a>'
                ).format(email=escape(email_log), link=wa_link),
                "warning"
            )
        else:
            flash(f"Utilisateur {escape(email_log)} supprimé ✅ (numéro WhatsApp invalide).", "warning")
    else:
        flash("Utilisateur supprimé ✅", "warning")

    return redirect(url_for("admin_validate"))




@app.route("/admin/contacter_partageur/<int:user_id>", methods=["POST"])
@login_required
@limiter.limit("60 per hour")
def contacter_partageur_verification(user_id):
    verifier_droits_admin("valider_utilisateurs")
    user = db.session.get(User, user_id)
    if not user:
        flash("Utilisateur introuvable. ⚠️", "danger")
        return redirect(url_for("admin_validate"))
    if user.is_confirmed:
        flash("Cet utilisateur est déjà confirmé. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 🆕 Verrouillage : si un autre admin/sous-admin a déjà pris ce dossier, on bloque
    if user.contacted_by_id and user.contacted_by_id != current_user.id:
        contacteur = db.session.get(User, user.contacted_by_id)
        nom_contacteur = contacteur.pseudo or contacteur.email if contacteur else "un autre administrateur"
        flash(f"Ce dossier est déjà pris en charge par {escape(nom_contacteur)}. ⚠️", "warning")
        return redirect(url_for("admin_validate"))

    # 🆕 Verrouillage du dossier sur l'admin/sous-admin qui envoie le message
    user.contacted_by_id = current_user.id
    user.contacted_at = datetime.utcnow()
    db.session.commit()
    logger.info(
        "[VÉRIFICATION] Partageur id=%d contacté par admin/sous-admin id=%d",
        user_id, current_user.id
    )

    pseudo_or_name = user.pseudo or user.email.split("@")[0]
    message = (
        f"Bonjour {pseudo_or_name}, ceci est un message automatique de vérification Pubwek. "
        f"Afin de confirmer votre inscription, merci de nous répondre en précisant que vous résidez "
        f"bien en République du Bénin, ainsi que votre ville/commune de résidence. Merci."
    )

    if user.whatsapp_number:
        # 🔒 Sécurité : le numéro WhatsApp est nettoyé pour ne garder que les chiffres.
        # Il est inséré directement dans le chemin de l'URL (pas dans la query string),
        # donc s'il contenait des caractères imprévus (espaces, "/", "?", balises...),
        # cela pourrait casser ou détourner le lien. On élimine ce risque à la source.
        numero_propre = re.sub(r"\D", "", user.whatsapp_number)

        if not numero_propre:
            flash(
                f"Dossier de {escape(user.email)} verrouillé, mais le numéro WhatsApp "
                f"enregistré est invalide. ⚠️",
                "warning"
            )
            return redirect(url_for("admin_validate"))

        encoded_message = urllib.parse.quote(message)
        wa_link = f"https://wa.me/{numero_propre}?text={encoded_message}"

        # 🔒 Sécurité : on construit le HTML nous-mêmes avec des valeurs contrôlées
        # (wa_link est fait de chiffres + texte encodé en URL, donc sûr), et on
        # échappe explicitement le seul champ "libre" injecté dans le HTML : l'email.
        # Markup() dit ensuite à Jinja2 "ce texte est déjà sûr, n'échappe pas le HTML".
        flash(
            Markup(
                'Dossier de {email} verrouillé sur votre compte. '
                '<a href="{link}" target="_blank" rel="noopener noreferrer" '
                'class="btn btn-sm btn-primary ms-2">📱 Envoyer le message de vérification</a>'
            ).format(email=escape(user.email), link=wa_link),
            "info"
        )
    else:
        flash(f"Dossier de {escape(user.email)} verrouillé, mais aucun numéro WhatsApp disponible. ⚠️", "warning")

    return redirect(url_for("admin_validate"))    




@app.route("/logout", methods=["GET", "POST"])
@login_required
def logout():
    logout_user()
    flash("Déconnexion réussie 👋", "info")
    return redirect(url_for("index"))


@app.route("/dashboard/annonceur/update_name_ajax", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def update_name_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if 'company_name' in request.form:
        new_name = request.form.get("company_name", "").strip()
    elif request.is_json:
        data = request.get_json() or {}
        new_name = data.get("company_name", "").strip()
    else:
        return jsonify({"success": False, "error": "Format de requête non supporté."}), 415

    if not new_name:
        return jsonify({"success": False, "error": "Le nom ne peut pas être vide."}), 400

    if len(new_name) > 100:
        return jsonify({"success": False, "error": "Nom trop long (100 caractères max)."}), 400

    # FIX: Nettoyage XSS avec bleach
    new_name = bleach.clean(new_name)
    current_user.company_name = new_name
    db.session.commit()

    return jsonify({"success": True, "message": "Nom de l'entreprise mis à jour !", "company_name": new_name})


@app.route("/dashboard/annonceur/delete_logo_ajax", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def delete_logo_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if not current_user.logo:
        return jsonify({"success": False, "error": "Aucune image à supprimer."}), 400

    # Supprime le fichier physique s'il existe
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], current_user.logo)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError as e:
            logger.warning("Échec suppression fichier logo (user %s) : %s", current_user.id, e)

    current_user.logo = None
    db.session.commit()

    return jsonify({"success": True, "message": "Photo de profil supprimée."})


@app.route("/dashboard/annonceur/delete_cover_ajax", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def delete_cover_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if not current_user.cover_photo:
        return jsonify({"success": False, "error": "Aucune image à supprimer."}), 400

    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], current_user.cover_photo)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError as e:
            logger.warning("Échec suppression fichier couverture (user %s) : %s", current_user.id, e)

    current_user.cover_photo = None
    db.session.commit()

    return jsonify({"success": True, "message": "Image de couverture supprimée."})

@app.route("/dashboard/annonceur/update_logo_ajax", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def update_logo_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if "logo_file" not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier détecté."}), 400

    file = request.files["logo_file"]
    if file and file.filename:
        ok, err = valider_image(file)
        if not ok:
            logger.warning("Logo rejeté (user %s) : %s", current_user.id, err)
            return jsonify({"success": False, "error": f"Fichier invalide : {err}"}), 400

        filename = generer_nom_unique(file.filename)
        file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
        enregistrer_upload(filename, current_user.id, kind="logo")
        current_user.logo = filename
        db.session.commit()
        return jsonify({"success": True, "logo_url": url_for("serve_upload", filename=filename)})

    return jsonify({"success": False, "error": "Fichier invalide."}), 400



# 🔄 ROUTES AJAX (Couverture, Bio, Slogan)


@app.route("/dashboard/annonceur/update_cover_ajax", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def update_cover_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if "cover_file" not in request.files:
        return jsonify({"success": False, "error": "Aucun fichier détecté."}), 400

    file = request.files["cover_file"]
    if file and file.filename:
        ok, err = valider_image(file)
        if not ok:
            logger.warning("Couverture rejetée (user %s) : %s", current_user.id, err)
            return jsonify({"success": False, "error": f"Fichier invalide : {err}"}), 400

        filename = generer_nom_unique(file.filename)
        file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
        enregistrer_upload(filename, current_user.id, kind="cover")
        current_user.cover_photo = filename
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Image de couverture mise à jour !",
            "cover_url": url_for("serve_upload", filename=filename)
        })

    return jsonify({"success": False, "error": "Fichier invalide."}), 400


@app.route("/dashboard/annonceur/update_bio_ajax", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def update_bio_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if 'bio' in request.form:
        new_bio = request.form.get("bio", "").strip()
    elif request.is_json:
        data = request.get_json() or {}
        new_bio = data.get("bio", "").strip()
    else:
        return jsonify({"success": False, "error": "Format de requête non supporté."}), 415

    if len(new_bio) > 500:
        return jsonify({"success": False, "error": "La présentation ne peut pas dépasser 500 caractères."}), 400

    # FIX: Nettoyage XSS avec bleach
    new_bio = bleach.clean(new_bio)
    current_user.bio = new_bio
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Présentation mise à jour avec succès !",
        "bio": new_bio
    })


@app.route("/dashboard/annonceur/update_slogan_ajax", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def update_slogan_ajax():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    if 'slogan' in request.form:
        new_slogan = request.form.get("slogan", "").strip()
    elif request.is_json:
        data = request.get_json() or {}
        new_slogan = data.get("slogan", "").strip()
    else:
        return jsonify({"success": False, "error": "Format de requête non supporté."}), 415

    if len(new_slogan) > 255:
        return jsonify({"success": False, "error": "Le slogan est trop long (maximum 255 caractères)."}), 400

    # FIX: Nettoyage XSS avec bleach
    new_slogan = bleach.clean(new_slogan)
    current_user.profile_slogan = new_slogan
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Slogan par défaut mis à jour !",
        "slogan": new_slogan
    })

# 🔄 ROUTES AJAX : Repositionnement (Couverture & Logo style YouTube)

@app.route("/dashboard/annonceur/update_cover_position_ajax", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def update_cover_position_ajax():
    """Mise à jour AJAX des coordonnées CSS object-position pour la couverture."""
    if current_user.role != "annonceur":
        return jsonify({"success": False, "error": "Accès refusé"}), 403

    # Récupération des données (support Form Data et JSON)
    pos_x = request.form.get("position_x")
    pos_y = request.form.get("position_y")

    if request.is_json:
        data = request.get_json() or {}
        pos_x = pos_x or data.get("position_x")
        pos_y = pos_y or data.get("position_y")

    if pos_x is None or pos_y is None:
        return jsonify({"success": False, "error": "Coordonnées manquantes."}), 400

    try:
        # CORRECTION : Utilisation des fonctions natives min() et max() de Python
        current_user.cover_position_x = min(100.0, max(0.0, float(pos_x)))
        current_user.cover_position_y = min(100.0, max(0.0, float(pos_y)))
        
        # Enregistrement en base de données
        db.session.commit()
        
        return jsonify({
            "success": True, 
            "message": "Position de la couverture enregistrée.",
            "x": current_user.cover_position_x,
            "y": current_user.cover_position_y
        })
    except ValueError:
        return jsonify({"success": False, "error": "Format de coordonnées invalide."}), 400
    except Exception as e:
        logger.error("Erreur save position couverture user %s: %s", current_user.id, e)
        db.session.rollback()
        return jsonify({"success": False, "error": "Erreur serveur lors de l'enregistrement."}), 500


@app.route("/dashboard/annonceur/update_logo_position_ajax", methods=["POST"])
@login_required
@limiter.limit("30 per hour")
def update_logo_position_ajax():
    """Mise à jour AJAX des coordonnées CSS object-position pour le logo."""
    if current_user.role != "annonceur":
        return jsonify({"success": False, "error": "Accès refusé"}), 403

    pos_x = request.form.get("position_x")
    pos_y = request.form.get("position_y")

    if request.is_json:
        data = request.get_json() or {}
        pos_x = pos_x or data.get("position_x")
        pos_y = pos_y or data.get("position_y")

    if pos_x is None or pos_y is None:
        return jsonify({"success": False, "error": "Coordonnées manquantes."}), 400

    try:
        # CORRECTION : Utilisation des fonctions natives min() et max() de Python
        current_user.logo_position_x = min(100.0, max(0.0, float(pos_x)))
        current_user.logo_position_y = min(100.0, max(0.0, float(pos_y)))
        
        db.session.commit()
        
        return jsonify({
            "success": True, 
            "message": "Position du profil enregistrée.",
            "x": current_user.logo_position_x,
            "y": current_user.logo_position_y
        })
    except ValueError:
        return jsonify({"success": False, "error": "Format de coordonnées invalide."}), 400
    except Exception as e:
        logger.error("Erreur save position logo user %s: %s", current_user.id, e)
        db.session.rollback()
        return jsonify({"success": False, "error": "Erreur serveur."}), 500  


# ==========================================
# 🆕 ROUTES : REDIRECTION AVEC TRACKING (liens dans les statuts des partageurs)
# ==========================================
# ==========================================
# 🆕 ROUTES : REDIRECTION AVEC TRACKING (liens dans les statuts des partageurs)
# ==========================================
# =========================================================================
# 🛡️ SUIVI DES CLICS ET GARDE-FOUS ANTI-FRAUDE
# Principe retenu : TOUS les clics sont enregistrés, mais seuls ceux qui
# passent l'évaluation sont payés. Le motif de refus est conservé sur la ligne,
# ce qui permet de justifier un solde auprès d'un partageur qui le conteste.
# =========================================================================
# Motifs de refus, stockés tels quels dans CampaignClick.rejection_reason
MOTIF_CAMPAGNE_INACTIVE = "campagne_inactive"
MOTIF_ROBOT            = "robot"
MOTIF_SANS_IP          = "sans_ip"
MOTIF_AUTO_CLIC        = "auto_clic"
MOTIF_QUOTA_JOUR       = "quota_jour"
MOTIF_DOUBLON_IP       = "doublon_ip"
MOTIF_RAFALE           = "rafale"
MOTIF_PLAFOND_PARTAGE  = "plafond_partage"
MOTIF_PLAFOND_IP       = "plafond_ip"

# =========================================================================
# 🆕 LIBELLÉS LISIBLES DES MOTIFS DE REJET ANTI-FRAUDE
#
# Traduit les codes techniques stockés dans CampaignClick.rejection_reason
# en phrases compréhensibles pour l'admin, sans dupliquer la logique.
# =========================================================================
MOTIFS_REJET_LIBELLES = {
    MOTIF_CAMPAGNE_INACTIVE: "Campagne inactive au moment du clic",
    MOTIF_ROBOT: "Agent automatique détecté (robot, aperçu de lien WhatsApp...)",
    MOTIF_SANS_IP: "Adresse IP manquante",
    MOTIF_AUTO_CLIC: "Le partageur a cliqué sur son propre lien",
    MOTIF_QUOTA_JOUR: "Quota journalier de la campagne déjà atteint",
    MOTIF_DOUBLON_IP: "Cet appareil a déjà été payé sur ce partage",
    MOTIF_RAFALE: "Délai anti-rafale non respecté (clics trop rapprochés)",
    MOTIF_PLAFOND_PARTAGE: "Plafond quotidien de ce partage atteint",
    MOTIF_PLAFOND_IP: "Plafond quotidien de cette adresse IP atteint",
}

# Signatures d'agents automatiques. Le premier cas est le plus important :
# quand un partageur publie son statut, WhatsApp visite lui-même le lien pour
# fabriquer l'aperçu. Sans ce filtre, chaque publication générerait un clic
# payé qui n'a jamais été vu par un humain.
SIGNATURES_ROBOTS = (
    "whatsapp", "facebookexternalhit", "facebot", "telegrambot", "twitterbot",
    "slackbot", "discordbot", "linkedinbot", "skypeuripreview", "pinterest",
    "googlebot", "bingbot", "yandexbot", "duckduckbot", "applebot",
    "bot", "crawler", "spider", "preview", "scraper", "fetch",
    "curl", "wget", "python-requests", "httpx", "axios", "okhttp",
    "headlesschrome", "phantomjs", "puppeteer", "playwright",
)


def ip_client():
    """Adresse IP réelle du visiteur.

    ProxyFix (voir create_app) a déjà résolu X-Forwarded-For en amont, donc
    request.remote_addr est fiable. Lire l'en-tête soi-même serait une faille :
    il est envoyé par le client, qui pourrait le remplir au hasard à chaque
    requête et faire sauter toute déduplication par IP.
    """
    return (request.remote_addr or "").strip()[:45]


def est_robot(user_agent):
    """L'agent ressemble-t-il à un automate plutôt qu'à un navigateur ?"""
    if not user_agent or len(user_agent.strip()) < 10:
        return True  # un vrai navigateur envoie toujours un agent détaillé
    ua = user_agent.lower()
    return any(signature in ua for signature in SIGNATURES_ROBOTS)


def evaluer_clic(share, camp, ip, user_agent, config, maintenant=None):
    """Ce clic doit-il être rémunéré ? Retourne (payable, motif_de_refus).

    Les contrôles vont du moins coûteux au plus coûteux : on ne consulte la
    base que si les vérifications immédiates sont passées.
    """
    maintenant = maintenant or datetime.utcnow()

    # 1. La campagne doit être en cours de diffusion
    if not (camp.is_active and camp.paid and camp.validated):
        return False, MOTIF_CAMPAGNE_INACTIVE

    # 2. Écarter les automates (aperçus de lien, robots d'indexation, scripts)
    if est_robot(user_agent):
        return False, MOTIF_ROBOT

    # 3. Sans adresse IP, aucune déduplication n'est possible : on ne paie pas
    if not ip:
        return False, MOTIF_SANS_IP

    # 4. Le partageur qui clique sur son propre lien
    sharer = db.session.get(User, share.sharer_id)
    if sharer and sharer.last_seen_ip and sharer.last_seen_ip == ip:
        return False, MOTIF_AUTO_CLIC

    # 5. Quota journalier de la campagne déjà atteint
    if camp.quota_du_jour_atteint():
        return False, MOTIF_QUOTA_JOUR

    debut_journee = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)

    # 6. Cet appareil (IP + navigateur) a-t-il déjà été payé sur ce partage,
    #    depuis le début de la campagne ? Pas de fenêtre de temps : un même
    #    appareil ne rapporte qu'une seule fois pour toute la durée de la
    #    diffusion de la campagne.
    deja_paye = (
        CampaignClick.query
        .filter(
            CampaignClick.campaign_share_id == share.id,
            CampaignClick.ip == ip,
            CampaignClick.user_agent == user_agent,
            CampaignClick.is_paid.is_(True),
        )
        .first()
    )
    if deja_paye:
        return False, MOTIF_DOUBLON_IP

    # 7. Deux clics payés trop rapprochés sur le même partage
    delai = config.min_seconds_between_paid_clicks or 0
    if delai > 0:
        recent = (
            CampaignClick.query
            .filter(
                CampaignClick.campaign_share_id == share.id,
                CampaignClick.is_paid.is_(True),
                CampaignClick.clicked_at >= maintenant - timedelta(seconds=delai),
            )
            .first()
        )
        if recent:
            return False, MOTIF_RAFALE

    # 8. Plafond de clics payés pour ce partage aujourd'hui : borne le gain
    #    d'un partageur sur une campagne, même s'il change d'adresse IP.
    plafond_partage = config.max_paid_clicks_per_share_per_day or 0
    if plafond_partage > 0:
        payes_partage = (
            CampaignClick.query
            .filter(
                CampaignClick.campaign_share_id == share.id,
                CampaignClick.is_paid.is_(True),
                CampaignClick.clicked_at >= debut_journee,
            )
            .count()
        )
        if payes_partage >= plafond_partage:
            return False, MOTIF_PLAFOND_PARTAGE

    # 9. Plafond par adresse IP, toutes campagnes confondues : borne une
    #    machine qui ferait le tour de toutes les campagnes disponibles.
    plafond_ip = config.max_paid_clicks_per_ip_per_day or 0
    if plafond_ip > 0:
        payes_ip = (
            CampaignClick.query
            .filter(
                CampaignClick.ip == ip,
                CampaignClick.is_paid.is_(True),
                CampaignClick.clicked_at >= debut_journee,
            )
            .count()
        )
        if payes_ip >= plafond_ip:
            return False, MOTIF_PLAFOND_IP

    return True, None


def recompense_pour(camp, config):
    """Montant reversé au partageur pour un clic, selon le type de contenu."""
    if camp.media_type == "video":
        return config.reward_per_click_video or 0.0
    if camp.media_type == "photo":
        return config.reward_per_click_photo or 0.0
    return config.reward_per_click_text or 0.0

def montant_en_attente_validation(user):
    """Montant total des clics valides déjà obtenus par ce partageur, mais
    pas encore crédités à son portefeuille retirable, car la preuve de fin
    de journée du jour concerné n'a pas encore été validée par un admin.

    Additionne, pour tous les partages de l'utilisateur, les clics payables
    non encore rémunérés (rewarded_at IS NULL), valorisés au tarif du type
    de contenu de la campagne correspondante (vidéo / photo / texte).
    """
    config = SystemConfig.get_config()

    lignes = (
        db.session.query(
            Campaign.media_type,
            func.count(CampaignClick.id)
        )
        .join(CampaignShare, CampaignShare.id == CampaignClick.campaign_share_id)
        .join(Campaign, Campaign.id == CampaignShare.campaign_id)
        .filter(
            CampaignShare.sharer_id == user.id,
            CampaignClick.is_paid.is_(True),
            CampaignClick.rewarded_at.is_(None),
        )
        .group_by(Campaign.media_type)
        .all()
    )

    total = 0.0
    for media_type, nb in lignes:
        if media_type == "video":
            tarif = config.reward_per_click_video or 0.0
        elif media_type == "photo":
            tarif = config.reward_per_click_photo or 0.0
        else:
            tarif = config.reward_per_click_text or 0.0
        total += tarif * nb

    return total





def preuve_jour_validee(campaign_share_id, day_number):
    """Les deux preuves (début + fin) de ce jour sont-elles validées ?"""
    validees = (
        CampaignShareProof.query
        .filter(
            CampaignShareProof.campaign_share_id == campaign_share_id,
            CampaignShareProof.day_number == day_number,
            CampaignShareProof.status == "validee",
        )
        .count()
    )
    return validees >= 2


def crediter_clics_du_jour(share, day_number):
    """Verse la récompense de tous les clics payables et non encore
    rémunérés de ce partage, pour ce jour de diffusion. Appelée uniquement
    quand les deux preuves du jour viennent d'être validées.
    Retourne (nombre_de_clics_credites, montant_total_verse).
    """
    camp = share.campaign
    config = SystemConfig.get_config()
    clics = (
        CampaignClick.query
        .filter(
            CampaignClick.campaign_share_id == share.id,
            CampaignClick.day_number == day_number,
            CampaignClick.is_paid.is_(True),
            CampaignClick.rewarded_at.is_(None),
        )
        .all()
    )
    if not clics:
        return 0, 0.0

    sharer = db.session.get(User, share.sharer_id)
    maintenant = datetime.utcnow()
    total = 0.0

    for click in clics:
        recompense = recompense_pour(camp, config)
        if sharer and recompense > 0:
            sharer.wallet_balance = (sharer.wallet_balance or 0.0) + recompense
            db.session.add(WalletTransaction(
                user_id=sharer.id,
                amount=recompense,
                balance_after=sharer.wallet_balance,
                transaction_type="click_reward",
                campaign_click_id=click.id,
                description=(
                    f"Clic généré sur la campagne #{camp.id} (jour {day_number}) "
                    f"({camp.promotion_detail or camp.promotion_type})"
                ),
            ))
            total += recompense
        click.rewarded_at = maintenant

    return len(clics), total




def enregistrer_clic(share, camp, link_type):
    """Enregistre un clic. Le clic est marqué payable ou non selon les
    garde-fous anti-fraude, mais l'argent n'est versé qu'après validation,
    par un admin, des preuves (captures d'écran) du jour de diffusion
    concerné — voir crediter_clics_du_jour().
    Ne lève jamais : le visiteur doit être redirigé quoi qu'il arrive, un
    incident de journalisation ne doit pas casser le parcours du client final.
    """
    try:
        config = SystemConfig.get_config()
        # Le quota du jour doit être recalculé avant toute décision
        if camp.is_active and camp.paid and camp.validated:
            camp.verifier_et_reset_quota_journalier()
        ip = ip_client()
        user_agent = (request.headers.get("User-Agent") or "")[:255]
        payable, motif = evaluer_clic(share, camp, ip, user_agent, config)
        jour = camp.jour_diffusion_campagne()
        click = CampaignClick(
            campaign_share_id=share.id,
            link_type=link_type,
            ip=ip or None,
            user_agent=user_agent,
            is_paid=payable,
            rejection_reason=motif,
            day_number=jour,
        )
        db.session.add(click)
        db.session.flush()  # pour disposer de click.id
        if payable:
            camp.whatsapp_views = (camp.whatsapp_views or 0) + 1
            camp.views_today = (camp.views_today or 0) + 1
            # Si les preuves du jour sont déjà validées (clic tardif après
            # validation admin), on crédite immédiatement ce clic-là.
            if preuve_jour_validee(share.id, jour):
                sharer = db.session.get(User, share.sharer_id)
                recompense = recompense_pour(camp, config)
                if sharer and recompense > 0:
                    sharer.wallet_balance = (sharer.wallet_balance or 0.0) + recompense
                    db.session.add(WalletTransaction(
                        user_id=sharer.id,
                        amount=recompense,
                        balance_after=sharer.wallet_balance,
                        transaction_type="click_reward",
                        campaign_click_id=click.id,
                        description=(
                            f"Clic généré sur la campagne #{camp.id} (jour {jour}) "
                            f"({camp.promotion_detail or camp.promotion_type})"
                        ),
                    ))
                    click.rewarded_at = datetime.utcnow()
            # Le quota du jour vient peut-être d'être atteint avec ce clic
            if camp.quota_du_jour_atteint():
                camp.daily_quota_paused = True
                _notifier_partageurs_quota_atteint(camp)
            # Objectif global de la campagne atteint → diffusion terminée
            if camp.target_whatsapp_views and camp.whatsapp_views >= camp.target_whatsapp_views:
                camp.is_active = False
                camp.status = "terminee"
        elif motif == MOTIF_QUOTA_JOUR:
            camp.daily_quota_paused = True
            if not camp.daily_quota_alert_sent:
                _notifier_partageurs_quota_atteint(camp)
        elif motif in (MOTIF_AUTO_CLIC, MOTIF_PLAFOND_PARTAGE, MOTIF_PLAFOND_IP):
            # Motifs qui traduisent un comportement anormal, pas un simple
            # doublon : on les journalise pour pouvoir enquêter.
            logger.warning(
                "[ANTI-FRAUDE] Clic non rémunéré (%s) — partage=%d campagne=%d ip=%s",
                motif, share.id, camp.id, ip or "inconnue"
            )
        db.session.commit()
    except Exception as e:
        logger.error(
            "Erreur enregistrement clic %s (partage %s) : %s",
            link_type, getattr(share, "id", "?"), e
        )
        db.session.rollback()


def preuve_jour_validee(campaign_share_id, day_number):
    """La preuve de fin de journée de ce jour est-elle validée ?

    Une seule preuve est désormais exigée par jour (la capture de fin de
    journée) : sa validation par un admin suffit à autoriser le crédit des
    clics de ce jour au portefeuille retirable du partageur.
    """
    validee = (
        CampaignShareProof.query
        .filter(
            CampaignShareProof.campaign_share_id == campaign_share_id,
            CampaignShareProof.day_number == day_number,
            CampaignShareProof.status == "validee",
        )
        .first()
    )
    return validee is not None


@app.route("/partageur/preuve/<int:share_id>/<int:day_number>", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def envoyer_preuve_partage(share_id, day_number):
    """Envoi de la capture de fin de journée, pour un jour de diffusion précis.

    Une seule preuve est désormais exigée par jour (fin de journée). Le
    partageur peut encore régulariser un jour passé tant qu'il reste dans la
    fenêtre de rattrapage (Campaign.FENETRE_RATTRAPAGE_HEURES après la fin de
    ce jour) ; passé ce délai, les clics de ce jour sont définitivement perdus
    et l'envoi est refusé.
    """
    if current_user.role != "partageur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    share = CampaignShare.query.filter_by(id=share_id, sharer_id=current_user.id).first()
    if not share:
        abort(404)

    camp = share.campaign
    if not (camp.is_active and camp.paid and camp.validated):
        flash("Cette campagne n'est plus active. ⚠️", "warning")
        return redirect(url_for("dashboard_partageur"))

    jour_courant = camp.jour_diffusion_campagne()

    # Le jour visé doit être un jour déjà entamé de cette campagne (jamais un
    # jour futur), et encore dans sa fenêtre de rattrapage.
    if day_number < 1 or day_number > jour_courant:
        flash("Jour de diffusion invalide. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    if not camp.jour_encore_reclamable(day_number):
        flash(
            f"Le délai pour envoyer la preuve du jour {day_number} est dépassé "
            f"({camp.FENETRE_RATTRAPAGE_HEURES}h après la fin de ce jour). "
            f"Les clics de ce jour sont malheureusement perdus. ⚠️",
            "danger"
        )
        return redirect(url_for("dashboard_partageur"))

    fichier = request.files.get("preuve")
    if not fichier or not fichier.filename:
        flash("Veuillez sélectionner une capture d'écran. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    ok, err = valider_image(fichier)
    if not ok:
        flash(f"Image refusée : {err}", "danger")
        return redirect(url_for("dashboard_partageur"))

    filename = generer_nom_unique(fichier.filename)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    fichier.save(path)
    enregistrer_upload(filename, current_user.id, kind="preuve_partage")

    preuve = CampaignShareProof.query.filter_by(
        campaign_share_id=share.id, day_number=day_number, proof_type="fin"
    ).first()

    if preuve and preuve.status == "validee":
        flash("Cette preuve a déjà été validée, impossible de la remplacer. ⚠️", "warning")
        return redirect(url_for("dashboard_partageur"))

    if preuve:
        # Renvoi après rejet : on écrase l'ancien fichier et on repasse en attente
        preuve.filename = filename
        preuve.status = "en_attente"
        preuve.submitted_at = datetime.utcnow()
        preuve.reviewed_at = None
        preuve.reviewed_by_id = None
        preuve.rejection_reason = None
    else:
        preuve = CampaignShareProof(
            campaign_share_id=share.id,
            day_number=day_number,
            proof_type="fin",
            filename=filename,
        )
        db.session.add(preuve)

    db.session.commit()
    flash(f"Preuve du jour {day_number} envoyée, en attente de validation. ✅", "success")
    return redirect(url_for("dashboard_partageur"))


def crediter_clics_du_jour(share, day_number):
    """Verse la récompense de tous les clics payables et non encore
    rémunérés de ce partage, pour ce jour de diffusion. Appelée uniquement
    quand la preuve de fin de journée de ce jour vient d'être validée.
    Retourne (nombre_de_clics_credites, montant_total_verse).
    """
    camp = share.campaign
    config = SystemConfig.get_config()
    clics = (
        CampaignClick.query
        .filter(
            CampaignClick.campaign_share_id == share.id,
            CampaignClick.day_number == day_number,
            CampaignClick.is_paid.is_(True),
            CampaignClick.rewarded_at.is_(None),
        )
        .all()
    )
    if not clics:
        return 0, 0.0

    sharer = db.session.get(User, share.sharer_id)
    maintenant = datetime.utcnow()
    total = 0.0

    for click in clics:
        recompense = recompense_pour(camp, config)
        if sharer and recompense > 0:
            sharer.wallet_balance = (sharer.wallet_balance or 0.0) + recompense
            db.session.add(WalletTransaction(
                user_id=sharer.id,
                amount=recompense,
                balance_after=sharer.wallet_balance,
                transaction_type="click_reward",
                campaign_click_id=click.id,
                description=(
                    f"Clic généré sur la campagne #{camp.id} (jour {day_number}) "
                    f"({camp.promotion_detail or camp.promotion_type})"
                ),
            ))
            total += recompense
        click.rewarded_at = maintenant

    return len(clics), total


@app.route("/admin/preuve/<int:proof_id>/<decision>", methods=["POST"])
@login_required
def valider_preuve_partage(proof_id, decision):
    if current_user.role != "admin":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    if decision not in ("valider", "rejeter"):
        abort(404)

    preuve = CampaignShareProof.query.get_or_404(proof_id)

    if preuve.status != "en_attente":
        flash("Cette preuve a déjà été traitée. ⚠️", "warning")
        return redirect(url_for("admin_preuves_partage"))

    preuve.reviewed_at = datetime.utcnow()
    preuve.reviewed_by_id = current_user.id

    share = db.session.get(CampaignShare, preuve.campaign_share_id)
    camp = share.campaign if share else None

    if decision == "valider":
        preuve.status = "validee"
        db.session.commit()

        # Une seule preuve est désormais exigée par jour : sa validation
        # déclenche systématiquement le crédit des clics de ce jour.
        nb, montant = crediter_clics_du_jour(share, preuve.day_number)
        db.session.commit()

        # 🆕 Notification verte au partageur : ses clics de ce jour précis
        # viennent d'être ajoutés à son portefeuille disponible au retrait.
        if share:
            nom_campagne = camp.promotion_detail or camp.promotion_type if camp else "campagne"
            db.session.add(Notification(
                user_id=share.sharer_id,
                title=f"Clics du jour {preuve.day_number} validés ✅",
                message=(
                    f"Votre preuve du jour {preuve.day_number} pour la campagne "
                    f"« {nom_campagne} » a été validée. {nb} clic(s) pour {montant:.0f} FCFA "
                    f"ont été ajoutés à votre portefeuille disponible. 💰"
                ),
                category="success",
                link=url_for("mes_retraits"),
                is_read=False
            ))
            db.session.commit()

        flash(f"Preuve validée. {nb} clic(s) crédité(s) pour {montant:.0f} FCFA. ✅", "success")
    else:
        motif = request.form.get("motif", "").strip()
        preuve.status = "rejetee"
        preuve.rejection_reason = bleach.clean(motif) if motif else "Non conforme"
        db.session.commit()

        # 🆕 Le partageur doit être averti du rejet pour pouvoir renvoyer une
        # preuve avant l'expiration de la fenêtre de rattrapage, sans quoi il
        # perdrait ses clics du jour sans même en être informé.
        if share:
            delai = camp.FENETRE_RATTRAPAGE_HEURES if camp else 48
            db.session.add(Notification(
                user_id=share.sharer_id,
                title=f"Preuve du jour {preuve.day_number} rejetée ⚠️",
                message=(
                    f"Votre preuve du jour {preuve.day_number} a été rejetée. "
                    f"Motif : {preuve.rejection_reason}. Veuillez en renvoyer une nouvelle "
                    f"avant l'expiration du délai de {delai}h après la fin de ce jour, "
                    f"sinon les clics de cette journée seront définitivement perdus."
                ),
                category="warning",
                link=url_for("dashboard_partageur"),
                is_read=False
            ))
            db.session.commit()

        flash("Preuve rejetée. Le partageur devra en renvoyer une nouvelle. ⚠️", "warning")

    return redirect(url_for("admin_preuves_partage"))


@app.route("/admin/preuves_partage")
@login_required
def admin_preuves_partage():
    if current_user.role != "admin":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    campaign_id = request.args.get("campaign_id", type=int)

    query = CampaignShareProof.query.filter_by(status="en_attente")
    if campaign_id:
        query = query.join(CampaignShare).filter(CampaignShare.campaign_id == campaign_id)

    preuves = query.order_by(CampaignShareProof.submitted_at.asc()).all()

    camp_filtree = db.session.get(Campaign, campaign_id) if campaign_id else None

    return render_template("preuves_partage.html", preuves=preuves, camp_filtree=camp_filtree)




@app.route("/t/<token>/whatsapp")
def tracking_redirect_whatsapp(token):
    """Redirige le visiteur vers la conversation WhatsApp de l'annonceur."""
    share = CampaignShare.query.filter_by(tracking_token=token).first()
    if not share:
        abort(404)

    camp = share.campaign
    if not camp or not camp.whatsapp_number:
        abort(404)

    # La destination est calculée d'abord : le visiteur ne doit jamais attendre
    numero = re.sub(r"[^0-9]", "", camp.whatsapp_number)
    message = urllib.parse.quote(
        f"Bonjour, je suis intéressé(e) par : {camp.promotion_detail or camp.promotion_type}"
    )
    lien_final = f"https://wa.me/{numero}?text={message}"

    enregistrer_clic(share, camp, "whatsapp")
    return redirect(lien_final)


def _notifier_partageurs_quota_atteint(camp):
    """
    Notifie tous les partageurs actifs d'une campagne que le quota de clics du jour
    est atteint, afin qu'ils puissent retirer leur statut WhatsApp s'ils le souhaitent.
    Ils ne sont jamais rémunérés pour les clics au-delà du quota, donc aucune obligation.
    """
    try:
        shares = CampaignShare.query.filter_by(campaign_id=camp.id).all()
        for s in shares:
            notif = Notification(
                user_id=s.sharer_id,
                title="Quota du jour atteint 🎯",
                message=(
                    f"Les clics prévus aujourd'hui pour la campagne "
                    f"« {camp.promotion_detail or camp.promotion_type} » sont atteints. "
                    f"Vous pouvez retirer votre statut WhatsApp si vous le souhaitez — "
                    f"vous ne serez pas rémunéré(e) au-delà de ce quota. La diffusion reprendra demain."
                ),
                category="warning",
                link=url_for("instructions_partage", campaign_id=camp.id),
                is_read=False
            )
            db.session.add(notif)
        camp.daily_quota_alert_sent = True
        logger.info("[QUOTA] Alerte quota envoyée à %d partageur(s) pour campagne #%d", len(shares), camp.id)
    except Exception as e:
        logger.error("Erreur notification quota atteint (campagne %d) : %s", camp.id, e)


@app.route("/t/<token>/site")
def tracking_redirect_site(token):
    """Redirige le visiteur vers le site web de l'annonceur."""
    share = CampaignShare.query.filter_by(tracking_token=token).first()
    if not share:
        abort(404)

    camp = share.campaign
    if not camp or not camp.website_url:
        abort(404)

    # Un clic vers le site vaut un clic WhatsApp : même quota, même rémunération
    enregistrer_clic(share, camp, "website")
    return redirect(camp.website_url)

# ==========================================
# 🆕 ROUTE : DEMANDE DE RETRAIT (PARTAGEUR)
# ==========================================
@app.route("/partageur/retrait/demander", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def demander_retrait():
    if current_user.role != "partageur":
        flash("Accès réservé aux partageurs. 🚫", "danger")
        return redirect(url_for("index"))

    from models import SystemConfig, WithdrawalRequest, WalletTransaction
    config = SystemConfig.get_config()

    montant_raw = request.form.get("amount")
    payout_channel = request.form.get("payout_channel", "").strip()
    payout_phone = request.form.get("payout_phone", "").strip()

    # 1️⃣ Validation du montant
    try:
        montant = float(montant_raw)
    except (ValueError, TypeError):
        flash("Montant invalide. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    if montant <= 0:
        flash("Le montant du retrait doit être positif. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    if montant < config.minimum_withdrawal_amount:
        flash(f"Le montant minimum de retrait est de {config.minimum_withdrawal_amount:.0f} FCFA. ⚠️", "warning")
        return redirect(url_for("dashboard_partageur"))

    # 2️⃣ Validation des coordonnées de réception
    if payout_channel not in ["MTN Mobile Money", "Moov Money", "Celtiis Cash", "Wave"]:
        flash("Moyen de réception invalide. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    if not re.match(r"^\+?[0-9]{7,15}$", payout_phone):
        flash("Numéro de téléphone invalide. Utilisez un format international (ex: +22960000000).", "danger")
        return redirect(url_for("dashboard_partageur"))

    # 3️⃣ Verrouillage de la ligne utilisateur pour toute la durée de l'opération.
    #
    # Sans ce verrou, deux demandes simultanées (double-clic, ou deux workers
    # gunicorn) pouvaient toutes les deux passer la vérification de solde avant
    # que l'une ne débite : le même argent partait deux fois. Le verrou les met
    # en file d'attente, la seconde voit le solde déjà débité.
    #
    # SELECT ... FOR UPDATE est actif sur PostgreSQL (la base de production) et
    # sans effet sur SQLite, où l'écriture est de toute façon sérialisée.
    partageur = (
        db.session.query(User)
        .filter_by(id=current_user.id)
        .with_for_update()
        .first()
    )
    if partageur is None:
        flash("Compte introuvable. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    current_balance = partageur.wallet_balance or 0.0
    if montant > current_balance:
        flash(f"Solde insuffisant. Votre solde disponible est de {current_balance:.0f} FCFA. ⚠️", "danger")
        return redirect(url_for("dashboard_partageur"))

    # 4️⃣ Vérification qu'il n'y a pas déjà une demande en cours (évite le double retrait du même argent)
    demande_en_cours = WithdrawalRequest.query.filter_by(
        user_id=current_user.id, status="pending"
    ).first()
    if demande_en_cours:
        flash("Vous avez déjà une demande de retrait en attente de traitement. Veuillez patienter. ⏳", "warning")
        return redirect(url_for("dashboard_partageur"))

    try:
        # 5️⃣ Débit immédiat du portefeuille (le montant est "réservé" pour ce retrait)
        partageur.wallet_balance = current_balance - montant

        db.session.add(WalletTransaction(
            user_id=current_user.id,
            amount=-montant,
            balance_after=partageur.wallet_balance,
            transaction_type="withdrawal",
            description=f"Demande de retrait vers {payout_channel} ({payout_phone})"
        ))

        # 6️⃣ Création de la demande de retrait
        demande = WithdrawalRequest(
            user_id=current_user.id,
            amount=montant,
            status="pending",
            payout_channel=payout_channel,
            payout_phone=payout_phone,
        )
        db.session.add(demande)
        db.session.commit()

        # 7️⃣ Notification aux admins
        admins = User.query.filter_by(role="admin").all()
        for admin in admins:
            db.session.add(Notification(
                user_id=admin.id,
                title="Nouvelle demande de retrait 💰",
                message=f"{current_user.pseudo or current_user.email} demande un retrait de {montant:.0f} FCFA.",
                category="info",
                link=url_for("admin_retraits"),
                is_read=False
            ))
        db.session.commit()

        logger.info(
            "[RETRAIT] Demande créée par user_id=%d, montant=%.2f, id_demande=%d",
            current_user.id, montant, demande.id
        )

        flash(f"Votre demande de retrait de {montant:.0f} FCFA a été envoyée. Elle sera traitée sous peu. ✅", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur lors de la demande de retrait (user %d) : %s", current_user.id, e)
        flash("Une erreur est survenue lors de votre demande. Réessayez. ⚠️", "danger")

    return redirect(url_for("dashboard_partageur"))

# ==========================================
# 🆕 ROUTE ADMIN : LISTE DES DEMANDES DE RETRAIT
# ==========================================
@app.route("/admin/retraits")
@login_required
def admin_retraits():
    verifier_droits_admin("gerer_retraits")

    from models import WithdrawalRequest

    demandes = WithdrawalRequest.query.order_by(WithdrawalRequest.requested_at.desc()).all()

    # On associe manuellement l'utilisateur à chaque demande pour l'affichage
    for d in demandes:
        d.demandeur = db.session.get(User, d.user_id)

    en_attente = [d for d in demandes if d.status == "pending"]
    traitees = [d for d in demandes if d.status != "pending"]

    return render_template(
        "admin_retraits.html",
        en_attente=en_attente,
        traitees=traitees
    )

# ==========================================
# 🆕 ROUTE ADMIN : CONFIRMER UN RETRAIT PAYÉ MANUELLEMENT
# ==========================================
@app.route("/admin/retraits/<int:withdrawal_id>/payer-manuel", methods=["POST"])
@login_required
def confirmer_retrait_manuel(withdrawal_id):
    verifier_droits_admin("gerer_retraits")

    from models import WithdrawalRequest

    # Verrou sur la demande : deux clics rapprochés de l'admin (ou deux workers)
    # pourraient sinon la traiter deux fois — donc rembourser ou payer en double.
    demande = (
        db.session.query(WithdrawalRequest)
        .filter_by(id=withdrawal_id)
        .with_for_update()
        .first()
    )
    if not demande:
        flash("Demande introuvable. ⚠️", "danger")
        return redirect(url_for("admin_retraits"))

    if demande.status != "pending":
        flash("Cette demande a déjà été traitée. ⚠️", "warning")
        return redirect(url_for("admin_retraits"))

    # 🆕 Preuve de paiement OBLIGATOIRE — capture d'écran du virement effectué
    proof_file = request.files.get("proof_file")
    if not proof_file or not proof_file.filename:
        flash("Une preuve de paiement (capture d'écran) est obligatoire pour confirmer un retrait manuel. ⚠️", "danger")
        return redirect(url_for("admin_retraits"))

    ok, err = valider_image(proof_file)
    if not ok:
        flash(f"Preuve refusée : {err}", "danger")
        return redirect(url_for("admin_retraits"))

    try:
        filename = generer_nom_unique(proof_file.filename)
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        proof_file.save(path)

        demande.status = "paid"
        demande.payment_method = "manual"
        demande.proof_file = filename
        demande.processed_by = current_user.id
        demande.processed_at = datetime.utcnow()

        db.session.commit()

        # Notification au partageur
        db.session.add(Notification(
            user_id=demande.user_id,
            title="Retrait effectué ✅",
            message=(
                f"Votre retrait de {demande.amount:.0f} FCFA a été crédité sur votre "
                f"{demande.payout_channel} ({demande.payout_phone}). Merci pour vos efforts sur Pubwek ! 🎉"
            ),
            category="success",
            link=url_for("mes_retraits"),
            is_read=False
        ))
        db.session.commit()

        logger.info(
            "[RETRAIT] Demande #%d payée manuellement par admin id=%d",
            demande.id, current_user.id
        )
        flash("Retrait confirmé et preuve enregistrée. Le partageur a été notifié. ✅", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur confirmation retrait manuel #%d : %s", withdrawal_id, e)
        flash("Une erreur est survenue. Réessayez. ⚠️", "danger")

    return redirect(url_for("admin_retraits"))


# ==========================================
# 🆕 ROUTE ADMIN : REFUSER UNE DEMANDE DE RETRAIT
# ==========================================
@app.route("/admin/retraits/<int:withdrawal_id>/refuser", methods=["POST"])
@login_required
def refuser_retrait(withdrawal_id):
    verifier_droits_admin("gerer_retraits")

    from models import WithdrawalRequest, WalletTransaction

    # Verrou sur la demande : deux clics rapprochés de l'admin (ou deux workers)
    # pourraient sinon la traiter deux fois — donc rembourser ou payer en double.
    demande = (
        db.session.query(WithdrawalRequest)
        .filter_by(id=withdrawal_id)
        .with_for_update()
        .first()
    )
    if not demande:
        flash("Demande introuvable. ⚠️", "danger")
        return redirect(url_for("admin_retraits"))

    if demande.status != "pending":
        flash("Cette demande a déjà été traitée. ⚠️", "warning")
        return redirect(url_for("admin_retraits"))

    motif = request.form.get("admin_note", "").strip()
    if not motif:
        flash("Veuillez indiquer un motif de refus. ⚠️", "warning")
        return redirect(url_for("admin_retraits"))

    try:
        # 🆕 Recrédit intégral du portefeuille du partageur
        partageur = db.session.get(User, demande.user_id)
        if partageur:
            partageur.wallet_balance = (partageur.wallet_balance or 0.0) + demande.amount
            db.session.add(WalletTransaction(
                user_id=partageur.id,
                amount=demande.amount,
                balance_after=partageur.wallet_balance,
                transaction_type="withdrawal_refund",
                description=f"Remboursement suite au refus de la demande de retrait #{demande.id}"
            ))

        demande.status = "rejected"
        demande.admin_note = bleach.clean(motif)
        demande.processed_by = current_user.id
        demande.processed_at = datetime.utcnow()

        db.session.commit()

        db.session.add(Notification(
            user_id=demande.user_id,
            title="Demande de retrait refusée ⚠️",
            message=f"Votre demande de retrait de {demande.amount:.0f} FCFA a été refusée. Motif : {motif}. Le montant a été recrédité sur votre portefeuille.",
            category="warning",
            link=url_for("mes_retraits"),
            is_read=False
        ))
        db.session.commit()

        logger.info("[RETRAIT] Demande #%d refusée par admin id=%d", demande.id, current_user.id)
        flash("Demande refusée. Le montant a été recrédité au partageur. ✅", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur refus retrait #%d : %s", withdrawal_id, e)
        flash("Une erreur est survenue. Réessayez. ⚠️", "danger")

    return redirect(url_for("admin_retraits"))              
          

# ==========================================
# 🆕 ROUTE : MES RETRAITS (ESPACE PARTAGEUR)
# ==========================================
@app.route("/partageur/mes-retraits")
@login_required
def mes_retraits():
    if current_user.role != "partageur":
        flash("Accès réservé aux partageurs. 🚫", "danger")
        return redirect(url_for("index"))

    from models import WithdrawalRequest, WalletTransaction, SystemConfig
    config = SystemConfig.get_config()

    demandes = (
        WithdrawalRequest.query
        .filter_by(user_id=current_user.id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .all()
    )

    # Historique complet des mouvements du portefeuille (crédits clics/parrainage + débits retraits)
    mouvements = (
        WalletTransaction.query
        .filter_by(user_id=current_user.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(100)
        .all()
    )

    # Total déjà retiré (uniquement les retraits effectivement payés)
    total_retire = sum(d.amount for d in demandes if d.status == "paid")

    return render_template(
        "mes_retraits.html",
        demandes=demandes,
        mouvements=mouvements,
        total_retire=total_retire,
        solde_actuel=current_user.wallet_balance or 0.0,
        minimum_retrait=config.minimum_withdrawal_amount
    )  

# ==========================================
# 🆕 UTILITAIRE : GÉNÉRATION PDF À PARTIR D'UN TEMPLATE HTML
# ==========================================


def generer_pdf_depuis_template(template_name, contexte, nom_fichier):
    """
    Génère un PDF à partir d'un template Jinja2 et le retourne en téléchargement.
    """
    html_rendu = render_template(template_name, **contexte)

    buffer = BytesIO()
    resultat = pisa.CreatePDF(html_rendu, dest=buffer, encoding="utf-8")

    if resultat.err:
        logger.error("Erreur génération PDF (%s) : %d erreur(s)", nom_fichier, resultat.err)
        raise ValueError("Erreur lors de la génération du PDF.")

    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={nom_fichier}"
    return response



# ==========================================
# 🆕 GESTION DES SOUS-ADMINS — RÉSERVÉ AU SUPER-ADMIN UNIQUEMENT
# ==========================================

PERMISSIONS_DISPONIBLES = {
    "valider_utilisateurs": "Valider / refuser les inscriptions",
    "valider_campagnes": "Valider / refuser les campagnes",
    "suivre_campagnes": "Suivre la progression des campagnes",
    "gerer_retraits": "Traiter les demandes de retrait",
    "voir_transactions": "Consulter le registre des transactions",
    "configurer_tarifs": "Configurer les tarifs et commissions",
    "configurer_video": "Configurer l'option génération vidéo",
}


def verifier_super_admin_strict():
    """
    Vérifie que l'utilisateur est le VRAI admin (role == 'admin').
    Un sous-admin, même avec toutes les permissions, ne peut JAMAIS accéder
    à la gestion des sous-admins — sécurité contre l'auto-promotion.
    """
    if current_user.role != "admin":
        logger.warning(
            "Tentative d'accès à la gestion des sous-admins par un compte non autorisé id=%s (role=%s).",
            current_user.id, current_user.role
        )
        abort(403)


@app.route("/admin/sous-admins")
@login_required
def admin_gestion_sous_admins():
    verifier_super_admin_strict()

    sous_admins = User.query.filter_by(role="sous_admin").order_by(User.created_at.desc()).all()

    for s in sous_admins:
        s.permissions_liste = s.get_permissions_list()
        s.createur = db.session.get(User, s.created_by_admin_id) if s.created_by_admin_id else None

    return render_template(
        "admin_sous_admins.html",
        sous_admins=sous_admins,
        permissions_disponibles=PERMISSIONS_DISPONIBLES
    )


@app.route("/admin/sous-admins/creer", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def creer_sous_admin():
    verifier_super_admin_strict()

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    pseudo = request.form.get("pseudo", "").strip()
    permissions_cochees = request.form.getlist("permissions[]")

    # Validation de base
    if not email or not password:
        flash("Email et mot de passe sont obligatoires. ⚠️", "danger")
        return redirect(url_for("admin_gestion_sous_admins"))

    if len(password) < LONGUEUR_MIN_MOT_DE_PASSE:
        flash(
            f"Le mot de passe doit contenir au moins "
            f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères. ⚠️", "danger"
        )
        return redirect(url_for("admin_gestion_sous_admins"))

    if User.query.filter_by(email=email).first():
        flash("Un compte existe déjà avec cet email. ⚠️", "danger")
        return redirect(url_for("admin_gestion_sous_admins"))

    # On ne garde que les permissions réellement valides (sécurité contre l'injection de valeurs arbitraires)
    permissions_valides = [p for p in permissions_cochees if p in PERMISSIONS_DISPONIBLES]

    if not permissions_valides:
        flash("Veuillez attribuer au moins une permission au sous-admin. ⚠️", "warning")
        return redirect(url_for("admin_gestion_sous_admins"))

    try:
        nouveau_sous_admin = User(
            email=email,
            password_hash=bcrypt.generate_password_hash(password).decode("utf-8"),
            role="sous_admin",
            pseudo=pseudo or email.split("@")[0],
            is_confirmed=True,  # Un sous-admin créé par le super-admin est confirmé d'office
            has_accepted_terms=True,
            admin_permissions=",".join(permissions_valides),
            created_by_admin_id=current_user.id,
            is_active_admin=True,
        )
        db.session.add(nouveau_sous_admin)
        db.session.commit()

        logger.warning(
            "[ACTION SUPER-ADMIN] Sous-admin créé : %s (permissions: %s) par admin id=%d",
            email, ", ".join(permissions_valides), current_user.id
        )
        flash(f"Sous-admin {email} créé avec succès. ✅", "success")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur création sous-admin : %s", e)
        flash("Une erreur est survenue lors de la création. ⚠️", "danger")

    return redirect(url_for("admin_gestion_sous_admins"))


@app.route("/admin/sous-admins/<int:sous_admin_id>/modifier-permissions", methods=["POST"])
@login_required
@limiter.limit("40 per hour")
def modifier_permissions_sous_admin(sous_admin_id):
    verifier_super_admin_strict()

    sous_admin = db.session.get(User, sous_admin_id)
    if not sous_admin or sous_admin.role != "sous_admin":
        flash("Sous-admin introuvable. ⚠️", "danger")
        return redirect(url_for("admin_gestion_sous_admins"))

    permissions_cochees = request.form.getlist("permissions[]")
    permissions_valides = [p for p in permissions_cochees if p in PERMISSIONS_DISPONIBLES]

    sous_admin.admin_permissions = ",".join(permissions_valides) if permissions_valides else None
    db.session.commit()

    logger.warning(
        "[ACTION SUPER-ADMIN] Permissions du sous-admin id=%d modifiées (%s) par admin id=%d",
        sous_admin_id, ", ".join(permissions_valides) or "aucune", current_user.id
    )
    flash(f"Permissions de {sous_admin.email} mises à jour. ✅", "success")

    return redirect(url_for("admin_gestion_sous_admins"))


@app.route("/admin/sous-admins/<int:sous_admin_id>/basculer-statut", methods=["POST"])
@login_required
@limiter.limit("40 per hour")
def basculer_statut_sous_admin(sous_admin_id):
    """Active ou désactive rapidement un sous-admin, sans le supprimer."""
    verifier_super_admin_strict()

    sous_admin = db.session.get(User, sous_admin_id)
    if not sous_admin or sous_admin.role != "sous_admin":
        flash("Sous-admin introuvable. ⚠️", "danger")
        return redirect(url_for("admin_gestion_sous_admins"))

    sous_admin.is_active_admin = not sous_admin.is_active_admin
    db.session.commit()

    statut = "activé" if sous_admin.is_active_admin else "désactivé"
    logger.warning(
        "[ACTION SUPER-ADMIN] Sous-admin id=%d %s par admin id=%d",
        sous_admin_id, statut, current_user.id
    )
    flash(f"Sous-admin {sous_admin.email} {statut}. ✅", "success")

    return redirect(url_for("admin_gestion_sous_admins"))


@app.route("/admin/sous-admins/<int:sous_admin_id>/supprimer", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def supprimer_sous_admin(sous_admin_id):
    """
    Suppression DÉFINITIVE d'un sous-admin. Action irréversible.
    Coupe immédiatement tout accès admin de cette personne.
    """
    verifier_super_admin_strict()

    sous_admin = db.session.get(User, sous_admin_id)
    if not sous_admin or sous_admin.role != "sous_admin":
        flash("Sous-admin introuvable. ⚠️", "danger")
        return redirect(url_for("admin_gestion_sous_admins"))

    email_supprime = sous_admin.email

    try:
        db.session.delete(sous_admin)
        db.session.commit()

        logger.warning(
            "[ACTION SUPER-ADMIN] Sous-admin SUPPRIMÉ DÉFINITIVEMENT : %s (id=%d) par admin id=%d",
            email_supprime, sous_admin_id, current_user.id
        )
        flash(f"Le sous-admin {email_supprime} a été supprimé définitivement de l'application. 🗑️", "warning")

    except Exception as e:
        db.session.rollback()
        logger.error("Erreur suppression sous-admin id=%d : %s", sous_admin_id, e)
        flash("Une erreur est survenue lors de la suppression. ⚠️", "danger")

    return redirect(url_for("admin_gestion_sous_admins"))


# ==========================================
# 🔑 MOT DE PASSE OUBLIÉ — DEMANDE DE RÉINITIALISATION
# ==========================================


def envoyer_email_reset_async(app, destinataire, reset_url):
    """Envoie l'email de réinitialisation via l'API Resend (HTTPS), pour contourner
    le blocage du SMTP sortant sur Railway."""
    with app.app_context():
        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {current_app.config['RESEND_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "Pubwek <noreply@pubwek.com>",
                    "to": [destinataire],
                    "subject": "Réinitialisation de votre mot de passe Pubwek",
                    "text": (
                        f"Bonjour,\n\n"
                        f"Vous avez demandé la réinitialisation de votre mot de passe.\n"
                        f"Cliquez sur ce lien pour en choisir un nouveau (valable 1 heure) :\n"
                        f"{reset_url}\n\n"
                        f"Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet email."
                    ),
                },
                timeout=10,
            )
            if response.status_code >= 400:
                logger.error("Échec envoi email de réinitialisation (Resend %s) : %s", response.status_code, response.text)
            else:
                logger.info("Email de réinitialisation envoyé à %s", destinataire)
        except Exception as e:
            logger.error("Échec envoi email de réinitialisation : %s", e)


# ==========================================
# 🔑 MOT DE PASSE OUBLIÉ — DEMANDE DE RÉINITIALISATION
# ==========================================
@app.route("/mot-de-passe-oublie", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == "POST":
        # Sans service d'envoi configuré, autant le dire : afficher « un lien
        # vient d'être envoyé » alors que rien ne part laisse l'utilisateur
        # attendre un e-mail qui n'arrivera jamais. Le message est le même pour
        # tout le monde, il ne révèle donc pas si le compte existe.
        if not current_app.config.get("RESEND_API_KEY"):
            flash(
                "L'envoi automatique est momentanément indisponible. "
                "Contactez le support pour réinitialiser votre mot de passe. ⚠️",
                "warning"
            )
            return redirect(url_for("login"))

        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user:
            token = creer_jeton_reset(user)
            reset_url = url_for("reset_password", token=token, _external=True)

            # Envoi en arrière-plan : la page répond immédiatement, sans attendre Gmail
            thread = threading.Thread(
                target=envoyer_email_reset_async,
                args=(current_app._get_current_object(), user.email, reset_url)
            )
            thread.daemon = True
            thread.start()

        # ⚠️ Message identique que l'email existe ou non (anti-énumération, même logique que register)
        flash("Si un compte existe avec cet email, un lien de réinitialisation vient de vous être envoyé. 📧", "info")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


# ==========================================
# 🔑 MOT DE PASSE OUBLIÉ — DÉFINITION DU NOUVEAU MOT DE PASSE
# ==========================================
@app.route("/reinitialiser-mot-de-passe/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def reset_password(token):
    # Le jeton porte l'empreinte du mot de passe en vigueur au moment de son
    # émission : une fois le mot de passe changé, il ne vaut plus rien, même
    # s'il reste dans l'historique du navigateur ou dans un mail transféré.
    user = lire_jeton_reset(token)
    if not user:
        flash(
            "Ce lien de réinitialisation est invalide, a expiré ou a déjà été "
            "utilisé. Veuillez en redemander un. ⚠️", "danger"
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < LONGUEUR_MIN_MOT_DE_PASSE:
            flash(
                f"Le mot de passe doit contenir au moins "
                f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères.", "danger"
            )
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Les mots de passe ne correspondent pas.", "danger")
            return render_template("reset_password.html", token=token)

        user.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        db.session.commit()

        # Le changement de mot de passe modifie l'empreinte, donc toutes les
        # sessions ouvertes ailleurs cessent d'être valides (voir load_user).
        logger.info("Mot de passe réinitialisé pour l'utilisateur id=%s", user.id)
        flash(
            "Votre mot de passe a été réinitialisé. Toutes vos sessions ouvertes "
            "ont été fermées, vous pouvez vous reconnecter. 🎉", "success"
        )
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)



# ==========================================
# 🆕 ROUTE : EXPORT PDF — MES RETRAITS (PARTAGEUR)
# ==========================================
           


# =========================================================================
# 🚀 Point d'entrée
# =========================================================================

if __name__ == "__main__":
    # FIX: debug=False en production. Pour dev local uniquement, passez DEBUG=true en variable d'env.
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, threaded=True)

import os
import secrets
import logging

from dotenv import load_dotenv

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

logger = logging.getLogger(__name__)

# Valeurs d'exemple qui ont circulé dans le dépôt : elles ne doivent plus
# jamais être acceptées comme clé de signature réelle.
PLACEHOLDER_SECRET_KEYS = {
    "une_cle_ultra_secrete_a_changer",
    "dev_secret_key_change_me",
    "changeme",
    "secret",
}


def _est_production():
    """L'application tourne-t-elle en production ?

    Piloté par la variable d'environnement ENV (et non par un bloc de code
    commenté qu'on oublie de décommenter le jour du déploiement).
    """
    return os.getenv("ENV", "development").strip().lower() in ("production", "prod")


def _resoudre_secret_key(is_production):
    """Retourne une SECRET_KEY sûre, ou fait échouer le démarrage en production.

    - En production : la clé est obligatoire et ne peut pas être une valeur
      d'exemple. Sans elle, n'importe qui peut forger un cookie de session
      administrateur, donc mieux vaut refuser de démarrer.
    - En développement : on génère une clé aléatoire éphémère pour ne pas
      bloquer le travail local (effet de bord : les sessions sont perdues à
      chaque redémarrage, ce qui est sans conséquence en local).
    """
    key = (os.getenv("SECRET_KEY") or "").strip()

    if key and key not in PLACEHOLDER_SECRET_KEYS and len(key) >= 32:
        return key

    if is_production:
        raise RuntimeError(
            "SECRET_KEY absente, trop courte ou laissée à sa valeur d'exemple.\n"
            "Générez-en une puis placez-la dans vos variables d'environnement :\n"
            '    python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if key:
        logger.warning(
            "SECRET_KEY faible ou valeur d'exemple : une clé aléatoire temporaire est "
            "utilisée pour cette session de développement. Générez une vraie clé avant "
            'le déploiement : python -c "import secrets; print(secrets.token_hex(32))"'
        )
    else:
        logger.warning(
            "SECRET_KEY absente : une clé aléatoire temporaire est utilisée pour cette "
            "session de développement (les sessions seront perdues au redémarrage)."
        )
    return secrets.token_hex(32)


class Config:
    # -------------------------
    # 🌍 Environnement
    # -------------------------
    IS_PRODUCTION = _est_production()
    ENV = "production" if IS_PRODUCTION else "development"

    # -------------------------
    # 🔐 Sécurité et Base de Données
    # -------------------------
    SECRET_KEY = _resoudre_secret_key(IS_PRODUCTION)

    # Base de données (SQLite par défaut, PostgreSQL conseillé en production)
    _database_url = os.getenv("DATABASE_URL", "sqlite:///pubwek.db")
    # Railway/Heroku fournissent parfois "postgres://", SQLAlchemy 2.x exige "postgresql://"
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # -------------------------
    # 🍪 Cookies de session
    # Les drapeaux "Secure" suivent l'environnement : actifs dès que HTTPS est
    # disponible, désactivés en local où il n'y a pas de certificat.
    # -------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION

    # -------------------------
    # 🗄️ Redis (progression vidéo, limitation de débit, Celery)
    # -------------------------
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # -------------------------
    # 💳 Configuration FedaPay (Sandbox / Live)
    # -------------------------
    FEDAPAY_ENV = os.getenv("FEDAPAY_ENV", "sandbox").lower()  # 'sandbox' ou 'live'
    FEDAPAY_PUBLIC_KEY = os.getenv("FEDAPAY_PUBLIC_KEY", "")
    FEDAPAY_SECRET_KEY = os.getenv("FEDAPAY_SECRET_KEY", "")
    FEDAPAY_WEBHOOK_SECRET = os.getenv("FEDAPAY_WEBHOOK_SECRET", "")

    # URLs d'API FedaPay
    FEDAPAY_BASE_URL_SANDBOX = "https://sandbox-api.fedapay.com/v1"
    FEDAPAY_BASE_URL_LIVE = "https://api.fedapay.com/v1"

    @property
    def FEDAPAY_BASE_URL(self):
        """Retourne l'URL de base selon l'environnement actif"""
        return self.FEDAPAY_BASE_URL_SANDBOX if self.FEDAPAY_ENV == "sandbox" else self.FEDAPAY_BASE_URL_LIVE

    # -------------------------
    # 📧 Envoi d'e-mails — API Resend (HTTPS)
    #
    # Railway bloque le SMTP sortant : l'envoi passe donc par l'API HTTPS de
    # Resend, pas par Flask-Mail. Sans cette clé, la réinitialisation de mot de
    # passe ne peut pas fonctionner.
    #
    # L'adresse d'expédition (noreply@pubwek.com) doit correspondre à un
    # domaine vérifié dans Resend, sinon les envois sont refusés.
    # -------------------------
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")

    @staticmethod
    def envoi_email_configure():
        return bool(Config.RESEND_API_KEY)


    # -------------------------
    # 🧩 Mode Debug
    # Jamais activable en production, quelle que soit la valeur de DEBUG.
    # -------------------------
    DEBUG = (
        False if IS_PRODUCTION
        else os.getenv("DEBUG", "true").lower() in ["true", "1", "yes"]
    )

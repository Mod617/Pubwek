import os
from dotenv import load_dotenv

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()


class Config:
    # -------------------------
    # 🔐 Sécurité et Base de Données
    # -------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev_secret_key_change_me")

    # Base de données (SQLite par défaut, PostgreSQL conseillé en production)
    _database_url = os.getenv("DATABASE_URL", "sqlite:///pubwek.db")
    # Railway/Heroku fournissent parfois "postgres://", SQLAlchemy 2.x exige "postgresql://"
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
    # 📧 Configuration Email (SMTP Gmail)
    # -------------------------
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))  # 465 (SSL) ou 587 (TLS)
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() in ["true", "1", "yes"]
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() in ["true", "1", "yes"]

    # Identifiants de messagerie
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "pubwek1@gmail.com")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", MAIL_USERNAME)

    # -------------------------
    # ☎️ Configuration Twilio (facultatif)
    # -------------------------
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

    @staticmethod
    def is_twilio_configured():
        """Vérifie si Twilio est bien configuré avant utilisation"""
        return all([
            Config.TWILIO_ACCOUNT_SID,
            Config.TWILIO_AUTH_TOKEN,
            Config.TWILIO_PHONE_NUMBER
        ])

    # -------------------------
    # 🧩 Mode Debug
    # -------------------------
    DEBUG = os.getenv("DEBUG", "true").lower() in ["true", "1", "yes"]
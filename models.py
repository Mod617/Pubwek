import hashlib
import uuid

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta
from sqlalchemy import Index, UniqueConstraint, CheckConstraint

db = SQLAlchemy()

# =========================================================================
# 🌐 NOUVEAUX MODÈLES DE CLUSTERING ET DE RÉSIDU RÉSEAU (ANTI-FERMES & BOTNETS)
# =========================================================================

class DeviceCluster(db.Model):
    """
    Regroupement algorithmique d'appareils partageant des caractéristiques 
    identiques ou fortement corrélées (ex: Canvas Hash identique mais Fingerprint variable).
    """
    __tablename__ = "device_clusters"

    id = db.Column(db.Integer, primary_key=True)
    cluster_name = db.Column(db.String(100), nullable=True)
    cluster_hash = db.Column(db.String(64), unique=True, index=True, nullable=False)
    risk_score = db.Column(db.Float, default=0.0, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relations
    devices = db.relationship("Device", backref="cluster", lazy=True)


class NetworkCluster(db.Model):
    """
    Regroupement d'adresses IP suspectées d'appartenir à la même entité d'attaque 
    (ex: Blocs CIDR /24, même ASN frauduleux, infrastructures coordonnées).
    """
    __tablename__ = "network_clusters"

    id = db.Column(db.Integer, primary_key=True)
    cluster_name = db.Column(db.String(100), nullable=True)
    cluster_hash = db.Column(db.String(64), unique=True, index=True, nullable=False)
    risk_score = db.Column(db.Float, default=0.0, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relations
    ip_addresses = db.relationship("IPAddress", backref="network_cluster", lazy=True)


# =========================================================================
# 🌍 MODÈLE DE NORMALISATION ET RENSEIGNEMENT IP (INTÉGRES ET INDEXÉS)
# =========================================================================

class IPAddress(db.Model):
    """
    Base de connaissances normalisée des adresses IP. Évite la redondance textuelle,
    optimise les jointures et stocke les indicateurs Cyber/Threat Intelligence.
    """
    __tablename__ = "ip_addresses"

    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(45), unique=True, index=True, nullable=False)  # Supporte IPv4 et IPv6
    network_cluster_id = db.Column(db.Integer, db.ForeignKey("network_clusters.id"), nullable=True)

    # Données de géolocalisation
    country = db.Column(db.String(100), nullable=True, index=True)
    country_code = db.Column(db.String(10), nullable=True, index=True)
    continent = db.Column(db.String(50), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    postal_code = db.Column(db.String(20), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    # Métadonnées réseau & ASN
    timezone = db.Column(db.String(100), nullable=True)
    timezone_offset = db.Column(db.Integer, nullable=True)
    isp = db.Column(db.String(150), nullable=True, index=True)
    organization = db.Column(db.String(150), nullable=True)
    asn = db.Column(db.Integer, nullable=True, index=True)
    reverse_dns = db.Column(db.String(255), nullable=True)

    # Indicateurs avancés Anti-Fraude (Proxy, VPN, Datacenter, Tor)
    hosting = db.Column(db.Boolean, default=False, index=True)
    mobile = db.Column(db.Boolean, default=False, index=True)
    proxy = db.Column(db.Boolean, default=False, index=True)
    vpn = db.Column(db.Boolean, default=False, index=True)
    tor = db.Column(db.Boolean, default=False, index=True)
    relay = db.Column(db.Boolean, default=False, index=True)

    # Notation du risque et contrôle d'accès
    risk_score = db.Column(db.Float, default=0.0, index=True)
    blocked = db.Column(db.Boolean, default=False, index=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    views = db.relationship("View", backref="ip_relation", lazy=True)
    user_sessions = db.relationship("UserSession", backref="ip_relation", lazy=True)

import uuid as uuidlib

class DocumentCertification(db.Model):
    """Preuve d'authenticité d'un document PDF généré par la plateforme.

    Chaque PDF émis (retraits, transactions...) a une ligne ici, créée au
    moment de la génération. L'UUID est imprimé sur le PDF (QR code + code
    court) et permet à quiconque de vérifier les données réelles du document
    sur /verifier/<uuid>, indépendamment du contenu du fichier PDF lui-même.
    """
    __tablename__ = "document_certifications"

    id = db.Column(db.Integer, primary_key=True)
    doc_uuid = db.Column(db.String(36), unique=True, nullable=False, index=True,
                          default=lambda: str(uuidlib.uuid4()))
    doc_type = db.Column(db.String(50), nullable=False)   # "retraits" ou "transactions"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Données figées au moment de la génération (jamais modifiées après coup)
    montant_reference = db.Column(db.Float, nullable=False)
    nb_lignes = db.Column(db.Integer, nullable=False)

    # Empreinte HMAC calculée sur les champs ci-dessus : détecte toute
    # altération de CETTE ligne elle-même (pas seulement du PDF).
    signature = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User")


# =========================================================================
# 🧍 MODÈLES MÉTIERS ET SESSIONS UTILISATEURS
# =========================================================================

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # "annonceur", "partageur", "admin" ou "sous_admin"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # --- NOUVEAU CHAMP : ACCEPTATION DES CGU & APDP BÉNIN ---
    has_accepted_terms = db.Column(db.Boolean, default=False)

    # --- NOUVEAUX CHAMPS POUR LE PARRAINAGE ---
    referrer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # Qui a parrainé cet utilisateur
    has_launched_first_campaign = db.Column(db.Boolean, default=False)  # True dès que la 1ère campagne est payée/validée

    # --- CHAMPS PRO & DESIGN STYLE FACEBOOK ---
    company_name = db.Column(db.String(150), nullable=True)
    profile_picture = db.Column(db.String(255), nullable=True, default="default_profile.png") 
    cover_photo = db.Column(db.String(255), nullable=True, default="default_cover.png")
    bio = db.Column(db.String(500), nullable=True)
    profile_slogan = db.Column(db.String(255), nullable=True)
    logo = db.Column(db.String(255), nullable=True)
    
    cover_position_x = db.Column(db.Float, nullable=False, default=50.0)
    cover_position_y = db.Column(db.Float, nullable=False, default=50.0)
    logo_position_x = db.Column(db.Float, nullable=False, default=50.0)
    logo_position_y = db.Column(db.Float, nullable=False, default=50.0)

    province = db.Column(db.String(100), nullable=False, default="Non spécifiée")
    commune = db.Column(db.String(100), nullable=True)  # 🆕 Ciblage fin des notifications de campagne (partageurs uniquement)
    is_confirmed = db.Column(db.Boolean, default=False)

    # Derniere adresse IP utilisee en session authentifiee. Sert uniquement a
    # reperer un partageur qui clique sur son propre lien de tracking.
    last_seen_ip = db.Column(db.String(45), nullable=True)
    whatsapp_number = db.Column(db.String(20), unique=True, nullable=True)
    pseudo = db.Column(db.String(50), nullable=True)

    # =========================================================================
    # 🆕 PORTEFEUILLE (partageurs) — solde crédité par les clics et le parrainage
    # =========================================================================
    wallet_balance = db.Column(db.Float, default=0.0, nullable=False)

    # =========================================================================
    # 🆕 SOUS-ADMINISTRATION — permissions accordées à un sous-admin
    # =========================================================================
    # Liste de permissions séparées par des virgules, ex: "valider_campagnes,gerer_retraits"
    # Permissions possibles : valider_utilisateurs, valider_campagnes, suivre_campagnes,
    #                          gerer_retraits, voir_transactions, configurer_tarifs, configurer_video
    admin_permissions = db.Column(db.Text, nullable=True)

    # Trace de qui a créé ce sous-admin, et quand — utile pour l'audit
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    is_active_admin = db.Column(db.Boolean, default=True)  # Permet une désactivation rapide sans suppression

    # =========================================================================
    # 🆕 VÉRIFICATION D'IDENTITÉ (partageurs en attente de validation)
    # =========================================================================
    # Marque quel admin/sous-admin a déjà envoyé le message de vérification à ce partageur.
    # Verrouille le dossier : seul cet admin/sous-admin pourra ensuite confirmer ou refuser.
    contacted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    contacted_at = db.Column(db.DateTime, nullable=True)

    # Relations Existantes
    products = db.relationship("Product", backref="owner", lazy=True, cascade="all, delete-orphan")
    shares = db.relationship("Share", backref="sharer", lazy=True, cascade="all, delete-orphan")
    campaigns = db.relationship("Campaign", backref="annonceur", lazy=True, cascade="all, delete-orphan")
    fraud_logs = db.relationship("FraudLog", backref="user", lazy=True, cascade="all, delete-orphan")
    views = db.relationship("View", backref="sharer", lazy=True, cascade="all, delete-orphan")
    user_sessions = db.relationship("UserSession", backref="user", lazy=True, cascade="all, delete-orphan")

    # 🆕 Relation de parrainage (auto-référencement) — foreign_keys précisé car il existe
    # maintenant PLUSIEURS colonnes users.id -> users.id (referrer_id, created_by_admin_id,
    # contacted_by_id), donc SQLAlchemy ne peut plus deviner seul laquelle utiliser pour cette relation.
    referrals = db.relationship(
        "User",
        backref=db.backref("referrer", remote_side=[id]),
        foreign_keys=[referrer_id],
        lazy=True
    )

    # 🆕 Relation vers l'admin/sous-admin qui a pris en charge le contact de ce partageur
    contacte_par = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[contacted_by_id],
        lazy=True
    )

    def empreinte_session(self):
        """Empreinte courte du mot de passe actuel.

        Intégrée à l'identifiant de session et aux jetons de réinitialisation :
        elle change dès que le mot de passe change, ce qui invalide d'un coup
        les sessions ouvertes et les liens de réinitialisation en circulation.
        """
        return hashlib.sha256((self.password_hash or "").encode("utf-8")).hexdigest()[:16]

    def get_id(self):
        """Identifiant stocké dans le cookie de session (Flask-Login)."""
        return f"{self.id}|{self.empreinte_session()}"

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"

    # =========================================================================
    # 🆕 MÉTHODES DE GESTION DES PERMISSIONS SOUS-ADMIN
    # =========================================================================
    def get_permissions_list(self):
        """Retourne la liste des permissions de ce sous-admin sous forme de liste Python."""
        if not self.admin_permissions:
            return []
        return [p.strip() for p in self.admin_permissions.split(",") if p.strip()]

    def has_permission(self, permission_key):
        """
        Vérifie si l'utilisateur a le droit d'accéder à une section admin donnée.
        Le super-admin (role == "admin") a TOUJOURS accès à tout, sans restriction.
        Un sous-admin doit avoir explicitement la permission ET être actif.
        """
        if self.role == "admin":
            return True
        if self.role == "sous_admin" and self.is_active_admin:
            return permission_key in self.get_permissions_list()
        return False

class CampaignShareProof(db.Model):
    """
    Preuve (capture d'écran) envoyée par un partageur pour justifier qu'un
    statut WhatsApp est resté publié pendant un jour de diffusion donné.
    Une seule preuve par jour est désormais exigée : la capture de FIN de
    journée (proof_type="fin"). Les clics du jour ne sont crédités au
    portefeuille retirable qu'une fois cette preuve validée par un admin,
    dans une fenêtre de Campaign.FENETRE_RATTRAPAGE_HEURES après la fin du
    jour concerné — passé ce délai, les clics du jour sont définitivement
    perdus.
    """
    __tablename__ = "campaign_share_proofs"
    id = db.Column(db.Integer, primary_key=True)
    campaign_share_id = db.Column(db.Integer, db.ForeignKey("campaign_shares.id"), nullable=False, index=True)
    day_number = db.Column(db.Integer, nullable=False)  # jour de diffusion : 1, 2, 3...
    proof_type = db.Column(db.String(10), nullable=False)  # "fin" uniquement désormais
    filename = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="en_attente", nullable=False, index=True)  # en_attente / validee / rejetee
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    rejection_reason = db.Column(db.String(255), nullable=True)
    campaign_share = db.relationship(
        "CampaignShare",
        backref=db.backref("proofs", lazy=True, cascade="all, delete-orphan")
    )
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    __table_args__ = (
        UniqueConstraint("campaign_share_id", "day_number", "proof_type", name="uq_share_day_prooftype"),
        Index("idx_share_day_status", "campaign_share_id", "day_number", "status"),
    )
    def __repr__(self):
        return f"<CampaignShareProof share={self.campaign_share_id} jour={self.day_number} type={self.proof_type} statut={self.status}>"


class Transaction(db.Model):
    """
    Journal des transactions de paiement FedaPay : paiements de campagnes
    et abonnements vidéo. Distinct de WalletTransaction (portefeuille des partageurs).
    """
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=True, index=True)

    reference = db.Column(db.String(100), unique=True, nullable=False, index=True)
    fedapay_transaction_id = db.Column(db.String(100), nullable=True, index=True)

    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default="XOF", nullable=False)

    # "campaign_payment", "video_subscription_monthly", "video_subscription_yearly"
    transaction_type = db.Column(db.String(50), nullable=False, index=True)

    # "pending", "approved", "canceled", "declined"
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref=db.backref("transactions", lazy=True, cascade="all, delete-orphan"))
    campaign = db.relationship("Campaign", backref=db.backref("transactions", lazy=True))

    def __repr__(self):
        return f"<Transaction #{self.id} user_id={self.user_id} type={self.transaction_type} status={self.status}>"


class WithdrawalRequest(db.Model):
    """
    Demande de retrait d'un partageur depuis son portefeuille.
    Traçabilité complète exigée : preuve de paiement obligatoire côté FedaPay,
    confirmation explicite de l'admin côté manuel.
    """
    __tablename__ = "withdrawal_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    amount = db.Column(db.Float, nullable=False)

    # "pending" (en attente) | "processing" (prise en charge) | "paid" (payé) | "rejected" (refusé)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)

    # "manual" (paiement manuel par l'admin) | "fedapay" (transfert automatisé)
    payment_method = db.Column(db.String(20), nullable=True)

    # Coordonnées de réception fournies par le partageur au moment de la demande
    payout_channel = db.Column(db.String(50), nullable=False)  # MTN Mobile Money, Moov Money, Celtiis Cash, Wave
    payout_phone = db.Column(db.String(20), nullable=False)

    # Preuve de paiement : soit une capture uploadée (manuel), soit l'ID de transaction FedaPay
    proof_file = db.Column(db.String(255), nullable=True)
    fedapay_transfer_id = db.Column(db.String(100), nullable=True)

    admin_note = db.Column(db.Text, nullable=True)  # Motif si refusé, ou note libre de l'admin
    processed_by = db.Column(db.Integer, nullable=True)  # ID de l'admin qui a traité la demande

    requested_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    processed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref=db.backref("withdrawal_requests", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<WithdrawalRequest #{self.id} user_id={self.user_id} amount={self.amount} status={self.status}>"        


class UserSession(db.Model):
    """
    Mémorise l'historique d'authentification des utilisateurs indépendamment des terminaux,
    pour tracer le multi-compte et le piratage de session.
    """
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    ip_id = db.Column(db.Integer, db.ForeignKey("ip_addresses.id"), nullable=False)
    
    login_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    logout_time = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    remember_me = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True, index=True)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================================================================
# 🛍️ PRODUITS, PARTAGES ET CLICS (STRICTEMENT INTACTS)
# =========================================================================

class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(500), nullable=False)
    price = db.Column(db.Float, nullable=True)
    media_url = db.Column(db.String(255))
    landing_url = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    shares = db.relationship("Share", backref="product", lazy=True, cascade="all, delete-orphan")


class Share(db.Model):
    __tablename__ = "shares"

    id = db.Column(db.Integer, primary_key=True)
    sharer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    channel = db.Column(db.String(50), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    clicks = db.relationship("Click", backref="share", lazy=True, cascade="all, delete-orphan")


class Click(db.Model):
    __tablename__ = "clicks"

    id = db.Column(db.Integer, primary_key=True)
    share_id = db.Column(db.Integer, db.ForeignKey("shares.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip = db.Column(db.String(45))
    user_agent = db.Column(db.String(255))


# =========================================================================
# 🎯 CAMPAGNES PUBLICITAIRES
# =========================================================================

class Campaign(db.Model):
    __tablename__ = "campaigns"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    promotion_type = db.Column(db.String(50), nullable=False)
    promotion_detail = db.Column(db.String(255), nullable=True)
    description = db.Column(db.String(500), nullable=True) 
    media_files = db.Column(db.Text, nullable=True) 
    
    # --- Type de média pour la tarification ---
    media_type = db.Column(db.String(10), nullable=False, default="photo") # "photo" ou "video"

    display_option = db.Column(db.String(20), default="A") 
    generated_video = db.Column(db.String(255), nullable=True) 

    # --- Lien externe optionnel (site web / application web de la structure) ---
    website_url = db.Column(db.String(255), nullable=True)

    provinces = db.Column(db.Text, nullable=False)

    # --- 🆕 Raffinement optionnel du ciblage : communes précises dans les départements choisis ---
    communes = db.Column(db.Text, nullable=True)

    duration_days = db.Column(db.Integer, default=7) # Sera limité à un max de 30 jours dans le formulaire
    end_date = db.Column(db.DateTime, nullable=True) 

    # ATTENTION au nom de ces colonnes : elles datent de la tarification à la
    # vue et comptent désormais des CLICS rémunérés. Le nom est conservé pour
    # éviter une migration risquée ; l'interface, elle, parle bien de clics.
    whatsapp_views = db.Column(db.Integer, default=0) 
    target_whatsapp_views = db.Column(db.Integer, default=0)
    
    # --- Vues quotidiennes consécutives ---
    # 🆕 Reste un indicateur informatif (affiché comme "rythme moyen prévu"),
    # mais le quota réellement exigé chaque jour est désormais recalculé
    # dynamiquement par quota_effectif_du_jour(), pour ne jamais perdre de
    # clics par troncature/arrondi et pour rattraper automatiquement les
    # jours sous-performants sur les jours restants.
    views_per_day = db.Column(db.Integer, default=0) # Calculé automatiquement (ex: target_views / duration_days)

    # =========================================================================
    # 🆕 GESTION DU QUOTA JOURNALIER DE VUES (clics WhatsApp vérifiés)
    # =========================================================================
    views_today = db.Column(db.Integer, default=0)  # Compteur du jour en cours, remis à 0 chaque nouveau jour
    current_day_number = db.Column(db.Integer, default=0)  # Jour 1, jour 2, jour 3... de la diffusion — resynchronisé sur jour_diffusion_campagne()
    last_quota_date = db.Column(db.Date, nullable=True)  # Informatif uniquement : date du dernier reset détecté (traçabilité/support)
    daily_quota_paused = db.Column(db.Boolean, default=False)  # True quand le quota du jour est atteint
    daily_quota_alert_sent = db.Column(db.Boolean, default=False)  # Empêche de spammer les partageurs plusieurs fois le même jour

    total_cost = db.Column(db.Float, nullable=False)
    whatsapp_number = db.Column(db.String(20), nullable=True)
    
    # =========================================================================
    # 🎯 STATUTS ET WORKFLOW DE VALIDATION / PAIEMENT / REMBOURSEMENT
    # =========================================================================
    status = db.Column(db.String(50), default="pending_review", nullable=False, index=True)
    payment_status = db.Column(db.String(20), default="unpaid", nullable=False, index=True)
    admin_status = db.Column(db.String(20), default="pending_review", nullable=False, index=True)
    rejection_reason = db.Column(db.Text, nullable=True)
    can_claim_refund = db.Column(db.Boolean, default=False)

    # --- 🆕 Partage manuel de la campagne validée aux partageurs ciblés ---
    shared_to_partageurs = db.Column(db.Boolean, default=False)  # Empêche les doublons de partage
    shared_at = db.Column(db.DateTime, nullable=True)  # Horodatage du partage

    validated = db.Column(db.Boolean, default=False)
    paid = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    views = db.relationship("View", backref="campaign", lazy=True, cascade="all, delete-orphan")
    fraud_logs = db.relationship("FraudLog", backref="campaign", lazy=True, cascade="all, delete-orphan")
    refund_requests = db.relationship("RefundRequest", backref="campaign", lazy=True, cascade="all, delete-orphan")

    # =========================================================================
    # 🆕 FENÊTRE DE RATTRAPAGE DES PREUVES
    #
    # Au-delà de ce délai après la fin d'un jour de diffusion, la preuve de ce
    # jour n'est plus acceptée et les clics correspondants sont perdus.
    # =========================================================================
    FENETRE_RATTRAPAGE_HEURES = 48

    def check_progress(self):
        """Désactive la campagne si l'objectif est atteint ou la date dépassée."""
        now = datetime.utcnow()
        if self.target_whatsapp_views and self.whatsapp_views >= self.target_whatsapp_views:
            self.is_active = False
        if self.end_date and now > self.end_date:
            self.is_active = False
        db.session.commit()

    def quota_effectif_du_jour(self):
        """Quota de clics réellement exigé aujourd'hui : l'objectif restant
        de la campagne, réparti sur les jours de diffusion restants (jour
        courant inclus). Recalculé dynamiquement — pas de valeur figée à la
        création, donc aucune perte par troncature/arrondi au fil des jours,
        et rattrapage automatique d'un jour sous-performant sur les jours
        suivants."""
        jour_actuel = self.jour_diffusion_campagne()
        jours_restants = max(1, (self.duration_days or 1) - jour_actuel + 1)
        restant_objectif = max(0, (self.target_whatsapp_views or 0) - (self.whatsapp_views or 0))
        # Arrondi au-dessus : mieux vaut viser large que perdre des clics à la fin.
        return -(-restant_objectif // jours_restants)  # équivalent d'un ceil() en entier

    def verifier_et_reset_quota_journalier(self):
        """
        Resynchronise l'état de la campagne sur jour_diffusion_campagne(), l'unique
        source de vérité pour le jour de diffusion. Si le jour calculé a changé
        depuis le dernier passage : reset le compteur du jour et réactive la campagne.
        last_quota_date est conservée à titre informatif (traçabilité/support), elle
        ne pilote plus la logique.
        Retourne True si un changement de jour a eu lieu.
        """
        nouveau_jour = self.jour_diffusion_campagne()

        # Jamais initialisé (première vue de la campagne)
        if self.last_quota_date is None:
            self.last_quota_date = datetime.utcnow().date()
            self.current_day_number = nouveau_jour
            self.views_today = 0
            self.daily_quota_paused = False
            self.daily_quota_alert_sent = False
            return True

        # Changement de jour détecté (comparaison sur le jour calculé, plus sur la date brute)
        if nouveau_jour != self.current_day_number:
            self.last_quota_date = datetime.utcnow().date()
            self.current_day_number = nouveau_jour
            self.views_today = 0
            self.daily_quota_paused = False
            self.daily_quota_alert_sent = False
            return True

        return False

    def quota_du_jour_atteint(self):
        """Retourne True si le quota effectif du jour en cours est atteint ou dépassé."""
        quota = self.quota_effectif_du_jour()
        if quota <= 0:
            return False
        return self.views_today >= quota

    def jour_diffusion_campagne(self, moment=None):
        """Numéro du jour de diffusion (1, 2, 3...) à un instant donné.
        Calculé sur la date calendaire, indépendamment des clics reçus : un jour
        sans clic doit quand même pouvoir recevoir une preuve de publication.
        Le point de départ est shared_at (date de mise à disposition aux
        partageurs) ; à défaut, created_at.
        """
        moment = moment or datetime.utcnow()
        reference = self.shared_at or self.created_at
        delta_jours = (moment.date() - reference.date()).days + 1
        plafond = self.duration_days or 1
        return max(1, min(delta_jours, plafond))

    def fin_du_jour(self, day_number):
        """Instant exact (minuit) où se termine le jour de diffusion `day_number`."""
        reference = self.shared_at or self.created_at
        date_du_jour = reference.date() + timedelta(days=day_number - 1)
        return datetime.combine(date_du_jour + timedelta(days=1), datetime.min.time())

    def date_limite_preuve(self, day_number):
        """Instant après lequel la preuve de `day_number` n'est plus acceptée."""
        return self.fin_du_jour(day_number) + timedelta(hours=self.FENETRE_RATTRAPAGE_HEURES)

    def jour_encore_reclamable(self, day_number, moment=None):
        """La preuve de ce jour peut-elle encore être envoyée/validée ?"""
        moment = moment or datetime.utcnow()
        return moment < self.date_limite_preuve(day_number)
    


class CampaignShare(db.Model):
    __tablename__ = "campaign_shares"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False, index=True)
    sharer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    # 🆕 Token unique pour générer les liens de tracking (whatsapp + site web)
    tracking_token = db.Column(db.String(32), unique=True, nullable=False, index=True, default=lambda: uuid.uuid4().hex[:12])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # =========================================================================
    # 🆕 SUIVI DES RAPPELS DE PREUVE URGENTS
    #
    # Liste de numéros de jour (séparés par des virgules, ex: "1,3") pour
    # lesquels l'alerte "deadline proche" a déjà été envoyée à ce partageur.
    # Évite de le notifier plusieurs fois du même rappel à chaque passage de
    # la tâche périodique qui scanne les preuves en attente.
    # =========================================================================
    jours_rappel_urgent_envoyes = db.Column(db.Text, nullable=True)

    campaign = db.relationship(
        "Campaign",
        backref=db.backref("campaign_shares", lazy=True, cascade="all, delete-orphan")
    )
    sharer = db.relationship(
        "User",
        backref=db.backref("campaign_shares", lazy=True, cascade="all, delete-orphan")
    )

    clicks = db.relationship("CampaignClick", backref="campaign_share", lazy=True, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("campaign_id", "sharer_id", name="uq_campaign_share_unique"),
    )

    def rappel_urgent_deja_envoye(self, jour):
        """Le rappel urgent a-t-il déjà été envoyé pour ce jour précis ?"""
        if not self.jours_rappel_urgent_envoyes:
            return False
        return str(jour) in self.jours_rappel_urgent_envoyes.split(",")

    def marquer_rappel_urgent_envoye(self, jour):
        """Enregistre que le rappel urgent vient d'être envoyé pour ce jour."""
        jours = set(self.jours_rappel_urgent_envoyes.split(",")) if self.jours_rappel_urgent_envoyes else set()
        jours.discard("")
        jours.add(str(jour))
        self.jours_rappel_urgent_envoyes = ",".join(sorted(jours, key=int))

    def __repr__(self):
        return f"<CampaignShare campaign_id={self.campaign_id} sharer_id={self.sharer_id}>"


class CampaignClick(db.Model):
    """
    Enregistre chaque clic effectué sur un lien de tracking (WhatsApp ou site web)
    inséré par un partageur dans son statut, pour une campagne donnée.
    """
    __tablename__ = "campaign_clicks"
    id = db.Column(db.Integer, primary_key=True)
    campaign_share_id = db.Column(db.Integer, db.ForeignKey("campaign_shares.id"), nullable=False, index=True)
    link_type = db.Column(db.String(20), nullable=False)  # "whatsapp" ou "website"
    ip = db.Column(db.String(45), nullable=True, index=True)
    user_agent = db.Column(db.String(255), nullable=True)
    clicked_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # --- Decision anti-fraude ---
    # Tous les clics sont enregistres ; seuls ceux marques is_paid ont donne
    # lieu a une remuneration. rejection_reason garde la trace du motif, ce qui
    # permet de justifier un solde aupres d'un partageur qui conteste.
    is_paid = db.Column(db.Boolean, default=False, nullable=False, index=True)
    rejection_reason = db.Column(db.String(40), nullable=True)
    # 🆕 Jour de diffusion de la campagne (1, 2, 3...) auquel appartient ce clic.
    # Sert à rattacher le clic aux preuves (captures) validées pour ce jour précis
    # — même convention de nommage que CampaignShareProof.day_number.
    day_number = db.Column(db.Integer, nullable=True)
    # 🆕 Horodatage de la récompense effective du partageur pour ce clic (audit/litiges).
    rewarded_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (
        Index("idx_share_type_clicked", "campaign_share_id", "link_type", "clicked_at"),
        # Sert la deduplication : "ce couple (partage, IP) a-t-il deja ete paye
        # dans la fenetre ?" est la requete la plus frequente du dispositif.
        Index("idx_share_ip_paid", "campaign_share_id", "ip", "is_paid", "clicked_at"),
        Index("idx_ip_paid_date", "ip", "is_paid", "clicked_at"),
    )

# =========================================================================
# 📎 PROPRIÉTÉ DES FICHIERS TÉLÉVERSÉS (PROTECTION IDOR)
# =========================================================================

class UploadedFile(db.Model):
    """
    Rattache chaque fichier de uploads_secure/ à son propriétaire.

    Sans cette table, la route /uploads/<filename> ne peut pas savoir à qui
    appartient un fichier : tout utilisateur connecté pouvait donc télécharger
    les visuels, logos et vidéos de tous les autres.
    """
    __tablename__ = "uploaded_files"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    # "image", "video", "audio", "logo", "cover" — informatif, facilite le ménage
    kind = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    owner = db.relationship(
        "User",
        backref=db.backref("uploaded_files", lazy=True, cascade="all, delete-orphan")
    )

    @classmethod
    def enregistrer(cls, filename, owner_id, kind=None):
        """Déclare un fichier comme appartenant à un utilisateur.

        Idempotent : si le nom existe déjà (collision d'UUID hautement
        improbable, ou double appel), l'enregistrement existant est conservé.
        """
        existant = cls.query.filter_by(filename=filename).first()
        if existant:
            return existant
        enreg = cls(filename=filename, owner_id=owner_id, kind=kind)
        db.session.add(enreg)
        return enreg

    def __repr__(self):
        return f"<UploadedFile {self.filename} owner_id={self.owner_id}>"


# =========================================================================
# 📱 MODÈLE DEVICE ENRICHI (FINGERPRINTING & AUTOMATION DETECTION)
# =========================================================================

class Device(db.Model):
    """
    Représentation matérielle et logicielle unique d'un terminal utilisateur.
    Stocke les hashes de fingerprinting et les flags de détection d'outils de triche.
    """
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(255), unique=True, index=True, nullable=False)
    device_cluster_id = db.Column(db.Integer, db.ForeignKey("device_clusters.id"), nullable=True)

    # Données d'origine (compatibilité descendante préservée)
    user_agent = db.Column(db.String(500), nullable=True)
    navigateur = db.Column(db.String(100), nullable=True)
    systeme = db.Column(db.String(100), nullable=True)
    langue = db.Column(db.String(50), nullable=True)
    timezone = db.Column(db.String(100), nullable=True)
    resolution_ecran = db.Column(db.String(50), nullable=True)
    plateforme = db.Column(db.String(100), nullable=True)
    cpu = db.Column(db.String(50), nullable=True)
    memoire = db.Column(db.Integer, nullable=True)  
    webgl = db.Column(db.Text, nullable=True)
    canvas = db.Column(db.Text, nullable=True)
    audio = db.Column(db.Text, nullable=True)
    
    premiere_connexion = db.Column(db.DateTime, default=datetime.utcnow)
    derniere_activite = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    nombre_total_de_vues = db.Column(db.Integer, default=0)
    trust_score = db.Column(db.Float, default=100.0, index=True)  
    is_blocked = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- CHAMPS SUPPLÉMENTAIRES PRO AVANCÉS (EXTRACTEURS COMPORTEMENTAUX) ---
    device_type = db.Column(db.String(50), nullable=True, index=True)  # mobile, tablet, desktop
    manufacturer = db.Column(db.String(100), nullable=True, index=True)  # Apple, Samsung, Google
    brand = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(100), nullable=True, index=True)
    
    os_name = db.Column(db.String(100), nullable=True, index=True)
    os_version = db.Column(db.String(50), nullable=True)
    browser_name = db.Column(db.String(100), nullable=True, index=True)
    browser_version = db.Column(db.String(50), nullable=True)
    engine = db.Column(db.String(100), nullable=True)
    engine_version = db.Column(db.String(50), nullable=True)

    # Métriques matérielles avancées
    screen_width = db.Column(db.Integer, nullable=True)
    screen_height = db.Column(db.Integer, nullable=True)
    screen_color_depth = db.Column(db.Integer, nullable=True)
    pixel_ratio = db.Column(db.Float, nullable=True)
    hardware_concurrency = db.Column(db.Integer, nullable=True)  # Cores CPU
    device_memory = db.Column(db.Integer, nullable=True)  # RAM alternative
    gpu = db.Column(db.String(255), nullable=True)
    gpu_vendor = db.Column(db.String(255), nullable=True)
    cpu_architecture = db.Column(db.String(50), nullable=True)
    touch_support = db.Column(db.Boolean, default=False)
    max_touch_points = db.Column(db.Integer, default=0)

    # API Navigateurs & Stockage
    cookies_enabled = db.Column(db.Boolean, default=True)
    local_storage = db.Column(db.Boolean, default=True)
    session_storage = db.Column(db.Boolean, default=True)
    indexed_db = db.Column(db.Boolean, default=True)
    do_not_track = db.Column(db.String(10), nullable=True)
    languages = db.Column(db.Text, nullable=True)  # Liste complète acceptée (navigator.languages)

    # Statut matériel dynamique (lors du premier enregistrement/check)
    battery_level = db.Column(db.Float, nullable=True)
    charging = db.Column(db.Boolean, nullable=True)
    network_type = db.Column(db.String(50), nullable=True)  # wifi, cellular, ethernet
    connection_speed = db.Column(db.String(50), nullable=True)

    # Signatures cryptographiques et Canvas avancés
    webgl_vendor = db.Column(db.String(255), nullable=True)
    webgl_renderer = db.Column(db.String(255), nullable=True)
    audio_fingerprint = db.Column(db.String(255), nullable=True)
    fonts_hash = db.Column(db.String(255), nullable=True)
    canvas_hash = db.Column(db.String(255), nullable=True)
    timezone_offset = db.Column(db.Integer, nullable=True)

    # Flags d'intégrité de l'environnement (Security Signals)
    rooted = db.Column(db.Boolean, default=False, index=True)
    jailbreak = db.Column(db.Boolean, default=False, index=True)
    virtual_machine = db.Column(db.Boolean, default=False, index=True)
    emulator = db.Column(db.Boolean, default=False, index=True)
    headless = db.Column(db.Boolean, default=False, index=True)

    # Détection d'automatisation (Bots & Headless Frameworks)
    webdriver = db.Column(db.Boolean, default=False, index=True)
    selenium = db.Column(db.Boolean, default=False, index=True)
    playwright = db.Column(db.Boolean, default=False, index=True)
    puppeteer = db.Column(db.Boolean, default=False, index=True)
    automation_detected = db.Column(db.Boolean, default=False, index=True)

    # Historique de géolocalisation réseau éphémère (Derniers états connus)
    last_ip = db.Column(db.String(45), nullable=True)
    last_country = db.Column(db.String(100), nullable=True)
    last_city = db.Column(db.String(100), nullable=True)

    # Compteurs d'activité et de réputation comportementale
    accepted_views = db.Column(db.Integer, default=0)
    blocked_views = db.Column(db.Integer, default=0)
    failed_views = db.Column(db.Integer, default=0)
    warning_count = db.Column(db.Integer, default=0)
    fraud_count = db.Column(db.Integer, default=0, index=True)
    reputation = db.Column(db.Float, default=1.0)  # Coefficient multiplicateur de confiance
    risk_level = db.Column(db.String(20), default="LOW", index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    last_risk_update = db.Column(db.DateTime, nullable=True)

    # Relations
    views = db.relationship("View", backref="device", lazy=True, cascade="all, delete-orphan")
    fraud_logs = db.relationship("FraudLog", backref="device", lazy=True, cascade="all, delete-orphan")
    history = db.relationship("DeviceHistory", backref="device", lazy=True, cascade="all, delete-orphan")
    sessions = db.relationship("DeviceSession", backref="device", lazy=True, cascade="all, delete-orphan")
    risk_history = db.relationship("DeviceRiskHistory", backref="device", lazy=True, cascade="all, delete-orphan")
    user_sessions = db.relationship("UserSession", backref="device", lazy=True, cascade="all, delete-orphan")


# =========================================================================
# 👁️ MODÈLE VIEW ENRICHI (ANALYSE TÉLÉMÉTRIQUE ET PAYLOADS HTTP)
# =========================================================================

class View(db.Model):
    """
    Enregistrement granulaire complet de chaque transaction de vue. 
    Contient les données comportementales de télémétrie client pour bloquer la simulation de clics.
    """
    __tablename__ = "views"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    sharer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=True)
    ip_id = db.Column(db.Integer, db.ForeignKey("ip_addresses.id"), nullable=True)  # Clé étrangère vers l'IP normalisée

    # Données originales préservées pour la compatibilité stricte de vos requêtes actuelles
    viewer_ip = db.Column(db.String(45), index=True, nullable=False)
    country = db.Column(db.String(100), nullable=True, index=True)
    city = db.Column(db.String(100), nullable=True)
    isp = db.Column(db.String(150), nullable=True)
    vpn_detected = db.Column(db.Boolean, default=False, index=True)
    proxy_detected = db.Column(db.Boolean, default=False, index=True)
    datacenter_detected = db.Column(db.Boolean, default=False, index=True)
    duplicate_view = db.Column(db.Boolean, default=False)
    suspicious = db.Column(db.Boolean, default=False, index=True)
    counted = db.Column(db.Boolean, default=False, index=True)
    duration = db.Column(db.Integer, default=0)  # Temps cumulé en secondes
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # --- ENRICHISSEMENT DU BLOC EN-TÊTES CLIENT & CONTEXTE ---
    referer = db.Column(db.String(500), nullable=True)
    origin = db.Column(db.String(255), nullable=True)
    accept_language = db.Column(db.String(255), nullable=True)
    sec_ch_ua = db.Column(db.String(500), nullable=True)  # Client Hints (sécurité contre l'usurpation d'User-Agent)
    view_source = db.Column(db.String(50), nullable=True)  # whatsapp_web, app, embed
    campaign_position = db.Column(db.Integer, nullable=True)

    # --- TÉLÉMÉTRIE COMPORTEMENTALE (ANTI FERMES ET SIMULATEURS) ---
    network_latency = db.Column(db.Integer, nullable=True)  # Latence mesurée en ms (anti-proxys lents)
    connection_type = db.Column(db.String(50), nullable=True)  # cellular, wifi, etc.
    screen_orientation = db.Column(db.String(20), nullable=True)  # portrait, landscape
    watch_percent = db.Column(db.Float, nullable=True)  # % global de complétion de visionnage
    scroll_activity = db.Column(db.Boolean, default=False)
    interaction_detected = db.Column(db.Boolean, default=False)  # Global flag
    mouse_activity = db.Column(db.Boolean, default=False)  # Présence de courbes de mouvements naturelles (anti-sélénium)
    touch_activity = db.Column(db.Boolean, default=False)
    keyboard_activity = db.Column(db.Boolean, default=False)

    # --- PIVOT DÉCISIONNEL MOTEUR ANTI-FRAUDE ---
    duplicate_reason = db.Column(db.String(100), nullable=True)
    validation_reason = db.Column(db.String(255), nullable=True)
    fraud_score = db.Column(db.Float, default=0.0, index=True)  # Calculé par le moteur asynchrone
    decision_time = db.Column(db.Integer, nullable=True)  # Temps système requis pour valider le calcul (ms)
    processing_time = db.Column(db.Integer, nullable=True)

    # --- SUIVI DE L'AUDIT HUMAIN / MODÉRATION ---
    reviewed = db.Column(db.Boolean, default=False, index=True)
    reviewed_by = db.Column(db.Integer, nullable=True)  # ID du modérateur ou de l'admin
    reviewed_at = db.Column(db.DateTime, nullable=True)

    # Configuration des Index Multi-Colonnes pour de hautes performances
    __table_args__ = (
        Index("idx_campaign_viewed", "campaign_id", "viewed_at"),
        Index("idx_campaign_device", "campaign_id", "device_id"),
        Index("idx_campaign_ip", "campaign_id", "ip_id"),
        Index("idx_device_viewed", "device_id", "viewed_at"),
    )


# =========================================================================
# 🛡️ MODÈLE FRAUDLOG ENRICHI (SIGNATURES D'ATTAQUES ET LOGS BRUTS)
# =========================================================================

class FraudLog(db.Model):
    """
    Journal d'audit des attaques de fraude détectées sur la plateforme.
    Permet d'alimenter les algorithmes de Machine Learning par analyse rétrospective.
    """
    __tablename__ = "fraud_logs"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  
    
    reason = db.Column(db.String(255), nullable=False)  
    severity = db.Column(db.String(20), nullable=False, default="LOW", index=True)  # LOW, MEDIUM, HIGH, CRITICAL
    ip = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # --- EXTENSIONS D'ARCHITECTURE SIEM ET MACHINE LEARNING ---
    fraud_code = db.Column(db.String(50), nullable=True, index=True)  # Ex: FR-001, BOT-SEL, NET-VPN
    fraud_category = db.Column(db.String(100), nullable=True, index=True)  # Automation, Repetitive, Network
    risk_score = db.Column(db.Float, default=0.0, index=True)
    detected_by = db.Column(db.String(50), nullable=True)  # Engine_V1, Manual, Threshold_Check
    
    # Flags opérationnels
    automatic = db.Column(db.Boolean, default=True)
    manual_review = db.Column(db.Boolean, default=False)
    resolved = db.Column(db.Boolean, default=False, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by = db.Column(db.Integer, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    raw_data = db.Column(db.Text, nullable=True)  # Dump texte brut
    json_data = db.Column(db.JSON, nullable=True)  # Payload JSON structuré de l'anomalie


# =========================================================================
# 📜 MODÈLES D'HISTORIQUE ET DE TRAÇABILITÉ DES APPAREILS
# =========================================================================

class DeviceHistory(db.Model):
    __tablename__ = "device_history"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    change_type = db.Column(db.String(100), nullable=False, index=True)  
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class DeviceRiskHistory(db.Model):
    """
    Enregistre les variations successives du score de confiance d'un terminal.
    Indispensable pour calculer des courbes comportementales dans le temps.
    """
    __tablename__ = "device_risk_history"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    old_score = db.Column(db.Float, nullable=False)
    new_score = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class DeviceSession(db.Model):
    __tablename__ = "device_sessions"

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey("devices.id"), nullable=False)
    fingerprint = db.Column(db.String(255), nullable=False, index=True)
    ip = db.Column(db.String(45), nullable=False)
    login_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    logout_time = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.utcnow() + timedelta(days=30))
    active = db.Column(db.Boolean, default=True, index=True)

class SystemConfig(db.Model):
    """
    Table de configuration globale permettant à l'administrateur
    de modifier dynamiquement les tarifs et commissions du site.
    """
    __tablename__ = "system_config"

    id = db.Column(db.Integer, primary_key=True)

    # =========================================================================
    # 🆕 TARIFICATION PAR CLIC (remplace l'ancienne tarification par vue)
    # Prix facturé à l'ANNONCEUR pour chaque clic généré, selon le type de contenu
    # =========================================================================
    cost_per_click_video = db.Column(db.Float, default=3.0)  # Option A : Vidéo
    cost_per_click_photo = db.Column(db.Float, default=1.0)  # Option B : Photo / Image
    cost_per_click_text = db.Column(db.Float, default=1.0)   # Option C : Texte statut pro

    # =========================================================================
    # 🆕 RÉMUNÉRATION DU PARTAGEUR PAR CLIC, selon le type de contenu qu'il a partagé
    # Crédité automatiquement sur son portefeuille à chaque clic généré
    # =========================================================================
    reward_per_click_video = db.Column(db.Float, default=1.0)  # Option A : Vidéo
    reward_per_click_photo = db.Column(db.Float, default=0.4)  # Option B : Photo / Image
    reward_per_click_text = db.Column(db.Float, default=0.3)   # Option C : Texte statut pro

    commission_rate = db.Column(db.Float, default=10.0)     # Par défaut 10%
    referral_reward_rate = db.Column(db.Float, default=3.0) # Par défaut 3% du montant total de la commission

    # 🆕 Seuil minimum de retrait pour les partageurs (portefeuille)
    minimum_withdrawal_amount = db.Column(db.Float, default=500.0)

    # =========================================================================
    # 🛡️ GARDE-FOUS ANTI-FRAUDE SUR LES CLICS
    #
    # Les liens de tracking sont publics et chaque clic paie le partageur : sans
    # ces limites, il suffit d'ouvrir son propre lien en boucle pour se créditer.
    # Réglables ici pour pouvoir être resserrés sans redéployer.
    # =========================================================================

    # Un même couple (partage, adresse IP) n'est payé qu'une fois par fenêtre.
    # C'est la protection principale.
    click_dedup_hours = db.Column(db.Integer, default=24)

    # Plafond de clics payés par partage et par jour : borne ce qu'un partageur
    # peut gagner sur une campagne même en changeant d'adresse IP.
    max_paid_clicks_per_share_per_day = db.Column(db.Integer, default=50)

    # Plafond de clics payés par adresse IP et par jour, toutes campagnes
    # confondues : borne une machine qui ferait le tour des campagnes.
    max_paid_clicks_per_ip_per_day = db.Column(db.Integer, default=20)

    # Délai minimal entre deux clics payés sur un même partage (anti-rafale).
    min_seconds_between_paid_clicks = db.Column(db.Integer, default=20)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_config(cls):
        """Récupère la configuration actuelle ou en crée une par défaut si vide."""
        config = cls.query.first()
        if not config:
            config = cls(
                cost_per_click_video=3.0,
                cost_per_click_photo=1.0,
                cost_per_click_text=1.0,
                reward_per_click_video=1.0,
                reward_per_click_photo=0.4,
                reward_per_click_text=0.3,
                commission_rate=10.0,
                referral_reward_rate=3.0,
                minimum_withdrawal_amount=500.0,
                click_dedup_hours=24,
                max_paid_clicks_per_share_per_day=50,
                max_paid_clicks_per_ip_per_day=20,
                min_seconds_between_paid_clicks=20,
            )
            db.session.add(config)
            db.session.commit()
        return config


class WalletTransaction(db.Model):
    """
    Journal complet et immuable de tout mouvement sur le portefeuille d'un utilisateur.
    Sert de preuve en cas de litige (crédit non reçu, retrait contesté, etc.).
    """
    __tablename__ = "wallet_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    amount = db.Column(db.Float, nullable=False)  # Positif = crédit, négatif = débit
    balance_after = db.Column(db.Float, nullable=False)  # Solde après l'opération, pour audit

    transaction_type = db.Column(db.String(30), nullable=False, index=True)
    # "click_reward", "referral_reward", "withdrawal"

    campaign_click_id = db.Column(db.Integer, db.ForeignKey("campaign_clicks.id"), nullable=True)
    description = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    user = db.relationship("User", backref=db.backref("wallet_transactions", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f"<WalletTransaction user_id={self.user_id} amount={self.amount}>"



class VideoGenerationConfig(db.Model):
    """
    Table de configuration pour l'Option A (Génération de vidéo à partir d'images).
    Permet à l'admin de basculer entre gratuité et abonnements payants.
    """
    __tablename__ = "video_generation_config"

    id = db.Column(db.Integer, primary_key=True)
    
    # Mode de tarification : "free" ou "subscription"
    pricing_mode = db.Column(db.String(20), default="free", nullable=False)
    
    # Tarifs d'abonnements (en XOF / FCFA)
    monthly_price = db.Column(db.Float, default=5000.0) # ex: 5 000 FCFA / mois
    yearly_price = db.Column(db.Float, default=45000.0) # ex: 45 000 FCFA / an
    
    # Système de promotion
    promo_active = db.Column(db.Boolean, default=False)
    promo_percentage = db.Column(db.Float, default=0.0) # Pourcentage de réduction (ex: 20 pour 20%)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_config(cls):
        """Récupère la configuration actuelle ou en crée une par défaut (gratuite)."""
        config = cls.query.first()
        if not config:
            config = cls(
                pricing_mode="free",
                monthly_price=5000.0,
                yearly_price=45000.0,
                promo_active=False,
                promo_percentage=0.0
            )
            db.session.add(config)
            db.session.commit()
        return config        


# =========================================================================
# 💳 MODÈLES DE PAIEMENT FADAPAY ET ABONNEMENTS
# =========================================================================




class UserSubscription(db.Model):
    """
    Gère le statut de l'abonnement actif d'un utilisateur 
    pour la fonctionnalité Option A (Génération vidéo).
    """
    __tablename__ = "user_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    
    plan_type = db.Column(db.String(20), nullable=False) # "monthly" ou "yearly"
    is_active = db.Column(db.Boolean, default=True, index=True)
    
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("subscription", uselist=False))


# =========================================================================
# 💸 MODÈLE DE DEMANDE DE RÉCLAMATION / REMBOURSEMENT
# =========================================================================

class RefundRequest(db.Model):
    """
    Stocke les demandes de réclamation soumises par les annonceurs 
    pour les campagnes refusées par l'administrateur.
    """
    __tablename__ = "refund_requests"

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    reason = db.Column(db.Text, nullable=False) # Explication/Justification de l'annonceur
    payment_method_details = db.Column(db.String(255), nullable=True) # Ex: Numéro Momo/Card pour le remboursement

    # Statut : "pending", "approved", "processed", "rejected"
    status = db.Column(db.String(20), default="pending", index=True)
    admin_notes = db.Column(db.Text, nullable=True) # Remarques de l'admin lors du traitement

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("refund_requests", lazy=True))

class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(20), default="info")  # info, success, warning, danger
    link = db.Column(db.String(255), nullable=True)
    is_read = db.Column(db.Boolean, default=False, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship(
        "User",
        backref=db.backref("notifications", lazy=True, cascade="all, delete-orphan")
    )

    def __repr__(self):
        return f"<Notification #{self.id} pour user_id={self.user_id}>"

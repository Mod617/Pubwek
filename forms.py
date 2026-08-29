from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed  # 🆕 Import pour la gestion des fichiers
from wtforms import (
    StringField,
    PasswordField,
    SubmitField,
    SelectField,
    BooleanField,
    TextAreaField,
    IntegerField
)
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, Regexp

# 🆕 Import des communes du Bénin pour le champ "commune" du partageur
from benin_communes import toutes_les_communes, commune_appartient_a


# =============================================================================
# 📞 Format des numéros WhatsApp béninois
#
# Base de données remise à zéro : on n'accepte plus qu'un seul format, le
# nouveau format béninois (+229 01 suivi de 8 chiffres).
#
# Pour simplifier la saisie, l'utilisateur ne tape QUE les 8 chiffres finaux
# (ex : 57290905) — le préfixe "+22901" est ajouté automatiquement par les
# routes (main.py) avant sauvegarde en base. NUMERO_WHATSAPP_REGEX valide donc
# le numéro COMPLET reconstruit, pas la saisie brute de l'utilisateur.
#
# Cette constante est la référence unique : main.py l'importe pour valider les
# numéros complets (création de campagne, inscription).
# =============================================================================
NUMERO_WHATSAPP_REGEX = r"^\+22901\d{8}$"

# Regex appliquée directement sur le champ de saisie du formulaire : seulement
# les 8 chiffres que l'utilisateur tape, sans préfixe.
HUIT_CHIFFRES_REGEX = r"^\d{8}$"

# Longueur minimale d'un mot de passe, valable partout : inscription,
# réinitialisation et création de sous-administrateur. Ces trois endroits
# exigeaient auparavant 6, 6 et 8 caractères.
LONGUEUR_MIN_MOT_DE_PASSE = 10

MESSAGE_NUMERO_INVALIDE = (
    "Numéro WhatsApp invalide. Veuillez entrer les 8 chiffres de votre "
    "numéro, sans le +229 01 (ex : 57290905)."
)


def numero_whatsapp_valide(numero):
    """Vérifie qu'un numéro COMPLET (+22901XXXXXXXX) respecte le format béninois attendu."""
    import re
    return bool(numero and re.match(NUMERO_WHATSAPP_REGEX, numero.strip()))


# ------------------------------
# 🔐 Formulaire de connexion
# ------------------------------
class LoginForm(FlaskForm):
    """Formulaire de connexion"""
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Mot de passe", validators=[DataRequired()])
    remember = BooleanField("Se souvenir de moi")
    submit = SubmitField("Se connecter")


# ------------------------------
# 🧾 Formulaire d'inscription
# ------------------------------
class RegisterForm(FlaskForm):
    """Formulaire d'inscription (Annonceur ou Partageur)"""
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField(
        "Mot de passe",
        validators=[
            DataRequired(),
            Length(
                min=LONGUEUR_MIN_MOT_DE_PASSE,
                message=f"Le mot de passe doit contenir au moins "
                        f"{LONGUEUR_MIN_MOT_DE_PASSE} caractères",
            ),
        ],
    )
    confirm_password = PasswordField(
        "Confirmer le mot de passe",
        validators=[
            DataRequired(),
            EqualTo("password", message="Les mots de passe ne correspondent pas"),
        ],
    )

    role = SelectField(
        "Je suis :",
        choices=[("annonceur", "Annonceur"), ("partageur", "Partageur")],
        validators=[DataRequired()],
    )

    province = SelectField(
        "Province",
        choices=[
            ("", "Sélectionnez une province"),
            ("Alibori", "Alibori"),
            ("Atacora", "Atacora"),
            ("Atlantique", "Atlantique"),
            ("Borgou", "Borgou"),
            ("Collines", "Collines"),
            ("Couffo", "Couffo"),
            ("Donga", "Donga"),
            ("Littoral", "Littoral"),
            ("Mono", "Mono"),
            ("Ouémé", "Ouémé"),
            ("Plateau", "Plateau"),
            ("Zou", "Zou"),
        ],
        default="",
        validators=[Optional()],
    )

    # 🆕 Commune — la liste affichée est filtrée en JS selon la province choisie,
    # mais on garde toutes les communes valides côté serveur pour la validation.
    commune = SelectField(
        "Commune",
        choices=[("", "Sélectionnez d'abord une province")] + [(c, c) for c in toutes_les_communes()],
        default="",
        validators=[Optional()],
    )

    company_name = StringField("Nom de l'entreprise", validators=[Optional()])

    # 🆕 Champ pour téléverser le logo (Uniquement pour l'Annonceur, non obligatoire)
    logo_file = FileField(
        "Logo de l'entreprise (Optionnel)", 
        validators=[
            Optional(), 
            FileAllowed(['jpg', 'png', 'jpeg', 'webp'], 'Seules les images sont autorisées !')
        ]
    )

    # ✅ L'utilisateur ne saisit que les 8 chiffres après +22901.
    # La reconstruction du numéro complet (+22901 + 8 chiffres) se fait dans
    # la route register() de main.py avant sauvegarde en base.
    whatsapp_number = StringField(
        "Numéro WhatsApp",
        validators=[
            Optional(),
            Regexp(
                HUIT_CHIFFRES_REGEX,
                message=MESSAGE_NUMERO_INVALIDE
            ),
        ],
    )

    submit = SubmitField("Créer mon compte")

    def validate(self, extra_validators=None):
        """Validation personnalisée selon le rôle"""
        initial_validation = super(RegisterForm, self).validate(extra_validators=extra_validators)
        if not initial_validation:
            return False

        is_valid = True

        # Validation PARTAGEUR
        if self.role.data == "partageur":
            if not self.province.data or self.province.data.strip() == "":
                self.province.errors.append("La province est obligatoire pour les partageurs.")
                is_valid = False

            # 🆕 Commune obligatoire pour les partageurs + cohérence avec la province choisie
            if not self.commune.data or self.commune.data.strip() == "":
                self.commune.errors.append("La commune est obligatoire pour les partageurs.")
                is_valid = False
            elif self.province.data and not commune_appartient_a(self.commune.data, self.province.data):
                self.commune.errors.append("La commune sélectionnée ne correspond pas à la province choisie.")
                is_valid = False

            if not self.whatsapp_number.data or not self.whatsapp_number.data.strip():
                self.whatsapp_number.errors.append("Le numéro WhatsApp est obligatoire pour les partageurs.")
                is_valid = False

        # Validation ANNONCEUR
        if self.role.data == "annonceur":
            if not self.company_name.data or not self.company_name.data.strip():
                self.company_name.errors.append("Le nom de l’entreprise est obligatoire pour les annonceurs.")
                is_valid = False

        return is_valid


# ------------------------------
# 📣 Formulaire de création de campagne (SANS FACEBOOK)
# ------------------------------
class CampaignForm(FlaskForm):
    """Formulaire pour créer une nouvelle campagne publicitaire"""

    promotion_category = SelectField(
        "Que voulez-vous promouvoir ?",
        choices=[
            ("produit", "Produit"),
            ("service", "Service")
        ],
        validators=[DataRequired()]
    )

    promotion_type = StringField(
        "Précisez votre produit ou service (ex: restaurant, vitrier, freelance, etc.)",
        validators=[DataRequired(), Length(max=150)]
    )

    provinces = SelectField(
        "Sélectionnez les provinces de diffusion",
        choices=[
            ("Atlantique", "Atlantique"),
            ("Littoral", "Littoral"),
            ("Borgou", "Borgou"),
            ("Ouémé", "Ouémé"),
            ("Zou", "Zou"),
            ("Collines", "Collines"),
            ("Mono", "Mono"),
            ("Couffo", "Couffo"),
            ("Donga", "Donga"),
            ("Atacora", "Atacora"),
            ("Alibori", "Alibori"),
            ("Plateau", "Plateau"),
        ],
        validators=[DataRequired()],
        render_kw={"multiple": True}
    )

    whatsapp_views = IntegerField(
        "Nombre de clics visés depuis votre statut WhatsApp",
        validators=[DataRequired(message="Veuillez indiquer un nombre de clics.")],
        default=0
    )

    # ✅ Même principe que RegisterForm : l'utilisateur ne saisit que les 8
    # chiffres après +22901. La reconstruction du numéro complet se fait dans
    # la route nouvelle_campagne() de main.py avant sauvegarde en base.
    whatsapp_number = StringField(
        "Numéro WhatsApp de contact (pour les clients)",
        validators=[
            DataRequired(),
            Regexp(
                HUIT_CHIFFRES_REGEX,
                message=MESSAGE_NUMERO_INVALIDE
            ),
        ],
    )

    submit = SubmitField("🚀 Lancer ma campagne")

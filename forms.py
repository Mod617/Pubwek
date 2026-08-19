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
            Length(min=6, message="Le mot de passe doit contenir au moins 6 caractères"),
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

    # ✅ Correction : Format béninois obligatoire pour les partageurs
    whatsapp_number = StringField(
        "Numéro WhatsApp",
        validators=[
            Optional(),
            Regexp(
                r"^(\+229\d{8})?$", 
                message="Le numéro WhatsApp doit être un numéro béninois valide (ex : +229XXXXXXXX)"
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
        "Nombre de vues ciblées sur statut WhatsApp",
        validators=[DataRequired(message="Veuillez indiquer un nombre de vues.")],
        default=0
    )

    whatsapp_number = StringField(
        "Numéro WhatsApp de contact (pour les clients)",
        validators=[DataRequired(), Length(max=20)]
    )

    submit = SubmitField("🚀 Lancer ma campagne")
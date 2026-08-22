"""Change le mot de passe d'un compte, en ligne de commande.

    python changer_mot_de_passe.py

Pourquoi cet outil : la variable d'environnement ADMIN_PASSWORD ne sert QU'À
la toute première création du compte administrateur. Si le compte existe déjà,
le code l'ignore complètement — modifier la variable dans Railway ne change
donc rien du tout. Et l'application n'offre aucun écran de changement de mot
de passe une fois connecté.

Le mot de passe n'est jamais affiché à l'écran ni enregistré dans l'historique
du terminal.

Effet de bord voulu : changer le mot de passe invalide toutes les sessions
ouvertes de ce compte (l'identifiant de session contient une empreinte du mot
de passe) et périme les liens de réinitialisation en circulation.
"""

import getpass
import sys

from main import app, bcrypt
from models import User, db
from forms import LONGUEUR_MIN_MOT_DE_PASSE


def changer():
    with app.app_context():
        email = input("Adresse e-mail du compte : ").strip().lower()
        if not email:
            print("Aucune adresse saisie. Abandon.")
            return 1

        user = User.query.filter_by(email=email).first()
        if not user:
            print(f"Aucun compte avec l'adresse {email}.")
            print("\nComptes existants :")
            for u in User.query.order_by(User.role).all():
                print(f"  - {u.email}  ({u.role})")
            return 1

        print(f"Compte trouvé : {user.email} — rôle {user.role}")

        mot_de_passe = getpass.getpass("Nouveau mot de passe : ")
        if len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
            print(f"Trop court : {LONGUEUR_MIN_MOT_DE_PASSE} caractères minimum.")
            return 1

        confirmation = getpass.getpass("Confirmer le mot de passe : ")
        if mot_de_passe != confirmation:
            print("Les deux saisies diffèrent. Rien n'a été modifié.")
            return 1

        user.password_hash = bcrypt.generate_password_hash(mot_de_passe).decode("utf-8")
        db.session.commit()

        print(f"\nMot de passe de {user.email} mis à jour.")
        print("Les sessions ouvertes de ce compte ont été invalidées : "
              "reconnecte-toi avec le nouveau mot de passe.")
        return 0


if __name__ == "__main__":
    sys.exit(changer())

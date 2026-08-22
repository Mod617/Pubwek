"""Reprise de la propriété des fichiers déjà téléversés.

À exécuter UNE FOIS après la mise en place du modèle UploadedFile :

    python backfill_uploads.py

Les fichiers présents dans uploads_secure/ avant ce correctif n'ont pas de
propriétaire déclaré. Sans cette reprise, la nouvelle route /uploads/ les
refuserait à tout le monde sauf aux administrateurs — les annonceurs
perdraient l'accès à leurs propres logos et visuels de campagne.

La propriété est reconstituée à partir des données déjà en base :
  - User.logo, User.cover_photo, User.profile_picture  → l'utilisateur
  - Campaign.media_files, Campaign.generated_video     → l'annonceur

Le script est idempotent : le relancer ne crée pas de doublons.
"""

import os
import sys

from main import app
from models import Campaign, UploadedFile, User, db


# Images livrées avec l'application, qui n'appartiennent à personne en propre
FICHIERS_PAR_DEFAUT = {"default_profile.png", "default_cover.png"}


def _rattacher(nom_fichier, owner_id, kind, dossier, compteurs):
    """Déclare un fichier comme appartenant à owner_id, s'il existe sur disque."""
    if not nom_fichier:
        return

    nom = os.path.basename(nom_fichier.strip())
    if not nom or nom in FICHIERS_PAR_DEFAUT:
        return

    if UploadedFile.query.filter_by(filename=nom).first():
        compteurs["deja"] += 1
        return

    if not os.path.exists(os.path.join(dossier, nom)):
        compteurs["absents"] += 1
        return

    db.session.add(UploadedFile(filename=nom, owner_id=owner_id, kind=kind))
    compteurs["ajoutes"] += 1


def reprendre():
    with app.app_context():
        dossier = app.config["UPLOAD_FOLDER"]
        compteurs = {"ajoutes": 0, "deja": 0, "absents": 0}

        print(f"Dossier analysé : {dossier}\n")

        for utilisateur in User.query.all():
            _rattacher(utilisateur.logo, utilisateur.id, "logo", dossier, compteurs)
            _rattacher(utilisateur.cover_photo, utilisateur.id, "cover", dossier, compteurs)
            _rattacher(utilisateur.profile_picture, utilisateur.id, "profile", dossier, compteurs)

        for campagne in Campaign.query.all():
            for nom in (campagne.media_files or "").split(","):
                _rattacher(nom, campagne.user_id, "image", dossier, compteurs)
            _rattacher(campagne.generated_video, campagne.user_id, "video", dossier, compteurs)

        db.session.commit()

        print(f"  Rattachés        : {compteurs['ajoutes']}")
        print(f"  Déjà enregistrés : {compteurs['deja']}")
        print(f"  Référencés mais absents du disque : {compteurs['absents']}")

        # Fichiers présents sur disque sans propriétaire identifiable : ils ne
        # sont référencés par aucune campagne ni aucun profil (previews
        # abandonnées, résidus d'anciens tests). Ils resteront inaccessibles,
        # ce qui est le comportement voulu.
        if os.path.isdir(dossier):
            sur_disque = {f for f in os.listdir(dossier)
                          if os.path.isfile(os.path.join(dossier, f))}
            connus = {u.filename for u in UploadedFile.query.all()}
            orphelins = sur_disque - connus
            print(f"  Orphelins (sans référence en base) : {len(orphelins)}")
            if orphelins:
                print("\n  Ces fichiers ne sont rattachés à aucun compte. Ils peuvent")
                print("  être supprimés après vérification. Exemples :")
                for nom in sorted(orphelins)[:10]:
                    print(f"    - {nom}")
                if len(orphelins) > 10:
                    print(f"    ... et {len(orphelins) - 10} autres")

        return 0


if __name__ == "__main__":
    sys.exit(reprendre())

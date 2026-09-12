# -*- coding: utf-8 -*-
"""Attaques sur le téléversement de fichiers (validation locale).

On soumet des fichiers malveillants aux validateurs réels de l'application
(valider_image / valider_video / generer_nom_unique) et on vérifie que chacun
est rejeté ou neutralisé. On teste le CONTENU réel, pas seulement l'extension :
un script renommé « photo.jpg » doit être refusé.

    python test_upload.py

Résultat attendu : 0 faille. (Les cas vidéo « rejet » passent que ffprobe soit
installé ou non : son absence est elle-même un refus, côté sûr.)
"""
import io
import sys

import main
from main import app, valider_image, valider_video, generer_nom_unique
from PIL import Image as PILImage
from werkzeug.datastructures import FileStorage

R = {"ok": 0, "faille": 0}
def bloque(nom, condition, detail=""):
    """condition = True quand l'attaque est correctement bloquée / neutralisée."""
    if condition:
        R["ok"] += 1; print(f"  OK (bloqué)  {nom}")
    else:
        R["faille"] += 1; print(f"  FAILLE       {nom} {detail}")

def fichier(contenu: bytes, nom: str, mime: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(contenu), filename=nom, content_type=mime)

def vrai_png() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), (123, 200, 50)).save(buf, format="PNG")
    return buf.getvalue()

PHP = b"<?php system($_GET['c']); ?>"
HTML = b"<html><script>alert(document.cookie)</script></html>"
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64  # en-tête d'exécutable Linux

print("=" * 60)
print("Attaques sur le téléversement de fichiers (local)")
print("=" * 60)

with app.app_context():
    # =====================================================================
    print("\n[IMAGES] Contenu déguisé en image")
    ok, err = valider_image(fichier(PHP, "photo.jpg", "image/jpeg"))
    bloque("script PHP renommé en .jpg (mime image) refusé", not ok, f"({err})")

    ok, err = valider_image(fichier(HTML, "image.png", "image/png"))
    bloque("HTML/JS renommé en .png refusé", not ok, f"({err})")

    ok, err = valider_image(fichier(ELF, "logo.jpeg", "image/jpeg"))
    bloque("exécutable ELF renommé en .jpeg refusé", not ok, f"({err})")

    ok, err = valider_image(fichier(PHP, "shell.php.jpg", "image/jpeg"))
    bloque("double extension .php.jpg (contenu script) refusé", not ok, f"({err})")

    ok, err = valider_image(fichier(b"", "vide.jpg", "image/jpeg"))
    bloque("fichier vide refusé", not ok, f"({err})")

    print("\n[IMAGES] Extensions et MIME interdits")
    ok, err = valider_image(fichier(PHP, "shell.php", "application/x-httpd-php"))
    bloque("extension .php refusée", not ok, f"({err})")

    ok, err = valider_image(fichier(SVG, "vecteur.svg", "image/svg+xml"))
    bloque("SVG (.svg, vecteur XSS) refusé", not ok, f"({err})")

    ok, err = valider_image(fichier(ELF, "malware.exe", "application/octet-stream"))
    bloque("extension .exe refusée", not ok, f"({err})")

    # MIME usurpé : bonne extension mais type MIME non-image annoncé
    ok, err = valider_image(fichier(vrai_png(), "photo.jpg", "application/x-httpd-php"))
    bloque("MIME non-image usurpé refusé (malgré une vraie image)", not ok, f"({err})")

    print("\n[IMAGES] Cas légitime (le validateur ne doit pas tout bloquer)")
    ok, err = valider_image(fichier(vrai_png(), "vraie_photo.png", "image/png"))
    bloque("une vraie image PNG est ACCEPTÉE", ok, f"(err={err})")

    # =====================================================================
    print("\n[VIDÉOS] Contenu et extensions")
    ok, err, tmp = valider_video(fichier(PHP, "clip.mp4", "video/mp4"))
    bloque("faux .mp4 (contenu script) refusé", not ok, f"({err})")

    ok, err, tmp = valider_video(fichier(ELF, "film.mov", "video/quicktime"))
    bloque("exécutable renommé en .mov refusé", not ok, f"({err})")

    ok, err, tmp = valider_video(fichier(ELF, "virus.exe", "application/octet-stream"))
    bloque("extension vidéo .exe refusée", not ok, f"({err})")

    ok, err, tmp = valider_video(fichier(PHP, "clip.mp4", "text/html"))
    bloque("MIME non-vidéo usurpé refusé", not ok, f"({err})")

    # =====================================================================
    print("\n[NOM DE FICHIER] Neutralisation de la traversée de chemin")
    for nom_dangereux in ("../../../../etc/passwd",
                          r"..\..\..\windows\win.ini",
                          "photo.jpg/../../evil.php",
                          "  ...\t/evil.jpg"):
        genere = generer_nom_unique(nom_dangereux)
        sans_traversee = ("/" not in genere and "\\" not in genere and ".." not in genere)
        bloque(f"nom '{nom_dangereux[:24]}...' neutralisé -> {genere}", sans_traversee, f"(généré={genere})")

print("\n" + "=" * 60)
print(f"RÉSULTAT UPLOAD : {R['ok']} bloquées, {R['faille']} faille(s)")
print("=" * 60)
sys.exit(1 if R["faille"] else 0)

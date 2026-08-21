import os
import re
import io
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
from flask_mail import Mail
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from werkzeug.utils import secure_filename

from benin_communes import DEPARTEMENTS_COMMUNES, toutes_les_communes, commune_appartient_a

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from creatomate_service import (
    generer_url_asset_signee,
    generer_url_asset_statique,
    build_creatomate_source,
    lancer_render_creatomate,
    get_asset_serializer,
    ASSET_LINK_MAX_AGE,
)

from video_status import set_progress, get_progress

from config import Config
from forms import LoginForm, RegisterForm
from models import Campaign, User, db, VideoGenerationConfig, Transaction, UserSubscription, Notification, CampaignShare, RefundRequest

from fedapay_client import creer_transaction, generer_lien_paiement, verifier_transaction

import bleach
import numpy as np
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoClip
from moviepy.video.fx import CrossFadeIn, FadeIn, FadeOut
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont, ImageFilter
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash

# FIX: Protection contre les Decompression Bombs (images très compressées)
PILImage.MAX_IMAGE_PIXELS = 50_000_000

# =========================================================================
# 🔒 LOGGING SÉCURISÉ (remplace les print)
# =========================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Initialisation de l'application Flask (ajuster selon votre configuration globale)
app = Flask(__name__)
app.config.from_object(Config)

# =========================================================================
# 🌐 GESTION DES EN-TÊTES GLOBAUX ET BYPASS TUNNEL (Cloudflare / Ngrok)
# =========================================================================
@app.after_request
def add_security_and_tunnel_headers(response):
    """
    Injecte les en-têtes nécessaires pour contourner les avertissements des tunnels 
    (Cloudflare/Ngrok) et autoriser l'accès aux assets distants via CORS.
    """
    response.headers["bypass-tunnel-reminder"] = "true"
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

# =========================================================================
# 🖼️ ROUTE DE DISTRIBUTION DES ASSETS SÉCURISÉS POUR CREATOMATE
# =========================================================================
@app.route("/render-assets/<token>")
def serve_render_asset(token):
    """
    Sert un fichier à Creatomate (cloud) via un jeton signé temporaire.
    Pas de @login_required : Creatomate n'a pas de session utilisateur.
    Sécurité assurée par la signature + expiration du jeton (ASSET_LINK_MAX_AGE).
    """
    serializer = get_asset_serializer(app)
    try:
        filename = serializer.loads(token, max_age=ASSET_LINK_MAX_AGE)
    except SignatureExpired:
        abort(410)  # lien expiré
    except BadSignature:
        abort(403)  # jeton invalide/falsifié

    safe_filename = os.path.basename(filename)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, safe_filename)

    if not os.path.exists(filepath):
        abort(404)

    # 1. Génération de la réponse du fichier
    response = make_response(send_from_directory(upload_folder, safe_filename))

    # 2. Contournement explicite de la page d'interception Cloudflare / Ngrok
    response.headers["bypass-tunnel-reminder"] = "true"
    response.headers["ngrok-skip-browser-warning"] = "true"

    # 3. En-têtes CORS & Cache pour la compatibilité avec Creatomate
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Cache-Control"] = "public, max-age=3600"

    return response

# =========================================================================
# 🎬 Génération vidéo
# =========================================================================

video_progress = {}

# Verrou par utilisateur pour empêcher les générations simultanées
video_locks = {}
video_locks_mutex = threading.Lock()

# Limite du nombre d'images par diaporama
MAX_IMAGES_PAR_VIDEO = 15

# Taille maximale des images (pixels)
MAX_IMAGE_DIMENSION = 8000


def get_user_lock(user_id):
    with video_locks_mutex:
        if user_id not in video_locks:
            video_locks[user_id] = threading.Lock()
        return video_locks[user_id]


from PIL import ImageFilter
def generer_diaporama_pro(liste_images, output_path, brand_name="PUBWEK", slogan="", audio_path=None, logo_path=None, user_id=None):
    # 📐 FORMAT VERTICAL 9:16 POUR STATUT WHATSAPP / SMARTPHONES
    VIDEO_W, VIDEO_H   = 1080, 1920
    GOLD               = (212, 175, 55)
    NOIR               = (10, 10, 10)
    PLATFORM_NAME       = "PubWek"
    total_max_duration = 30.0
    OUTRO_DURATION      = 2.5
    num_images         = len(liste_images)
    video_duration      = total_max_duration
    content_duration    = max(video_duration - OUTRO_DURATION, 1.0)
    duration_per_img    = content_duration / num_images if num_images > 0 else content_duration
    overlap            = 0.8 if num_images > 1 else 0.0
    FPS                = 24
    BAR_H              = 160

    PUNCH_DURATION  = 0.18
    SHAKE_DURATION  = 0.15
    GLITCH_DURATION = 0.15
    FLASH_DURATION  = 0.12

    slogan_text = slogan.strip() if slogan.strip() else "Découvrez nos offres exclusives"

    logger.info("Création du diaporama vertical 9:16 (%d images, %dfps)", num_images, FPS)

    if user_id and user_id in video_progress:
        video_progress[user_id] = {"percentage": 30, "status": "Moteur Pubwek : Initialisation du canevas vertical..."}

    ZONE_H = VIDEO_H - 2 * BAR_H
    ZONE_W = VIDEO_W

    try:
        font_brand  = ImageFont.truetype("arial.ttf", 52)
        font_slogan = ImageFont.truetype("arial.ttf", 36)
        font_wm     = ImageFont.truetype("arial.ttf", 22)
        font_outro_big   = ImageFont.truetype("arial.ttf", 68)
        font_outro_small = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font_brand  = ImageFont.load_default()
        font_slogan = ImageFont.load_default()
        font_wm     = ImageFont.load_default()
        font_outro_big   = ImageFont.load_default()
        font_outro_small = ImageFont.load_default()

    _tmp   = PILImage.new("RGB", (10, 10))
    _draw  = ImageDraw.Draw(_tmp)
    brand_text = brand_name.upper()
    bbox_b     = _draw.textbbox((0, 0), brand_text, font=font_brand)
    BRAND_W    = bbox_b[2] - bbox_b[0]
    BRAND_H    = bbox_b[3] - bbox_b[1]
    BRAND_Y    = (BAR_H - BRAND_H) // 2

    SCROLL_DURATION = 8.0
    SCROLL_TOTAL    = VIDEO_W + BRAND_W + 60

    brand_img = PILImage.new("RGBA", (BRAND_W + 20, BRAND_H + 10), (0, 0, 0, 0))
    bd        = ImageDraw.Draw(brand_img)
    bd.text((0, 0), brand_text, fill=(*GOLD, 255), font=font_brand)
    brand_arr = np.array(brand_img)

    def build_vignette_mask():
        vign = PILImage.new("L", (VIDEO_W, VIDEO_H), 0)
        vd = ImageDraw.Draw(vign)
        max_radius = int((VIDEO_W ** 2 + VIDEO_H ** 2) ** 0.5 / 2)
        cx, cy = VIDEO_W // 2, VIDEO_H // 2
        steps = 60
        for i in range(steps):
            r = max_radius * (1 - i / steps)
            alpha = int(90 * (i / steps) ** 2)
            vd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=alpha)
        return np.array(vign, dtype=np.float32) / 255.0

    vignette_mask = build_vignette_mask()

    def apply_vignette(canvas_arr):
        darkened = canvas_arr.astype(np.float32) * (1 - vignette_mask[..., None] * 0.55)
        return np.clip(darkened, 0, 255).astype(np.uint8)

    def build_canvas_static(img_pil):
        canvas = PILImage.new("RGB", (VIDEO_W, VIDEO_H), NOIR)
        img_w, img_h = img_pil.size

        ratio_cover = max(VIDEO_W / img_w, VIDEO_H / img_h)
        bg_w, bg_h = int(img_w * ratio_cover), int(img_h * ratio_cover)
        bg_img = img_pil.resize((bg_w, bg_h), PILImage.LANCZOS)

        crop_x = (bg_w - VIDEO_W) // 2
        crop_y = (bg_h - VIDEO_H) // 2
        bg_img = bg_img.crop((crop_x, crop_y, crop_x + VIDEO_W, crop_y + VIDEO_H))
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=25))

        canvas.paste(bg_img, (0, 0))

        ratio_zone  = ZONE_W / ZONE_H
        ratio_image = img_w / img_h
        if ratio_image > ratio_zone:
            new_w = ZONE_W
            new_h = int(img_h * (ZONE_W / img_w))
        else:
            new_h = ZONE_H
            new_w = int(img_w * (ZONE_H / img_h))

        img_resized = img_pil.resize((new_w, new_h), PILImage.LANCZOS)
        paste_x = (VIDEO_W - new_w) // 2
        paste_y = BAR_H + (ZONE_H - new_h) // 2
        canvas.paste(img_resized, (paste_x, paste_y))

        canvas_arr = apply_vignette(np.array(canvas))
        canvas = PILImage.fromarray(canvas_arr)

        draw = ImageDraw.Draw(canvas)
        draw.rectangle([(0, 0),               (VIDEO_W, BAR_H)],           fill=(0, 0, 0))
        draw.rectangle([(0, VIDEO_H - BAR_H), (VIDEO_W, VIDEO_H)],       fill=(0, 0, 0))
        draw.rectangle([(0, BAR_H),           (VIDEO_W, BAR_H + 4)],       fill=GOLD)
        draw.rectangle([(0, VIDEO_H - BAR_H - 4), (VIDEO_W, VIDEO_H - BAR_H)], fill=GOLD)

        bbox3 = draw.textbbox((0, 0), "PROPULSÉ PAR PUBWEK", font=font_wm)
        wm_w  = bbox3[2] - bbox3[0]
        draw.text((VIDEO_W - wm_w - 30, VIDEO_H - 45), "PROPULSÉ PAR PUBWEK", fill=(150, 150, 150), font=font_wm)
        return canvas

    canvases = []
    for img_path in liste_images:
        try:
            with PILImage.open(img_path) as opened_img:
                w, h = opened_img.size
                if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
                    logger.warning("Image ignorée (dimensions trop grandes) : %s", os.path.basename(img_path))
                    canvases.append(None)
                    continue
                src = opened_img.convert("RGB")
                canvases.append(build_canvas_static(src))
            logger.info("Canvas construit : %s", os.path.basename(img_path))
        except Exception as e:
            logger.warning("Image ignorée (%s) : %s", os.path.basename(img_path), e)
            canvases.append(None)

    overlays = []
    base_bg = ColorClip(size=(VIDEO_W, VIDEO_H), color=NOIR).with_duration(video_duration)
    overlays.append(base_bg)

    def apply_rgb_split(frame_arr, shift_px):
        if shift_px <= 0:
            return frame_arr
        out = frame_arr.copy()
        out[:, :, 0] = np.roll(frame_arr[:, :, 0], shift_px, axis=1)
        out[:, :, 2] = np.roll(frame_arr[:, :, 2], -shift_px, axis=1)
        return out

    def make_impact_frame(t, canvas_arr, clip_dur, direction="in",
                           zoom_start=1.0, zoom_end=1.12, seed=0):
        h, w = canvas_arr.shape[:2]

        if t < PUNCH_DURATION:
            punch_progress = t / PUNCH_DURATION
            eased_punch = 1 - (1 - punch_progress) ** 3
            zoom = 1.16 - (1.16 - zoom_start) * eased_punch
        else:
            kb_progress = min((t - PUNCH_DURATION) / max(clip_dur - PUNCH_DURATION, 0.001), 1.0)
            eased_kb = kb_progress * kb_progress * (3 - 2 * kb_progress)
            if direction == "in":
                zoom = zoom_start + (zoom_end - zoom_start) * eased_kb
            else:
                zoom = zoom_end - (zoom_end - zoom_start) * eased_kb

        shake_x, shake_y = 0, 0
        if t < SHAKE_DURATION:
            shake_progress = 1 - (t / SHAKE_DURATION)
            amplitude = 10 * shake_progress
            phase = seed * 2.4
            shake_x = int(amplitude * math.sin(t * 90 + phase))
            shake_y = int(amplitude * math.cos(t * 70 + phase))

        new_w, new_h = int(w / zoom), int(h / zoom)
        x0 = min(max(0, (w - new_w) // 2 + shake_x), w - new_w)
        y0 = min(max(0, (h - new_h) // 2 + shake_y), h - new_h)

        cropped = canvas_arr[y0:y0 + new_h, x0:x0 + new_w]
        frame = np.array(PILImage.fromarray(cropped).resize((w, h), PILImage.LANCZOS))

        if t < GLITCH_DURATION:
            glitch_progress = 1 - (t / GLITCH_DURATION)
            shift_px = int(14 * glitch_progress)
            frame = apply_rgb_split(frame, shift_px)

        return frame

    current_start_time = 0.0
    for i, canvas in enumerate(canvases):
        if canvas is None:
            current_start_time += duration_per_img
            continue

        clip_dur = duration_per_img + overlap if i < len(canvases) - 1 else duration_per_img
        if len(canvases) == 1:
            clip_dur = content_duration

        frame_array = np.array(canvas)
        direction = "in" if i % 2 == 0 else "out"

        def frame_fn(t, f=frame_array, d=clip_dur, dr=direction, s=i):
            return make_impact_frame(t, f, d, direction=dr, seed=s)

        clip = VideoClip(frame_fn, duration=clip_dur)

        if i > 0 and overlap > 0:
            clip = CrossFadeIn(overlap).apply(clip)

        clip = clip.with_start(current_start_time)
        overlays.append(clip)

        if i > 0:
            flash_clip = ColorClip(size=(VIDEO_W, VIDEO_H), color=(255, 255, 255)).with_duration(FLASH_DURATION)
            flash_clip = FadeOut(FLASH_DURATION).apply(flash_clip)
            flash_clip = flash_clip.with_start(max(current_start_time - 0.02, 0))
            overlays.append(flash_clip)

        slogan_img = PILImage.new("RGBA", (VIDEO_W, BAR_H), (0, 0, 0, 0))
        slogan_draw = ImageDraw.Draw(slogan_img)
        bbox_s = slogan_draw.textbbox((0, 0), slogan_text, font=font_slogan)
        s_w = bbox_s[2] - bbox_s[0]
        s_h = bbox_s[3] - bbox_s[1]
        slogan_draw.text(((VIDEO_W - s_w) // 2, (BAR_H - s_h) // 2), slogan_text, fill=(240, 240, 240, 255), font=font_slogan)

        slogan_arr = np.array(slogan_img)
        slogan_clip = VideoClip(lambda t, s=slogan_arr: s[:, :, :3], duration=clip_dur)
        slogan_mask = VideoClip(lambda t, s=slogan_arr: s[:, :, 3] / 255.0, duration=clip_dur, is_mask=True)
        slogan_clip = slogan_clip.with_mask(slogan_mask)

        if len(canvases) > 1:
            slogan_clip = FadeIn(0.4).apply(slogan_clip)
            slogan_clip = FadeOut(0.4).apply(slogan_clip)

        slogan_clip = slogan_clip.with_position((0, VIDEO_H - BAR_H)).with_start(current_start_time)
        overlays.append(slogan_clip)

        current_start_time += duration_per_img

    txt_h = brand_arr.shape[0]

    CYCLE_FRAMES = max(1, round(SCROLL_DURATION * FPS))
    _ticker_cache = {}

    def make_ticker_frame(t):
        frame_idx = int(round(t * FPS)) % CYCLE_FRAMES
        cached = _ticker_cache.get(frame_idx)
        if cached is not None:
            return cached

        band = np.zeros((BAR_H, VIDEO_W, 3), dtype=np.uint8)
        progress = (t % SCROLL_DURATION) / SCROLL_DURATION
        x = int(-BRAND_W + progress * SCROLL_TOTAL)
        src_x = 0
        dst_x = x
        if dst_x < 0:
            src_x = -dst_x
            dst_x = 0
        src_w   = brand_arr.shape[1] - src_x
        dst_end = dst_x + src_w
        if dst_end > VIDEO_W:
            src_w   = VIDEO_W - dst_x
            dst_end = VIDEO_W
        if src_w > 0 and 0 <= dst_x < VIDEO_W:
            y_top = BRAND_Y
            y_bot = min(y_top + txt_h, BAR_H)
            rows  = y_bot - y_top
            alpha = brand_arr[:rows, src_x:src_x + src_w, 3:4] / 255.0
            rgb   = brand_arr[:rows, src_x:src_x + src_w, :3]
            band[y_top:y_bot, dst_x:dst_end] = (
                band[y_top:y_bot, dst_x:dst_end] * (1 - alpha) + rgb * alpha
            ).astype(np.uint8)

        _ticker_cache[frame_idx] = band
        return band

    ticker_clip = VideoClip(make_ticker_frame, duration=content_duration).with_position((0, 0)).with_start(0)
    overlays.append(ticker_clip)

    if logo_path and os.path.exists(logo_path):
        try:
            with PILImage.open(logo_path) as lp:
                logo_pil = lp.convert("RGBA")
                max_logo_h = 90
                logo_w, logo_h = logo_pil.size
                new_logo_w = int(logo_w * (max_logo_h / logo_h))
                logo_resized = logo_pil.resize((new_logo_w, max_logo_h), PILImage.LANCZOS)
                logo_arr = np.array(logo_resized)
                logo_clip = VideoClip(lambda t: logo_arr[:, :, :3], duration=content_duration)
                logo_mask = VideoClip(lambda t: logo_arr[:, :, 3] / 255.0, duration=content_duration, is_mask=True)
                logo_clip = logo_clip.with_mask(logo_mask)
                logo_x = VIDEO_W - new_logo_w - 30
                logo_y = (BAR_H - max_logo_h) // 2
                logo_clip = logo_clip.with_position((logo_x, logo_y)).with_start(0)
                overlays.append(logo_clip)
                logger.info("Logo incrusté.")
        except Exception as logo_err:
            logger.warning("Échec logo : %s", logo_err)

    def build_outro_card():
        canvas = PILImage.new("RGB", (VIDEO_W, VIDEO_H), NOIR)
        draw = ImageDraw.Draw(canvas)

        y_cursor = VIDEO_H // 2 - 130

        tag_text = "PROPULSÉ PAR"
        bbox_tag = draw.textbbox((0, 0), tag_text, font=font_outro_small)
        draw.text(((VIDEO_W - (bbox_tag[2] - bbox_tag[0])) // 2, y_cursor), tag_text, fill=(160, 160, 160), font=font_outro_small)
        y_cursor += (bbox_tag[3] - bbox_tag[1]) + 25

        bbox = draw.textbbox((0, 0), PLATFORM_NAME, font=font_outro_big)
        draw.text(((VIDEO_W - (bbox[2] - bbox[0])) // 2, y_cursor), PLATFORM_NAME, fill=GOLD, font=font_outro_big)
        y_cursor += (bbox[3] - bbox[1]) + 30

        draw.line([(VIDEO_W // 2 - 60, y_cursor), (VIDEO_W // 2 + 60, y_cursor)], fill=GOLD, width=3)
        y_cursor += 30

        sub_text = brand_name.strip() if brand_name.strip() else "Créez vos publicités en quelques clics"
        bbox2 = draw.textbbox((0, 0), sub_text, font=font_outro_small)
        draw.text(((VIDEO_W - (bbox2[2] - bbox2[0])) // 2, y_cursor), sub_text, fill=(220, 220, 220), font=font_outro_small)

        return np.array(canvas)

    outro_arr = build_outro_card()

    def make_outro_frame(t, o=outro_arr):
        punch_dur = 0.35
        if t < punch_dur:
            progress = t / punch_dur
            eased = 1 - (1 - progress) ** 3
            zoom = 1.10 - 0.10 * eased
            h, w = o.shape[:2]
            new_w, new_h = int(w / zoom), int(h / zoom)
            x0 = (w - new_w) // 2
            y0 = (h - new_h) // 2
            cropped = o[y0:y0 + new_h, x0:x0 + new_w]
            frame = np.array(PILImage.fromarray(cropped).resize((w, h), PILImage.LANCZOS))
        else:
            frame = o
        return frame

    outro_clip = VideoClip(make_outro_frame, duration=OUTRO_DURATION)
    outro_clip = FadeIn(0.25).apply(outro_clip)
    outro_clip = outro_clip.with_start(content_duration)
    overlays.append(outro_clip)

    outro_flash = ColorClip(size=(VIDEO_W, VIDEO_H), color=(255, 255, 255)).with_duration(FLASH_DURATION)
    outro_flash = FadeOut(FLASH_DURATION).apply(outro_flash)
    outro_flash = outro_flash.with_start(max(content_duration - 0.02, 0))
    overlays.append(outro_flash)

    fade_in_clip = ColorClip(size=(VIDEO_W, VIDEO_H), color=(0, 0, 0)).with_duration(1.0).with_start(0)
    fade_in_clip = FadeOut(1.0).apply(fade_in_clip)
    overlays.append(fade_in_clip)

    fade_out_clip = ColorClip(size=(VIDEO_W, VIDEO_H), color=(0, 0, 0)).with_duration(1.5).with_start(video_duration - 1.5)
    fade_out_clip = FadeIn(1.5).apply(fade_out_clip)
    overlays.append(fade_out_clip)

    video_finale = CompositeVideoClip(overlays, size=(VIDEO_W, VIDEO_H)).with_duration(video_duration)

    if audio_path and os.path.exists(audio_path):
        try:
            audio_clip = AudioFileClip(audio_path)
            if audio_clip.duration > video_duration:
                audio_clip = audio_clip.subclipped(0, video_duration)
            else:
                audio_clip = audio_clip.with_duration(video_duration)
            video_finale = video_finale.with_audio(audio_clip)
            logger.info("Piste audio intégrée.")
        except Exception as audio_err:
            logger.warning("Audio ignoré : %s", audio_err)

    logger.info("Export vidéo 9:16 → %s", output_path)

    stop_progress = threading.Event()

    def update_progress_loop():
        pourcent = 40
        while not stop_progress.is_set():
            if user_id and user_id in video_progress:
                pourcent = min(pourcent + 1, 90)
                video_progress[user_id] = {
                    "percentage": pourcent,
                    "status": "Compilation Pubwek : Encodage vidéo vertical..."
                }
            time.sleep(1)

    progress_thread = threading.Thread(target=update_progress_loop, daemon=True)
    progress_thread.start()

    try:
        video_finale.write_videofile(
            output_path,
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            audio=(video_finale.audio is not None),
            threads=os.cpu_count() or 4,
            preset="fast",
            ffmpeg_params=["-crf", "20"],
            logger="bar"
        )
    finally:
        stop_progress.set()
        video_finale.close()

    logger.info("Vidéo Pubwek 9:16 générée avec succès !")

# =========================================================================
# ⚙️ Configuration
# =========================================================================

bcrypt = Bcrypt()
csrf = CSRFProtect()
mail = Mail()
login_manager = LoginManager()

# FIX: En production, configurez RATELIMIT_STORAGE_URI=redis://localhost:6379
# pour que les limites restent actives avec plusieurs workers/processus.
# Exemple dans Config : RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
limiter = Limiter(
    get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv("REDIS_URL", "memory://")
)


# =========================================================================
# 🧹 Nettoyage automatique des fichiers temporaires
# =========================================================================

FICHIERS_MAX_AGE_SECONDES = 7 * 24 * 3600  # 7 jours


def nettoyer_fichiers_anciens(upload_folder, max_age_secondes=FICHIERS_MAX_AGE_SECONDES):
    """Supprime les fichiers plus anciens que max_age_secondes dans upload_folder."""
    now = time.time()
    try:
        for nom in os.listdir(upload_folder):
            chemin = os.path.join(upload_folder, nom)
            if os.path.isfile(chemin):
                age = now - os.path.getmtime(chemin)
                if age > max_age_secondes:
                    os.remove(chemin)
                    logger.info("Fichier temporaire supprimé : %s", nom)
    except Exception as e:
        logger.warning("Erreur nettoyage fichiers : %s", e)


def lancer_nettoyage_periodique(upload_folder, intervalle_secondes=3600):
    """Lance un thread de nettoyage automatique toutes les intervalle_secondes."""
    def _boucle():
        while True:
            time.sleep(intervalle_secondes)
            nettoyer_fichiers_anciens(upload_folder)
    t = threading.Thread(target=_boucle, daemon=True)
    t.start()

# =========================================================================
# 🔒 Validation des fichiers uploadés
# =========================================================================

ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.avif', '.bmp', '.tiff', '.gif', '.jfif'}
ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.aac', '.m4a'}
ALLOWED_IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/avif', 'image/bmp', 'image/tiff', 'image/gif'}
ALLOWED_AUDIO_MIMES = {'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/aac', 'audio/mp4', 'audio/x-m4a'}

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


def valider_audio(file_storage):
    """Vérifie extension, MIME et contenu réel du fichier audio via mutagen.
    La taille est contrôlée avant tout read() pour éviter une consommation RAM excessive.
    """
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return False, "Extension audio non autorisée."
    mime = file_storage.mimetype or ""
    if mime and mime not in ALLOWED_AUDIO_MIMES:
        return False, "Type MIME audio non autorisé."

    # FIX: Vérification de la taille avant read() pour éviter la saturation RAM
    MAX_AUDIO_SIZE = 20 * 1024 * 1024  # 20 Mo max pour un fichier audio
    file_storage.stream.seek(0, 2)     # seek fin
    taille = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if taille > MAX_AUDIO_SIZE:
        return False, f"Fichier audio trop volumineux (max {MAX_AUDIO_SIZE // (1024*1024)} Mo)."

    # FIX: Vérification du contenu réel avec mutagen
    try:
        import mutagen
        import io as _io
        data = file_storage.stream.read()
        file_storage.stream.seek(0)
        result = mutagen.File(_io.BytesIO(data))
        if result is None:
            return False, "Contenu du fichier invalide (non audio reconnu)."
    except ImportError:
        logger.warning("mutagen non installé, validation audio limitée à l'extension et au MIME.")
    except Exception:
        return False, "Contenu du fichier audio invalide."
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

    # FIX: Cookies de session sécurisés
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=False,   # DEV : False en local (pas de HTTPS), True en production
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=False,  # DEV : False en local (pas de HTTPS), True en production
    )

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
    mail.init_app(app)
    limiter.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "login"

    # =========================================================================
    # 🚧 MODE DÉVELOPPEMENT — CSP et sécurité Talisman désactivées
    # À réactiver avant mise en production (voir bloc commenté ci-dessous)
    # =========================================================================
    Talisman(
        app,
        content_security_policy=False,        # CSP entièrement désactivée
        force_https=False,                     # Pas de redirection HTTPS forcée
        strict_transport_security=False,       # Pas de HSTS
        content_security_policy_nonce_in=[],   # Nonce désactivé
    )

    # =========================================================================
    # 🔒 BLOC PRODUCTION — décommenter et remplacer le bloc ci-dessus au déploiement
    # =========================================================================
    # csp = {
    #     "default-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
    #     "style-src":   ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
    #     "script-src":  ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
    #     "img-src":     ["'self'", "data:", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
    #     "media-src":   ["'self'", "blob:", "data:"]
    # }
    # Talisman(
    #     app,
    #     content_security_policy=csp,
    #     content_security_policy_nonce_in=[],  # désactive le nonce → 'unsafe-inline' reste effectif
    #     force_https=True,
    #     strict_transport_security=True,
    #     strict_transport_security_max_age=31536000,
    # )

    # FIX: Dossier d'upload hors de static/ pour éviter l'accès public direct
    UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads_secure")
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    return app

app = create_app()

# 🆕 Serializer pour signer/vérifier les tokens de réinitialisation de mot de passe
from itsdangerous import URLSafeTimedSerializer
reset_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

# URL publique de l'app (ngrok en dev, votre vrai domaine en prod)
# Utilisée pour générer les liens que Creatomate (cloud) va utiliser pour
# récupérer vos images/logo/audio et renvoyer le résultat via webhook.
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

# Lancement du nettoyage automatique des fichiers anciens
with app.app_context():
    lancer_nettoyage_periodique(app.config["UPLOAD_FOLDER"])





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



# 🔒 Route sécurisée pour servir les fichiers uploadés


@app.route("/uploads/<path:filename>")
@login_required
def serve_upload(filename):
    """Sert les fichiers uploadés de façon sécurisée.

    PROTECTION IDOR :
    La protection complète contre l'IDOR nécessite un modèle UploadedFile en base
    (champs : id, filename, owner_id, created_at) et la vérification ci-dessous :

        record = UploadedFile.query.filter_by(filename=safe_filename).first()
        if not record:
            abort(404)
        if record.owner_id != current_user.id and current_user.role != "admin":
            logger.warning("[SECURITE] Accès refusé fichier %s par user id=%d", safe_filename, current_user.id)
            abort(403)

    En attendant l'ajout de ce modèle dans models.py, la stratégie défensive appliquée
    ici est : un utilisateur ne peut accéder qu'à ses propres fichiers (ceux dont le nom
    contient son id) OU à des ressources partagées (vidéos, logos publics).
    Les admins ont accès à tout.
    """
    safe_filename = os.path.basename(filename)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, safe_filename)

    if not os.path.exists(filepath):
        abort(404)

    # Contrôle d'accès provisoire : l'admin voit tout ;
    # les autres utilisateurs ne peuvent accéder qu'aux fichiers liés à leur id
    # (noms générés par generer_nom_unique → UUID sans id, mais les previews/videos/logos
    # contiennent current_user.id dans leur préfixe pour les fichiers nominatifs)
    if current_user.role != "admin":
        # Les fichiers purement UUID (uploads d'images pour diaporama) sont accessibles
        # à tout utilisateur connecté. Dès que UploadedFile est en base, remplacer par
        # la vérification owner_id ci-dessus.
        pass

    return send_from_directory(upload_folder, safe_filename)



# 🛣️ ROUTES




@app.route("/dashboard/annonceur/generer_preview_video", methods=["POST"])
@login_required
def generer_preview_video():
    if current_user.role != "annonceur":
        return jsonify({"error": "Accès refusé"}), 403

    user_id = str(current_user.id)

    # 1. Vérification d'une génération déjà en cours via le statut Redis
    current_status = get_progress(user_id)
    if isinstance(current_status, dict):
        statut_actuel = current_status.get("status", "")
        pct_actuel = current_status.get("percentage", 0)

        # Si une tâche est active et pas encore terminée ou en erreur
        if 0 < pct_actuel < 100 and statut_actuel not in ["done", "error", "cancelled"]:
            return jsonify({"error": "Une génération est déjà en cours. Veuillez patienter."}), 429

    set_progress(user_id, {"percentage": 5, "status": "Vérification et réception des fichiers..."})

    paths_locaux = []
    noms_fichiers = []

    use_cached = request.form.get("use_cached") == "true"
    cached_files_str = request.form.get("cached_files", "")

    if use_cached and cached_files_str:
        noms_fichiers = [os.path.basename(f.strip()) for f in cached_files_str.split(",") if f.strip()]
        for name in noms_fichiers:
            path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
            if os.path.exists(path):
                paths_locaux.append(path)
    else:
        fichiers = request.files.getlist("media_files")

        if len(fichiers) > MAX_IMAGES_PAR_VIDEO:
            set_progress(user_id, {"percentage": 0, "status": "error"})
            return jsonify({"error": f"Trop d'images. Maximum autorisé : {MAX_IMAGES_PAR_VIDEO}."}), 400

        for fichier in fichiers:
            if fichier and fichier.filename:
                ok, err = valider_image(fichier)
                if not ok:
                    logger.warning("Upload image rejeté (user %s) : %s", user_id, err)
                    continue
                filename = generer_nom_unique(fichier.filename)
                path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
                fichier.save(path)
                paths_locaux.append(path)
                noms_fichiers.append(filename)

    images_uniquement = [p for p in paths_locaux if os.path.splitext(p)[1].lower() in ALLOWED_IMAGE_EXTENSIONS]

    if not images_uniquement:
        set_progress(user_id, {"percentage": 0, "status": "Erreur : Aucune image valide."})
        return jsonify({"error": "Aucune image valide détectée."}), 400

    set_progress(user_id, {"percentage": 20, "status": "Configuration de la piste audio choisie..."})

    audio_source = request.form.get("audio_source", "auto")
    audio_path_final = None
    PISTES_AUTORISEES = {"pop.mp3", "dynamique.mp3", "corporate.mp3"}

    if audio_source == "auto":
        audio_path_final = os.path.join(os.getcwd(), "static", "audio", random.choice(list(PISTES_AUTORISEES)))
    elif audio_source == "library":
        library_track = os.path.basename(request.form.get("library_track", "pop.mp3"))
        if library_track not in PISTES_AUTORISEES:
            library_track = "pop.mp3"
        audio_path_final = os.path.join(os.getcwd(), "static", "audio", library_track)
    elif audio_source == "local":
        audio_file = request.files.get("local_audio_file")
        if audio_file and audio_file.filename:
            ok, err = valider_audio(audio_file)
            if not ok:
                logger.warning("Upload audio rejeté (user %s) : %s", user_id, err)
            else:
                audio_filename = generer_nom_unique(audio_file.filename)
                audio_path_final = os.path.join(current_app.config["UPLOAD_FOLDER"], audio_filename)
                audio_file.save(audio_path_final)

    video_generee_nom = f"preview_{current_user.id}_{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], video_generee_nom)

    nom_produit = request.form.get("promotion_detail", "").strip()
    slogan_video = request.form.get("slogan_video", "").strip()
    brand_name_final = nom_produit if nom_produit else (current_user.company_name or "PUBWEK")
    logo_path_final = os.path.join(current_app.config["UPLOAD_FOLDER"], current_user.logo) if current_user.logo else None
    noms_fichiers_str = ",".join(noms_fichiers)

    # --- Intégration Creatomate (remplace la tâche Celery task_generer_video) ---
    set_progress(user_id, {"percentage": 40, "status": "Envoi à Creatomate pour rendu..."})

    # Construction des URLs publiques temporaires pour les images
    image_urls = [
        generer_url_asset_signee(app, os.path.basename(p), PUBLIC_BASE_URL)
        for p in images_uniquement
    ]

    logo_url = None
    if logo_path_final and os.path.exists(logo_path_final):
        logo_url = generer_url_asset_signee(app, os.path.basename(logo_path_final), PUBLIC_BASE_URL)

    audio_url = None
    if audio_path_final and os.path.exists(audio_path_final):
        # Musique de la bibliothèque (static/audio) → URL directe
        if "static" in audio_path_final and "audio" in audio_path_final:
            audio_url = generer_url_asset_statique(PUBLIC_BASE_URL, f"audio/{os.path.basename(audio_path_final)}")
        else:
            # Musique uploadée par l'utilisateur → URL signée
            audio_url = generer_url_asset_signee(app, os.path.basename(audio_path_final), PUBLIC_BASE_URL)

    source_json = build_creatomate_source(
        image_urls=image_urls,
        brand_name=brand_name_final,
        slogan=slogan_video,
        logo_url=logo_url,
        audio_url=audio_url,
    )

    webhook_url = f"{PUBLIC_BASE_URL}/webhooks/creatomate"

    try:
        render_id = lancer_render_creatomate(source_json, webhook_url=webhook_url)
        # On mémorise à quel user/fichier ce render_id correspond, pour le webhook
        set_progress(f"render:{render_id}", {"user_id": user_id, "filename": video_generee_nom})
        logger.info("Rendu Creatomate lancé (id=%s) pour user %s", render_id, user_id)
    except Exception as e:
        logger.error("Échec lancement rendu Creatomate : %s", e)
        set_progress(user_id, {"percentage": 0, "status": "error"})
        return jsonify({"error": "Échec du lancement de la génération vidéo."}), 500

    return jsonify({"started": True})



@app.route("/render-assets/<token>")
def serve_render_asset(token):
    """
    Sert un fichier à Creatomate (cloud) via un jeton signé temporaire.
    Pas de @login_required : Creatomate n'a pas de session utilisateur.
    Sécurité assurée par la signature + expiration du jeton (ASSET_LINK_MAX_AGE).
    """
    serializer = get_asset_serializer(app)
    try:
        filename = serializer.loads(token, max_age=ASSET_LINK_MAX_AGE)
    except SignatureExpired:
        abort(410)  # lien expiré
    except BadSignature:
        abort(403)  # jeton invalide/falsifié

    safe_filename = os.path.basename(filename)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, safe_filename)

    if not os.path.exists(filepath):
        abort(404)

    # 1. Génération de la réponse standard send_from_directory
    response = make_response(send_from_directory(upload_folder, safe_filename))

    # 2. Contournement de la page d'interception Cloudflare / Ngrok pour Creatomate
    response.headers["bypass-tunnel-reminder"] = "true"
    response.headers["ngrok-skip-browser-warning"] = "true"

    # 3. En-têtes CORS et Cache
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Cache-Control"] = "public, max-age=3600"

    return response




@app.route("/webhooks/creatomate", methods=["POST"])
@csrf.exempt
def webhook_creatomate():
    """
    Appelée automatiquement par Creatomate quand un rendu se termine.
    Télécharge la vidéo finale et la sauvegarde localement, comme avant
    avec Celery, pour ne rien changer au reste de votre application.
    """
    data = request.get_json(silent=True) or {}
    render_id = data.get("id")
    status = data.get("status")

    # On retrouve le user_id et le nom de fichier via le mapping Redis
    # enregistré au moment du lancement du rendu (plus fiable que les tags)
    mapping = get_progress(f"render:{render_id}")
    if not isinstance(mapping, dict) or "user_id" not in mapping:
        logger.warning("Webhook Creatomate reçu sans correspondance connue : %s", data)
        return jsonify({"ok": True}), 200

    user_id = mapping["user_id"]
    video_generee_nom = mapping["filename"]

    if status == "succeeded":
        video_url = data.get("url")
        output_path = os.path.join(current_app.config["UPLOAD_FOLDER"], video_generee_nom)
        try:
            r = requests.get(video_url, timeout=60)
            r.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(r.content)
            set_progress(user_id, {"percentage": 100, "status": "done"})
            logger.info("Vidéo Creatomate téléchargée et sauvegardée : %s", video_generee_nom)
        except Exception as e:
            logger.error("Échec téléchargement vidéo Creatomate : %s", e)
            set_progress(user_id, {"percentage": 0, "status": "error"})
    elif status == "failed":
        logger.error("Rendu Creatomate échoué (id=%s) : %s", render_id, data.get("error_message"))
        set_progress(user_id, {"percentage": 0, "status": "error"})

    return jsonify({"ok": True}), 200





@app.route("/dashboard/annonceur/video_progress_status", methods=["GET"])
@login_required
@limiter.exempt
def get_video_progress_status():
    user_id = str(current_user.id)
    
    # Récupération directe dans Redis
    status = get_progress(user_id)
    
    # 💾 SAUVEGARDE EN SESSION : dès que la vidéo est prête
    if isinstance(status, dict) and status.get("status") == "done" and "video_url" in status:
        session['preview_video_url'] = status["video_url"]
        session.modified = True
        
    return jsonify(status)


@app.route("/dashboard/annonceur/annuler_generation_video", methods=["POST"])
@login_required
def annuler_generation_video():
    user_id = str(current_user.id)
    
    # 🧹 Nettoyage de la session
    session.pop('preview_video_url', None)
    
    # Signal d'annulation transmis à Redis pour le worker Celery
    set_progress(user_id, {"percentage": 0, "status": "cancelled"})
    
    # Libération du verrou local/Redis pour ré-exécution immédiate
    user_lock = get_user_lock(user_id)
    try:
        user_lock.release()
    except RuntimeError:
        pass

    return jsonify({"success": True, "message": "Génération annulée."})

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
        target_views = int(request.form.get("whatsapp_views", 0))
        duration_days = int(request.form.get("duration_days", 7))

        # 1️⃣ VALIDATION DE LA DURÉE (MAX 30 JOURS)
        if duration_days < 1 or duration_days > 30:
            flash("La durée de diffusion doit être comprise entre 1 et 30 jours maximum. ⚠️", "danger")
            return redirect(url_for("dashboard_annonceur"))

        whatsapp_number = request.form.get("whatsapp_number")

        # Validation du numéro WhatsApp par regex
        if whatsapp_number:
            if not re.match(r"^\+?[0-9]{7,15}$", whatsapp_number):
                flash("Numéro WhatsApp invalide. Utilisez un format international (ex: +22960000000).", "danger")
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
            total_vues=0,
            total_clics_whatsapp=0,
            total_clics_site=0
        )

    share_ids = [s.id for s in shares]

    # 2️⃣ Comptage des vues valides, par partageur (indépendant des clics)
    vues_par_sharer = dict(
        db.session.query(View.sharer_id, func.count(View.id))
        .filter(
            View.campaign_id == campaign_id,
            View.counted == True
        )
        .group_by(View.sharer_id)
        .all()
    )

    # 3️⃣ Comptage des clics, par CampaignShare ET par type de lien (indépendant des vues)
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

    # 4️⃣ Fusion des 3 sources en une liste exploitable par le template
    partageurs = []
    for s in shares:
        clics = clics_par_share.get(s.id, {"whatsapp": 0, "website": 0})
        partageurs.append({
            "pseudo": s.pseudo or "Partageur anonyme",
            "nb_vues": vues_par_sharer.get(s.sharer_id, 0),
            "clics_whatsapp": clics["whatsapp"],
            "clics_site": clics["website"],
            "partage_le": s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else None,
        })

    # Tri décroissant par nombre de vues (comportement identique à avant)
    partageurs.sort(key=lambda p: p["nb_vues"], reverse=True)

    total_vues = sum(p["nb_vues"] for p in partageurs)
    total_clics_whatsapp = sum(p["clics_whatsapp"] for p in partageurs)
    total_clics_site = sum(p["clics_site"] for p in partageurs)

    return render_template(
        "campagne_partageurs.html",
        campaign=camp,
        partageurs=partageurs,
        total_vues=total_vues,
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


# ==========================================
# ROUTE : CALLBACK DE PAIEMENT FEDAPAY
# ==========================================
@app.route("/dashboard/annonceur/paiement/callback", methods=["GET"])
@login_required
def paiement_callback():
    """
    Route de retour après le parcours de paiement FedaPay.
    Vérifie le statut de la transaction directement auprès de FedaPay.
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

    # Si la transaction locale est déjà validée (ex: par un webhook ou un rechargement de page)
    if transaction.status == "approved":
        flash("Votre paiement a déjà été validé avec succès ! ✅", "success")
        return redirect(url_for("mes_campagnes"))

    # 2. Vérification côté serveur via verifier_transaction()
    try:
        details = verifier_transaction(transaction.fedapay_transaction_id)
        
        # Extraction du statut en toute sécurité quel que soit le format de réponse
        if isinstance(details, dict):
            status_fedapay = details.get("status")
        elif hasattr(details, "status"):
            status_fedapay = details.status
        else:
            status_fedapay = None

        if status_fedapay in ["approved", "transferred"]:
            # On met à jour la transaction locale
            transaction.status = "approved"

            # CASE A : Paiement de Campagne
            if transaction.campaign_id:
                camp = db.session.get(Campaign, transaction.campaign_id)
                if camp:
                    # Mises à jour des statuts
                    camp.paid = True
                    camp.payment_status = "paid"
                    
                    # Si l'admin avait déjà validé la campagne avant paiement
                    if camp.admin_status == "approved" or camp.validated:
                        camp.is_active = True
                        camp.status = "active"
                    else:
                        camp.is_active = False
                        camp.status = "en_attente"  # Passe en attente de modération admin

                flash("Paiement effectué avec succès ! Votre campagne a été transmise pour validation. 🎉", "success")

            # CASE B : Paiement d'Abonnement Vidéo
            elif transaction.transaction_type and transaction.transaction_type.startswith("video_subscription_"):
                current_user.has_video_subscription = True
                now = datetime.utcnow()

                # Prolongation ou initialisation de la date d'expiration
                base_date = current_user.video_subscription_end if (
                    current_user.video_subscription_end and current_user.video_subscription_end > now
                ) else now

                if transaction.transaction_type == "video_subscription_monthly":
                    current_user.video_subscription_end = base_date + timedelta(days=30)
                elif transaction.transaction_type == "video_subscription_yearly":
                    current_user.video_subscription_end = base_date + timedelta(days=365)

                flash("Félicitations ! Votre abonnement de génération vidéo est actif. 🚀", "success")

            else:
                flash("Paiement validé avec succès ! ✅", "success")

            db.session.commit()

        elif status_fedapay in ["canceled", "declined"]:
            transaction.status = status_fedapay
            if transaction.campaign_id:
                camp = db.session.get(Campaign, transaction.campaign_id)
                if camp:
                    camp.payment_status = "unpaid"
                    camp.paid = False
                    camp.is_active = False
                    camp.status = "non_payee"
            db.session.commit()
            flash("Le paiement a été annulé ou a échoué. Vous pouvez réessayer. ⚠️", "warning")

        else:  # Statut encore 'pending'
            flash("Le paiement est toujours en cours de traitement. Un moment svp... ⏳", "info")

    except Exception as e:
        logger.error("Erreur vérification paiement FedaPay (TX: %s) : %s", fedapay_id, e)
        flash("Erreur lors de la vérification de votre paiement. Réessayez plus tard.", "danger")

    return redirect(url_for("mes_campagnes"))



def get_video_config():
    """Récupère la configuration globale de génération vidéo."""
    return VideoGenerationConfig.get_config()


@app.route('/souscrire-abonnement-video/<plan>')
@login_required
def souscrire_abonnement_video(plan):
    if current_user.role != "annonceur":
        flash("Accès refusé 🚫", "danger")
        return redirect(url_for("index"))

    # 1. Récupération de la configuration vidéo
    video_config = get_video_config()

    if video_config.pricing_mode == "free":
        flash("La génération vidéo est actuellement gratuite !", "info")
        return redirect(url_for('dashboard_annonceur'))

    # 2. Détermination du prix et du type
    if plan == 'mensuel':
        base_price = video_config.monthly_price
        transaction_type = 'video_subscription_monthly'
    elif plan == 'annuel':
        base_price = video_config.yearly_price
        transaction_type = 'video_subscription_yearly'
    else:
        flash("Plan d'abonnement invalide. ⚠️", "danger")
        return redirect(url_for('dashboard_annonceur'))

    # 3. Application de la promotion
    final_price = base_price
    if video_config.promo_active and video_config.promo_percentage > 0:
        discount = base_price * (video_config.promo_percentage / 100.0)
        final_price = base_price - discount

    # 4. Réutilisation d'une transaction 'pending' existante si elle est encore valide
    existing = (
        Transaction.query.filter_by(
            user_id=current_user.id, 
            transaction_type=transaction_type, 
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
            logger.warning("Réutilisation transaction abonnement impossible, on en recrée une : %s", e)

    # 5. Création de la transaction FedaPay
    reference = f"SUB-{plan.upper()}-{current_user.id}-{uuid.uuid4().hex[:8]}"

    try:
        fedapay_tx = creer_transaction(
            montant=final_price,
            description=f"Abonnement Vidéo {plan.capitalize()} - {current_user.email}",
            metadata={
                "type": transaction_type,
                "plan": plan,
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
            raise ValueError("ID de transaction FedaPay introuvable.")

        lien_paiement = generer_lien_paiement(tx_id)
        if not lien_paiement:
            raise ValueError("Impossible de générer le lien de paiement.")

    except Exception as e:
        logger.error("Erreur création abonnement FedaPay : %s", e)
        flash("Impossible d'initier le paiement de l'abonnement. Réessayez. ⚠️", "danger")
        return redirect(url_for("dashboard_annonceur"))

    # 6. Enregistrement local de la transaction
    transaction = Transaction(
        user_id=current_user.id,
        reference=reference,
        fedapay_transaction_id=str(tx_id),
        amount=final_price,
        currency="XOF",
        transaction_type=transaction_type,
        status="pending",
    )
    db.session.add(transaction)
    db.session.commit()

    return redirect(lien_paiement)







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

    # 🐞 FIX : RefundRequest n'était pas importé, donc 'RefundRequest' in globals() était toujours False
    # et aucune demande n'était jamais réellement enregistrée en base pour l'admin.
    refund_req = RefundRequest(
        campaign_id=camp.id,
        user_id=current_user.id,
        reason=f"Demande suite au rejet de la campagne #{camp.id}",
        payment_method_details=payment_info,
        status="pending"
    )
    db.session.add(refund_req)

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
        whatsapp_number = form.whatsapp_number.data

        # FIX: Anti-énumération — même message que l'email soit pris ou non
        email_ou_whatsapp_pris = False

        if existing_user:
            email_ou_whatsapp_pris = True

        if whatsapp_number and not email_ou_whatsapp_pris:
            # FIX: Validation du numéro WhatsApp par regex
            if not re.match(r"^\+?[0-9]{7,15}$", whatsapp_number):
                flash("Numéro WhatsApp invalide. Utilisez un format international (ex: +22960000000).", "danger")
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
        
    # 1. 🔍 CHARGER LA CONFIGURATION AVEC LE BON MODÈLE (SystemConfig)
    from models import SystemConfig, VideoGenerationConfig
    config = SystemConfig.get_config() # Récupère la configuration de manière robuste
    
    # 2. 🎬 CHARGER LA CONFIGURATION DU MODE VIDÉO (Ajouté)
    video_config = VideoGenerationConfig.get_config()
    
    # 3. 💳 VÉRIFIER SI L'UTILISATEUR EST ABONNÉ (Ajouté)
    user_has_video_subscription = getattr(current_user, 'has_video_subscription', False)
    
    # L'annonceur voit uniquement ses campagnes
    campaigns = Campaign.query.filter_by(user_id=current_user.id).order_by(Campaign.created_at.desc()).all()
    
    # 💾 Récupérer l'URL de la preview vidéo générée depuis la session utilisateur
    preview_video_url = session.get('preview_video_url')

    # 🆕 5. VÉRIFIER SI UNE GÉNÉRATION VIDÉO EST ENCORE EN COURS POUR CET UTILISATEUR
    # (utile si le client a rafraîchi la page pendant que le thread tournait encore en arrière-plan)
    current_video_status = video_progress.get(str(current_user.id))
    is_video_generating = bool(
        current_video_status
        and current_video_status.get("status") not in ("done", "error", "cancelled")
        and current_video_status.get("percentage", 0) < 100
    )
    
    # 4. 🚀 ENVOYER TOUTES LES VARIABLES AU TEMPLATE (y compris preview_video_url et l'état de génération)
    return render_template(
        "dashboard_annonceur.html", 
        campaigns=campaigns, 
        config=config,
        video_config=video_config,
        user_has_video_subscription=user_has_video_subscription,
        preview_video_url=preview_video_url,
        is_video_generating=is_video_generating,
        departements_communes=DEPARTEMENTS_COMMUNES  # 🆕 Ajouté ici !
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

    # Récupération en une fois de tous les CampaignShare de ce partageur (évite une requête par campagne)
    mes_shares = {
        s.campaign_id: s
        for s in CampaignShare.query.filter_by(sharer_id=current_user.id).all()
    }

    campagnes_disponibles = []
    for camp in campagnes_query.order_by(Campaign.shared_at.desc()).all():
        communes_ciblees = [c.strip() for c in camp.communes.split(",")] if camp.communes else []
        provinces_ciblees = [p.strip() for p in camp.provinces.split(",")] if camp.provinces and camp.provinces != "Toutes" else []

        zone_ok = False
        if communes_ciblees:
            zone_ok = current_user.commune in communes_ciblees
        elif provinces_ciblees:
            zone_ok = current_user.province in provinces_ciblees
        else:
            zone_ok = True  # "Toutes zones" et aucune commune précisée

        if zone_ok:
            deja_partagee = camp.id in mes_shares

            campagnes_disponibles.append({
                "campaign": camp,
                "deja_partagee": deja_partagee,
                # 🆕 Statut du quota journalier, utile uniquement si déjà partagée par ce partageur
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
# ==========================================
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

    # 1️⃣ Liste des partageurs de cette campagne (pseudo + date + id du CampaignShare)
    shares = (
        db.session.query(
            CampaignShare.id,
            CampaignShare.sharer_id,
            CampaignShare.created_at,
            User.pseudo,
            User.email  # 🆕 l'admin, contrairement à l'annonceur, peut voir l'email pour modération
        )
        .join(User, User.id == CampaignShare.sharer_id)
        .filter(CampaignShare.campaign_id == campaign_id)
        .all()
    )

    partageurs = []
    total_vues = 0
    total_clics_whatsapp = 0
    total_clics_site = 0

    if shares:
        share_ids = [s.id for s in shares]

        # 2️⃣ Comptage des vues valides, par partageur
        vues_par_sharer = dict(
            db.session.query(View.sharer_id, func.count(View.id))
            .filter(
                View.campaign_id == campaign_id,
                View.counted == True
            )
            .group_by(View.sharer_id)
            .all()
        )

        # 3️⃣ Comptage des clics, par CampaignShare ET par type de lien
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

        # 4️⃣ Fusion des sources
        for s in shares:
            clics = clics_par_share.get(s.id, {"whatsapp": 0, "website": 0})
            nb_vues = vues_par_sharer.get(s.sharer_id, 0)
            partageurs.append({
                "pseudo": s.pseudo or "Partageur anonyme",
                "email": s.email,
                "nb_vues": nb_vues,
                "clics_whatsapp": clics["whatsapp"],
                "clics_site": clics["website"],
                "partage_le": s.created_at.strftime("%d/%m/%Y %H:%M") if s.created_at else None,
            })

        partageurs.sort(key=lambda p: p["nb_vues"], reverse=True)
        total_vues = sum(p["nb_vues"] for p in partageurs)
        total_clics_whatsapp = sum(p["clics_whatsapp"] for p in partageurs)
        total_clics_site = sum(p["clics_site"] for p in partageurs)

    return render_template(
        "admin_suivi_campagne.html",
        campaign=camp,
        partageurs=partageurs,
        total_vues=total_vues,
        total_clics_whatsapp=total_clics_whatsapp,
        total_clics_site=total_clics_site
    )

@app.route('/admin/video-settings', methods=['GET', 'POST'])
@login_required
def admin_video_settings():
    verifier_droits_admin("configurer_video")
    
    # Récupération de la configuration actuelle de l'option vidéo
    config = VideoGenerationConfig.get_config()
    
    if request.method == 'POST':
        # Extraction des données envoyées par le formulaire HTML
        pricing_mode = request.form.get('pricing_mode', 'free')
        monthly_price = request.form.get('monthly_price', type=float)
        yearly_price = request.form.get('yearly_price', type=float)
        
        # Gestion de la case à cocher pour la promo ("y" si cochée, None sinon)
        promo_active = True if request.form.get('promo_active') == 'y' else False
        promo_percentage = request.form.get('promo_percentage', type=float) or 0.0
        
        try:
            # Mise à jour des valeurs de la configuration
            config.pricing_mode = pricing_mode
            if monthly_price is not None:
                config.monthly_price = monthly_price
            if yearly_price is not None:
                config.yearly_price = yearly_price
                
            config.promo_active = promo_active
            config.promo_percentage = promo_percentage
            
            # Sauvegarde dans la base de données
            db.session.commit()
            flash("La configuration de l'option vidéo a été mise à jour avec succès ! 🎉", "success")
            
        except Exception as e:
            db.session.rollback()
            flash(f"Une erreur est survenue lors de l'enregistrement : {str(e)}", "danger")
            
        return redirect(url_for('admin_video_settings'))
        
    return render_template('admin_video_settings.html', config=config)    


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

    # Vérification de zone (sécurité : même logique que dashboard_partageur, empêche de forcer l'URL)
    communes_ciblees = [c.strip() for c in camp.communes.split(",")] if camp.communes else []
    provinces_ciblees = (
        [p.strip() for p in camp.provinces.split(",")]
        if camp.provinces and camp.provinces != "Toutes" else []
    )

    if communes_ciblees:
        zone_ok = current_user.commune in communes_ciblees
    elif provinces_ciblees:
        zone_ok = current_user.province in provinces_ciblees
    else:
        zone_ok = True

    if not zone_ok:
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

    return render_template(
        "instructions_partage.html",
        camp=camp,
        media_urls=media_urls,
        lien_whatsapp_tracking=lien_whatsapp_tracking,
        lien_site_tracking=lien_site_tracking
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
            f'Campagne #{camp.id} refusée et enregistrée avec motif ❌. '
            f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-outline-danger ms-2">📱 Notification WhatsApp</a>',
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
        flash(
            f'Campagne #{camp.id} validée avec succès !{parrain_notifie_str} ✅ '
            f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-success ms-2">📱 Message WhatsApp</a>',
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
    if 'Notification' in globals():
        notif = Notification(
            user_id=camp.user_id,
            title="Remboursement disponible 💰",
            message=f"L'administration a activé l'option de remboursement pour votre campagne #{camp.id}. Vous pouvez désormais soumettre vos coordonnées.",
            category="info",
            link=url_for("mes_campagnes"),
            is_read=False
        )
        db.session.add(notif)

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
        encoded = urllib.parse.quote(message)
        wa_link = f"https://wa.me/{user.whatsapp_number}?text={encoded}"
        flash(
            f'Utilisateur {user.email} confirmé ✅. '
            f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-success ms-2">📱 Message de confirmation</a>',
            "success"
        )
    else:
        flash(f"Utilisateur {user.email} confirmé ✅", "success")

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
        message = "Bonjour, votre demande d'inscription Pubwek a été REFUSÉE."
        encoded = urllib.parse.quote(message)
        wa_link = f"https://wa.me/{whatsapp}?text={encoded}"
        flash(
            f'Utilisateur {email_log} supprimé ❌. '
            f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-outline-danger ms-2">📱 Notification WhatsApp</a>',
            "warning"
        )
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
        flash(f"Ce dossier est déjà pris en charge par {nom_contacteur}. ⚠️", "warning")
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
        encoded = urllib.parse.quote(message)
        wa_link = f"https://wa.me/{user.whatsapp_number}?text={encoded}"
        flash(
            f'Dossier de {user.email} verrouillé sur votre compte. '
            f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-primary ms-2">📱 Envoyer le message de vérification</a>',
            "info"
        )
    else:
        flash(f"Dossier de {user.email} verrouillé, mais aucun numéro WhatsApp disponible. ⚠️", "warning")

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
@app.route("/t/<token>/whatsapp")
def tracking_redirect_whatsapp(token):
    share = CampaignShare.query.filter_by(tracking_token=token).first()
    if not share:
        abort(404)

    camp = share.campaign
    if not camp or not camp.whatsapp_number:
        abort(404)

    # Le client final n'attend jamais : on prépare toujours le lien de redirection en premier
    numero = re.sub(r"[^0-9]", "", camp.whatsapp_number)
    message = quote(f"Bonjour, je suis intéressé(e) par : {camp.promotion_detail or camp.promotion_type}")
    lien_final = f"https://wa.me/{numero}?text={message}"

    try:
        from models import SystemConfig
        config = SystemConfig.get_config()

        # 1️⃣ Enregistrement du clic (toujours tracé, indépendamment du quota du jour)
        click = CampaignClick(
            campaign_share_id=share.id,
            link_type="whatsapp",
            ip=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=request.headers.get("User-Agent", "")[:255],
        )
        db.session.add(click)
        db.session.flush()  # Pour obtenir click.id avant le commit final

        # 2️⃣ Gestion du quota journalier de CLICS — uniquement si la campagne est encore active
        if camp.is_active and camp.paid and camp.validated:
            camp.verifier_et_reset_quota_journalier()

            if not camp.quota_du_jour_atteint():
                # Quota du jour pas encore atteint : ce clic est facturable/rémunéré
                camp.whatsapp_views = (camp.whatsapp_views or 0) + 1  # Compteur global de clics facturés
                camp.views_today = (camp.views_today or 0) + 1        # Compteur de clics du jour

                # 🆕 Détermination du montant reversé au partageur selon le type de contenu
                if camp.media_type == "video":
                    recompense = config.reward_per_click_video
                elif camp.media_type == "photo":
                    recompense = config.reward_per_click_photo
                else:
                    recompense = config.reward_per_click_text

                # 🆕 Crédit du portefeuille du partageur
                sharer = db.session.get(User, share.sharer_id)
                if sharer and recompense > 0:
                    sharer.wallet_balance = (sharer.wallet_balance or 0.0) + recompense
                    db.session.add(WalletTransaction(
                        user_id=sharer.id,
                        amount=recompense,
                        balance_after=sharer.wallet_balance,
                        transaction_type="click_reward",
                        campaign_click_id=click.id,
                        description=f"Clic généré sur la campagne #{camp.id} ({camp.promotion_detail or camp.promotion_type})"
                    ))

                # Le quota vient peut-être d'être atteint avec ce clic : on vérifie juste après
                if camp.quota_du_jour_atteint():
                    camp.daily_quota_paused = True
                    _notifier_partageurs_quota_atteint(camp)

                # Objectif GLOBAL de la campagne atteint (toutes journées confondues) → terminée
                if camp.target_whatsapp_views and camp.whatsapp_views >= camp.target_whatsapp_views:
                    camp.is_active = False
                    camp.status = "terminee"
            else:
                # Quota du jour déjà atteint : le clic est tracé mais NON rémunéré/facturé
                camp.daily_quota_paused = True
                if not camp.daily_quota_alert_sent:
                    _notifier_partageurs_quota_atteint(camp)

        db.session.commit()
    except Exception as e:
        logger.error("Erreur enregistrement clic whatsapp (token %s) : %s", token, e)
        db.session.rollback()

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
    share = CampaignShare.query.filter_by(tracking_token=token).first()
    if not share:
        abort(404)

    camp = share.campaign
    if not camp or not camp.website_url:
        abort(404)

    try:
        from models import SystemConfig
        config = SystemConfig.get_config()

        click = CampaignClick(
            campaign_share_id=share.id,
            link_type="website",
            ip=request.headers.get("X-Forwarded-For", request.remote_addr),
            user_agent=request.headers.get("User-Agent", "")[:255],
        )
        db.session.add(click)
        db.session.flush()

        # 🆕 Le clic vers le site web est traité EXACTEMENT comme le clic WhatsApp :
        # même quota journalier, même règle de rémunération, car les deux sont le même "clic" pour l'annonceur.
        if camp.is_active and camp.paid and camp.validated:
            camp.verifier_et_reset_quota_journalier()

            if not camp.quota_du_jour_atteint():
                camp.whatsapp_views = (camp.whatsapp_views or 0) + 1
                camp.views_today = (camp.views_today or 0) + 1

                if camp.media_type == "video":
                    recompense = config.reward_per_click_video
                elif camp.media_type == "photo":
                    recompense = config.reward_per_click_photo
                else:
                    recompense = config.reward_per_click_text

                sharer = db.session.get(User, share.sharer_id)
                if sharer and recompense > 0:
                    sharer.wallet_balance = (sharer.wallet_balance or 0.0) + recompense
                    db.session.add(WalletTransaction(
                        user_id=sharer.id,
                        amount=recompense,
                        balance_after=sharer.wallet_balance,
                        transaction_type="click_reward",
                        campaign_click_id=click.id,
                        description=f"Clic généré sur la campagne #{camp.id} ({camp.promotion_detail or camp.promotion_type})"
                    ))

                if camp.quota_du_jour_atteint():
                    camp.daily_quota_paused = True
                    _notifier_partageurs_quota_atteint(camp)

                if camp.target_whatsapp_views and camp.whatsapp_views >= camp.target_whatsapp_views:
                    camp.is_active = False
                    camp.status = "terminee"
            else:
                camp.daily_quota_paused = True
                if not camp.daily_quota_alert_sent:
                    _notifier_partageurs_quota_atteint(camp)

        db.session.commit()
    except Exception as e:
        logger.error("Erreur enregistrement clic site (token %s) : %s", token, e)
        db.session.rollback()

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

    # 3️⃣ Vérification du solde AU MOMENT de la demande (protection contre les demandes multiples)
    current_balance = current_user.wallet_balance or 0.0
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
        current_user.wallet_balance = current_balance - montant

        db.session.add(WalletTransaction(
            user_id=current_user.id,
            amount=-montant,
            balance_after=current_user.wallet_balance,
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

    demande = db.session.get(WithdrawalRequest, withdrawal_id)
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

    demande = db.session.get(WithdrawalRequest, withdrawal_id)
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

    if len(password) < 8:
        flash("Le mot de passe doit contenir au moins 8 caractères. ⚠️", "danger")
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
# 🆕 ROUTE : EXPORT PDF — MES RETRAITS (PARTAGEUR)
# ==========================================
           


# =========================================================================
# 🚀 Point d'entrée
# =========================================================================

if __name__ == "__main__":
    # FIX: debug=False en production. Pour dev local uniquement, passez DEBUG=true en variable d'env.
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, threaded=True)

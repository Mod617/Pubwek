"""
Service Creatomate pour Pubwek.

Contient :
- La génération de jetons signés temporaires pour exposer un fichier local
  à Creatomate (cloud) sans casser la protection IDOR de /uploads/.
- La construction du JSON ("source") décrivant la vidéo à rendre.
- L'appel à l'API Creatomate pour lancer un rendu.

Ce fichier est autonome : il ne modifie rien dans main.py.
Étape suivante : on branchera ces fonctions dans la route Flask.
"""

import os
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from itsdangerous import URLSafeTimedSerializer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (à lire depuis vos variables d'environnement / config Flask)
# ---------------------------------------------------------------------------
# IMPORTANT : on NE lit PAS CREATOMATE_API_KEY ici au niveau du module, car cet
# import peut arriver avant que .env soit chargé dans main.py (selon l'ordre des
# imports). On la relit à chaque appel via _get_api_key() pour être sûr d'avoir
# la valeur une fois l'environnement bien chargé.
CREATOMATE_RENDERS_URL = "https://api.creatomate.com/v1/renders"


def _get_api_key():
    api_key = os.getenv("CREATOMATE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "CREATOMATE_API_KEY est introuvable dans l'environnement. "
            "Vérifiez votre fichier .env et que load_dotenv() est bien appelé "
            "avant toute requête vers Creatomate."
        )
    return api_key

# Durée de validité d'un lien signé vers un fichier (en secondes) — 1 heure
ASSET_LINK_MAX_AGE = 3600

# Le "sel" utilisé pour signer les jetons — isolé du reste de l'app
ASSET_TOKEN_SALT = "pubwek-render-asset"


def get_asset_serializer(app):
    """Crée le serializer de jetons à partir de la SECRET_KEY Flask existante."""
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=ASSET_TOKEN_SALT)


def generer_url_asset_signee(app, filename, base_url):
    """
    Génère une URL publique temporaire et signée pour un fichier local,
    utilisable par Creatomate pendant ASSET_LINK_MAX_AGE secondes.

    filename : nom du fichier tel que stocké dans UPLOAD_FOLDER (ex: 'abc123.jpg')
    base_url : URL publique de votre app (ex: votre URL ngrok ou domaine de prod),
               SANS slash final. Ex: "https://aching-monthly-justifier.ngrok-free.dev"
    """
    serializer = get_asset_serializer(app)
    token = serializer.dumps(filename)
    return f"{base_url}/render-assets/{token}"


def generer_url_asset_statique(base_url, sous_chemin):
    """
    Pour les fichiers déjà publics par nature (ex: musique dans /static/audio/),
    pas besoin de jeton — juste l'URL directe.

    sous_chemin : ex: "audio/pop.mp3"
    """
    return f"{base_url}/static/{sous_chemin}"


# ---------------------------------------------------------------------------
# Construction du JSON de composition vidéo (RenderScript Creatomate)
# ---------------------------------------------------------------------------

VIDEO_W, VIDEO_H = 1080, 1920
GOLD = "#D4AF37"
NOIR = "#0A0A0A"
BAR_H_RATIO = 160 / 1920  # proportion de la bande haut/bas, cohérente avec votre design actuel
TOTAL_DURATION = 30
OUTRO_DURATION = 2.5


def build_creatomate_source(image_urls, brand_name, slogan, logo_url=None, audio_url=None):
    """
    Construit le JSON "source" (RenderScript) décrivant la vidéo, en gardant
    l'esprit du design actuel (bandes noires + doré, logo, slogan, zoom Ken Burns,
    fondu enchaîné, carte outro) mais simplifié pour Creatomate.

    Retourne un dict prêt à être envoyé dans le champ "source" de la requête API.
    """
    content_duration = TOTAL_DURATION - OUTRO_DURATION
    n = len(image_urls)
    duration_per_image = content_duration / n if n > 0 else content_duration

    elements = []

    # --- Bande noire du haut avec liseré doré ---
    elements.append({
        "type": "shape",
        "shape": "rectangle",
        "x_alignment": "0%", "y_alignment": "0%",
        "width": "100%", "height": f"{BAR_H_RATIO*100:.2f}%",
        "x": "0%", "y": "0%",
        "fill_color": NOIR,
        "track": 10,
    })
    # --- Bande noire du bas ---
    elements.append({
        "type": "shape",
        "shape": "rectangle",
        "x_alignment": "0%", "y_alignment": "100%",
        "width": "100%", "height": f"{BAR_H_RATIO*100:.2f}%",
        "x": "0%", "y": "100%",
        "fill_color": NOIR,
        "track": 10,
    })

    # --- Diaporama : chaque image avec zoom Ken Burns + fondu ---
    for i, img_url in enumerate(image_urls):
        zoom_in = i % 2 == 0
        elements.append({
            "type": "image",
            "source": img_url,
            "track": 1,
            "time": round(i * duration_per_image, 2),
            "duration": round(duration_per_image + 0.5, 2),  # léger chevauchement pour le fondu
            "fit": "cover",
            "width": "100%",
            "height": "100%",
            "animations": [
                {
                    "type": "scale",
                    "start_scale": "100%" if zoom_in else "112%",
                    "end_scale": "112%" if zoom_in else "100%",
                    "easing": "linear",
                },
                {"type": "fade", "duration": 0.5, "easing": "linear"},
            ],
        })

    # --- Nom de marque : slide d'entrée puis fixe, en haut ---
    elements.append({
        "type": "text",
        "text": brand_name.upper(),
        "track": 11,
        "time": 0,
        "duration": content_duration,
        "x_alignment": "0%", "y_alignment": "50%",
        "x": "5%", "y": f"{BAR_H_RATIO*50:.2f}%",
        "font_family": "Arial",
        "font_weight": "700",
        "font_size": "4vmin",
        "fill_color": GOLD,
        "animations": [
            {"type": "slide", "direction": "left", "duration": 0.6, "easing": "ease-out"}
        ],
    })

    # --- Bandeau slogan en bas ---
    elements.append({
        "type": "text",
        "text": slogan if slogan else "Découvrez nos offres exclusives",
        "track": 12,
        "time": 0,
        "duration": content_duration,
        "x_alignment": "50%", "y_alignment": "50%",
        "x": "50%", "y": f"{100 - BAR_H_RATIO*50:.2f}%",
        "font_family": "Arial",
        "font_size": "2.8vmin",
        "fill_color": "#F0F0F0",
    })

    # --- Logo (si fourni), en haut à droite ---
    if logo_url:
        elements.append({
            "type": "image",
            "source": logo_url,
            "track": 13,
            "time": 0,
            "duration": content_duration,
            "x_alignment": "100%", "y_alignment": "50%",
            "x": "95%", "y": f"{BAR_H_RATIO*50:.2f}%",
            "height": f"{BAR_H_RATIO*60:.2f}%",
            "fit": "contain",
        })

    # --- Carte outro PubWek, après le contenu ---
    elements.append({
        "type": "composition",
        "track": 20,
        "time": round(content_duration, 2),
        "duration": OUTRO_DURATION,
        "width": "100%",
        "height": "100%",
        "elements": [
            {
                "type": "shape", "shape": "rectangle",
                "width": "100%", "height": "100%",
                "fill_color": NOIR,
            },
            {
                "type": "text",
                "text": "PROPULSÉ PAR",
                "x_alignment": "50%", "y_alignment": "42%",
                "font_family": "Arial", "font_size": "2.2vmin",
                "fill_color": "#A0A0A0",
            },
            {
                "type": "text",
                "text": "PubWek",
                "x_alignment": "50%", "y_alignment": "50%",
                "font_family": "Arial", "font_weight": "700",
                "font_size": "6vmin",
                "fill_color": GOLD,
                "animations": [{"type": "scale", "start_scale": "90%", "end_scale": "100%", "duration": 0.35, "easing": "ease-out"}],
            },
            {
                "type": "text",
                "text": brand_name if brand_name else "Créez vos publicités en quelques clics",
                "x_alignment": "50%", "y_alignment": "58%",
                "font_family": "Arial", "font_size": "2.2vmin",
                "fill_color": "#DCDCDC",
            },
        ],
    })

    # --- Musique de fond (si fournie) ---
    if audio_url:
        elements.append({
            "type": "audio",
            "source": audio_url,
            "track": 30,
            "time": 0,
            "duration": TOTAL_DURATION,
            "volume": "70%",
            "audio_fade_out": 1.0,
        })

    return {
        "output_format": "mp4",
        "width": VIDEO_W,
        "height": VIDEO_H,
        "duration": TOTAL_DURATION,
        "elements": elements,
    }


# ---------------------------------------------------------------------------
# Appel à l'API Creatomate (avec retry + User-Agent explicite)
# ---------------------------------------------------------------------------

def _build_creatomate_session():
    """
    Crée une session requests avec :
    - Un User-Agent explicite (évite d'être flaggé comme bot générique par Cloudflare)
    - Une politique de retry automatique avec backoff pour absorber les coupures
      de connexion transitoires (ConnectionResetError, timeouts, 502/503/504)
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=3,                      # 3 tentatives au total
        backoff_factor=1.5,           # attend 1.5s, puis 3s, puis 6s entre les essais
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "PubwekApp/1.0 (+https://pubwek.com; contact: support@pubwek.com)",
    })
    return session


def lancer_render_creatomate(source_json, webhook_url=None):
    """
    Envoie la requête de rendu à Creatomate. Retourne le render_id.
    Le résultat final sera notifié via webhook (recommandé) plutôt qu'attendu ici.

    NOTE : le paramètre "tags" de l'API Creatomate sert à SÉLECTIONNER des
    templates existants par tag (pour lancer plusieurs rendus à la fois), pas à
    associer une métadonnée libre au rendu. On ne l'utilise donc plus ici — on
    associe render_id → (user_id, filename) côté Pubwek via Redis à la place.
    """
    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {"source": source_json}
    if webhook_url:
        payload["webhook_url"] = webhook_url

    session = _build_creatomate_session()

    try:
        response = session.post(
            CREATOMATE_RENDERS_URL,
            headers=headers,
            json=payload,
            timeout=(10, 30),  # (connexion, lecture) en secondes
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        logger.error(
            "Connexion à Creatomate interrompue même après retries. "
            "Vérifiez la clé API, le quota du compte, ou l'état de Creatomate. Détail : %s", e
        )
        raise
    except requests.exceptions.HTTPError as e:
        logger.error(
            "Creatomate a répondu une erreur HTTP %s : %s",
            response.status_code, response.text[:500]
        )
        raise
    finally:
        session.close()

    renders = response.json()

    # Journalisation temporaire pour diagnostic — à retirer une fois stable
    logger.info(
        "Réponse Creatomate (statut %s) : %s", response.status_code, renders
    )

    if isinstance(renders, list):
        if not renders:
            raise RuntimeError(
                f"Creatomate a répondu avec une liste vide (statut {response.status_code}). "
                f"Payload envoyé : {payload}"
            )
        render = renders[0]
    else:
        render = renders

    return render["id"]
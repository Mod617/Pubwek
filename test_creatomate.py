"""
Script de TEST ISOLÉ pour vérifier que la clé API Creatomate fonctionne.
Ne touche à rien dans votre application Flask. À lancer seul, en dehors de main.py.

Usage :
    pip install requests python-dotenv --break-system-packages   (si pas déjà fait)
    python test_creatomate.py
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()  # charge le fichier .env

API_KEY = os.getenv("CREATOMATE_API_KEY")

if not API_KEY:
    print("❌ ERREUR : CREATOMATE_API_KEY introuvable dans votre .env")
    print("   Vérifiez que le fichier .env existe et contient : CREATOMATE_API_KEY=votre_cle")
    exit(1)

print("✅ Clé API trouvée, envoi d'une requête de test à Creatomate...")

url = "https://api.creatomate.com/v1/renders"
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# Un rendu minimal : juste une vidéo de 2 secondes avec du texte, en JSON "source" direct
# (pas besoin de template pour ce test)
payload = {
    "output_format": "mp4",
    "source": {
        "output_format": "mp4",
        "width": 1080,
        "height": 1920,
        "duration": 2,
        "elements": [
            {
                "type": "text",
                "text": "Test Pubwek OK ✅",
                "width": "80%",
                "height": "20%",
                "x_alignment": "50%",
                "y_alignment": "50%",
                "font_size": "8vmin",
                "fill_color": "#D4AF37",
            }
        ],
    },
}

response = requests.post(url, headers=headers, json=payload, timeout=30)

if response.status_code not in (200, 202):
    print(f"❌ ERREUR HTTP {response.status_code}")
    print(response.text)
    exit(1)

renders = response.json()
render = renders[0] if isinstance(renders, list) else renders
render_id = render["id"]
print(f"✅ Rendu lancé avec succès. ID : {render_id}")
print("⏳ Attente de la fin du rendu...")

# On vérifie le statut toutes les 2 secondes (juste pour ce test — en prod on utilisera un webhook)
status_url = f"https://api.creatomate.com/v1/renders/{render_id}"
for _ in range(30):
    time.sleep(2)
    check = requests.get(status_url, headers=headers, timeout=30)
    data = check.json()
    status = data.get("status")
    print(f"   statut actuel : {status}")
    if status == "succeeded":
        print(f"🎉 SUCCÈS ! Vidéo disponible ici : {data.get('url')}")
        break
    elif status == "failed":
        print(f"❌ Le rendu a échoué : {data.get('error_message')}")
        break
else:
    print("⏳ Toujours en cours après 60 secondes, vérifiez votre dashboard Creatomate.")
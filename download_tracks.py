import os
import requests

# Liens d'exemples de musiques libres de droits (fichiers MP3 directs)
TRACKS = {
    "pop.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    "dynamique.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
    "corporate.mp3": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3"
}

dest_folder = os.path.join("static", "audio")
os.makedirs(dest_folder, exist_ok=True)

print("🚀 Début du téléchargement des pistes audio...")

for name, url in TRACKS.items():
    path = os.path.join(dest_folder, name)
    print(f"Téléchargement de {name} depuis le terminal...")
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            with open(path, "wb") as f:
                f.write(response.content)
            print(f"✅ {name} enregistré avec succès dans {dest_folder} !")
        else:
            print(f"❌ Échec pour {name} (Code: {response.status_code})")
    except Exception as e:
        print(f"⚠️ Erreur lors du téléchargement de {name}: {e}")

print("🎉 Terminé ! Tu as tes musiques pour tester ton application.")
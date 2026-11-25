# Vidéo publicitaire « partageurs » — sources de production

Ce dossier ne fait pas partie de l'application : il sert seulement à refaire la vidéo.

- `compo/compo.html` : composition animée en 1080×1920, 9 scènes. Elle utilise les vraies captures de Pubwek prises avec `tools_apercu_local.py`.
- `timeline.json` : durées des scènes et sous-titres. Les scènes seront recalées sur la voix off.
- `render.js` : rendu image par image avec Playwright et ffmpeg.
- `audio.py` : musique originale (afro-pop, 112 BPM) et bruitages, entièrement synthétisés et libres de droits.
- `master.sh` : normalisation à −14 LUFS et exports TikTok/Facebook (1080p) et WhatsApp (720p).
- `cap3.js` : recapture des écrans réels et des positions des boutons.
- `SCRIPT_VOIX_OFF_PUBWEK.md` : texte de la voix off.

Rendu :

    node render.js compo/compo.html timeline.json video_raw.mp4
    python3 audio.py timeline.json music.wav sfx.wav

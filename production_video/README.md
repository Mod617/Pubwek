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

Les clips des personnages (clip1.webm à clip5.webm, préparés depuis les vidéos envoyées) ne sont pas versionnés à cause de leur poids. Ils doivent être placés dans compo/ avant le rendu.

## Version finale (voix en 2 fichiers)

1. Assembler les voix : l'accroche de 0 à 8,15 s, puis la suite à 8,25 s.
2. `python3 audio.py timeline_final.json music.wav sfx.wav`
3. `node render.js compo/compo.html timeline_final.json video_raw.mp4`
4. `bash mix_vo.sh vo_final.wav music.wav sfx.wav mix.wav 48.3`
5. Accroche +3,2 dB sur les 8,1 premières secondes : `ffmpeg -i mix.wav -af "volume=volume='if(lt(t,8.1),1.45,1)':eval=frame" mix_hook.wav`
6. `bash master.sh video_raw.mp4 mix_hook.wav pubwek_final`

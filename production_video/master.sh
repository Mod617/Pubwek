# master.sh <video_raw> <audio_mix_wav> <out_prefix>
set -e
V=$1; A=$2; O=$3
# normalisation en deux passes à -14 LUFS
J=$(ffmpeg -hide_banner -i "$A" -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | sed -n '/^{/,/^}/p')
g(){ echo "$J" | python3 -c "import json,sys;print(json.load(sys.stdin)['$1'])"; }
ffmpeg -v error -y -i "$A" -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=$(g input_i):measured_TP=$(g input_tp):measured_LRA=$(g input_lra):measured_thresh=$(g input_thresh):offset=$(g target_offset):linear=true,aresample=48000" "$O.norm.wav"
# TikTok / Facebook : 1080x1920, H.264 High, ~10 Mbit/s, AAC 192k
ffmpeg -v error -y -i "$V" -i "$O.norm.wav" -map 0:v -map 1:a -c:v libx264 -preset slow -profile:v high -level 4.2 -b:v 10M -maxrate 12M -bufsize 20M -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000 -shortest -movflags +faststart "${O}_tiktok_facebook.mp4"
# WhatsApp : 720x1280, fichier léger (WhatsApp recompresse de toute façon)
ffmpeg -v error -y -i "$V" -i "$O.norm.wav" -map 0:v -map 1:a -vf scale=720:1280:flags=lanczos -c:v libx264 -preset slow -profile:v high -b:v 2.8M -maxrate 3.5M -bufsize 6M -pix_fmt yuv420p -r 30 -c:a aac -b:a 128k -ar 48000 -shortest -movflags +faststart "${O}_whatsapp.mp4"
rm "$O.norm.wav"
for f in "${O}_tiktok_facebook.mp4" "${O}_whatsapp.mp4"; do printf "%s  " "$(basename $f)"; ffprobe -v error -show_entries format=duration,size -of csv=p=0 "$f"; ffmpeg -i "$f" -af ebur128=framelog=quiet -f null - 2>&1 | grep -A1 Integrated | tail -1; done

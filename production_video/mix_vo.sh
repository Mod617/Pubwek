# mix_vo.sh <voix.wav> <music.wav> <sfx.wav> <sortie.wav> <durée>
# Voix traitée (EQ + compression) ; musique atténuée automatiquement sous la voix (≈ -14 dB).
ffmpeg -v error -y -i "$1" -i "$2" -i "$3" -filter_complex "
[0]aresample=48000,pan=stereo|c0=c0|c1=c0,highpass=f=80,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.2:g=2.5,equalizer=f=7000:t=q:w=2:g=-1.5,acompressor=threshold=0.08:ratio=3:attack=8:release=120:makeup=2,alimiter=limit=0.9,apad=pad_dur=3[v];
[v]asplit=2[vmix][vsc];[1]volume=1.0[m];
[m][vsc]sidechaincompress=threshold=0.035:ratio=5:attack=25:release=450:makeup=1[md];
[2]volume=0.38[s];[vmix][md][s]amix=inputs=3:normalize=0:duration=longest[a]" -map "[a]" -t "$5" "$4"

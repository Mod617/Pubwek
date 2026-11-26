# Musique originale (afro-pop / amapiano, 112 BPM) + bruitages, synthétisés.
# Aucun échantillon externe : 100 % libre de droits.
# Usage : python3 audio.py timeline.json out_music.wav out_sfx.wav
import json, sys, numpy as np

SR = 48000
tl = json.load(open(sys.argv[1]))
T = tl["T"]; DUR = T["s9"][1] + 0.6
N = int(DUR * SR)
rng = np.random.default_rng(7)

def env(n, a=0.002, d=0.2, curve=4.0):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4)) * np.exp(-t * curve / max(d, 1e-4))
    return e

def add(buf, x, at, gain=1.0, pan=0.0):
    i = int(at * SR)
    if i >= len(buf): return
    x = x[: len(buf) - i]
    l = np.cos((pan + 1) * np.pi / 4); r = np.sin((pan + 1) * np.pi / 4)
    buf[i:i + len(x), 0] += x * gain * l * 1.414
    buf[i:i + len(x), 1] += x * gain * r * 1.414

def lp(x, fc):
    a = np.exp(-2 * np.pi * fc / SR); y = np.empty_like(x); s = 0.0
    for i in range(len(x)):  # petites longueurs seulement
        s = (1 - a) * x[i] + a * s; y[i] = s
    return y

def lp_fast(x, fc):
    # filtre passe-bas d'ordre 1 vectorisé via scipy-like lfilter maison (par blocs FFT)
    n = len(x); f = np.fft.rfftfreq(n * 2, 1 / SR)
    H = 1 / (1 + 1j * f / fc)
    return np.fft.irfft(np.fft.rfft(x, n * 2) * H)[:n]

def hp_fast(x, fc):
    return x - lp_fast(x, fc)

def note(m): return 440 * 2 ** ((m - 69) / 12)

# ------------------------------------------------------------------ musique
music = np.zeros((N, 2))
BPM = 112; BEAT = 60 / BPM; BAR = BEAT * 4; S16 = BEAT / 4
DROP = T["s2"][0]                      # le groove complet démarre à la scène 2
grid0 = DROP - BAR * np.ceil(DROP / BAR)  # aligner une mesure sur le drop

def kick():
    n = int(.35 * SR); t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 28); ph = 2 * np.pi * np.cumsum(f) / SR
    return np.tanh(1.6 * np.sin(ph) * env(n, .001, .3, 9))
def clap():
    n = int(.25 * SR); x = rng.standard_normal(n)
    x = hp_fast(x, 900) * env(n, .001, .18, 18)
    for d in (0.011, 0.022):
        k = int(d * SR); x[k:] += 0.6 * x[:-k]
    body = np.sin(2 * np.pi * 190 * np.arange(n) / SR) * env(n, .001, .06, 30)
    return 0.55 * x + 0.35 * body
def shaker(acc):
    n = int(.07 * SR); x = hp_fast(rng.standard_normal(n), 6000) * env(n, .004, .05, 40)
    return x * (0.5 if acc else 0.25)
def logdrum(m):
    n = int(.42 * SR); t = np.arange(n) / SR
    f = note(m) * (1 + 0.5 * np.exp(-t * 40)); ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) + 0.25 * np.sin(2 * ph)
    return np.tanh(2.2 * x * env(n, .002, .35, 6)) * 0.8
def pluck(m, d=.5):
    n = int(d * SR); t = np.arange(n) / SR; f = note(m)
    x = np.sin(2 * np.pi * f * t) + .35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 18) + .15 * np.sin(2 * np.pi * 3.98 * f * t) * np.exp(-t * 30)
    return x * env(n, .002, d, 7)
def pad(ms, d):
    n = int(d * SR); t = np.arange(n) / SR; x = np.zeros(n)
    for m in ms:
        for det in (-0.12, 0, 0.11):
            f = note(m + det); x += 2 * ((t * f) % 1) - 1
    x = lp_fast(x / (len(ms) * 3), 1400)
    a = np.minimum(1, t / .25) * np.minimum(1, (d - t) / .3)
    return x * a

# grille d'accords : Am - F - C - G (vi IV I V), une mesure chacun
CH = [(57, [57, 60, 64, 67]), (53, [53, 57, 60, 64]), (48, [55, 60, 64, 67]), (55, [55, 59, 62, 67])]
PENTA = [69, 72, 74, 76, 79, 81]
LOGPAT = [0, 3, 6, 10, 12, 14]
PLUCKPAT = [(0, 0), (3, 2), (6, 4), (8, 3), (10, 1), (14, 2)]

def section_level(t):
    """Intensité selon la scène : (kick, perc, log, pad, pluck)"""
    if t < DROP: return (0, .6, 0, .8, .9)
    if t < T["s8"][0]: return (1, 1, 1, .8, .8)
    if t < T["s9"][0]: return (0, .7, .6, 1, .9)      # passage plus doux : honnêteté
    return (1, 1, 1, .9, 1)

nbars = int(np.ceil((DUR - grid0) / BAR)) + 1
for b in range(nbars):
    t0 = grid0 + b * BAR
    root, chord = CH[b % 4]
    lv = section_level(max(0, t0 + .01))
    if t0 + BAR > 0:
        add(music, pad(chord, BAR + .2), max(0, t0), .22 * lv[3], 0)
    for s in range(16):
        ts = t0 + s * S16
        if ts < 0 or ts > DUR - .3: continue
        lv = section_level(ts)
        if s in (0, 8) and lv[0]: add(music, kick(), ts, .95 * lv[0])
        if s in (4, 12) and lv[1] and ts >= DROP: add(music, clap(), ts, .45 * lv[1], .05)
        if lv[1]: add(music, shaker(s % 2 == 1), ts + .004 * (s % 2), .55 * lv[1], .35 if s % 2 else -.3)
        if s in LOGPAT and lv[2]:
            m = root - 12 + (7 if s == 10 else 0) + (12 if s == 14 else 0)
            add(music, logdrum(m), ts, .55 * lv[2])
        for (ps, deg) in PLUCKPAT:
            if s == ps and lv[4]:
                add(music, pluck(PENTA[(deg + b) % len(PENTA)]), ts, .16 * lv[4], .4 if deg % 2 else -.4)

# montée (riser) avant le drop et avant le CTA
def riser(d):
    n = int(d * SR); t = np.arange(n) / SR
    x = hp_fast(rng.standard_normal(n), 2500) * (t / d) ** 2
    f = 300 + 1500 * (t / d) ** 2; x += .3 * np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / d) ** 2
    return x
add(music, riser(1.6), DROP - 1.6, .18)
add(music, riser(1.2), T["s9"][0] - 1.2, .16)
# impact final
def impact():
    n = int(2.5 * SR); t = np.arange(n) / SR
    return (np.sin(2 * np.pi * (45 + 60 * np.exp(-t * 20)) * t) * np.exp(-t * 2.5) + .3 * lp_fast(rng.standard_normal(n), 800) * np.exp(-t * 5))
add(music, impact(), DROP, .6)
# fin : on coupe le groove sur la dernière seconde, accord tenu
end = T["s9"][1]
fade = np.ones(N); i0 = int((end - 1.2) * SR); fade[i0:] = np.linspace(1, 0, N - i0) ** 1.5
music *= fade[:, None]
add(music, pad(CH[0][1] + [69], 2.0), end - 1.4, .25)

# ------------------------------------------------------------------ bruitages
sfx = np.zeros((N, 2))
def tone(f, d, dec=10, a=.002):
    n = int(d * SR); t = np.arange(n) / SR
    return np.sin(2 * np.pi * f * t) * env(n, a, d, dec)
def tick(): return tone(2600, .03, 60) * .6
def pop():
    n = int(.12 * SR); t = np.arange(n) / SR
    return np.sin(2 * np.pi * (400 + 900 * np.exp(-t * 40)) * t) * env(n, .001, .1, 25)
def click(): return hp_fast(rng.standard_normal(int(.02 * SR)), 2000) * env(int(.02 * SR), .0005, .015, 60) * .8 + tone(1800, .02, 80) * .4
def whoosh(d=.38):
    n = int(d * SR); t = np.arange(n) / SR; x = rng.standard_normal(n)
    fc = 400 + 4000 * np.sin(np.pi * t / d); y = np.zeros(n); s = 0
    a = np.exp(-2 * np.pi * fc / SR)
    for i in range(n): s = (1 - a[i]) * x[i] + a[i] * s; y[i] = s
    return y * np.sin(np.pi * t / d) ** 2 * 2.2
def ding():
    return (tone(1318.5, .9, 4) + .6 * tone(1975.5, .9, 5)) * .5
def ding2():
    x = np.zeros(int(1.1 * SR)); a = ding(); b = (tone(1760, .9, 4) + .6 * tone(2637, .9, 5)) * .5
    x[:len(a)] += a; k = int(.12 * SR); x[k:k + len(b)] += b[:len(x) - k]; return x * .7
def coin(f=2400):
    return (tone(f, .25, 14) + .7 * tone(f * 1.5, .25, 18) + .4 * tone(f * 2.4, .25, 25)) * .35
def buzz(d=.9):
    n = int(d * SR); t = np.arange(n) / SR
    x = np.sign(np.sin(2 * np.pi * 170 * t)) * .5 + np.sin(2 * np.pi * 85 * t)
    gate = ((t % .45) < .3).astype(float)
    return lp_fast(x, 600) * gate * .5
def bonk():
    n = int(.5 * SR); t = np.arange(n) / SR
    x = np.sign(np.sin(2 * np.pi * (140 - 60 * t) * t)) * env(n, .002, .4, 6)
    th = np.sin(2 * np.pi * (60 + 80 * np.exp(-t * 30)) * t) * env(n, .001, .3, 8)
    return lp_fast(x, 900) * .45 + th * .9
def shutter():
    n = int(.12 * SR); x = hp_fast(rng.standard_normal(n), 1500)
    e = np.zeros(n); e[:int(.02 * SR)] = 1; e[int(.06 * SR):int(.085 * SR)] = .8
    return x * e * .7
def success():
    x = np.zeros(int(.7 * SR))
    for i, f in enumerate((784, 988, 1175)):
        a = tone(f, .45, 7) * .45; k = int(i * .07 * SR); x[k:k + len(a)] += a[:len(x) - k]
    return x
def type_k(): return click() * .6

def at(sc, lt): return T[sc][0] + lt

# transitions
for sc in [k for k in ["s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9"] if k in T]:
    add(sfx, whoosh(), T[sc][0] - .08, .35)
# S1 : compteur + tampon 0 F
for k in range(14):
    add(sfx, tick(), at("s1", .5 + 2.2 * (1 - (1 - k / 14) ** 0.5) * .99), .5)
add(sfx, bonk(), at("s1", 3.1), .9)
# S2 : vibration, notification, pièces
add(sfx, buzz(.9), at("s2", .55), .55)
add(sfx, ding2(), at("s2", 1.0), .8)
for i in range(9): add(sfx, coin(2200 + (i * 173) % 900), at("s2", 1.25 + i * .07), .5, (-1) ** i * .4)
# S3
add(sfx, pop(), at("s3", .15), .6); add(sfx, pop(), at("s3", 1.9), .7); add(sfx, pop(), at("s3", 2.4), .5)
# S4
add(sfx, pop(), at("s4", .15), .6)
for i in range(3): add(sfx, pop(), at("s4", .55 + i * .22), .55)
add(sfx, click(), at("s4", 4.3), .9); add(sfx, success(), at("s4", 4.55), .6)
# S5
add(sfx, pop(), at("s5", .15), .6)
add(sfx, click(), at("s5", 2.05), .9); add(sfx, pop(), at("s5", 2.2), .5)
for i, tt in enumerate([3.15, 3.45, 3.75, 4.35, 4.65]): add(sfx, coin(2600 + i * 120), at("s5", tt), .55, (-1) ** i * .5)
add(sfx, bonk(), at("s5", 4.15), .35)
# S6 (seulement si la scène existe)
if "s6" in T:
    add(sfx, pop(), at("s6", .15), .6)
    add(sfx, shutter(), at("s6", .75), .9)
    add(sfx, whoosh(.3), at("s6", 2.0), .25)
    add(sfx, click(), at("s6", 2.85), .9); add(sfx, success(), at("s6", 3.0), .6)
    add(sfx, pop(), at("s6", 3.25), .4)
# S7
add(sfx, pop(), at("s7", .15), .6)
for i in range(4): add(sfx, pop(), at("s7", .45 + i * .16), .5, (-1) ** i * .3)
add(sfx, pop(), at("s7", 1.15), .7)
add(sfx, click(), at("s7", 2.95), .9)
add(sfx, buzz(.6), at("s7", 3.2), .45); add(sfx, ding2(), at("s7", 3.25), .6)
add(sfx, success(), at("s7", 4.0), .45)
# S8
add(sfx, whoosh(.25), at("s8", .85), .3)
add(sfx, pop(), at("s8", 1.35), .6)
for tt in (2.2, 2.9, 3.9): add(sfx, pop(), at("s8", tt), .5)
for tt in (2.75, 3.75): add(sfx, whoosh(.22), at("s8", tt - .1), .3)
# S9
add(sfx, pop(), at("s9", .1), .7)
for i in range(10): add(sfx, type_k(), at("s9", 1.0 + i * .1), .7)
add(sfx, success(), at("s9", 2.1), .6)
for i in range(3): add(sfx, pop(), at("s9", 2.7 + i * .3), .45)
add(sfx, click(), at("s9", 4.6), .8)

def save(x, path):
    x = x / max(1e-9, np.abs(x).max()) * 0.89
    import wave
    w = wave.open(path, "wb"); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((x * 32767).astype("<i2").tobytes()); w.close()
save(music, sys.argv[2]); save(sfx, sys.argv[3])
print("ok", DUR)

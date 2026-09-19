"""Rapatrie les polices Google utilisees par le design Pubwek en local."""
import os
import re
import urllib.request

BASE = r"C:\Users\pierr\Desktop\Claude\Pubwek\pubwek-github"
FONT_DIR = os.path.join(BASE, "static", "fonts")
CSS_OUT = os.path.join(BASE, "static", "css", "fonts.css")

os.makedirs(FONT_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ICONS = (
    "account_balance_wallet,account_circle,add,add_circle,"
    "admin_panel_settings,analytics,arrow_back,arrow_forward,attach_file,"
    "badge,bar_chart,block,bolt,business,calendar_month,campaign,cancel,chat,"
    "check_circle,chevron_right,close,content_copy,contract,dashboard,delete,"
    "delete_forever,description,done_all,download,edit,error,"
    "expand_circle_up,expand_more,fact_check,filter_alt,gavel,group,help,"
    "history,home,hourglass_top,image,info,inventory_2,key,layers,link,"
    "location_on,lock,lock_clock,logout,mail,mark_email_unread,menu,"
    "monetization_on,more_vert,notifications,open_in_new,paid,payments,"
    "pending,person,phone_iphone,photo_camera,play_circle,point_of_sale,"
    "price_check,priority_high,public,qr_code,receipt_long,refresh,remove,"
    "report,restore,rocket_launch,save,savings,schedule,search,send,settings,"
    "share,shield,smart_display,sort,speed,storefront,support_agent,task_alt,"
    "trending_up,upload,verified,verified_user,video_library,visibility,"
    "visibility_off,warning"
)

SOURCES = [
    ("texte",
     "https://fonts.googleapis.com/css2"
     "?family=Inter:wght@400;500;600;700"
     "&family=JetBrains+Mono:wght@600;700"
     "&family=Plus+Jakarta+Sans:wght@600;700;800"
     "&display=swap"),
    ("icones",
     "https://fonts.googleapis.com/css2"
     "?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200"
     "&icon_names=" + ICONS + "&display=block"),
]


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


morceaux = []
telecharges = {}

for nom, url in SOURCES:
    css = get(url)
    print(f"[{nom}] CSS recu : {len(css)} octets")
    for font_url in sorted(set(re.findall(r"url\((https://[^)]+)\)", css))):
        if font_url in telecharges:
            continue
        if "/l/font?kit=" in font_url:
            # Endpoint de sous-ensemble dynamique (Material Symbols) : l'URL ne
            # se termine pas par .woff2, on lui donne un nom stable.
            nom_fichier = "material-symbols-subset.woff2"
        else:
            # nom de fichier lisible a partir du chemin distant
            fichier = font_url.rsplit("/", 2)
            nom_fichier = (fichier[-2] + "-" + fichier[-1]).replace("%", "_")
            nom_fichier = re.sub(r"[^A-Za-z0-9._-]", "_", nom_fichier)
        chemin = os.path.join(FONT_DIR, nom_fichier)
        with open(chemin, "wb") as f:
            f.write(get(font_url, binary=True))
        telecharges[font_url] = nom_fichier
        print(f"   -> {nom_fichier} ({os.path.getsize(chemin)} octets)")
    for font_url, nom_fichier in telecharges.items():
        css = css.replace(font_url, "../fonts/" + nom_fichier)
    morceaux.append(f"/* ===== {nom} ===== */\n{css}")

with open(CSS_OUT, "w", encoding="utf-8") as f:
    f.write("/* Polices auto-hebergees - genere par scratchpad/fetch_fonts.py.\n"
            "   Aucune dependance a fonts.googleapis.com a l'execution. */\n\n")
    f.write("\n\n".join(morceaux))

total = sum(os.path.getsize(os.path.join(FONT_DIR, n)) for n in telecharges.values())
print(f"\n{len(telecharges)} fichiers woff2, {total/1024:.0f} Ko au total")
print(f"CSS ecrit : {CSS_OUT} ({os.path.getsize(CSS_OUT)} octets)")

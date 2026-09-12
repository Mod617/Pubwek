# -*- coding: utf-8 -*-
"""Harnais d'attaque authentifié (instance LOCALE, base SQLite jetable).

On se connecte comme un utilisateur et on tente d'accéder / d'agir sur les
ressources d'un AUTRE, ou de franchir la frontière de privilèges. Chaque
attaque DOIT être bloquée. CSRF désactivé dans le client de test : on isole
l'AUTORISATION (la protection CSRF est vérifiée en direct sur la prod, où POST
sans jeton -> 400).

Résultat attendu : 0 faille.
"""
import sys
from datetime import datetime

import main
from main import app, db
from models import (User, Campaign, CampaignShare, CampaignClick,
                    CampaignShareProof, UploadedFile, WithdrawalRequest,
                    SystemConfig, WalletTransaction)

app.config["WTF_CSRF_ENABLED"] = False

R = {"ok": 0, "faille": 0}
def bloque(nom, condition, detail=""):
    """condition = True quand l'attaque est CORRECTEMENT bloquée."""
    if condition:
        R["ok"] += 1; print(f"  OK (bloqué)  {nom}")
    else:
        R["faille"] += 1; print(f"  FAILLE       {nom} {detail}")

def creer_user(email, role, mdp, **kw):
    u = User(email=email, role=role,
             password_hash=main.bcrypt.generate_password_hash(mdp).decode("utf-8"),
             is_confirmed=True, province="Littoral", commune="Cotonou", **kw)
    db.session.add(u); db.session.commit()
    return u

def client_connecte(email, mdp):
    c = app.test_client()
    c.post("/login", data={"email": email, "password": mdp}, follow_redirects=True)
    return c

print("=" * 60)
print("Harnais d'attaque authentifié PubWek (local)")
print("=" * 60)

with app.app_context():
    config = SystemConfig.get_config()
    config.exiger_preuve_partage = True
    db.session.commit()

    admin = User.query.filter_by(role="admin").first()

    A = creer_user("atk-annonceurA@pubwek-atk.com", "annonceur", "MdpAnnonceurA1", whatsapp_number="+2290102000001")
    B = creer_user("atk-annonceurB@pubwek-atk.com", "annonceur", "MdpAnnonceurB1", whatsapp_number="+2290102000002")
    PA = creer_user("atk-partageurA@pubwek-atk.com", "partageur", "MdpPartageurA1", whatsapp_number="+2290102000003")
    PB = creer_user("atk-partageurB@pubwek-atk.com", "partageur", "MdpPartageurB1", whatsapp_number="+2290102000004")
    # Sous-admin avec UNE seule permission : valider_campagnes (pas gerer_retraits)
    SA = creer_user("atk-sousadmin@pubwek-atk.com", "sous_admin", "MdpSousAdmin1",
                    admin_permissions="valider_campagnes", is_active_admin=True)

    # Campagne de A, payée + validée + active + diffusée
    camp = Campaign(user_id=A.id, promotion_type="photo", promotion_detail="Camp de A",
                    provinces="Toutes", media_type="photo", media_files="atk_a.jpg",
                    whatsapp_number="+2290102000001", total_cost=5000.0,
                    target_whatsapp_views=100, whatsapp_views=0, duration_days=3,
                    paid=True, validated=True, is_active=True, shared_to_partageurs=True,
                    payment_status="paid", status="active", shared_at=datetime.utcnow())
    db.session.add(camp); db.session.commit()
    camp_id = camp.id

    # Campagne de A rejetée (pour tester la resoumission croisée)
    camp_rej = Campaign(user_id=A.id, promotion_type="photo", promotion_detail="Camp rejetée de A",
                        provinces="Toutes", media_type="photo", media_files="atk_a2.jpg",
                        total_cost=2000.0, admin_status="rejected", status="rejete",
                        paid=True, payment_status="paid")
    db.session.add(camp_rej); db.session.commit()
    camp_rej_id = camp_rej.id

    # Fichier appartenant à A
    uf = UploadedFile(filename="atk_a.jpg", owner_id=A.id)
    db.session.add(uf); db.session.commit()

    # Partage par PA + clic payable + preuve en attente (cible de l'attaque sur l'argent)
    share = CampaignShare(campaign_id=camp_id, sharer_id=PA.id)
    db.session.add(share); db.session.commit()
    share_id = share.id
    jour = camp.jour_diffusion_campagne()
    click = CampaignClick(campaign_share_id=share_id, link_type="whatsapp", ip="41.85.77.1",
                          user_agent="Mozilla/5.0 Chrome/120 Mobile", is_paid=True,
                          rewarded_at=None, day_number=jour)
    db.session.add(click)
    preuve = CampaignShareProof(campaign_share_id=share_id, day_number=jour,
                                proof_type="fin", filename="atk_preuve.jpg", status="en_attente")
    db.session.add(preuve); db.session.commit()
    preuve_id = preuve.id
    solde_PA_avant = PA.wallet_balance or 0.0

    # Demande de retrait de PA (cible IDOR admin)
    wd = WithdrawalRequest(user_id=PA.id, amount=1000.0, status="pending",
                           payout_channel="mtn", payout_phone="+2290102000003")
    db.session.add(wd); db.session.commit()
    wd_id = wd.id

    # IDs figés en entiers (les instances ORM seront détachées hors contexte)
    A_id, B_id, PA_id, PB_id, SA_id = A.id, B.id, PA.id, PB.id, SA.id

# --- Clients connectés ---
cA  = client_connecte("atk-annonceurA@pubwek-atk.com", "MdpAnnonceurA1")
cB  = client_connecte("atk-annonceurB@pubwek-atk.com", "MdpAnnonceurB1")
cPA = client_connecte("atk-partageurA@pubwek-atk.com", "MdpPartageurA1")
cPB = client_connecte("atk-partageurB@pubwek-atk.com", "MdpPartageurB1")
cSA = client_connecte("atk-sousadmin@pubwek-atk.com", "MdpSousAdmin1")

def etat_camp(cid):
    with app.app_context():
        c = db.session.get(Campaign, cid)
        return (c.paid, c.validated, c.is_active, c.status, c.admin_status)

# =========================================================================
print("\n[A] Escalade de privilèges : non-admin -> routes admin")
for nom, client in [("annonceur", cB), ("partageur", cPB)]:
    r = client.post(f"/admin/validate_campaign/{camp_id}")
    bloque(f"{nom} ne peut pas valider une campagne (403)", r.status_code == 403, f"(status={r.status_code})")
    r = client.post(f"/admin/confirm_user/{PA_id}")
    bloque(f"{nom} ne peut pas confirmer un utilisateur (403)", r.status_code == 403, f"(status={r.status_code})")
    r = client.post(f"/admin/retraits/{wd_id}/payer-manuel")
    bloque(f"{nom} ne peut pas payer un retrait (403)", r.status_code == 403, f"(status={r.status_code})")
    r = client.get("/admin/settings")
    bloque(f"{nom} ne peut pas ouvrir les réglages admin (403)", r.status_code == 403, f"(status={r.status_code})")
    r = client.get("/admin/utilisateurs")
    bloque(f"{nom} ne peut pas lister les utilisateurs (403)", r.status_code == 403, f"(status={r.status_code})")

# =========================================================================
print("\n[B] Attaque sur l'argent : valider sa propre preuve pour se payer")
r = cPA.post(f"/admin/preuve/{preuve_id}/valider")
with app.app_context():
    pv = db.session.get(CampaignShareProof, preuve_id)
    pa = db.session.get(User, PA_id)
    bloque("le partageur ne peut pas valider sa propre preuve (403)", r.status_code == 403, f"(status={r.status_code})")
    bloque("la preuve reste en attente", pv.status == "en_attente", f"(status={pv.status})")
    bloque("le portefeuille n'a pas été crédité", (pa.wallet_balance or 0.0) == solde_PA_avant,
           f"(solde={pa.wallet_balance})")

# =========================================================================
print("\n[C] IDOR annonceur : agir sur la campagne d'un autre annonceur")
avant = etat_camp(camp_id)
r = cB.get(f"/dashboard/annonceur/campagne/{camp_id}/payer", follow_redirects=False)
bloque("annonceur B ne peut pas payer la campagne de A (redirigé, pas 200)",
       r.status_code in (301, 302), f"(status={r.status_code})")
r = cB.post(f"/annonceur/campaign/{camp_rej_id}/resoumettre", follow_redirects=False)
bloque("annonceur B ne peut pas resoumettre la campagne rejetée de A",
       r.status_code in (301, 302), f"(status={r.status_code})")
r = cB.post(f"/annonceur/campaign/{camp_id}/reclamer_remboursement", follow_redirects=False)
bloque("annonceur B ne peut pas réclamer le remboursement de A",
       r.status_code in (301, 302), f"(status={r.status_code})")
apres = etat_camp(camp_id)
bloque("l'état de la campagne de A est inchangé après les tentatives", avant == apres,
       f"(avant={avant}, après={apres})")

# =========================================================================
print("\n[D] IDOR fichier : accéder au média d'un autre utilisateur")
for nom, client in [("annonceur B", cB), ("partageur B", cPB)]:
    r = client.get("/uploads/atk_a.jpg")
    bloque(f"{nom} ne peut pas télécharger le fichier de A (404)", r.status_code == 404, f"(status={r.status_code})")

# =========================================================================
print("\n[E] Cloisonnement sous-admin : permission absente")
# SA a 'valider_campagnes' mais PAS 'gerer_retraits'
r = cSA.post(f"/admin/retraits/{wd_id}/payer-manuel")
bloque("sous-admin sans 'gerer_retraits' ne peut pas payer un retrait (403)",
       r.status_code == 403, f"(status={r.status_code})")
with app.app_context():
    w = db.session.get(WithdrawalRequest, wd_id)
    bloque("le retrait reste en attente", w.status == "pending", f"(status={w.status})")

# =========================================================================
print("\n[F] Détournement de campagne : partager une campagne non diffusée / hors zone")
with app.app_context():
    # Campagne de B, NON diffusée aux partageurs
    campB = Campaign(user_id=B.id, promotion_type="photo", promotion_detail="Camp non diffusée de B",
                     provinces="Toutes", media_type="photo", media_files="atk_b.jpg",
                     total_cost=1000.0, paid=True, validated=True, is_active=True,
                     shared_to_partageurs=False, payment_status="paid", status="active")
    db.session.add(campB); db.session.commit()
    campB_id = campB.id
r = cPB.post(f"/partageur/partager_campagne/{campB_id}", follow_redirects=False)
with app.app_context():
    s = CampaignShare.query.filter_by(campaign_id=campB_id, sharer_id=PB.id).first()
    bloque("partageur ne peut pas partager une campagne non diffusée (aucun partage créé)",
           s is None, f"(share={'créé' if s else 'aucun'})")

print("\n" + "=" * 60)
print(f"RÉSULTAT ATTAQUES : {R['ok']} bloquées, {R['faille']} faille(s)")
print("=" * 60)
sys.exit(1 if R["faille"] else 0)

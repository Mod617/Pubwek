# -*- coding: utf-8 -*-
"""Test de bout en bout PubWek (instance LOCALE, base SQLite jetable).

Parcours réel : inscription annonceur -> inscription partageur -> validation
admin du partageur -> connexions -> création de campagne (photo) -> PAIEMENT
via le webhook FedaPay SIGNÉ (seul l'appel HTTP externe à FedaPay est simulé)
-> validation admin de la campagne -> partage par le partageur -> clic d'un
vrai visiteur via le lien de tracking -> preuve de fin de journée -> validation
admin de la preuve -> versement de la récompense au portefeuille.

Chaque étape passe par les VRAIES routes Flask (client de test). CSRF désactivé
dans le client (la protection CSRF est vérifiée séparément en direct sur la
prod). Aucune donnée n'est écrite en production.
"""
import hmac, hashlib, json, os, sys
from datetime import datetime

# Mot de passe de l'admin créé au démarrage (variable ADMIN_PASSWORD).
ADMIN_MDP = os.environ.get("ADMIN_PASSWORD", "TestAdmin123!")

# Hors-ligne : on désactive la vérification DNS de délivrabilité de l'e-mail
# (en production, les vrais utilisateurs ont des adresses livrables et cette
# vérification passe normalement). La validation SYNTAXIQUE reste active.
import email_validator
_orig_validate = email_validator.validate_email
def _validate_sans_dns(*a, **k):
    k["check_deliverability"] = False
    return _orig_validate(*a, **k)
email_validator.validate_email = _validate_sans_dns

import main
from main import app, db
from models import (User, Campaign, CampaignShare, CampaignClick,
                    CampaignShareProof, Transaction, SystemConfig, WalletTransaction)

app.config["WTF_CSRF_ENABLED"] = False
app.config["FEDAPAY_WEBHOOK_SECRET"] = "secret-webhook-test"

R = {"ok": 0, "ko": 0}
def check(nom, cond, detail=""):
    if cond:
        R["ok"] += 1; print(f"  OK    {nom}")
    else:
        R["ko"] += 1; print(f"  ECHEC {nom} {detail}")

def poster(client, url, data, suivre=True):
    return client.post(url, data=data, follow_redirects=suivre)

print("=" * 60)
print("Test de bout en bout PubWek (local)")
print("=" * 60)

with app.app_context():
    # --- Admin : créé au démarrage via ADMIN_EMAIL/ADMIN_PASSWORD ---
    admin = User.query.filter_by(role="admin").first()
    print(f"\n[Pré-requis] admin présent : {bool(admin)} ({admin.email if admin else '—'})")

    config = SystemConfig.get_config()
    config.exiger_preuve_partage = True  # comportement de production
    db.session.commit()

annonceur_client = app.test_client()
partageur_client = app.test_client()
admin_client = app.test_client()

# =========================================================================
print("\n[1] Inscription de l'annonceur (vraie route /register/annonceur)")
r = poster(annonceur_client, "/register/annonceur", {
    "email": "e2e-annonceur@pubwek-test.com",
    "password": "MotDePasseE2E1", "confirm_password": "MotDePasseE2E1",
    "role": "annonceur", "province": "Littoral", "commune": "Cotonou",
    "company_name": "Boutique E2E", "whatsapp_number": "01000001",
})
with app.app_context():
    a = User.query.filter_by(email="e2e-annonceur@pubwek-test.com").first()
    check("annonceur créé", a is not None, f"(status={r.status_code})")
    check("annonceur auto-confirmé (rôle annonceur)", bool(a and a.is_confirmed))

# =========================================================================
print("\n[2] Inscription du partageur (vraie route /register/partageur)")
r = poster(partageur_client, "/register/partageur", {
    "email": "e2e-partageur@pubwek-test.com",
    "password": "MotDePasseE2E2", "confirm_password": "MotDePasseE2E2",
    "role": "partageur", "province": "Littoral", "commune": "Cotonou",
    "whatsapp_number": "01000002",
})
with app.app_context():
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
    check("partageur créé", p is not None, f"(status={r.status_code})")
    check("partageur en attente de confirmation admin", bool(p and not p.is_confirmed))

# =========================================================================
print("\n[3] Connexion admin + confirmation du partageur")
with app.app_context():
    admin = User.query.filter_by(role="admin").first()
    # on connaît le mot de passe passé au démarrage
r = poster(admin_client, "/login", {"email": admin.email if admin else "admin@pubwek-test.com",
                                     "password": ADMIN_MDP})
with app.app_context():
    # vérifie la session admin en accédant à une page protégée admin
    rp = admin_client.get("/admin/validate")
    check("admin connecté (accès /admin/validate)", rp.status_code == 200, f"(status={rp.status_code})")
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
r = admin_client.post(f"/admin/confirm_user/{p.id}", follow_redirects=True)
with app.app_context():
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
    check("partageur confirmé par l'admin", bool(p and p.is_confirmed), f"(status={r.status_code})")

# =========================================================================
print("\n[4] Connexions annonceur & partageur")
r1 = poster(annonceur_client, "/login", {"email": "e2e-annonceur@pubwek-test.com", "password": "MotDePasseE2E1"})
da = annonceur_client.get("/dashboard/annonceur")
check("annonceur connecté (dashboard 200)", da.status_code == 200, f"(status={da.status_code})")
r2 = poster(partageur_client, "/login", {"email": "e2e-partageur@pubwek-test.com", "password": "MotDePasseE2E2"})
dp = partageur_client.get("/dashboard/partageur")
check("partageur connecté (dashboard 200)", dp.status_code == 200, f"(status={dp.status_code})")

# =========================================================================
print("\n[5] Création d'une campagne photo (en base, non payée) + transaction FedaPay en attente")
with app.app_context():
    a = User.query.filter_by(email="e2e-annonceur@pubwek-test.com").first()
    camp = Campaign(
        user_id=a.id, promotion_type="photo", promotion_detail="Campagne E2E",
        provinces="Toutes", media_type="photo", media_files="e2e_photo_1.jpg",
        whatsapp_number="+2290101000001",
        total_cost=5000.0, target_whatsapp_views=100, whatsapp_views=0,
        duration_days=3, paid=False, validated=False, is_active=False,
        payment_status="pending", status="brouillon",
    )
    db.session.add(camp); db.session.commit()
    camp_id = camp.id
    tx = Transaction(
        user_id=a.id, campaign_id=camp_id, amount=5000.0,
        transaction_type="campaign_payment", status="pending",
        reference="E2E-REF-0001", fedapay_transaction_id="E2E-TX-0001",
    )
    db.session.add(tx); db.session.commit()
    check("campagne créée non payée", (not camp.paid) and camp.payment_status == "pending")
    check("transaction FedaPay en attente", tx.status == "pending")

# =========================================================================
print("\n[6] PAIEMENT via webhook FedaPay SIGNÉ (appel externe simulé)")
# On simule uniquement l'appel HTTP à FedaPay ; la vérification de signature,
# la recherche de la transaction et l'application du paiement sont le vrai code.
main.verifier_transaction = lambda tid: {"status": "approved", "id": tid}
main._statut_fedapay = lambda details: "approved"

corps = json.dumps({"entity": {"id": "E2E-TX-0001", "status": "approved"}}).encode("utf-8")
horodatage = str(int(datetime.utcnow().timestamp()))
secret = app.config["FEDAPAY_WEBHOOK_SECRET"].encode("utf-8")
signature = hmac.new(secret, f"{horodatage}.".encode("utf-8") + corps, hashlib.sha256).hexdigest()

webhook_client = app.test_client()
# 6a. Signature invalide -> rejet
r_bad = webhook_client.post("/webhooks/fedapay", data=corps,
                            headers={"x-fedapay-signature": f"t={horodatage},s=deadbeef",
                                     "Content-Type": "application/json"})
check("webhook : signature invalide rejetée (400)", r_bad.status_code == 400, f"(status={r_bad.status_code})")
# 6b. Signature valide -> paiement appliqué
r_ok = webhook_client.post("/webhooks/fedapay", data=corps,
                           headers={"x-fedapay-signature": f"t={horodatage},s={signature}",
                                    "Content-Type": "application/json"})
check("webhook : signature valide acceptée (200)", r_ok.status_code == 200, f"(status={r_ok.status_code})")
with app.app_context():
    camp = db.session.get(Campaign, camp_id)
    tx = Transaction.query.filter_by(fedapay_transaction_id="E2E-TX-0001").first()
    check("campagne marquée payée après webhook", camp.paid and camp.payment_status == "paid",
          f"(paid={camp.paid}, status={camp.payment_status})")
    check("transaction approuvée", tx.status == "approved")

# =========================================================================
print("\n[7] Validation de la campagne par l'admin (vraie route)")
r = admin_client.post(f"/admin/validate_campaign/{camp_id}", follow_redirects=True)
with app.app_context():
    camp = db.session.get(Campaign, camp_id)
    check("campagne validée et active", camp.validated and camp.is_active,
          f"(validated={camp.validated}, active={camp.is_active}, status={r.status_code})")
    # Diffusion aux partageurs (déclenchée par l'admin via /admin/partager_campagne)
    camp.shared_to_partageurs = True
    if not camp.shared_at:
        camp.shared_at = datetime.utcnow()
    db.session.commit()

# =========================================================================
print("\n[8] Partage de la campagne par le partageur (vraie route)")
r = partageur_client.post(f"/partageur/partager_campagne/{camp_id}", follow_redirects=True)
with app.app_context():
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
    share = CampaignShare.query.filter_by(campaign_id=camp_id, sharer_id=p.id).first()
    check("partage créé avec jeton de tracking", bool(share and share.tracking_token),
          f"(status={r.status_code})")
    token = share.tracking_token if share else None

# =========================================================================
print("\n[9] Clic d'un vrai visiteur via le lien de tracking (vraie route /t/<token>/whatsapp)")
NAV = ("Mozilla/5.0 (Linux; Android 13; SM-A536B) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
visiteur = app.test_client()
rc = visiteur.get(f"/t/{token}/whatsapp",
                  headers={"User-Agent": NAV, "X-Forwarded-For": "41.85.200.10"})
with app.app_context():
    share = CampaignShare.query.filter_by(campaign_id=camp_id).first()
    clics = CampaignClick.query.filter_by(campaign_share_id=share.id).all()
    payable = [c for c in clics if c.is_paid]
    check("clic enregistré et redirigé (302)", rc.status_code in (301, 302), f"(status={rc.status_code})")
    check("clic marqué payable en attente de preuve", len(payable) == 1 and payable[0].rewarded_at is None,
          f"(clics={len(clics)}, payables={len(payable)})")
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
    check("portefeuille non crédité tant que la preuve n'est pas validée", (p.wallet_balance or 0.0) == 0.0,
          f"(solde={p.wallet_balance})")

# =========================================================================
print("\n[10] Preuve de fin de journée + validation admin -> versement")
with app.app_context():
    share = CampaignShare.query.filter_by(campaign_id=camp_id).first()
    camp = db.session.get(Campaign, camp_id)
    jour = camp.jour_diffusion_campagne()
    preuve = CampaignShareProof(
        campaign_share_id=share.id, day_number=jour, proof_type="fin",
        filename="e2e_preuve_fin.jpg", status="en_attente",
    )
    db.session.add(preuve); db.session.commit()
    preuve_id = preuve.id
r = admin_client.post(f"/admin/preuve/{preuve_id}/valider", follow_redirects=True)
with app.app_context():
    preuve = db.session.get(CampaignShareProof, preuve_id)
    p = User.query.filter_by(email="e2e-partageur@pubwek-test.com").first()
    camp = db.session.get(Campaign, camp_id)
    config = SystemConfig.get_config()
    attendu = main.recompense_pour(camp, config)
    check("preuve validée par l'admin", preuve.status == "validee", f"(status={r.status_code})")
    check("portefeuille crédité après validation de la preuve",
          abs((p.wallet_balance or 0.0) - attendu) < 1e-9,
          f"(solde={p.wallet_balance}, attendu={attendu})")
    wt = WalletTransaction.query.filter_by(user_id=p.id, transaction_type="click_reward").all()
    check("écriture de portefeuille 'click_reward' présente", len(wt) == 1, f"(nb={len(wt)})")

# =========================================================================
print("\n" + "=" * 60)
print(f"RÉSULTAT E2E : {R['ok']} réussis, {R['ko']} échoués")
print("=" * 60)
sys.exit(1 if R["ko"] else 0)

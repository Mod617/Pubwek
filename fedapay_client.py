# fedapay_client.py
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

if not Config.FEDAPAY_SECRET_KEY:
    logger.critical("FEDAPAY_SECRET_KEY manquant dans les variables d'environnement.")


def _headers():
    return {
        "Authorization": f"Bearer {Config.FEDAPAY_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _base_url():
    return Config().FEDAPAY_BASE_URL


def _extraire_objet_transaction(data):
    """
    Extrait l'objet transaction en gérant les différentes structures
    renvoyées par les versions de l'API FedaPay ('v1/transaction', 'transaction', ou direct).
    """
    if isinstance(data, dict):
        if "v1/transaction" in data:
            return data["v1/transaction"]
        elif "transaction" in data:
            return data["transaction"]
    return data


def creer_transaction(montant, description, metadata, customer_email=None, customer_phone=None):
    """
    Crée une transaction FedaPay (sandbox tant que FEDAPAY_ENV=sandbox).
    `metadata` doit permettre d'identifier plus tard, dans la vérification,
    de quel paiement il s'agit (ex: type='campaign', campaign_id=..., user_id=...).
    """
    payload = {
        "description": description,
        "amount": int(montant),
        "currency": {"iso": "XOF"},
        "metadata": metadata,
    }
    if customer_email or customer_phone:
        payload["customer"] = {}
        if customer_email:
            payload["customer"]["email"] = customer_email
        if customer_phone:
            payload["customer"]["phone_number"] = {"number": customer_phone, "country": "BJ"}

    resp = requests.post(f"{_base_url()}/transactions", json=payload, headers=_headers(), timeout=15)
    resp.raise_for_status()
    
    return _extraire_objet_transaction(resp.json())


def generer_lien_paiement(transaction_id):
    """Génère l'URL de paiement à afficher/rediriger vers l'utilisateur."""
    # S'assure de récupérer uniquement l'ID entier/chaîne au cas où un objet dictionnaire serait passé
    if isinstance(transaction_id, dict):
        transaction_id = transaction_id.get("id")

    resp = requests.post(
        f"{_base_url()}/transactions/{transaction_id}/token",
        headers=_headers(), timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    
    if "token" in data and isinstance(data["token"], dict):
        return data["token"]["url"]
    elif "url" in data:
        return data["url"]
    
    return data


def verifier_transaction(transaction_id):
    """
    Interroge DIRECTEMENT l'API FedaPay pour connaître le vrai statut d'une transaction.
    On rappelle toujours cette fonction avant de marquer quoi que ce soit comme payé —
    jamais confiance uniquement au retour navigateur.
    Statuts possibles : 'pending' | 'approved' | 'declined' | 'canceled' | 'transferred'
    """
    if isinstance(transaction_id, dict):
        transaction_id = transaction_id.get("id")

    resp = requests.get(f"{_base_url()}/transactions/{transaction_id}", headers=_headers(), timeout=15)
    resp.raise_for_status()
    
    return _extraire_objet_transaction(resp.json())
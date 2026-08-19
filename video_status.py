import json
import redis

# Connexion à Redis (compatibilité RESP2 pour Windows/Redis 3.x)
r = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True,
    protocol=2
)

def set_progress(user_id, data, ttl=3600):
    """Enregistre le statut dans Redis avec une durée de vie (ex: 1 heure)."""
    r.set(f"video_progress:{str(user_id)}", json.dumps(data), ex=ttl)

def get_progress(user_id):
    """Récupère le statut depuis Redis."""
    raw = r.get(f"video_progress:{str(user_id)}")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"percentage": 0, "status": "Initialisation..."}

def delete_progress(user_id):
    """Supprime la clé de progression dans Redis."""
    r.delete(f"video_progress:{str(user_id)}")
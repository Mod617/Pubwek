from celery import Celery

def make_celery(app_name="pubwek_tasks"):
    redis_url = "redis://localhost:6379/0"
    return Celery(
        app_name,
        backend=redis_url,
        broker=redis_url
    )

celery = make_celery()

# Configuration propre pour Celery
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,  # Expiration des résultats après 1h pour ne pas engorger Redis
)
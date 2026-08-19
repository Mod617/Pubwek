import os
import logging
from celery_app import celery
from video_status import get_progress, set_progress

logger = logging.getLogger(__name__)

@celery.task(name="tasks.generer_video")
def task_generer_video(user_id, images_uniquement, output_path, brand_name_final, slogan_video, audio_path_final, logo_path_final, video_generee_nom, noms_fichiers_str):
    try:
        # Importation locale pour éviter la boucle d'importation circulaire avec main.py
        from main import generer_diaporama_pro

        # Vérification si la tâche a été annulée avant de commencer
        current_status = get_progress(user_id)
        if current_status.get("status") == "cancelled":
            return {"status": "cancelled"}

        # Mise à jour du statut dans Redis
        set_progress(user_id, {
            "percentage": 40,
            "status": "Moteur Pubwek : Compilation des images, logos et encodage vidéo..."
        })

        # Appel de la fonction de génération vidéo lourde
        generer_diaporama_pro(
            images_uniquement,
            output_path,
            brand_name=brand_name_final,
            slogan=slogan_video,
            audio_path=audio_path_final,
            logo_path=logo_path_final,
            user_id=user_id
        )

        # Vérification si l'utilisateur a annulé pendant l'encodage
        if get_progress(user_id).get("status") == "cancelled":
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            return {"status": "cancelled"}

        # Génération terminée avec succès
        set_progress(user_id, {
            "percentage": 100,
            "status": "done",
            "video_url": f"/uploads/{video_generee_nom}",
            "video_name": video_generee_nom,
            "media_files_list": noms_fichiers_str
        })
        return {"status": "success", "video_url": f"/uploads/{video_generee_nom}"}

    except Exception as e:
        logger.error("Erreur génération vidéo (user %s) : %s", user_id, e)
        if get_progress(user_id).get("status") != "cancelled":
            set_progress(user_id, {
                "percentage": 0,
                "status": "error",
                "error": "Une erreur est survenue lors de la génération."
            })
        raise e
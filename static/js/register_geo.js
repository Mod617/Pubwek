// static/js/register_geo.js

document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("get-location-btn");
    const status = document.getElementById("location-status");
    const latField = document.getElementById("latitude");
    const lonField = document.getElementById("longitude");

    if (!btn) return; // sécurité si bouton absent

    btn.addEventListener("click", function (e) {
        e.preventDefault();

        if (!navigator.geolocation) {
            status.textContent = "❌ La géolocalisation n'est pas supportée par votre navigateur.";
            return;
        }

        status.textContent = "📍 Détection de votre position en cours...";

        navigator.geolocation.getCurrentPosition(
            async (position) => {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;

                latField.value = lat;
                lonField.value = lon;

                status.textContent = `✅ Position détectée : ${lat.toFixed(5)}, ${lon.toFixed(5)}`;

                // Vérification que l'utilisateur est au Bénin via API gratuite
                try {
                    let response = await fetch(
                        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`
                    );
                    let data = await response.json();
                    if (
                        data.address &&
                        data.address.country_code &&
                        data.address.country_code.toLowerCase() === "bj"
                    ) {
                        status.textContent += " (Vous êtes bien au Bénin 🇧🇯)";
                    } else {
                        status.textContent = "❌ Vous devez être au Bénin pour vous inscrire.";
                        latField.value = "";
                        lonField.value = "";
                    }
                } catch (err) {
                    console.error("Erreur API géoloc:", err);
                    status.textContent =
                        "⚠️ Impossible de vérifier votre position exacte. Réessayez.";
                }
            },
            (error) => {
                switch (error.code) {
                    case error.PERMISSION_DENIED:
                        status.textContent = "❌ Autorisation refusée pour accéder à la position.";
                        break;
                    case error.POSITION_UNAVAILABLE:
                        status.textContent = "❌ Informations de localisation indisponibles.";
                        break;
                    case error.TIMEOUT:
                        status.textContent = "⏳ La demande de localisation a expiré.";
                        break;
                    default:
                        status.textContent = "❌ Une erreur est survenue.";
                        break;
                }
            }
        );
    });
});

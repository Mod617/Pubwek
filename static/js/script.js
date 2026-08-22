// ============================================
// PAGE : dashboard_campagne.html
// ============================================

(function() {
    // --- VARIABLES GLOBALES ---
    let selectedFiles = []; // Fichiers photos cumulés pour l'Option B (Multi-Statuts)

    let currentTarget = null;
    let isDragging = false;
    let startX, startY;
    let startPercentX = 50, startPercentY = 50;

    // Positions en mémoire (valeurs par défaut)
    let coverPos = { x: 50, y: 50 };
    let logoPos = { x: 50, y: 50 };

    const MAX_IMAGES = 25;
    const DRAFT_STORAGE_KEY = 'pubwek_draft_images';

    // Cache pour les Blob URLs afin d'éviter les fuites mémoire
    const objectUrlCache = new WeakMap();

    function getBlobUrl(file) {
        if (!objectUrlCache.has(file)) {
            objectUrlCache.set(file, URL.createObjectURL(file));
        }
        return objectUrlCache.get(file);
    }

    // --- UTILITAIRES ---

    function getCsrfToken() {
        const metaToken = document.querySelector('meta[name="csrf-token"]');
        if (metaToken) return metaToken.getAttribute('content');
        const inputToken = document.querySelector('input[name="csrf_token"]');
        return inputToken ? inputToken.value : '';
    }

    function fileToBase64(file) {
        return new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = e => resolve({ name: file.name, data: e.target.result });
            reader.onerror = () => resolve(null);
            reader.readAsDataURL(file);
        });
    }

    function base64ToFile(base64Data, filename) {
        try {
            const arr = base64Data.split(',');
            if (arr.length < 2) return null;
            const mimeMatch = arr[0].match(/:(.*?);/);
            const mime = mimeMatch ? mimeMatch[1] : 'image/png';
            const bstr = atob(arr[1]);
            let n = bstr.length;
            const u8arr = new Uint8Array(n);
            while (n--) {
                u8arr[n] = bstr.charCodeAt(n);
            }
            return new File([u8arr], filename || 'image_restauree.png', { type: mime });
        } catch (e) {
            console.error("Erreur lors de la conversion Base64 vers File :", e);
            return null;
        }
    }

    // Sauvegarde sécurisée (gestion de la limite de stockage 5MB)
    async function persistDraftImages() {
        try {
            const encoded = await Promise.all(selectedFiles.map(file => fileToBase64(file)));
            const validEncoded = encoded.filter(Boolean);
            localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(validEncoded));
        } catch (e) {
            if (e.name === 'QuotaExceededError') {
                console.warn("Le quota LocalStorage a été dépassé. Les images restent chargées en mémoire mais ne seront pas conservées au rafraîchissement.");
            } else {
                console.error("Impossible d'enregistrer les photos en cache local :", e);
            }
        }
    }

    function syncInputFiles() {
        const mediaInput = document.getElementById('media_files');
        if (!mediaInput) return;
        const dataTransfer = new DataTransfer();
        selectedFiles.forEach(file => {
            if (file instanceof File) dataTransfer.items.add(file);
        });
        mediaInput.files = dataTransfer.files;
    }

    // --- DRAG & DROP POSITIONNEMENT COUVERTURE / LOGO ---
    function initDragAndDrop(containerId, imgId, saveBtnId, type) {
        const container = document.getElementById(containerId);
        const img = document.getElementById(imgId);
        const saveBtn = document.getElementById(saveBtnId);
        if (!container || !img || !saveBtn) return;

        function startDrag(e) {
            if (e.target.tagName === 'BUTTON' || e.target.closest('.logo-overlay') || e.target.closest('.cover-actions')) return;

            isDragging = true;
            currentTarget = { img, saveBtn, type, container };

            const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
            const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;

            startX = clientX;
            startY = clientY;

            const currentPosition = img.style.objectPosition || "50% 50%";
            const parts = currentPosition.split(' ');
            startPercentX = parseFloat(parts[0]) || 50;
            startPercentY = parseFloat(parts[1]) || 50;

            document.body.style.userSelect = 'none';

            window.addEventListener('mousemove', handleMove);
            window.addEventListener('touchmove', handleMove, { passive: false });
            window.addEventListener('mouseup', endDrag);
            window.addEventListener('touchend', endDrag);
        }

        container.addEventListener('mousedown', startDrag);
        container.addEventListener('touchstart', startDrag, { passive: true });
    }

    function handleMove(e) {
        if (!isDragging || !currentTarget) return;

        const clientX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
        const clientY = e.type.includes('touch') ? e.touches[0].clientY : e.clientY;

        const deltaX = clientX - startX;
        const deltaY = clientY - startY;

        const moveFactorX = (deltaX / currentTarget.container.offsetWidth) * 100;
        const moveFactorY = (deltaY / currentTarget.container.offsetHeight) * 100;

        let newX = Math.min(100, Math.max(0, startPercentX - moveFactorX));
        let newY = Math.min(100, Math.max(0, startPercentY - moveFactorY));

        currentTarget.img.style.objectPosition = `${newX}% ${newY}%`;
        currentTarget.saveBtn.classList.remove('d-none');

        if (currentTarget.type === 'cover') {
            coverPos = { x: newX, y: newY };
        } else {
            logoPos = { x: newX, y: newY };
        }
    }

    function endDrag() {
        if (!isDragging) return;
        isDragging = false;
        document.body.style.userSelect = '';

        window.removeEventListener('mousemove', handleMove);
        window.removeEventListener('touchmove', handleMove);
        window.removeEventListener('mouseup', endDrag);
        window.removeEventListener('touchend', endDrag);
    }

    // --- ENVOI DES POSITIONS EN AJAX ---
    function saveCoverPosition() {
        const formData = new FormData();
        formData.append('position_x', coverPos.x);
        formData.append('position_y', coverPos.y);

        fetch("/dashboard/annonceur/update_cover_position_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const btn = document.getElementById('btnSaveCoverPosition');
                if (btn) btn.classList.add('d-none');
                alert("Position de la couverture enregistrée !");
            } else {
                alert("Erreur position : " + data.error);
            }
        })
        .catch(err => console.error("Erreur réseau sauvegarde couverture :", err));
    }

    function saveLogoPosition() {
        const formData = new FormData();
        formData.append('position_x', logoPos.x);
        formData.append('position_y', logoPos.y);

        fetch("/dashboard/annonceur/update_logo_position_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const btn = document.getElementById('btnSaveLogoPosition');
                if (btn) btn.classList.add('d-none');
                alert("Position du profil enregistrée !");
            } else {
                alert("Erreur position : " + data.error);
            }
        })
        .catch(err => console.error("Erreur réseau sauvegarde logo :", err));
    }

    // --- UPLOADS DIRECTS EN AJAX (cover / logo) ---
    function uploadCoverViaAjax() {
        const fileInput = document.getElementById('ajaxCoverInput');
        if (!fileInput || fileInput.files.length === 0) return;

        const formData = new FormData();
        formData.append('cover_file', fileInput.files[0]);

        fetch("/dashboard/annonceur/update_cover_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const img = document.getElementById('userCoverPreview');
                if (img) {
                    img.src = data.cover_url;
                    img.style.objectPosition = "50% 50%";
                }
            } else {
                alert("Erreur de couverture : " + data.error);
            }
        })
        .catch(err => console.error("Erreur réseau couverture :", err));
    }

    function uploadLogoViaAjax() {
        const fileInput = document.getElementById('ajaxLogoInput');
        if (!fileInput || fileInput.files.length === 0) return;

        const formData = new FormData();
        formData.append('logo_file', fileInput.files[0]);

        fetch("/dashboard/annonceur/update_logo_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const img = document.getElementById('userLogoPreview');
                if (img) {
                    img.src = data.logo_url;
                    img.style.objectPosition = "50% 50%";
                }
            } else {
                alert("Erreur logo : " + data.error);
            }
        })
        .catch(err => console.error("Erreur réseau logo :", err));
    }

    // --- ÉDITION NOM & BIO DE L'ENTREPRISE ---
    function enableCompanyNameEdit() {
        const text = document.getElementById('companyNameText');
        const btn = document.getElementById('btnEditName');
        const input = document.getElementById('companyNameInput');
        if (text) text.style.display = 'none';
        if (btn) btn.style.display = 'none';
        if (input) {
            input.style.display = 'block';
            input.focus();
        }
    }

    function cancelCompanyNameEdit() {
        const input = document.getElementById('companyNameInput');
        const text = document.getElementById('companyNameText');
        const btn = document.getElementById('btnEditName');
        if (input) input.style.display = 'none';
        if (text) text.style.display = 'block';
        if (btn) btn.style.display = 'block';
    }

    function saveCompanyNameViaAjax() {
        const input = document.getElementById('companyNameInput');
        if (!input) return;
        const newName = input.value.trim();
        if (newName === "") {
            alert("Le nom ne peut pas être vide.");
            cancelCompanyNameEdit();
            return;
        }

        const formData = new FormData();
        formData.append('company_name', newName);

        fetch("/dashboard/annonceur/update_name_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const text = document.getElementById('companyNameText');
                if (text) text.textContent = data.company_name;
            } else {
                alert("Erreur : " + data.error);
            }
            cancelCompanyNameEdit();
        })
        .catch(err => {
            console.error("Erreur réseau nom :", err);
            cancelCompanyNameEdit();
        });
    }

    function handleNameKeydown(event) {
        if (event.key === "Enter") {
            event.preventDefault();
            const input = document.getElementById('companyNameInput');
            if (input) input.blur();
        } else if (event.key === "Escape") {
            cancelCompanyNameEdit();
        }
    }

    function enableCompanyBioEdit() {
        const text = document.getElementById('companyBioText');
        const textarea = document.getElementById('companyBioInput');
        if (text) text.style.display = 'none';
        if (textarea) {
            textarea.style.display = 'block';
            textarea.focus();
        }
    }

    function saveCompanyBioViaAjax() {
        const textarea = document.getElementById('companyBioInput');
        if (!textarea) return;
        const newBio = textarea.value.trim();

        const formData = new FormData();
        formData.append('bio', newBio);

        fetch("/dashboard/annonceur/update_bio_ajax", {
            method: "POST",
            body: formData,
            headers: { "X-CSRFToken": getCsrfToken() }
        })
        .then(response => response.json())
        .then(data => {
            const text = document.getElementById('companyBioText');
            if (data.success && text) {
                // textContent, pas innerHTML : la bio est une saisie utilisateur.
                text.textContent = "";
                if (data.bio) {
                    const icone = document.createElement('i');
                    icone.className = "fas fa-quote-left me-1 opacity-50";
                    text.appendChild(icone);
                    text.appendChild(document.createTextNode(" " + data.bio));
                } else {
                    text.textContent = "Ajouter une description ou présentation de votre entreprise...";
                }
            } else {
                alert("Erreur bio : " + data.error);
            }
            textarea.style.display = 'none';
            if (text) text.style.display = 'block';
        })
        .catch(err => {
            console.error("Erreur réseau bio :", err);
            textarea.style.display = 'none';
            const text = document.getElementById('companyBioText');
            if (text) text.style.display = 'block';
        });
    }

    // --- BASCULE VIDÉO / PHOTOS / TEXTE (Option A / B / C) ---
    function handleDisplayOptionChange() {
        const optionA = document.getElementById('optionA');
        const optionB = document.getElementById('optionB');
        const isOptionA = optionA ? optionA.checked : true;
        const isOptionB = optionB ? optionB.checked : false;
        const isOptionC = !isOptionA && !isOptionB;

        const videoInput = document.getElementById('video_file');
        const mediaInput = document.getElementById('media_files');

        // Seule l'option correspondante impose un fichier requis ; l'Option C n'en a besoin d'aucun
        if (videoInput) videoInput.required = isOptionA;
        if (mediaInput) mediaInput.required = isOptionB;

        updateTotal();
    }

    // --- CALCUL DU BUDGET TOTAL ---
    function updateTotal() {
        const form = document.getElementById('campaignForm');

        const costVideo = form ? parseFloat(form.getAttribute('data-cost-video')) || 0 : 0;
        const costPhoto = form ? parseFloat(form.getAttribute('data-cost-photo')) || 0 : 0;
        const costText = form ? parseFloat(form.getAttribute('data-cost-text')) || 0 : 0;
        const commissionRate = form ? parseFloat(form.getAttribute('data-commission-rate')) || 0 : 0;

        const viewsInput = document.getElementById('whatsapp_views');
        const views = viewsInput ? (parseInt(viewsInput.value, 10) || 0) : 0;

        const optionA = document.getElementById('optionA');
        const optionB = document.getElementById('optionB');
        const isOptionA = optionA ? optionA.checked : true;
        const isOptionB = optionB ? optionB.checked : false;
        const isOptionC = !isOptionA && !isOptionB;

        let fileCount = selectedFiles.length;

        let unitCost = costVideo; // Option A par défaut
        if (isOptionB) {
            unitCost = costPhoto * (fileCount > 0 ? fileCount : 1);
        } else if (isOptionC) {
            unitCost = costText;
        }

        const base = views * unitCost;
        const commission = Math.round(base * (commissionRate / 100));
        const fees = Math.round(base * 0.01);
        const total = base + commission + fees;

        const totalDisplay = document.getElementById('total_cost_display');
        const totalHidden = document.getElementById('total_cost_hidden');
        if (totalDisplay) totalDisplay.textContent = total.toLocaleString('fr-FR') + " XOF";
        if (totalHidden) totalHidden.value = total;

        const fileInfo = document.getElementById('fileInfo');
        if (fileInfo) {
            fileInfo.textContent = (isOptionB && selectedFiles.length > 0)
                ? `📊 ${selectedFiles.length} photo(s) sélectionnée(s).`
                : "";
        }
    }
    window.updateTotal = updateTotal;

    // --- VIGNETTES DES PHOTOS (Option B) ---
    function renderImagePreviews() {
        let container = document.getElementById('imagePreviewsContainer');
        const mediaInput = document.getElementById('media_files');

        if (!container && mediaInput && mediaInput.parentNode) {
            container = document.createElement('div');
            container.id = 'imagePreviewsContainer';
            container.className = 'd-flex flex-wrap gap-2 mt-2';
            mediaInput.parentNode.appendChild(container);
        }
        if (!container) return;

        container.innerHTML = '';

        selectedFiles.forEach((file, index) => {
            const fileUrl = getBlobUrl(file);

            const wrapper = document.createElement('div');
            wrapper.className = 'position-relative d-inline-block';
            wrapper.style.width = '75px';
            wrapper.style.height = '75px';

            const img = document.createElement('img');
            img.src = fileUrl;
            img.className = 'img-thumbnail w-100 h-100';
            img.style.objectFit = 'cover';
            img.style.borderRadius = '8px';
            img.style.cursor = 'pointer';
            img.title = 'Cliquer pour agrandir';

            img.addEventListener('click', () => {
                const modalImg = document.getElementById('previewModalImage');
                const modalElement = document.getElementById('imagePreviewModal');
                if (modalImg && modalElement) {
                    modalImg.src = fileUrl;
                    if (typeof bootstrap !== 'undefined') {
                        new bootstrap.Modal(modalElement).show();
                    }
                }
            });

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.innerHTML = '&times;';
            removeBtn.className = 'btn btn-danger btn-sm position-absolute p-0 d-flex align-items-center justify-content-center';
            removeBtn.style.cssText = 'top: -6px; right: -6px; width: 22px; height: 22px; border-radius: 50%; font-size: 14px; line-height: 1; border: 2px solid white; z-index: 2;';

            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                selectedFiles.splice(index, 1);
                persistDraftImages();
                syncInputFiles();
                renderImagePreviews();
                updateTotal();
            });

            wrapper.appendChild(img);
            wrapper.appendChild(removeBtn);
            container.appendChild(wrapper);
        });
    }

    // --- EXPOSITION GLOBALE ---
    window.triggerCoverUpload = function() {
        const input = document.getElementById('ajaxCoverInput');
        if (input) input.click();
    };
    window.triggerLogoUpload = function() {
        const input = document.getElementById('ajaxLogoInput');
        if (input) input.click();
    };
    window.uploadCoverViaAjax = uploadCoverViaAjax;
    window.uploadLogoViaAjax = uploadLogoViaAjax;
    window.saveCoverPosition = saveCoverPosition;
    window.saveLogoPosition = saveLogoPosition;
    window.enableCompanyNameEdit = enableCompanyNameEdit;
    window.saveCompanyNameViaAjax = saveCompanyNameViaAjax;
    window.handleNameKeydown = handleNameKeydown;
    window.enableCompanyBioEdit = enableCompanyBioEdit;
    window.saveCompanyBioViaAjax = saveCompanyBioViaAjax;

    // --- INITIALISATION ---
    document.addEventListener('DOMContentLoaded', async () => {
        // 1. Réhydratation des photos depuis le cache local
        try {
            const stored = JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY) || '[]');
            if (Array.isArray(stored) && stored.length > 0) {
                selectedFiles = stored
                    .map((item, index) => item && item.data ? base64ToFile(item.data, item.name || `image_${index + 1}.png`) : null)
                    .filter(Boolean);
                syncInputFiles();
            }
        } catch (e) {
            console.error("Erreur lors de la réhydratation des photos :", e);
        }

        // 2. Positionnement couverture / logo
        const coverContainer = document.getElementById('coverContainer');
        const logoContainer = document.getElementById('logoContainer');

        if (coverContainer) {
            coverPos.x = parseFloat(coverContainer.dataset.posX) || 50;
            coverPos.y = parseFloat(coverContainer.dataset.posY) || 50;
            initDragAndDrop('coverContainer', 'userCoverPreview', 'btnSaveCoverPosition', 'cover');
        }
        if (logoContainer) {
            logoPos.x = parseFloat(logoContainer.dataset.posX) || 50;
            logoPos.y = parseFloat(logoContainer.dataset.posY) || 50;
            initDragAndDrop('logoContainer', 'userLogoPreview', 'btnSaveLogoPosition', 'logo');
        }

        // 3. Upload multi-photos (Option B)
        const mediaFilesField = document.getElementById('media_files');
        if (mediaFilesField) {
            mediaFilesField.addEventListener('change', async function() {
                const newFiles = Array.from(this.files);
                let addedCount = 0;

                for (const file of newFiles) {
                    if (selectedFiles.length >= MAX_IMAGES) break;
                    const exists = selectedFiles.some(f => f.name === file.name && f.size === file.size);
                    if (!exists) {
                        selectedFiles.push(file);
                        addedCount++;
                    }
                }

                if (newFiles.length > addedCount && selectedFiles.length >= MAX_IMAGES) {
                    alert(`Vous ne pouvez sélectionner que ${MAX_IMAGES} photos maximum.`);
                }

                await persistDraftImages();
                syncInputFiles();
                renderImagePreviews();
                updateTotal();
            });
        }

        // 4. Recalcul du budget sur changement du nombre de vues
        const whatsappViewsField = document.getElementById('whatsapp_views');
        if (whatsappViewsField) {
            whatsappViewsField.addEventListener('input', updateTotal);
        }

        // 5. Recalcul du budget + required sur changement d'option A/B/C
        const cardA = document.getElementById('cardA');
        const cardB = document.getElementById('cardB');
        const cardC = document.getElementById('cardC');
        const optionA = document.getElementById('optionA');
        const optionB = document.getElementById('optionB');
        const optionC = document.getElementById('optionC');
        if (cardA) cardA.addEventListener('click', handleDisplayOptionChange);
        if (cardB) cardB.addEventListener('click', handleDisplayOptionChange);
        if (cardC) cardC.addEventListener('click', handleDisplayOptionChange);
        if (optionA) optionA.addEventListener('change', handleDisplayOptionChange);
        if (optionB) optionB.addEventListener('change', handleDisplayOptionChange);
        if (optionC) optionC.addEventListener('change', handleDisplayOptionChange);

        // 6. Validation avant soumission
        const campaignForm = document.getElementById('campaignForm');
        if (campaignForm) {
            campaignForm.addEventListener('submit', function(e) {
                const isOptionA = optionA ? optionA.checked : true;
                const isOptionB = optionB ? optionB.checked : false;
                const isOptionC = !isOptionA && !isOptionB;

                if (isOptionA) {
                    const videoInput = document.getElementById('video_file');
                    if (!videoInput || videoInput.files.length === 0) {
                        e.preventDefault();
                        alert("Veuillez téléverser votre vidéo publicitaire (30 secondes maximum).");
                        return;
                    }
                }

                if (isOptionB && selectedFiles.length === 0) {
                    e.preventDefault();
                    alert("Veuillez téléverser au moins une photo pour le mode Multi-Statuts.");
                    return;
                }

                if (isOptionC) {
                    const descriptionField = document.getElementById('description');
                    if (!descriptionField || descriptionField.value.trim() === "") {
                        e.preventDefault();
                        alert("Veuillez rédiger le texte de votre publicité pour l'Option C.");
                        return;
                    }
                }

                const provinceChecks = document.querySelectorAll('input[name="provinces[]"]:checked');
                if (provinceChecks.length === 0) {
                    e.preventDefault();
                    alert("Veuillez sélectionner au moins une zone de diffusion.");
                }
            });
        }

        // 7. État initial
        renderImagePreviews();
        handleDisplayOptionChange();
    });
})();
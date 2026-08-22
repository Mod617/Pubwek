-- =============================================================================
-- Migration des changements de schéma issus de l'audit — PostgreSQL (Railway)
--
-- POURQUOI CE FICHIER
-- db.create_all() crée les tables manquantes mais n'ajoute JAMAIS une colonne
-- à une table qui existe déjà. Ces colonnes sont donc à créer à la main, une
-- seule fois, avant de déployer le nouveau code. Sans elles, l'application
-- plante au démarrage avec « column users.last_seen_ip does not exist ».
--
-- COMMENT L'EXÉCUTER
--   1. Sauvegarder la base (Railway : onglet Data → Backup).
--   2. Ouvrir la console PostgreSQL du projet.
--   3. Coller ce fichier en entier. Toutes les instructions sont idempotentes
--      (IF NOT EXISTS) : le relancer ne casse rien.
--   4. Déployer le nouveau code.
--
-- La table uploaded_files n'est pas ici : étant NOUVELLE, db.create_all() la
-- créera tout seul au premier démarrage.
--
-- Ceci reste un dépannage. Installer Flask-Migrate reste la bonne solution
-- pour la suite — voir LISEZ-MOI-AUDIT.md.
-- =============================================================================

BEGIN;

-- --- Anti-fraude : décision de paiement conservée sur chaque clic -----------
ALTER TABLE campaign_clicks
    ADD COLUMN IF NOT EXISTS is_paid BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE campaign_clicks
    ADD COLUMN IF NOT EXISTS rejection_reason VARCHAR(40);

-- Les clics déjà enregistrés avant la migration ont tous été payés sans
-- contrôle : on les marque comme tels pour rester fidèle à l'historique.
UPDATE campaign_clicks SET is_paid = TRUE WHERE rejection_reason IS NULL;

-- --- Détection de l'auto-clic ------------------------------------------------
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_seen_ip VARCHAR(45);

-- --- Seuils anti-fraude, réglables depuis la configuration -------------------
ALTER TABLE system_config
    ADD COLUMN IF NOT EXISTS click_dedup_hours INTEGER DEFAULT 24;

ALTER TABLE system_config
    ADD COLUMN IF NOT EXISTS max_paid_clicks_per_share_per_day INTEGER DEFAULT 50;

ALTER TABLE system_config
    ADD COLUMN IF NOT EXISTS max_paid_clicks_per_ip_per_day INTEGER DEFAULT 20;

ALTER TABLE system_config
    ADD COLUMN IF NOT EXISTS min_seconds_between_paid_clicks INTEGER DEFAULT 20;

-- Renseigner la ligne de configuration existante, créée avant ces colonnes
UPDATE system_config
SET click_dedup_hours                 = COALESCE(click_dedup_hours, 24),
    max_paid_clicks_per_share_per_day = COALESCE(max_paid_clicks_per_share_per_day, 50),
    max_paid_clicks_per_ip_per_day    = COALESCE(max_paid_clicks_per_ip_per_day, 20),
    min_seconds_between_paid_clicks   = COALESCE(min_seconds_between_paid_clicks, 20);

-- --- Index servant les requêtes de déduplication -----------------------------
-- Sans eux, chaque clic déclencherait un parcours complet de campaign_clicks.
CREATE INDEX IF NOT EXISTS ix_campaign_clicks_ip
    ON campaign_clicks (ip);

CREATE INDEX IF NOT EXISTS ix_campaign_clicks_is_paid
    ON campaign_clicks (is_paid);

CREATE INDEX IF NOT EXISTS idx_share_ip_paid
    ON campaign_clicks (campaign_share_id, ip, is_paid, clicked_at);

CREATE INDEX IF NOT EXISTS idx_ip_paid_date
    ON campaign_clicks (ip, is_paid, clicked_at);

COMMIT;

-- =============================================================================
-- Vérification : les sept colonnes doivent apparaître.
-- =============================================================================
-- SELECT table_name, column_name
-- FROM information_schema.columns
-- WHERE (table_name = 'campaign_clicks' AND column_name IN ('is_paid', 'rejection_reason'))
--    OR (table_name = 'users'           AND column_name = 'last_seen_ip')
--    OR (table_name = 'system_config'   AND column_name LIKE '%click%')
-- ORDER BY table_name, column_name;

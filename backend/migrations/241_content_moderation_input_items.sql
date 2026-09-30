ALTER TABLE content_moderation_logs
    ADD COLUMN IF NOT EXISTS input_items JSONB;

COMMENT ON COLUMN content_moderation_logs.input_items IS
    'Complete current-turn input for administrator review; absent for legacy records';

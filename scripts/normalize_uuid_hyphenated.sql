-- Normalize existing compact UUID strings to standard hyphenated UUID strings.
--
-- Use this after the UUID columns have been widened to CHAR(36).
-- It is idempotent: already-hyphenated values are left unchanged.
--
-- Run against the storybook database:
--   mysql -u app -p storybook < scripts/normalize_uuid_hyphenated.sql

SET @old_foreign_key_checks = @@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS = 0;

DELIMITER $$

DROP PROCEDURE IF EXISTS normalize_uuid_column $$

CREATE PROCEDURE normalize_uuid_column(IN p_table_name VARCHAR(128), IN p_column_name VARCHAR(128))
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
          AND COLUMN_NAME = p_column_name
    ) THEN
        SET @table_name = REPLACE(p_table_name, '`', '``');
        SET @column_name = REPLACE(p_column_name, '`', '``');
        SET @sql = CONCAT(
            'UPDATE `', @table_name, '` ',
            'SET `', @column_name, '` = LOWER(CONCAT(',
                'SUBSTRING(`', @column_name, '`, 1, 8), ''-'', ',
                'SUBSTRING(`', @column_name, '`, 9, 4), ''-'', ',
                'SUBSTRING(`', @column_name, '`, 13, 4), ''-'', ',
                'SUBSTRING(`', @column_name, '`, 17, 4), ''-'', ',
                'SUBSTRING(`', @column_name, '`, 21, 12)',
            ')) ',
            'WHERE `', @column_name, '` IS NOT NULL ',
              'AND CHAR_LENGTH(`', @column_name, '`) = 32 ',
              'AND `', @column_name, '` NOT LIKE ''%-%'' ',
              'AND `', @column_name, '` REGEXP ''^[0-9A-Fa-f]{32}$'''
        );
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END $$

DELIMITER ;

-- Child/reference columns first.
CALL normalize_uuid_column('child_activity_logs', 'child_id');
CALL normalize_uuid_column('child_activity_logs', 'resource_id');
CALL normalize_uuid_column('child_audios', 'child_id');
CALL normalize_uuid_column('child_audios', 'audio_id');
CALL normalize_uuid_column('child_books', 'child_id');
CALL normalize_uuid_column('child_books', 'story_id');
CALL normalize_uuid_column('child_profiles', 'user_id');
CALL normalize_uuid_column('custom_story_batch_jobs', 'workflow_id');
CALL normalize_uuid_column('custom_story_batch_jobs', 'story_id');
CALL normalize_uuid_column('custom_story_workflow_steps', 'workflow_id');
CALL normalize_uuid_column('custom_story_workflows', 'user_id');
CALL normalize_uuid_column('custom_story_workflows', 'child_id');
CALL normalize_uuid_column('custom_story_workflows', 'story_id');
CALL normalize_uuid_column('generic_story_batch_jobs', 'generic_story_id');
CALL normalize_uuid_column('generic_story_batch_jobs', 'workflow_id');
CALL normalize_uuid_column('generic_story_contents', 'generic_story_id');
CALL normalize_uuid_column('generic_story_workflows', 'user_id');
CALL normalize_uuid_column('generic_story_workflows', 'generic_story_id');
CALL normalize_uuid_column('notifications', 'user_id');
CALL normalize_uuid_column('notifications', 'child_id');
CALL normalize_uuid_column('otp_verifications', 'user_id');
CALL normalize_uuid_column('push_device_tokens', 'user_id');
CALL normalize_uuid_column('push_device_tokens', 'child_id');
CALL normalize_uuid_column('refresh_tokens', 'user_id');
CALL normalize_uuid_column('stories', 'user_id');
CALL normalize_uuid_column('stories', 'child_id');
CALL normalize_uuid_column('story_batch_jobs', 'story_id');
CALL normalize_uuid_column('story_contents', 'story_id');
CALL normalize_uuid_column('story_pages', 'story_id');
CALL normalize_uuid_column('story_steps', 'story_id');
CALL normalize_uuid_column('users', 'active_child_profile_id');

-- Parent / primary key columns after references.
CALL normalize_uuid_column('child_activity_logs', 'id');
CALL normalize_uuid_column('child_audios', 'id');
CALL normalize_uuid_column('child_books', 'id');
CALL normalize_uuid_column('child_profiles', 'id');
CALL normalize_uuid_column('custom_story_batch_jobs', 'id');
CALL normalize_uuid_column('custom_story_workflow_steps', 'id');
CALL normalize_uuid_column('custom_story_workflows', 'id');
CALL normalize_uuid_column('generic_audios', 'id');
CALL normalize_uuid_column('generic_stories', 'id');
CALL normalize_uuid_column('generic_story_batch_jobs', 'id');
CALL normalize_uuid_column('generic_story_contents', 'id');
CALL normalize_uuid_column('generic_story_workflows', 'id');
CALL normalize_uuid_column('notifications', 'id');
CALL normalize_uuid_column('otp_verifications', 'id');
CALL normalize_uuid_column('push_device_tokens', 'id');
CALL normalize_uuid_column('refresh_tokens', 'id');
CALL normalize_uuid_column('stories', 'id');
CALL normalize_uuid_column('story_batch_jobs', 'id');
CALL normalize_uuid_column('story_contents', 'id');
CALL normalize_uuid_column('story_pages', 'id');
CALL normalize_uuid_column('story_steps', 'id');
CALL normalize_uuid_column('users', 'id');

DROP PROCEDURE IF EXISTS normalize_uuid_column;

SET FOREIGN_KEY_CHECKS = @old_foreign_key_checks;

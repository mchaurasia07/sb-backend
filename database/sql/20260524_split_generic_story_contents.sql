CREATE TABLE generic_story_contents (
    id CHAR(32) NOT NULL,
    generic_story_id CHAR(32) NOT NULL,
    language VARCHAR(2) NOT NULL,
    story_json JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_generic_story_contents_language CHECK (language IN ('en', 'hi')),
    CONSTRAINT fk_generic_story_contents_story_id_generic_stories
        FOREIGN KEY (generic_story_id) REFERENCES generic_stories (id)
        ON DELETE CASCADE,
    CONSTRAINT uq_generic_story_contents_story_language UNIQUE (generic_story_id, language)
);

CREATE INDEX ix_generic_story_contents_story_id
    ON generic_story_contents (generic_story_id);

CREATE INDEX ix_generic_story_contents_language
    ON generic_story_contents (language);

INSERT INTO generic_story_contents (
    id,
    generic_story_id,
    language,
    story_json,
    created_at,
    updated_at
)
SELECT
    REPLACE(UUID(), '-', ''),
    id,
    CASE WHEN language IN ('en', 'hi') THEN language ELSE 'en' END,
    story_json,
    created_at,
    updated_at
FROM generic_stories
WHERE story_json IS NOT NULL;

ALTER TABLE generic_stories DROP COLUMN story_json;
ALTER TABLE generic_stories DROP COLUMN language;

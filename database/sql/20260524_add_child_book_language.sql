ALTER TABLE child_books
    ADD COLUMN language VARCHAR(2) NOT NULL DEFAULT 'en';

ALTER TABLE child_books
    ADD CONSTRAINT ck_child_books_language CHECK (language IN ('en', 'hi', 'mr'));

ALTER TABLE child_books
    DROP INDEX uq_child_books_child_story_type;

ALTER TABLE child_books
    ADD CONSTRAINT uq_child_books_child_story_type_language
        UNIQUE (child_id, story_id, story_type, language);

CREATE INDEX ix_child_books_language ON child_books (language);

CREATE TABLE generic_stories (
    id CHAR(32) NOT NULL,
    title VARCHAR(255) NOT NULL,
    summary TEXT NULL,
    age_group VARCHAR(32) NOT NULL,
    theme VARCHAR(100) NULL,
    genre VARCHAR(100) NULL,
    language VARCHAR(50) NOT NULL,
    moral VARCHAR(255) NULL,
    learning_goal VARCHAR(500) NULL,
    reading_time_minutes INT NULL,
    character_type VARCHAR(100) NULL,
    total_pages INT NOT NULL,
    cover_image VARCHAR(1024) NULL,
    story_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_generic_stories_reading_time_non_negative CHECK (reading_time_minutes >= 0),
    CONSTRAINT ck_generic_stories_total_pages_non_negative CHECK (total_pages >= 0)
);

CREATE INDEX ix_generic_stories_status ON generic_stories (status);
CREATE INDEX ix_generic_stories_age_group ON generic_stories (age_group);
CREATE INDEX ix_generic_stories_theme ON generic_stories (theme);
CREATE INDEX ix_generic_stories_genre ON generic_stories (genre);
CREATE INDEX ix_generic_stories_created_at ON generic_stories (created_at);

CREATE TABLE child_books (
    id CHAR(32) NOT NULL,
    child_id CHAR(32) NOT NULL,
    story_id CHAR(32) NOT NULL,
    story_type VARCHAR(32) NOT NULL,
    title VARCHAR(255) NOT NULL,
    cover_image VARCHAR(1024) NULL,
    status VARCHAR(32) NOT NULL,
    last_page_read INT NOT NULL,
    last_page_read_time DATETIME NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_child_books_child_id_child_profiles
        FOREIGN KEY (child_id) REFERENCES child_profiles (id)
        ON DELETE CASCADE,
    CONSTRAINT uq_child_books_child_story_type UNIQUE (child_id, story_id, story_type)
);

CREATE INDEX ix_child_books_child_id ON child_books (child_id);
CREATE INDEX ix_child_books_story_id ON child_books (story_id);
CREATE INDEX ix_child_books_status ON child_books (status);
CREATE INDEX ix_child_books_created_at ON child_books (created_at);

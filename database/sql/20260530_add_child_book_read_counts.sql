ALTER TABLE child_books
    ADD COLUMN reading_started_count INT NOT NULL DEFAULT 0,
    ADD COLUMN reading_completed_count INT NOT NULL DEFAULT 0;

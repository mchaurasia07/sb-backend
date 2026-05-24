DELETE cb
FROM child_books cb
JOIN generic_stories gs
  ON cb.story_type = 'generic'
 AND cb.story_id = gs.id
JOIN generic_stories newer
  ON newer.title = gs.title
 AND (
      newer.created_at > gs.created_at
      OR (newer.created_at = gs.created_at AND newer.id > gs.id)
 );

DELETE gs
FROM generic_stories gs
JOIN generic_stories newer
  ON newer.title = gs.title
 AND (
      newer.created_at > gs.created_at
      OR (newer.created_at = gs.created_at AND newer.id > gs.id)
 );

ALTER TABLE generic_stories
    ADD CONSTRAINT uq_generic_stories_title UNIQUE (title);

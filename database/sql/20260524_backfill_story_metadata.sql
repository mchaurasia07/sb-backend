UPDATE stories
SET
    title = COALESCE(
        NULLIF(title, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.title')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_plan_json, '$.title')), '')
    ),
    moral = COALESCE(
        NULLIF(moral, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.moral')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.moral_theme')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_plan_json, '$.moral_theme')), '')
    ),
    summary = COALESCE(
        NULLIF(summary, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.summary')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_plan_json, '$.summary')), '')
    ),
    category = COALESCE(
        NULLIF(category, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.source_inputs.category')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_plan_json, '$.source_inputs.category')), ''),
        NULLIF(event_description, '')
    ),
    learning_goal = COALESCE(
        NULLIF(learning_goal, ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_json, '$.source_inputs.learning_goal')), ''),
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(story_plan_json, '$.source_inputs.learning_goal')), '')
    )
WHERE story_json IS NOT NULL OR story_plan_json IS NOT NULL;

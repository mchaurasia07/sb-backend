# Google Story Workflow Production Plan

Date: 2026-05-31
Provider target: Google AI only

## Goal

Make story generation cheaper, resumable, and safer without changing the public story creation behavior. The workflow remains:

1. Story plan generation
2. Story plan validation
3. Story text generation
4. Image plan generation
5. Image plan validation
6. Image generation
7. Narration generation

## Current Risk Summary

- The workflow runs in a FastAPI background task. If the process restarts, the task is lost.
- Some step progress is flushed but not committed before long model calls, so polling can lag.
- Final story content was written only at the end. A late narration failure could waste completed text and image work.
- Image/page writes were not idempotent. Retrying could hit duplicate page records or regenerate paid images.
- Most external calls fail the whole workflow instead of retrying transient provider/network errors.
- Text calls use large token ceilings, especially `36000`, which raises cost exposure.
- Gemini JSON mode is used, but response schemas are not yet enforced.
- TTS duration and timestamps are estimated from text, not measured from generated audio.

## Production Architecture Direction

Use checkpointed state, not one giant transaction. A long AI workflow should commit after each durable checkpoint so work can be reused after a failure. The production contract should be:

- Every step can be run more than once.
- Every external output is persisted as soon as it becomes useful.
- Retrying a failed story resumes from the last usable artifact.
- Page/image/audio writes are idempotent.
- Step telemetry records model, token usage, duration, retry count, and error code.

## Implementation Phases

### Phase 1: Reliability Foundation

Implemented first because it protects cost immediately.

- Add retry API: `POST /api/v1/stories/{story_id}/retry`.
- Resume from stored artifacts:
  - `stories.story_plan_json`
  - `stories.image_plan_json`
  - `story_contents.story_json`
  - existing `story_pages`
- Commit story status/current step before long model calls.
- Persist story JSON right after story text generation.
- Persist image plan right after validation.
- Use page upserts so retry does not duplicate pages.
- Skip already generated page images when retrying.
- Use narration `overwrite=false` inside workflow so existing audio/timing can be reused.

### Phase 2: Google Cost Control

- Enforce `AI_PROVIDER=google` in production settings.
- Add age-tiered max output tokens:
  - plan: lower fixed ceiling
  - story text: toddler < early reader < advanced
  - image plan: toddler < early reader < advanced
- Store Google `usage_metadata` per step for cost reporting.
- Add per-story cost estimate fields or an audit table.
- Remove raw prompt `print()` and redact logs.

### Phase 3: Structured Outputs

- Define Pydantic schemas for:
  - story plan
  - story JSON
  - image plan
- Pass Gemini response schema in `GenerateContentConfig`.
- Keep validators as defense-in-depth.
- Replace full plan regeneration with targeted repair when only a small field is invalid.

### Phase 4: Resilience and Throughput

- Move from FastAPI `BackgroundTasks` to a durable worker queue.
- Add worker heartbeat and stuck-job recovery.
- Add provider retry with exponential backoff and jitter.
- Add circuit breaker for Google outages/rate limits.
- Generate images concurrently with a configurable concurrency limit.

### Phase 5: Audio Accuracy

- Compute WAV duration from audio bytes instead of word-count estimate.
- Rename `word_timestamps` internally to sentence timestamps while keeping API compatibility.
- Optionally support real alignment if Google exposes timing metadata for the selected TTS model.

## Retry Semantics

Retry is allowed only for a story owned by the parent user and currently not `IN_PROGRESS`.

On retry:

- If validated story plan exists, reuse it.
- If story content exists, reuse story text.
- If image plan exists, reuse it.
- If a page image exists, skip regenerating it.
- If narration metadata exists, skip regenerating it.
- If a later step fails, keep earlier completed artifacts.

## Google-Only Notes

The code still supports multiple providers today. For production, keep the abstraction only if you need future fallback. Otherwise, simplify around Google:

- Gemini text model for plan/story/image-plan.
- Gemini image model with reference image for story illustrations.
- Imagen only for text-only fallback if quality/cost is acceptable.
- Gemini TTS for narration.

## Success Metrics

- Failed workflow retry reuses prior paid artifacts.
- No duplicate `story_pages` after retries.
- Parent can retry a failed story with one API call.
- Story polling shows accurate current step.
- Token ceilings are lower by age group.
- Cost per story can be measured per step.

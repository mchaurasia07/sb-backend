"""Generate sentence-level timestamps for story narration audio."""

import re
from typing import List


_SENTENCE_PATTERN = re.compile(r"\S.*?(?:[.!?।]+[\"')\]]*|$)", re.DOTALL)


def _split_sentences(text: str) -> list[str]:
    """Split narration into readable sentence segments."""
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []

    sentences = [match.group(0).strip() for match in _SENTENCE_PATTERN.finditer(normalized)]
    return [sentence for sentence in sentences if sentence]


def generate_word_timestamps(text: str, audio_duration: float) -> List[dict]:
    """
    Generate approximate sentence-level timestamps based on text and audio duration.

    The historical response field is named `word_timestamps`, so each item keeps
    the `word` key for API compatibility. The value is now a complete sentence,
    allowing the reader UI to highlight one sentence at a time.

    Args:
        text: Narration text to generate timestamps for
        audio_duration: Total duration of audio in seconds

    Returns:
        List of dicts with keys: word, start (seconds), end (seconds)

    Example:
        >>> timestamps = generate_word_timestamps("Every night. Luna waited.", 2.0)
        >>> timestamps[0]
        {'word': 'Every night.', 'start': 0.0, 'end': 0.95}
    """
    if not text or not text.strip():
        return []

    if audio_duration <= 0:
        return []

    sentences = _split_sentences(text)

    if not sentences:
        return []

    sentence_lengths = [max(1, len(sentence)) for sentence in sentences]
    total_length = sum(sentence_lengths)
    timestamps = []
    current_time = 0.0

    for index, sentence in enumerate(sentences):
        start_time = current_time
        if index == len(sentences) - 1:
            end_time = audio_duration
        else:
            sentence_duration = audio_duration * (sentence_lengths[index] / total_length)
            end_time = start_time + sentence_duration

        timestamps.append({"word": sentence, "start": round(start_time, 2), "end": round(end_time, 2)})

        current_time = end_time

    return timestamps

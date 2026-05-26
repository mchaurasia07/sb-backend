"""Generate word-level timestamps for story narration audio."""

import re
from typing import List


def generate_word_timestamps(text: str, audio_duration: float) -> List[dict]:
    """
    Generate approximate word-level timestamps based on text and audio duration.

    Algorithm:
    1. Split text into words
    2. Calculate base duration per word
    3. Adjust word duration based on word length
    4. Distribute timestamps across duration

    Args:
        text: Narration text to generate timestamps for
        audio_duration: Total duration of audio in seconds

    Returns:
        List of dicts with keys: word, start (seconds), end (seconds)

    Example:
        >>> timestamps = generate_word_timestamps("Every night", 2.0)
        >>> timestamps[0]
        {'word': 'Every', 'start': 0.0, 'end': 0.95}
    """
    if not text or not text.strip():
        return []

    if audio_duration <= 0:
        return []

    # Split text into words, preserving punctuation
    # This regex captures contiguous non-whitespace as words
    words = text.split()

    if not words:
        return []

    # Calculate base duration per word
    base_duration_per_word = audio_duration / len(words)

    # Calculate average word length for normalization
    word_lengths = [len(word) for word in words]
    avg_word_length = sum(word_lengths) / len(word_lengths) if word_lengths else 1

    # Generate timestamps with length-based adjustment
    timestamps = []
    current_time = 0.0

    for i, word in enumerate(words):
        # Length factor: longer words get slightly more duration
        length_factor = len(word) / avg_word_length if avg_word_length > 0 else 1.0
        # Cap the length factor to avoid extreme variations (0.5x to 1.5x)
        length_factor = max(0.5, min(1.5, length_factor))

        # Adjusted duration for this word
        word_duration = base_duration_per_word * length_factor

        start_time = current_time
        end_time = start_time + word_duration

        timestamps.append({"word": word, "start": round(start_time, 2), "end": round(end_time, 2)})

        current_time = end_time

    return timestamps

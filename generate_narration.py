"""Generate narration audio per beat using edge-tts (free, no API key required).

Generating ONE audio file per beat (instead of one file for the whole script) is the
core trick that makes sync possible: each beat's audio duration becomes the exact
duration we trim its matched footage clip to.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts

from utils import get_duration, log

DEFAULT_VOICE = "en-US-GuyNeural"  # change to any edge-tts voice you prefer


async def _synthesize(text: str, voice: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def generate_beat_audio(text: str, out_path: Path, voice: str = DEFAULT_VOICE) -> float:
    """Synthesize narration for one beat and return its duration in seconds."""
    asyncio.run(_synthesize(text, voice, out_path))
    duration = get_duration(out_path)
    log.info("Narration '%s...' -> %.2fs (%s)", text[:40], duration, out_path.name)
    return duration


def generate_all(beats: list[dict], out_dir: Path, voice: str = DEFAULT_VOICE) -> list[dict]:
    """Attach 'audio_path' and 'duration' to each beat dict, in place, and return it."""
    for i, beat in enumerate(beats):
        audio_path = out_dir / f"audio_{i}.mp3"
        duration = generate_beat_audio(beat["line"], audio_path, voice)
        beat["audio_path"] = str(audio_path)
        beat["duration"] = duration
    return beats

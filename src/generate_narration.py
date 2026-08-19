"""Generate natural-sounding narration audio per beat using edge-tts."""
from __future__ import annotations
import asyncio
from pathlib import Path
import edge_tts
from utils import get_duration, log

# More natural documentary-style US English voice than the previous Guy voice.
DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_RATE = "-6%"
DEFAULT_PITCH = "-1Hz"

async def _synthesize(text: str, voice: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=DEFAULT_RATE, pitch=DEFAULT_PITCH)
    await communicate.save(str(out_path))

def generate_beat_audio(text: str, out_path: Path, voice: str = DEFAULT_VOICE) -> float:
    asyncio.run(_synthesize(text, voice, out_path))
    duration = get_duration(out_path)
    log.info("Narration '%s...' -> %.2fs (%s)", text[:40], duration, out_path.name)
    return duration

def generate_all(beats: list[dict], out_dir: Path, voice: str = DEFAULT_VOICE) -> list[dict]:
    for i, beat in enumerate(beats):
        audio_path = out_dir / f"audio_{i}.mp3"
        duration = generate_beat_audio(beat["line"], audio_path, voice)
        beat["audio_path"] = str(audio_path)
        beat["duration"] = duration
    return beats

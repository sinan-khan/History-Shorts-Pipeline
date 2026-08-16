"""End-to-end pipeline: pick topic -> script+metadata -> footage -> narration -> assemble -> upload.

Run locally:
    GROQ_API_KEY=... python src/pipeline.py "the Apollo 11 moon landing"

Run with no topic argument to auto-pick the next topic from the queue in
config/topics.json / config/state.json — see topic_queue.py. This is what lets
multiple runs per day each get a different, non-repeating topic.

Set DRY_RUN=1 to build the video but skip the YouTube upload step.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fetch_footage import fetch_clip_for_beat
from generate_narration import generate_all
from generate_script import generate_script
from assemble_video import assemble
from topic_queue import get_next_topic
from upload_youtube import upload_short
from utils import ensure_dir, log

ROOT = Path(__file__).resolve().parent.parent
TOPICS_FILE = ROOT / "config" / "topics.json"
STATE_FILE = ROOT / "config" / "state.json"

SOURCE_CREDIT = "\n\nHistorical footage sourced from public-domain archives via the Internet Archive."


def build_video(topic: str, work_dir: Path) -> tuple[Path, dict]:
    """Returns (video_path, script) where script has title/description/tags/beats."""
    script = generate_script(topic)
    beats = script["beats"]

    footage_dir = ensure_dir(work_dir / "footage")
    footage_paths = []
    for i, beat in enumerate(beats):
        clip = fetch_clip_for_beat(
            query=beat["footage_query"],
            fallback_query=topic,
            out_dir=footage_dir,
            index=i,
        )
        if clip is None:
            raise RuntimeError(
                f"No public-domain footage found for beat {i} "
                f"(query='{beat['footage_query']}', fallback='{topic}'). "
                "Try a broader footage_query or add a manual fallback clip."
            )
        footage_paths.append(clip)

    audio_dir = ensure_dir(work_dir / "audio")
    beats = generate_all(beats, audio_dir)
    script["beats"] = beats

    out_path = work_dir / "final_short.mp4"
    assemble(beats, footage_paths, work_dir, out_path)
    return out_path, script


def main() -> None:
    forced_topic = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else None
    topic = forced_topic or get_next_topic(TOPICS_FILE, STATE_FILE)
    log.info("=== Building history short: %s ===", topic)

    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        work_dir = Path(tmp)
        video_path, script = build_video(topic, work_dir)

        if os.environ.get("DRY_RUN") == "1":
            keep_path = ROOT / "output_preview.mp4"
            keep_path.write_bytes(video_path.read_bytes())
            log.info("DRY_RUN set — video saved to %s, skipping upload.", keep_path)
            return

        description = script["description"].rstrip() + SOURCE_CREDIT
        upload_short(
            video_path,
            title=script["title"],
            description=description,
            tags=script["tags"],
        )


if __name__ == "__main__":
    main()

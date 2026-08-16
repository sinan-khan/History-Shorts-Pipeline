"""Tracks which topic to use next so that multiple runs per day never repeat a topic
until the whole pool has cycled.

State is a single {"next_index": N} pointer written to config/state.json. The GitHub
Actions workflow commits this file back to the repo after a successful run, so the
pointer survives across scheduled runs (GitHub Actions gives each run a clean checkout,
it has no memory of its own).
"""
from __future__ import annotations

import json
from pathlib import Path

from utils import log


def get_next_topic(topics_path: Path, state_path: Path) -> str:
    topics = json.loads(topics_path.read_text())
    if not topics:
        raise ValueError(f"{topics_path} is empty")

    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {"next_index": 0}

    idx = state.get("next_index", 0) % len(topics)
    topic = topics[idx]

    state["next_index"] = (idx + 1) % len(topics)
    state_path.write_text(json.dumps(state, indent=2))

    log.info("Topic queue: using index %d/%d -> '%s'", idx, len(topics), topic)
    return topic

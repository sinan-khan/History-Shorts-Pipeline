"""Generate a 'did you know' history script AND its YouTube metadata in one call.

Each beat = {"line": narration sentence, "footage_query": archive.org search term}.
Keeping the script structured (not a paragraph) is what makes footage sync possible:
every sentence gets its own search query and later its own matched clip + audio duration.

The same call also returns title/description/tags so nothing about publishing needs
a human in the loop.
"""
from __future__ import annotations

import json
import os
import re

import requests

from utils import log

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# llama-3.3-70b-versatile was shut down by Groq on 2026-08-16. Groq's own migration
# guidance points to openai/gpt-oss-120b as the closest replacement. Override with
# GROQ_MODEL if you'd rather use openai/gpt-oss-20b (faster/cheaper) or another model —
# check https://console.groq.com/docs/models for the current list.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

SYSTEM_PROMPT = """You write short "did you know" history scripts for vertical YouTube
Shorts, AND the YouTube publishing metadata for that video.

Output ONLY valid JSON. No markdown fences, no commentary. Output a single JSON object
with exactly this shape:

{
  "title": "...",
  "description": "...",
  "tags": ["...", "..."],
  "beats": [{"line": "...", "footage_query": "..."}, ...]
}

Rules for "beats":
- 6 to 8 beats.
- Each beat: {"line": "...", "footage_query": "..."}.
- "line" is ONE spoken sentence, punchy, 8-18 words, plain narration (no quotes from
  copyrighted sources).
- "footage_query" is a short (2-5 word) search phrase describing archival footage that
  would visually match that specific sentence (e.g. "1969 apollo moon landing",
  "1929 wall street crowd").
- The first beat must hook the viewer ("Did you know..." style opener).
- The last beat must be a punchy closer/payoff.
- Total spoken content should run about 35-50 seconds when narrated aloud.
- Keep facts historically accurate. If uncertain of an exact figure, phrase it
  approximately.

Rules for the metadata:
- "title": under 90 characters, includes a hook ("Did You Know" or similar), ends with
  "#Shorts". Must accurately reflect the beats — no misleading clickbait.
- "description": 2-4 sentences summarizing the story IN YOUR OWN WORDS (do not just
  restate the beats verbatim), followed by a blank line and 4-6 relevant hashtags
  (always include #Shorts and #History).
- "tags": 8-15 short plain keyword phrases for YouTube's search tags field (topics,
  era, names, places involved) — no hashtags, no punctuation, just the bare phrases.
"""


def _extract_json(text: str) -> str:
    """Strip markdown code fences if the model adds them anyway."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1) if fence else text


def generate_script(topic: str) -> dict:
    """Call Groq's chat completion API and return {title, description, tags, beats}."""
    api_key = os.environ["GROQ_API_KEY"]
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Topic: {topic}"},
        ],
        "temperature": 0.7,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    log.info("Requesting script + metadata for topic: %s", topic)
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]

    data = json.loads(_extract_json(raw))

    if not isinstance(data, dict) or "beats" not in data:
        raise ValueError(f"Model did not return the expected JSON object: {raw[:300]}")

    beats = data["beats"]
    if not isinstance(beats, list) or not beats:
        raise ValueError(f"'beats' was not a non-empty list: {raw[:300]}")
    for b in beats:
        if "line" not in b or "footage_query" not in b:
            raise ValueError(f"Beat missing required fields: {b}")

    # Fall back to sane defaults if the model skips a metadata field.
    data.setdefault("title", f"Did You Know? {topic.title()} #Shorts")
    data.setdefault("description", f"Quick facts about {topic}.\n\n#shorts #history #didyouknow")
    data.setdefault("tags", ["history", "didyouknow", "shorts", topic.split()[0]])

    log.info("Got %d beats, title='%s'", len(beats), data["title"])
    return data


if __name__ == "__main__":
    import sys

    topic_arg = sys.argv[1] if len(sys.argv) > 1 else "the construction of the Eiffel Tower"
    result = generate_script(topic_arg)
    print(json.dumps(result, indent=2))

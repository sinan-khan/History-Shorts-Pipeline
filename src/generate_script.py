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
# Use llama-3.1-8b-instant (only enabled model on free tier), or set via environment variable
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

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

IMPORTANT: Always output valid, complete JSON with no unterminated strings.
"""


def _extract_json(text: str) -> str:
    """Strip markdown code fences if the model adds them anyway."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1) if fence else text


def generate_script(topic: str) -> dict:
    """Call Groq's chat completion API and return {title, description, tags, beats}."""
    api_key = os.environ.get("GROQ_API_KEY")
    
    # Debug: check if API key is set
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set!")
    
    log.info("GROQ_API_KEY is set: %s", "***" + api_key[-4:] if api_key else "NOT SET")
    log.info("Using endpoint: %s", GROQ_API_URL)
    log.info("Using model: %s", GROQ_MODEL)
    
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Topic: {topic}"},
        ],
        "temperature": 0.7,
        "max_tokens": 1200,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    log.info("Requesting script + metadata for topic: %s", topic)
    
    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=60)
        log.info("Response status code: %d", resp.status_code)
        log.info("Response headers: %s", dict(resp.headers))
        log.info("Response text length: %d bytes", len(resp.text))
        log.info("Response text (first 500 chars): %s", resp.text[:500])
        
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        log.error("HTTP Error: %s", str(e))
        log.error("Response text: %s", resp.text)
        raise
    
    # Check if response is actually JSON
    if not resp.text.strip():
        raise ValueError("API returned empty response!")
    
    log.info("Parsing JSON response...")
    raw = resp.json()["choices"][0]["message"]["content"]
    log.info("Raw model output (first 500 chars): %s", raw[:500])

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

"""Generate accurate history scripts and YouTube metadata."""
from __future__ import annotations
import json
import os
import re
import requests
from utils import log

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

SHORT_PROMPT = """Write an accurate, engaging history YouTube Short.
Output ONLY JSON: {"title":"...","description":"...","tags":[...],"beats":[{"line":"...","footage_query":"..."},...]}
Use 6-8 beats, each line one spoken sentence of 8-18 words. Total narration 35-50 seconds. First beat hooks; last beat pays off. Footage queries are 2-5 words. Title under 90 chars and ends #Shorts. Description is 2-4 sentences plus 4-6 hashtags including #Shorts and #History. Tags are 8-15 plain keywords. Do not invent uncertain facts."""

LONG_PROMPT = """Write an accurate, cinematic long-form history YouTube video about the supplied topic.
Output ONLY JSON: {"title":"...","description":"...","tags":[...],"beats":[{"line":"...","footage_query":"..."},...]}
Create 18-26 beats totaling roughly 5-8 minutes when narrated. Each line should be 25-45 spoken words and form a coherent documentary: hook, context, chronology, key people, details, turning points, surprising facts, significance, and conclusion. Footage queries are 2-5 words and should seek visually useful archival material. Title under 100 characters without #Shorts. Description should summarize the documentary and include relevant hashtags. Tags are 10-20 plain keywords. Keep facts historically accurate and avoid unsupported claims."""


def _extract_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def generate_script(topic: str, long_form: bool = False) -> dict:
    api_key = os.environ["GROQ_API_KEY"]
    system = LONG_PROMPT if long_form else SHORT_PROMPT
    payload = {"model": GROQ_MODEL, "messages":[{"role":"system","content":system},{"role":"user","content":f"Topic: {topic}"}],"temperature":0.7,"max_tokens":7000 if long_form else 3000,"response_format":{"type":"json_object"}}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type":"application/json"}
    log.info("Requesting %s script for topic: %s", "long-form" if long_form else "Short", topic)
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(_extract_json(raw))
    beats = data.get("beats")
    if not isinstance(data, dict) or not isinstance(beats, list) or not beats:
        raise ValueError(f"Model did not return valid beats: {raw[:300]}")
    for beat in beats:
        if not isinstance(beat, dict) or not beat.get("line") or not beat.get("footage_query"):
            raise ValueError(f"Invalid beat: {beat}")
    data.setdefault("title", topic.title() if long_form else f"Did You Know? {topic.title()} #Shorts")
    data.setdefault("description", f"A history of {topic}.\n\n#history #documentary")
    data.setdefault("tags", ["history", "documentary", topic])
    log.info("Got %d beats, title='%s'", len(beats), data["title"])
    return data

if __name__ == "__main__":
    import sys
    print(json.dumps(generate_script(sys.argv[1] if len(sys.argv)>1 else "the Eiffel Tower"), indent=2))

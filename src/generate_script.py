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
LONG_PROMPT = """Write a deeply researched, cinematic 25-35 minute history documentary about the supplied topic.
Output ONLY valid JSON with this shape: {"title":"...","description":"...","tags":[...],"beats":[{"line":"...","footage_query":"..."},...]}
Target approximately 30 minutes of spoken narration: about 3,900-5,000 words total. Create 55-75 beats, with each line normally 55-80 spoken words. Organize the story into a compelling documentary arc: opening hook, historical context, origins, chronology, important people, causes and motivations, major developments, turning points, primary-source-aware details, consequences, lesser-known facts, historical debate where relevant, legacy, and a strong conclusion. Keep the narration flowing naturally between beats rather than sounding like disconnected facts.
Every beat must have a useful 2-5 word footage_query suitable for public-domain/CC0 archival footage. Prefer concrete visual subjects, places, people, buildings, maps, documents, crowds, technology, landscapes, or period events. Do not pad the script merely to reach a runtime. Facts must be historically accurate; distinguish uncertainty or disputed claims instead of inventing details.
Title: under 100 characters, compelling but not misleading, and never include #Shorts. Description: a detailed but concise documentary summary followed by relevant hashtags. Tags: 10-20 plain YouTube search keywords."""


def _extract_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def _request_script(api_key: str, system: str, topic: str, max_tokens: int) -> requests.Response:
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": f"Topic: {topic}"}],
        "temperature": 0.65,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=180)


def generate_script(topic: str, long_form: bool = False) -> dict:
    api_key = os.environ["GROQ_API_KEY"]
    system = LONG_PROMPT if long_form else SHORT_PROMPT
    # Groq can reject an unnecessarily large requested output budget with HTTP 413.
    # 8k is enough for the requested 25-35 minute documentary JSON while leaving
    # headroom for the prompt and structured-output overhead.
    token_budget = 8000 if long_form else 3000
    log.info("Requesting %s script for topic: %s", "long-form" if long_form else "Short", topic)
    resp = _request_script(api_key, system, topic, token_budget)
    if resp.status_code == 413 and long_form:
        log.warning("Groq rejected 8000-token long-form request (413); retrying with 7000 tokens")
        resp = _request_script(api_key, system, topic, 7000)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(_extract_json(raw))
    beats = data.get("beats") if isinstance(data, dict) else None
    if not isinstance(beats, list) or not beats:
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

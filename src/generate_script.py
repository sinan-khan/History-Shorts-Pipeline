"""Generate accurate history scripts and YouTube metadata."""
from __future__ import annotations
import json, os, re, time, requests
from utils import log
GROQ_API_URL="https://api.groq.com/openai/v1/chat/completions"; GROQ_MODEL=os.environ.get("GROQ_MODEL","openai/gpt-oss-120b")
SHORT_PROMPT="""Write an accurate, engaging history YouTube Short. Output ONLY JSON with title, description, tags and beats. Use 6-8 beats, each 8-18 spoken words, 35-50 seconds total. First beat hooks; last pays off. footage_query must be 2-5 concrete visual words. Title under 90 chars and ends #Shorts. Do not invent uncertain facts."""
LONG_PROMPT="""Write a deeply researched cinematic 25-35 minute history documentary. Output ONLY valid JSON with title, description, tags, thumbnail_text and beats. Target about 3,900-5,000 spoken words in 55-75 beats; each beat normally 55-80 words. Build a coherent documentary arc with context, chronology, people, causes, turning points, consequences, debate, lesser-known facts and legacy. Every beat needs a concrete 2-5 word visual query. Vary visual subjects and never repeat the same query in consecutive beats. Be historically accurate and distinguish disputed claims."""
def _extract_json(text:str)->str:
    text=text.strip();m=re.search(r"```(?:json)?\s*(\{.*\})\s*```",text,re.DOTALL);return m.group(1) if m else text
def _request(api_key:str,system:str,topic:str,max_tokens:int)->requests.Response:
    payload={"model":GROQ_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":f"Topic: {topic}"}],"temperature":0.55,"max_tokens":max_tokens,"response_format":{"type":"json_object"}}
    return requests.post(GROQ_API_URL,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},json=payload,timeout=180)
def _normalize_beats(beats:list)->list:
    normalized=[]
    for beat in beats:
        if not isinstance(beat,dict):raise ValueError(f"Invalid beat: {beat}")
        line=beat.get("line") or beat.get("text") or beat.get("narration") or beat.get("script");query=beat.get("footage_query") or beat.get("visual_query") or beat.get("visual")
        if not line or not query:raise ValueError(f"Invalid beat: {beat}")
        b=dict(beat);b["line"]=str(line).strip();b["footage_query"]=str(query).strip();normalized.append(b)
    return normalized
def generate_script(topic:str,long_form:bool=False)->dict:
    api_key=os.environ["GROQ_API_KEY"];system=LONG_PROMPT if long_form else SHORT_PROMPT;budgets=[5000,4200,3600,3000] if long_form else [2500];last=None
    for attempt,budget in enumerate(budgets):
        resp=_request(api_key,system,topic,budget);last=resp
        if resp.ok:break
        if long_form and resp.status_code in (400,413,429):
            wait=min(60,8*(2**attempt));log.warning("Groq long-form request rejected at %d tokens (%s); waiting %ss then retrying",budget,resp.status_code,wait);time.sleep(wait);continue
        resp.raise_for_status()
    if not last or not last.ok:
        if last is not None and last.status_code==429:raise RuntimeError(f"Groq long-form rate limit persisted after {len(budgets)} attempts")
        last.raise_for_status()
    payload=last.json();content=payload.get("choices",[{}])[0].get("message",{}).get("content")
    if not content:raise ValueError(f"Groq returned no message content: {payload.get('error',payload)}")
    data=json.loads(_extract_json(content));beats=data.get("beats") if isinstance(data,dict) else None
    if not isinstance(beats,list) or not beats:raise ValueError("Model did not return valid beats")
    data["beats"]=_normalize_beats(beats);data.setdefault("title",topic.title() if long_form else f"Did You Know? {topic.title()} #Shorts");data.setdefault("description",f"A history of {topic}.\n\n#history #documentary");data.setdefault("tags",["history","documentary",topic])
    if long_form:data.setdefault("thumbnail_text","THE UNTOLD STORY")
    log.info("Got %d beats, title='%s'",len(data["beats"]),data["title"]);return data

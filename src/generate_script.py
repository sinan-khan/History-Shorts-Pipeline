"""Generate accurate history scripts and YouTube metadata."""
from __future__ import annotations
import json, os, re, time, requests
from utils import log
GROQ_API_URL="https://api.groq.com/openai/v1/chat/completions"; GROQ_MODEL=os.environ.get("GROQ_MODEL","openai/gpt-oss-120b")
SHORT_PROMPT="""Write an accurate, engaging history YouTube Short. Output ONLY JSON with title, description, tags and beats. Use 6-8 beats, each 8-18 spoken words, 35-50 seconds total. First beat hooks; last pays off. footage_query must be 2-5 concrete visual words. Title under 90 chars and ends #Shorts. Do not invent uncertain facts."""
OUTLINE_PROMPT="""Create a detailed outline for a 25-35 minute history documentary. Output ONLY JSON with title, description, tags, thumbnail_text, and chapters. Use 7-9 chapters. Each chapter has a title and 5-9 concrete beat topics. Build chronology, causes, people, turning points, consequences, controversy/debate where relevant, lesser-known facts and legacy. Every chapter must be useful for a visual documentary."""
CHAPTER_PROMPT="""Write one chapter of a cinematic, historically accurate history documentary. Output ONLY JSON with beats. Create 6-8 beats, each about 70-95 spoken words. Every beat must have line and a concrete 2-5 word footage_query closely matching the narration. Do not use unrelated historical objects. Prefer recognizable people, places, events, documents, machines, buildings, maps, crowds or landscapes directly connected to the topic. Distinguish disputed claims. Maintain chronology and avoid repeating the same visual query."""
def _extract_json(text:str)->str:
    text=text.strip();m=re.search(r"```(?:json)?\s*(\{.*\})\s*```",text,re.DOTALL);return m.group(1) if m else text
def _request(api_key:str,system:str,user:str,max_tokens:int,retries:int=3):
    last=None
    for attempt in range(retries):
        try:
            r=requests.post(GROQ_API_URL,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},json={"model":GROQ_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}],"temperature":0.45,"max_tokens":max_tokens,"response_format":{"type":"json_object"}},timeout=180);last=r
            if r.ok:return r
            if r.status_code in (400,413,429,500,502,503,504):
                wait=min(90,10*(2**attempt));log.warning("Groq request rejected (%s), retrying in %ss",r.status_code,wait);time.sleep(wait);continue
            r.raise_for_status()
        except requests.RequestException as exc:
            last=exc;wait=min(90,10*(2**attempt));log.warning("Groq request error: %s; retrying in %ss",exc,wait);time.sleep(wait)
    if isinstance(last,requests.Response):return last
    raise RuntimeError(f"Groq unavailable after {retries} attempts: {last}")
def _json_response(resp):
    if not resp.ok:
        if resp.status_code==429:raise RuntimeError("Groq rate limit persisted")
        raise RuntimeError(f"Groq request failed with HTTP {resp.status_code}")
    content=resp.json().get("choices",[{}])[0].get("message",{}).get("content")
    if not content:raise ValueError("Groq returned no message content")
    return json.loads(_extract_json(content))
def _normalize_beats(beats:list)->list:
    normalized=[]
    for beat in beats:
        if not isinstance(beat,dict):raise ValueError(f"Invalid beat: {beat}")
        line=beat.get("line") or beat.get("text") or beat.get("narration") or beat.get("script");query=beat.get("footage_query") or beat.get("visual_query") or beat.get("visual")
        if not line or not query:raise ValueError(f"Invalid beat: {beat}")
        b=dict(beat);b["line"]=str(line).strip();b["footage_query"]=str(query).strip();normalized.append(b)
    return normalized
def _build_short(topic:str,api_key:str)->dict:
    data=_json_response(_request(api_key,SHORT_PROMPT,f"Topic: {topic}",2500,2));data["beats"]=_normalize_beats(data.get("beats",[]));return data
def _build_long(topic:str,api_key:str)->dict:
    outline=_json_response(_request(api_key,OUTLINE_PROMPT,f"Topic: {topic}",1800,3));chapters=outline.get("chapters")
    if not isinstance(chapters,list) or len(chapters)<5:raise ValueError("Long-form outline has too few chapters")
    all_beats=[]
    for i,ch in enumerate(chapters):
        title=ch.get("title",f"Chapter {i+1}");topics=ch.get("beat_topics") or ch.get("topics") or []
        user=f"Documentary topic: {topic}\nChapter {i+1}: {title}\nChapter topics: {json.dumps(topics)}\nWrite only this chapter's beats."
        data=_json_response(_request(api_key,CHAPTER_PROMPT,user,1500,3));beats=_normalize_beats(data.get("beats",[]));all_beats.extend(beats);log.info("Generated chapter %d/%d: %s (%d beats)",i+1,len(chapters),title,len(beats))
    if len(all_beats)<45:raise ValueError(f"Long-form generation produced only {len(all_beats)} beats")
    outline["beats"]=all_beats;outline.setdefault("title",topic.title());outline.setdefault("description",f"A detailed history documentary about {topic}.");outline.setdefault("tags",["history","documentary",topic]);outline.setdefault("thumbnail_text","THE UNTOLD STORY");return outline
def generate_script(topic:str,long_form:bool=False)->dict:
    api_key=os.environ["GROQ_API_KEY"]
    data=_build_long(topic,api_key) if long_form else _build_short(topic,api_key)
    log.info("Generated %s: %d beats, title='%s'",'long-form' if long_form else 'Short',len(data['beats']),data.get('title',topic));return data

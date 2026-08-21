"""Generate accurate history scripts and YouTube metadata."""
from __future__ import annotations
import json, os, re, time, requests
from pathlib import Path
from utils import log
GROQ_API_URL="https://api.groq.com/openai/v1/chat/completions"; GROQ_MODEL=os.environ.get("GROQ_MODEL","openai/gpt-oss-120b")
SHORT_PROMPT="""Write an accurate, engaging history YouTube Short. Output ONLY JSON with title, description, tags and beats. Use 6-8 beats, each 8-18 spoken words, 35-50 seconds total. First beat hooks; last pays off. footage_query must be 2-5 concrete visual words closely tied to the narration. Do not invent uncertain facts."""
OUTLINE_PROMPT="""Create a detailed outline for a 25-35 minute history documentary. Output ONLY JSON with title, description, tags, thumbnail_text, and chapters. Use exactly 9 chapters. Each chapter has a title and exactly 6 concrete beat topics. Build chronology, causes, people, turning points, consequences, controversy/debate where relevant, lesser-known facts and legacy. The finished narration must target roughly 3,700-4,300 spoken words so it can naturally run 25-35 minutes. Every chapter must be useful for a visual documentary."""
CHAPTER_PROMPT="""Write one chapter of a cinematic, historically accurate history documentary. Output ONLY JSON with beats. Create exactly 6 beats, each about 70-80 spoken words. Every beat must have line and a concrete 2-5 word footage_query closely matching the narration. Prefer recognizable people, places, events, documents, machines, buildings, maps, crowds or landscapes directly connected to the topic. Do not use generic unrelated historical objects. Distinguish disputed claims. Maintain chronology and avoid repeating the same visual query."""
def _checkpoint_dir()->Path:
    p=Path(os.environ.get("LONGFORM_CHECKPOINT_DIR","config/.longform_checkpoints"));p.mkdir(parents=True,exist_ok=True);return p
def _ckpt_path(topic):return _checkpoint_dir()/(re.sub(r"[^a-z0-9]+","_",topic.lower()).strip("_")+".json")
def _extract_json(text:str)->str:
    text=text.strip();m=re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```",text,re.DOTALL);return m.group(1) if m else text
def _request(api_key:str,system:str,user:str,max_tokens:int,retries:int=2):
    last=None
    for attempt in range(retries):
        try:
            r=requests.post(GROQ_API_URL,headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"},json={"model":GROQ_MODEL,"messages":[{"role":"system","content":system},{"role":"user","content":user}],"temperature":0.3,"max_tokens":max_tokens,"response_format":{"type":"json_object"}},timeout=180);last=r
            if r.ok:return r
            if r.status_code in (429,500,502,503,504):
                wait=min(120,12*(attempt+1));log.warning("Groq transient error %s; waiting %ss",r.status_code,wait);time.sleep(wait);continue
            if r.status_code in (400,413):return r
            r.raise_for_status()
        except requests.RequestException as exc:
            last=exc;time.sleep(min(60,10*(attempt+1)))
    if isinstance(last,requests.Response):return last
    raise RuntimeError(f"Groq unavailable: {last}")
def _json_response(resp):
    if not resp.ok:raise RuntimeError(f"Groq request failed HTTP {resp.status_code}: {resp.text[:400]}")
    content=resp.json().get("choices",[{}])[0].get("message",{}).get("content")
    if not content:raise ValueError("Groq returned no message content")
    return json.loads(_extract_json(content))
def _normalize_beats(beats)->list:
    if isinstance(beats,dict):beats=beats.get("beats") or beats.get("items") or []
    if not isinstance(beats,list):raise ValueError("Beats response is not a list")
    out=[]
    for beat in beats:
        if not isinstance(beat,dict):raise ValueError("Invalid beat object")
        line=beat.get("line") or beat.get("text") or beat.get("narration") or beat.get("script");query=beat.get("footage_query") or beat.get("visual_query") or beat.get("visual")
        if not line or not query:raise ValueError("Beat missing narration or footage_query")
        b=dict(beat);b["line"]=str(line).strip();b["footage_query"]=str(query).strip();out.append(b)
    return out
def _build_short(topic,api_key):
    raw=_json_response(_request(api_key,SHORT_PROMPT,f"Topic: {topic}",1800,2));data=raw if isinstance(raw,dict) else {"beats":raw};data["beats"]=_normalize_beats(data.get("beats",[]));return data
def _build_long(topic,api_key):
    cp=_ckpt_path(topic)
    if cp.exists():
        try:checkpoint=json.loads(cp.read_text(encoding="utf-8"));outline=checkpoint["outline"];completed=checkpoint.get("completed",[]);log.info("Resuming long-form '%s' at chapter %d",topic,len(completed)+1)
        except Exception:checkpoint={};completed=[];outline=None
    else:checkpoint={};completed=[];outline=None
    if outline is None:
        outline=_json_response(_request(api_key,OUTLINE_PROMPT,f"Topic: {topic}",1800,2))
        if not isinstance(outline,dict):raise ValueError("Long-form outline must be an object")
        chapters=outline.get("chapters")
        if not isinstance(chapters,list) or len(chapters)!=9:raise ValueError("Long-form outline must contain exactly 9 chapters")
        checkpoint={"topic":topic,"outline":outline,"completed":[]};cp.write_text(json.dumps(checkpoint,indent=2),encoding="utf-8");completed=[]
    chapters=outline["chapters"];all_beats=[]
    for i,ch in enumerate(chapters):
        if i<len(completed):all_beats.extend(completed[i]);continue
        if not isinstance(ch,dict):raise ValueError("Invalid chapter outline")
        title=ch.get("title",f"Chapter {i+1}");topics=ch.get("beat_topics") or ch.get("topics") or []
        user=f"Documentary topic: {topic}\nChapter {i+1}: {title}\nChapter topics: {json.dumps(topics)}\nReturn ONLY a JSON object with exactly 6 beats."
        raw=_json_response(_request(api_key,CHAPTER_PROMPT,user,1000,2));beats=_normalize_beats(raw.get("beats",[]) if isinstance(raw,dict) else raw)
        if len(beats)!=6:raise ValueError(f"Chapter {i+1} produced {len(beats)} beats instead of 6")
        completed.append(beats);checkpoint["completed"]=completed;cp.write_text(json.dumps(checkpoint,indent=2),encoding="utf-8");all_beats.extend(beats);log.info("Generated chapter %d/%d: %s (%d beats)",i+1,len(chapters),title,len(beats));time.sleep(3)
    word_count=sum(len(str(b.get("line","")).split()) for b in all_beats)
    if len(all_beats)!=54 or not 3300<=word_count<=4800:raise ValueError(f"Long-form generation word-count QC failed: {word_count} words")
    outline["beats"]=all_beats;outline.setdefault("title",topic.title());outline.setdefault("description",f"A detailed history documentary about {topic}.");outline.setdefault("tags",["history","documentary",topic]);outline.setdefault("thumbnail_text","THE UNTOLD STORY");return outline
def generate_script(topic:str,long_form:bool=False)->dict:
    api_key=os.environ["GROQ_API_KEY"];data=_build_long(topic,api_key) if long_form else _build_short(topic,api_key);log.info("Generated %s: %d beats, title='%s'",'long-form' if long_form else 'Short',len(data['beats']),data.get('title',topic));return data

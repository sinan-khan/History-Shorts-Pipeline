"""Search/download reusable archival footage with resilient caching and strict relevance."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
import hashlib, json, time, requests
from utils import log
SEARCH_URL="https://archive.org/advancedsearch.php"; METADATA_URL="https://archive.org/metadata/{identifier}"; DOWNLOAD_URL="https://archive.org/download/{identifier}/{filename}"
SAFE_COLLECTIONS={"usgovernmentfilms","NASAarchive","nasa","prelinger","universal_newsreels"}; PREFERRED_EXTENSIONS=(".mp4",".m4v",".mov"); MAX_VIDEO_BYTES=500*1024*1024; MAX_DOWNLOAD_RETRIES=3; MAX_CANDIDATES=10; SEARCH_TTL=7*24*3600
BAD_TITLE_TERMS={"hoax","fake","conspiracy","jolly heretic","genetic interests","flat earth"}; STOPWORDS={"the","a","an","of","and","to","in","on","for","with","from","about","history","historical","story","video","film","documentary"}

def _cache_dir(out_dir:Path|None=None)->Path:
    root=out_dir or Path(".cache"); root.mkdir(parents=True,exist_ok=True); return root

def _key(q:str)->str:return hashlib.sha256(q.strip().lower().encode()).hexdigest()[:20]

def search_archive(query:str,rows:int=20,cache_dir:Path|None=None)->list[dict]:
    cache=_cache_dir(cache_dir)/f"search_{_key(query)}.json"
    if cache.exists() and time.time()-cache.stat().st_mtime<SEARCH_TTL:
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:pass
    params={"q":f'({query}) AND mediatype:(movies)',"fl[]":["identifier","title","description","licenseurl","collection"],"rows":rows,"output":"json"}; last=None
    for attempt in range(1,MAX_DOWNLOAD_RETRIES+1):
        try:
            r=requests.get(SEARCH_URL,params=params,timeout=(10,30));r.raise_for_status();docs=r.json().get("response",{}).get("docs",[]);cache.write_text(json.dumps(docs),encoding="utf-8");return docs
        except requests.RequestException as exc:
            last=exc;log.warning("Archive search %d/%d failed for '%s': %s",attempt,MAX_DOWNLOAD_RETRIES,query,exc)
            if attempt<MAX_DOWNLOAD_RETRIES:time.sleep(2**(attempt-1)*2)
    # A cached stale result is better than hammering an unavailable service.
    if cache.exists():
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:pass
    return []

def _is_public_domain(doc:dict)->bool:
    if not doc.get("identifier"):return False
    lic=(doc.get("licenseurl") or "").lower(); col=doc.get("collection",[]); col=[col] if isinstance(col,str) else col
    return "publicdomain" in lic or "creativecommons.org/publicdomain" in lic or "cc0" in lic or bool(SAFE_COLLECTIONS.intersection(col))

def _terms(text:str)->set[str]:
    import re
    return {w.lower() for w in re.findall(r"[a-z0-9]+",str(text)) if len(w)>2 and w.lower() not in STOPWORDS}

def _relevance(query:str,doc:dict)->int:
    q=_terms(query); title=_terms(doc.get("title","") ); desc=_terms(doc.get("description","") ); score=0
    for term in q: score += 8 if term in title else (2 if term in desc else 0)
    low=(str(doc.get("title",""))+" "+str(doc.get("description",""))).lower()
    if any(x in low for x in BAD_TITLE_TERMS):score-=50
    ql=query.lower()
    topic_groups=[(("apollo","moon","nasa","mercury","gemini","saturn","lunar"),("apollo","nasa","moon","lunar","mercury","gemini","saturn")),(("eiffel","tower","paris","1889"),("eiffel","tower","paris"))]
    for terms,boosts in topic_groups:
        if any(x in ql for x in terms) and any(x in low for x in boosts):score+=15
    return score

def _query_variants(query:str)->list[str]:
    words=[w for w in query.replace(","," ").split() if w];variants=[query]
    if len(words)>4:variants.append(" ".join(words[:4]))
    if len(words)>2:variants.append(" ".join(words[-3:]))
    q=query.lower()
    if "construction" in q and "tower" in q:variants += ["Eiffel Tower construction","Eiffel Tower workers","Paris 1889 Eiffel"]
    if any(x in q for x in ("apollo","moon","lunar")):variants += ["Apollo 11","Apollo astronauts","NASA Apollo","moon landing","Saturn V","Apollo mission"]
    return list(dict.fromkeys(variants))

def _video_candidates(identifier:str)->list[tuple[str,int]]:
    r=requests.get(METADATA_URL.format(identifier=identifier),timeout=(10,30));r.raise_for_status();out=[]
    for f in r.json().get("files",[]):
        name=f.get("name","")
        if not name.lower().endswith(PREFERRED_EXTENSIONS) or f.get("private"):continue
        try:size=int(f.get("size",0) or 0)
        except (TypeError,ValueError):size=0
        if 0<size<=MAX_VIDEO_BYTES:out.append((name,size))
    # Avoid microscopic proxy files where a better file exists; prefer the smallest
    # file above 5MB, otherwise the smallest valid file.
    usable=[x for x in out if x[1]>=5*1024*1024] or out
    return sorted(usable,key=lambda x:x[1])

def _candidate_items(query:str,fallback_query:str|None=None,cache_dir:Path|None=None)->list[tuple[dict,str,int]]:
    queries=_query_variants(query)+(_query_variants(fallback_query) if fallback_query else []);seen_q=set();seen_ids=set();candidates=[]
    for q in queries:
        if q in seen_q:continue
        seen_q.add(q)
        for doc in search_archive(q,cache_dir=cache_dir):
            if not _is_public_domain(doc):continue
            ident=doc["identifier"]
            if ident in seen_ids:continue
            relevance=_relevance(query,doc)
            if relevance<8:continue
            try:files=_video_candidates(ident)
            except requests.RequestException as exc:log.warning("Archive metadata failed for %s: %s",ident,exc);continue
            if not files:continue
            filename,size=files[0];url=DOWNLOAD_URL.format(identifier=ident,filename=quote(filename,safe="/"));seen_ids.add(ident);candidates.append((doc,url,size,relevance))
    candidates.sort(key=lambda x:(-x[3],x[2]));return [(d,u,s) for d,u,s,_ in candidates[:MAX_CANDIDATES]]

def download_file(url:str,dest:Path)->Path:
    log.info("Downloading %s",url);tmp=dest.with_suffix(dest.suffix+".part")
    with requests.get(url,stream=True,timeout=(20,120)) as r:
        r.raise_for_status();length=int(r.headers.get("Content-Length",0) or 0)
        if length>MAX_VIDEO_BYTES:raise requests.RequestException(f"Remote file exceeds 500MB ({length} bytes)")
        total=0
        with open(tmp,"wb") as f:
            for chunk in r.iter_content(chunk_size=1<<20):
                if chunk:
                    total+=len(chunk)
                    if total>MAX_VIDEO_BYTES:raise requests.RequestException("Download exceeded 500MB limit")
                    f.write(chunk)
    if total==0:raise requests.RequestException("Archive.org returned an empty file")
    tmp.replace(dest);return dest

def fetch_clip_for_beat(query:str,fallback_query:str,out_dir:Path,index:int)->Path|None:
    candidates=_candidate_items(query,fallback_query,out_dir/".cache");dest=out_dir/f"raw_{index}.mp4"
    for attempt,(doc,url,size) in enumerate(candidates[:MAX_DOWNLOAD_RETRIES],1):
        try:return download_file(url,dest)
        except requests.RequestException as exc:
            log.warning("Download candidate %d failed (%s): %s",attempt,doc.get("identifier"),exc)
            if dest.exists():dest.unlink()
    return None

def probe_video_duration(path:Path)->float:
    from utils import get_duration
    return get_duration(path)

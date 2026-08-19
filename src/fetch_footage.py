"""Find relevant archival video first, then licensed historical stills as fallback."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
import hashlib,json,time,requests
from utils import log,run
from content_sources import search_all

SEARCH_URL="https://archive.org/advancedsearch.php"; METADATA_URL="https://archive.org/metadata/{identifier}"; DOWNLOAD_URL="https://archive.org/download/{identifier}/{filename}"
SAFE_COLLECTIONS={"usgovernmentfilms","NASAarchive","nasa","prelinger","universal_newsreels"}; PREFERRED_EXTENSIONS=(".mp4",".m4v",".mov"); MAX_VIDEO_BYTES=500*1024*1024; MAX_DOWNLOAD_RETRIES=3; MAX_CANDIDATES=10; SEARCH_TTL=7*24*3600
BAD_TITLE_TERMS={"hoax","fake","conspiracy","jolly heretic","genetic interests","flat earth","home movie"}; STOPWORDS={"the","a","an","of","and","to","in","on","for","with","from","about","history","historical","story","video","film","documentary"}

def _cache_dir(out_dir:Path|None=None)->Path:
    root=out_dir or Path(".cache");root.mkdir(parents=True,exist_ok=True);return root

def _key(q:str)->str:return hashlib.sha256(q.strip().lower().encode()).hexdigest()[:20]

def search_archive(query:str,rows:int=20,cache_dir:Path|None=None)->list[dict]:
    cache=_cache_dir(cache_dir)/f"search_{_key(query)}.json"
    if cache.exists() and time.time()-cache.stat().st_mtime<SEARCH_TTL:
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:pass
    params={"q":f'({query}) AND mediatype:(movies)',"fl[]":["identifier","title","description","licenseurl","collection"],"rows":rows,"output":"json"}
    for attempt in range(1,MAX_DOWNLOAD_RETRIES+1):
        try:
            r=requests.get(SEARCH_URL,params=params,timeout=(10,30));r.raise_for_status();docs=r.json().get("response",{}).get("docs",[]);cache.write_text(json.dumps(docs),encoding="utf-8");return docs
        except requests.RequestException as exc:
            log.warning("Archive search %d/%d failed for '%s': %s",attempt,MAX_DOWNLOAD_RETRIES,query,exc)
            if attempt<MAX_DOWNLOAD_RETRIES:time.sleep(2**(attempt-1)*2)
    if cache.exists():
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:pass
    return []

def _is_public_domain(doc:dict)->bool:
    lic=(doc.get("licenseurl") or "").lower();col=doc.get("collection",[]);col=[col] if isinstance(col,str) else col
    return bool(doc.get("identifier")) and ("publicdomain" in lic or "creativecommons.org/publicdomain" in lic or "cc0" in lic or bool(SAFE_COLLECTIONS.intersection(col)))

def _terms(text:str)->set[str]:
    import re
    return {w.lower() for w in re.findall(r"[a-z0-9]+",str(text)) if len(w)>2 and w.lower() not in STOPWORDS}

def _relevance(query:str,doc:dict)->int:
    q=_terms(query);title=_terms(doc.get("title",""));desc=_terms(doc.get("description",""));score=sum(8 if x in title else 2 if x in desc else 0 for x in q);low=(str(doc.get("title",""))+" "+str(doc.get("description",""))).lower()
    if any(x in low for x in BAD_TITLE_TERMS):score-=50
    ql=query.lower()
    if any(x in ql for x in ("apollo","moon","nasa","mercury","gemini","saturn","lunar")) and any(x in low for x in ("apollo","nasa","moon","lunar","mercury","gemini","saturn")):score+=15
    if any(x in ql for x in ("eiffel","tower","paris","1889")) and any(x in low for x in ("eiffel","tower","paris")):score+=15
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
    return sorted([x for x in out if x[1]>=5*1024*1024] or out,key=lambda x:x[1])

def _candidate_items(query:str,fallback_query:str|None=None,cache_dir:Path|None=None):
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

def _download(url:str,dest:Path)->Path:
    tmp=dest.with_suffix(dest.suffix+".part")
    with requests.get(url,stream=True,timeout=(20,120)) as r:
        r.raise_for_status();length=int(r.headers.get("Content-Length",0) or 0)
        if length>MAX_VIDEO_BYTES:raise requests.RequestException("Remote file exceeds 500MB")
        total=0
        with open(tmp,"wb") as f:
            for chunk in r.iter_content(1<<20):
                if chunk:
                    total+=len(chunk)
                    if total>MAX_VIDEO_BYTES:raise requests.RequestException("Download exceeded 500MB")
                    f.write(chunk)
    if total==0:raise requests.RequestException("Empty download")
    tmp.replace(dest);return dest

def _image_to_video(url:str,dest:Path,duration:float,index:int)->Path:
    image=dest.with_suffix(".jpg")
    with requests.get(url,stream=True,timeout=(15,60),headers={"User-Agent":"History-Shorts-Pipeline/1.0"}) as r:
        r.raise_for_status();data=r.content
    if len(data)>25*1024*1024:raise requests.RequestException("Historical image exceeds 25MB")
    image.write_bytes(data)
    # Cinematic still treatment: slow alternating push/pan, landscape or vertical aware.
    frames=max(30,int(duration*30));zoom="min(zoom+0.00035,1.14)" if index%2==0 else "max(zoom-0.00025,1.0)";x="(iw-iw/zoom)/2+20*sin(on/18)";y="(ih-ih/zoom)/2+12*sin(on/23)"
    vf=f"scale=2200:2200:force_original_aspect_ratio=increase,crop=2200:1240,zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps=30,setsar=1,format=yuv420p"
    run(["ffmpeg","-y","-loop","1","-i",str(image),"-t",str(duration),"-vf",vf,"-an","-c:v","libx264","-crf","19","-preset","veryfast","-pix_fmt","yuv420p",str(dest)])
    return dest

def fetch_clip_for_beat(query:str,fallback_query:str,out_dir:Path,index:int,duration:float|None=None,used_sources:set[str]|None=None)->Path|None:
    used_sources=used_sources if used_sources is not None else set();cache=out_dir/".cache";out_dir.mkdir(parents=True,exist_ok=True)
    candidates=[(d,u,s) for d,u,s in _candidate_items(query,fallback_query,cache) if d.get("identifier") not in used_sources]
    for attempt,(doc,url,size) in enumerate(candidates[:MAX_DOWNLOAD_RETRIES],1):
        try:
            p=_download(url,out_dir/f"raw_{index}.mp4");used_sources.add(doc["identifier"]);log.info("Using Archive footage %s for beat %d",doc.get("title"),index);return p
        except requests.RequestException as exc:log.warning("Archive candidate %d failed: %s",attempt,exc)
    # Multi-source fallback: real historical stills, never random stock footage.
    for item in search_all(query,cache):
        key=f"{item.get('source')}:{item.get('url')}"
        if key in used_sources or item.get("score",0)<8:continue
        try:
            p=_image_to_video(item["url"],out_dir/f"raw_{index}.mp4",duration or 5.0,index);used_sources.add(key);log.info("Using %s historical still for beat %d: %s",item["source"],index,item.get("title"));return p
        except (requests.RequestException,RuntimeError,OSError) as exc:log.warning("Still fallback failed: %s",exc)
    return None

def probe_video_duration(path:Path)->float:
    from utils import get_duration
    return get_duration(path)

"""Find relevant archival video first, then rights-cleared multi-source visuals."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
import hashlib,json,time,requests,os
from utils import log,run
from content_sources import search_all
SEARCH_URL="https://archive.org/advancedsearch.php"; METADATA_URL="https://archive.org/metadata/{identifier}"; DOWNLOAD_URL="https://archive.org/download/{identifier}/{filename}"
SAFE_COLLECTIONS={"usgovernmentfilms","NASAarchive","nasa","prelinger","universal_newsreels"}; PREFERRED_EXTENSIONS=(".mp4",".m4v",".mov"); MAX_VIDEO_BYTES=500*1024*1024; MAX_RETRIES=2; MAX_CANDIDATES=10; SEARCH_TTL=7*24*3600
BAD_TITLE_TERMS={"hoax","fake","conspiracy","jolly heretic","genetic interests","flat earth","home movie"}; STOPWORDS={"the","a","an","of","and","to","in","on","for","with","from","about","history","historical","story","video","film","documentary"}

def _cache_dir(out_dir=None):root=out_dir or Path(".cache");root.mkdir(parents=True,exist_ok=True);return root
def _key(q):return hashlib.sha256(q.strip().lower().encode()).hexdigest()[:20]
def search_archive(query,rows=20,cache_dir=None):
    cache=_cache_dir(cache_dir)/f"search_{_key(query)}.json"
    if cache.exists() and time.time()-cache.stat().st_mtime<SEARCH_TTL:
        try:return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:pass
    params={"q":f'({query}) AND mediatype:(movies)',"fl[]":["identifier","title","description","licenseurl","collection"],"rows":rows,"output":"json"}
    for attempt in range(1,MAX_RETRIES+1):
        try:
            r=requests.get(SEARCH_URL,params=params,timeout=(8,20));r.raise_for_status();docs=r.json().get("response",{}).get("docs",[]);cache.write_text(json.dumps(docs),encoding="utf-8");return docs
        except requests.RequestException as exc:
            log.warning("Archive search %d/%d failed for '%s': %s",attempt,MAX_RETRIES,query,exc)
            if attempt<MAX_RETRIES:time.sleep(2)
    return []

def _is_public_domain(doc):
    lic=(doc.get("licenseurl") or "").lower();col=doc.get("collection",[]);col=[col] if isinstance(col,str) else col
    return bool(doc.get("identifier")) and ("publicdomain" in lic or "creativecommons.org/publicdomain" in lic or "cc0" in lic or bool(SAFE_COLLECTIONS.intersection(col)))
def _terms(text):
    import re
    return {w.lower() for w in re.findall(r"[a-z0-9]+",str(text)) if len(w)>2 and w.lower() not in STOPWORDS}
def _relevance(query,doc):
    q=_terms(query);title=_terms(doc.get("title",""));desc=_terms(doc.get("description",""));score=sum(10 if x in title else 3 if x in desc else 0 for x in q);low=(str(doc.get("title",""))+" "+str(doc.get("description",""))).lower()
    if any(x in low for x in BAD_TITLE_TERMS):score-=50
    return score

def _query_variants(query):
    words=[w for w in query.replace(","," ").split() if w];variants=[query]
    if len(words)>4:variants.append(" ".join(words[:4]))
    if len(words)>2:variants.append(" ".join(words[-3:]))
    if len(words)>1:variants.append(" ".join(words[:2]))
    q=query.lower()
    if "construction" in q and "tower" in q:variants += ["Eiffel Tower construction","Eiffel Tower workers","Paris 1889 Eiffel"]
    if any(x in q for x in ("apollo","moon","lunar")):variants += ["Apollo 11","Apollo astronauts","NASA Apollo","moon landing","Saturn V"]
    if "berlin" in q and "wall" in q:variants += ["Berlin Wall","Berlin Wall 1989","Checkpoint Charlie"]
    if "titanic" in q:variants += ["Titanic","Titanic ship","Titanic passengers"]
    return list(dict.fromkeys(variants))

def _video_candidates(identifier):
    r=requests.get(METADATA_URL.format(identifier=identifier),timeout=(8,20));r.raise_for_status();out=[]
    for f in r.json().get("files",[]):
        name=f.get("name","")
        if not name.lower().endswith(PREFERRED_EXTENSIONS) or f.get("private"):continue
        try:size=int(f.get("size",0) or 0)
        except (TypeError,ValueError):size=0
        if 0<size<=MAX_VIDEO_BYTES:out.append((name,size))
    return sorted([x for x in out if x[1]>=5*1024*1024] or out,key=lambda x:x[1])

def _candidate_items(query,fallback_query=None,cache_dir=None):
    candidates=[];seen=set()
    for q in _query_variants(query)+(_query_variants(fallback_query) if fallback_query else []):
        for doc in search_archive(q,cache_dir=cache_dir):
            ident=doc.get("identifier")
            if not ident or ident in seen or not _is_public_domain(doc):continue
            if _relevance(query,doc)<10 and _relevance(fallback_query or query,doc)<10:continue
            try:files=_video_candidates(ident)
            except requests.RequestException as exc:log.warning("Archive metadata failed for %s: %s",ident,exc);continue
            if files:
                filename,size=files[0];seen.add(ident);candidates.append((doc,DOWNLOAD_URL.format(identifier=ident,filename=quote(filename,safe="/")),size))
    return candidates[:MAX_CANDIDATES]

def _download(url,dest,max_bytes=MAX_VIDEO_BYTES,timeout=(15,90)):
    tmp=dest.with_suffix(dest.suffix+".part")
    try:
        with requests.get(url,stream=True,timeout=timeout,headers={"User-Agent":"History-Shorts-Pipeline/1.0"}) as r:
            r.raise_for_status();length=int(r.headers.get("Content-Length",0) or 0)
            if length>max_bytes:raise requests.RequestException(f"Remote file exceeds {max_bytes//(1024*1024)}MB")
            total=0
            with open(tmp,"wb") as f:
                for chunk in r.iter_content(1<<20):
                    if chunk:
                        total+=len(chunk)
                        if total>max_bytes:raise requests.RequestException("Download exceeded size limit")
                        f.write(chunk)
        if total==0:raise requests.RequestException("Empty download")
        tmp.replace(dest);return dest
    finally:
        if tmp.exists():
            try:tmp.unlink()
            except OSError:pass

def _image_to_video(url,dest,duration,index):
    image=dest.with_suffix(".jpg");r=requests.get(url,stream=True,timeout=(15,45),headers={"User-Agent":"History-Shorts-Pipeline/1.0"});r.raise_for_status();data=r.content
    if len(data)>25*1024*1024:raise requests.RequestException("Historical image exceeds 25MB")
    image.write_bytes(data);frames=max(30,int(duration*30));zoom="min(zoom+0.00035,1.14)" if index%2==0 else "max(zoom-0.00025,1.0)";x="(iw-iw/zoom)/2+20*sin(on/18)";y="(ih-ih/zoom)/2+12*sin(on/23)";vf=f"scale=2200:2200:force_original_aspect_ratio=increase,crop=2200:1240,zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps=30,setsar=1,format=yuv420p";run(["ffmpeg","-y","-loop","1","-i",str(image),"-t",str(duration),"-vf",vf,"-an","-c:v","libx264","-crf","19","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]);return dest

def _record_source(used_sources,source):
    if isinstance(used_sources,dict):used_sources[source]=used_sources.get(source,0)+1
    elif isinstance(used_sources,set):used_sources.add(source)

def _source_used(used_sources,key):
    return key in used_sources if isinstance(used_sources,set) else False

def fetch_clip_for_beat(query,fallback_query,out_dir,index,duration=None,used_sources=None,disabled_sources=None):
    used_sources=used_sources if used_sources is not None else {};disabled_sources=disabled_sources if disabled_sources is not None else set();cache=out_dir/".cache";out_dir.mkdir(parents=True,exist_ok=True)
    archive_failures=0
    for doc,url,size in _candidate_items(query,fallback_query,cache)[:MAX_CANDIDATES]:
        ident=doc.get("identifier")
        if _source_used(used_sources,ident):continue
        try:p=_download(url,out_dir/f"raw_{index}.mp4");_record_source(used_sources,"archive");return p
        except requests.RequestException as exc:
            archive_failures+=1;log.warning("Archive candidate failed (%d/2): %s",archive_failures,exc)
            if archive_failures>=2:disabled_sources.add("archive");log.warning("Archive circuit breaker tripped for this run");break
    items=[]
    api={"europeana_key":os.getenv("EUROPEANA_API_KEY"),"nasa_key":os.getenv("NASA_API_KEY"),"pexels_key":os.getenv("PEXELS_API_KEY"),"pixabay_key":os.getenv("PIXABAY_API_KEY")}
    seen=set()
    for q in _query_variants(query)+_query_variants(fallback_query or ""):
        if not q:continue
        for item in search_all(q,cache,**api,used_sources=used_sources if isinstance(used_sources,dict) else {}):
            source=item.get("source","unknown");key=f"{source}:{item.get('url')}"
            if source in disabled_sources or key in seen or _source_used(used_sources,key):continue
            seen.add(key);items.append(item)
    for item in items:
        source=item.get("source","unknown");url=item.get("url")
        try:
            if item.get("kind")=="video":
                p=_download(url,out_dir/f"raw_{index}.mp4",max_bytes=MAX_VIDEO_BYTES,timeout=(15,90));_record_source(used_sources,source);log.info("Selected %s video: %s",source,item.get("title"));return p
            p=_image_to_video(url,out_dir/f"raw_{index}.mp4",duration or 5.0,index);_record_source(used_sources,source);log.info("Selected %s still: %s",source,item.get("title"));return p
        except requests.HTTPError as exc:
            if getattr(exc.response,"status_code",None)==429:disabled_sources.add(source);log.warning("%s rate-limited; disabling it",source)
        except (requests.RequestException,RuntimeError,OSError) as exc:log.warning("%s visual failed: %s",source,exc)
    return None

def probe_video_duration(path):
    from utils import get_duration
    return get_duration(path)

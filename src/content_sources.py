"""Balanced discovery for reusable historical video/still assets."""
from __future__ import annotations
import hashlib,json,time,re
from pathlib import Path
import requests
from utils import log
UA={"User-Agent":"History-Shorts-Pipeline/1.0 (+https://github.com/sinan-khan/History-Shorts-Pipeline)"};CACHE_TTL=7*24*3600
SOURCE_LIMITS={"loc":6,"wikimedia":4,"europeana":4}
SOURCE_ORDER=("loc","wikimedia","europeana")

def _cache(root:Path,q:str):
    root.mkdir(parents=True,exist_ok=True);return root/(hashlib.sha256(q.lower().encode()).hexdigest()[:20]+".json")
def _get_json(url:str,params:dict,cache_dir:Path):
    p=_cache(cache_dir,url+json.dumps(params,sort_keys=True))
    if p.exists() and time.time()-p.stat().st_mtime<CACHE_TTL:
        try:return json.loads(p.read_text())
        except Exception:pass
    try:
        r=requests.get(url,params=params,headers=UA,timeout=(10,30));r.raise_for_status();data=r.json();p.write_text(json.dumps(data),encoding="utf-8");return data
    except requests.RequestException as e:
        log.warning("Content source unavailable %s: %s",url,e)
        if p.exists():
            try:return json.loads(p.read_text())
            except Exception:pass
    return None

def _terms(text:str)->set[str]:return {x.lower() for x in re.findall(r"[a-z0-9]+",str(text)) if len(x)>2}
def _as_text(value)->str:
    if value is None:return ""
    if isinstance(value,list):return " ".join(_as_text(x) for x in value)
    if isinstance(value,dict):return " ".join(_as_text(v) for v in value.values())
    return str(value)
def _score(query:str,title:str,description:str="")->int:
    q=_terms(query);t=_terms(title);d=_terms(description);return sum(10 if x in t else 3 if x in d else 0 for x in q)
def _relevant(query:str,title:str,description:str="",minimum:int=10)->bool:
    q=_terms(query);t=_terms(title);d=_terms(description);score=_score(query,title,description);matches=q & (t|d)
    if score<minimum or not matches:return False
    if len(q)>=2 and not (q & t):return False
    return True

def search_wikimedia(query:str,cache_dir:Path)->list[dict]:
    data=_get_json("https://commons.wikimedia.org/w/api.php",{"action":"query","generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":12,"prop":"imageinfo|info","iiprop":"url|size|mime|extmetadata","iiurlwidth":1800,"format":"json","formatversion":2},cache_dir);out=[]
    for p in (data or {}).get("query",{}).get("pages",[]):
        info=(p.get("imageinfo") or [{}])[0];meta=info.get("extmetadata",{});license_name=_as_text(meta.get("LicenseShortName"));title=p.get("title","");desc=_as_text(meta.get("ImageDescription"));thumb=info.get("thumburl") or info.get("url")
        if thumb and ("public domain" in license_name.lower() or "cc0" in license_name.lower() or license_name.lower().startswith("cc")) and _relevant(query,title,desc):out.append({"source":"wikimedia","kind":"still","title":title,"url":thumb,"original_url":info.get("url"),"license":license_name,"score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:SOURCE_LIMITS["wikimedia"]]

def search_europeana(query:str,cache_dir:Path,api_key:str|None=None)->list[dict]:
    if not api_key:
        log.info("Europeana skipped: EUROPEANA_API_KEY is not configured")
        return []
    data=_get_json("https://api.europeana.eu/record/v2/search.json",{"wskey":api_key,"query":query,"rows":12,"profile":"rich"},cache_dir);out=[]
    for item in (data or {}).get("items",[]):
        rights=_as_text(item.get("rights"));link=item.get("edmIsShownBy") or item.get("edmPreview");title=_as_text(item.get("title"));desc=_as_text(item.get("dcDescription"))
        if link and any(x in rights.lower() for x in ("creativecommons.org","public domain","creativecommons")) and _relevant(query,title,desc):out.append({"source":"europeana","kind":"still","title":title,"url":link,"license":rights,"score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:SOURCE_LIMITS["europeana"]]

def search_loc(query:str,cache_dir:Path)->list[dict]:
    data=_get_json("https://www.loc.gov/search/",{"q":query,"fo":"json","c":12,"fa":"online-format:image|online-format:video"},cache_dir);out=[]
    for item in (data or {}).get("results",[]):
        rights=_as_text(item.get("rights"));description=_as_text(item.get("description"));rights_text=f"{rights} {description}".strip();images=item.get("image_url");url=images[0] if isinstance(images,list) and images else item.get("url");title=_as_text(item.get("title"))
        if url and any(x in rights_text.lower() for x in ("public domain","free to use","no known restrictions")) and _relevant(query,title,description):out.append({"source":"loc","kind":"still_or_video","title":title,"url":url,"license":rights_text,"score":_score(query,title,description)})
    return sorted(out,key=lambda x:-x["score"])[:SOURCE_LIMITS["loc"]]

def search_all(query:str,cache_dir:Path,europeana_key:str|None=None,used_sources:dict[str,int]|None=None)->list[dict]:
    """Search every configured source, then rank by relevance with a gentle diversity bonus.

    Relevance always wins: an unrelated asset is never selected merely for diversity.
    used_sources tracks assets already selected by the caller so repeated Wikimedia use
    is penalized when LOC/Europeana have comparable relevant candidates.
    """
    used_sources=used_sources if used_sources is not None else {}
    pools={}
    for name,fn in (("loc",search_loc),("wikimedia",search_wikimedia),("europeana",lambda q,c:search_europeana(q,c,europeana_key))):
        try:pools[name]=fn(query,cache_dir)
        except (requests.RequestException,ValueError,TypeError) as exc:log.warning("%s source failed for '%s': %s",name,query,exc);pools[name]=[]
    candidates=[]
    for source in SOURCE_ORDER:
        for item in pools.get(source,[]):
            # Diversity bonus is capped so it cannot overpower a materially better match.
            diversity_bonus=max(0,8-min(used_sources.get(source,0),8))
            item=dict(item);item["selection_score"]=item.get("score",0)+diversity_bonus;candidates.append(item)
    candidates.sort(key=lambda x:(-x["selection_score"],-x.get("score",0),SOURCE_ORDER.index(x["source"])))
    if candidates:
        log.info("Source candidates for '%s': %s",query,", ".join(f"{x['source']}:{x['score']}" for x in candidates[:8]))
    return candidates

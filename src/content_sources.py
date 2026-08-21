"""Balanced discovery for reusable historical video/still assets."""
from __future__ import annotations
import hashlib,json,time,re
from pathlib import Path
import requests
from utils import log
UA={"User-Agent":"History-Shorts-Pipeline/1.0 (+https://github.com/sinan-khan/History-Shorts-Pipeline)"};CACHE_TTL=7*24*3600
SOURCE_LIMITS={"loc":6,"wikimedia":4,"europeana":4,"nasa":4,"pexels":4,"pixabay":4}
SOURCE_ORDER=("nasa","loc","europeana","pexels","pixabay","wikimedia")

def _cache(root:Path,q:str):root.mkdir(parents=True,exist_ok=True);return root/(hashlib.sha256(q.lower().encode()).hexdigest()[:20]+".json")
def _get_json(url:str,params:dict,cache_dir:Path,headers=None,timeout=(8,15)):
    p=_cache(cache_dir,url+json.dumps(params,sort_keys=True))
    if p.exists() and time.time()-p.stat().st_mtime<CACHE_TTL:
        try:return json.loads(p.read_text())
        except Exception:pass
    try:
        r=requests.get(url,params=params,headers=headers or UA,timeout=timeout);r.raise_for_status();data=r.json();p.write_text(json.dumps(data),encoding="utf-8");return data
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

def search_wikimedia(query,cache_dir):
    data=_get_json("https://commons.wikimedia.org/w/api.php",{"action":"query","generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":12,"prop":"imageinfo|info","iiprop":"url|size|mime|extmetadata","iiurlwidth":1800,"format":"json","formatversion":2},cache_dir);out=[]
    for p in (data or {}).get("query",{}).get("pages",[]):
        info=(p.get("imageinfo") or [{}])[0];meta=info.get("extmetadata",{});lic=_as_text(meta.get("LicenseShortName"));title=p.get("title","");desc=_as_text(meta.get("ImageDescription"));url=info.get("thumburl") or info.get("url")
        if url and ("public domain" in lic.lower() or "cc0" in lic.lower() or lic.lower().startswith("cc")) and _relevant(query,title,desc):out.append({"source":"wikimedia","kind":"still","title":title,"url":url,"original_url":info.get("url"),"license":lic,"score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:4]

def search_europeana(query,cache_dir,api_key=None):
    if not api_key:log.info("Europeana skipped: EUROPEANA_API_KEY is not configured");return []
    data=_get_json("https://api.europeana.eu/record/v2/search.json",{"wskey":api_key,"query":query,"rows":12,"profile":"rich"},cache_dir);out=[]
    for item in (data or {}).get("items",[]):
        rights=_as_text(item.get("rights"));url=item.get("edmIsShownBy") or item.get("edmPreview");title=_as_text(item.get("title"));desc=_as_text(item.get("dcDescription"))
        if url and any(x in rights.lower() for x in ("creativecommons.org","public domain","creativecommons")) and _relevant(query,title,desc):out.append({"source":"europeana","kind":"still","title":title,"url":url,"license":rights,"score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:4]

def search_loc(query,cache_dir):
    data=_get_json("https://www.loc.gov/search/",{"q":query,"fo":"json","c":12,"fa":"online-format:image|online-format:video"},cache_dir);out=[]
    for item in (data or {}).get("results",[]):
        rights=_as_text(item.get("rights"));desc=_as_text(item.get("description"));rights_text=f"{rights} {desc}";imgs=item.get("image_url");url=imgs[0] if isinstance(imgs,list) and imgs else item.get("url");title=_as_text(item.get("title"))
        if url and any(x in rights_text.lower() for x in ("public domain","free to use","no known restrictions")) and _relevant(query,title,desc):out.append({"source":"loc","kind":"still_or_video","title":title,"url":url,"license":rights_text,"score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:6]

def search_nasa(query,cache_dir,api_key=None):
    data=_get_json("https://images-api.nasa.gov/search",{"q":query,"media_type":"image,video","page_size":12},cache_dir);out=[]
    for item in (data or {}).get("collection",{}).get("items",[]):
        d=(item.get("data") or [{}])[0];title=_as_text(d.get("title"));desc=_as_text(d.get("description"));href=item.get("href")
        if not href or not _relevant(query,title,desc,minimum=8):continue
        try:assets=requests.get(href,headers=UA,timeout=(8,15)).json()
        except (requests.RequestException,ValueError):continue
        urls=[u for u in assets if isinstance(u,str) and re.search(r"\.(jpg|jpeg|png|mp4|mov)(\?|$)",u,re.I)]
        if urls:out.append({"source":"nasa","kind":"video" if any(re.search(r"\.(mp4|mov)(\?|$)",u,re.I) for u in urls) else "still","title":title,"url":urls[0],"license":"NASA media","score":_score(query,title,desc)})
    return sorted(out,key=lambda x:-x["score"])[:4]

def search_pexels(query,cache_dir,api_key=None):
    if not api_key:log.info("Pexels skipped: PEXELS_API_KEY is not configured");return []
    data=_get_json("https://api.pexels.com/videos/search",{"query":query,"per_page":12,"orientation":"landscape"},cache_dir,headers={"Authorization":api_key,"User-Agent":UA["User-Agent"]});out=[]
    for v in (data or {}).get("videos",[]):
        files=sorted([f for f in (v.get("video_files") or []) if f.get("link") and f.get("width",0)>=1280],key=lambda f:f.get("width",0),reverse=True);title=_as_text(v.get("url") or query)
        if files:out.append({"source":"pexels","kind":"video","title":title,"url":files[0]["link"],"license":"Pexels license","score":_score(query,title,query)})
    return out[:4]

def search_pixabay(query,cache_dir,api_key=None):
    if not api_key:log.info("Pixabay skipped: PIXABAY_API_KEY is not configured");return []
    data=_get_json("https://pixabay.com/api/videos/",{"key":api_key,"q":query,"per_page":12,"safesearch":"true"},cache_dir);out=[]
    for v in (data or {}).get("hits",[]):
        title=_as_text(v.get("tags"));best=(v.get("videos") or {}).get("large") or (v.get("videos") or {}).get("medium") or (v.get("videos") or {}).get("small")
        if best and best.get("url"):out.append({"source":"pixabay","kind":"video","title":title,"url":best["url"],"license":"Pixabay Content License","score":_score(query,title,query)})
    return out[:4]

def search_all(query,cache_dir,europeana_key=None,nasa_key=None,pexels_key=None,pixabay_key=None,used_sources=None):
    used_sources=used_sources or {};pools={}
    funcs=(("nasa",lambda:search_nasa(query,cache_dir,nasa_key)),("loc",lambda:search_loc(query,cache_dir)),("europeana",lambda:search_europeana(query,cache_dir,europeana_key)),("pexels",lambda:search_pexels(query,cache_dir,pexels_key)),("pixabay",lambda:search_pixabay(query,cache_dir,pixabay_key)),("wikimedia",lambda:search_wikimedia(query,cache_dir)))
    for name,fn in funcs:
        try:pools[name]=fn()
        except (requests.RequestException,ValueError,TypeError) as exc:log.warning("%s source failed for '%s': %s",name,query,exc);pools[name]=[]
    candidates=[]
    for source in SOURCE_ORDER:
        for item in pools.get(source,[]):
            item=dict(item);item["selection_score"]=item.get("score",0)+max(0,8-min(used_sources.get(source,0),8));candidates.append(item)
    candidates.sort(key=lambda x:(-x["selection_score"],-x.get("score",0),SOURCE_ORDER.index(x["source"])))
    log.info("Source candidates for '%s': %s",query,", ".join(f"{x['source']}:{x['score']}" for x in candidates[:12]) or "none")
    return candidates

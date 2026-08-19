"""Multi-source discovery for historical video and still-image assets.

Sources are searched only for discovery. Assets are accepted only when their
metadata indicates a reusable/public-domain/CC license. Commercial archives
are never treated as automatically free.
"""
from __future__ import annotations
import hashlib,json,time
from pathlib import Path
import requests
from utils import log

UA={"User-Agent":"History-Shorts-Pipeline/1.0"}
CACHE_TTL=7*24*3600

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

def _terms(text:str)->set[str]:
    import re
    return {x.lower() for x in re.findall(r"[a-z0-9]+",str(text)) if len(x)>2}

def _score(query:str,title:str,description:str="")->int:
    q=_terms(query);t=_terms(title);d=_terms(description);return sum(8 if x in t else 2 if x in d else 0 for x in q)

def search_wikimedia(query:str,cache_dir:Path)->list[dict]:
    data=_get_json("https://commons.wikimedia.org/w/api.php",{"action":"query","generator":"search","gsrsearch":query,"gsrnamespace":6,"gsrlimit":20,"prop":"imageinfo|info","iiprop":"url|size|mime|extmetadata","format":"json","formatversion":2},cache_dir)
    out=[]
    for p in (data or {}).get("query",{}).get("pages",[]):
        info=(p.get("imageinfo") or [{}])[0];meta=info.get("extmetadata",{});license_name=(meta.get("LicenseShortName") or {}).get("value","");title=p.get("title","");
        if info.get("url") and ("public domain" in license_name.lower() or "cc0" in license_name.lower() or license_name.lower().startswith("cc")):
            out.append({"source":"wikimedia","kind":"still","title":title,"url":info["url"],"license":license_name,"score":_score(query,title,str(meta.get("ImageDescription",{}).get("value","")))})
    return sorted(out,key=lambda x:-x["score"])

def search_europeana(query:str,cache_dir:Path,api_key:str|None=None)->list[dict]:
    if not api_key:return []
    data=_get_json("https://api.europeana.eu/record/v2/search.json",{"wskey":api_key,"query":query,"rows":20,"profile":"rich"},cache_dir);out=[]
    for item in (data or {}).get("items",[]):
        rights=str(item.get("rights",""));link=item.get("edmIsShownBy") or item.get("edmPreview")
        if link and any(x in rights.lower() for x in ("creativecommons.org","public domain","creativecommons")):
            out.append({"source":"europeana","kind":"still","title":item.get("title",[""])[0] if isinstance(item.get("title"),list) else item.get("title",""),"url":link,"license":rights,"score":_score(query,str(item.get("title","")),str(item.get("dcDescription","")))})
    return sorted(out,key=lambda x:-x["score"])

def search_loc(query:str,cache_dir:Path)->list[dict]:
    data=_get_json("https://www.loc.gov/search/",{"q":query,"fo":"json","c":20,"fa":"online-format:image|online-format:video"},cache_dir);out=[]
    for item in (data or {}).get("results",[]):
        rights=str(item.get("rights","")+" "+item.get("description","") or "");url=item.get("image_url",[None])[0] if item.get("image_url") else item.get("url")
        if url and any(x in rights.lower() for x in ("public domain","free to use","no known restrictions")):
            out.append({"source":"loc","kind":"still_or_video","title":item.get("title",""),"url":url,"license":rights,"score":_score(query,item.get("title",""),str(item.get("description","")))})
    return sorted(out,key=lambda x:-x["score"])

def search_all(query:str,cache_dir:Path, europeana_key:str|None=None)->list[dict]:
    results=[]
    results += search_wikimedia(query,cache_dir)
    results += search_loc(query,cache_dir)
    results += search_europeana(query,cache_dir,europeana_key)
    return sorted(results,key=lambda x:-x.get("score",0))

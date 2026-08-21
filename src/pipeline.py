"""End-to-end history video pipeline with daily Shorts and 48-hour documentaries."""
from __future__ import annotations
import json, os, sys, tempfile, subprocess
from datetime import datetime, timezone
from pathlib import Path
from fetch_footage import fetch_clip_for_beat
from generate_narration import generate_all
from generate_script import generate_script
from assemble_video import assemble, make_thumbnail
from upload_youtube import upload_short, upload_long
from utils import ensure_dir, log
ROOT=Path(__file__).resolve().parent.parent; TOPICS_FILE=ROOT/"config"/"topics.json"; STATE_FILE=ROOT/"config"/"state.json"; CHECKPOINT_DIR=ROOT/"config"/".longform_checkpoints"
SOURCE_CREDIT="\n\nHistorical footage and imagery are selected from public-domain, CC/CC0, or otherwise rights-cleared sources. Credits are retained where available."; LONG_FORM_INTERVAL_HOURS=48; MIN_LONG_SECONDS=25*60; MAX_LONG_SECONDS=35*60; MAX_TOPIC_ATTEMPTS=15; PREFLIGHT_BEATS=2

def _probe(path):
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-show_entries","stream=width,height","-of","json",str(path)],capture_output=True,text=True,check=True);d=json.loads(p.stdout);duration=float((d.get("format") or {}).get("duration",0));streams=d.get("streams") or [];v=next((x for x in streams if x.get("width") and x.get("height")),{});return duration,int(v.get("width",0)),int(v.get("height",0))
def _thumbnail_dimensions(path):
    p=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=s=x:p=0",str(path)],capture_output=True,text=True,check=True);w,h=p.stdout.strip().split("x");return int(w),int(h)
def _load_state():
    try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):return {}
def _save_state(s):STATE_FILE.write_text(json.dumps(s,indent=2)+"\n",encoding="utf-8")
def _long_form_due(s):
    stamp=s.get("last_longform_publish")
    if not stamp:return True
    try:last=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    except ValueError:return True
    return (datetime.now(timezone.utc)-last).total_seconds()/3600>=LONG_FORM_INTERVAL_HOURS
def _record_long_form_publish(s):s["last_longform_publish"]=datetime.now(timezone.utc).isoformat();_save_state(s)
def _mark_topic_failed(s,t,r):s.setdefault("skipped_topics",{})[t]={"reason":r,"at":datetime.now(timezone.utc).isoformat()};_save_state(s)
def _topics():
    data=json.loads(TOPICS_FILE.read_text(encoding="utf-8"));return data.get("topics",[]) if isinstance(data,dict) else data
def _candidate_topics(s,preferred=None):
    if preferred:return [preferred]
    topics=_topics();failed=set(s.get("skipped_topics",{}));published=set(s.get("published_topics",[]));cursor=int(s.get("topic_cursor",0));out=[]
    if CHECKPOINT_DIR.exists():
        for cp in CHECKPOINT_DIR.glob("*.json"):
            try:
                t=json.loads(cp.read_text()).get("topic")
                if t and t not in published and t not in out:out.append(t)
            except Exception:pass
    for off in range(len(topics)):
        t=topics[(cursor+off)%len(topics)]
        if t not in failed and t not in published and t not in out:out.append(t)
        if len(out)>=MAX_TOPIC_ATTEMPTS:break
    return out

def _mark_published(s,t):
    p=s.setdefault("published_topics",[])
    if t not in p:p.append(t)
    topics=_topics()
    if topics and t in topics:s["topic_cursor"]=(topics.index(t)+1)%len(topics)
    _save_state(s)

def _preflight(topic,script,work):
    d=ensure_dir(work/"preflight");used={};disabled=set()
    for i,b in enumerate(script["beats"][:PREFLIGHT_BEATS]):
        if fetch_clip_for_beat(b["footage_query"],topic,d,i,duration=1.0,used_sources=used,disabled_sources=disabled) is None:return False
    return True

def _build(topic,work,long_form):
    if long_form:os.environ["LONGFORM_CHECKPOINT_DIR"]=str(CHECKPOINT_DIR)
    script=generate_script(topic,long_form)
    if not _preflight(topic,script,work):raise RuntimeError("visual preflight failed")
    fd=ensure_dir(work/"footage");used={};disabled=set();paths=[]
    for i,b in enumerate(script["beats"]):
        p=fetch_clip_for_beat(b["footage_query"],topic,fd,i,duration=8.0 if not long_form else 24.0,used_sources=used,disabled_sources=disabled)
        if p is None:raise RuntimeError(f"No suitable related rights-cleared visual for beat {i}: {b['footage_query']}")
        paths.append(p)
    ad=ensure_dir(work/"audio");script["beats"]=generate_all(script["beats"],ad);out=work/("final_long.mp4" if long_form else "final_short.mp4");assemble(script["beats"],paths,work,out,long_form=long_form);return out,script,used

def _try_topics(s,root,long_form,preferred=None):
    for topic in _candidate_topics(s,preferred):
        try:
            log.info("Trying %s topic: %s",'long-form' if long_form else 'Short',topic);p,script,used=_build(topic,root/topic.replace(" ","_"),long_form);log.info("Completed %s topic '%s' with source usage: %s",'long-form' if long_form else 'Short',topic,used);return topic,p,script
        except (RuntimeError,ValueError,KeyError,json.JSONDecodeError) as exc:
            cp=CHECKPOINT_DIR/(topic.lower().replace(" ","_")+".json")
            if long_form and cp.exists():log.warning("Deferring unfinished long-form '%s' without marking topic failed: %s",topic,exc)
            else:_mark_topic_failed(s,topic,str(exc))
    return None,None,None

def _validate_long(path,thumb):
    duration,w,h=_probe(path)
    if not MIN_LONG_SECONDS<=duration<=MAX_LONG_SECONDS:raise RuntimeError(f"Long-form QC failed: {duration:.1f}s")
    if w<1920 or h<1080 or w/h<1.7:raise RuntimeError(f"Long-form QC failed: {w}x{h}")
    if not thumb.exists() or thumb.stat().st_size<10000:raise RuntimeError("Long-form QC failed: thumbnail missing")
    tw,th=_thumbnail_dimensions(thumb)
    if (tw,th)!=(1280,720):raise RuntimeError(f"Long-form QC failed: thumbnail {tw}x{th}")

def main():
    forced=sys.argv[1].strip() if len(sys.argv)>1 and sys.argv[1].strip() else None;s=_load_state();due=_long_form_due(s)
    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        root=Path(tmp);st,sp,ss=_try_topics(s,root/"short",False,forced)
        if sp is None:
            log.warning("No viable Short topic found; ending run cleanly so scheduler can retry tomorrow.");return
        if os.environ.get("DRY_RUN")=="1":(ROOT/"output_preview.mp4").write_bytes(sp.read_bytes());return
        upload_short(sp,title=ss["title"],description=ss["description"].rstrip()+SOURCE_CREDIT,tags=ss["tags"]);_mark_published(s,st)
        if not due:return
        lt,lp,ls=_try_topics(s,root/"long",True)
        if lp is None:
            log.warning("Long-form deferred: Short was already published successfully.");return
        try:
            thumb=lp.parent/"thumbnail.jpg";make_thumbnail(lp,ls["title"],thumb,ls.get("thumbnail_text"));_validate_long(lp,thumb);upload_long(lp,title=ls["title"],description=ls["description"].rstrip()+SOURCE_CREDIT,tags=ls["tags"],thumbnail_path=thumb);_mark_published(s,lt);_record_long_form_publish(s)
            cp=CHECKPOINT_DIR/(lt.lower().replace(" ","_")+".json")
            if cp.exists():cp.unlink()
        except (RuntimeError,ValueError,OSError) as exc:log.warning("Long-form deferred after build/QC/upload preparation failure: %s",exc)
if __name__=="__main__":main()

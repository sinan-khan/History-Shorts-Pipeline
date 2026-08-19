"""End-to-end history video pipeline with daily Shorts and 48-hour documentaries."""
from __future__ import annotations
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
from fetch_footage import fetch_clip_for_beat
from generate_narration import generate_all
from generate_script import generate_script
from assemble_video import assemble, make_thumbnail
from topic_queue import get_next_topic
from upload_youtube import upload_short, upload_long
from utils import ensure_dir, log, run
ROOT=Path(__file__).resolve().parent.parent; TOPICS_FILE=ROOT/"config"/"topics.json"; STATE_FILE=ROOT/"config"/"state.json"
SOURCE_CREDIT="\n\nHistorical footage and imagery sourced from reusable public-domain/Creative Commons archives; individual asset credits are retained where available."; LONG_FORM_INTERVAL_HOURS=48; MIN_LONG_SECONDS=25*60; MAX_LONG_SECONDS=35*60; MAX_TOPIC_ATTEMPTS=5

def _load_state()->dict:
    try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):return {}
def _save_state(state:dict)->None:STATE_FILE.write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")
def _long_form_due(state:dict)->bool:
    stamp=state.get("last_longform_publish")
    if not stamp:return True
    try:last=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    except ValueError:return True
    return (datetime.now(timezone.utc)-last).total_seconds()/3600>=LONG_FORM_INTERVAL_HOURS
def _record_long_form_publish(state:dict)->None:state["last_longform_publish"]=datetime.now(timezone.utc).isoformat();_save_state(state)
def _mark_topic_failed(state:dict,topic:str,reason:str)->None:state.setdefault("skipped_topics",{})[topic]={"reason":reason,"at":datetime.now(timezone.utc).isoformat()};_save_state(state)
def _probe(path:Path)->tuple[float,int,int]:
    result=run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height:format=duration","-of","json",str(path)]);data=json.loads(result.stdout);s=data["streams"][0];return float(data.get("format",{}).get("duration",0)),int(s.get("width",0)),int(s.get("height",0))
def _thumbnail_dimensions(path:Path)->tuple[int,int]:
    result=run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0:s=x",str(path)]);w,h=result.stdout.strip().split("x");return int(w),int(h)
def _validate_long_form(path:Path,thumbnail:Path)->None:
    duration,width,height=_probe(path)
    if not MIN_LONG_SECONDS<=duration<=MAX_LONG_SECONDS:raise RuntimeError(f"Long-form validation failed: {duration:.1f}s outside 25-35 minutes")
    if width<1920 or height<1080 or width/height<1.7:raise RuntimeError(f"Long-form validation failed: {width}x{height} is not 16:9 landscape")
    if not thumbnail.exists() or thumbnail.stat().st_size<10000:raise RuntimeError("Long-form validation failed: thumbnail missing")
    tw,th=_thumbnail_dimensions(thumbnail)
    if (tw,th)!=(1280,720):raise RuntimeError(f"Long-form validation failed: thumbnail {tw}x{th}")
def _build(topic:str,work_dir:Path,long_form:bool)->tuple[Path,dict]:
    footage_dir=ensure_dir(work_dir/"footage");script=generate_script(topic,long_form);footage_paths=[];used_sources=set()
    for i,beat in enumerate(script["beats"]):
        clip=fetch_clip_for_beat(beat["footage_query"],topic,footage_dir,i,duration=8.0 if not long_form else 24.0,used_sources=used_sources)
        if clip is None:raise RuntimeError(f"No suitable unique historical visual found for beat {i} (query='{beat['footage_query']}')")
        footage_paths.append(clip)
    audio_dir=ensure_dir(work_dir/"audio");script["beats"]=generate_all(script["beats"],audio_dir);out_path=work_dir/("final_long.mp4" if long_form else "final_short.mp4");assemble(script["beats"],footage_paths,work_dir,out_path,long_form=long_form);return out_path,script
def _candidate_topics(state:dict,preferred:str|None=None):
    if preferred:return [preferred]
    topics=json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    if isinstance(topics,dict):topics=topics.get("topics",[])
    failed=set(state.get("skipped_topics",{}));published=set(state.get("published_topics",[]));cursor=int(state.get("topic_cursor",0))
    out=[]
    for offset in range(len(topics)):
        topic=topics[(cursor+offset)%len(topics)]
        if topic not in failed and topic not in published and topic not in out:out.append(topic)
        if len(out)>=MAX_TOPIC_ATTEMPTS:break
    return out
def _mark_published(state:dict,topic:str)->None:
    published=state.setdefault("published_topics",[])
    if topic not in published:published.append(topic)
    topics=json.loads(TOPICS_FILE.read_text(encoding="utf-8"));topics=topics.get("topics",[]) if isinstance(topics,dict) else topics
    if topics:state["topic_cursor"]=(topics.index(topic)+1)%len(topics)
    _save_state(state)
def _build_with_fallback(state:dict,root:Path,long_form:bool,preferred:str|None=None):
    for topic in _candidate_topics(state,preferred):
        try:return topic,*_build(topic,root/topic.replace(" ","_"),long_form)
        except (RuntimeError,ValueError,KeyError,json.JSONDecodeError) as exc:
            log.warning("Skipping topic '%s': %s",topic,exc);_mark_topic_failed(state,topic,str(exc))
    raise RuntimeError(f"No viable {'long-form' if long_form else 'Short'} topic found after {MAX_TOPIC_ATTEMPTS} attempts")
def main()->None:
    forced=sys.argv[1].strip() if len(sys.argv)>1 and sys.argv[1].strip() else None;state=_load_state();due=_long_form_due(state)
    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        root=Path(tmp);short_topic,short_path,short_script=_build_with_fallback(state,root/"short",False,forced)
        if os.environ.get("DRY_RUN")=="1":(ROOT/"output_preview.mp4").write_bytes(short_path.read_bytes());return
        upload_short(short_path,title=short_script["title"],description=short_script["description"].rstrip()+SOURCE_CREDIT,tags=short_script["tags"]);_mark_published(state,short_topic)
        if due:
            long_topic,long_path,long_script=_build_with_fallback(state,root/"long",True);thumbnail=long_path.parent/"thumbnail.jpg";make_thumbnail(long_path,long_script["title"],thumbnail,long_script.get("thumbnail_text"));_validate_long_form(long_path,thumbnail);upload_long(long_path,title=long_script["title"],description=long_script["description"].rstrip()+SOURCE_CREDIT,tags=long_script["tags"],thumbnail_path=thumbnail);_mark_published(state,long_topic);_record_long_form_publish(state)
if __name__=="__main__":main()

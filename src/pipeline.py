"""End-to-end history video pipeline with daily Shorts and 48-hour documentaries."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from fetch_footage import fetch_clip_for_beat
from generate_narration import generate_all
from generate_script import generate_script
from assemble_video import assemble, make_thumbnail
from topic_queue import get_next_topic
from upload_youtube import upload_short, upload_long
from utils import ensure_dir, log

ROOT=Path(__file__).resolve().parent.parent
TOPICS_FILE=ROOT/"config"/"topics.json"
STATE_FILE=ROOT/"config"/"state.json"
SOURCE_CREDIT="\n\nHistorical footage sourced from public-domain archives via the Internet Archive."
LONG_FORM_INTERVAL_HOURS=48


def _load_state()->dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):
        return {}


def _long_form_due(state:dict)->bool:
    stamp=state.get("last_longform_publish")
    if not stamp:
        return True
    try:
        last=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    except ValueError:
        return True
    elapsed=(datetime.now(timezone.utc)-last).total_seconds()/3600
    log.info("Last long-form publish was %.1f hours ago",elapsed)
    return elapsed >= LONG_FORM_INTERVAL_HOURS


def _record_long_form_publish(state:dict)->None:
    state["last_longform_publish"]=datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")


def _build(topic:str,work_dir:Path,long_form:bool)->tuple[Path,dict]:
    footage_dir=ensure_dir(work_dir/"footage")
    script=generate_script(topic,long_form)
    footage_paths=[]
    for i,beat in enumerate(script["beats"]):
        clip=fetch_clip_for_beat(beat["footage_query"],topic,footage_dir,i)
        if clip is None:
            raise RuntimeError(f"No suitable public-domain footage found for beat {i} (query='{beat['footage_query']}'.)")
        footage_paths.append(clip)
    audio_dir=ensure_dir(work_dir/"audio")
    script["beats"]=generate_all(script["beats"],audio_dir)
    out_path=work_dir/("final_long.mp4" if long_form else "final_short.mp4")
    assemble(script["beats"],footage_paths,work_dir,out_path,long_form=long_form)
    return out_path,script


def main()->None:
    forced_topic=sys.argv[1] if len(sys.argv)>1 and sys.argv[1].strip() else None
    state=_load_state()
    long_form_due=_long_form_due(state)

    short_topic=forced_topic or get_next_topic(TOPICS_FILE,STATE_FILE)
    log.info("=== Building daily history Short: %s ===",short_topic)

    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        root=Path(tmp)
        short_path,short_script=_build(short_topic,root/"short",False)
        if os.environ.get("DRY_RUN")=="1":
            (ROOT/"output_preview.mp4").write_bytes(short_path.read_bytes())
            log.info("DRY_RUN set — Short preview saved; skipping uploads.")
            return
        upload_short(short_path,title=short_script["title"],description=short_script["description"].rstrip()+SOURCE_CREDIT,tags=short_script["tags"])

        if long_form_due:
            long_topic=get_next_topic(TOPICS_FILE,STATE_FILE)
            log.info("=== Building 25-35 minute documentary: %s ===",long_topic)
            long_path,long_script=_build(long_topic,root/"long",True)
            thumbnail=root/"long"/"thumbnail.jpg"
            make_thumbnail(long_path,long_script["title"],thumbnail)
            upload_long(long_path,title=long_script["title"],description=long_script["description"].rstrip()+SOURCE_CREDIT,tags=long_script["tags"],thumbnail_path=thumbnail)
            _record_long_form_publish(state)
            log.info("Long-form documentary uploaded and scheduled; next one is due in 48 hours.")

if __name__=="__main__":
    main()

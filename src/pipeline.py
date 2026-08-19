"""End-to-end history video pipeline with automatic Short/long-form selection."""
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
from upload_youtube import upload_video
from utils import ensure_dir, log

ROOT=Path(__file__).resolve().parent.parent
TOPICS_FILE=ROOT/"config"/"topics.json"
STATE_FILE=ROOT/"config"/"state.json"
SOURCE_CREDIT="\n\nHistorical footage sourced from public-domain archives via the Internet Archive."
LARGE_FOOTAGE_THRESHOLD=100*1024*1024
LONG_FORM_INTERVAL_HOURS=48


def _long_form_due() -> bool:
    try:
        state=json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return True
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


def _record_long_form_publish() -> None:
    try:
        state=json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError,json.JSONDecodeError):
        state={}
    state["last_longform_publish"]=datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state,indent=2)+"\n",encoding="utf-8")


def _download_beats(beats:list[dict],topic:str,out_dir:Path)->tuple[list[Path],bool]:
    paths=[]; large=False
    for i,beat in enumerate(beats):
        clip=fetch_clip_for_beat(beat["footage_query"],topic,out_dir,i)
        if clip is None:
            raise RuntimeError(f"No suitable public-domain footage found for beat {i} (query='{beat['footage_query']}').")
        paths.append(clip)
        size=clip.stat().st_size
        log.info("Footage %d: %.1f MB",i,size/(1024*1024))
        if size>LARGE_FOOTAGE_THRESHOLD:
            large=True
    return paths,large


def build_video(topic:str,work_dir:Path,long_form:bool)->tuple[Path,dict]:
    footage_dir=ensure_dir(work_dir/"footage")
    script=generate_script(topic,long_form)
    footage_paths,_=_download_beats(script["beats"],topic,footage_dir)
    audio_dir=ensure_dir(work_dir/"audio")
    script["beats"]=generate_all(script["beats"],audio_dir)
    out_path=work_dir/("final_long.mp4" if long_form else "final_short.mp4")
    assemble(script["beats"],footage_paths,work_dir,out_path,long_form=long_form)
    return out_path,script


def main()->None:
    forced_topic=sys.argv[1] if len(sys.argv)>1 and sys.argv[1].strip() else None
    # A forced topic is still allowed, but automatic scheduling respects the 48-hour
    # long-form cadence. The daily Actions run makes this deterministic without cron math.
    if _long_form_due():
        topic=forced_topic or get_next_topic(TOPICS_FILE,STATE_FILE)
        log.info("=== Building scheduled 25-35 minute documentary: %s ===",topic)
        long_form=True
    else:
        topic=forced_topic or get_next_topic(TOPICS_FILE,STATE_FILE)
        log.info("=== Building history Short: %s ===",topic)
        long_form=False

    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        work_dir=Path(tmp)
        video_path,script=build_video(topic,work_dir,long_form)
        if os.environ.get("DRY_RUN")=="1":
            keep_path=ROOT/("output_preview_long.mp4" if long_form else "output_preview.mp4")
            keep_path.write_bytes(video_path.read_bytes())
            log.info("DRY_RUN set — video saved to %s, skipping upload.")
            return
        description=script["description"].rstrip()+SOURCE_CREDIT
        thumbnail=None
        if long_form:
            thumbnail=work_dir/"thumbnail.jpg"
            make_thumbnail(video_path,script["title"],thumbnail)
        upload_video(video_path,title=script["title"],description=description,tags=script["tags"],thumbnail_path=thumbnail)
        if long_form:
            _record_long_form_publish()

if __name__=="__main__":
    main()

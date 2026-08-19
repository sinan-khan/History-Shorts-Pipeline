"""End-to-end history video pipeline with automatic Short/long-form selection."""
from __future__ import annotations
import os
import sys
import tempfile
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


def build_video(topic:str,work_dir:Path)->tuple[Path,dict,bool]:
    footage_dir=ensure_dir(work_dir/"footage")
    # Start cheap: a normal Short is the default. If any selected source is substantial,
    # regenerate the story as a documentary and render it horizontally.
    short_script=generate_script(topic,False)
    footage_paths,large=_download_beats(short_script["beats"],topic,footage_dir)
    script=short_script
    long_form=large
    if large:
        log.info("Large footage detected (>100 MB). Switching this topic to long-form mode.")
        script=generate_script(topic,True)
        # Rebuild the footage set for the documentary's longer structure.
        footage_paths,_=_download_beats(script["beats"],topic,footage_dir)

    audio_dir=ensure_dir(work_dir/"audio")
    script["beats"]=generate_all(script["beats"],audio_dir)
    out_path=work_dir/("final_long.mp4" if long_form else "final_short.mp4")
    assemble(script["beats"],footage_paths,work_dir,out_path,long_form=long_form)
    return out_path,script,long_form


def main()->None:
    forced_topic=sys.argv[1] if len(sys.argv)>1 and sys.argv[1].strip() else None
    topic=forced_topic or get_next_topic(TOPICS_FILE,STATE_FILE)
    log.info("=== Building history video: %s ===",topic)
    with tempfile.TemporaryDirectory(prefix="history-shorts-") as tmp:
        work_dir=Path(tmp)
        video_path,script,long_form=build_video(topic,work_dir)
        if os.environ.get("DRY_RUN")=="1":
            keep_path=ROOT/("output_preview_long.mp4" if long_form else "output_preview.mp4")
            keep_path.write_bytes(video_path.read_bytes())
            log.info("DRY_RUN set — video saved to %s, skipping upload.",keep_path)
            return
        description=script["description"].rstrip()+SOURCE_CREDIT
        thumbnail=None
        if long_form:
            thumbnail=work_dir/"thumbnail.jpg"
            make_thumbnail(video_path,script["title"],thumbnail)
        upload_video(video_path,title=script["title"],description=description,tags=script["tags"],thumbnail_path=thumbnail)

if __name__=="__main__":
    main()

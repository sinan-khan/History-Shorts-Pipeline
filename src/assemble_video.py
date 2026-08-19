"""Assemble beat-synced history videos in vertical or long-form format."""
from __future__ import annotations
from pathlib import Path
from utils import get_duration, log, run
SHORT_SIZE=(1080,1920); LONG_SIZE=(1920,1080)

def _short_filter(width:int,height:int)->str:
    return (f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma=28,eq=brightness=-0.16:saturation=0.85[bg2];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];[fg2]eq=contrast=1.05:saturation=1.05[fg3];[bg2][fg3]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p")

def _long_filter(width:int,height:int,index:int)->str:
    # Gentle documentary motion on archival material; avoids a static slideshow.
    zoom="zoompan=z='min(zoom+0.00035,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30" if index%2==0 else "zoompan=z='max(1.0,zoom-0.0003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30"
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p,{zoom}"

def _trim_or_loop_to_duration(src:Path,dest:Path,target_duration:float,long_form:bool=False,index:int=0)->None:
    width,height=LONG_SIZE if long_form else SHORT_SIZE; src_duration=get_duration(src)
    vf=_long_filter(width,height,index) if long_form else _short_filter(width,height)
    if src_duration>=target_duration:
        start=0.5 if src_duration>target_duration+0.5 else 0.0; cmd=["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    else:
        cmd=["ffmpeg","-y","-stream_loop","-1","-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    run(cmd)

def _mux_beat(video_only:Path,audio:Path,dest:Path)->None:
    run(["ffmpeg","-y","-i",str(video_only),"-i",str(audio),"-c:v","copy","-c:a","aac","-b:a","192k","-shortest",str(dest)])

def _write_srt(beats:list[dict],dest:Path)->None:
    def fmt(t:float)->str:
        h,rem=divmod(t,3600);m,s=divmod(rem,60);ms=int((s-int(s))*1000);return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    lines=[];t=0.0;cue=1
    for beat in beats:
        words=beat["line"].split(); chunk_size=6 if len(words)<=24 else 7; chunks=[" ".join(words[j:j+chunk_size]) for j in range(0,len(words),chunk_size)] or [beat["line"]]; chunk_duration=beat["duration"]/len(chunks)
        for chunk in chunks:
            lines += [str(cue),f"{fmt(t)} --> {fmt(t+chunk_duration)}",chunk,""];cue+=1;t+=chunk_duration
    dest.write_text("\n".join(lines),encoding="utf-8")

def assemble(beats:list[dict],footage_paths:list[Path],work_dir:Path,out_path:Path,long_form:bool=False)->Path:
    beat_clips=[]
    for i,(beat,footage) in enumerate(zip(beats,footage_paths)):
        trimmed=work_dir/f"trimmed_{i}.mp4";muxed=work_dir/f"beat_{i}.mp4";_trim_or_loop_to_duration(footage,trimmed,beat["duration"],long_form,i);_mux_beat(trimmed,Path(beat["audio_path"]),muxed);beat_clips.append(muxed)
    concat_list=work_dir/"concat.txt";concat_list.write_text("\n".join(f"file '{c.resolve()}'" for c in beat_clips),encoding="utf-8");concatenated=work_dir/"concatenated.mp4";run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_list),"-c","copy",str(concatenated)])
    srt_path=work_dir/"captions.srt";_write_srt(beats,srt_path)
    style="FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=65,MarginL=80,MarginR=80" if long_form else "FontSize=17,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=330,MarginL=120,MarginR=120"
    run(["ffmpeg","-y","-i",str(concatenated),"-vf",f"subtitles={srt_path}:force_style='{style}'","-c:v","libx264","-crf","18","-preset","medium","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-movflags","+faststart",str(out_path)])
    log.info("Final %s video written to %s","long-form" if long_form else "Short",out_path);return out_path

def make_thumbnail(video_path:Path,title:str,dest:Path,thumbnail_text:str|None=None)->Path:
    """Create a four-panel historical collage: launch/hero, portrait, landscape, artifact.
    Simple curiosity text is layered over the strongest hero image. All source frames
    are extracted from the finished documentary, so no extra image API/key is required.
    """
    work=dest.parent; frames=[]
    for i,ss in enumerate((4, max(8,get_duration(video_path)*0.25), max(12,get_duration(video_path)*0.55), max(16,get_duration(video_path)*0.8))):
        p=work/f"thumb_{i}.jpg";run(["ffmpeg","-y","-ss",str(ss),"-i",str(video_path),"-frames:v","1","-vf","scale=640:360:force_original_aspect_ratio=increase,crop=640:360,eq=contrast=1.12:saturation=1.12",str(p)]);frames.append(p)
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf";text=(thumbnail_text or "THE UNTOLD STORY").upper()[:32].replace("'","\\'").replace("%","\\%")
    # Four selected historical frames, with one dominant hero panel and three supporting panels.
    run(["ffmpeg","-y","-i",str(frames[0]),"-i",str(frames[1]),"-i",str(frames[2]),"-i",str(frames[3]),"-filter_complex",f"[0:v]scale=1280:720[hero];[1:v]scale=426:240[p1];[2:v]scale=426:240[p2];[3:v]scale=426:240[p3];[hero][p1]overlay=854:0[a];[a][p2]overlay=854:240[b];[b][p3]overlay=854:480[c];[c]drawbox=x=40:y=500:w=780:h=175:color=black@0.55:t=fill,drawtext=fontfile={font}:text='{text}':fontcolor=white:fontsize=62:borderw=3:bordercolor=black:x=65:y=545", "-frames:v","1",str(dest)])
    log.info("Four-image cinematic thumbnail written to %s",dest);return dest

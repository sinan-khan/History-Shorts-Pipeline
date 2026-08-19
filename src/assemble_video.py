"""Assemble beat-synced history videos in vertical or long-form format."""
from __future__ import annotations
from pathlib import Path
from utils import get_duration, log, run
SHORT_SIZE=(1080,1920); LONG_SIZE=(1920,1080)

def _short_filter(width:int,height:int)->str:
    return (f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma=28,eq=brightness=-0.16:saturation=0.85[bg2];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];[fg2]eq=contrast=1.05:saturation=1.05[fg3];[bg2][fg3]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p")

def _long_filter(width:int,height:int,index:int)->str:
    # Alternating slow push/pull gives archival footage gentle documentary motion.
    zoom="zoompan=z='min(zoom+0.00035,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30" if index%2==0 else "zoompan=z='max(1.0,zoom-0.0003)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=30"
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},format=yuv420p,{zoom}"

def _trim_or_loop_to_duration(src:Path,dest:Path,target_duration:float,long_form:bool=False,index:int=0)->None:
    width,height=LONG_SIZE if long_form else SHORT_SIZE; src_duration=get_duration(src); vf=_long_filter(width,height,index) if long_form else _short_filter(width,height)
    if src_duration>=target_duration:
        start=0.5 if src_duration>target_duration+0.5 else 0.0; cmd=["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    else: cmd=["ffmpeg","-y","-stream_loop","-1","-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
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
    """Create an adaptive cinematic thumbnail from authentic documentary frames.

    The chosen visual language is a dark historical-mystery + dramatic-portrait look,
    not a four-panel collage. We use one dominant hero frame and subtle supporting
    imagery, with the concept text supplied by the script model. No image is claimed
    to be AI-generated or authentic beyond the source footage itself.
    """
    work=dest.parent; duration=get_duration(video_path)
    # Four candidate moments; the strongest becomes the full-frame hero. The other
    # three are softly blended into the background rather than shown as a grid.
    positions=[min(4,duration*0.08),min(duration*0.30,max(8,duration-1)),min(duration*0.58,max(12,duration-1)),min(duration*0.82,max(16,duration-1))]
    frames=[]
    for i,ss in enumerate(positions):
        p=work/f"thumb_source_{i}.jpg"
        run(["ffmpeg","-y","-ss",str(ss),"-i",str(video_path),"-frames:v","1","-vf","scale=1600:900:force_original_aspect_ratio=increase,crop=1600:900,eq=contrast=1.18:saturation=1.08,unsharp=5:5:0.5",str(p)])
        frames.append(p)
    # Build a cinematic background from all four source moments, then place the first
    # frame prominently. A dark gradient/overlay keeps text readable without a box.
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text=(thumbnail_text or "THE UNTOLD STORY").upper().strip()[:36].replace("'","\\'").replace("%","\\%")
    out_tmp=work/"thumbnail_composite.jpg"
    fc=("[0:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "eq=contrast=1.16:saturation=1.08,unsharp=5:5:0.5[hero];"
        "[1:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "gblur=sigma=24,eq=brightness=-0.22:saturation=0.75[bg1];"
        "[2:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "gblur=sigma=24,eq=brightness=-0.28:saturation=0.70[bg2];"
        "[3:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "gblur=sigma=24,eq=brightness=-0.30:saturation=0.65[bg3];"
        "[bg1][bg2]blend=all_mode=screen:all_opacity=0.22[b12];"
        "[b12][bg3]blend=all_mode=screen:all_opacity=0.18[bg];"
        "[bg][hero]blend=all_mode=overlay:all_opacity=0.88[base];"
        "[base]drawbox=x=0:y=0:w=1280:h=720:color=black@0.18:t=fill,"
        "drawbox=x=0:y=430:w=1280:h=290:color=black@0.30:t=fill,"
        f"drawtext=fontfile={font}:text='{text}':fontcolor=white:fontsize=62:"
        "line_spacing=6:borderw=3:bordercolor=black:x=55:y=500:enable='between(t,0,1)'[v]")
    run(["ffmpeg","-y","-i",str(frames[0]),"-i",str(frames[1]),"-i",str(frames[2]),"-i",str(frames[3]),"-filter_complex",fc,"-frames:v","1",str(out_tmp)])
    # A final exact-size pass guarantees YouTube thumbnail dimensions.
    run(["ffmpeg","-y","-i",str(out_tmp),"-vf","scale=1280:720:flags=lanczos","-q:v","2",str(dest)])
    log.info("Adaptive cinematic thumbnail written to %s",dest);return dest

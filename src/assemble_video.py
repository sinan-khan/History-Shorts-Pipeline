"""Assemble beat-synced history videos in vertical or long-form format."""
from __future__ import annotations
from pathlib import Path
from utils import get_duration, log, run

SHORT_SIZE=(1080,1920)
LONG_SIZE=(1920,1080)


def _short_filter(width:int,height:int)->str:
    # Preserve the complete archival frame. A softly blurred/darkened copy fills
    # the vertical canvas, while the sharp original sits centered on top.
    return (f"split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"gblur=sigma=28,eq=brightness=-0.16:saturation=0.85[bg2];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2];"
            f"[fg2]eq=contrast=1.05:saturation=1.05[fg3];"
            f"[bg2][fg3]overlay=(W-w)/2:(H-h)/2,"
            f"setsar=1,format=yuv420p")


def _trim_or_loop_to_duration(src:Path,dest:Path,target_duration:float,long_form:bool=False)->None:
    width,height=LONG_SIZE if long_form else SHORT_SIZE
    src_duration=get_duration(src)
    vf=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if long_form else _short_filter(width,height)
    if src_duration>=target_duration:
        # Start near the beginning so the source's establishing shot is not lost.
        start=0.5 if src_duration>target_duration+0.5 else 0.0
        cmd=["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    else:
        cmd=["ffmpeg","-y","-stream_loop","-1","-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-crf","18","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    run(cmd)


def _mux_beat(video_only:Path,audio:Path,dest:Path)->None:
    run(["ffmpeg","-y","-i",str(video_only),"-i",str(audio),"-c:v","copy","-c:a","aac","-b:a","192k","-shortest",str(dest)])


def _write_srt(beats:list[dict],dest:Path)->None:
    def fmt(t:float)->str:
        h,rem=divmod(t,3600);m,s=divmod(rem,60);ms=int((s-int(s))*1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    lines=[];t=0.0;cue=1
    for beat in beats:
        words=beat["line"].split()
        # 5-7 words per cue gives Shorts viewers time to read without covering
        # the entire frame. Keep each cue to at most two visual lines.
        chunk_size=6 if not len(words)>24 else 7
        chunks=[" ".join(words[j:j+chunk_size]) for j in range(0,len(words),chunk_size)] or [beat["line"]]
        chunk_duration=beat["duration"]/len(chunks)
        for chunk in chunks:
            start,end=t,t+chunk_duration
            lines += [str(cue),f"{fmt(start)} --> {fmt(end)}",chunk,""]
            cue+=1;t=end
    dest.write_text("\n".join(lines),encoding="utf-8")


def assemble(beats:list[dict],footage_paths:list[Path],work_dir:Path,out_path:Path,long_form:bool=False)->Path:
    beat_clips=[]
    for i,(beat,footage) in enumerate(zip(beats,footage_paths)):
        trimmed=work_dir/f"trimmed_{i}.mp4";muxed=work_dir/f"beat_{i}.mp4"
        _trim_or_loop_to_duration(footage,trimmed,beat["duration"],long_form)
        _mux_beat(trimmed,Path(beat["audio_path"]),muxed);beat_clips.append(muxed)
    concat_list=work_dir/"concat.txt"
    concat_list.write_text("\n".join(f"file '{c.resolve()}'" for c in beat_clips),encoding="utf-8")
    concatenated=work_dir/"concatenated.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_list),"-c","copy",str(concatenated)])
    srt_path=work_dir/"captions.srt";_write_srt(beats,srt_path)
    if long_form:
        style="FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=65,MarginL=80,MarginR=80"
    else:
        # Clean Shorts caption treatment: small, centered, safely above YouTube UI.
        style="FontSize=17,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=330,MarginL=120,MarginR=120"
    run(["ffmpeg","-y","-i",str(concatenated),"-vf",f"subtitles={srt_path}:force_style='{style}'","-c:v","libx264","-crf","18","-preset","medium","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-movflags","+faststart",str(out_path)])
    log.info("Final %s video written to %s","long-form" if long_form else "Short",out_path)
    return out_path


def make_thumbnail(video_path:Path,title:str,dest:Path)->Path:
    clean=" ".join(title.replace("#Shorts","").split())[:70].replace(":"," -")
    font="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text=clean.replace("'","\\'").replace("%","\\%")
    vf=(f"scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.12:saturation=1.15,drawbox=x=0:y=500:w=1280:h=220:color=black@0.62:t=fill,drawtext=fontfile={font}:text='{text}':fontcolor=white:fontsize=58:line_spacing=8:borderw=3:bordercolor=black:x=55:y=535:box=0")
    run(["ffmpeg","-y","-ss","3","-i",str(video_path),"-frames:v","1","-vf",vf,str(dest)])
    log.info("Custom thumbnail written to %s",dest)
    return dest

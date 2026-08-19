"""Assemble beat-synced history videos in vertical or long-form format."""
from __future__ import annotations
from pathlib import Path
from utils import get_duration, log, run

SHORT_SIZE = (1080, 1920)
LONG_SIZE = (1920, 1080)


def _trim_or_loop_to_duration(src: Path, dest: Path, target_duration: float, long_form: bool = False) -> None:
    width, height = LONG_SIZE if long_form else SHORT_SIZE
    src_duration = get_duration(src)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if src_duration >= target_duration:
        start = min(3.0, max(0.0, src_duration - target_duration))
        cmd = ["ffmpeg","-y","-ss",str(start),"-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    else:
        cmd = ["ffmpeg","-y","-stream_loop","-1","-i",str(src),"-t",str(target_duration),"-an","-vf",vf,"-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",str(dest)]
    run(cmd)


def _mux_beat(video_only: Path, audio: Path, dest: Path) -> None:
    run(["ffmpeg","-y","-i",str(video_only),"-i",str(audio),"-c:v","copy","-c:a","aac","-shortest",str(dest)])


def _write_srt(beats: list[dict], dest: Path) -> None:
    def fmt(t: float) -> str:
        h, rem = divmod(t, 3600); m, s = divmod(rem, 60); ms = int((s-int(s))*1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
    lines=[]; t=0.0
    for i, beat in enumerate(beats, 1):
        start,end=t,t+beat["duration"]
        lines += [str(i),f"{fmt(start)} --> {fmt(end)}",beat["line"],""]
        t=end
    dest.write_text("\n".join(lines),encoding="utf-8")


def assemble(beats: list[dict], footage_paths: list[Path], work_dir: Path, out_path: Path, long_form: bool = False) -> Path:
    beat_clips=[]
    for i,(beat,footage) in enumerate(zip(beats,footage_paths)):
        trimmed=work_dir/f"trimmed_{i}.mp4"; muxed=work_dir/f"beat_{i}.mp4"
        _trim_or_loop_to_duration(footage,trimmed,beat["duration"],long_form)
        _mux_beat(trimmed,Path(beat["audio_path"]),muxed); beat_clips.append(muxed)
    concat_list=work_dir/"concat.txt"
    concat_list.write_text("\n".join(f"file '{c.resolve()}'" for c in beat_clips),encoding="utf-8")
    concatenated=work_dir/"concatenated.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat_list),"-c","copy",str(concatenated)])
    srt_path=work_dir/"captions.srt"; _write_srt(beats,srt_path)
    style="FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=70" if long_form else "FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=120"
    run(["ffmpeg","-y","-i",str(concatenated),"-vf",f"subtitles={srt_path}:force_style='{style}'","-c:a","copy",str(out_path)])
    log.info("Final %s video written to %s","long-form" if long_form else "Short",out_path)
    return out_path


def make_thumbnail(video_path: Path, title: str, dest: Path) -> Path:
    """Create a 1280x720 custom thumbnail from the video's strongest opening frame."""
    clean = " ".join(title.replace("#Shorts", "").split())[:70].replace(":", " -")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text = clean.replace("'", "\\'").replace("%", "\\%")
    vf = (f"scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
          f"eq=contrast=1.12:saturation=1.15,drawbox=x=0:y=500:w=1280:h=220:color=black@0.62:t=fill,"
          f"drawtext=fontfile={font}:text='{text}':fontcolor=white:fontsize=58:line_spacing=8:"
          f"borderw=3:bordercolor=black:x=55:y=535:box=0")
    run(["ffmpeg","-y","-ss","3","-i",str(video_path),"-frames:v","1","-vf",vf,str(dest)])
    log.info("Custom thumbnail written to %s",dest)
    return dest

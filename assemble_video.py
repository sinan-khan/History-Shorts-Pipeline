"""Assemble the final vertical Short from per-beat footage + narration.

Sync strategy (beat-level, not word-level — reliable and simple):
  For beat i: narration_duration[i] == footage_clip_duration[i] == caption_display_duration[i]
So the footage cut points, the narration, and the on-screen captions all change
at exactly the same moments. That's what "perfectly synced with the story" means here.
"""
from __future__ import annotations

from pathlib import Path

from utils import get_duration, log, run

WIDTH, HEIGHT = 1080, 1920


def _trim_or_loop_to_duration(src: Path, dest: Path, target_duration: float) -> None:
    src_duration = get_duration(src)
    vf = f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT}"

    if src_duration >= target_duration:
        # Take a clip from a bit into the source (skip likely title-card openings) if room allows.
        start = min(3.0, max(0.0, src_duration - target_duration))
        cmd = [
            "ffmpeg", "-y", "-ss", str(start), "-i", str(src),
            "-t", str(target_duration), "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest),
        ]
    else:
        # Footage shorter than narration: loop it to fill the beat.
        cmd = [
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
            "-t", str(target_duration), "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest),
        ]
    run(cmd)


def _mux_beat(video_only: Path, audio: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_only), "-i", str(audio),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        str(dest),
    ]
    run(cmd)


def _write_srt(beats: list[dict], dest: Path) -> None:
    def fmt(t: float) -> str:
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        ms = int((s - int(s)) * 1000)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"

    lines = []
    t = 0.0
    for i, beat in enumerate(beats, start=1):
        start, end = t, t + beat["duration"]
        lines.append(str(i))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(beat["line"])
        lines.append("")
        t = end
    dest.write_text("\n".join(lines), encoding="utf-8")


def assemble(beats: list[dict], footage_paths: list[Path], work_dir: Path, out_path: Path) -> Path:
    """beats[i] must already have 'audio_path' and 'duration' set (see generate_narration.generate_all)."""
    beat_clips = []
    for i, (beat, footage) in enumerate(zip(beats, footage_paths)):
        trimmed = work_dir / f"trimmed_{i}.mp4"
        muxed = work_dir / f"beat_{i}.mp4"
        _trim_or_loop_to_duration(footage, trimmed, beat["duration"])
        _mux_beat(trimmed, Path(beat["audio_path"]), muxed)
        beat_clips.append(muxed)

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(f"file '{c.resolve()}'" for c in beat_clips), encoding="utf-8")

    concatenated = work_dir / "concatenated.mp4"
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(concatenated),
    ])

    srt_path = work_dir / "captions.srt"
    _write_srt(beats, srt_path)

    style = "FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,Outline=2,Alignment=2,MarginV=120"
    run([
        "ffmpeg", "-y", "-i", str(concatenated),
        "-vf", f"subtitles={srt_path}:force_style='{style}'",
        "-c:a", "copy",
        str(out_path),
    ])

    log.info("Final video written to %s", out_path)
    return out_path

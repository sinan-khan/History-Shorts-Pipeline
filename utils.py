"""Shared helpers used across the pipeline."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return logging.getLogger("history-shorts")


log = setup_logging()


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command, logging it first."""
    log.info("RUN: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        log.error("Command failed (%s): %s", result.returncode, result.stderr[-2000:])
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr[-2000:]}")
    return result


def get_duration(path: str | Path) -> float:
    """Return media duration in seconds using ffprobe."""
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

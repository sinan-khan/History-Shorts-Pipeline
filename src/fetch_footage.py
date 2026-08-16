"""Search and download public-domain footage from the Internet Archive.

archive.org needs no API key. We bias results toward collections that are reliably
public domain (Prelinger ephemeral films, US government film collections, NASA, etc.)
and double-check each candidate's licenseurl before downloading.
"""
from __future__ import annotations

from pathlib import Path

import requests

from utils import log, run

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

# Collections known to be public-domain / government / ephemeral film archives.
# Search results from these collections are preferred over the open "movies" pool.
SAFE_COLLECTIONS = {
    "prelinger",
    "usgovernmentfilms",
    "universal_newsreels",
    "NASAarchive",
    "nasa",
    "internetarchivebooks",  # excluded on purpose from video use, kept for reference
}

PREFERRED_EXTENSIONS = (".mp4", ".m4v", ".mov")


def search_archive(query: str, rows: int = 8) -> list[dict]:
    params = {
        "q": f'({query}) AND mediatype:(movies)',
        "fl[]": ["identifier", "title", "licenseurl", "collection"],
        "rows": rows,
        "output": "json",
    }
    resp = requests.get(SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("response", {}).get("docs", [])
    log.info("Archive.org search '%s' -> %d results", query, len(docs))
    return docs


def _is_public_domain(doc: dict) -> bool:
    license_url = (doc.get("licenseurl") or "").lower()
    if "publicdomain" in license_url or "/by/" in license_url or "cc0" in license_url:
        return True
    collections = doc.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    return bool(SAFE_COLLECTIONS.intersection(collections))


def pick_best_candidate(query: str, fallback_query: str | None = None) -> dict | None:
    """Return the first public-domain-verified search result, trying a fallback query if needed."""
    for q in [query, fallback_query]:
        if not q:
            continue
        for doc in search_archive(q):
            if _is_public_domain(doc):
                return doc
    log.warning("No verified public-domain match for '%s' / fallback '%s'", query, fallback_query)
    return None


def get_video_file_url(identifier: str) -> str | None:
    resp = requests.get(METADATA_URL.format(identifier=identifier), timeout=30)
    resp.raise_for_status()
    files = resp.json().get("files", [])
    # Prefer the largest mp4-ish file (usually the primary derivative, not the raw master).
    candidates = [f for f in files if f.get("name", "").lower().endswith(PREFERRED_EXTENSIONS)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: int(f.get("size", 0) or 0), reverse=True)
    best = candidates[0]
    return DOWNLOAD_URL.format(identifier=identifier, filename=best["name"])


def download_file(url: str, dest: Path) -> Path:
    log.info("Downloading %s -> %s", url, dest)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return dest


def fetch_clip_for_beat(query: str, fallback_query: str, out_dir: Path, index: int) -> Path | None:
    """High-level entry point: search, verify, download raw footage for one beat.

    Returns the path to the raw downloaded clip (untrimmed) or None if nothing usable was found.
    Caller (assemble_video.py) is responsible for trimming to the matching narration duration.
    """
    doc = pick_best_candidate(query, fallback_query)
    if not doc:
        return None
    url = get_video_file_url(doc["identifier"])
    if not url:
        log.warning("No downloadable video file for identifier %s", doc["identifier"])
        return None
    dest = out_dir / f"raw_{index}.mp4"
    return download_file(url, dest)


def probe_video_duration(path: Path) -> float:
    from utils import get_duration

    return get_duration(path)

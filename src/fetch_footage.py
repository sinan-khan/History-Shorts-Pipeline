"""Search and download reusable archival footage from the Internet Archive.

Only candidates with an explicit public-domain/CC0 license, or from a known
public-domain government collection, are accepted. The downloader also handles
Archive.org filenames that contain spaces or other URL-sensitive characters.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests

from utils import log

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"

SAFE_COLLECTIONS = {
    "usgovernmentfilms",
    "NASAarchive",
    "nasa",
}

PREFERRED_EXTENSIONS = (".mp4", ".m4v", ".mov")


def search_archive(query: str, rows: int = 12) -> list[dict]:
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
    explicit_pd = "publicdomain" in license_url or "creativecommons.org/publicdomain" in license_url
    cc0 = "cc0" in license_url
    collections = doc.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    return explicit_pd or cc0 or bool(SAFE_COLLECTIONS.intersection(collections))


def pick_best_candidate(query: str, fallback_query: str | None = None) -> dict | None:
    """Return the first license-verified result, trying the fallback query if needed."""
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
    candidates = [
        f for f in files
        if f.get("name", "").lower().endswith(PREFERRED_EXTENSIONS)
        and not f.get("private")
    ]
    candidates.sort(key=lambda f: int(f.get("size", 0) or 0), reverse=True)
    for candidate in candidates:
        filename = candidate.get("name")
        if filename:
            return DOWNLOAD_URL.format(
                identifier=identifier,
                filename=quote(filename, safe="/"),
            )
    return None


def download_file(url: str, dest: Path) -> Path:
    log.info("Downloading %s -> %s", url, dest)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


def fetch_clip_for_beat(query: str, fallback_query: str, out_dir: Path, index: int) -> Path | None:
    """Search, verify, and download raw footage for one beat."""
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

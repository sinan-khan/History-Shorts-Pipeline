"""Search and download reusable archival footage from the Internet Archive."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
import requests
from utils import log

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"
SAFE_COLLECTIONS = {"usgovernmentfilms", "NASAarchive", "nasa", "prelinger", "universal_newsreels"}
PREFERRED_EXTENSIONS = (".mp4", ".m4v", ".mov")
MAX_VIDEO_BYTES = 500 * 1024 * 1024
GENERIC_FALLBACK_QUERIES = ("historical city street", "historical people crowd", "historic europe", "vintage city")


def search_archive(query: str, rows: int = 20) -> list[dict]:
    params = {"q": f'({query}) AND mediatype:(movies)', "fl[]": ["identifier", "title", "licenseurl", "collection"], "rows": rows, "output": "json"}
    r = requests.get(SEARCH_URL, params=params, timeout=30)
    r.raise_for_status()
    docs = r.json().get("response", {}).get("docs", [])
    log.info("Archive.org search '%s' -> %d results", query, len(docs))
    return docs


def _is_public_domain(doc: dict) -> bool:
    if not doc.get("identifier"):
        return False
    license_url = (doc.get("licenseurl") or "").lower()
    collections = doc.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    return ("publicdomain" in license_url or "creativecommons.org/publicdomain" in license_url or "cc0" in license_url or bool(SAFE_COLLECTIONS.intersection(collections)))


def _query_variants(query: str) -> list[str]:
    words = [w for w in query.replace(",", " ").split() if w]
    variants = [query]
    if len(words) > 3: variants.append(" ".join(words[:3]))
    if len(words) > 2: variants.append(" ".join(words[-3:]))
    if "construction" in words: variants.append("historic construction")
    if "tower" in words: variants.append("historic tower")
    return list(dict.fromkeys(variants))


def get_video_file_url(identifier: str) -> tuple[str, int] | None:
    r = requests.get(METADATA_URL.format(identifier=identifier), timeout=30)
    r.raise_for_status()
    candidates = []
    for f in r.json().get("files", []):
        name = f.get("name", "")
        if not name.lower().endswith(PREFERRED_EXTENSIONS) or f.get("private"):
            continue
        try: size = int(f.get("size", 0) or 0)
        except (TypeError, ValueError): size = 0
        if 0 < size <= MAX_VIDEO_BYTES:
            candidates.append((size, name))
        elif size > MAX_VIDEO_BYTES:
            log.info("Skipping oversized Archive.org file %s (%d MB)", name, size // (1024 * 1024))
    if not candidates:
        return None
    size, filename = min(candidates)
    return DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename, safe="/")), size


def pick_best_candidate(query: str, fallback_query: str | None = None) -> tuple[dict, str, int] | None:
    """Only select a public-domain item after confirming it has a <=500 MB video."""
    queries = _query_variants(query) + (_query_variants(fallback_query) if fallback_query else []) + list(GENERIC_FALLBACK_QUERIES)
    seen_queries, seen_ids = set(), set()
    for q in queries:
        if q in seen_queries: continue
        seen_queries.add(q)
        for doc in search_archive(q):
            if not _is_public_domain(doc): continue
            identifier = doc["identifier"]
            if identifier in seen_ids: continue
            seen_ids.add(identifier)
            try:
                selected = get_video_file_url(identifier)
            except requests.RequestException as exc:
                log.warning("Archive metadata failed for %s: %s", identifier, exc)
                continue
            if selected:
                url, size = selected
                log.info("Selected '%s' (%d MB) for query '%s'", doc.get("title"), size // (1024 * 1024), q)
                return doc, url, size
            log.info("Ignoring public-domain item %s: no video <= 500 MB", identifier)
    log.warning("No verified downloadable public-domain match for '%s' / '%s'", query, fallback_query)
    return None


def download_file(url: str, dest: Path) -> Path:
    log.info("Downloading %s -> %s", url, dest)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk: f.write(chunk)
    return dest


def fetch_clip_for_beat(query: str, fallback_query: str, out_dir: Path, index: int) -> Path | None:
    selected = pick_best_candidate(query, fallback_query)
    if not selected: return None
    doc, url, size = selected
    dest = out_dir / f"raw_{index}.mp4"
    try:
        log.info("Downloading selected Archive.org file (%d MB): %s", size // (1024 * 1024), url)
        return download_file(url, dest)
    except requests.RequestException as exc:
        log.warning("Download failed for %s: %s", doc.get("identifier"), exc)
        if dest.exists(): dest.unlink()
        return None


def probe_video_duration(path: Path) -> float:
    from utils import get_duration
    return get_duration(path)

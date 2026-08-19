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
MAX_DOWNLOAD_RETRIES = 3


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
    if len(words) > 3:
        variants.append(" ".join(words[:3]))
    if len(words) > 2:
        variants.append(" ".join(words[-3:]))
    if "construction" in words:
        variants.append("historic construction")
    if "tower" in words:
        variants.append("historic tower")
    return list(dict.fromkeys(variants))


def _video_candidates(identifier: str) -> list[tuple[str, int]]:
    """Return all usable video files for an Archive item, smallest first."""
    r = requests.get(METADATA_URL.format(identifier=identifier), timeout=30)
    r.raise_for_status()
    candidates = []
    for f in r.json().get("files", []):
        name = f.get("name", "")
        if not name.lower().endswith(PREFERRED_EXTENSIONS) or f.get("private"):
            continue
        try:
            size = int(f.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if 0 < size <= MAX_VIDEO_BYTES:
            candidates.append((name, size))
        elif size > MAX_VIDEO_BYTES:
            log.info("Skipping oversized Archive.org file %s (%d MB)", name, size // (1024 * 1024))
    candidates.sort(key=lambda item: item[1])
    return candidates


def get_video_file_url(identifier: str) -> tuple[str, int] | None:
    candidates = _video_candidates(identifier)
    if not candidates:
        return None
    filename, size = candidates[0]
    return DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename, safe="/")), size


def _candidate_items(query: str, fallback_query: str | None = None) -> list[tuple[dict, str, int]]:
    """Find multiple verified candidates so a transient Archive download failure doesn't abort a beat."""
    queries = _query_variants(query)
    if fallback_query:
        queries += _query_variants(fallback_query)
    queries += list(GENERIC_FALLBACK_QUERIES)
    seen_queries, seen_ids = set(), set()
    candidates = []
    for q in queries:
        if q in seen_queries:
            continue
        seen_queries.add(q)
        try:
            docs = search_archive(q)
        except requests.RequestException as exc:
            log.warning("Archive search failed for '%s': %s", q, exc)
            continue
        for doc in docs:
            if not _is_public_domain(doc):
                continue
            identifier = doc["identifier"]
            if identifier in seen_ids:
                continue
            seen_ids.add(identifier)
            try:
                files = _video_candidates(identifier)
            except requests.RequestException as exc:
                log.warning("Archive metadata failed for %s: %s", identifier, exc)
                continue
            if not files:
                log.info("Ignoring public-domain item %s: no video <= 500 MB", identifier)
                continue
            # Prefer the smallest file, but retain the item as a fallback if the first URL fails.
            filename, size = files[0]
            url = DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename, safe="/"))
            candidates.append((doc, url, size))
            log.info("Candidate '%s' (%d MB) for query '%s'", doc.get("title"), size // (1024 * 1024), q)
    candidates.sort(key=lambda item: item[2])
    return candidates


def pick_best_candidate(query: str, fallback_query: str | None = None) -> tuple[dict, str, int] | None:
    candidates = _candidate_items(query, fallback_query)
    if not candidates:
        log.warning("No verified downloadable public-domain match for '%s' / '%s'", query, fallback_query)
        return None
    doc, url, size = candidates[0]
    log.info("Selected '%s' (%d MB)", doc.get("title"), size // (1024 * 1024))
    return doc, url, size


def download_file(url: str, dest: Path) -> Path:
    log.info("Downloading %s", url)
    with requests.get(url, stream=True, timeout=(20, 120)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


def fetch_clip_for_beat(query: str, fallback_query: str, out_dir: Path, index: int) -> Path | None:
    candidates = _candidate_items(query, fallback_query)
    if not candidates:
        return None

    dest = out_dir / f"raw_{index}.mp4"
    # Try several independent Archive.org items. A 500/timeout on one mirror should
    # never make the entire Short fail when another valid item is available.
    for attempt, (doc, url, size) in enumerate(candidates[:MAX_DOWNLOAD_RETRIES], 1):
        try:
            log.info("Downloading candidate %d/%d (%d MB): %s", attempt, min(MAX_DOWNLOAD_RETRIES, len(candidates)), size // (1024 * 1024), url)
            return download_file(url, dest)
        except requests.RequestException as exc:
            log.warning("Download failed for %s: %s", doc.get("identifier"), exc)
            if dest.exists():
                dest.unlink()

    log.warning("All Archive.org download candidates failed for beat %d (%s)", index, query)
    return None


def probe_video_duration(path: Path) -> float:
    from utils import get_duration
    return get_duration(path)

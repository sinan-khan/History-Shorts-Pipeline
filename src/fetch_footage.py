"""Search and download reusable archival footage from the Internet Archive."""
from __future__ import annotations
from pathlib import Path
from urllib.parse import quote
import time
import requests
from utils import log

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"
SAFE_COLLECTIONS = {"usgovernmentfilms", "NASAarchive", "nasa", "prelinger", "universal_newsreels"}
PREFERRED_EXTENSIONS = (".mp4", ".m4v", ".mov")
MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_DOWNLOAD_RETRIES = 3
MAX_CANDIDATES = 8

# Never use generic modern/random footage merely to fill a historical beat.
# A clip is allowed only when its title/query has meaningful topical overlap.
BAD_TITLE_TERMS = {"hoax", "fake", "conspiracy", "jolly heretic", "genetic interests", "flat earth"}
STOPWORDS = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "from", "about", "history", "historical", "story", "video", "film"}


def search_archive(query: str, rows: int = 20) -> list[dict]:
    params = {"q": f'({query}) AND mediatype:(movies)', "fl[]": ["identifier", "title", "description", "licenseurl", "collection"], "rows": rows, "output": "json"}
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            r = requests.get(SEARCH_URL, params=params, timeout=(10, 20))
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
            log.info("Archive.org search '%s' -> %d results", query, len(docs))
            return docs
        except requests.RequestException as exc:
            log.warning("Archive search attempt %d/%d failed for '%s': %s", attempt, MAX_DOWNLOAD_RETRIES, query, exc)
            if attempt < MAX_DOWNLOAD_RETRIES:
                time.sleep(1.5 * attempt)
    return []


def _is_public_domain(doc: dict) -> bool:
    if not doc.get("identifier"):
        return False
    license_url = (doc.get("licenseurl") or "").lower()
    collections = doc.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    return ("publicdomain" in license_url or "creativecommons.org/publicdomain" in license_url or "cc0" in license_url or bool(SAFE_COLLECTIONS.intersection(collections)))


def _terms(text: str) -> set[str]:
    import re
    return {w.lower() for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2 and w.lower() not in STOPWORDS}


def _relevance(query: str, doc: dict) -> int:
    q = _terms(query)
    title = (doc.get("title") or "")
    desc = doc.get("description") or ""
    if isinstance(desc, list):
        desc = " ".join(map(str, desc))
    text = _terms(title)
    score = 0
    for term in q:
        if term in text:
            score += 8
        elif term in _terms(desc):
            score += 2
    low = (title + " " + str(desc)).lower()
    if any(bad in low for bad in BAD_TITLE_TERMS):
        score -= 50
    # NASA/public-domain collections are valuable for space-history queries.
    if any(x in query.lower() for x in ("apollo", "moon", "nasa", "mercury", "gemini", "saturn", "lunar")):
        if any(x.lower() in low for x in ("apollo", "nasa", "moon", "lunar", "mercury", "gemini", "saturn")):
            score += 15
    return score


def _query_variants(query: str) -> list[str]:
    words = [w for w in query.replace(",", " ").split() if w]
    variants = [query]
    if len(words) > 4:
        variants.append(" ".join(words[:4]))
    if len(words) > 2:
        variants.append(" ".join(words[-3:]))
    qlow = query.lower()
    if "construction" in qlow and "tower" in qlow:
        variants += ["Eiffel Tower construction", "Eiffel Tower workers", "Paris 1889 Eiffel"]
    if any(x in qlow for x in ("apollo", "moon", "lunar")):
        variants += ["Apollo 11", "Apollo astronauts", "NASA Apollo", "moon landing", "Saturn V", "Apollo mission"]
    return list(dict.fromkeys(variants))


def _video_candidates(identifier: str) -> list[tuple[str, int]]:
    r = requests.get(METADATA_URL.format(identifier=identifier), timeout=(10, 20))
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
    queries = _query_variants(query)
    if fallback_query:
        queries += _query_variants(fallback_query)
    seen_queries, seen_ids = set(), set()
    candidates = []
    for q in queries:
        if q in seen_queries:
            continue
        seen_queries.add(q)
        docs = search_archive(q)
        for doc in docs:
            if not _is_public_domain(doc):
                continue
            identifier = doc["identifier"]
            if identifier in seen_ids:
                continue
            relevance = _relevance(query, doc)
            # Require real topical evidence. This prevents "Crowd watching TV"
            # from silently becoming a random movie about an unrelated subject.
            if relevance < 8:
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
            # Prefer relevance first, then a reasonably small file. Do not always
            # choose the tiniest file: tiny 512kb proxies can be visibly poor quality.
            best_file = min(files, key=lambda item: item[1])
            filename, size = best_file
            url = DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename, safe="/"))
            candidates.append((doc, url, size, relevance))
            log.info("Candidate '%s' (%d MB, relevance=%d) for query '%s'", doc.get("title"), size // (1024 * 1024), relevance, q)
    candidates.sort(key=lambda item: (-item[3], item[2]))
    return [(doc, url, size) for doc, url, size, _ in candidates[:MAX_CANDIDATES]]


def pick_best_candidate(query: str, fallback_query: str | None = None) -> tuple[dict, str, int] | None:
    candidates = _candidate_items(query, fallback_query)
    if not candidates:
        log.warning("No relevant downloadable public-domain match for '%s' / '%s'", query, fallback_query)
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
    if dest.stat().st_size == 0:
        raise requests.RequestException("Archive.org returned an empty file")
    return dest


def fetch_clip_for_beat(query: str, fallback_query: str, out_dir: Path, index: int) -> Path | None:
    candidates = _candidate_items(query, fallback_query)
    if not candidates:
        return None
    dest = out_dir / f"raw_{index}.mp4"
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

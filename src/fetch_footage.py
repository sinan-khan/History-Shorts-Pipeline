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
GENERIC_FALLBACK_QUERIES = ("historical city street", "historical people crowd", "historic europe", "vintage city")


def search_archive(query: str, rows: int = 12) -> list[dict]:
    params = {"q": f'({query}) AND mediatype:(movies)', "fl[]": ["identifier", "title", "licenseurl", "collection"], "rows": rows, "output": "json"}
    resp = requests.get(SEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("response", {}).get("docs", [])
    log.info("Archive.org search '%s' -> %d results", query, len(docs))
    return docs


def _is_public_domain(doc: dict) -> bool:
    if not doc.get("identifier"):
        return False
    license_url = (doc.get("licenseurl") or "").lower()
    explicit_pd = "publicdomain" in license_url or "creativecommons.org/publicdomain" in license_url
    cc0 = "cc0" in license_url
    collections = doc.get("collection", [])
    if isinstance(collections, str):
        collections = [collections]
    return explicit_pd or cc0 or bool(SAFE_COLLECTIONS.intersection(collections))


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


def pick_best_candidate(query: str, fallback_query: str | None = None) -> dict | None:
    queries = _query_variants(query)
    if fallback_query:
        queries += _query_variants(fallback_query)
    queries += list(GENERIC_FALLBACK_QUERIES)
    seen: set[str] = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        for doc in search_archive(q):
            if _is_public_domain(doc):
                log.info("Selected public-domain footage '%s' for query '%s'", doc.get("title"), q)
                return doc
    log.warning("No verified public-domain match for '%s' / fallback '%s'", query, fallback_query)
    return None


def get_video_file_url(identifier: str) -> str | None:
    resp = requests.get(METADATA_URL.format(identifier=identifier), timeout=30)
    resp.raise_for_status()
    files = resp.json().get("files", [])
    candidates = [f for f in files if f.get("name", "").lower().endswith(PREFERRED_EXTENSIONS) and not f.get("private")]
    candidates.sort(key=lambda f: int(f.get("size", 0) or 0), reverse=True)
    for candidate in candidates:
        filename = candidate.get("name")
        if filename:
            return DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename, safe="/"))
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
    doc = pick_best_candidate(query, fallback_query)
    if not doc:
        return None
    identifier = doc.get("identifier")
    if not identifier:
        log.warning("Archive candidate has no identifier: %r", doc)
        return None
    url = get_video_file_url(identifier)
    if not url:
        log.warning("No downloadable video file for identifier %s", identifier)
        return None
    dest = out_dir / f"raw_{index}.mp4"
    return download_file(url, dest)


def probe_video_duration(path: Path) -> float:
    from utils import get_duration
    return get_duration(path)

"""Provider catalog and playback resolution, independent of Kodi."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

NETWORK_ERRORS = (OSError, ValueError)
TIMEOUT = 4
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MEDIA_TYPES = {"movies", "tv", "sports"}
SOURCE_TYPES = {"direct", "real-debrid"}


def is_web_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and "|" not in value
    except ValueError:
        return False


def request_json(url, headers=None, data=None):
    with urlopen(Request(url, headers=headers or {}, data=data), timeout=TIMEOUT) as response:
        return decode_json(response)


def decode_json(stream):
    payload = stream.read(MAX_CATALOG_BYTES + 1)
    if len(payload) > MAX_CATALOG_BYTES:
        raise ValueError("JSON response is too large")
    return json.loads(payload)


def read_provider(location):
    try:
        if is_web_url(location):
            return request_json(location)
        with Path(location).open("rb") as source:
            return decode_json(source)
    except NETWORK_ERRORS:
        return None


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def valid_item(item):
    if not isinstance(item, dict):
        return False
    if not isinstance(item.get("type"), str) or item["type"] not in MEDIA_TYPES:
        return False
    if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("id", "title")):
        return False
    return item["type"] != "sports" or (
        isinstance(item.get("sport"), str)
        and item["sport"].strip()
        and isinstance(item.get("sport_id"), str)
        and item["sport_id"].strip()
        and parse_time(item.get("starts_at")) is not None
    )


def load_catalog(locations):
    if not locations:
        return {}, 0
    with ThreadPoolExecutor(max_workers=min(8, len(locations))) as pool:
        documents = list(pool.map(read_provider, locations))
    items = {}
    failed = 0
    for priority, document in enumerate(documents):
        if not isinstance(document, dict) or not isinstance(document.get("items"), list):
            failed += 1
            continue
        for item in filter(valid_item, document["items"]):
            key = (item["type"], item["id"])
            merged = items.setdefault(key, dict(item, sources=[]))
            sources = item.get("sources")
            if isinstance(sources, list):
                merged["sources"].extend(
                    (priority, source) for source in sources if valid_source(source)
                )
    return items, failed


def valid_source(source):
    return (
        isinstance(source, dict)
        and isinstance(source.get("kind"), str)
        and source["kind"] in SOURCE_TYPES
        and is_web_url(source.get("url"))
        and type(source.get("height", 0)) is int
        and 0 <= source.get("height", 0) <= 8640
    )


def event_status(item, now=None):
    now = now or datetime.now(timezone.utc)
    start = parse_time(item.get("starts_at"))
    end = parse_time(item.get("ends_at")) or start + timedelta(hours=4)
    if now < start:
        return "Upcoming"
    if end and now >= end:
        return "Replay"
    return "Live"


def probe(url, depth=0):
    """Read a small media sample and follow an HLS playlist to its first segment."""
    request = Request(url, headers={"Range": "bytes=0-511"})
    with urlopen(request, timeout=TIMEOUT) as response:
        mime = response.headers.get_content_type()
        if mime.startswith("text/html") or mime in {"application/json", "application/xhtml+xml"}:
            return False
        sample = response.read(512).lstrip()
        if sample.startswith(b"#EXTM3U"):
            sample += response.read(16384)
        final_url = response.geturl()
    if not sample or sample.lower().startswith((b"<!doctype html", b"<html")):
        return False
    if not sample.startswith(b"#EXTM3U"):
        return True
    if depth >= 3:
        return False
    lines = sample.decode("utf-8", errors="replace").splitlines()
    entries = (line.strip() for line in lines[1:] if line.strip() and not line.startswith("#"))
    entry = next(entries, None)
    # A range response can cut off a URI mid-line.
    if entry is None or (sample.endswith(entry.encode()) and not sample.endswith(b"\n")):
        with urlopen(url, timeout=TIMEOUT) as response:
            lines = response.read(16384).decode("utf-8", errors="replace").splitlines()
            final_url = response.geturl()
        entry = next((line.strip() for line in lines[1:] if line.strip() and not line.startswith("#")), "")
    child = urljoin(final_url, entry)
    return bool(entry) and is_web_url(child) and probe(child, depth + 1)


def unrestrict(url, token):
    payload = request_json(
        "https://api.real-debrid.com/rest/1.0/unrestrict/link",
        {"Authorization": "Bearer " + token, "Content-Type": "application/x-www-form-urlencoded"},
        urlencode({"link": url}).encode(),
    )
    return payload.get("download") if isinstance(payload, dict) else None


def source_key(source):
    return sha256((source["kind"] + "\n" + source["url"]).encode()).hexdigest()[:20]


def ranked_sources(item, token, *, max_height=0, prefer_hdr=False, skip=()):
    def metric(source, key):
        value = source.get(key, 0)
        return value if type(value) is int and value > 0 else 0

    def rank(entry):
        priority, source = entry
        return (
            -metric(source, "height"),
            -(prefer_hdr and source.get("hdr") is True),
            -metric(source, "bitrate_kbps"),
            -metric(source, "audio_channels"),
            priority,
        )

    sources = sorted(item["sources"], key=rank)
    seen = set()
    ranked = []
    for _, source in sources:
        height = metric(source, "height")
        if (max_height and height > max_height) or (source["kind"] == "real-debrid" and not token):
            continue
        key = source_key(source)
        if key in skip:
            continue
        if key not in seen:
            seen.add(key)
            ranked.append(source)
    return ranked


def playable_sources(ranked, token):

    def check(url):
        try:
            return is_web_url(url) and probe(url)
        except NETWORK_ERRORS:
            return False

    pool = ThreadPoolExecutor(max_workers=6)
    direct = {}
    try:
        for offset in range(0, len(ranked), 6):
            batch = ranked[offset:offset + 6]
            direct = {
                source["url"]: pool.submit(check, source["url"])
                for source in batch if source["kind"] == "direct"
            }
            for source in batch:
                url = source["url"]
                if source["kind"] == "direct":
                    if direct[url].result():
                        yield source, url
                elif token:
                    try:
                        resolved = unrestrict(url, token)
                        if check(resolved):
                            yield source, resolved
                    except NETWORK_ERRORS:
                        continue
    finally:
        for probe_task in direct.values():
            probe_task.cancel()
        pool.shutdown(wait=False)


def playable_urls(item, token, **preferences):
    for _, url in playable_sources(ranked_sources(item, token, **preferences), token):
        yield url


def imdb_id(item):
    value = item.get("imdb")
    return value if isinstance(value, str) and re.fullmatch(r"tt[0-9]+", value) else None

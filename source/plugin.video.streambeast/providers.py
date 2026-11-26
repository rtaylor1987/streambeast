"""Optional on-demand provider lookups."""
from concurrent.futures import ThreadPoolExecutor, wait
from hashlib import sha256
from importlib import import_module
import json
import re
import sys
from time import time
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import xbmcaddon
import xbmcgui
import xbmcvfs

from catalog import NETWORK_ERRORS, is_web_url, request_json, valid_source

RD = "https://api.real-debrid.com/rest/1.0/"
VIDEO_FILE = re.compile(r"\.(?:mkv|mp4|avi|mov|m4v|webm|ts)$", re.I)


def cached(namespace, key, fetch, ttl=45):
    window = xbmcgui.Window(10000)
    name = "StreamBeast.Cache." + namespace
    signature = sha256(repr(key).encode()).hexdigest()
    try:
        entry = json.loads(window.getProperty(name))
        if entry["key"] == signature and entry["until"] > time():
            return entry["value"]
    except (ValueError, KeyError, TypeError):
        pass
    value = fetch()
    if value:
        window.setProperty(name, json.dumps({"key": signature, "until": time() + ttl, "value": value}))
    return value


def lookup(endpoint, identity):
    if not is_web_url(endpoint):
        return []
    url = urlsplit(endpoint)
    query = "&".join(filter(None, (url.query, urlencode(identity))))
    try:
        result = request_json(urlunsplit(url._replace(query=query, fragment="")))
    except NETWORK_ERRORS:
        return []
    sources = result.get("sources") if isinstance(result, dict) else None
    return [(8, source) for source in sources if valid_source(source)] if isinstance(sources, list) else []


def coco_cloud(identity, token):
    """Match optional Coco results to files already downloaded in Real-Debrid."""
    if not token or not identity.get("imdb") or identity["kind"] == "sports":
        return []
    try:
        module = xbmcaddon.Addon("script.module.cocoscrapers")
        library = xbmcvfs.translatePath(module.getAddonInfo("path") + "/lib")
        if library not in sys.path:
            sys.path.append(library)
        scrapers = import_module("cocoscrapers").sources(["torrents"])
    except (RuntimeError, ImportError, OSError, AttributeError):
        return []
    if not scrapers:
        return []
    headers = {"Authorization": "Bearer " + token}
    try:
        cloud = request_json(RD + "torrents?limit=100", headers)
    except NETWORK_ERRORS:
        return []
    cloud = {item.get("hash", "").lower(): item["id"] for item in cloud
             if item.get("status") == "downloaded" and item.get("id") and item.get("hash")}
    if not cloud:
        return []
    data = {"title": identity["title"], "imdb": identity["imdb"], "year": str(identity.get("year") or ""),
            "season": str(identity.get("season") or ""), "episode": str(identity.get("episode") or ""),
            "tvshowtitle": identity["title"] if identity["kind"] == "tv" else "",
            "aliases": [], "timeout": 4}

    def scrape(source):
        try:
            return source().sources(dict(data), {}) or []
        except Exception:
            return []

    pool = ThreadPoolExecutor(max_workers=4)
    try:
        tasks = [pool.submit(scrape, implementation) for _, implementation in scrapers]
        done, pending = wait(tasks, timeout=5)
        for task in pending:
            task.cancel()
        hashes = {}
        for task in tasks:
            if task not in done:
                continue
            for entry in task.result():
                if not isinstance(entry, dict):
                    continue
                value = str(entry.get("hash") or "").lower()
                if value in cloud:
                    label = str(entry.get("name") or "")
                    height = re.search(r"\b(2160|1080|720|480)p\b|\b4k\b", label, re.I)
                    hashes[value] = max(hashes.get(value, 0), int(height[1] or 2160) if height else 0)
    finally:
        pool.shutdown(wait=False)

    sources = []
    for value, height in sorted(hashes.items(), key=lambda entry: -entry[1])[:5]:
        try:
            torrent = request_json(RD + "torrents/info/" + quote(str(cloud[value]), safe=""), headers)
        except NETWORK_ERRORS:
            continue
        files = [file for file in torrent.get("files", []) if file.get("selected") == 1]
        links = torrent.get("links", [])
        if len(files) != len(links):
            continue
        videos = [(file.get("path") or "", link) for file, link in zip(files, links)
                  if VIDEO_FILE.search(file.get("path") or "") and is_web_url(link)]
        if identity["kind"] == "tv":
            episode = re.compile(r"(?<!\d)S0*{}E0*{}(?!\d)".format(
                int(identity["season"]), int(identity["episode"])), re.I)
            videos = [(path, url) for path, url in videos if episode.search(path)]
        if len(videos) == 1:
            sources.append((9, {"kind": "real-debrid", "url": videos[0][1], "height": height,
                                "provider": "CocoScrapers · Real-Debrid cloud"}))
    return sources

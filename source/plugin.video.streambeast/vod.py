"""TMDB discovery and direct-link Stremio transport."""
from concurrent.futures import ThreadPoolExecutor
import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from catalog import NETWORK_ERRORS, imdb_id, is_web_url, request_json

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGES = "https://image.tmdb.org/t/p/"
KINDS = {"movies": "movie", "tv": "tv"}
FEATURED = ("Netflix", "Hulu", "Amazon Prime Video", "Disney Plus", "HBO Max",
            "Apple TV", "Paramount Plus", "Peacock", "Crunchyroll", "Tubi TV", "Pluto TV")


class TMDB:
    def __init__(self, token, region):
        self.token = token
        self.region = region.strip().upper()
        if not re.fullmatch("[A-Z]{2}", self.region):
            self.region = "US"

    def get(self, path, **params):
        url = TMDB_API + path
        if params:
            url += "?" + urlencode(params)
        return request_json(url, {"Authorization": "Bearer " + self.token})

    def providers(self, kind):
        providers = self.get("/watch/providers/" + KINDS[kind], watch_region=self.region)["results"]
        return sorted(providers, key=lambda p: (
            FEATURED.index(p["provider_name"]) if p["provider_name"] in FEATURED else len(FEATURED),
            p["provider_name"].casefold(),
        ))

    def titles(self, kind, page, provider=None, query=None):
        if query:
            return self.get("/search/" + KINDS[kind], query=query, page=page, include_adult="false")
        filters = {"page": page, "include_adult": "false", "sort_by": "popularity.desc"}
        if provider:
            filters.update(watch_region=self.region, with_watch_providers=provider,
                           with_watch_monetization_types="flatrate|free|ads")
        return self.get("/discover/" + KINDS[kind], **filters)

    def detail(self, kind, title_id):
        return self.get("/{}/{}".format(KINDS[kind], title_id),
                        append_to_response="external_ids,watch/providers")

    def episodes(self, title_id, season):
        return self.get("/tv/{}/season/{}".format(title_id, season))["episodes"]


def art(path, size):
    return TMDB_IMAGES + size + path if isinstance(path, str) and path.startswith("/") else ""


def metadata(data):
    date = data.get("release_date") or data.get("first_air_date") or ""
    return {
        "title": data.get("title") or data.get("name") or "",
        "plot": data.get("overview") or "",
        "poster": art(data.get("poster_path"), "w500"),
        "fanart": art(data.get("backdrop_path"), "w1280"),
        "thumb": art(data.get("still_path"), "w500"),
        "year": int(date[:4]) if date[:4].isdigit() else None,
    }


def matches(items, kind, title_id, imdb, season=None, episode=None):
    return [
        item for item in items if item["type"] == kind
        and (str(item.get("tmdb", "")) == str(title_id) or (imdb and imdb_id(item) == imdb))
        and (kind == "movies" or (str(item.get("season")) == str(season)
                                  and str(item.get("episode")) == str(episode)))
    ]


def stremio_sources(locations, kind, imdb, season=None, episode=None):
    if not re.fullmatch(r"tt[0-9]+", imdb or ""):
        return []
    video_id = imdb if kind == "movies" else "{}:{}:{}".format(imdb, season, episode)
    stream_type = "movie" if kind == "movies" else "series"

    def fetch(location):
        if not is_web_url(location):
            return []
        url = urlsplit(location)
        suffix = "/manifest.json"
        if not url.path.endswith(suffix):
            return []
        endpoint = url.path[:-len(suffix)] + "/stream/{}/{}.json".format(stream_type, quote(video_id, safe=":"))
        try:
            payload = request_json(urlunsplit(url._replace(path=endpoint, fragment="")))
        except NETWORK_ERRORS:
            return []
        streams = payload.get("streams") if isinstance(payload, dict) else None
        if not isinstance(streams, list):
            return []
        candidates = []
        for stream in streams:
            if not isinstance(stream, dict) or not is_web_url(stream.get("url")):
                continue
            hints = stream.get("behaviorHints")
            if isinstance(hints, dict) and hints.get("proxyHeaders"):
                continue
            label = " ".join(str(stream.get(key) or "") for key in ("name", "title", "description"))
            resolution = re.search(r"\b(2160|1080|720|480)p\b|\b(4K)\b", label, re.I)
            height = int(resolution[1] or 2160) if resolution else 0
            hdr = bool(re.search(r"\b(?:HDR(?:10\+?)?|HLG|Dolby Vision|DV)\b", label, re.I))
            candidates.append({"kind": "direct", "url": stream["url"], "height": height, "hdr": hdr})
        return candidates

    if not locations:
        return []
    with ThreadPoolExecutor(max_workers=len(locations)) as pool:
        return [(10 + index, source) for index, results in enumerate(pool.map(fetch, locations))
                for source in results]

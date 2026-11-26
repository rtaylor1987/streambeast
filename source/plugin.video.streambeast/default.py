import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from catalog import (
    NETWORK_ERRORS, event_status, imdb_id, is_web_url, load_catalog, parse_time,
    playable_sources, ranked_sources, source_key,
)
from playback import handoff, history, identity
from providers import cached, coco_cloud, lookup
from trakt import Trakt
from vod import KINDS, TMDB, art, matches, metadata, stremio_sources

TITLES = {"movies": "Movies", "tv": "TV", "sports": "Sports"}
BASE_URL, handle, query = sys.argv
HANDLE = int(handle)
ADDON = xbmcaddon.Addon()


def route(**parameters):
    return BASE_URL + "?" + urlencode(parameters)


def notify(message):
    xbmcgui.Dialog().notification("StreamBeast", message)


def row(label, folder=True, item=None, **parameters):
    entry = xbmcgui.ListItem(label=label)
    if item:
        artwork = {key: item.get(key) for key in ("thumb", "poster", "fanart")
                   if is_web_url(item.get(key))}
        entry.setArt(artwork)
        info = entry.getVideoInfoTag()
        info.setTitle(item["title"])
        if isinstance(item.get("plot"), str):
            info.setPlot(item["plot"])
        if type(item.get("year")) is int and item["year"] > 0:
            info.setYear(item["year"])
    if parameters["action"] in {"play", "play_vod"}:
        entry.setProperty("IsPlayable", "true")
        entry.addContextMenuItems([("Choose source", 'PlayMedia("{}")'.format(
            route(**dict(parameters, choose=1))
        ))])
    xbmcplugin.addDirectoryItem(HANDLE, route(**parameters), entry, folder)


def provider_locations(include_open=False):
    configured = [ADDON.getSetting("provider_" + str(number)).strip() for number in range(1, 5)]
    fallback = Path(xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))) / "catalog.json"
    locations = [location for location in configured if location]
    if fallback.is_file():
        locations.append(str(fallback))
    if include_open:
        locations.append(str(Path(__file__).parent / "resources" / "open_catalog.json"))
    return locations


def show_items(items, sport=None):
    for item in items:
        label = item["title"]
        if sport:
            start = parse_time(item["starts_at"]).astimezone()
            label += "  •  {}  ·  {}".format(event_status(item), start.strftime("%a %b %d, %I:%M %p"))
            if sport is True:
                label = item["sport"] + "  ·  " + label
        if item["type"] == "tv" and type(item.get("season")) is int and type(item.get("episode")) is int:
            label = "S{:02d}E{:02d} · {}".format(item["season"], item["episode"], label)
        row(label, False, item, action="play", kind=item["type"], id=item["id"])


def connect_trakt(trakt):
    if not (trakt.client_id and trakt.secret):
        notify("Enter the Trakt app credentials in StreamBeast settings.")
        return
    progress = xbmcgui.DialogProgress()
    progress.create("Connect Trakt", "Requesting a device code…")
    try:
        if trakt.connect(progress, xbmc.Monitor()):
            notify("Trakt connected.")
    except NETWORK_ERRORS + (KeyError, TypeError):
        notify("Trakt sign-in failed. Check your app credentials and connection.")
    finally:
        progress.close()


def show_watchlist(trakt, catalog, widget=False):
    token = ADDON.getSetting("trakt_access")
    if widget and not token:
        return
    try:
        watchlist = cached("Watchlist", (trakt.client_id, token), trakt.watchlist, ttl=60)
    except NETWORK_ERRORS + (KeyError, TypeError) as error:
        if not widget:
            notify(str(error) if type(error) is ValueError and str(error) == "Connect Trakt first."
                   else "Could not load the Trakt watchlist.")
        return
    ids = {"movies": set(), "tv": set()}
    for entry in watchlist:
        for singular, kind in (("movie", "movies"), ("show", "tv")):
            media = entry.get(singular) or {}
            value = (media.get("ids") or {}).get("imdb")
            if value:
                ids[kind].add(value)
    local = [item for item in catalog.values() if imdb_id(item) in ids.get(item["type"], ())]
    local_ids = {(item["type"], imdb_id(item)) for item in local}
    show_items(sorted(local, key=lambda item: item["title"].casefold())[:8] if widget
               else sorted(local, key=lambda item: item["title"].casefold()))
    remote_count = 0
    if ADDON.getSetting("tmdb_token"):
        for entry in watchlist:
            for singular, kind in (("movie", "movies"), ("show", "tv")):
                media = entry.get(singular) or {}
                identifiers = media.get("ids") or {}
                if (kind, identifiers.get("imdb")) in local_ids or not identifiers.get("tmdb"):
                    continue
                if widget and remote_count + len(local) >= 8:
                    break
                row(media["title"], action="title", kind=kind, id=identifiers["tmdb"])
                remote_count += 1
    if not local and not remote_count and not widget:
        notify("No Trakt watchlist titles match the configured catalogs.")


def stremio_locations():
    return [value for value in (ADDON.getSetting(key).strip()
            for key in ("stremio_1", "stremio_2", "stremio_3")) if value]


def show_history(widget=False):
    rows = history()
    for entry in rows[:8] if widget else rows:
        if not isinstance(entry, dict) or not isinstance(entry.get("params"), dict):
            continue
        row(entry["title"], False, dict(entry.get("art", {}), title=entry["title"]),
            **dict(entry["params"], resume=entry["position"]))
    if not rows and not widget:
        notify("Partially watched movies and episodes will appear here.")


def show_sports(items, params):
    sport = params.get("sport")
    status = params.get("status")
    widget = params.get("widget") == "1"
    now = datetime.now(timezone.utc)
    order = {"Live": 0, "Upcoming": 1, "Replay": 2}
    xbmcplugin.setPluginCategory(HANDLE, status or "Sports")
    if not sport and not status:
        for name in order:
            row("Live now" if name == "Live" else name + (" games" if name == "Upcoming" else "s"),
                action="browse", kind="sports", status=name)
        counts = Counter(item["sport_id"] for item in items)
        labels = {item["sport_id"]: item["sport"] for item in reversed(items)}
        for key in sorted(labels, key=lambda key: labels[key].casefold()):
            row("{}  ({})".format(labels[key], counts[key]), action="browse", kind="sports", sport=key)
        return
    games = [item for item in items if (not sport or item["sport_id"] == sport)
             and (not status or event_status(item, now) == status)]
    games.sort(key=lambda item: (
        order[event_status(item, now)],
        parse_time(item["starts_at"]).timestamp() * (-1 if event_status(item, now) == "Replay" else 1),
    ))
    show_items(games[:8] if widget else games, sport or True)
    if not games and not widget:
        notify("No matching games in your configured feeds.")


def external_sources(data):
    endpoint = ADDON.getSetting("lookup_endpoint").strip()
    result = cached("Lookup", (endpoint, data), lambda: lookup(endpoint, data)) if endpoint else []
    if data["kind"] == "sports" or not data.get("imdb"):
        return result
    locations = stremio_locations()
    args = data["kind"], data["imdb"], data.get("season"), data.get("episode")
    result = result + cached("Stremio", (locations, args), lambda: stremio_sources(locations, *args))
    token = ADDON.getSetting("rd_token").strip()
    if ADDON.getSetting("coco_enabled") == "true" and token:
        result += cached("Coco", (token, data), lambda: coco_cloud(data, token))
    return result


def source_label(index, source):
    parts = [str(source["height"]) + "p" if source.get("height") else "Unknown resolution"]
    if source.get("hdr") is True:
        parts.append("HDR")
    if type(source.get("bitrate_kbps")) is int and source["bitrate_kbps"] > 0:
        parts.append("{} kbps".format(source["bitrate_kbps"]))
    if type(source.get("audio_channels")) is int and source["audio_channels"] > 0:
        parts.append("{}ch".format(source["audio_channels"]))
    provider = source.get("provider")
    parts.append(provider if isinstance(provider, str) and provider else source["kind"])
    return "{}. {}".format(index, "  ·  ".join(parts))


def play(item, params):
    url = None
    canceled = False
    if item:
        cap = ADDON.getSetting("quality_cap")
        token = ADDON.getSetting("rd_token").strip()
        skipped = [value for value in params.get("skip", "").split(",") if len(value) == 20][:3]
        ranked = ranked_sources(
            item, token, skip=skipped,
            max_height=int(cap) if cap.isdecimal() else 0,
            prefer_hdr=ADDON.getSetting("prefer_hdr") == "true",
        )
        choose = params.get("choose") == "1"
        if choose and ranked:
            selection = xbmcgui.Dialog().select("Choose source", [
                source_label(index, source) for index, source in enumerate(ranked, 1)
            ])
            canceled = selection < 0
            ranked = [] if canceled else [ranked[selection]]
        candidates = playable_sources(ranked, token)
        try:
            selected = next(candidates, None)
        finally:
            candidates.close()
        if selected:
            source, url = selected
            retry = None
            if not choose and len(skipped) < 3 and len(ranked) > 1:
                retry = dict(identity(params), skip=",".join(skipped + [source_key(source)]))
                if params.get("resume"):
                    retry["resume"] = params["resume"]
            handoff({"params": identity(params), "url": url, "retry": retry, "time": time(),
                     "title": item["title"], "art": {key: item[key] for key in ("thumb", "poster", "fanart")
                                                    if is_web_url(item.get(key))}})
    if not url and not canceled:
        notify("No playable source was available.")
    entry = xbmcgui.ListItem(label=item["title"] if item else "", path=url or "")
    if url and params.get("resume", "").isdigit():
        entry.setProperty("StartOffset", params["resume"])
    xbmcplugin.setResolvedUrl(HANDLE, bool(url), entry)


def play_vod(tmdb, params, catalog):
    kind = params["kind"]
    title_id = int(params["id"])
    details = tmdb.detail(kind, title_id)
    imdb = (details.get("external_ids") or {}).get("imdb_id") or details.get("imdb_id")
    season = int(params["season"]) if kind == "tv" else None
    episode = int(params["episode"]) if kind == "tv" else None
    sources = [source for item in matches(catalog.values(), kind, title_id, imdb, season, episode)
               for source in item["sources"]]
    item = metadata(details)
    sources.extend(external_sources({"kind": kind, "id": title_id, "imdb": imdb or "",
                                     "title": item["title"], "year": item["year"] or "",
                                     "season": season or 0, "episode": episode or 0}))
    play(dict(item, sources=sources), params)


def show_vod(tmdb, params):
    action = params["action"]
    kind = params.get("kind")
    if action == "services" and not kind:
        for category in KINDS:
            row(TITLES[category], action="services", kind=category)
        row("About streaming catalogs", False, {
            "title": "About streaming catalogs",
            "thumb": "https://www.themoviedb.org/assets/v4/logos/v2/blue_square_1-5bdc75aaebeb75dc7ae79426ddd9be3b2be1e342510f8202baf6bffa71d7f5c4.svg",
        }, action="credits")
        return
    if kind not in KINDS:
        return
    if not tmdb.token:
        row("Set up streaming catalogs", False, action="settings")
        return
    if action == "services":
        xbmcplugin.setPluginCategory(HANDLE, "Streaming services · " + tmdb.region)
        for provider in tmdb.providers(kind):
            title = provider["provider_name"]
            row(title, item={"title": title, "thumb": art(provider.get("logo_path"), "w185")},
                action="discover", kind=kind, provider=provider["provider_id"])
    elif action in {"discover", "search"}:
        query_text = params.get("q")
        if action == "search" and not query_text:
            query_text = xbmcgui.Dialog().input("Search " + TITLES[kind]).strip()
            if not query_text:
                return
        page = max(1, int(params.get("page", 1)))
        provider = params.get("provider")
        result = tmdb.titles(kind, page, provider=provider, query=query_text)
        xbmcplugin.setContent(HANDLE, "movies" if kind == "movies" else "tvshows")
        for entry in result["results"]:
            if entry.get("adult"):
                continue
            item = metadata(entry)
            if not item["title"]:
                continue
            label = "{} ({})".format(item["title"], item["year"]) if item["year"] else item["title"]
            row(label, item=item, action="title", kind=kind, id=entry["id"])
        if page < result.get("total_pages", 1) and page < 500:
            more = {"action": action, "kind": kind, "page": page + 1}
            if provider:
                more["provider"] = provider
            if query_text:
                more["q"] = query_text
            row("Next page", **more)
    elif action == "title":
        title_id = int(params["id"])
        details = tmdb.detail(kind, title_id)
        item = metadata(details)
        xbmcplugin.setPluginCategory(HANDLE, item["title"])
        if kind == "movies":
            row("Play " + item["title"], False, item, action="play_vod", kind=kind, id=title_id)
        else:
            for season in details.get("seasons", []):
                row(season["name"], action="season", kind=kind, id=title_id,
                    season=season["season_number"])
        offers = (details.get("watch/providers") or {}).get("results", {}).get(tmdb.region, {})
        if offers:
            row("Where to watch · " + tmdb.region, False, action="availability",
                kind=kind, id=title_id)
    elif action == "season":
        title_id, season = int(params["id"]), int(params["season"])
        xbmcplugin.setContent(HANDLE, "episodes")
        for episode in tmdb.episodes(title_id, season):
            item = metadata(episode)
            number = episode["episode_number"]
            row("{:02d} · {}".format(number, item["title"]), False, item,
                action="play_vod", kind=kind, id=title_id, season=season, episode=number)
    elif action == "availability":
        details = tmdb.detail(kind, int(params["id"]))
        offers = (details.get("watch/providers") or {}).get("results", {}).get(tmdb.region, {})
        labels = {"flatrate": "Subscription", "free": "Free", "ads": "With ads",
                  "rent": "Rent", "buy": "Buy"}
        lines = ["{}  •  {}".format(label, ", ".join(p["provider_name"] for p in offers[key]))
                 for key, label in labels.items() if offers.get(key)]
        lines.append("Availability data from JustWatch. Open the provider's official app or website to watch.")
        xbmcgui.Dialog().textviewer("Where to watch · " + tmdb.region, "\n\n".join(lines))


def browse_catalog(items, kind, group, open_catalog):
    if kind == "movies":
        show_items(sorted(items, key=lambda item: item["title"].casefold()))
        return
    if group:
        show_items(sorted((item for item in items if item.get("series") == group),
                          key=lambda item: (str(item.get("season", "")).zfill(4),
                                            str(item.get("episode", "")).zfill(4), item["title"])))
        return
    series = sorted({item["series"] for item in items
                     if isinstance(item.get("series"), str) and item["series"]}, key=str.casefold)
    for title in series:
        row(title, action="catalog", kind=kind, group=title, open="1" if open_catalog else "0")
    show_items(sorted((item for item in items if item.get("series") not in series),
                      key=lambda item: item["title"].casefold()))


def main():
    params = {key: values[0] for key, values in parse_qs(query.lstrip("?")).items()}
    action = params.get("action", "home")
    kind = params.get("kind")
    if action == "home":
        for category in ("sports", "movies", "tv"):
            row(TITLES[category], action="browse", kind=category)
        row("Trakt watchlist", action="watchlist")
        row("Continue watching", action="history")
        row("Streaming services", action="services")
        row("Connect Trakt", False, action="connect")
        row("StreamBeast settings", False, action="settings")
    elif action == "settings":
        ADDON.openSettings()
    elif action == "history":
        show_history(params.get("widget") == "1")
    elif action == "watchlist" and params.get("widget") == "1" and not ADDON.getSetting("trakt_access"):
        pass
    elif action == "connect":
        connect_trakt(Trakt(ADDON))
    elif action == "credits":
        xbmcgui.Dialog().textviewer(
            "About streaming catalogs",
            "This product uses the TMDB API but is not endorsed or certified by TMDB.\n\n"
            "Availability data from JustWatch.\n\n"
            "Open movies by Blender Foundation. NASA Explorers episodes by NASA.",
        )
    elif action in {"services", "discover", "search", "title", "season", "availability", "play_vod"}:
        tmdb = TMDB(ADDON.getSetting("tmdb_token").strip(), ADDON.getSetting("watch_region"))
        try:
            if action == "play_vod":
                catalog, _ = load_catalog(provider_locations(include_open=True))
                play_vod(tmdb, params, catalog)
                return
            show_vod(tmdb, params)
        except NETWORK_ERRORS + (KeyError, TypeError):
            if action == "play_vod":
                play(None, params)
                return
            notify("Could not load the streaming catalog. Check your TMDB token and connection.")
    elif action == "browse" and kind in KINDS:
        xbmcplugin.setPluginCategory(HANDLE, TITLES[kind])
        row("Play from your catalogs", action="catalog", kind=kind)
        row("Free to play", action="catalog", kind=kind, open="1")
        row("Popular", action="discover", kind=kind)
        row("Search", action="search", kind=kind)
        row("Streaming services", action="services", kind=kind)
    else:
        if action == "catalog" and params.get("open") == "1":
            locations = [str(Path(__file__).parent / "resources" / "open_catalog.json")]
        else:
            locations = provider_locations(include_open=action == "watchlist" or
                                           (action == "play" and kind in KINDS))
        widget = params.get("widget") == "1"
        if kind == "sports" and action == "browse":
            # Cache only in Kodi's memory. Refresh live playback URLs on selection.
            def sports_catalog():
                entries, failed = load_catalog(locations)
                return list(entries.values()), failed
            raw, failures = cached("Sports", locations, sports_catalog, ttl=30)
            catalog = {(item["type"], item["id"]): item for item in raw}
        else:
            catalog, failures = load_catalog(locations)
        if action == "play":
            item = catalog.get((kind, params.get("id")))
            if item:
                data = {"kind": kind, "id": item["id"], "imdb": imdb_id(item) or "",
                        "title": item["title"], "year": item.get("year") or "",
                        "season": item.get("season") or 0, "episode": item.get("episode") or 0}
                if kind != "tv" or (str(data["season"]).isdigit() and str(data["episode"]).isdigit()
                                    and int(data["episode"]) > 0):
                    item["sources"].extend(external_sources(data))
            play(item, params)
            return
        if not locations and not widget:
            row("Set up a provider catalog", False, action="settings")
        elif failures and not widget:
            notify("{} provider catalog(s) unavailable.".format(failures))
        if action == "watchlist":
            show_watchlist(Trakt(ADDON), catalog, widget)
        elif action == "catalog" and kind in KINDS:
            xbmcplugin.setPluginCategory(HANDLE, params.get("group") or TITLES[kind])
            xbmcplugin.setContent(HANDLE, "movies" if kind == "movies" else "episodes")
            browse_catalog([item for item in catalog.values() if item["type"] == kind],
                           kind, params.get("group"), params.get("open") == "1")
        elif action == "browse" and kind == "sports":
            show_sports([item for item in catalog.values() if item["type"] == "sports"], params)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


if __name__ == "__main__":
    main()

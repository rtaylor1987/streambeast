# StreamBeast for Kodi

StreamBeast is a Kodi video add-on for movies, TV episodes, and sports. Its optional skin targets Kodi 21 Omega and 16:9 displays. The add-on uses Kodi 21's native video metadata APIs.

## Install

1. Run `py tools/package.py` on Windows (or `python3 tools/package.py`).
2. In Kodi, open **Add-ons → Install from zip file** and install the video add-on ZIP under `dist/plugin.video.streambeast`.
3. Install the skin ZIP under `dist/skin.streambeast`. Enable **StreamBeast Skin** under **Settings → Interface → Skin**.
4. Open **StreamBeast settings** and enter the URL or local path of each provider catalog. Open the StreamBeast video add-on to use **Connect Trakt**.

The video add-on also works independently in Kodi's Video add-ons menu. For remote distribution, run `py tools/package.py --base-url <your HTTPS repository URL>`, publish the contents of `dist/repository` at that URL, and distribute `dist/repository.streambeast-0.1.1.zip`. The repository ZIP points at that host.

## Movies, TV, and streaming services

- **Free to play** works without accounts. It includes Blender Foundation open movies and two episodes of NASA Explorers from the NASA Image and Video Library.
- For **Popular**, **Search**, and **Streaming services**, enter a TMDB API **Read Access Token** (the bearer token) under **StreamBeast settings → Movies and TV**. The availability region defaults to `US`. Enter a different two-letter country code if needed. The services list comes from TMDB and JustWatch and varies by region and media type. [TMDB API registration](https://developer.themoviedb.org/docs/getting-started)
- Selecting a discovered movie or episode looks for a matching source in your configured catalogs and Stremio providers. **Where to watch** shows the regional subscription, free, ad-supported, rental, and purchase options. Subscription services play through their own apps or websites.
- Add up to three full Stremio-style `manifest.json` URLs in the same settings section. StreamBeast requests movie streams by IMDb ID and episode streams by `IMDb-ID:season:episode`. It accepts direct HTTP(S) video URLs. Torrent entries, external-app links, DRM, and streams requiring custom proxy headers are unsupported. [Stremio stream protocol](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/protocol.md)
- When writing a catalog, an optional `tmdb` ID can match a movie or a parent TV show. For TV, supply numeric `season` and `episode` values to match discovery entries, and use `series` to group episodes in **Play from your catalogs**.

## Providers

With **The Crew** installed and enabled in Kodi, StreamBeast Sports can open The Crew's live sports directories directly. The Crew supplies its current listings, resolves streams, and handles playback. Its menus may still include provider selection. When StreamBeast has no sports catalog, the Crew sport shortcuts appear directly in Sports. If you configure a catalog, use **Sports → The Crew sports** to reach the same shortcuts. StreamBeast's own game-first ranking applies to its configured feeds.

StreamBeast accepts up to four configured JSON feeds plus an optional `catalog.json` in the add-on profile directory. Feed operators supply schedules, artwork, and playback links they are authorized to distribute. The repository includes no provider subscriptions or live feeds. Sports has **Live now**, **Upcoming games**, **Replays**, and per-sport game lists. The skin shows live events on Home.

Each catalog is an object with an `items` array. The following structural example uses placeholder URLs.

```json
{
  "items": [
    {
      "id": "league:2026-11-25:city-united",
      "type": "sports",
      "title": "City vs. United",
      "sport": "Soccer",
      "sport_id": "soccer",
      "starts_at": "2026-11-26T01:00:00Z",
      "ends_at": "2026-11-26T03:00:00Z",
      "sources": [
        {"kind": "direct", "height": 1080, "url": "https://example.com/live.m3u8"}
      ]
    },
    {
      "id": "example-movie",
      "type": "movies",
      "title": "Example Movie",
      "imdb": "tt1234567",
      "sources": [
        {"kind": "real-debrid", "height": 1080, "url": "https://example.com/file"}
      ]
    }
  ]
}
```

- Feeds covering the same game must agree on the game `id`. Set `sport_id` consistently across feeds. Start and end times require explicit offsets. Supply an end time for accurate status. If omitted, the game moves to Replays four hours after its start.
- Movies use `type: "movies"`. TV episodes use `type: "tv"` and a unique episode ID. To match the Trakt watchlist, use the IMDb ID of the movie or the parent show. Optional metadata includes `poster`, `thumb`, `fanart`, and `plot`.
- The resolver ranks candidates by advertised resolution, then by optional HDR preference, bitrate, audio channel count, and provider order. Feed authors can supply `hdr` (boolean), `bitrate_kbps` and `audio_channels` (positive integers). Direct HTTP(S) sources are probed concurrently in batches. If the first candidate fails its preflight, the resolver tries the next one. The Playback settings can exclude sources advertised above 2160p, 1080p, or 720p. Streams with unknown resolution and adaptive streams can still negotiate resolution in Kodi.
- Real-Debrid uses your API token to resolve supported hoster links marked `real-debrid`. Trakt uses your app's client ID and secret for device sign-in. Treat the Kodi add-on profile as sensitive because it stores service credentials.
- You can configure an **On-demand provider endpoint URL**. StreamBeast sends a GET request with `kind`, `id`, and `title`, plus IMDb ID and episode metadata when known. Return a JSON object with a `sources` array using the same schema as a feed. The lookup runs when you select a title or game. Successful recent lookups are cached briefly in Kodi memory.
- To use **CocoScrapers**, install the module separately, enable the providers you intend to use in its settings, enter a Real-Debrid token, and enable the StreamBeast option under **Movies and TV**. StreamBeast matches scraper results against the latest 100 items already downloaded to your Real-Debrid cloud. It uses unambiguous video matches, including episode-number checks for TV. It does not submit new torrents. [CocoScrapers repository](https://github.com/CocoJoe2411/cocojoe2411.github.io)

## Playback

Open a game, movie, or episode's Kodi context menu and select **Choose source** to override automatic ranking. Automatic playback retries up to three alternative sources if Kodi reports a startup error. A user stop cancels the retry, and errors after playback starts do not switch the stream.

**Continue watching** stores recent movie and TV resume positions locally. The skin also offers a **Trakt watchlist** row. Trakt scrobbling is not implemented.

The skin adapts Kodi Estuary from Kodi 21.2 Omega, retaining its browsing windows, settings, player UI, and license. StreamBeast supplies a new home screen and palette. [Upstream Kodi source](https://github.com/xbmc/xbmc/tree/21.2-Omega/addons/skin.estuary)

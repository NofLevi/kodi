"""Now 14, which answers only if you tell it which tenant you are.

The catalogue lives behind insight-api-shared.univtec.com, a platform that
hosts several broadcasters. It returns HTTP 500 - not 401, not 404 - to a
request that does not name its tenant, which is why this looked dead until the
headers were right. The two that matter are x-tenant-id and an origin from the
channel's own player.

A series page arrives as one document holding every season and every episode,
around 2.8 MB for a long-running programme. As with sport5, that is reduced to
the fields the UI shows before anything is cached, so browsing a programme
twice does not mean parsing three megabytes twice.

Episodes carry a direct HLS master playlist, so there is no second call to
resolve one. The CDN wants the player's Referer, which play.py appends.

Derived from the Idan Plus add-on by Fishenzon (github.com/Fishenzon/repo).
"""
from ... import cache, http, kodi, router
from ...meta import items

PLAYER = "https://vod.c14.co.il"
TENANT = "channel14"

# Sent on every call. Without x-tenant-id the platform answers 500.
HEADERS = {
    "Accept": "*/*",
    "Origin": PLAYER,
    "Referer": PLAYER + "/",
    "platform": "web",
    "x-device-type": "web",
    "x-tenant-id": TENANT,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/136.0.0.0 Safari/537.36"),
}

SERIES_TTL = 6 * 3600
MAX_EPISODES = 150


def episodes(ref, mode=""):
    """Every episode of a programme, oldest season first."""
    url = str(ref or "").strip()
    if not url.startswith("http"):
        return []

    return [_to_item(entry) for entry in _series(url)]


def _series(url, refresh=False):
    """The reduced episode list for one programme."""
    key = cache.make_key("vod", "now14", url)
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached

    payload = http.get_json(url, headers=HEADERS, timeout=(6, 20), default=None)
    if not isinstance(payload, dict):
        kodi.log("now14: %s did not answer with a series" % url.rsplit("/", 1)[-1])
        return []

    reduced = _reduce(payload)
    if reduced:
        cache.set(key, reduced, SERIES_TTL)
    return reduced


def _reduce(payload):
    """Keep the title, picture, date and stream; drop the rest of the 2.8 MB."""
    reduced = []
    for season in payload.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        season_title = season.get("title") or ""
        for episode in season.get("episodes") or []:
            if not isinstance(episode, dict):
                continue
            video = episode.get("videoUrl") or ""
            if not video.startswith("http"):
                continue
            reduced.append({
                "t": episode.get("title") or "",
                "u": video,
                "i": episode.get("image") or "",
                "d": episode.get("keywords") or "",
                "a": _date(episode.get("date")),
                "s": season_title,
            })
            if len(reduced) >= MAX_EPISODES:
                return reduced
    return reduced


def _date(stamp):
    """The API stamps an episode in milliseconds; Kodi wants YYYY-MM-DD."""
    try:
        seconds = float(stamp) / 1000.0
    except (TypeError, ValueError):
        return ""
    import time
    try:
        return time.strftime("%Y-%m-%d", time.localtime(seconds))
    except (ValueError, OSError):
        return ""


def _to_item(entry):
    image = entry.get("i") or ""
    return items.new_item(
        "vod",
        ids={"vod": entry.get("u", "")},
        title=entry.get("t") or "14",
        plot=entry.get("d") or "",
        art={"poster": image, "thumb": image},
        premiered=entry.get("a") or "",
        show_title=entry.get("s") or "",
        extra={"url": router.url_for("play_vod", module="14tv",
                                     ref=entry.get("u", "")),
               "module": "14tv", "ref": entry.get("u", "")},
    )


def stream(ref, mode=""):
    """An episode reference is already its manifest."""
    url = str(ref or "").strip()
    if not url.startswith("http"):
        return "", False
    return url, True

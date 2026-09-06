"""Israeli live television and radio.

The channel list is data, not code: name, logo, and how to turn an entry into a
playable URL. Keeping it as JSON fetched at runtime means a broadcaster moving
a stream needs a data update, not a new add-on release, which matters because
these URLs change several times a year.

The resolution strategies, in the order they are tried:

    final      the link is already playable, use it as it is
    regex      fetch the page and pull the stream URL out of it
    host       the link is a path that needs a CDN host in front
    referer    the CDN checks the Referer header before serving
    api        an endpoint returns the current URL, with link as a fallback

Channel data and stream details were derived from the Idan Plus add-on by
Fishenzon (github.com/Fishenzon/repo), which is what the Kodi POV IL build uses
for Israeli channels.
"""
import json
import os
import re

from .. import cache, http, kodi, settings
from ..meta import items

DATA_TTL = 24 * 3600
CACHE_KEY = "vod|channels"

DEFAULT_HOSTS = {
    "keshet": "https://d18b0e6mopany4.cloudfront.net",
}


def data_url():
    return settings.get("vod.channels_url", "").strip()


def seed_path():
    return os.path.join(kodi.addon_path(), "resources", "data", "channels.json")


def load():
    """The channel table, from the remote list or the bundled seed."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    data = _fetch_remote() or _read_seed()
    if data:
        cache.set(CACHE_KEY, data, DATA_TTL)
    return data or {}


def _fetch_remote():
    url = data_url()
    if not url:
        return None
    payload = http.get_json(url, timeout=(5, 12), default=None)
    if isinstance(payload, dict) and payload:
        kodi.log("loaded %d channels from the remote list" % len(payload))
        return payload
    return None


def _read_seed():
    try:
        with open(seed_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        kodi.log("no bundled channel data available")
        return {}


def refresh():
    cache.delete(CACHE_KEY)
    return load()


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


def _to_item(key, entry):
    from .. import router

    kind = entry.get("type", "tv")
    return items.new_item(
        "channel",
        ids={"channel": key},
        title=entry.get("name") or key,
        plot="",
        art={"poster": entry.get("image", ""), "thumb": entry.get("image", "")},
        extra={
            "url": router.url_for("play_channel", id=key),
            "channel_id": key,
            "kind": kind,
            "tvg_id": entry.get("tvgID", ""),
            "index": int(entry.get("index") or 999),
        },
    )


def live_channels(limit=None, kind="tv", include_broken=None):
    """Television channels, in the broadcaster ordering.

    Entries carry a "working" flag recorded by tools/check_channels.py, which
    resolves each channel and asks the stream for a few bytes. Channels known
    not to play are hidden, because a list where a third of the entries fail
    is worse than a shorter list that works. Some broadcasters sign their
    streams with a token this add-on does not mint yet, and those are the ones
    that get hidden.
    """
    if include_broken is None:
        include_broken = settings.get_bool("vod.show_broken_channels", False)

    table = load()
    entries = [(key, value) for key, value in table.items()
               if value.get("type", "tv") == kind
               and (include_broken or value.get("working", True))]
    entries.sort(key=lambda kv: (int(kv[1].get("index") or 999), kv[0]))
    result = [_to_item(key, value) for key, value in entries]
    return result[:limit] if limit else result


def hidden_count(kind="tv"):
    """How many channels are hidden because they are known not to play."""
    return sum(1 for value in load().values()
               if value.get("type", "tv") == kind and value.get("working") is False)


def radio_stations(limit=None):
    return live_channels(limit, kind="radio")


def get(channel_id):
    return load().get(channel_id)


# --------------------------------------------------------------------------
# resolving a channel to a playable URL
# --------------------------------------------------------------------------


def resolve(channel_id):
    """Return (url, headers, is_adaptive) for a channel, or ("", {}, False)."""
    entry = get(channel_id)
    if not entry:
        return "", {}, False

    details = entry.get("linkDetails") or {}
    link = details.get("link") or details.get("live") or ""
    headers = {}
    if details.get("referer"):
        headers["Referer"] = details["referer"]

    if details.get("final"):
        return link, headers, bool(details.get("adaptive"))

    if details.get("regex"):
        found = _scrape(link, details["regex"], headers)
        if found:
            return found, headers, bool(details.get("adaptive"))
        return "", headers, False

    if details.get("host") or not link.startswith("http"):
        host = details.get("host") or DEFAULT_HOSTS.get(entry.get("module"), "")
        if host:
            link = host.rstrip("/") + "/" + link.lstrip("/")

    if not link.startswith("http"):
        kodi.log("channel %s has no resolvable link" % channel_id)
        return "", headers, False

    return link, headers, bool(details.get("adaptive"))


def _scrape(url, pattern, headers):
    """Pull a stream URL out of a web page."""
    if not url:
        return ""
    response = http.get(url, headers=headers or None, timeout=(4, 8))
    if response is None or response.status_code >= 400:
        return ""
    try:
        match = re.search(pattern, response.text)
    except re.error:
        kodi.log("channel pattern did not compile: %s" % pattern)
        return ""
    if not match:
        return ""
    found = match.group(1)
    if found.startswith("//"):
        found = "https:" + found
    return found


def play_url(channel_id):
    """A URL Kodi can play, with any required headers appended."""
    url, headers, adaptive = resolve(channel_id)
    if not url:
        return "", False
    if headers:
        try:
            from urllib.parse import urlencode
        except ImportError:      # pragma: no cover
            from urllib import urlencode
        url = "%s|%s" % (url, urlencode(headers))
    return url, adaptive

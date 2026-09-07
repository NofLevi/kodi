"""Hand the next episode to the Up Next add-on.

Up Next draws the Netflix-style "next episode in 10 seconds" card. It is an
optional dependency: when it is not installed the signal simply goes nowhere,
which is why nothing here raises.

The contract is a JSON-RPC notification carrying the current and next episode,
plus a plugin URL Up Next calls when the viewer accepts.
"""
import base64
import json

import xbmc

from . import kodi, router

SENDER = "plugin.video.katan"


def notify_upnext(meta):
    """Send the current and next episode to Up Next.

    The installed check is first because working out the next episode costs up
    to two TMDB requests - the rest of this season, and then the next one -
    and without Up Next there is nothing to draw the card. That is two round
    trips per episode, on a device with a few hundred megabytes, for a signal
    with no listener. `installed()` existed for exactly this and was never
    called.
    """
    if not meta or meta.get("type") != "episode":
        return False
    if not installed():
        return False

    nxt = next_episode(meta)
    if not nxt:
        return False

    payload = {
        "current_episode": _episode_block(meta),
        "next_episode": _episode_block(nxt),
        "play_url": router.url_for("episode",
                                   tmdb=(meta.get("ids") or {}).get("tmdb"),
                                   season=nxt["season"], episode=nxt["episode"]),
    }
    return _send(payload)


def next_episode(meta):
    """The episode after this one, rolling into the next season if needed."""
    from .meta import tmdb

    tmdb_id = (meta.get("ids") or {}).get("tmdb")
    if not tmdb_id:
        return None
    season = int(meta.get("season") or 0)
    episode = int(meta.get("episode") or 0)

    episodes = tmdb.episodes(tmdb_id, season) or []
    for entry in episodes:
        if entry.get("episode") == episode + 1:
            return _from_item(entry, meta, season, episode + 1)

    # End of the season: look for the first episode of the next one.
    following = tmdb.episodes(tmdb_id, season + 1) or []
    for entry in following:
        if entry.get("episode") == 1:
            return _from_item(entry, meta, season + 1, 1)
    return None


def _from_item(item, meta, season, episode):
    return {
        "type": "episode",
        "ids": meta.get("ids") or {},
        "title": meta.get("title", ""),
        "show_title": meta.get("show_title") or meta.get("title", ""),
        "season": season,
        "episode": episode,
        "episode_title": item.get("title", ""),
        "plot": item.get("plot", ""),
        "premiered": item.get("premiered", ""),
        "rating": item.get("rating", 0),
        "art": item.get("art", {}),
        "duration": item.get("duration", 0),
    }


def _episode_block(meta):
    art = meta.get("art") or {}
    return {
        "episodeid": "%s_%s_%s" % ((meta.get("ids") or {}).get("tmdb", ""),
                                   meta.get("season"), meta.get("episode")),
        "tvshowid": (meta.get("ids") or {}).get("tmdb", ""),
        "title": meta.get("episode_title") or meta.get("title", ""),
        "showtitle": meta.get("show_title") or meta.get("title", ""),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        "plot": meta.get("plot", ""),
        "playcount": 0,
        "rating": meta.get("rating", 0),
        "firstaired": meta.get("premiered", ""),
        "runtime": meta.get("duration", 0),
        "art": {
            "thumb": art.get("thumb", ""),
            "tvshow.poster": art.get("poster", ""),
            "tvshow.fanart": art.get("fanart", ""),
        },
    }


def _send(payload):
    """Up Next expects a base64 encoded JSON blob inside a JSON-RPC notify."""
    try:
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "JSONRPC.NotifyAll",
            "params": {
                "sender": "%s.SIGNAL" % SENDER,
                "message": "upnext_data",
                "data": [encoded],
            },
        })
        xbmc.executeJSONRPC(request)
        return True
    except Exception:
        kodi.log_exception("could not signal Up Next")
        return False


def installed():
    """Is the Up Next add-on here to receive the signal?

    Through kodi.has_addon rather than its own Addon() call, because that one
    remembers the answer for the process and this is asked once per episode.
    """
    return kodi.has_addon("service.upnext")

# -*- coding: utf-8 -*-
"""Putting things into the download queue, from the plugin side.

Two shapes, and the difference matters:

A single film or episode is queued **with a chosen source**. The viewer is
looking at the picker, a download commits several gigabytes of a small disk,
and the size is right there on the row - so they choose it.

A whole season is queued **without one**. Fourteen pickers is not a feature,
and searching fourteen times would take a minute and a half with the screen
frozen, because a source search is bounded by a wall-clock deadline rather
than by how fast anything answers. So the episodes go in as intentions and
the service finds each source when it reaches it, one at a time.

Nothing here downloads anything. The queue is a file; the service reads it
within a second and does the work, which is the same arrangement the
translation request already uses - a plugin invocation is torn down the moment
it returns, so it cannot be where a two-gigabyte transfer lives.
"""
import time

from .. import kodi
from . import library

# What of a source is worth keeping. Deliberately not the whole dict: `url` is
# a signed link and must never reach the disk, and `extra` carries the entire
# TMDB item, cast included, which would be fifty kilobytes of JSON per
# episode for no reason.
SOURCE_KEYS = ("title", "hash", "provider", "quality", "size", "seeders",
               "cached", "cached_by", "group", "file_name", "file_size",
               "file_index", "file_id", "subs_mode")

# What of an item the Downloaded row needs to draw with no network at all.
ITEM_KEYS = ("type", "ids", "title", "original_title", "original_language",
             "year", "plot", "art", "rating", "votes", "mpaa", "genres",
             "duration", "premiered", "season", "episode", "show_title")


def source_for_storage(source, meta):
    """A source dict the downloader can re-resolve from, and nothing more."""
    kept = {key: source.get(key) for key in SOURCE_KEYS
            if source.get(key) is not None}
    # What `base.pick_file` reads out of meta: the runtime, to tell a film
    # from a two-minute decoy, and the numbers, to find one episode in a
    # season pack.
    kept["extra"] = {"meta": {
        "type": meta.get("type") or "movie",
        "season": int(meta.get("season") or 0),
        "episode": int(meta.get("episode") or 0),
        "absolute": int(meta.get("absolute") or 0),
        "item": {"duration": int((meta.get("item") or {}).get("duration") or 0)},
    }}
    return kept


def snapshot(item, meta=None):
    """The item as the Downloaded row will draw it, months from now."""
    kept = {key: item.get(key) for key in ITEM_KEYS if item.get(key) is not None}
    kept.setdefault("type", (meta or {}).get("type") or "movie")
    extra = {}
    source_extra = item.get("extra") or {}
    # An episode row needs its show's id to build a playable address.
    for name in ("tmdb_show", "anime", "absolute"):
        if source_extra.get(name) is not None:
            extra[name] = source_extra[name]
    if meta and meta.get("type") == "episode":
        extra.setdefault("tmdb_show", (meta.get("ids") or {}).get("tmdb"))
        kept.setdefault("show_title", meta.get("show_title") or "")
    kept["extra"] = extra
    return kept


def one(meta, source):
    """Queue a single film or episode with the source the viewer picked.

    Returns the key, or "" with the reason already on screen.
    """
    if not (source or {}).get("cached"):
        # An uncached source would have to be added to the debrid service as
        # a new torrent, and TorBox allows sixty of those an hour. One film
        # is not worth the budget, and a season would spend all of it.
        kodi.notify(kodi.localize(32639))
        return ""

    item = meta.get("item") or {}
    record = {
        "key": library.key_for(meta.get("type") or "movie", meta.get("ids"),
                               meta.get("season"), meta.get("episode")),
        "type": meta.get("type") or "movie",
        "ids": meta.get("ids") or {},
        "season": int(meta.get("season") or 0),
        "episode": int(meta.get("episode") or 0),
        "source": source_for_storage(source, meta),
        "item": snapshot(item, meta),
        "size": int(source.get("file_size") or source.get("size") or 0),
    }
    if not record["key"]:
        return ""
    key = library.queue(record)
    if not key:
        kodi.notify(kodi.localize(32640))
        return ""
    kodi.notify(kodi.localize(32634, _name(record)))
    wake()
    return key


def season(tmdb_id, season_number, episodes, show):
    """Queue every episode of one season, sources to be found later.

    `episodes` are TMDB episode items and `show` is the series item, both
    already in hand from the details screen - so this costs no network call
    of its own.
    """
    queued = 0
    for episode in episodes or []:
        if episode.get("type") != "episode":
            continue
        ids = {"tmdb": tmdb_id}
        key = library.key_for("episode", ids, episode.get("season"),
                              episode.get("episode"))
        if not key:
            continue
        item = snapshot(episode)
        item["type"] = "episode"
        item["show_title"] = show.get("title") or ""
        item.setdefault("art", show.get("art") or {})
        extra = dict(item.get("extra") or {})
        extra["tmdb_show"] = tmdb_id
        item["extra"] = extra
        if library.queue({
                "key": key,
                "type": "episode",
                "ids": ids,
                "season": int(episode.get("season") or 0),
                "episode": int(episode.get("episode") or 0),
                # No source. The service searches for one when it gets here;
                # see `downloader._source_for`.
                "source": {},
                "item": item,
                "size": 0}):
            queued += 1
    if queued:
        kodi.notify(kodi.localize(32644, queued))
        wake()
    else:
        kodi.notify(kodi.localize(32640))
    return queued


def wake():
    """Tell the service there is work, so it starts within the second.

    Only a nudge. The queue itself is the file, which is what makes it
    survive a restart - a window property is one slot and is gone the moment
    Kodi is.
    """
    kodi.set_property("offline.wake", str(int(time.time())))


def _name(record):
    item = record.get("item") or {}
    title = item.get("title") or "?"
    if record.get("type") == "episode":
        return "%s %dx%02d" % (title, int(record.get("season") or 0),
                               int(record.get("episode") or 0))
    return title

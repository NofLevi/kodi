"""From "the user pressed OK" to "Kodi has a URL".

The flow is deliberately short:

    resolve metadata -> find sources -> pick one -> resolve to a stream URL

Everything expensive happens behind a progress dialog the user can cancel, and
the source search is bounded by the shared worker pool and its deadline.
"""
from . import kodi, settings
from .ui import listing


def build_meta(request):
    """Turn route parameters into the metadata every later stage needs."""
    from .meta import tmdb

    item_type = request.get("type") or "movie"
    tmdb_id = request.get("tmdb")
    meta = {
        "type": item_type,
        "ids": {},
        "title": "",
        "year": 0,
        "season": int(request.get("season") or 0),
        "episode": int(request.get("episode") or 0),
    }

    if item_type == "movie":
        detail = tmdb.movie(tmdb_id) if tmdb_id else None
        if detail:
            meta["ids"] = detail["ids"]
            meta["title"] = detail["title"]
            meta["original_title"] = detail.get("original_title", "")
            meta["year"] = detail["year"]
            meta["art"] = detail.get("art", {})
            meta["item"] = detail
    else:
        show = tmdb.show(tmdb_id) if tmdb_id else None
        if show:
            meta["ids"] = show["ids"]
            meta["title"] = show["title"]
            meta["original_title"] = show.get("original_title", "")
            meta["year"] = show["year"]
            meta["show_title"] = show["title"]
            meta["art"] = show.get("art", {})
            episodes = tmdb.episodes(tmdb_id, meta["season"]) or []
            for episode in episodes:
                if episode.get("episode") == meta["episode"]:
                    meta["episode_title"] = episode.get("title", "")
                    meta["item"] = episode
                    meta["art"] = episode.get("art", meta["art"])
                    break

    if request.get("imdb") and not meta["ids"].get("imdb"):
        meta["ids"]["imdb"] = request["imdb"]
    return meta


def play(handle, request, force_picker=False):
    """Route entry point for playing a movie or an episode."""
    meta = build_meta(request)
    if not meta.get("title"):
        kodi.notify(kodi.localize(32280))
        listing.resolve_failed(handle)
        return

    try:
        from .sources import aggregator
    except ImportError:
        kodi.ok_dialog(kodi.localize(32281))
        listing.resolve_failed(handle)
        return

    if not settings.configured_debrid():
        kodi.ok_dialog(kodi.localize(32282))
        listing.resolve_failed(handle)
        return

    sources = aggregator.find(meta)
    if not sources:
        kodi.notify(kodi.localize(32283))
        listing.resolve_failed(handle)
        return

    chosen = _choose(sources, meta, force_picker)
    if not chosen:
        listing.resolve_failed(handle)
        return

    url = _resolve(chosen)
    if not url:
        kodi.notify(kodi.localize(32284))
        listing.resolve_failed(handle)
        return

    from . import player
    meta["source"] = {
        "group": chosen.get("group", ""),
        "provider": chosen.get("provider", ""),
        "quality": chosen.get("quality", ""),
        "release": chosen.get("title", ""),
    }
    meta["stream_url"] = url
    player.set_now_playing(meta)
    listing.resolve(handle, url, meta.get("item"))


def _choose(sources, meta, force_picker):
    """Autoplay the best source, or open the picker."""
    autoplay = settings.get_bool("sources.autoplay") and not force_picker
    if autoplay:
        return sources[0]
    try:
        from .ui.sources_window import pick_source
        return pick_source(sources, meta)
    except ImportError:
        labels = ["%s | %s | %s" % (s.get("quality", ""), _size(s), s.get("provider", ""))
                  for s in sources]
        index = kodi.select(labels, kodi.localize(32285))
        return sources[index] if index >= 0 else None


def _size(source):
    size = source.get("size") or 0
    return "%.2f GB" % (size / 1024.0 ** 3) if size else "?"


def _resolve(source):
    """Ask the debrid service that has this source cached for a stream URL."""
    from .debrid import registry
    service = source.get("cached_by") or (settings.configured_debrid() or [None])[0]
    if not service:
        return ""
    client = registry.get(service)
    if client is None:
        return ""
    try:
        return client.resolve(source) or ""
    except Exception:
        kodi.log_exception("resolving through %s failed" % service)
        return ""


def prefetch_next_episode(meta):
    """Warm the source list for the next episode, without resolving anything."""
    if meta.get("type") != "episode":
        return
    nxt = dict(meta)
    nxt["episode"] = int(meta.get("episode") or 0) + 1
    try:
        from .sources import aggregator
        aggregator.find(nxt, prefetch=True)
    except ImportError:
        return
    except Exception:
        kodi.log_exception("prefetch failed")

"""Local watched/progress mirror.

Watched ticks and resume points have to render instantly while scrolling, so
they are never fetched per item. The service keeps a small SQLite-backed
snapshot of the Trakt state and this module reads it. With no Trakt account
configured every function is a no-op, which is why the UI can call annotate()
unconditionally.
"""
from .. import cache, kodi, settings

WATCHED_KEY = "trakt|watched"
PLAYBACK_KEY = "trakt|playback"
TTL = 24 * 3600


def enabled():
    return bool(settings.get("trakt.access_token"))


def _watched_map():
    """{"movie:tmdb:123": playcount, "episode:tmdb:99:1:2": playcount}"""
    return cache.get(WATCHED_KEY) or {}


def _playback_map():
    """{"movie:tmdb:123": {"progress": percentage}}"""
    return cache.get(PLAYBACK_KEY) or {}


def store(watched=None, playback=None):
    if watched is not None:
        cache.set(WATCHED_KEY, watched, TTL)
    if playback is not None:
        cache.set(PLAYBACK_KEY, playback, TTL)


def state_key(item_type, ids, season=None, episode=None):
    tmdb_id = (ids or {}).get("tmdb")
    imdb_id = (ids or {}).get("imdb")
    base = "%s:tmdb:%s" % (item_type, tmdb_id) if tmdb_id else "%s:imdb:%s" % (item_type, imdb_id)
    if item_type == "episode" and season is not None:
        return "%s:%s:%s" % (base, season, episode)
    return base


def annotate(entries):
    """Fill playcount and resume on a list of items, in place."""
    if not entries or not enabled():
        return entries
    watched = _watched_map()
    playback = _playback_map()
    if not watched and not playback:
        return entries
    for item in entries:
        item_type = item.get("type")
        if item_type == "episode":
            key = state_key("episode", item.get("extra", {}).get("show_ids") or item.get("ids"),
                            item.get("season"), item.get("episode"))
        elif item_type in ("movie", "show"):
            key = state_key(item_type, item.get("ids"))
        else:
            continue
        if watched.get(key):
            item["playcount"] = int(watched[key])
        resume = playback.get(key)
        if resume:
            progress = resume.get("progress")
            duration = float(item.get("duration") or 0)
            if progress is not None and duration > 0:
                progress = max(0.0, min(100.0, float(progress)))
                item["resume"] = {"position": duration * progress / 100.0,
                                  "total": duration}
            elif "position" in resume:  # legacy/local second-based snapshot
                item["resume"] = resume
    return entries


def mark_watched(params):
    """Context-menu action. Updates the mirror first so the UI reacts at once."""
    item_type = params.get("type", "movie")
    ids = {"tmdb": params.get("tmdb")}
    season = params.get("season")
    episode = params.get("episode")
    key = state_key(item_type, ids, season, episode)

    watched = _watched_map()
    watched[key] = 0 if watched.get(key) else 1
    store(watched=watched)

    if not enabled():
        return
    try:
        from . import trakt
        trakt.set_watched(item_type, ids, season, episode, bool(watched[key]))
    except Exception:
        kodi.log_exception("failed to sync watched state to Trakt")


def invalidate():
    cache.delete(WATCHED_KEY)
    cache.delete(PLAYBACK_KEY)

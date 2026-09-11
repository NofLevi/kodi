"""Where an episode's intro and end credits are, so they can be skipped.

From IntroDB, which is free, needs no key and is asked by the show's IMDb id
with the season and episode - exactly what an episode's meta already carries,
because its ids are the series' ids. Measured over six shows it knew the
credits of all six and the intro of four.

Its timings were submitted against one release, and ours may be another: a
cut with a recap in front of it moves everything. Nothing here can see that,
so every segment is held to rules a real intro or credits roll keeps, and one
that breaks them is dropped rather than skipped - jumping ninety seconds into
the story is worse than not jumping at all.
"""
from . import cache, http, kodi

API = "https://api.introdb.app/segments"

# Found timings do not change once a show has aired; an empty answer may be
# filled in by the next viewer, so it is asked again sooner.
TTL_FOUND = 7 * 24 * 3600
TTL_EMPTY = 24 * 3600

MIN_SEGMENT = 5.0          # shorter than this is a mis-click, not an intro
MAX_INTRO = 180.0          # the longest anime opening runs about 1:50
MIN_CONFIDENCE = 0.5


def segments(meta):
    """{"intro": [start, end], "credits": [start, end]} for an episode.

    Either may be missing, and so may both. `end` of the credits may be None,
    which means they run to the end of the file.
    """
    if not meta or meta.get("type") != "episode":
        return {}
    imdb = str((meta.get("ids") or {}).get("imdb") or "")
    season = int(meta.get("season") or 0)
    episode = int(meta.get("episode") or 0)
    if not imdb.startswith("tt") or season < 1 or episode < 1:
        return {}

    key = cache.make_key("introdb", imdb, season, episode)
    hit = cache.get(key)
    if hit is not None:
        return hit.get("segments") or {}
    payload = _fetch(imdb, season, episode)
    if payload is None:
        return {}           # the network failed: ask again next time
    found = _parse(payload)
    cache.set(key, {"segments": found}, TTL_FOUND if found else TTL_EMPTY)
    kodi.log("introdb for %s %dx%d: %s"
             % (imdb, season, episode, ", ".join(sorted(found)) or "nothing"))
    return found


def _fetch(imdb, season, episode):
    return http.get_json(API, params={"imdb_id": imdb, "season": season,
                                      "episode": episode},
                         timeout=(5, 10), default=None)


def _parse(payload):
    """IntroDB's intro and outro, as ours; anything malformed is left out."""
    found = {}
    for theirs, ours in (("intro", "intro"), ("outro", "credits")):
        part = (payload or {}).get(theirs)
        if not isinstance(part, dict):
            continue
        try:
            start = float(part.get("start_sec"))
            end = part.get("end_sec")
            end = float(end) if end is not None else None
            confidence = float(part.get("confidence", 1.0))
        except (TypeError, ValueError):
            continue
        if confidence < MIN_CONFIDENCE:
            continue
        found[ours] = [start, end]
    return found


def usable(found, duration=0.0):
    """The segments that make sense for a file this long.

    The rules a real intro and credits roll keep: an intro is between five
    seconds and three minutes and is over before half-time; the credits start
    after half-time. A file whose length is not known yet is judged on the
    rest.
    """
    found = found or {}
    duration = float(duration or 0.0)
    out = {}

    intro = found.get("intro")
    if intro and intro[1] is not None:
        start, end = float(intro[0]), float(intro[1])
        if (start >= 0 and MIN_SEGMENT <= end - start <= MAX_INTRO
                and (not duration or end < duration * 0.5)):
            out["intro"] = (start, end)

    credits = found.get("credits")
    if credits:
        start = float(credits[0])
        end = float(credits[1]) if credits[1] is not None else None
        if duration:
            fits = duration * 0.5 < start < duration - MIN_SEGMENT
        else:
            fits = start > 0
        if fits and (end is None or end - start >= MIN_SEGMENT):
            out["credits"] = (start, end)
    return out

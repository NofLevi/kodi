"""One query, every catalog.

Search fans out to TMDB, AniList and the Israeli VOD index at the same time,
under the shared worker cap. Results keep their source grouping so the UI can
show "Movies / Shows / Anime / Israeli" without a second round trip.
"""
from .. import cache, http, kodi
from ..meta import items as meta_items

TTL = 3600
MAX_PER_SOURCE = 20


def search(query, limit=60):
    """Return a merged, de-duplicated result list for a free-text query."""
    query = (query or "").strip()
    if not query:
        return []

    key = cache.make_key("search", query.lower())
    hit = cache.get(key)
    if hit is not None:
        return hit

    tasks = [
        ("tmdb", lambda: _tmdb_search(query)),
        ("anilist", lambda: _anilist_search(query)),
        ("vod", lambda: _vod_search(query)),
    ]
    found = http.run_parallel(tasks, workers=3, deadline=8.0)

    merged = []
    for source in ("tmdb", "anilist", "vod"):
        merged.extend(found.get(source) or [])
    merged = meta_items.dedupe(merged)[:limit]
    if merged:
        cache.set(key, merged, TTL)
    return merged


def suggest(query, limit=12):
    """Fast suggestions for the search window.

    Local matches come first because they cost nothing, then whatever the
    remote catalogs return. Matching is substring, not prefix, so typing a word
    from the middle of a title still finds it.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    results = list(_local_matches(query, limit))
    remaining = limit - len(results)
    if remaining > 0:
        try:
            results.extend(_tmdb_search(query)[:remaining])
        except Exception:
            kodi.log_exception("suggestion lookup failed")
    return meta_items.dedupe(results)[:limit]


# --------------------------------------------------------------------------
# per-source adapters
# --------------------------------------------------------------------------


def _tmdb_search(query):
    from ..meta import tmdb
    if not tmdb.has_key():
        return []
    return tmdb.search(query)[:MAX_PER_SOURCE]


def _anilist_search(query):
    try:
        from ..meta import anilist
    except ImportError:
        return []
    return anilist.search(query, limit=MAX_PER_SOURCE)


def _vod_search(query):
    try:
        from ..vod import library
    except ImportError:
        return []
    return library.search(query, limit=MAX_PER_SOURCE)


# --------------------------------------------------------------------------
# the local suggestion index
# --------------------------------------------------------------------------

INDEX_KEY = "search|index"
INDEX_TTL = 24 * 3600
RECENT_KEY = "search|recent"
RECENT_MAX = 30


def _normalise(text):
    return (text or "").strip().lower()


def _local_index():
    """A flat list of (normalised title, item) pairs kept small on purpose.

    It is built from what the user has already seen: warmed catalog rows, the
    Israeli VOD series list and recent searches. A few thousand short strings
    scan in well under a millisecond, so no extra dependency is needed.
    """
    cached_index = cache.get(INDEX_KEY)
    if cached_index is not None:
        return cached_index

    from .. import catalog
    seen = {}
    for row_id in catalog.enabled_row_ids():
        for item in catalog.peek(row_id) or []:
            key = meta_items.unique_key(item)
            if key not in seen:
                seen[key] = item
    try:
        from ..vod import library
        for item in library.all_titles():
            key = meta_items.unique_key(item)
            seen.setdefault(key, item)
    except Exception:
        pass

    index = [[_normalise(i.get("title")), _normalise(i.get("original_title")), i]
             for i in seen.values()]
    cache.set(INDEX_KEY, index, INDEX_TTL)
    return index


def _local_matches(query, limit):
    needle = _normalise(query)
    starts, contains = [], []
    for title, original, item in _local_index():
        if title.startswith(needle) or original.startswith(needle):
            starts.append(item)
        elif needle in title or needle in original:
            contains.append(item)
        if len(starts) >= limit:
            break
    return (starts + contains)[:limit]


def invalidate_index():
    cache.delete(INDEX_KEY)


def recent():
    return cache.get(RECENT_KEY) or []


def remember(query):
    """Keep the last few searches so they show up as instant suggestions."""
    query = (query or "").strip()
    if not query:
        return
    history = [q for q in recent() if q.lower() != query.lower()]
    history.insert(0, query)
    cache.set(RECENT_KEY, history[:RECENT_MAX], 90 * 24 * 3600)

"""Declarative definition of the home rows.

This is the answer to "how do we show popular and new content". A Netflix home
page is nothing more than a set of ranked lists refreshed on a schedule, so
every row here is one function that returns a list of items, plus a cache TTL.

Two signals are deliberately kept apart:

* TMDB trending reflects what people are *looking up* on TMDB.
* Trakt trending reflects what people are *playing right now*.

Showing both is what makes the page feel alive rather than like one flat chart.

Adding a row is a single entry in ROWS. The user can reorder and hide rows,
and the background service warms whichever ones are enabled.
"""
from . import cache, kodi, settings
from .meta import items

# String ids from resources/language/*/strings.po
S = {
    "continue": 32200,
    "trending_movies": 32201,
    "trending_shows": 32202,
    "popular_week_movies": 32203,
    "popular_week_shows": 32204,
    "in_cinemas": 32205,
    "coming_soon": 32206,
    "airing_today": 32207,
    "returning": 32208,
    "trakt_trending_movies": 32209,
    "trakt_trending_shows": 32210,
    "anticipated": 32211,
    "box_office": 32212,
    "new_netflix": 32213,
    "israeli_movies": 32214,
    "israeli_shows": 32215,
    "anime_trending": 32216,
    "israel_live": 32217,
    "israel_vod": 32218,
    "because_you_watched": 32219,
    "top_rated_movies": 32220,
    "watchlist": 32221,
}

TTL_SHORT = 3 * 3600
TTL_MEDIUM = 6 * 3600
TTL_LONG = 24 * 3600


def _tmdb():
    from .meta import tmdb
    return tmdb


def _row(row_id, title_id, loader, ttl=TTL_MEDIUM, needs=("tmdb",), default=True):
    return {
        "id": row_id,
        "title_id": title_id,
        "loader": loader,
        "ttl": ttl,
        "needs": list(needs),
        "default": default,
    }


def _build_rows():
    """Build the row table. Loaders are lazy so no metadata module is imported
    until a row is actually rendered."""
    return [
        _row("continue", S["continue"],
             lambda: _continue_watching(), ttl=300, needs=("trakt",)),

        _row("trending_movies", S["trending_movies"],
             lambda: _tmdb().trending("movie", "day"), TTL_SHORT),
        _row("trending_shows", S["trending_shows"],
             lambda: _tmdb().trending("tv", "day"), TTL_SHORT),

        _row("popular_week_movies", S["popular_week_movies"],
             lambda: _tmdb().trending("movie", "week"), TTL_MEDIUM),
        _row("popular_week_shows", S["popular_week_shows"],
             lambda: _tmdb().trending("tv", "week"), TTL_MEDIUM),

        _row("in_cinemas", S["in_cinemas"],
             lambda: _tmdb().now_playing(), TTL_LONG),
        _row("coming_soon", S["coming_soon"],
             lambda: _tmdb().upcoming(), TTL_LONG, default=False),

        _row("airing_today", S["airing_today"],
             lambda: _tmdb().airing_today(), TTL_SHORT),
        _row("returning", S["returning"],
             lambda: _tmdb().on_the_air(), TTL_MEDIUM, default=False),

        _row("trakt_trending_movies", S["trakt_trending_movies"],
             lambda: _trakt_list("movies", "trending"), TTL_SHORT, needs=("trakt_public",)),
        _row("trakt_trending_shows", S["trakt_trending_shows"],
             lambda: _trakt_list("shows", "trending"), TTL_SHORT, needs=("trakt_public",)),
        _row("anticipated", S["anticipated"],
             lambda: _trakt_list("movies", "anticipated"), TTL_LONG,
             needs=("trakt_public",), default=False),
        _row("box_office", S["box_office"],
             lambda: _trakt_list("movies", "boxoffice"), TTL_LONG,
             needs=("trakt_public",), default=False),

        _row("new_netflix", S["new_netflix"],
             lambda: _tmdb().new_on_provider("netflix", "movie"), TTL_LONG, default=False),

        _row("israeli_movies", S["israeli_movies"],
             lambda: _tmdb().by_original_language("he", "movie"), TTL_LONG),
        _row("israeli_shows", S["israeli_shows"],
             lambda: _tmdb().by_original_language("he", "tv"), TTL_LONG),

        _row("top_rated_movies", S["top_rated_movies"],
             lambda: _tmdb().top_rated("movie"), TTL_LONG, default=False),

        _row("anime_trending", S["anime_trending"],
             lambda: _anime_trending(), TTL_SHORT, needs=("anilist",)),

        _row("israel_live", S["israel_live"],
             lambda: _israel_live(), TTL_LONG, needs=("vod",)),
        _row("israel_vod", S["israel_vod"],
             lambda: _israel_vod_new(), TTL_SHORT, needs=("vod",)),

        _row("because_you_watched", S["because_you_watched"],
             lambda: _because_you_watched(), TTL_MEDIUM, needs=("trakt",), default=False),
        _row("watchlist", S["watchlist"],
             lambda: _watchlist(), 900, needs=("trakt",)),
    ]


_ROWS = None


def rows():
    global _ROWS
    if _ROWS is None:
        _ROWS = _build_rows()
    return _ROWS


def by_id(row_id):
    for row in rows():
        if row["id"] == row_id:
            return row
    return None


# --------------------------------------------------------------------------
# which rows are on, and in what order
# --------------------------------------------------------------------------


def available(row):
    """Can this row work with the credentials the user has configured."""
    from .meta import tmdb
    for need in row["needs"]:
        if need == "tmdb" and not tmdb.has_key():
            return False
        if need == "trakt" and not settings.get("trakt.access_token"):
            return False
        if need == "anilist":
            continue          # AniList needs no key
        if need == "trakt_public":
            continue          # public Trakt lists need only the client id
        if need == "vod":
            continue          # bundled data, always available
    return True


def enabled_row_ids():
    """Ordered row ids from settings, falling back to the defaults."""
    configured = settings.get_list("ui.rows")
    if configured:
        known = {row["id"] for row in rows()}
        return [row_id for row_id in configured if row_id in known]
    return [row["id"] for row in rows() if row["default"]]


def enabled_rows():
    out = []
    for row_id in enabled_row_ids():
        row = by_id(row_id)
        if row and available(row):
            out.append(row)
    return out


def set_row_order(row_ids):
    settings.set("ui.rows", ",".join(row_ids))


# --------------------------------------------------------------------------
# loading, always through the cache
# --------------------------------------------------------------------------

def row_limit():
    """How many items to keep per row.

    Fewer items means fewer list entries and fewer artwork requests, which is
    what a small device notices. More than about twenty never gets scrolled to
    anyway.
    """
    return max(6, settings.get_int("ui.row_items", 20))


ROW_LIMIT = 20      # kept for callers that want the default


def cache_key(row_id):
    from .meta import tmdb
    return cache.make_key("row", row_id, tmdb.language(), tmdb.region())


def peek(row_id):
    """Return a warmed row without ever hitting the network."""
    return cache.get(cache_key(row_id))


def load(row_id, refresh=False):
    """Return the items for a row, fetching only when the cache is cold."""
    row = by_id(row_id)
    if row is None:
        return []
    key = cache_key(row_id)
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    try:
        with kodi.Timer("row %s" % row_id, threshold_ms=800):
            result = row["loader"]() or []
    except Exception:
        kodi.log_exception("row %s failed to load" % row_id)
        return cache.get(key) or []
    result = items.dedupe(result)[:row_limit()]
    if result:
        cache.set(key, result, row["ttl"])
    return result


def warm(row_ids=None, force=False):
    """Refresh rows in the background service. Returns how many were updated."""
    updated = 0
    for row_id in (row_ids or enabled_row_ids()):
        row = by_id(row_id)
        if row is None or not available(row):
            continue
        if not force and cache.get(cache_key(row_id)) is not None:
            continue
        if load(row_id, refresh=True):
            updated += 1
        if kodi.abort_requested():
            break
    return updated


def invalidate(row_id=None):
    if row_id:
        cache.delete(cache_key(row_id))
    else:
        cache.delete_prefix("row|")


# --------------------------------------------------------------------------
# loaders for rows whose data does not come from TMDB
#
# Each one imports its module lazily and returns an empty list when that part
# of the add-on is not built or configured yet, so a missing feature degrades
# to a hidden row rather than a broken home screen.
# --------------------------------------------------------------------------


def _continue_watching():
    try:
        from .meta import trakt
    except ImportError:
        return []
    return trakt.continue_watching(limit=ROW_LIMIT)


def _watchlist():
    try:
        from .meta import trakt
    except ImportError:
        return []
    return trakt.watchlist(limit=ROW_LIMIT)


def _trakt_list(media_type, chart):
    try:
        from .meta import trakt
    except ImportError:
        return []
    return trakt.chart(media_type, chart, limit=ROW_LIMIT)


def _because_you_watched():
    """Recommendations seeded from the last thing the user finished."""
    try:
        from .meta import trakt
    except ImportError:
        return []
    seed = trakt.last_finished()
    if not seed:
        return []
    tmdb_id = (seed.get("ids") or {}).get("tmdb")
    if not tmdb_id:
        return []
    media_type = "movie" if seed.get("type") == "movie" else "tv"
    return _tmdb().recommendations(media_type, tmdb_id)


def _anime_trending():
    try:
        from .meta import anilist
    except ImportError:
        return []
    return anilist.trending(limit=ROW_LIMIT)


def _israel_live():
    try:
        from .vod import channels
    except ImportError:
        return []
    return channels.live_channels(limit=ROW_LIMIT)


def _israel_vod_new():
    try:
        from .vod import library
    except ImportError:
        return []
    return library.newest_episodes(limit=ROW_LIMIT)


def row_title(row):
    """Localised heading for a row, with a readable fallback."""
    text = kodi.localize(row["title_id"])
    if text and text != str(row["title_id"]):
        return text
    return row["id"].replace("_", " ").title()

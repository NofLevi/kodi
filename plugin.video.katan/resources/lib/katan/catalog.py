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
    "kids_movies": 32222,
    "kids_shows": 32223,
    "kids_anime": 32224,
    "kids_israel": 32225,
}

TTL_SHORT = 3 * 3600
TTL_MEDIUM = 6 * 3600
TTL_LONG = 24 * 3600
# How long a row that came back empty is remembered as empty. Short on
# purpose: see the note in load().
TTL_EMPTY = 10 * 60


def _tmdb():
    from .meta import tmdb
    return tmdb


def _row(row_id, title_id, loader, ttl=TTL_MEDIUM, needs=("tmdb",),
         default=True, paged=True):
    """One row.

    `loader` takes a page number. `paged` says whether asking for page two is
    worth doing at all: a row built from the bundled Israeli data, or from a
    Trakt list that arrives whole, has exactly one page and asking for another
    would be a wasted round trip.
    """
    return {
        "id": row_id,
        "title_id": title_id,
        "loader": loader,
        "ttl": ttl,
        "needs": list(needs),
        "default": default,
        "paged": paged,
    }


def _build_rows():
    """Build the row table. Loaders are lazy so no metadata module is imported
    until a row is actually rendered.

    **This order is the default running order of the home screen**, and the
    home window has ten slots. That makes the order load-bearing rather than
    cosmetic: the Israeli live channels and on-demand catalogue used to sit at
    positions twelve and thirteen and were therefore never drawn at all - the
    two things this add-on exists for, cut off the end of its own front page,
    silently. They are near the top now, where an Israeli add-on should have
    put them in the first place.
    """
    return [
        _row("continue", S["continue"],
             lambda page: _continue_watching(), ttl=300, needs=("trakt",),
             paged=False),

        _row("trending_movies", S["trending_movies"],
             lambda page: _tmdb().trending("movie", "day", page), TTL_SHORT),
        _row("trending_shows", S["trending_shows"],
             lambda page: _tmdb().trending("tv", "day", page), TTL_SHORT),

        # The Israeli half, high up. Neither needs a key of any kind.
        _row("israel_live", S["israel_live"],
             lambda page: _israel_live(), TTL_LONG, needs=("vod",),
             paged=False),
        _row("israel_vod", S["israel_vod"],
             lambda page: _israel_vod_new(), TTL_SHORT, needs=("vod",),
             paged=False),

        _row("israeli_movies", S["israeli_movies"],
             lambda page: _tmdb().by_original_language("he", "movie", page),
             TTL_LONG),
        _row("israeli_shows", S["israeli_shows"],
             lambda page: _tmdb().by_original_language("he", "tv", page),
             TTL_LONG),

        _row("popular_week_movies", S["popular_week_movies"],
             lambda page: _tmdb().trending("movie", "week", page), TTL_MEDIUM),
        _row("popular_week_shows", S["popular_week_shows"],
             lambda page: _tmdb().trending("tv", "week", page), TTL_MEDIUM),

        _row("in_cinemas", S["in_cinemas"],
             lambda page: _tmdb().now_playing(page), TTL_LONG),
        _row("coming_soon", S["coming_soon"],
             lambda page: _tmdb().upcoming(page), TTL_LONG, default=False),

        _row("airing_today", S["airing_today"],
             lambda page: _tmdb().airing_today(page), TTL_SHORT),
        _row("returning", S["returning"],
             lambda page: _tmdb().on_the_air(page), TTL_MEDIUM, default=False),

        _row("anime_trending", S["anime_trending"],
             lambda page: _anime_trending(page), TTL_SHORT, needs=("anilist",)),

        # Below the fold by default. These need a Trakt client id to return
        # anything at all, and without one they were spending two of the ten
        # slots on nothing.
        _row("trakt_trending_movies", S["trakt_trending_movies"],
             lambda page: _trakt_list("movies", "trending"), TTL_SHORT,
             needs=("trakt_public",), paged=False),
        _row("trakt_trending_shows", S["trakt_trending_shows"],
             lambda page: _trakt_list("shows", "trending"), TTL_SHORT,
             needs=("trakt_public",), paged=False),
        _row("anticipated", S["anticipated"],
             lambda page: _trakt_list("movies", "anticipated"), TTL_LONG,
             needs=("trakt_public",), default=False, paged=False),
        _row("box_office", S["box_office"],
             lambda page: _trakt_list("movies", "boxoffice"), TTL_LONG,
             needs=("trakt_public",), default=False, paged=False),

        _row("new_netflix", S["new_netflix"],
             lambda page: _tmdb().new_on_provider("netflix", "movie", page),
             TTL_LONG, default=False),

        _row("top_rated_movies", S["top_rated_movies"],
             lambda page: _tmdb().top_rated("movie", page), TTL_LONG,
             default=False),

        _row("because_you_watched", S["because_you_watched"],
             lambda page: _because_you_watched(page), TTL_MEDIUM,
             needs=("trakt",), default=False),
        _row("watchlist", S["watchlist"],
             lambda page: _watchlist(), 900, needs=("trakt",), paged=False),

        # Kids mode rows. These are never in the default set: kids.rows_allowed
        # swaps the whole row list for them when the mode is on. They ask TMDB
        # for a certification ceiling rather than filtering afterwards, which
        # is what makes them safe on list data that carries no rating.
        _row("kids_movies", S["kids_movies"],
             lambda page: _kids_discover("movie", page), TTL_LONG,
             default=False),
        _row("kids_shows", S["kids_shows"],
             lambda page: _kids_discover("tv", page), TTL_LONG, default=False),
        _row("kids_anime", S["kids_anime"],
             lambda page: _kids_anime(page), TTL_LONG, default=False),
        _row("kids_israel", S["kids_israel"],
             lambda page: _kids_israel(), TTL_LONG, needs=("vod",),
             default=False, paged=False),
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
    """Ordered row ids from settings, falling back to the defaults.

    Kids mode replaces the list rather than filtering it, so a row that cannot
    express a certification ceiling is never offered while it is on.
    """
    from . import kids

    if kids.enabled():
        return list(kids.ROW_IDS)

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


def cache_key(row_id, page=1):
    from .meta import tmdb
    # Page one keeps the key it has always had, so an upgrade does not throw
    # away every warmed row.
    parts = ["row", row_id, tmdb.language(), tmdb.region()]
    if page > 1:
        parts.append("p%d" % page)
    return cache.make_key(*parts)


def peek(row_id):
    """Return a warmed row without ever hitting the network.

    None means "not warmed yet" and is the signal the home window uses to fall
    back to a live fetch. It has to stay distinct from an empty list, because
    collapsing the two turns a cold cache into a row that is permanently empty
    and never retried.
    """
    from . import kids
    cached = cache.get(cache_key(row_id))
    if cached is None:
        return None
    return kids.filter_items(cached)


def has_more(row_id):
    """Could this row go on past its first page?

    A row built from the bundled Israeli data, or from a Trakt chart that
    arrives whole, has exactly one page: asking for a second is a round trip
    that can only come back empty.
    """
    row = by_id(row_id)
    return bool(row and row.get("paged"))


def load(row_id, refresh=False, page=1):
    """Return one page of a row, fetching only when the cache is cold.

    Page one is what everything has always asked for and behaves exactly as it
    did. Later pages exist so a row can grow as the viewer scrolls along it
    rather than arriving all at once - twelve posters is what a row costs to
    show, not what the row has to contain.
    """
    row = by_id(row_id)
    if row is None:
        return []
    if page > 1 and not row.get("paged"):
        return []
    key = cache_key(row_id, page)
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    try:
        with kodi.Timer("row %s page %d" % (row_id, page), threshold_ms=800):
            result = row["loader"](page) or []
    except Exception:
        kodi.log_exception("row %s page %d failed to load" % (row_id, page))
        return cache.get(key) or []
    result = items.dedupe(result)[:row_limit()]
    if result:
        cache.set(key, result, row["ttl"])
    else:
        # An empty answer is remembered briefly, and briefly is the whole
        # point. Not remembering it at all left peek() unable to tell "never
        # warmed" from "warmed and empty", which is the distinction the home
        # listing needs to stop offering a row that opens an empty screen -
        # the anime row has been in that state for as long as AniList has been
        # refusing requests. Remembering it for the row's full TTL would be
        # worse: a service that came back in five minutes would stay hidden
        # for a day. Ten minutes is short enough to be a hiccup and long
        # enough to be useful - and never longer than the row's own TTL, or a
        # fast-moving row like continue-watching would remember "nothing here"
        # for longer than it would have remembered something.
        cache.set(key, [], min(TTL_EMPTY, row["ttl"]))
    # Applied after the cache, not before, so turning kids mode on takes effect
    # on rows that were warmed while it was off.
    from . import kids
    return kids.filter_items(result)


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


def _because_you_watched(page=1):
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
    return _tmdb().recommendations(media_type, tmdb_id, page)


def _anime_trending(page=1):
    try:
        from .meta import anilist
    except ImportError:
        return []
    return anilist.trending(limit=ROW_LIMIT, page=page)


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


# TMDB genre ids. Family and Animation for film, Kids and Family for
# television, which is what "safe by construction" means here.
_KIDS_GENRES = {"movie": "10751,16", "tv": "10762,10751"}


def _kids_discover(media_type, page=1):
    """Family titles under the configured certification ceiling.

    The ceiling is applied by TMDB rather than by filtering afterwards. That
    matters because a list result carries no certification of its own, so a
    local filter would have nothing to work with.
    """
    from . import kids

    filters = {
        "with_genres": _KIDS_GENRES[media_type],
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "vote_count.gte": 50,
    }
    if media_type == "movie":
        filters["certification_country"] = "US"
        filters["certification.lte"] = kids.ceiling()

    found = _tmdb().discover(media_type, page=page, **filters)
    # Second line of defence: anything that did arrive with genres attached is
    # still checked, so a mislabelled title does not ride in on the row.
    return kids.filter_items(found)


def _kids_anime(page=1):
    """Animation that is actually for children, not animation in general."""
    from . import kids

    found = _tmdb().discover(
        "tv", page=page, with_genres="16,10762", sort_by="popularity.desc",
        include_adult="false", **{"vote_count.gte": 20})
    return kids.filter_items(found)


# A channel's name names its audience: "כאן ילדים" is a children's channel.
# Both scripts, because the channel list is titled in Hebrew and an
# English-only list was matching on the key alone - one entry in eighty-four,
# and nothing at all if that key were ever renamed.
KIDS_CHANNEL_WORDS = ("kids", "luli", "junior", "baby",
                      "ילדים",       # children
                      "פעוטות", # toddlers
                      "לולי",             # Luli
                      "ג'וניור")  # Junior

# A broadcaster's own children's section. This is the only signal used for
# on-demand programmes, and the reason is worth stating: a programme's *name*
# describes its subject, not its audience. Matching names put "לא לפני
# הילדים", "מחפשת תשובה - חינוך ילדים" and "הילדים האבודים" into a children's
# row - three adult programmes about children.
KIDS_CATEGORY_WORDS = ("kids", "ילדים", "פעוטות")


def _kids_israel():
    """Israeli children's television, live and on demand.

    Live first, then the broadcasters' own children's sections, because the
    live list is short: of eighty-four channels exactly one is a children's
    channel, and it is a DASH stream, so on a device without
    inputstream.adaptive the row was empty altogether. Kan alone publishes a
    children's category with twenty programmes in it.

    Note what is *not* matched on. An earlier version included "הופ" for the
    Hop! channel, and Hebrew substring matching turned that into "הופעה"
    (performance) and "הופקר" (abandoned) - which is how a documentary about
    7 October found its way into a row for small children. A three-letter
    substring is not a word, and a kids row is the wrong place to learn that.
    """
    found = []
    try:
        from .vod import channels
    except ImportError:
        channels = None
    if channels is not None:
        for channel in (channels.live_channels(kind="tv")
                        + channels.radio_stations()):
            key = (channel.get("ids") or {}).get("channel", "").lower()
            title = (channel.get("title") or "").lower()
            if any(word in key or word in title
                   for word in KIDS_CHANNEL_WORDS):
                found.append(channel)

    try:
        from .vod import library
    except ImportError:
        return found[:ROW_LIMIT]
    for entry in library.load():
        category = (entry.get("c") or "").lower()
        if not category:
            continue
        if any(word in category for word in KIDS_CATEGORY_WORDS):
            found.append(library._to_item(entry))
        if len(found) >= ROW_LIMIT:
            break
    return found[:ROW_LIMIT]


def row_title(row):
    """Localised heading for a row, with a readable fallback."""
    text = kodi.localize(row["title_id"])
    if text and text != str(row["title_id"]):
        return text
    return row["id"].replace("_", " ").title()

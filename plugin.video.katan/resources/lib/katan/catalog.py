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

    # The sections, and the rows that only exist to fill them.
    "section_home": 32420,
    "section_movies": 32421,
    "section_shows": 32422,
    "section_live": 32423,

    "top_rated_shows": 32424,
    "israel_radio": 32425,
    "movies_action": 32426,
    "movies_comedy": 32427,
    "movies_drama": 32428,
    "movies_thriller": 32429,
    "movies_scifi": 32430,
    "movies_horror": 32431,
    "movies_animation": 32432,
    "movies_documentary": 32433,
    "shows_drama": 32434,
    "shows_comedy": 32435,
    "shows_crime": 32436,
    "shows_scifi": 32437,
    "shows_documentary": 32438,
    "shows_reality": 32439,

    # Topics, which are what a tab is actually for: new, popular, classics,
    # rather than eighteen genres and nothing to browse by.
    "movies_new": 32440,
    "movies_popular": 32441,
    "movies_classics": 32442,
    "movies_gems": 32443,
    "movies_blockbusters": 32444,
    "movies_nineties": 32445,
    "movies_eighties": 32446,
    "shows_new": 32447,
    "shows_popular": 32448,
    "shows_classics": 32449,
    "shows_gems": 32450,
}

# --------------------------------------------------------------------------
# sections
#
# The home screen used to be one long mixed list: films, series, live
# channels and Israeli VOD interleaved, ten rows of it, and no way to say "I
# want a film". These are the tabs down the left-hand side. A row can belong
# to more than one, so the mixed view survives as the "home" section rather
# than being thrown away.
# --------------------------------------------------------------------------

HOME = "home"
MOVIES = "movies"
SHOWS = "shows"
LIVE = "live"

# What the rail offers, in order. There is deliberately no "Home" entry: a
# mixed section of everything is what the rail exists to replace, and a button
# called Home next to Films, Series and Live TV does not say what it would
# show. `HOME` survives as the row set the plain directory listing and the
# background service use - "every row that is switched on" - which is a
# different question from "which tab am I looking at".
SECTIONS = [
    {"id": MOVIES, "title_id": S["section_movies"]},
    {"id": SHOWS, "title_id": S["section_shows"]},
    {"id": LIVE, "title_id": S["section_live"]},
]

# Where the window opens. Films rather than Live TV because it is the larger
# catalogue, and the channels are one clearly labelled button away.
DEFAULT_SECTION = MOVIES


# The running order of each tab. This is an editorial decision and belongs in
# one visible list, not implied by the order rows happen to be declared in:
# the tab is what somebody sees when they say "show me films", and it should
# open on what is new and popular rather than on whatever was defined first.
# Rows in a section but missing from its order follow, in table order.
SECTION_ORDER = {
    MOVIES: [
        "continue", "because_you_watched",
        "trending_movies", "movies_new", "movies_popular",
        "top_rated_movies", "movies_classics", "movies_gems",
        "movies_blockbusters", "israeli_movies", "coming_soon",
        "movies_nineties", "movies_eighties",
        "movies_action", "movies_comedy", "movies_drama", "movies_thriller",
        "movies_scifi", "movies_horror", "movies_animation",
        "movies_documentary",
    ],
    SHOWS: [
        "continue", "because_you_watched",
        "trending_shows", "shows_new", "airing_today", "shows_popular",
        "top_rated_shows", "shows_classics", "shows_gems", "returning",
        "israeli_shows", "anime_trending",
        "shows_drama", "shows_comedy", "shows_crime", "shows_scifi",
        "shows_documentary", "shows_reality",
    ],
    LIVE: [
        "israel_live", "israel_radio", "israel_vod",
        # the per-broadcaster rows follow, in the catalogue's own order
    ],
}


def section_ids():
    return [section["id"] for section in SECTIONS]


def section_title(section_id):
    for section in SECTIONS:
        if section["id"] == section_id:
            text = kodi.localize(section["title_id"])
            if text and text != str(section["title_id"]):
                return text
            return section_id.title()
    return section_id.title()


# TMDB genre ids, which are stable and documented. Film and television use
# different sets - television has no "Action", it has "Action & Adventure" -
# so they are kept apart rather than shared and fudged.
GENRE_MOVIE = {
    "action": 28, "comedy": 35, "drama": 18, "thriller": 53,
    "scifi": 878, "horror": 27, "animation": 16, "documentary": 99,
}
GENRE_TV = {
    "drama": 18, "comedy": 35, "crime": 80, "scifi": 10765,
    "documentary": 99, "reality": 10764,
}


def _by_genre(media_type, genre_id, page):
    """One TMDB discover call, most popular first."""
    return _tmdb().discover(media_type, page=page, with_genres=str(genre_id),
                            sort_by="popularity.desc")


# --------------------------------------------------------------------------
# topics
#
# A tab wants topics - new, popular, classics - more than it wants a wall of
# genres. Each of these is one discover call with the filters that make the
# label true, and the vote-count floors are the important part: without them
# "top rated" is a film four people have seen and scored ten, and "classics"
# is an obscure 1974 short rather than anything anybody would call a classic.
# --------------------------------------------------------------------------

# Television uses first_air_date where film uses primary_release_date, and
# there is no third option: asking TMDB for the wrong one is not an error, it
# is silently ignored, and the row comes back unfiltered.
_DATE_FIELD = {"movie": "primary_release_date", "tv": "first_air_date"}

VOTES_MAINSTREAM = 300      # enough people to mean the score is real
VOTES_CLASSIC = 800         # a classic is by definition widely seen
VOTES_GEM_MIN = 80          # a gem is well liked...
VOTES_GEM_MAX = 900         # ...and not already famous
CLASSIC_BEFORE = "1996-01-01"


# How long after release a film is likely to exist as something other than a
# camera recording. Not a guess: the picker's own log for a film still in
# cinemas read "64 found, 8 kept, dropped 40 cam release" - two thirds of
# everything on offer was somebody's phone pointed at a screen. A "new films"
# row full of titles whose only sources are cams is worse than no row.
DIGITAL_WINDOW_DAYS = 45


def _newest(media_type, page):
    """Recently out, and out for long enough to be worth opening.

    For film this deliberately excludes what is still in cinemas. That is also
    what stops it being a copy of the "in cinemas" row, which is the other
    half of the same complaint: sorting every film by release date and
    sorting the ones currently in cinemas gives two rows of the same titles.
    """
    field = _DATE_FIELD[media_type]
    newest_allowed = _days_ago(DIGITAL_WINDOW_DAYS
                               if media_type == "movie" else 0)
    return _tmdb().discover(
        media_type, page=page, sort_by="%s.desc" % field,
        **{"%s.lte" % field: newest_allowed, "vote_count.gte": 20})


def _popular(media_type, page):
    return _tmdb().popular(media_type, page)


def _classics(media_type, page):
    field = _DATE_FIELD[media_type]
    return _tmdb().discover(
        media_type, page=page, sort_by="vote_average.desc",
        **{"%s.lte" % field: CLASSIC_BEFORE,
           "vote_count.gte": VOTES_CLASSIC})


def _hidden_gems(media_type, page):
    """Well rated, not widely seen. The upper bound is what makes it a gem."""
    return _tmdb().discover(
        media_type, page=page, sort_by="vote_average.desc",
        **{"vote_average.gte": 7.2, "vote_count.gte": VOTES_GEM_MIN,
           "vote_count.lte": VOTES_GEM_MAX})


def _blockbusters(page):
    """Highest grossing. Film only - TMDB has no revenue for television."""
    return _tmdb().discover("movie", page=page, sort_by="revenue.desc",
                            **{"vote_count.gte": VOTES_MAINSTREAM})


def _decade(media_type, first_year, page):
    field = _DATE_FIELD[media_type]
    return _tmdb().discover(
        media_type, page=page, sort_by="popularity.desc",
        **{"%s.gte" % field: "%d-01-01" % first_year,
           "%s.lte" % field: "%d-12-31" % (first_year + 9),
           "vote_count.gte": VOTES_MAINSTREAM})


def _today():
    import time
    return time.strftime("%Y-%m-%d")


def _days_ago(days):
    import time
    return time.strftime("%Y-%m-%d", time.localtime(time.time()
                                                    - days * 86400))

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
         default=True, paged=True, sections=(HOME,)):
    """One row.

    `loader` takes a page number. `paged` says whether asking for page two is
    worth doing at all: a row built from the bundled Israeli data, or from a
    Trakt list that arrives whole, has exactly one page and asking for another
    would be a wasted round trip.

    `sections` is which tabs this row appears under, and `default` means "on
    the mixed home tab out of the box". The two are separate on purpose: a
    genre row belongs in Films but would crowd out the mixed view, so it has
    a section and no default.
    """
    return {
        "id": row_id,
        "title_id": title_id,
        "loader": loader,
        "ttl": ttl,
        "needs": list(needs),
        "default": default,
        "paged": paged,
        "sections": list(sections),
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
        # The top two rows, and they are personal ones. Both need Trakt, and
        # both hide themselves without it - `available` drops them when there
        # is no token, and an empty result hides the row - so on an install
        # with no account the screen simply starts at what is trending, with
        # no gap where they would have been.
        _row("continue", S["continue"],
             lambda page: _continue_watching(), ttl=300, needs=("trakt",),
             paged=False, sections=(HOME, MOVIES, SHOWS)),
        _row("because_you_watched", S["because_you_watched"],
             lambda page: _because_you_watched(page), TTL_MEDIUM,
             needs=("trakt",), sections=(HOME, MOVIES, SHOWS)),

        _row("trending_movies", S["trending_movies"],
             lambda page: _tmdb().trending("movie", "day", page), TTL_SHORT,
             sections=(HOME, MOVIES)),
        _row("trending_shows", S["trending_shows"],
             lambda page: _tmdb().trending("tv", "day", page), TTL_SHORT,
             sections=(HOME, SHOWS)),

        # The Israeli half, high up. Neither needs a key of any kind.
        _row("israel_live", S["israel_live"],
             lambda page: _israel_live(page), TTL_LONG, needs=("vod",),
             sections=(HOME, LIVE)),
        _row("israel_vod", S["israel_vod"],
             lambda page: _israel_vod_new(page), TTL_SHORT, needs=("vod",),
             sections=(HOME, LIVE)),

        _row("israeli_movies", S["israeli_movies"],
             lambda page: _tmdb().by_original_language("he", "movie", page),
             TTL_LONG, sections=(HOME, MOVIES)),
        _row("israeli_shows", S["israeli_shows"],
             lambda page: _tmdb().by_original_language("he", "tv", page),
             TTL_LONG, sections=(HOME, SHOWS)),

        # "Trending this week" and "Popular" are the same films in a slightly
        # different order, and having both on one tab reads as a mistake -
        # which is what it was. They stay on the mixed home view, where the
        # tab's own "popular" row is not next to them.
        _row("popular_week_movies", S["popular_week_movies"],
             lambda page: _tmdb().trending("movie", "week", page), TTL_MEDIUM,
             sections=(HOME,)),
        _row("popular_week_shows", S["popular_week_shows"],
             lambda page: _tmdb().trending("tv", "week", page), TTL_MEDIUM,
             sections=(HOME,)),

        # Home only. On the Films tab it sat beside "new releases" showing
        # much the same titles, and it is the row most likely to lead to a
        # film whose only sources are camera recordings.
        _row("in_cinemas", S["in_cinemas"],
             lambda page: _tmdb().now_playing(page), TTL_LONG,
             sections=(HOME,)),
        _row("coming_soon", S["coming_soon"],
             lambda page: _tmdb().upcoming(page), TTL_LONG, default=False,
             sections=(MOVIES,)),

        _row("airing_today", S["airing_today"],
             lambda page: _tmdb().airing_today(page), TTL_SHORT,
             sections=(HOME, SHOWS)),
        _row("returning", S["returning"],
             lambda page: _tmdb().on_the_air(page), TTL_MEDIUM, default=False,
             sections=(SHOWS,)),

        _row("anime_trending", S["anime_trending"],
             lambda page: _anime_trending(page), TTL_SHORT, needs=("anilist",),
             sections=(HOME, SHOWS)),

        # Below the fold by default. These need a Trakt client id to return
        # anything at all, and without one they were spending two of the ten
        # slots on nothing.
        # Trakt rows stay on the mixed home view only. They need a client id
        # nobody has yet, and a tab whose first four rows are empty is worse
        # than a tab that does not offer them.
        _row("trakt_trending_movies", S["trakt_trending_movies"],
             lambda page: _trakt_list("movies", "trending"), TTL_SHORT,
             needs=("trakt_public",), paged=False, sections=(HOME,)),
        _row("trakt_trending_shows", S["trakt_trending_shows"],
             lambda page: _trakt_list("shows", "trending"), TTL_SHORT,
             needs=("trakt_public",), paged=False, sections=(HOME,)),
        _row("anticipated", S["anticipated"],
             lambda page: _trakt_list("movies", "anticipated"), TTL_LONG,
             needs=("trakt_public",), default=False, paged=False,
             sections=(HOME,)),
        _row("box_office", S["box_office"],
             lambda page: _trakt_list("movies", "boxoffice"), TTL_LONG,
             needs=("trakt_public",), default=False, paged=False,
             sections=(HOME,)),

        _row("new_netflix", S["new_netflix"],
             lambda page: _tmdb().new_on_provider("netflix", "movie", page),
             TTL_LONG, default=False, sections=(HOME,)),

        _row("top_rated_movies", S["top_rated_movies"],
             lambda page: _tmdb().top_rated("movie", page), TTL_LONG,
             default=False, sections=(MOVIES,)),
        _row("top_rated_shows", S["top_rated_shows"],
             lambda page: _tmdb().top_rated("tv", page), TTL_LONG,
             default=False, sections=(SHOWS,)),

        _row("watchlist", S["watchlist"],
             lambda page: _watchlist(), 900, needs=("trakt",), paged=False,
             sections=(HOME,)),

        # --- the Films tab ------------------------------------------------
        # Topics first, then genres. These are all off the mixed home view on
        # purpose: twenty more rows there would bury everything else, which is
        # the problem the tabs were added to solve.
        _row("movies_new", S["movies_new"],
             lambda page: _newest("movie", page), TTL_MEDIUM,
             default=False, sections=(MOVIES,)),
        _row("movies_popular", S["movies_popular"],
             lambda page: _popular("movie", page), TTL_MEDIUM,
             default=False, sections=(MOVIES,)),
        _row("movies_classics", S["movies_classics"],
             lambda page: _classics("movie", page), TTL_LONG,
             default=False, sections=(MOVIES,)),
        _row("movies_gems", S["movies_gems"],
             lambda page: _hidden_gems("movie", page), TTL_LONG,
             default=False, sections=(MOVIES,)),
        _row("movies_blockbusters", S["movies_blockbusters"],
             lambda page: _blockbusters(page), TTL_LONG,
             default=False, sections=(MOVIES,)),
        _row("movies_nineties", S["movies_nineties"],
             lambda page: _decade("movie", 1990, page), TTL_LONG,
             default=False, sections=(MOVIES,)),
        _row("movies_eighties", S["movies_eighties"],
             lambda page: _decade("movie", 1980, page), TTL_LONG,
             default=False, sections=(MOVIES,)),

        _row("movies_action", S["movies_action"],
             lambda page: _by_genre("movie", GENRE_MOVIE["action"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_comedy", S["movies_comedy"],
             lambda page: _by_genre("movie", GENRE_MOVIE["comedy"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_drama", S["movies_drama"],
             lambda page: _by_genre("movie", GENRE_MOVIE["drama"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_thriller", S["movies_thriller"],
             lambda page: _by_genre("movie", GENRE_MOVIE["thriller"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_scifi", S["movies_scifi"],
             lambda page: _by_genre("movie", GENRE_MOVIE["scifi"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_horror", S["movies_horror"],
             lambda page: _by_genre("movie", GENRE_MOVIE["horror"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_animation", S["movies_animation"],
             lambda page: _by_genre("movie", GENRE_MOVIE["animation"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),
        _row("movies_documentary", S["movies_documentary"],
             lambda page: _by_genre("movie", GENRE_MOVIE["documentary"], page),
             TTL_LONG, default=False, sections=(MOVIES,)),

        # --- the Series tab -----------------------------------------------
        _row("shows_new", S["shows_new"],
             lambda page: _newest("tv", page), TTL_MEDIUM,
             default=False, sections=(SHOWS,)),
        _row("shows_popular", S["shows_popular"],
             lambda page: _popular("tv", page), TTL_MEDIUM,
             default=False, sections=(SHOWS,)),
        _row("shows_classics", S["shows_classics"],
             lambda page: _classics("tv", page), TTL_LONG,
             default=False, sections=(SHOWS,)),
        _row("shows_gems", S["shows_gems"],
             lambda page: _hidden_gems("tv", page), TTL_LONG,
             default=False, sections=(SHOWS,)),

        _row("shows_drama", S["shows_drama"],
             lambda page: _by_genre("tv", GENRE_TV["drama"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),
        _row("shows_comedy", S["shows_comedy"],
             lambda page: _by_genre("tv", GENRE_TV["comedy"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),
        _row("shows_crime", S["shows_crime"],
             lambda page: _by_genre("tv", GENRE_TV["crime"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),
        _row("shows_scifi", S["shows_scifi"],
             lambda page: _by_genre("tv", GENRE_TV["scifi"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),
        _row("shows_documentary", S["shows_documentary"],
             lambda page: _by_genre("tv", GENRE_TV["documentary"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),
        _row("shows_reality", S["shows_reality"],
             lambda page: _by_genre("tv", GENRE_TV["reality"], page),
             TTL_LONG, default=False, sections=(SHOWS,)),

        # --- the Live tab -------------------------------------------------
        # One row per broadcaster is built below rather than listed here,
        # because the broadcasters come from the bundled catalogue and their
        # names are already Hebrew in the data.
        _row("israel_radio", S["israel_radio"],
             lambda page: _israel_radio(page), TTL_LONG, needs=("vod",),
             default=False, sections=(LIVE,)),
    ] + [

        # Kids mode rows. These are never in the default set: kids.rows_allowed
        # swaps the whole row list for them when the mode is on. They ask TMDB
        # for a certification ceiling rather than filtering afterwards, which
        # is what makes them safe on list data that carries no rating.
        _kids(_row("kids_movies", S["kids_movies"],
                   lambda page: _kids_discover("movie", page), TTL_LONG,
                   default=False, sections=(HOME, MOVIES))),
        _kids(_row("kids_shows", S["kids_shows"],
                   lambda page: _kids_discover("tv", page), TTL_LONG,
                   default=False, sections=(HOME, SHOWS))),
        _kids(_row("kids_anime", S["kids_anime"],
                   lambda page: _kids_anime(page), TTL_LONG, default=False,
                   sections=(HOME, SHOWS))),
        _kids(_row("kids_israel", S["kids_israel"],
                   lambda page: _kids_israel(), TTL_LONG, needs=("vod",),
                   default=False, paged=False, sections=(HOME, LIVE))),
    ]


def _kids(row):
    """Mark a row as belonging to kids mode and nowhere else.

    Without this the kid-safe rows appeared on the Films and Series tabs with
    the mode switched off - not a safety problem, they are only films, but a
    grown-up browsing films was being shown a children's row for no reason.
    """
    row["kids_only"] = True
    return row


def _broadcaster_rows():
    """One Live-tab row per Israeli broadcaster in the bundled catalogue.

    Built rather than listed because the catalogue is data: adding a
    broadcaster to it should not need a code change here, and the names are
    already Hebrew in the file, so they need no string ids either. The title
    comes back through `row_title`, which prefers a literal `title` when a row
    carries one.
    """
    try:
        from .vod import library
    except ImportError:
        return []
    try:
        modules = library.modules()
    except Exception:
        kodi.log_exception("could not list the VOD broadcasters")
        return []

    out = []
    for module, _count in modules:
        if not module:
            continue
        row = _row("israel_vod_%s" % module, 0,
                   lambda page, m=module: _broadcaster(m, page), TTL_SHORT,
                   needs=("vod",), default=False, sections=(LIVE,))
        row["title"] = library.MODULE_NAMES.get(module, module)
        out.append(row)
    return out


_ROWS = None
_HAVE_BROADCASTERS = False


def rows():
    """The row table, built once.

    The broadcaster rows are appended separately and retried until they
    arrive, rather than being built with the rest. They are the only rows that
    come from data rather than from this file, so they are the only ones that
    can fail to exist: the bundled catalogue has to be readable, which means
    the cache and the profile directory have to be there. Folding them into
    the one-time build meant a single early failure - the service starting
    before the profile did, say - removed the whole Live tab for the life of
    the process, silently and permanently.
    """
    global _ROWS, _HAVE_BROADCASTERS
    if _ROWS is None:
        _ROWS = _build_rows()
    if not _HAVE_BROADCASTERS:
        extra = _broadcaster_rows()
        if extra:
            _ROWS = _ROWS + extra
            _HAVE_BROADCASTERS = True
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
            continue          # neither anime catalogue needs a key
        if need == "trakt_public":
            continue          # public Trakt lists need only the client id
        if need == "vod":
            continue          # bundled data, always available
    return True


def enabled_row_ids(section=HOME):
    """Ordered row ids for one section, falling back to the defaults.

    Only the mixed home section is user-orderable. `ui.rows` is a list the
    viewer arranged for *that* screen, and applying it to the Films tab would
    either empty the tab or reorder it by a preference expressed about
    something else. The other tabs are the row table's own order, filtered.

    Kids mode replaces the list rather than filtering it, so a row that cannot
    express a certification ceiling is never offered while it is on. The kids
    rows carry sections of their own, so the tabs keep working inside it: the
    Films tab shows kid-safe films rather than nothing.
    """
    from . import kids

    if kids.enabled():
        allowed = [by_id(row_id) for row_id in kids.ROW_IDS]
        return [row["id"] for row in allowed
                if row and section in row["sections"]]

    if section != HOME:
        members = [row for row in rows()
                   if section in row["sections"] and not row.get("kids_only")]
        order = SECTION_ORDER.get(section, [])
        rank = {row_id: n for n, row_id in enumerate(order)}
        members.sort(key=lambda row: rank.get(row["id"], len(order)))
        return [row["id"] for row in members]

    configured = settings.get_list("ui.rows")
    if configured:
        known = {row["id"] for row in rows()}
        return [row_id for row_id in configured if row_id in known]
    return [row["id"] for row in rows() if row["default"]]


def enabled_rows(section=HOME):
    out = []
    for row_id in enabled_row_ids(section):
        row = by_id(row_id)
        if row and available(row):
            out.append(row)
    return out


# --------------------------------------------------------------------------
# loading, always through the cache
# --------------------------------------------------------------------------

def row_limit():
    """How many items a row starts with.

    Fewer items means fewer list entries and fewer artwork requests, which is
    what a small device notices, so a row nobody scrolls stays small.

    **The first page only.** This used to trim every page, which was right
    when a row was one fixed list and wrong the moment rows began to grow:
    TMDB sends twenty items and the lean profile kept twelve, so eight were
    thrown away and the next page had to be fetched forty per cent sooner
    than it needed to be. Holding a few more items costs almost nothing -
    Kodi decodes artwork for the handful actually on screen, not for the
    list - and the round trip it saves is the thing the viewer feels.
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


def peek(row_id, section=None):
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
    return kids.filter_items(_presentable(row_id, cached, section))


# One entry per tab: {row_id: what every row above it holds}. Built in a
# single pass and reused, because the alternative is quadratic in the number
# of rows and it is paid on the GUI thread while a row is being drawn.
# Measured on this desktop, the last row of the Films tab cost 5 ms of cache
# reads on its own; nineteen rows of that is a hundred milliseconds of
# nothing, and the device this is written for has much slower storage than
# this desktop does.
_CLAIMED = {}


def _claimed_above(row_id, section):
    """What the rows above this one in this tab are already showing.

    Read out of the cache and never fetched, so it can never turn drawing a
    row into a network call. A row above that has not been warmed yet claims
    nothing, which is the right way round: it keeps its own items when it
    does arrive, because it is the one above.
    """
    if not section:
        return set()
    table = _CLAIMED.get(section)
    if table is None:
        table = _build_claims(section)
        _CLAIMED[section] = table
    return table.get(row_id, set())


def _build_claims(section):
    """Every row in a tab and what the rows above it hold, in one pass."""
    from .meta import items as meta_items

    table = {}
    running = set()
    for row in enabled_rows(section):
        table[row["id"]] = set(running)
        for item in cache.get(cache_key(row["id"])) or []:
            running.add(meta_items.unique_key(item))
    return table


def forget_claims(section=None):
    """Drop the memo, because what a row holds has changed.

    Called whenever a row is fetched afresh and by invalidate(). Getting this
    wrong shows as a row repeating one item from the row above it until the
    window is reopened, which is the right way for it to be wrong.
    """
    if section is None:
        _CLAIMED.clear()
    else:
        _CLAIMED.pop(section, None)


def _presentable(row_id, entries, section):
    """One row as it should be shown: nothing a row above it already has.

    The rows in a tab ask TMDB overlapping questions and always will -
    "trending this week" and "popular" are different questions with much the
    same answer, and measured live they shared six films of twelve. Three
    rows of the same posters under three different headings is what "too many
    duplications in topics" meant, and no amount of renaming fixes it.

    So a row shows what is left after the rows above it have taken theirs.
    That is why more is cached than is drawn: the trim to the row length
    happens here, after the removal, so a row that loses half its page fills
    up again from what it already fetched rather than shrinking on screen.

    Only ever applied to a tab. The mixed home listing has no top-to-bottom
    order to inherit priority from, so it is left exactly as it was.
    """
    if not section:
        return entries[:row_limit()]
    claimed = _claimed_above(row_id, section)
    if not claimed:
        return entries[:row_limit()]
    from .meta import items as meta_items
    kept = [item for item in entries
            if meta_items.unique_key(item) not in claimed]
    # Never leave a row empty on account of tidiness. A row whose every item
    # appears above it is genuinely redundant, and showing it half-empty is a
    # worse answer than showing it as it was.
    return (kept or entries)[:row_limit()]


def has_more(row_id):
    """Could this row go on past its first page?

    A row built from the bundled Israeli data, or from a Trakt chart that
    arrives whole, has exactly one page: asking for a second is a round trip
    that can only come back empty.
    """
    row = by_id(row_id)
    return bool(row and row.get("paged"))


def load(row_id, refresh=False, page=1, section=None):
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
            # Through _presentable like every other path. Returning the cached
            # list raw skipped both the trim to the row length and the
            # cross-row removal, so a warmed row - which is to say almost
            # every row a viewer ever sees - came back untouched.
            from . import kids
            return kids.filter_items(_presentable(row_id, hit, section))
    try:
        with kodi.Timer("row %s page %d" % (row_id, page), threshold_ms=800):
            result = row["loader"](page) or []
    except Exception:
        kodi.log_exception("row %s page %d failed to load" % (row_id, page))
        return cache.get(key) or []
    # Cached whole and trimmed on the way out, because the cross-row removal
    # in _presentable needs something to fill the gaps with.
    result = items.dedupe(result)
    if result:
        cache.set(key, result, row["ttl"])
        forget_claims()
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
    # on rows that were warmed while it was off - and so the cross-row removal
    # sees the rows above as they are now rather than as they were when this
    # one was warmed.
    from . import kids
    return kids.filter_items(_presentable(row_id, result, section))


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
    forget_claims()


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
    """Trending anime, from whichever catalogue is answering today.

    This went through AniList alone until AniList started refusing every
    request, which emptied the row. It now asks meta.anime, which prefers
    AniList and falls back to Kitsu.
    """
    try:
        from .meta import anime
    except ImportError:
        return []
    return anime.trending(limit=ROW_LIMIT, page=page)


def _slice(items, page):
    """One page of a list that is already in memory.

    The Israeli rows come from bundled data - every channel, every station,
    every programme is already loaded - and they were each handed to the row
    with a limit of twenty and marked as having no further pages. So the
    channels row showed twelve of forty-six and stopped, on the screen this
    add-on exists for, and scrolling it did nothing because as far as the
    catalog was concerned there was nothing more to fetch.

    Paging a list in memory costs a slice.
    """
    size = row_limit()
    start = (max(1, page) - 1) * size
    return list(items or [])[start:start + size]


def _israel_live(page=1):
    try:
        from .vod import channels
    except ImportError:
        return []
    return _slice(channels.live_channels(), page)


def _israel_vod_new(page=1):
    try:
        from .vod import library
    except ImportError:
        return []
    # Ask for enough to page through rather than for one row's worth.
    return _slice(library.newest_episodes(limit=row_limit() * 10), page)


def _israel_radio(page=1):
    try:
        from .vod import channels
    except ImportError:
        return []
    return _slice(channels.radio_stations(), page)


def _broadcaster(module, page=1):
    """One broadcaster's programmes, for its row on the Live tab.

    Kan alone has over eight hundred programmes in the bundled catalogue.
    Showing twelve of them and calling the row finished was not a decision
    anybody made; it was the row limit being applied where a page size was
    meant.
    """
    try:
        from .vod import library
    except ImportError:
        return []
    return _slice(library.by_module(module), page)


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
    """Localised heading for a row, with a readable fallback.

    A row may carry a literal `title` instead of a string id. That is for the
    broadcaster rows, whose names come out of the bundled catalogue already in
    Hebrew and are proper nouns in any language - translating "כאן" would be
    inventing a word for a television channel.
    """
    if row.get("title"):
        return row["title"]
    text = kodi.localize(row["title_id"])
    if text and text != str(row["title_id"]):
        return text
    return row["id"].replace("_", " ").title()

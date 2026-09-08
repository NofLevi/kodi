"""Kitsu, a second anime catalogue.

This exists because AniList went down. On 7 September 2026 it began answering
every request with 403 and "The AniList API has been temporarily disabled due
to severe stability issues", which left the anime row empty and anime search
returning nothing. A feature that depends on one free service having a good day
is a feature that will be broken again.

Kitsu suits the job for a reason beyond being a second option: its ids are the
same ones Torrentio uses for anime, so a title found here already carries the
id the source layer needs. Nothing has to be mapped.

What it does not carry is an AniList id, so SeaDex release rankings are
unavailable for titles sourced here. That is the one thing lost by falling
back, and it degrades quietly rather than failing.
"""
from .. import cache, http
from . import items

API = "https://kitsu.io/api/edge"

# Kitsu speaks JSON:API and answers 406 to a plain application/json Accept.
HEADERS = {"Accept": "application/vnd.api+json"}

TTL_LIST = 3 * 3600
TTL_SEARCH = 3600
TTL_DETAILS = 7 * 24 * 3600

# Kitsu calls a full series "TV" and everything else is a film, short or extra.
MOVIE_SUBTYPES = ("movie",)


def available():
    """Kitsu needs no key, so it is available whenever the network is."""
    return True


def _get(path, params=None, ttl=TTL_LIST):
    key = cache.make_key("kitsu", path, sorted((params or {}).items()))

    def fetch():
        return http.get_json(API + path, params=params, headers=HEADERS,
                             timeout=(5, 12), default=None)

    return cache.cached(key, fetch, ttl) or {}


def to_item(node):
    """Convert one Kitsu anime record into a normalised item."""
    if not node or not node.get("id"):
        return None
    attributes = node.get("attributes") or {}

    titles = attributes.get("titles") or {}
    title = (titles.get("en")
             or attributes.get("canonicalTitle")
             or titles.get("en_jp")
             or titles.get("ja_jp") or "")
    if not title:
        return None

    subtype = (attributes.get("subtype") or "").lower()
    poster = (attributes.get("posterImage") or {})
    cover = (attributes.get("coverImage") or {})

    try:
        kitsu_id = int(node["id"])
    except (TypeError, ValueError):
        return None

    return items.new_item(
        "movie" if subtype in MOVIE_SUBTYPES else "show",
        # The kitsu id is what Torrentio wants for anime, so it goes in as an
        # id rather than being buried in extra.
        ids={"kitsu": kitsu_id},
        title=title,
        original_title=titles.get("en_jp") or attributes.get("canonicalTitle") or "",
        year=_year(attributes.get("startDate")),
        premiered=attributes.get("startDate") or "",
        plot=(attributes.get("synopsis") or "").strip(),
        art={
            "poster": poster.get("medium") or poster.get("small")
                      or poster.get("original") or "",
            "fanart": cover.get("original") or cover.get("large") or "",
        },
        rating=_rating(attributes.get("averageRating")),
        votes=int(attributes.get("userCount") or 0),
        mpaa=attributes.get("ageRating") or "",
        duration=int(attributes.get("episodeLength") or 0) * 60,
        extra={
            "anime": True,
            "source": "kitsu",
            "format": subtype,
            "status": attributes.get("status") or "",
            "episodes": int(attributes.get("episodeCount") or 0),
        },
    )


def _year(date_string):
    try:
        return int((date_string or "")[:4])
    except (TypeError, ValueError):
        return 0


def _rating(value):
    """Kitsu rates out of 100 as a string; everything else here is out of 10."""
    try:
        return round(float(value) / 10.0, 1)
    except (TypeError, ValueError):
        return 0.0


def _convert(payload, limit):
    out = []
    for node in (payload or {}).get("data") or []:
        item = to_item(node)
        if item:
            out.append(item)
        if limit and len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------


def trending(limit=20, page=1):
    """What Kitsu is featuring now.

    The trending endpoint is a fixed, curated list with no paging, so later
    pages come from popularity instead. That is a different ordering, which is
    why it is only used once the curated list has run out.
    """
    if page <= 1:
        found = _convert(_get("/trending/anime", {"page[limit]": limit}), limit)
        if found:
            return found
    return popular(limit=limit, page=page)


def popular(limit=20, page=1):
    return _convert(_get("/anime", {
        "sort": "-userCount",
        "page[limit]": limit,
        "page[offset]": max(0, (page - 1) * limit),
    }), limit)


def seasonal(season=None, year=None, limit=20, page=1):
    if season is None or year is None:
        season, year = current_season()
    return _convert(_get("/anime", {
        "filter[season]": str(season).lower(),
        "filter[seasonYear]": int(year),
        "sort": "-userCount",
        "page[limit]": limit,
        "page[offset]": max(0, (page - 1) * limit),
    }), limit)


def search(query, limit=20, page=1):
    if not query:
        return []
    return _convert(_get("/anime", {
        "filter[text]": query,
        "page[limit]": limit,
        "page[offset]": max(0, (page - 1) * limit),
    }, ttl=TTL_SEARCH), limit)


def details(kitsu_id):
    payload = _get("/anime/%s" % kitsu_id, ttl=TTL_DETAILS)
    return to_item((payload or {}).get("data"))


def current_season():
    """Kitsu names seasons the same way AniList does, in lower case."""
    import time

    month = time.localtime().tm_mon
    year = time.localtime().tm_year
    if month in (1, 2, 3):
        return "winter", year
    if month in (4, 5, 6):
        return "spring", year
    if month in (7, 8, 9):
        return "summer", year
    return "fall", year

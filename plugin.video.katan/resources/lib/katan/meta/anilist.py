"""AniList catalog for the anime rows.

AniList needs no API key, which is why it is the anime catalog of choice here.
Results are converted into the same item dicts as everything else so the UI
and the source aggregator do not care where a title came from.

**The API is refusing every request as of 7 September 2026.** It answers 403
with "The AniList API has been temporarily disabled due to severe stability
issues" - to any User-Agent, including a browser one, so it is an outage at
the service and not something this end can work around. The consequence is
that the anime row comes back empty and is therefore hidden, and anime search
returns nothing. Nothing here is broken and nothing needs changing when it
comes back; the failure is logged plainly rather than left to look like a bug
in this module.
"""
from .. import cache, http, kodi
from . import items

API_URL = "https://graphql.anilist.co"
TTL_LIST = 3 * 3600
TTL_SEARCH = 3600

_MEDIA_FIELDS = """
id
idMal
title { romaji english native }
description(asHtml: false)
coverImage { large }
bannerImage
averageScore
popularity
episodes
duration
genres
format
status
season
seasonYear
startDate { year month day }
"""

_TRENDING_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: TRENDING_DESC, isAdult: false) { %s }
  }
}
""" % _MEDIA_FIELDS

_SEASON_QUERY = """
query ($page: Int, $perPage: Int, $season: MediaSeason, $year: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, season: $season, seasonYear: $year,
          sort: POPULARITY_DESC, isAdult: false) { %s }
  }
}
""" % _MEDIA_FIELDS

_SEARCH_QUERY = """
query ($page: Int, $perPage: Int, $search: String) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, search: $search, sort: SEARCH_MATCH, isAdult: false) { %s }
  }
}
""" % _MEDIA_FIELDS

_DETAILS_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) { %s }
}
""" % _MEDIA_FIELDS


def _query(document, variables, ttl):
    key = cache.make_key("anilist", document[:40], sorted(variables.items()))

    def fetch():
        response = http.post(
            API_URL,
            json={"query": document, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if response is None:
            return None
        if response.status_code >= 400:
            kodi.log("AniList refused the request: %s" % _refusal(response))
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not payload or "data" not in payload:
            # GraphQL puts its own errors in the body with a 200, so this is
            # where a query that stopped being valid would show up.
            detail = str((payload or {}).get("errors") or "empty body")[:200]
            kodi.log("AniList returned no data: %s" % detail)
            return None
        return payload["data"]

    return cache.cached(key, fetch, ttl) or {}


def _refusal(response):
    """AniList explains itself in the body, so quote it rather than the code."""
    try:
        errors = (response.json() or {}).get("errors") or []
        message = errors[0].get("message") if errors else ""
    except (ValueError, AttributeError, IndexError, TypeError):
        message = ""
    # The outer parentheses matter. Without them the strip binds to the
    # argument tuple rather than to the formatted string and raises, which is
    # what took the whole source picker down earlier. test_imports guards it.
    return ("HTTP %s %s" % (response.status_code, message or "")).strip()


def _page_media(data):
    return ((data or {}).get("Page") or {}).get("media") or []


def to_item(media):
    """Convert one AniList media node into a normalised item."""
    if not media or not media.get("id"):
        return None
    titles = media.get("title") or {}
    title = titles.get("english") or titles.get("romaji") or titles.get("native") or ""
    is_movie = (media.get("format") or "").upper() == "MOVIE"
    plot = (media.get("description") or "").replace("<br>", "\n").replace("<i>", "")
    plot = plot.replace("</i>", "").replace("<b>", "").replace("</b>", "")
    score = media.get("averageScore")
    return items.new_item(
        "movie" if is_movie else "show",
        ids={"anilist": media["id"], "mal": media.get("idMal") or 0},
        title=title,
        original_title=titles.get("romaji") or "",
        year=int((media.get("startDate") or {}).get("year") or media.get("seasonYear") or 0),
        plot=plot,
        art={
            "poster": (media.get("coverImage") or {}).get("large") or "",
            "fanart": media.get("bannerImage") or "",
        },
        rating=float(score) / 10.0 if score else 0.0,
        votes=int(media.get("popularity") or 0),
        genres=media.get("genres") or [],
        duration=int(media.get("duration") or 0) * 60,
        extra={
            "anime": True,
            "format": media.get("format") or "",
            "status": media.get("status") or "",
            "episodes": int(media.get("episodes") or 0),
            "season": media.get("season") or "",
            "season_year": media.get("seasonYear") or 0,
        },
    )


def _convert(nodes, limit):
    out = []
    for node in nodes:
        item = to_item(node)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def trending(limit=20, page=1):  # noqa: D401 - page is used below
    data = _query(_TRENDING_QUERY, {"page": page, "perPage": limit}, TTL_LIST)
    return _convert(_page_media(data), limit)


def seasonal(season=None, year=None, limit=20, page=1):
    """Current season by default, e.g. season="SUMMER"."""
    if season is None or year is None:
        season, year = current_season()
    data = _query(_SEASON_QUERY,
                  {"page": page, "perPage": limit, "season": season, "year": year},
                  TTL_LIST)
    return _convert(_page_media(data), limit)


def search(query, limit=20, page=1):
    if not query:
        return []
    data = _query(_SEARCH_QUERY,
                  {"page": page, "perPage": limit, "search": query}, TTL_SEARCH)
    return _convert(_page_media(data), limit)


def details(anilist_id):
    data = _query(_DETAILS_QUERY, {"id": int(anilist_id)}, 7 * 24 * 3600)
    return to_item((data or {}).get("Media"))


def current_season():
    """AniList season name and year for today."""
    import time
    now = time.localtime()
    month = now.tm_mon
    if month in (1, 2, 3):
        return "WINTER", now.tm_year
    if month in (4, 5, 6):
        return "SPRING", now.tm_year
    if month in (7, 8, 9):
        return "SUMMER", now.tm_year
    return "FALL", now.tm_year

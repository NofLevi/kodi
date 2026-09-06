"""The normalised media item.

Items are plain dicts, not objects, for one deliberate reason: they are cached
as JSON, and a dict needs no serialisation layer in either direction. The
schema below is the contract every provider converts into and every UI layer
reads from.

    type            "movie" | "show" | "season" | "episode" | "channel" | "vod"
    ids             {"tmdb": int, "imdb": str, "tvdb": int, "trakt": int,
                     "anilist": int, "kitsu": int, "slug": str}
    title           display title in the user language
    original_title  title in the original language
    year            release year as int
    plot, tagline   text
    art             {"poster": url, "fanart": url, "thumb": url, "clearlogo": url}
    rating, votes   float, int
    mpaa            certification string
    genres          list of strings
    duration        seconds
    premiered       "YYYY-MM-DD"
    studio          list of strings
    cast            list of {"name", "role", "thumb"}
    show_title      episodes and seasons only
    season, episode ints, episodes and seasons only
    playcount       0 or 1, filled from the Trakt mirror
    resume          {"position": seconds, "total": seconds}
    extra           provider specific payload the UI may ignore
"""

IMAGE_BASE = "https://image.tmdb.org/t/p/"

# Deliberately modest sizes. Posters render at roughly 200 px wide in the row
# layout, so w342 is already generous, and originals would blow the texture
# cache on a low-memory device.
POSTER_SIZE = "w342"
FANART_SIZE = "w780"
STILL_SIZE = "w300"
PROFILE_SIZE = "w185"


def poster_size():
    """The poster width to request.

    This is the biggest memory lever there is. Kodi holds decoded bitmaps, not
    the compressed files, so a w342 poster costs roughly 700 KB of RAM while a
    w185 one costs about 205 KB. On a device with a gigabyte of memory and a
    720p panel, the larger image is invisible and the difference is not.
    """
    try:
        from .. import settings
        return settings.get("ui.poster_size") or POSTER_SIZE
    except Exception:
        return POSTER_SIZE


def image_url(path, size=None):
    """Turn a TMDB relative image path into a full URL."""
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return "%s%s%s" % (IMAGE_BASE, size or poster_size(), path)


def new_item(item_type, **fields):
    """Create an item with every expected key present, so callers never KeyError."""
    item = {
        "type": item_type,
        "ids": {},
        "title": "",
        "original_title": "",
        "year": 0,
        "plot": "",
        "tagline": "",
        "art": {},
        "rating": 0.0,
        "votes": 0,
        "mpaa": "",
        "genres": [],
        "duration": 0,
        "premiered": "",
        "studio": [],
        "cast": [],
        "playcount": 0,
        "resume": {},
        "extra": {},
    }
    item.update(fields)
    return item


def label(item):
    """The one-line label shown in a list."""
    if item.get("type") == "episode":
        season = item.get("season") or 0
        episode = item.get("episode") or 0
        title = item.get("title") or ""
        return "%dx%02d. %s" % (season, episode, title) if title else "%dx%02d" % (season, episode)
    title = item.get("title") or ""
    year = item.get("year") or 0
    return "%s (%d)" % (title, year) if year else title


def unique_key(item):
    """A stable identity used for de-duplicating rows across sources."""
    ids = item.get("ids") or {}
    for source in ("imdb", "tmdb", "tvdb", "trakt", "anilist"):
        if ids.get(source):
            return "%s:%s:%s" % (item.get("type", ""), source, ids[source])
    return "%s:title:%s:%s" % (item.get("type", ""), item.get("title", ""), item.get("year", 0))


def dedupe(items):
    """Remove repeats while preserving order."""
    seen = set()
    out = []
    for item in items:
        key = unique_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# --------------------------------------------------------------------------
# builders from TMDB payloads
# --------------------------------------------------------------------------


def from_tmdb_movie(data):
    if not data or not data.get("id"):
        return None
    release = data.get("release_date") or ""
    return new_item(
        "movie",
        ids={"tmdb": data["id"], "imdb": data.get("imdb_id") or ""},
        title=data.get("title") or data.get("original_title") or "",
        original_title=data.get("original_title") or "",
        year=_year(release),
        premiered=release,
        plot=data.get("overview") or "",
        tagline=data.get("tagline") or "",
        art={
            "poster": image_url(data.get("poster_path")),
            "fanart": image_url(data.get("backdrop_path"), FANART_SIZE),
        },
        rating=float(data.get("vote_average") or 0.0),
        votes=int(data.get("vote_count") or 0),
        genres=_genres(data),
        duration=int(data.get("runtime") or 0) * 60,
        studio=[c.get("name", "") for c in data.get("production_companies") or []],
    )


def from_tmdb_show(data):
    if not data or not data.get("id"):
        return None
    first_air = data.get("first_air_date") or ""
    external = data.get("external_ids") or {}
    return new_item(
        "show",
        ids={
            "tmdb": data["id"],
            "imdb": external.get("imdb_id") or "",
            "tvdb": external.get("tvdb_id") or 0,
        },
        title=data.get("name") or data.get("original_name") or "",
        original_title=data.get("original_name") or "",
        year=_year(first_air),
        premiered=first_air,
        plot=data.get("overview") or "",
        tagline=data.get("tagline") or "",
        art={
            "poster": image_url(data.get("poster_path")),
            "fanart": image_url(data.get("backdrop_path"), FANART_SIZE),
        },
        rating=float(data.get("vote_average") or 0.0),
        votes=int(data.get("vote_count") or 0),
        genres=_genres(data),
        studio=[c.get("name", "") for c in data.get("networks") or []],
        extra={
            "seasons": int(data.get("number_of_seasons") or 0),
            "episodes": int(data.get("number_of_episodes") or 0),
            "status": data.get("status") or "",
        },
    )


def from_tmdb_episode(data, show):
    if not data:
        return None
    show_ids = (show or {}).get("ids", {})
    air_date = data.get("air_date") or ""
    return new_item(
        "episode",
        ids={"tmdb": data.get("id") or 0, "imdb": show_ids.get("imdb", "")},
        title=data.get("name") or "",
        season=int(data.get("season_number") or 0),
        episode=int(data.get("episode_number") or 0),
        show_title=(show or {}).get("title", ""),
        year=_year(air_date),
        premiered=air_date,
        plot=data.get("overview") or "",
        art={
            "thumb": image_url(data.get("still_path"), STILL_SIZE),
            "poster": (show or {}).get("art", {}).get("poster", ""),
            "fanart": (show or {}).get("art", {}).get("fanart", ""),
        },
        rating=float(data.get("vote_average") or 0.0),
        duration=int(data.get("runtime") or 0) * 60,
        extra={"show_ids": show_ids},
    )


def from_tmdb_multi(data):
    """Convert one row of a search/multi or trending/all response."""
    media_type = (data or {}).get("media_type")
    if media_type == "movie":
        return from_tmdb_movie(data)
    if media_type == "tv":
        return from_tmdb_show(data)
    return None


def _year(date_string):
    try:
        return int((date_string or "")[:4])
    except (ValueError, TypeError):
        return 0


# TMDB sends full genre objects on a details call and bare ids in a list, so a
# row item used to arrive with no genres at all. The ids are a fixed, published
# table, so resolving them locally costs nothing and means a list item knows
# what it is - which is what lets kids mode filter a row without fetching the
# details of every title in it.
TMDB_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    # television has its own ids for some of the same ground
    10759: "Action & Adventure", 10762: "Kids", 10763: "News",
    10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Soap",
    10767: "Talk", 10768: "War & Politics",
}


def _genres(data):
    genres = data.get("genres")
    if genres and isinstance(genres[0], dict):
        return [g.get("name", "") for g in genres]

    names = []
    for genre_id in data.get("genre_ids") or []:
        name = TMDB_GENRES.get(genre_id)
        if name:
            names.append(name)
    return names

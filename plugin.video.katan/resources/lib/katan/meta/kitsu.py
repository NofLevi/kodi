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
from .. import cache, http, kodi
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


def episode_address(title, episode, season_name="", season_episodes=0):
    """Where an anime episode lives on Kitsu: (kitsu_id, episode), or None.

    This exists because of how differently the two worlds count. TMDB folds a
    long-running anime into a few large seasons - all of Bleach's
    Thousand-Year Blood War is one "season 2" numbered 1 to 50 - while Kitsu,
    AniDB and every release group treat each cour as its own series numbered
    from 1. Measured on Bleach 2x46: `tt0434665:2:46` returns nothing at all
    from Torrentio, and `kitsu:49444:6` - the same episode, addressed as the
    sixth of the fourth cour - returns nine sources.

    The walk is over Kitsu's own episode counts, so nothing is assumed about
    how a show is divided: the cours go in broadcast order and the
    within-season number is spent against them until it lands inside one.
    Bleach: 46 - 13 - 13 - 14 = 6, the fourth cour.

    `season_episodes` is what TMDB says the season holds, and it is the check
    that makes this safe to act on. A wrong address is worse than none - it
    plays a real episode that is not the one asked for, which no filter
    downstream can catch - so if the cours do not add up to the season, this
    gives up rather than guessing. Bleach: 13 + 13 + 14 + 10 = 50, which is
    exactly what TMDB says season 2 holds.
    """
    episode = int(episode or 0)
    if episode < 1:
        return None
    parts = _cours(title, season_name)
    if not parts:
        return None

    total = sum(count for _id, count in parts)
    if season_episodes and total != int(season_episodes):
        kodi.log("kitsu has %d episodes for %s %s where TMDB has %s, so the "
                 "numbering cannot be trusted" % (total, title, season_name,
                                                  season_episodes))
        return None

    remaining = episode
    for kitsu_id, count in parts:
        if remaining <= count:
            return kitsu_id, remaining
        remaining -= count
    return None


# A cour is roughly ten to twenty-six episodes. Anything shorter is a recap,
# an OVA or a trailer, and letting one into the walk shifts every number after
# it by one - which is the failure that plays the wrong episode rather than
# none at all. Bleach's arc has exactly that: a one-episode recap sitting
# between its second and third cours.
MIN_COUR_EPISODES = 4

# A recap is published as "special" and a theme song as "music", and both sit
# in the middle of the same search results. Only a broadcast run is a cour.
COUR_SUBTYPES = ("TV", "ONA")


def _cours(title, season_name=""):
    """The cours of one arc, in broadcast order, with their episode counts.

    Searched on the season's own name as well as the show's, because that is
    what the arc is called and released under, and because the show name
    alone brings back the 366-episode series it continues, six one-episode
    specials and several unrelated shows - measured, all together.

    The titles themselves are deliberately not matched on. Kitsu's canonical
    title is romaji - "BLEACH: Sennen Kessen-hen" - so the English arc name
    TMDB gives us appears nowhere in it, and requiring it matched nothing at
    all. Kitsu's own text search already spans every title variant, which is
    how it finds these from the English name in the first place.
    """
    if not title or not season_name:
        return []
    payload = _get("/anime", {
        "filter[text]": "%s %s" % (title, season_name),
        "page[limit]": 20,
        "fields[anime]": "canonicalTitle,titles,episodeCount,startDate,subtype",
    })

    found = []
    for node in (payload or {}).get("data") or []:
        attributes = node.get("attributes") or {}
        count = int(attributes.get("episodeCount") or 0)
        start = attributes.get("startDate") or ""
        if attributes.get("subtype") not in COUR_SUBTYPES:
            continue
        if count < MIN_COUR_EPISODES or not start:
            continue
        found.append((start, node.get("id"), count))

    found.sort()
    return [(kitsu_id, count) for _start, kitsu_id, count in found]


def season_address(titles, season, episode, season_counts):
    """A whole TMDB season as one Kitsu entry: (kitsu_id, episode), or None.

    The other mapping in this file needs the season to have a name, because a
    name is what separates an arc from the series it continues. Most anime
    seasons have no name - TMDB calls them "Season 1", "Season 2" - and for
    those the shape is much simpler: one TMDB season *is* one Kitsu entry, in
    order, and the episode number does not change. KonoSuba, measured: TMDB
    has three aired seasons of 10, 10 and 11 episodes and Kitsu has three TV
    entries of 10, 10 and 11.

    That correspondence is also the check. The two lists have to be the same
    length and agree on every count, so a spin-off or an OVA that crept into
    the search results makes this refuse rather than shift everything by one.
    `titles` are TMDB's names for the show, English and original, and are what
    keeps the spin-off out in the first place: KonoSuba's Kitsu neighbour is
    the same franchise and the same length of season, and only its Japanese
    title - Bakuen rather than Shukufuku - says it is a different show.
    """
    season = int(season or 0)
    episode = int(episode or 0)
    counts = [int(c or 0) for c in (season_counts or [])]
    if season < 1 or episode < 1 or not counts:
        return None

    entries = _entries_for(titles)
    if len(entries) != len(counts):
        return None
    if [count for _id, count in entries] != counts:
        return None
    if season > len(entries):
        return None

    kitsu_id, count = entries[season - 1]
    if episode > count:
        return None
    return kitsu_id, episode


def _entries_for(titles):
    """This show's own broadcast runs on Kitsu, in order, with their counts.

    Matched on the titles rather than trusted from the search, because a text
    search for a franchise returns the franchise: KonoSuba brings back two
    OVAs, a film and a spin-off whose seasons are the same length as the real
    ones. A Kitsu entry carries its name in several languages and TMDB gives
    us two, so a run belongs to this show when any one of its names begins
    with any one of ours - which is what makes "Season 2" match, since Kitsu
    writes that as the show's name with a 2 after it.
    """
    wanted = [_words(t) for t in (titles or []) if t and _words(t)]
    if not wanted:
        return []
    payload = _get("/anime", {
        "filter[text]": (titles or [""])[0],
        "page[limit]": 20,
        "fields[anime]": "canonicalTitle,titles,episodeCount,startDate,subtype",
    })

    found = []
    for node in (payload or {}).get("data") or []:
        attributes = node.get("attributes") or {}
        count = int(attributes.get("episodeCount") or 0)
        start = attributes.get("startDate") or ""
        if attributes.get("subtype") not in COUR_SUBTYPES:
            continue
        if count < MIN_COUR_EPISODES or not start:
            continue
        names = [_words(n) for n in _names(attributes)]
        if not any(name.startswith(one) for name in names for one in wanted):
            continue
        found.append((start, node.get("id"), count))

    found.sort()
    return [(kitsu_id, count) for _start, kitsu_id, count in found]


def _names(attributes):
    """Every name Kitsu has for one entry.

    The canonical one is romaji, so on its own it can never match an English
    name from TMDB or a Japanese one. The `titles` map holds both.
    """
    names = [attributes.get("canonicalTitle") or ""]
    for value in (attributes.get("titles") or {}).values():
        if value:
            names.append(value)
    return [n for n in names if n]


def _words(text):
    """Lowercase words with the punctuation gone, for comparing two titles.

    Japanese has no spaces and needs none of this, and passing it through
    unchanged is the point - it is compared against another Japanese title.
    """
    letters = [c.lower() if (c.isalnum() or c == " ") else " " for c in text]
    return " ".join("".join(letters).split())

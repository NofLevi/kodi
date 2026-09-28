"""TMDB client.

Every response goes through the SQLite cache, so a warmed home screen makes no
network calls at all. Discovery lists get a short TTL because they change
daily; details get a long one because they effectively never change.
"""
import re

from .. import cache, http, settings
from . import items

API_BASE = "https://api.themoviedb.org/3"

TTL_DETAILS = 7 * 24 * 3600  # a specific movie or show
TTL_SEARCH = 3600            # search results and suggestions

# How long a discovery list stays fresh. The default of six hours was a
# constant here and "Metadata cache (hours)" was a setting in the dialog with
# the same default and nothing reading it - a switch that looked like a
# feature and did nothing. They are the same number now.
DEFAULT_LIST_HOURS = 6


def list_ttl():
    """Seconds a trending or discover list is kept before asking TMDB again.

    Floored at ten minutes: this is an advanced setting, and someone typing 0
    into it should not turn every home screen into a fresh round of network
    calls on a device that has a few hundred megabytes to work with.
    """
    return max(600, settings.get_int("cache.meta_hours",
                                     DEFAULT_LIST_HOURS) * 3600)

# TMDB numeric ids for the streaming services people actually ask for.
WATCH_PROVIDERS = {
    "netflix": 8,
    "disney": 337,
    "prime": 119,
    "apple": 350,
    "hbo": 1899,
}


# A TMDB key is not a secret in the way an account password is. It is read
# only, rate limited per key, and TMDB's terms allow one to ship inside a
# client - Kodi's own metadata.themoviedb.org.python embeds one, as does every
# other Kodi add-on that reads TMDB. Shipping one here is the difference
# between a fresh install showing the home screen and showing an empty Films
# tab, because every row in that tab needs it.
#
# Paste one in to enable that. The setting still wins, so anyone who wants
# their own quota, or a different language default, just enters theirs.
# This is the v3 API Key, not the v4 "API Read Access Token". The names are
# misleading: the v3 key is the one that cannot write. Measured against the
# live API - reads answer 200, while creating a list and adding to a watchlist
# both answer 401, because TMDB writes need a user session on top of the key.
# The v4 bearer token is bound to the account and is deliberately not here.
BUNDLED_KEY = "94584cab08f71b624286f19eda8d2b5e"


def api_key():
    return (settings.get("tmdb.apikey") or BUNDLED_KEY).strip()


def has_key():
    return bool(api_key())


def language():
    """TMDB language tag derived from the add-on UI language setting."""
    choice = settings.get("ui.language")
    if choice == "he":
        return "he-IL"
    if choice == "en":
        return "en-GB"
    kodi_lang = ""
    try:
        import xbmc
        kodi_lang = xbmc.getLanguage(xbmc.ISO_639_1) or ""
    except Exception:
        pass
    return "he-IL" if kodi_lang == "he" else "en-GB"


def region():
    return settings.get("ui.region") or "IL"


def _call(path, ttl=None, **params):
    """GET a TMDB endpoint through the cache. Always returns a dict.

    `ttl` is resolved here rather than as a default argument, because a
    default is evaluated once at import and would never notice the setting
    changing.
    """
    if ttl is None:
        ttl = list_ttl()
    key = api_key()
    if not key:
        return {}
    params.setdefault("language", language())
    query = dict(params)
    query["api_key"] = key

    # The cache key excludes the API key on purpose, so changing keys does not
    # invalidate everything the user already downloaded.
    cache_key = cache.make_key("tmdb", path, sorted(params.items()))

    def fetch():
        return http.get_json("%s%s" % (API_BASE, path), params=query, default=None)

    return cache.cached(cache_key, fetch, ttl) or {}


def _results(payload, media_type=None):
    """Convert a TMDB paged response into a list of items."""
    out = []
    for row in (payload or {}).get("results") or []:
        if media_type == "movie":
            item = items.from_tmdb_movie(row)
        elif media_type == "tv":
            item = items.from_tmdb_show(row)
        else:
            item = items.from_tmdb_multi(row)
        if item:
            out.append(item)
    return out


# --------------------------------------------------------------------------
# discovery endpoints, one per home row
# --------------------------------------------------------------------------


def trending(media_type="movie", window="day", page=1):
    """media_type: movie, tv or all. window: day or week."""
    payload = _call("/trending/%s/%s" % (media_type, window), page=page)
    return _results(payload, media_type if media_type != "all" else None)


def popular(media_type="movie", page=1):
    return _results(_call("/%s/popular" % media_type, page=page), media_type)


def now_playing(page=1):
    return _results(_call("/movie/now_playing", page=page, region=region()), "movie")


def upcoming(page=1):
    return _results(_call("/movie/upcoming", page=page, region=region()), "movie")


def airing_today(page=1):
    return _results(_call("/tv/airing_today", page=page), "tv")


def on_the_air(page=1):
    return _results(_call("/tv/on_the_air", page=page), "tv")


def top_rated(media_type="movie", page=1):
    return _results(_call("/%s/top_rated" % media_type, page=page), media_type)


def discover(media_type="movie", page=1, **filters):
    """Raw discover access. Filters pass straight through to TMDB."""
    return _results(_call("/discover/%s" % media_type, page=page, **filters), media_type)


def new_on_provider(provider, media_type="movie", page=1):
    """Recently added titles on a streaming service in the user region."""
    provider_id = WATCH_PROVIDERS.get(provider, provider)
    sort = "primary_release_date.desc" if media_type == "movie" else "first_air_date.desc"
    filters = {
        "watch_region": region(),
        "with_watch_providers": provider_id,
        "sort_by": sort,
        "vote_count.gte": 20,
    }
    return discover(media_type, page=page, **filters)


def by_original_language(code="he", media_type="movie", page=1):
    """Titles originally made in a given language, most popular first."""
    return discover(media_type, page=page,
                    with_original_language=code, sort_by="popularity.desc")


def recommendations(media_type, tmdb_id, page=1):
    return _results(_call("/%s/%s/recommendations" % (media_type, tmdb_id),
                          page=page), media_type)


def similar(media_type, tmdb_id, page=1):
    return _results(_call("/%s/%s/similar" % (media_type, tmdb_id),
                          page=page), media_type)


# --------------------------------------------------------------------------
# details
# --------------------------------------------------------------------------


def movie(tmdb_id):
    payload = _call("/movie/%s" % tmdb_id, ttl=TTL_DETAILS,
                    append_to_response="credits,external_ids,videos,release_dates")
    item = items.from_tmdb_movie(payload)
    if item:
        _attach_credits(item, payload)
        item["mpaa"] = _movie_certification(payload)
        item["extra"]["trailer"] = _trailer(payload)
        item["extra"]["anime"] = is_anime(payload)
    return item


# TMDB's genre id for animation, and what makes animation *anime*: made in
# Japan. Matching on the genre name would work in English and nowhere else -
# TMDB returns genre names in the requested language, so a Hebrew interface
# sees "אנימציה" and an id-based test sees the same 16 everywhere.
GENRE_ANIMATION = 16


def is_anime(payload):
    """Is this TMDB payload an anime?

    This decides whether the anime source providers are asked at all, and
    they were never being asked: the only signals `_is_anime` had were an
    AniList or Kitsu id, and nothing that comes from TMDB carries either. So
    a series browsed from any TMDB row - which is every row that works,
    AniList having been down for months - could not reach nyaa, AnimeTosho
    or SeaDex. Bleach episode 46 found nothing, and nyaa had forty-four
    copies of it.
    """
    if not payload:
        return False
    genres = payload.get("genres") or []
    ids = {genre.get("id") for genre in genres if isinstance(genre, dict)}
    ids.update(payload.get("genre_ids") or [])
    if GENRE_ANIMATION not in ids:
        return False
    if (payload.get("original_language") or "") == "ja":
        return True
    return "JP" in (payload.get("origin_country") or [])


def show(tmdb_id):
    payload = _call("/tv/%s" % tmdb_id, ttl=TTL_DETAILS,
                    append_to_response="credits,external_ids,videos,content_ratings")
    item = items.from_tmdb_show(payload)
    if item:
        _attach_credits(item, payload)
        item["mpaa"] = _show_certification(payload)
        item["extra"]["trailer"] = _trailer(payload)
        item["extra"]["season_numbers"] = [
            s.get("season_number") for s in payload.get("seasons") or []
            if s.get("season_number") is not None
        ]
        item["extra"]["anime"] = is_anime(payload)
    return item


def seasons(tmdb_id):
    """Season stubs for a show. Specials are hidden unless they are all we have."""
    payload = _call("/tv/%s" % tmdb_id, ttl=TTL_DETAILS)
    show_item = items.from_tmdb_show(payload) or {}
    out = []
    for row in payload.get("seasons") or []:
        number = row.get("season_number")
        if number is None:
            continue
        poster = (items.image_url(row.get("poster_path"))
                  or show_item.get("art", {}).get("poster", ""))
        out.append(items.new_item(
            "season",
            ids=dict(show_item.get("ids", {})),
            title=row.get("name") or ("Season %s" % number),
            show_title=show_item.get("title", ""),
            season=int(number),
            plot=row.get("overview") or show_item.get("plot", ""),
            premiered=row.get("air_date") or "",
            year=items._year(row.get("air_date") or ""),
            art={"poster": poster,
                 "fanart": show_item.get("art", {}).get("fanart", "")},
            extra={"episode_count": int(row.get("episode_count") or 0),
                   "tmdb_show": tmdb_id},
        ))
    real_seasons = [s for s in out if s["season"] > 0]
    return real_seasons or out


def episodes(tmdb_id, season_number):
    payload = _call("/tv/%s/season/%s" % (tmdb_id, season_number), ttl=TTL_DETAILS)
    show_item = show(tmdb_id) or {}
    out = []
    for row in payload.get("episodes") or []:
        episode = items.from_tmdb_episode(row, show_item)
        if episode:
            episode["extra"]["tmdb_show"] = tmdb_id
            out.append(episode)
    return out


def english_title(media_type, tmdb_id):
    """The title TMDB has in English, for searching indexes by name.

    Nearly every provider is asked by IMDb id and does not care what anything
    is called. The two that are asked by *name* are the anime ones, and they
    were being handed `original_title` - which for anime is Japanese, in
    Japanese script. Measured against Nyaa: the Japanese title returns zero
    results for Doraemon, Reborn, Frieren and Madoka alike, while the English
    name returns 75, 45, 6 and 0. It is not a near miss; it cannot match,
    because English-translated releases are named in romaji or English and
    Nyaa searches the release name as text.

    One extra call, cached like every other, and only made for anime.
    """
    if not tmdb_id:
        return ""
    path = "/movie/%s" if media_type == "movie" else "/tv/%s"
    payload = _call(path % tmdb_id, ttl=TTL_DETAILS, language="en-US")
    return payload.get("title") or payload.get("name") or ""


def translations(media_type, tmdb_id):
    """Every name TMDB has for a title, as (language, name) pairs.

    A release is named in the language of whoever uploaded it - "Juego de
    tronos", "Il Trono di Spade", "Hra o trůny" - and `alternative_titles`
    does not carry those: TMDB keeps them here. A blank name means "the same
    as the original" and is left out. One call, cached like every other.
    """
    if not tmdb_id:
        return []
    path = "/movie/%s/translations" if media_type == "movie" \
        else "/tv/%s/translations"
    payload = _call(path % tmdb_id, ttl=TTL_DETAILS)
    pairs = []
    for entry in payload.get("translations") or []:
        data = entry.get("data") or {}
        name = (data.get("name") or data.get("title") or "").strip()
        pair = (entry.get("iso_639_1") or "", name)
        if name and pair not in pairs:
            pairs.append(pair)
    return pairs


# An alternative title TMDB labels as belonging to another part of the show -
# "series 2 title", "second series", "OAD title", "season 4 title" - is a
# different production's name, and knowing it would let that production's
# releases through: Digimon's "Digimon Adventure 02" is the sequel series.
_ANOTHER_PART = re.compile(
    r"series|season|oad|ova|movie|film|sequel|part|chapter|special|arc|cour"
    r"|incorrect|initialism", re.I)


def _latin(text):
    return all(ord(char) < 0x250 for char in text)


def anime_titles(media_type, tmdb_id):
    """The Latin-script names an anime is released under, romaji first.

    `original_title` is native script - "進撃の巨人" for Attack on Titan - and
    fansub and raw releases use a Latin name that is often not TMDB's English
    one: "Shingeki no Kyojin", "Boku no Hero Academia", and for Digimon
    "Digimon Adventure", where TMDB's English name is the dub's "Digimon:
    Digital Monsters". Only JP-tagged "romaji" entries were taken at first,
    and Digimon has none - its release name is tagged US and IT - so 26 of
    its 34 releases were hidden as another show.

    Every Latin-script alternative title is taken, romaji first so the
    subtitle search, which asks under the first two, asks the right ones -
    except those TMDB labels as another part of the show (`_ANOTHER_PART`)
    and the initialisms ("SNK", "AOT"), too short to be worth the collision.
    One extra call, cached like every other, only for anime.
    """
    if not tmdb_id:
        return []
    path = "/movie/%s/alternative_titles" if media_type == "movie"         else "/tv/%s/alternative_titles"
    payload = _call(path % tmdb_id, ttl=TTL_DETAILS)
    entries = payload.get("titles") or payload.get("results") or []
    romaji, others = [], []
    for entry in entries:
        title = (entry.get("title") or "").strip()
        kind = (entry.get("type") or "").lower()
        if not title or not _latin(title):
            continue
        # Every Japanese romaji name is kept, arc names included: on TMDB an
        # arc is a season of this same show, so "Kimetsu no Yaiba: Hashira
        # Geiko-hen" is Demon Slayer's season 4 and must stay known. A label
        # naming another part only rules out the *other* Latin names, whose
        # labels are free text TMDB editors write in a dozen languages and
        # cannot be tied to a season reliably - naming per season is what
        # an anime list does properly, and is the next step here.
        if entry.get("iso_3166_1") == "JP" and "romaji" in kind:
            romaji.append(title)
        elif not _ANOTHER_PART.search(kind):
            others.append(title)
    seen = []
    for title in romaji + others:
        if title.lower() not in [t.lower() for t in seen]:
            seen.append(title)
    return seen


def absolute_episode(tmdb_id, season, episode):
    """Which episode this is counting from the first, not from the season.

    Fansub groups number anime absolutely far more often than by season, so
    "season 8, episode 14" of Reborn is released as episode 203 and searching
    for 14 finds episode 149 - Nyaa matches "14" inside "149" - which is a
    different episode of the same show, offered confidently.

    Season zero is skipped: specials are not part of the count.
    """
    season = int(season or 0)
    episode = int(episode or 0)
    if season <= 1 or not tmdb_id:
        return episode
    # Some shows TMDB already numbers absolutely inside each season: Naruto
    # Shippuden's season 3 is episodes 54 to 71, so "3x55" *is* 55. Adding
    # the earlier seasons on top made it 108, and the picker then offered
    # episode 108's subtitle at 71% for episode 55. A season that does not
    # start at 1 says so. This is the same cached call `episodes()` makes.
    numbers = [int(row.get("episode_number") or 0) for row in
               _call("/tv/%s/season/%s" % (tmdb_id, season),
                     ttl=TTL_DETAILS).get("episodes") or []]
    if numbers and min(numbers) > 1:
        return episode
    payload = _call("/tv/%s" % tmdb_id, ttl=TTL_DETAILS)
    total = 0
    for row in payload.get("seasons") or []:
        number = row.get("season_number")
        if number is None or int(number) < 1 or int(number) >= season:
            continue
        total += int(row.get("episode_count") or 0)
    return total + episode if total else episode


def find_by_name(title, media_type="tv", year=0, alternatives=()):
    """A TMDB id for something that only has a name, or 0.

    The anime rows come from AniList or Kitsu, which know nothing about TMDB:
    an item arrives carrying `{"anilist": 189046, "mal": 61316}` and no tmdb
    id at all. Everything downstream is keyed on TMDB - seasons, episodes,
    the source search - so without this the whole anime row is a dead end,
    which is exactly how it behaved: One Piece opened on an empty episode
    list and Play answered "nothing left to watch".

    The year is what makes it safe rather than a coin toss. Searching
    "ONE PIECE" returns the 2023 live-action series *first* and the 1999
    anime second, and both match the name exactly - so a name match alone
    would have opened the wrong programme with a straight face. A name and a
    year together separate them.

    Falling back to the first result is deliberate. AniList names a cour -
    "Re:ZERO -Starting Life in Another World- Season 4", year 2026 - where
    TMDB has one show from 2016, so neither the name nor the year agrees and
    the best answer is still the show TMDB thinks is called that.
    """
    names = [title] + [name for name in alternatives if name]
    for name in names:
        if not name:
            continue
        results = search(name, media_type) or []
        if not results:
            continue
        wanted = name.strip().lower()
        exact = [r for r in results
                 if (r.get("title") or "").strip().lower() == wanted]
        if year:
            for row in exact or results:
                if int(row.get("year") or 0) == int(year):
                    return (row.get("ids") or {}).get("tmdb") or 0
        if exact:
            return (exact[0].get("ids") or {}).get("tmdb") or 0
        return (results[0].get("ids") or {}).get("tmdb") or 0
    return 0


def search(query, media_type="multi", page=1, language=None):
    """Search TMDB. `language` overrides the interface's own.

    It is an override because the question a query asks is not always the one
    the interface asks. Somebody typing in Hebrew wants the Hebrew titles
    back, whatever language the buttons are in - see `unified.query_language`.
    """
    if not query:
        return []
    extra = {"language": language} if language else {}
    payload = _call("/search/%s" % media_type, ttl=TTL_SEARCH, page=page,
                    query=query, include_adult="false", **extra)
    return _results(payload, None if media_type == "multi" else media_type)


def find_by_imdb(imdb_id):
    """Resolve an IMDb id to a TMDB item."""
    if not imdb_id:
        return None
    payload = _call("/find/%s" % imdb_id, ttl=TTL_DETAILS, external_source="imdb_id")
    for row in payload.get("movie_results") or []:
        return items.from_tmdb_movie(row)
    for row in payload.get("tv_results") or []:
        return items.from_tmdb_show(row)
    return None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _attach_credits(item, payload):
    credits = payload.get("credits") or {}
    # TMDB gender: 1 female, 2 male, 0 or 3 unknown. It is kept because Hebrew
    # marks speaker gender on verbs and adjectives, so a translator that knows
    # who is speaking produces far better Hebrew than one that guesses.
    item["cast"] = [
        {
            "name": person.get("name", ""),
            "role": person.get("character", ""),
            "gender": int(person.get("gender") or 0),
            "thumb": items.image_url(person.get("profile_path"), items.PROFILE_SIZE),
        }
        for person in (credits.get("cast") or [])[:15]
    ]
    crew = credits.get("crew") or []
    item["extra"]["director"] = [c.get("name", "") for c in crew
                                 if c.get("job") == "Director"]
    item["extra"]["writer"] = [c.get("name", "") for c in crew
                               if c.get("job") in ("Writer", "Screenplay")]


def _trailer(payload):
    """A YouTube plugin URL for the first trailer, when one exists."""
    for video in (payload.get("videos") or {}).get("results") or []:
        if video.get("site") == "YouTube" and video.get("type") == "Trailer":
            return "plugin://plugin.video.youtube/play/?video_id=%s" % video.get("key")
    return ""


def _movie_certification(payload):
    wanted = region()
    for entry in (payload.get("release_dates") or {}).get("results") or []:
        if entry.get("iso_3166_1") != wanted:
            continue
        for release in entry.get("release_dates") or []:
            if release.get("certification"):
                return release["certification"]
    return ""


def _show_certification(payload):
    wanted = region()
    ratings = (payload.get("content_ratings") or {}).get("results") or []
    for entry in ratings:
        if entry.get("iso_3166_1") == wanted and entry.get("rating"):
            return entry["rating"]
    for entry in ratings:
        if entry.get("iso_3166_1") == "US" and entry.get("rating"):
            return entry["rating"]
    return ""

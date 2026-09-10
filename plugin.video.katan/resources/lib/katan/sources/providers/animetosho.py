"""AnimeTosho.

It mirrors Nyaa, TokyoTosho and AniDex, and unlike them it offers a real JSON
feed with sizes and infohashes already parsed. That makes it both cheaper and
more reliable than scraping, so it is the preferred anime provider.
"""
from ... import http
from ...sources import model
from ...utils import release

NAME = "animetosho"

# Searched by name and episode number rather than by an id. That matters
# when the aggregator retries an anime episode under a Kitsu address: the
# retry carries a cour-relative number, which is meaningless as text to an
# index whose releases are named absolutely, and matching it against one
# offers a real episode that is not the one asked for.
BY_NAME = True
BASE = "https://feed.animetosho.org"


def search(meta):
    query = _query_for(meta)
    if not query:
        return []
    payload = http.get_json("%s/json" % BASE,
                            params={"q": query, "only_tor": 1},
                            timeout=(4, 8), default=None)
    if not isinstance(payload, list):
        return []

    episode = int(meta.get("episode") or 0)
    is_episode = meta.get("type") == "episode"

    sources = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = entry.get("title") or ""
        magnet = entry.get("magnet_uri") or ""
        info_hash = entry.get("info_hash") or model.normalise_hash(magnet)
        if not title or not info_hash:
            continue
        if is_episode and not _episode_matches(entry, title, meta, episode):
            continue
        try:
            size = int(entry.get("total_size") or 0)
            seeders = int(entry.get("seeders") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        sources.append(model.from_release_name(
            title, provider=NAME, size=size, seeders=seeders,
            info_hash=info_hash, magnet=magnet))
    return sources


def _query_for(meta):
    """The name and number this index is actually going to match on.

    `search_title` is the English one, set for anime by play.build_meta,
    because the original title is Japanese and this searches release names as
    text: the Japanese title returns zero results, every time, for every
    anime tried.

    `absolute` is the episode counted from the first rather than from the
    season, because fansub groups number that way. Both fall back to what was
    used before when they are absent, so nothing else changes.
    """
    title = (meta.get("search_title") or meta.get("original_title")
             or meta.get("title") or "")
    if not title:
        return ""
    if meta.get("type") == "episode":
        number = int(meta.get("absolute") or meta.get("episode") or 1)
        return "%s %02d" % (title, number)
    return title


def _episode_matches(entry, title, meta, episode):
    """Trust the AniDB episode id when present, fall back to the name.

    AniDB numbers absolutely, so its episode id is compared against the
    absolute number when there is one - the same correction the name path
    needs, for the same reason.
    """
    absolute = int(meta.get("absolute") or 0) or episode
    if entry.get("anidb_eid") and entry.get("episode_number"):
        try:
            return int(entry["episode_number"]) in (episode, absolute)
        except (TypeError, ValueError):
            pass
    parsed = release.parse(title)
    return release.matches_episode(parsed, int(meta.get("season") or 1),
                                   episode, absolute)

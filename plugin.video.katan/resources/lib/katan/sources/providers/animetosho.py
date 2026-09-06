"""AnimeTosho.

It mirrors Nyaa, TokyoTosho and AniDex, and unlike them it offers a real JSON
feed with sizes and infohashes already parsed. That makes it both cheaper and
more reliable than scraping, so it is the preferred anime provider.
"""
from ... import http
from ...sources import model
from ...utils import release

NAME = "animetosho"
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
        title = entry.get("title") or ""
        magnet = entry.get("magnet_uri") or ""
        info_hash = entry.get("info_hash") or model.normalise_hash(magnet)
        if not title or not info_hash:
            continue
        if is_episode and not _episode_matches(entry, title, meta, episode):
            continue
        sources.append(model.from_release_name(
            title, provider=NAME,
            size=int(entry.get("total_size") or 0),
            seeders=int(entry.get("seeders") or 0),
            info_hash=info_hash,
            magnet=magnet))
    return sources


def _query_for(meta):
    title = meta.get("original_title") or meta.get("title") or ""
    if not title:
        return ""
    if meta.get("type") == "episode":
        return "%s %02d" % (title, int(meta.get("episode") or 1))
    return title


def _episode_matches(entry, title, meta, episode):
    """Trust the AniDB episode id when present, fall back to the name."""
    if entry.get("anidb_eid") and entry.get("episode_number"):
        try:
            return int(entry["episode_number"]) == episode
        except (TypeError, ValueError):
            pass
    parsed = release.parse(title)
    return release.matches_episode(parsed, int(meta.get("season") or 1), episode)

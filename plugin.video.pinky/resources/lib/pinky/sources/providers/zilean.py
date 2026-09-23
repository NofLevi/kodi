"""Zilean, a searchable index of DebridMediaManager hash lists.

Everything it returns is, by definition, something someone already had on a
debrid service, which makes it a good source of cached results. It answers with
raw torrent titles and infohashes, so the release parser does the rest.
"""
from ... import http, settings
from ...sources import model
from ...utils import release

NAME = "zilean"
BASE = "https://zilean.elfhosted.com"


def search(meta):
    base = settings.get("sources.zilean.url", BASE).rstrip("/")
    payload = _query(base, meta)
    if not payload:
        return []

    season = int(meta.get("season") or 0)
    episode = int(meta.get("episode") or 0)
    is_episode = meta.get("type") == "episode"

    sources = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        title = entry.get("raw_title") or entry.get("title") or ""
        info_hash = entry.get("info_hash") or entry.get("infoHash") or ""
        if not title or not info_hash:
            continue
        if is_episode:
            parsed = release.parse(title)
            if not release.matches_episode(parsed, season, episode):
                continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        sources.append(model.from_release_name(
            title, provider=NAME, size=size, info_hash=info_hash))
    return sources


def _query(base, meta):
    """Prefer the filtered endpoint, which understands IMDb ids and episodes."""
    ids = meta.get("ids") or {}
    imdb = ids.get("imdb")
    if imdb:
        params = {"imdbId": imdb}
        if meta.get("type") == "episode":
            params["season"] = int(meta.get("season") or 1)
            params["episode"] = int(meta.get("episode") or 1)
        found = http.get_json("%s/dmm/filtered" % base, params=params,
                              timeout=(4, 8), default=None)
        if found:
            return found

    title = meta.get("title") or ""
    if not title:
        return []
    return http.get_json("%s/dmm/search" % base,
                         params={"queryText": title}, timeout=(4, 8),
                         default=None) or []

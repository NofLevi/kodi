"""Shared adapter for Stremio-protocol stream addons.

Torrentio, Comet and MediaFusion all answer the same request shape:

    GET {base}/{config}/stream/{movie|series}/{id}.json

and return a list of streams. Writing one adapter for the protocol rather than
three near-identical providers means a fix to the parsing benefits all of them.

These services crawl on their own servers, which is exactly what a weak device
wants: one request returns dozens of already-parsed results with sizes, seeders
and cache flags, instead of the device scraping a dozen sites itself.
"""
import re

from ... import http, kodi
from ...sources import model

# Cache markers the services put in the stream name, e.g. "[RD+]" or "[TB+]".
_CACHE_TAGS = {
    "rd": "realdebrid",
    "real-debrid": "realdebrid",
    "pm": "premiumize",
    "premiumize": "premiumize",
    "tb": "torbox",
    "torbox": "torbox",
    "ad": "alldebrid",
    "alldebrid": "alldebrid",
}
_CACHE_TAG = re.compile(r"\[([a-zA-Z-]{2,12})\+?\]")

# These services decorate their stat lines with emoji, and the bust-in-
# silhouette glyph marks the seeder count. It is written as a real
# character because the re module does not understand unicode escapes.
SEEDER_GLYPH = u"\U0001F464"
_SEEDERS = re.compile(SEEDER_GLYPH + r"\s*(\d+)|(?:seeders?|\bS):?\s*(\d+)",
                      re.I)
_SEEDERS_FALLBACK = re.compile(r"(\d+)\s*(?:seeders?|seeds)\b", re.I)
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(GB|MB|GiB|MiB|TB)", re.I)


def stream_id(meta):
    """The Stremio id for a movie or episode.

    IMDb ids are the lingua franca. Anime falls back to a kitsu id when the
    title has no IMDb entry, which is common for seasonal shows.
    """
    ids = meta.get("ids") or {}
    imdb = ids.get("imdb") or ""
    if meta.get("type") == "episode":
        if imdb:
            return "series", "%s:%s:%s" % (imdb, meta.get("season") or 1,
                                           meta.get("episode") or 1)
        if ids.get("kitsu"):
            return "series", "kitsu:%s:%s" % (ids["kitsu"], meta.get("episode") or 1)
        return "", ""
    if imdb:
        return "movie", imdb
    if ids.get("kitsu"):
        return "movie", "kitsu:%s" % ids["kitsu"]
    return "", ""


def fetch(base_url, config, meta, provider_name, timeout=(4, 9)):
    """Query one Stremio stream addon and return normalised sources."""
    kind, identifier = stream_id(meta)
    if not identifier:
        return []

    base_url = base_url.rstrip("/")
    parts = [base_url]
    if config:
        parts.append(config.strip("/"))
    parts.append("stream/%s/%s.json" % (kind, identifier))
    url = "/".join(parts)

    payload = http.get_json(url, timeout=timeout, default=None)
    if not payload:
        return []
    return parse_streams(payload.get("streams") or [], provider_name)


def parse_streams(streams, provider_name):
    sources = []
    for stream in streams:
        source = _parse_stream(stream, provider_name)
        if source:
            sources.append(source)
    return sources


def _parse_stream(stream, provider_name):
    if not isinstance(stream, dict):
        return None

    # The release name lives in different fields depending on the service.
    title = (stream.get("title") or stream.get("name") or "").strip()
    behaviour = stream.get("behaviorHints") or {}
    filename = behaviour.get("filename") or ""
    release_name = filename or _first_line(title)
    if not release_name:
        return None

    info_hash = model.normalise_hash(
        stream.get("infoHash") or stream.get("url") or "")
    direct_url = stream.get("url") or ""
    if direct_url and not direct_url.startswith("http"):
        direct_url = ""

    if not info_hash and not direct_url:
        return None

    size = _size_bytes(title) or int(behaviour.get("videoSize") or 0)
    seeders = _seeders(title)
    cached_by = _cached_by(stream.get("name", "") + " " + title)

    source = model.from_release_name(
        release_name,
        provider=provider_name,
        size=size,
        seeders=seeders,
        info_hash=info_hash,
        magnet="",
        url=direct_url,
    )
    if cached_by:
        source["cached"] = True
        source["cached_by"] = cached_by
    elif direct_url:
        # A direct URL from these services always means the debrid copy is
        # ready, otherwise they would have returned a magnet instead.
        source["cached"] = True

    file_index = stream.get("fileIdx")
    if file_index is not None:
        source["file_index"] = int(file_index)
    if filename:
        source["file_name"] = filename
    return source


def _first_line(text):
    for line in (text or "").split("\n"):
        line = line.strip()
        # Skip the decorated stat lines and keep the actual release name.
        if line and not _looks_like_stats(line):
            return line
    return ""


def _looks_like_stats(line):
    if _SIZE.search(line) and len(line) < 40:
        return True
    return bool(re.match(r"^[\W\d\s]+$", line))


def _size_bytes(text):
    match = _SIZE.search(text or "")
    if not match:
        return 0
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return 0
    unit = match.group(2).lower()
    factor = {"mb": 1024 ** 2, "mib": 1024 ** 2,
              "gb": 1024 ** 3, "gib": 1024 ** 3,
              "tb": 1024 ** 4}.get(unit, 0)
    return int(value * factor)


def _seeders(text):
    """The seeder count, whichever of the alternations matched."""
    match = _SEEDERS.search(text or "") or _SEEDERS_FALLBACK.search(text or "")
    if not match:
        return 0
    for value in match.groups():
        if value:
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def _cached_by(text):
    for tag in _CACHE_TAG.findall(text or ""):
        service = _CACHE_TAGS.get(tag.lower())
        if service:
            return service
    return ""

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


_SAID = set()


def fetch(base_url, config, meta, provider_name, timeout=(4, 9),
          needs_config=False):
    """Query one Stremio stream addon and return normalised sources.

    `needs_config` is the ones whose whole answer is the blob their own web
    UI produces. Asked without it they are not merely empty, they are a
    wasted worker: Comet answers **403** and MediaFusion **200 with zero
    streams**, every search, for up to nine seconds each. Measured on the
    box this is written for, comet, mediafusion, zilean and external were all
    switched on with no configuration between them, against a pool of two
    workers and a ten second deadline - which is what
    `deadline hit after 10.0s, dropped: ktuvit` was really about.

    So it is refused here and said once, which is what this add-on does
    everywhere else rather than shipping a provider that is on and silently
    contributes nothing.
    """
    if needs_config and not config:
        if provider_name not in _SAID:
            _SAID.add(provider_name)
            kodi.log("%s is switched on with no configuration, so it can only "
                     "answer nothing; paste its config or switch it off"
                     % provider_name)
        return []
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
    """Normalise a service's streams, one bad entry costing only itself.

    These payloads come from other people's servers and their shapes drift.
    Letting one malformed stream out of two hundred abort the loop would turn
    a cosmetic upstream change into "no sources found".
    """
    sources = []
    for stream in streams:
        try:
            source = _parse_stream(stream, provider_name)
        except Exception:
            kodi.log_exception("could not read a %s stream" % provider_name)
            continue
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

    size = _size_bytes(title) or _as_int(behaviour.get("videoSize"))
    seeders = _seeders(title)
    audio = _audio_languages(title)
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
    elif direct_url and not info_hash:
        # A direct URL with nothing else to go on is taken as ready. With a
        # hash it is not an answer at all: Torrentio, given a TorBox key,
        # returns a resolve link for *every* torrent - "[TB+]" for the cached
        # ones and "[TB download]" for the rest - and assuming it meant
        # "cached" put uncached torrents on the screen as "Cached TorBox",
        # skipped the real cache check, and TorBox then refused to play them
        # ("Torrent not found in cache"). The hash goes to that check instead.
        source["cached"] = True

    if audio:
        source["audio_languages"] = audio

    file_index = _as_int(stream.get("fileIdx"), default=None)
    if file_index is not None:
        source["file_index"] = file_index
    if filename:
        source["file_name"] = filename
    return source


# Torrentio says what the audio tracks are, and it was being thrown away.
# Its stream title is three lines - the release name, the stats, then a
# languages line - and only the first was ever read. On a multi-audio file
# that third line begins "Multi Audio" and then lists a flag per track:
#
#   Multi Audio / FLAG(gb) / FLAG(it) / ... / FLAG(il)
#
# which is the provider stating, as data rather than as a guess from the
# name, that one of the audio tracks is Hebrew. Measured over 347 films:
# 21 such rows, 18 of them with seeders, on 12 titles - 8 of which had no
# playable Hebrew dub by name at all. A Disney+ remux carrying a Hebrew
# track is a better answer than a 700 MB XviD with one seeder, and nothing
# here could see it.
#
# **Only with that prefix.** A flag list on its own says a language is in
# the file somewhere and not which track: 140 of the 147 such rows carry
# ten or more flags, which is the whole Disney+ or iTunes language bundle,
# and claiming a dub off one of those would be inventing an audio track out
# of a subtitle.
_AUDIO_LINE = re.compile(r"^\s*multi[\s._-]*audio\b", re.I | re.M)
# A flag emoji is two regional indicators, and a plain substring search
# matches across two adjacent flags - Finland then Lithuania is F,I,L,T and
# the I,L inside it reads as Israel. Runs are split into pairs first.
_INDICATOR_RUN = re.compile("[🇦-🇿]+")
# Only the ones this add-on has any use for.
_FLAG_LANGUAGE = {"IL": "he", "GB": "en", "US": "en", "SA": "ar", "RU": "ru",
                  "FR": "fr", "DE": "de", "ES": "es", "IT": "it", "TR": "tr",
                  "JP": "ja", "KR": "ko", "CN": "zh", "PT": "pt", "PL": "pl"}


def _flags(text):
    """The country codes of every flag emoji in `text`, in order."""
    out = []
    for run in _INDICATOR_RUN.findall(text or ""):
        for at in range(0, len(run) - 1, 2):
            out.append("".join(
                chr(ord(char) - 0x1F1E6 + ord("A")) for char in run[at:at + 2]))
    return out


def _audio_languages(title):
    """The audio tracks this stream says it has, where it says so.

    Empty unless the languages line names itself as audio - see above.
    """
    for line in (title or "").splitlines():
        if not _AUDIO_LINE.search(line):
            continue
        found = []
        for code in _flags(line):
            language = _FLAG_LANGUAGE.get(code)
            if language and language not in found:
                found.append(language)
        return found
    return []


def _as_int(value, default=0):
    """A number from a field these services do not always send as one.

    videoSize has arrived as a string and as a float, and a fileIdx that is
    not a number at all is better ignored than allowed to pick a file.
    """
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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

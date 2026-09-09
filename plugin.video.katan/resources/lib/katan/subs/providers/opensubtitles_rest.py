"""OpenSubtitles without an account, which is the only version of it that
this add-on can actually use.

`opensubtitles.com` - the modern one, which `opensubtitles.py` speaks - needs a
registered API consumer, and its free tier is **five downloads a day**. This
add-on downloads exactly one subtitle per playback by design, so five a day is
five programmes a day for a whole household, and the next tier up is $20 a
month. For a family television that is not a subtitle provider, it is a
countdown.

`rest.opensubtitles.org` is the older search API and it still answers. Measured
on 9 September 2026, anonymously, with nothing but a User-Agent:

    search Silo S01E01 English   HTTP 200, 4 results
    search Silo S01E01 Hebrew    HTTP 200, 3 results
    download the first Hebrew    18 KB gzip -> 57 KB of SRT, real cues

That last step is the one worth stating, because a search that works and a
download that is gated would be worse than nothing: `SubDownloadLink` is
fetched with no token and no session.

Two things to know before relying on it.

**It is the legacy service.** This project has watched SubSource move behind a
login and AniList go dark, and this is a better candidate for that than most.
It is therefore an addition beside Wizdom rather than a replacement for it, and
everything degrades to what it was if this stops answering.

**The BOM is not decoration.** The first cue of the Hebrew file measured above
begins with one, and Hebrew subtitles here arrive as cp1255 as often as UTF-8.
`srt.decode` already handles both, because both have been shipped bugs.
"""
import gzip
import io as _io
import urllib.parse

from ... import http, kodi
from . import common

NAME = "opensubtitles_rest"
BASE = "https://rest.opensubtitles.org/search"

# The service identifies clients by User-Agent and refuses an empty one. A
# real registered agent would be better manners; until this add-on has one,
# the documented temporary agent is what its own instructions offer.
USER_AGENT = "TemporaryUserAgent"

# ISO 639-2, which is what this API speaks. Kodi and the rest of this add-on
# use two-letter codes, so the translation lives here rather than leaking.
THREE_LETTER = {
    "he": "heb", "en": "eng", "ar": "ara", "ru": "rus", "es": "spa",
    "fr": "fre", "de": "ger", "it": "ita", "pt": "por", "tr": "tur",
    "pl": "pol", "nl": "dut", "ro": "rum", "cs": "cze", "hu": "hun",
    "uk": "ukr", "ja": "jpn", "ko": "kor", "zh": "chi", "hi": "hin",
}

MAX_PER_LANGUAGE = 12


def supports(language):
    return language in THREE_LETTER


def search(meta, target, languages):
    """Ask once per language, because the API keys the whole query on one."""
    results = []
    for language in languages:
        code = THREE_LETTER.get(language)
        if not code:
            continue
        results.extend(_search_one(meta, language, code))
    return results


def _search_one(meta, language, code):
    ids = meta.get("ids") or {}
    imdb = str(ids.get("imdb") or "").replace("tt", "").strip()

    # Ordered, because the path is positional rather than a query string. An
    # IMDb id is far more precise than a title, so it is used when there is
    # one and the title is the fallback.
    parts = []
    if meta.get("type") == "episode":
        parts.append("episode-%d" % int(meta.get("episode") or 1))
    if imdb:
        parts.append("imdbid-%s" % imdb)
    else:
        title = meta.get("title") or ""
        if not title:
            return []
        # Lowercased, and that is not tidiness. A capitalised query answers
        # **302** to a redirect this add-on cannot follow - urllib reports it
        # as `getaddrinfo failed`, which reads like the network being down
        # rather than like the one character that caused it. Measured: "Silo"
        # 302, "silo" 200; "Mousetrap" 302, "mousetrap" 200 with 5 results.
        parts.append("query-%s" % urllib.parse.quote(title.lower()))
    if meta.get("type") == "episode":
        parts.append("season-%d" % int(meta.get("season") or 1))
    parts.append("sublanguageid-%s" % code)

    payload = http.get_json(
        "%s/%s" % (BASE, "/".join(sorted(parts))),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=(5, 12), default=None)

    # A miss is an empty list; anything else is the service having a bad day
    # and is worth one line rather than silence.
    if payload is None:
        kodi.log("opensubtitles.org did not answer for %s" % language)
        return []
    if not isinstance(payload, list):
        return []

    results = []
    for entry in payload[:MAX_PER_LANGUAGE]:
        link = entry.get("SubDownloadLink")
        if not link:
            continue
        results.append(common.candidate(
            NAME, language,
            entry.get("MovieReleaseName") or entry.get("SubFileName") or "",
            link,
            downloads=_number(entry.get("SubDownloadsCnt")),
            # The service reports whether the subtitle was matched to a file
            # by hash. That is the strongest evidence a candidate can carry,
            # and the matcher already scores a hash match at 100.
            hash_match=str(entry.get("MatchedBy") or "") == "moviehash",
        ))
    return results


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def download(candidate):
    """Fetch and un-gzip. The API serves .gz rather than the zip Wizdom does."""
    data = common.fetch_bytes(candidate["download"],
                              headers={"User-Agent": USER_AGENT})
    if not data:
        return b""
    if data[:2] != b"\x1f\x8b":
        # Already plain, or an error page. Let the extractor decide.
        return common.extract_subtitle(data, candidate.get("language", ""))
    try:
        return gzip.GzipFile(fileobj=_io.BytesIO(data)).read()
    except (IOError, OSError, EOFError):
        kodi.log("opensubtitles.org sent something that is not gzip")
        return b""

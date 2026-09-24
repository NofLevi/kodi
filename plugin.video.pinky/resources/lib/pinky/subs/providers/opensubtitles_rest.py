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


def search(meta, target, languages, video_hash="", video_size=0):
    """Ask once per language, because the API keys the whole query on one.

    Twice per language when there is a file hash, because a hash query and a
    title query are different questions and the hash one is worth far more: it
    is the only evidence that a subtitle belongs to *this file* rather than to
    something with the same name, and the matcher scores it at 100.
    """
    from . import common

    # One title query per way this episode is numbered. For almost everything
    # that is one. For anime it can be two, because TMDB numbers a
    # long-running series by season and OpenSubtitles files it the way IMDb
    # does - often one season counted from the first episode. Asking TMDB's
    # numbering alone is how anime came to get nothing on 48% of titles.
    numberings = common.episode_numberings(meta) or [None]
    results = []
    for language in languages:
        code = THREE_LETTER.get(language)
        if not code:
            continue
        if video_hash:
            results.extend(_search_one(meta, language, code,
                                       video_hash=video_hash,
                                       video_size=video_size))
        for numbering in numberings:
            asked = meta if numbering is None else dict(
                meta, season=numbering[0], episode=numbering[1])
            results.extend(_search_one(asked, language, code))
    return results


def _imdb_agrees(entry, meta):
    """Is this row filed under the title we are actually watching?

    True, False, or None when there is nothing to compare. This is not
    pedantry: the hash of one real Breaking Bad episode is registered in their
    database against *three* titles - Breaking Bad, The Vampire Diaries and a
    Bollywood film - all four rows claiming `MatchedBy: moviehash`. Uploaders
    mis-register hashes, and without this check the first row wins at a score
    of 100, above every other kind of evidence, and the wrong-episode guard
    does not catch it because both are S01E01.

    Episodes are filed under the *episode's* imdb id and we carry the show's,
    so `SeriesIMDBParent` is the field that compares - and it is right there in
    the response.

    **Not asked of a name query**, which is the whole point of a name query.
    One show can have several IMDb entries and an uploader files against
    whichever they were looking at: Hikaru no Go has at least 0426711,
    0303461 and 13364846, and the only English subtitle for episode 32 is
    under the third. Having deliberately gone around the id because it found
    nothing, throwing the answer away for disagreeing with that same id is
    the search cancelling itself out. The name and the episode number are
    what constrained that query, and the wrong-episode guard downstream is
    what checks the answer.
    """
    ours = str((meta.get("ids") or {}).get("imdb") or "")
    ours = ours.replace("tt", "").lstrip("0")
    if not ours:
        return None
    if meta.get("type") == "episode":
        theirs = str(entry.get("SeriesIMDBParent") or "").lstrip("0")
    else:
        theirs = str(entry.get("IDMovieImdb") or "").lstrip("0")
    if not theirs:
        return None
    return theirs == ours


def _search_one(meta, language, code, video_hash="", video_size=0):
    ids = meta.get("ids") or {}
    imdb = str(ids.get("imdb") or "").replace("tt", "").strip()

    # Ordered, because the path is positional rather than a query string. An
    # IMDb id is far more precise than a title, so it is used when there is
    # one and the title is the fallback.
    parts = []
    if video_hash:
        # A hash query stands alone: adding a title or an episode narrows it
        # to nothing, because the row is filed against the file rather than
        # against the name.
        parts = ["moviehash-%s" % video_hash]
        if video_size:
            parts.append("moviebytesize-%s" % video_size)
        parts.append("sublanguageid-%s" % code)
        return _fetch(meta, language, parts, hash_query=True,
                      video_size=video_size)

    if meta.get("type") == "episode":
        parts.append("episode-%d" % int(meta.get("episode") or 1))
        parts.append("season-%d" % int(meta.get("season") or 1))
    parts.append("sublanguageid-%s" % code)

    if imdb:
        found = _fetch(meta, language, parts + ["imdbid-%s" % imdb])
        if found:
            return found
        # An id query that finds nothing is not the same as there being
        # nothing. Measured on Hikaru no Go 2x02, which is S01E32 to everyone
        # but TMDB: `imdbid-0426711` knows English for episodes 1 to 30 and
        # stops, while the same season and episode asked *by name* returns
        # "Hikaru No Go S01E32.en.vtt". An upload is filed against an id only
        # if whoever uploaded it said so, and for a seventy-five episode anime
        # most of them did not - so the picker offered one Japanese subtitle
        # and called it a choice.
        #
        # Only after the id query comes back empty, so it costs one request in
        # exactly the case that currently returns nothing, and none at all in
        # the common one.
    return _fetch(meta, language, parts + _by_name(meta), by_name=True)


def _by_name(meta):
    """The title as a query fragment, or nothing if there is no title.

    Lowercased, and that is not tidiness. A capitalised query answers **302**
    to a redirect this add-on cannot follow - urllib reports it as
    `getaddrinfo failed`, which reads like the network being down rather than
    like the one character that caused it. Measured: "Silo" 302, "silo" 200;
    "Mousetrap" 302, "mousetrap" 200 with 5 results.

    A series is asked for under the show's name, not the episode's. `title`
    is the episode's own name on an episode, and "The Last Day of the
    Preliminaries" is not what anybody filed a subtitle under.
    """
    title = meta.get("show_title") or meta.get("title") or ""
    if not title:
        return []
    return ["query-%s" % urllib.parse.quote(title.lower())]


def _fetch(meta, language, parts, hash_query=False, video_size=0,
           by_name=False):
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
        if not isinstance(entry, dict):
            continue
        link = entry.get("SubDownloadLink")
        if not link:
            continue
        agrees = None if by_name else _imdb_agrees(entry, meta)
        if agrees is False:
            # Somebody else's programme, filed against our file's hash.
            continue
        try:
            exact_size = (int(video_size) > 0
                          and int(entry.get("MovieByteSize") or 0)
                          == int(video_size))
        except (TypeError, ValueError):
            exact_size = False
        results.append(common.candidate(
            NAME, language,
            entry.get("MovieReleaseName") or entry.get("SubFileName") or "",
            link,
            downloads=_number(entry.get("SubDownloadsCnt")),
            uploader=str(entry.get("UserNickName") or "").strip(),
            # A hash match scores 100 only for our own hash request, the exact
            # byte size, and the same title. The legacy index contains
            # mis-filed hashes, so any missing check makes certainty a guess.
            hash_match=(hash_query and exact_size
                        and str(entry.get("MatchedBy") or "") == "moviehash"
                        and agrees is True),
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
        return common.extract_subtitle(data, candidate.get("language", ""),
                                       candidate=candidate)
    return common.decompress_gzip(data)

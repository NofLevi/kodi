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

# The reverse of THREE_LETTER, for a query that asks for no language at all
# and has to read each row's own.
TWO_LETTER = dict((three, two) for two, three in THREE_LETTER.items())

# A clock needs two independent timelines, not a library. Two rows per
# language is enough to find an independent pair and keeps a popular title -
# Naruto answers a language-less query with a hundred rows in twenty-three
# languages - from becoming a hundred parsed subtitles on a projector.
EVIDENCE_PER_LANGUAGE = 2
MAX_EVIDENCE = 12


def supports(language):
    return language in THREE_LETTER


def search_any_language(meta, target):
    """Every language this episode has, in one request, purely as a clock.

    Asking per language is what the rest of this file does, and for timing
    evidence it is the wrong shape: five languages is five requests against a
    host that rate-limits, and it still misses the ones nobody thought to
    list. Dropping `sublanguageid` returns them all at once - measured on 27
    September 2026: Hikaru no Go 1x33 answers with three languages, Black
    Lagoon 1x04 with seventeen, Naruto 1x27 with twenty-three, each in a
    single request.

    That matters because a language which is a poor *translation* source is
    a perfectly good *clock*. Japanese, Korean and Chinese are kept out of
    `AI_SOURCE_LANGUAGES` because they drop the subject and leave the model
    guessing gender, which has nothing whatever to do with when somebody
    speaks. Hikaru no Go 2x03 is the case: three subtitles exist in the
    world, and the Chinese one is the third - without it there are only two
    and the cross-language proof is one short.

    Every row is marked `evidence`, because these are a ruler and not a
    subtitle: nothing may translate from them or put them on screen.
    """
    numberings = common.episode_numberings(meta) or [None]
    rows = []
    for numbering in numberings:
        asked = meta if numbering is None else dict(
            meta, season=numbering[0], episode=numbering[1])
        rows.extend(_search_one(asked, None, None))
        if rows:
            break

    per_language = {}
    evidence = []
    for candidate in rows:
        language = candidate.get("language") or ""
        if not language:
            continue
        if per_language.get(language, 0) >= EVIDENCE_PER_LANGUAGE:
            continue
        per_language[language] = per_language.get(language, 0) + 1
        candidate["evidence"] = True
        evidence.append(candidate)
        if len(evidence) >= MAX_EVIDENCE:
            break
    return evidence


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
    if code:
        parts.append("sublanguageid-%s" % code)

    anime = bool((meta.get("extra") or {}).get("anime"))
    found = []
    if imdb:
        found = _fetch(meta, language, parts + ["imdbid-%s" % imdb])
        # Anime is asked by name as well, not only when the id finds nothing.
        # An anime upload is filed against an id so rarely that a non-empty
        # answer is usually the wrong half of the corpus: Naruto Shippuden
        # 3x55 answered with two rows, "055_LEG" and "155_LEG", and that was
        # enough to stop the name query that finds the fansub releases.
        if found and not anime:
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
    results = list(found)
    seen = set(row.get("download") for row in results)
    for name in _by_name(meta):
        for row in _fetch(
                meta, language,
                parts + ["query-%s" % urllib.parse.quote(name.lower())],
                by_name=True):
            if row.get("download") not in seen:
                seen.add(row.get("download"))
                results.append(row)
    return results


def _by_name(meta):
    """The name or names worth asking under, or nothing if there is none.

    Lowercased, and that is not tidiness. A capitalised query answers **302**
    to a redirect this add-on cannot follow - urllib reports it as
    `getaddrinfo failed`, which reads like the network being down rather than
    like the one character that caused it. Measured: "Silo" 302, "silo" 200;
    "Mousetrap" 302, "mousetrap" 200 with 5 results.

    A series is asked for under the show's name, not the episode's. `title`
    is the episode's own name on an episode, and "The Last Day of the
    Preliminaries" is not what anybody filed a subtitle under. `search_title`
    is preferred over the raw title because for anime it already *is* the
    English name - `_name_it_the_way_the_indexes_do` computed it for exactly
    this reason - and because `title` can be Hebrew: `tmdb.language()`
    follows the UI language, so a Hebrew household's `meta["title"]` is a
    Hebrew string, and a Hebrew-script query to this index returns nothing.
    A series that is not anime has `english_title` for the same reason, from
    `play._name_it_in_every_language`.

    An anime release is as likely to be filed under its Japanese romaji name
    as its English one - fansub and raw groups both use it - and the two are
    not the same query: asked for My Hero Academia 1x01 in English,
    "my hero academia" returned 10 rows and "boku no hero academia" 9, nine
    of them the same file, but Naruto Shippuden 1x01 told the other story -
    "naruto shippuuden" returned a HorribleSubs upload that "naruto
    shippuden" did not, because this index matches against its own title
    mapping rather than the filename and the two names do not map to
    identically the same rows. `meta["aliases"]` is where
    `_name_it_the_way_the_indexes_do` puts that name; capped at two so a
    title with several regional respellings costs at most three requests
    total, only in the case that already costs one.
    """
    names = []
    primary = meta.get("search_title") or meta.get("english_title") \
        or meta.get("show_title") or meta.get("title") or ""
    if primary:
        names.append(primary)
    for alias in (meta.get("aliases") or [])[:2]:
        if alias and alias not in names:
            names.append(alias)
    return names


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
        row_language = language or TWO_LETTER.get(
            str(entry.get("SubLanguageID") or "").strip().lower(),
            str(entry.get("SubLanguageID") or "").strip().lower())
        if not row_language:
            continue
        results.append(common.candidate(
            NAME, row_language,
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

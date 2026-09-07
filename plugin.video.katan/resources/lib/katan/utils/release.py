"""Parsing release names.

Two very different features depend on this one parser:

* Source ranking, which needs resolution, codec, HDR flags and size sanity.
* Subtitle correlation, where matching the release group between the video and
  the subtitle is the single strongest signal that the timings will line up.

It is pure string work with no Kodi imports, so it is cheap and easy to test.
"""
import functools
import re

RESOLUTIONS = [
    ("2160p", r"\b(2160p|4k|uhd|ultrahd)\b"),
    ("1080p", r"\b(1080p|1080i|fullhd|fhd)\b"),
    ("720p", r"\b(720p|hd)\b"),
    ("480p", r"\b(480p|576p|sd)\b"),
]

SOURCES = [
    ("bluray", r"\b(blu-?ray|bdrip|brrip|bdremux|remux|bd25|bd50)\b"),
    ("web", r"\b(web-?dl|webrip|web|amzn|nf|dsnp|hmax|atvp|hulu|itunes)\b"),
    ("hdtv", r"\b(hdtv|pdtv|dsr)\b"),
    ("dvd", r"\b(dvdrip|dvd-?r|ntsc|pal)\b"),
    ("cam", r"\b(cam|camrip|ts|telesync|tc|telecine|hdts|hdcam|scr|screener)\b"),
]

CODECS = [
    ("av1", r"\b(av1)\b"),
    ("h265", r"\b(x265|h\.?265|hevc)\b"),
    ("h264", r"\b(x264|h\.?264|avc)\b"),
    ("xvid", r"\b(xvid|divx)\b"),
]

AUDIO = [
    ("atmos", r"\b(atmos)\b"),
    ("truehd", r"\b(truehd|true-hd)\b"),
    ("dtshd", r"\b(dts-?hd|dts-?x|dtsma)\b"),
    ("dts", r"\b(dts)\b"),
    ("eac3", r"\b(eac3|ddp|dd\+|e-ac-3)\b"),
    ("ac3", r"\b(ac3|dd5|dd2|dolby ?digital)\b"),
    ("aac", r"\b(aac)\b"),
]

HDR_PATTERNS = {
    "dv": r"\b(dv|dolby-?vision|dovi)\b",
    # No trailing word boundary: "+" is not a word character,
    # so a pattern ending in one could never match a real name.
    "hdr10plus": r"\bhdr10\s?\+|\bhdr10plus\b",
    "hdr": r"\b(hdr|hdr10|pq|bt2020)\b",
}

# Language hints that appear in release names. Hebrew releases are the ones we
# care most about, because they usually carry burned-in or muxed Hebrew subs.
LANGUAGE_PATTERNS = {
    "he": r"\b(hebsub|hebsubs|hebrew|heb|hebdub|\u05e2\u05d1\u05e8\u05d9\u05ea)\b",
    "en": r"\b(english|eng)\b",
    "multi": r"\b(multi|multisub|multisubs|dual|dual-?audio)\b",
}

# "+" is deliberately NOT stripped: HDR10+ and DD+ depend on it.
_JUNK = re.compile(r"[\[\]\(\)\{\}_.]+")
_GROUP_TAIL = re.compile(r"-([A-Za-z0-9]{2,20})$")

# A bracket at the *end* is the site that re-hosted it, or a CRC, and never
# the release group: "silo.s01e01.1080p.web.h264-ggwp[eztv.re].mkv" is a ggwp
# release. A bracket at the *start* is the opposite - that is exactly how
# anime names its group - which is why only the trailing ones come off.
_TRAILING_BRACKET = re.compile(r"(?:\s*[\[(][^\[\]()]{1,30}[\])])+$")

# A leading bracket is how anime names its group - [SubsPlease], [Erai-raws] -
# and also how a torrent site stamps its name on a file it did not make:
# "[COOL-TORENTS.PL]Silo.S01E01...-Ralf.mkv" is a Ralf release. A domain is
# the tell, so anything holding a dot or saying "torrent" is a site and the
# real group is looked for behind it.
_SITE_BRACKET = re.compile(r"\.|www|torrent|\.com|\.net|\.org", re.I)


_GROUP_DOT_TAIL = re.compile(r"\.([A-Za-z][A-Za-z0-9]{1,19})$")

# Everything a dot-separated tail could be other than a release group. The
# dash form needs no such list because a dash is already the scene's own
# marker for "what follows is the group"; a dot separates every token in the
# name, so the last one has to be identified rather than assumed.
_NOT_A_GROUP = frozenset((
    "1080p", "1080i", "720p", "2160p", "480p", "576p", "4k", "uhd", "hd", "sd",
    "x264", "x265", "h264", "h265", "avc", "hevc", "av1", "xvid", "divx",
    "web", "webrip", "webdl", "bluray", "bdrip", "brrip", "remux", "hdtv",
    "dvdrip", "dvd", "cam", "hdrip", "proper", "repack", "extended", "unrated",
    "internal", "limited", "complete", "multi", "dual", "subs", "sub",
    "aac", "ac3", "eac3", "ddp", "dts", "dtshd", "truehd", "atmos", "flac",
    "mp3", "opus", "10bit", "8bit", "hdr", "hdr10", "dv", "sdr", "imax",
))
_GROUP_BRACKET = re.compile(r"^\[([^\]]{2,20})\]")
_SEASON_EPISODE = re.compile(
    r"\bs(\d{1,2})[\s._-]?e(\d{1,3})\b|\b(\d{1,2})x(\d{1,3})\b", re.I)
_SEASON_ONLY = re.compile(r"\bs(\d{1,2})\b", re.I)
_ABSOLUTE_EPISODE = re.compile(r"\s-\s(\d{1,4})(?:\s|$|v\d)")

# The dash form above - "Show - 12" - is the tidy convention and plenty of
# groups do not follow it. These three were found by surveying a thousand
# titles rather than by imagining them, and are shown here as `normalise`
# leaves them, with every bracket already flattened to a space:
#
#   ksn katekyo hitman reborn! 149 576p h264 b036f3bf     a bare number
#   late bleach tybw 46 v2 web x264                       a version suffix
#   yonkou one piece 539 hd 01891224                      underscores
#
# What tells an episode number from every other number in a release name is
# what comes *after* it: a resolution, a source, a codec, or a version tag.
# "the matrix 1999 1080p bluray" would qualify on that rule alone, which is
# why years are refused outright - and no anime has run for nineteen hundred
# episodes, so nothing real is lost.
_AFTER_EPISODE = (
    r"\d{3,4}p|4k|uhd|hd|sd|x26[45]|h\.?26[45]|hevc|avc|av1|xvid|divx"
    r"|web|webrip|web-dl|bluray|blu-ray|bdrip|brrip|bdremux|remux|hdtv"
    r"|dvdrip|aac|ac3|eac3|ddp|dts|truehd|atmos|flac|opus|multi|dual"
    r"|batch|complete|10bit|8bit|v\d")
_BARE_EPISODE = re.compile(
    r"\b(\d{1,4})\s+(?:%s)\b" % _AFTER_EPISODE, re.I)

# "episode 930" says so in as many words, and the word is the whole signal.
_NAMED_EPISODE = re.compile(r"\bep(?:isode)?\s*(\d{1,4})\b", re.I)

# A batch or a range: "(125-203)", "[Complete Episodes 1 - 203]", "01-12".
# It is a pack containing the episode rather than the episode itself, and
# saying so is what lets a fansub batch answer for an episode inside it -
# which for a long-running anime is often the only thing anybody is seeding.
_EPISODE_RANGE = re.compile(r"\b(\d{1,4})\s*-\s*(\d{1,4})\b")


_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_PROPER = re.compile(r"\b(proper|repack|rerip|fixed)\b", re.I)
_3D = re.compile(r"\b(3d|sbs|hsbs|half-?ou)\b", re.I)


@functools.lru_cache(maxsize=1024)
def _normalised(name):
    text = _JUNK.sub(" ", name)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _is_a_site(text):
    return bool(_SITE_BRACKET.search(text or ""))


def strip_site_tags(name):
    """Take the torrent site's stamp off a file name, leaving the release.

    A site brands what it re-hosts - "[ OxTorrent.com ] Les evades (1994) -
    1080p ..." is the same file as "Les evades (1994) - 1080p ...", and both
    were sitting in the picker. Only a leading bracket that looks like a
    domain is removed, so an anime group in the same position survives.
    """
    stem = str(name or "").strip()
    while True:
        bracket = _GROUP_BRACKET.match(stem)
        if not bracket or not _is_a_site(bracket.group(1)):
            break
        stem = stem[bracket.end():].strip()
    return _TRAILING_BRACKET.sub("", stem).strip()


def _is_a_year(number):
    return 1900 <= number <= 2099


def normalise(name):
    """Lower-case a release name and flatten its separators to spaces.

    Memoised for the same reason `parse` is, and it turned out to matter
    more: comparing one subtitle against one release normalises *both* names,
    and the picker does that for every subtitle against every release. On a
    search with 240 sources and 32 Hebrew subtitles that is 7,680 pairs and
    15,360 normalisations of 272 distinct strings.
    """
    return _normalised(str(name)) if name else ""


def _first_match(text, table, default=""):
    for label, pattern in table:
        if re.search(pattern, text, re.I):
            return label
    return default


def parse(name, size=0):
    """Break a release name into the fields ranking and matching need.

    Memoised, because the same names are parsed over and over and parsing is
    two dozen regular expressions. Measured on a search returning 240 sources
    with 32 Hebrew subtitles to weigh against them: the subtitle outlook
    alone asked for **8,160 parses of 272 distinct names**, because it scores
    every candidate against every source and each scoring parsed the
    candidate's name again. Ranking added three more per source - the
    rejection check, the source-type weight and the proper check each parsed
    the same title independently.

    A copy is handed out rather than the cached dictionary itself. Nothing
    writes into a parse result today, and this is what keeps that true
    cheaply: the copy costs well under a microsecond against sixty-odd for
    the parse.

    512 entries is comfortably more than one search needs and about a third
    of a megabyte, which on the device this is written for is worth spending
    once rather than paying for in regular expressions every time.
    """
    return dict(_parse(str(name or ""), size))


@functools.lru_cache(maxsize=512)
def _parse(raw, size=0):
    text = normalise(raw)

    hdr = [flag for flag, pattern in HDR_PATTERNS.items()
           if re.search(pattern, text, re.I)]
    # HDR10+ implies HDR; keep the list tidy so scoring does not double count.
    if "hdr10plus" in hdr and "hdr" in hdr:
        hdr.remove("hdr")

    languages = [code for code, pattern in LANGUAGE_PATTERNS.items()
                 if re.search(pattern, text, re.I)]

    season, episode, absolute = _episode_numbers(text)

    return {
        "raw": raw,
        "normalised": text,
        # "unknown", not "sd". A release name that does not say its
        # resolution has not told us it is standard definition - it has told
        # us nothing - and calling that SD is a claim rather than a reading.
        # Fansub names very often omit it, so the minimum-resolution filter
        # was throwing away 1080p anime releases as though they were SD:
        # thirteen of the forty-four copies of one Bleach episode.
        "resolution": _first_match(text, RESOLUTIONS, "unknown"),
        "source": _first_match(text, SOURCES, "unknown"),
        "codec": _first_match(text, CODECS, "unknown"),
        "audio": _first_match(text, AUDIO, "unknown"),
        "hdr": hdr,
        "languages": languages,
        "group": release_group(raw),
        "year": _year(text),
        "season": season,
        "episode": episode,
        "absolute": absolute,
        "episode_range": _episode_range(text),
        "proper": bool(_PROPER.search(text)),
        "three_d": bool(_3D.search(text)),
        "size": int(size or 0),
    }


# What a subtitle file is decorated with, after the release name it was made
# for: the language it is in, sometimes a "forced" or "sdh" flag, and a
# subtitle extension. All of it lands after the release group.
_SUBTITLE_TAGS = ("heb", "hebrew", "he", "eng", "english", "en", "ara",
                  "arabic", "spa", "spanish", "rus", "russian", "fre",
                  "french", "ger", "german", "forced", "sdh", "hi", "cc",
                  "default", "und")
_SUBTITLE_EXTENSIONS = ("srt", "sub", "ass", "ssa", "vtt", "idx", "smi", "txt")
_VIDEO_EXTENSIONS = ("mkv", "mp4", "avi", "m4v", "ts")

_TRAILING_TAG = re.compile(
    r"[.\-_ ](%s)$" % "|".join(_SUBTITLE_TAGS + _SUBTITLE_EXTENSIONS
                               + _VIDEO_EXTENSIONS), re.I)


@functools.lru_cache(maxsize=1024)
def strip_subtitle_tags(name):
    """Take the decoration off a subtitle file name, leaving the release.

    This is worth more than it looks. The release group is the strongest
    subtitle-matching signal there is - groups mux their own timings, so a
    subtitle made for a group release fits that release and usually only that
    one - and the decoration was hiding it in exactly the file names where it
    matters most. "X.1080p.BluRay.x264-AMIABLE.heb.srt" was reading as a
    release by a group called "heb", and "X.1080p.BluRay.x264-AMIABLE.srt" as
    having no group at all, because only video extensions were being stripped.

    Repeated rather than done once, because the tags stack: a file is
    routinely "...-GROUP.forced.heb.srt".
    """
    stem = str(name or "").strip()
    while True:
        shorter = _TRAILING_TAG.sub("", stem)
        if shorter == stem:
            return stem
        stem = shorter


def release_group(name):
    """The scene or p2p group, which is the strongest subtitle-match signal."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    bracket = _GROUP_BRACKET.match(raw)
    if bracket and not _is_a_site(bracket.group(1)):
        return bracket.group(1).lower()
    if bracket:
        raw = raw[bracket.end():].strip()
    stem = _TRAILING_BRACKET.sub("", strip_subtitle_tags(raw)).strip()
    stem = strip_subtitle_tags(stem)
    tail = _GROUP_TAIL.search(stem)
    if tail:
        candidate = tail.group(1).lower()
        # "the-office" style titles end in a word, not a group
        if candidate not in _NOT_A_GROUP:
            return candidate

    # A dot-separated group, which is common enough to matter: Wizdom returns
    # "The.Film.1994.1080p.x264.YIFY" and the group is the strongest signal
    # this has. Only attempted on something that already looks like a release
    # name, because otherwise the last word of any title would be read as a
    # group - "The.Office" would be by a group called Office.
    if _looks_like_a_release(stem):
        dotted = _GROUP_DOT_TAIL.search(stem)
        if dotted:
            candidate = dotted.group(1).lower()
            if candidate not in _NOT_A_GROUP:
                return candidate
    return ""


def _looks_like_a_release(text):
    """Does this name carry the marks of a release rather than a plain title?"""
    lowered = text.lower()
    for table in (RESOLUTIONS, SOURCES, CODECS):
        for _name, pattern in table:
            if re.search(pattern, lowered):
                return True
    return False


def _year(text):
    matches = _YEAR.findall(text)
    return int(matches[0]) if matches else 0


def _episode_numbers(text):
    match = _SEASON_EPISODE.search(text)
    if match:
        if match.group(1) is not None:
            return int(match.group(1)), int(match.group(2)), 0
        return int(match.group(3)), int(match.group(4)), 0
    absolute = _ABSOLUTE_EPISODE.search(text)
    if absolute:
        # A season marker alongside the number changes what the number means.
        # "[AnimeRG] Shingeki no Kyojin S3 - 11" is season three episode
        # eleven, not absolute episode eleven - and refusing it as the wrong
        # episode threw away a correct source, which is worse than the loose
        # matching it was meant to prevent. Both readings are kept and
        # matches_episode tries each.
        alongside = _SEASON_ONLY.search(text)
        return (int(alongside.group(1)) if alongside else 0), 0,             int(absolute.group(1))
    named = _NAMED_EPISODE.search(text)
    if named and not _is_a_year(int(named.group(1))):
        return 0, 0, int(named.group(1))
    for bare in _BARE_EPISODE.finditer(text):
        number = int(bare.group(1))
        if number and not _is_a_year(number):
            return 0, 0, number
    season_only = _SEASON_ONLY.search(text)
    if season_only:
        return int(season_only.group(1)), 0, 0      # a season pack
    return 0, 0, 0


def _episode_range(text):
    """The span of episodes a batch covers, or None."""
    for match in _EPISODE_RANGE.finditer(text):
        first, last = int(match.group(1)), int(match.group(2))
        if first >= last or _is_a_year(first) or _is_a_year(last):
            continue
        if last - first < 1 or last > 3000:
            continue
        return (first, last)
    return None


def matches_episode(parsed, season, episode, absolute=None):
    """Does a parsed release name refer to this episode?

    A season pack has no episode number and is treated as a match, because the
    debrid layer can still pick the right file out of it.

    `absolute` is the episode counted from the first rather than from the
    season, which is how fansub groups number anime. Without it, an
    absolutely-numbered release was compared against the *season-relative*
    number - so "season 8, episode 14" of Reborn matched a release named
    episode 14, which is in season one. It defaults to the season-relative
    number, which is correct for a single-season show and is what every
    caller that does not know any better gets.
    """
    wanted = int(absolute) if absolute else int(episode)

    if parsed["season"] and parsed["episode"]:
        return parsed["season"] == int(season) and parsed["episode"] == int(episode)

    if parsed["absolute"]:
        if parsed["absolute"] == wanted:
            return True
        # The number is season-relative when the name also states a season.
        return bool(parsed["season"]
                    and parsed["season"] == int(season)
                    and parsed["absolute"] == int(episode))

    if parsed["season"]:
        return parsed["season"] == int(season)        # season pack
    span = parsed.get("episode_range")
    if span and span[0] <= wanted <= span[1]:
        return True                                   # a batch containing it
    return False


def size_label(size_bytes):
    if not size_bytes:
        return ""
    gigabytes = float(size_bytes) / (1024 ** 3)
    if gigabytes >= 1:
        return "%.2f GB" % gigabytes
    return "%.0f MB" % (float(size_bytes) / (1024 ** 2))

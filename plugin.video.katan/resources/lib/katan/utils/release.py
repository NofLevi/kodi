"""Parsing release names.

Two very different features depend on this one parser:

* Source ranking, which needs resolution, codec, HDR flags and size sanity.
* Subtitle correlation, where matching the release group between the video and
  the subtitle is the single strongest signal that the timings will line up.

It is pure string work with no Kodi imports, so it is cheap and easy to test.
"""
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
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_PROPER = re.compile(r"\b(proper|repack|rerip|fixed)\b", re.I)
_3D = re.compile(r"\b(3d|sbs|hsbs|half-?ou)\b", re.I)


def normalise(name):
    """Lower-case a release name and flatten its separators to spaces."""
    if not name:
        return ""
    text = _JUNK.sub(" ", str(name))
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _first_match(text, table, default=""):
    for label, pattern in table:
        if re.search(pattern, text, re.I):
            return label
    return default


def parse(name, size=0):
    """Break a release name into the fields ranking and matching need."""
    raw = str(name or "")
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
        return 0, 0, int(absolute.group(1))
    season_only = _SEASON_ONLY.search(text)
    if season_only:
        return int(season_only.group(1)), 0, 0      # a season pack
    return 0, 0, 0


def matches_episode(parsed, season, episode):
    """Does a parsed release name refer to this episode?

    A season pack has no episode number and is treated as a match, because the
    debrid layer can still pick the right file out of it.
    """
    if parsed["season"] and parsed["episode"]:
        return parsed["season"] == int(season) and parsed["episode"] == int(episode)
    if parsed["season"] and not parsed["episode"]:
        return parsed["season"] == int(season)        # season pack
    if parsed["absolute"]:
        return parsed["absolute"] == int(episode)
    return False


def size_label(size_bytes):
    if not size_bytes:
        return ""
    gigabytes = float(size_bytes) / (1024 ** 3)
    if gigabytes >= 1:
        return "%.2f GB" % gigabytes
    return "%.0f MB" % (float(size_bytes) / (1024 ** 2))

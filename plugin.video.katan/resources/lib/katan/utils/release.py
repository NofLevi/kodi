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
        "resolution": _first_match(text, RESOLUTIONS, "sd"),
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


def release_group(name):
    """The scene or p2p group, which is the strongest subtitle-match signal."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    bracket = _GROUP_BRACKET.match(raw)
    if bracket:
        return bracket.group(1).lower()
    stem = re.sub(r"\.(mkv|mp4|avi|m4v|ts)$", "", raw, flags=re.I).strip()
    tail = _GROUP_TAIL.search(stem)
    if tail:
        candidate = tail.group(1).lower()
        # "the-office" style titles end in a word, not a group
        if candidate not in ("1080p", "720p", "2160p", "x264", "x265"):
            return candidate
    return ""


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

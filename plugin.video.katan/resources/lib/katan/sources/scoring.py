"""Filtering and ranking, so the picker shows a few good options, not fifty.

The order of operations matters. Filtering happens first and is absolute: a
source that cannot play on this device is removed, not merely demoted. What
survives is then scored, and only the top handful is ever shown.

Weights are chosen so that one consideration dominates: a cached source beats
any uncached one, because on a weak device waiting for a download to start is
the difference between watching something and giving up.
"""
import math
import re

from .. import cache, settings
from ..utils import release

# Sane byte ranges per resolution, used to spot mislabelled and bloated files.
SIZE_RANGE_PER_HOUR = {
    "sd":    (150 * 1024 ** 2, 1200 * 1024 ** 2),
    "480p":  (200 * 1024 ** 2, 1800 * 1024 ** 2),
    "720p":  (500 * 1024 ** 2, 4 * 1024 ** 3),
    "1080p": (800 * 1024 ** 2, 12 * 1024 ** 3),
    "2160p": (2 * 1024 ** 3, 60 * 1024 ** 3),
}

WEIGHT_CACHED = 1000.0
WEIGHT_RESOLUTION = 120.0
WEIGHT_SOURCE_TYPE = 40.0
WEIGHT_SEEDERS = 30.0
WEIGHT_HEBREW = 90.0
WEIGHT_REMEMBERED_GROUP = 140.0
WEIGHT_SIZE_FIT = 60.0
WEIGHT_PROPER = 15.0
# Not a small nudge. A release that says it is Italian, for somebody who reads
# Hebrew and English, is not a slightly worse option - it is one they cannot
# watch, and autoplay picked exactly that: an Italian dub of a Korean series,
# because "ITA.KOR" parsed to no languages at all and nothing could rank it
# down. Large enough to lose to any ordinary release, small enough that it
# still beats nothing when a foreign release is all there is.
WEIGHT_WRONG_LANGUAGE = -400.0

# Spanish, Turkish and Italian dramas are watched in this house, in their own
# language with Hebrew subtitles. For those, the release that names the show's
# own language is the one somebody actually wants - an English redub of a
# Turkish serial is a worse answer even though English is on the readable
# list. Small on purpose: it settles a tie and loses to resolution, to being
# cached, and to a remembered group.
#
# It does nothing at all for an English-language show, because it only applies
# when the original language is *not* one the viewer reads - which is the whole
# definition of the case it is for.
WEIGHT_ORIGINAL_LANGUAGE = 60.0

# A SeaDex recommendation outranks every quality signal except being cached,
# and deliberately so. For anime the release group is the quality: two 1080p
# encodes of the same episode can differ by a botched encode or the wrong audio
# track, and nothing in the release name distinguishes them. It sits below
# WEIGHT_CACHED because a perfect release that has to be downloaded first is
# still the wrong answer on a weak device.
WEIGHT_SEADEX = 200.0

SOURCE_TYPE_RANK = {
    "bluray": 1.0,
    "web": 0.95,
    "hdtv": 0.55,
    "dvd": 0.4,
    "unknown": 0.5,
    "cam": 0.0,
}


class Preferences(object):
    """A snapshot of the user settings, read once per search."""

    def __init__(self):
        self.max_resolution = settings.get("sources.max_resolution")
        self.min_resolution = settings.get("sources.min_resolution")
        self.max_size = settings.get_int("sources.max_size_gb") * 1024 ** 3
        self.allow_hevc = settings.get_bool("sources.allow_hevc")
        self.allow_av1 = settings.get_bool("sources.allow_av1")
        self.allow_hdr = settings.get_bool("sources.allow_hdr")
        self.allow_dv = settings.get_bool("sources.allow_dv")
        self.allow_cam = settings.get_bool("sources.allow_cam", False)
        self.cached_only = settings.get_bool("sources.cached_only")
        self.prefer_hebrew = settings.get_bool("sources.prefer_hebrew")
        # What the viewer reads, which is the same list the subtitle search
        # uses. A release naming none of these and no neutral tag is one they
        # would have to watch in a language they did not ask for.
        self.languages = tuple(settings.get_list("subs.languages") or ("en",))
        # Filled in by `rank` from the title in hand, because it is a property
        # of what is being watched rather than of the settings.
        self.original_language = ""
        # Built once. It was being rebuilt per source - three set
        # constructions and two unions for every one of 240 releases on a
        # search - to answer a question whose inputs never change within one
        # ranking.
        self.readable = (set(self.languages)
                         | set(release.NEUTRAL_LANGUAGES) | {"he"})
        self.size_preference = settings.get("sources.size_preference", "balanced")
        self.results = settings.get_int("sources.results")
        self.max_rank = settings.resolution_rank(self.max_resolution)
        self.min_rank = settings.resolution_rank(self.min_resolution)


def rejection_reason(source, prefs, runtime_hours=2.0):
    """Why this source cannot be used, or an empty string when it is fine.

    Returning the reason rather than a boolean makes the "no sources found"
    case explainable instead of mysterious.
    """
    # Order matters only for which reason gets reported, so the most
    # explanatory check goes first. A cam rip is a cam rip, not merely
    # something that happens to be low resolution.
    parsed_source = source.get("extra", {}).get("source_type") or _source_type(source)
    if parsed_source == "cam" and not prefs.allow_cam:
        return "cam release"

    codec = source.get("codec")
    if codec == "h265" and not prefs.allow_hevc:
        return "HEVC is switched off"
    if codec == "av1" and not prefs.allow_av1:
        return "AV1 is switched off"

    if source.get("hdr") and not prefs.allow_hdr:
        return "HDR is switched off"

    # Dolby Vision is its own question even once HDR is allowed. A release
    # carrying an HDR10 (or HDR10+) layer beside it plays that layer on a
    # screen without Dolby Vision; one carrying Dolby Vision alone has nothing
    # to fall back to, and on a display that cannot decode it the picture
    # comes out purple and green. The parser tags the fallback as "hdr" or
    # "hdr10plus", so a lone "dv" is the case with nothing underneath.
    flags = source.get("hdr") or []
    if ("dv" in flags and "hdr" not in flags and "hdr10plus" not in flags
            and not prefs.allow_dv):
        return "Dolby Vision without an HDR10 fallback"

    rank = settings.resolution_rank(source.get("quality"))
    # An unknown resolution is judged by neither limit. Treating it as the
    # bottom of the ladder meant every release whose name does not mention
    # its resolution was refused for being too low, which is a decision made
    # on no evidence at all - and it hit anime hardest, where the convention
    # is not to put it in the name.
    if rank >= 0:
        if prefs.max_rank >= 0 and rank > prefs.max_rank:
            return "above the resolution limit"
        if prefs.min_rank >= 0 and rank < prefs.min_rank:
            return "below the resolution limit"

    size = source.get("size") or 0
    if prefs.max_size and size > prefs.max_size:
        return "larger than the size limit"

    if size:
        low, high = SIZE_RANGE_PER_HOUR.get(source.get("quality"), (0, 0))
        if low and size < low * runtime_hours * 0.35:
            return "far too small for its claimed quality"
        if high and size > high * runtime_hours * 2.5:
            return "implausibly large"

    if prefs.cached_only and not source.get("cached"):
        return "not cached"

    return ""


# What a leading "[Group]" looks like, so the title after it can be read.
_LEADING_GROUP = re.compile(r"^\s*\[[^\]]*\]\s*")
# A year that opens the rest of the name, and what follows it.
_YEAR_FIRST = re.compile(r"(19\d{2}|20\d{2})\b ?(.*)")
# "2024 03 01" is a daily show's air date, not a year of production.
_MONTH_DAY = re.compile(r"(?:0[1-9]|1[0-2]) (?:0[1-9]|[12]\d|3[01])\b")


def _another_production(source, meta):
    """Is this release of a different production that shares the title?

    Asked for episode 1 of the 2001 Hikaru no Go anime, Torrentio answered
    with "Hikaru no Go (2020) - 01" - the Chinese live-action drama - under
    the anime's own IMDb address. A year straight after the title is the
    release saying which production it is, and one more than a year from the
    show's own says it is another.

    Only a year *directly* after the title counts: "Fargo.S05E01.2023" is the
    year that episode aired. Only shows and episodes: a film's TMDB year and
    its release year are two apart often enough that the same rule would
    throw real releases away.
    """
    meta = meta or {}
    year = int(meta.get("year") or 0)
    if not year or meta.get("type") not in ("episode", "show"):
        return ""
    name = release.normalise(
        _LEADING_GROUP.sub("", release.strip_site_tags(source.get("title") or "")))
    titles = {release.normalise(meta.get(key) or "")
              for key in ("search_title", "original_title", "title",
                          "show_title")}
    for title in sorted((t for t in titles if t), key=len, reverse=True):
        if not name.startswith(title + " "):
            continue
        found = _YEAR_FIRST.match(name[len(title) + 1:])
        if not found or _MONTH_DAY.match(found.group(2)):
            return ""
        if abs(int(found.group(1)) - year) > 1:
            return "another production of the same name"
        return ""
    return ""


def _source_type(source):
    return release.parse(source.get("title", ""))["source"]


def score(source, prefs, runtime_hours=2.0, remembered=None, preferred=None):
    """Rank a single source. Higher is better."""
    total = 0.0

    if source.get("cached"):
        total += WEIGHT_CACHED

    # preferred is the SeaDex set, matched by infohash rather than by name, so
    # there is no way to promote a release that merely looks similar.
    if preferred and source.get("hash") and source["hash"] in preferred:
        total += WEIGHT_SEADEX
        source["seadex"] = True

    rank = max(0, settings.resolution_rank(source.get("quality")))
    ceiling = prefs.max_rank if prefs.max_rank >= 0 else len(settings.RESOLUTIONS) - 1
    # Closer to the ceiling is better, so 1080p wins when 1080p is the limit.
    total += WEIGHT_RESOLUTION * (1.0 - min(1.0, abs(ceiling - rank) / 4.0))

    total += WEIGHT_SOURCE_TYPE * SOURCE_TYPE_RANK.get(_source_type(source), 0.5)

    seeders = source.get("seeders") or 0
    if seeders > 0:
        # Diminishing returns: 200 seeders is not twice as good as 100.
        total += WEIGHT_SEEDERS * min(1.0, math.log10(seeders + 1) / 3.0)

    if prefs.prefer_hebrew and "he" in (source.get("languages") or []):
        total += WEIGHT_HEBREW

    if _wrong_language(source, prefs, prefs.original_language):
        total += WEIGHT_WRONG_LANGUAGE
    elif _is_the_original(source, prefs):
        total += WEIGHT_ORIGINAL_LANGUAGE

    total += WEIGHT_SIZE_FIT * _size_fit(source, prefs, runtime_hours)

    if remembered and source.get("group") and source["group"] == remembered.get("group"):
        total += WEIGHT_REMEMBERED_GROUP

    if release.parse(source.get("title", ""))["proper"]:
        total += WEIGHT_PROPER

    return total


def _wrong_language(source, prefs, original=""):
    """True when a release advertises a language and none of them is readable.

    Deliberately narrow, because most releases name no language at all and
    those must not be touched: an empty list means "nothing was claimed", not
    "English". So this fires only when the release *says* what it is and the
    answer is no use - "ITA.KOR" for somebody reading Hebrew and English.

    "multi" and "en" are neutral: a multi-audio release usually carries the
    original track too, and an English tag is readable by anyone who got this
    far. Hebrew is always acceptable whatever the language list says, since it
    is the reason this add-on exists.

    **And a show's own language is never the wrong language.** Spanish,
    Turkish and Italian dramas are watched here, in Spanish, Turkish and
    Italian, with Hebrew subtitles - and without this the rule read every
    honest release of them as unwatchable and ranked an English dub above the
    original. Nothing was ever removed, because the language rule only sorts
    and `rejection_reason` is what filters; but "TURKISH" sinking below an
    English redub is the wrong answer for the person actually watching.
    `original` is TMDB's `original_language` for the title in hand.
    """
    languages = source.get("languages") or []
    if not languages:
        return False
    for code in languages:
        if code in prefs.readable or (original and code == original):
            return False
    return True


def _is_the_original(source, prefs):
    """A foreign-language show, in its own language, as the viewer wants it.

    Only when the show's language is one the viewer does *not* read: for an
    English series every release is "the original" and saying so would be a
    bonus applied to everything, which is the same as no bonus and only
    slower.
    """
    original = prefs.original_language
    if not original or original in prefs.languages:
        return False
    return original in (source.get("languages") or [])


def _size_fit(source, prefs, runtime_hours):
    """How well the file size suits this device, in 0..1.

    A weak box streaming over wifi does better with a moderate bitrate than
    with a 40 GB remux, so the default preference is the middle of the sane
    range rather than the largest file available.
    """
    size = source.get("size") or 0
    if not size:
        return 0.4                      # unknown, neither rewarded nor punished
    low, high = SIZE_RANGE_PER_HOUR.get(source.get("quality"), (0, 0))
    if not high:
        return 0.4
    low *= runtime_hours
    high *= runtime_hours
    if prefs.size_preference == "biggest":
        return min(1.0, size / float(high))
    if prefs.size_preference == "smallest":
        return max(0.0, 1.0 - (size / float(high)))
    # balanced: peak in the middle of the sane band, falling off either side
    if size <= low:
        return 0.3
    ideal = (low + high) / 2.0
    spread = max(1.0, (high - low) / 2.0)
    return max(0.0, 1.0 - abs(size - ideal) / spread)


def remembered_group(meta):
    """The release group that played well for this show last time."""
    if not settings.get_bool("sources.source_memory"):
        return None
    show_id = (meta.get("ids") or {}).get("tmdb")
    if not show_id:
        return None
    return cache.get(cache.make_key("srcmem", show_id))


def preferred_hashes(meta):
    """The SeaDex opinion for this title, or an empty set.

    Only anime has one, so this costs a request for anime and nothing at all
    for everything else. The import is deferred so a film never loads it.
    """
    anilist_id = (meta.get("ids") or {}).get("anilist")
    if not anilist_id:
        return set()
    try:
        from ..meta import seadex
    except ImportError:
        return set()
    try:
        return seadex.best_hashes(anilist_id)
    except Exception:
        from .. import kodi
        kodi.log_exception("seadex lookup failed")
        return set()


def rank(sources, meta=None, runtime_hours=2.0, limit=None):
    """Filter, score and sort. Returns (kept, rejection counts)."""
    prefs = Preferences()
    meta = meta or {}
    # A Turkish drama is in Turkish, and that is not a foreign-language
    # release - it is the only honest one there is.
    item = meta.get("item") or meta
    prefs.original_language = (item.get("original_language") or "").strip()
    if prefs.original_language:
        prefs.readable = prefs.readable | {prefs.original_language}
    remembered = remembered_group(meta)
    preferred = preferred_hashes(meta)

    kept = []
    rejected = {}
    for source in sources:
        reason = (rejection_reason(source, prefs, runtime_hours)
                  or _another_production(source, meta))
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        source["score"] = score(source, prefs, runtime_hours, remembered,
                                preferred)
        kept.append(source)

    kept.sort(key=lambda s: sort_key(s, prefs))
    if limit is None:
        limit = prefs.results
    return kept[:limit] if limit else kept, rejected


def sort_key(source, prefs=None):
    """The order the viewer asked for, in as many words.

        the best picture I can get, with subtitles that fit,
        in the smallest file that delivers both

    So: resolution first, then how well its Hebrew subtitles match, then
    size - and only then the weighted score, which decides between two
    releases equal on all three. The filters have already removed anything
    above the configured maximum, so "highest resolution" means highest
    *allowed*, which is why the ceiling is a setting.

    Cached comes before all of it and is not negotiable. On a device that
    cannot afford to wait for a download, an uncached source is not a
    slightly worse option but a different thing entirely - and with "cached
    only" on, which is the default, every source here is cached and this
    term changes nothing.

    The size term follows `sources.size_preference` rather than always
    preferring the smallest, and that is not hedging. "Smallest" is the lean
    profile's setting and gives exactly what was asked for. "Balanced" exists
    because a 1080p film in 900 MB is usually a bad encode rather than a
    clever one, and somebody who chose that setting has said they would
    rather have the moderate bitrate than the smallest file - so there the
    existing size-fit score decides, and "smallest" would quietly override a
    choice they made on purpose.
    """
    from ..subs import outlook

    size = source.get("size") or 0
    preference = getattr(prefs, "size_preference", "smallest")
    if preference in ("largest", "biggest"):
        size_term = -size
    elif preference == "balanced":
        size_term = 0            # leave it to the weighted score below
    else:
        size_term = size

    return (
        0 if source.get("cached") else 1,
        -settings.resolution_rank(source.get("quality")),
        -outlook.ranking_score(source),
        size_term,
        -(source.get("score") or 0.0),
    )


def rank_all(sources, meta=None, runtime_hours=2.0):
    """Everything that passed the filters, for the "show all" view."""
    return rank(sources, meta, runtime_hours, limit=0)

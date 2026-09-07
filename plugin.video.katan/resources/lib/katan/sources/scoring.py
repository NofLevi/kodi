"""Filtering and ranking, so the picker shows a few good options, not fifty.

The order of operations matters. Filtering happens first and is absolute: a
source that cannot play on this device is removed, not merely demoted. What
survives is then scored, and only the top handful is ever shown.

Weights are chosen so that one consideration dominates: a cached source beats
any uncached one, because on a weak device waiting for a download to start is
the difference between watching something and giving up.
"""
import math

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
        self.allow_cam = settings.get_bool("sources.allow_cam", False)
        self.cached_only = settings.get_bool("sources.cached_only")
        self.prefer_hebrew = settings.get_bool("sources.prefer_hebrew")
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

    rank = settings.resolution_rank(source.get("quality"))
    if rank < 0:
        rank = 0
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

    total += WEIGHT_SIZE_FIT * _size_fit(source, prefs, runtime_hours)

    if remembered and source.get("group") and source["group"] == remembered.get("group"):
        total += WEIGHT_REMEMBERED_GROUP

    if release.parse(source.get("title", ""))["proper"]:
        total += WEIGHT_PROPER

    return total


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
    remembered = remembered_group(meta)
    preferred = preferred_hashes(meta)

    kept = []
    rejected = {}
    for source in sources:
        reason = rejection_reason(source, prefs, runtime_hours)
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
    if preference == "largest":
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

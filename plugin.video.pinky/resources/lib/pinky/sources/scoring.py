"""Filtering and ranking, so the picker shows a few good options, not fifty.

The order of operations matters. Filtering happens first and is absolute: a
source that cannot play on this device is removed, not merely demoted. What
survives is then scored, and only the top handful is ever shown.

Weights are chosen so that one consideration dominates: a cached source beats
any uncached one, because on a weak device waiting for a download to start is
the difference between watching something and giving up.
"""
import math
import time
import re

from .. import cache, kodi, settings
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
    # A flat ceiling and a per-hour expectation both measure a release
    # against one episode's runtime - and neither should, once the release
    # names a batch. Measured on Naruto 2x54: the only other genuine
    # (non-Boruto, non-Shippuuden) release Torrentio had was a 13.2 GB batch
    # of episodes 53-106. Against an 8 GB ceiling built for one file it was
    # refused outright; against the per-hour range built for one ~24 minute
    # episode it was also "implausibly large" by the same mistake one check
    # further down. 13.2 GB over 54 episodes is 244 MB an episode - entirely
    # ordinary - and both checks were punishing it purely for being a batch.
    #
    # `episode_range` is the only batch marker with an actual count attached;
    # a bare season pack ("Naruto Season 2 COMPLETE") states no number of
    # episodes and is measured exactly as before, because there is nothing
    # here to divide by.
    # `runtime_hours` is already the single target *episode's* duration, not
    # a batch's total - "so an episode is not judged like a film" is its own
    # docstring - so only `size` needs dividing down to what one episode of
    # the batch actually weighs; the runtime side of the comparison is
    # unchanged.
    span = release.parse(source.get("title", "")).get("episode_range")
    episodes = (span[1] - span[0] + 1) if span and span[1] > span[0] else 1
    per_episode_size = size / float(episodes) if episodes > 1 else size

    if prefs.max_size and per_episode_size > prefs.max_size:
        return "larger than the size limit"

    if size:
        low, high = SIZE_RANGE_PER_HOUR.get(source.get("quality"), (0, 0))
        if low and per_episode_size < low * runtime_hours * 0.35:
            return "far too small for its claimed quality"
        if high and per_episode_size > high * runtime_hours * 2.5:
            return "implausibly large"

    if not source.get("cached") and int(source.get("seeders") or 0) <= 0:
        # An uncached source with nobody seeding it cannot become anything
        # else. It is not a quality trade-off like a low resolution or an
        # oversized batch - a torrent with zero seeders has nothing for a
        # debrid service to fetch from, ever, so offering it as "Uncached"
        # promises a download that will sit at 0% forever. Measured on
        # Naruto 2x54: the one surviving batch that named the right episode
        # had zero seeders, and would have been offered next to a genuinely
        # working cached copy as though the two were the same kind of thing.
        #
        # A cached source is exempt regardless of seeders - it is already
        # sitting on the debrid service's own storage and does not need the
        # swarm at all.
        return "no seeders"

    if prefs.cached_only and not source.get("cached"):
        return "not cached"

    return ""


# What a leading "[Group]" looks like, so the title after it can be read.
_LEADING_GROUP = re.compile(r"^\s*\[[^\]]*\]\s*")
# A year that opens the rest of the name, and what follows it.
_YEAR_FIRST = re.compile(r"(19\d{2}|20\d{2})\b ?(.*)")
# "2024 03 01" is a daily show's air date, not a year of production.
_MONTH_DAY = re.compile(r"(?:0[1-9]|1[0-2]) (?:0[1-9]|[12]\d|3[01])\b")


# Every four-digit year in a name, so a film can be checked too.
_ANY_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _a_different_film(source, meta):
    """Is this film release something else entirely?

    Asked for Spider-Man: Brand New Day (2026), Torrentio answered with
    "Marvel Studios Iron Man 2008 1080p MA WEB-DL DDP5 1 H 264-SARVO.mkv" -
    a different film, under the right film's address. `_another_production`
    could not catch it: that one reads the year *directly after the title*,
    and this name does not begin with the title at all.

    Two weak signals together rather than one strong one, because either
    alone throws away real releases:

    * a year that is nowhere near the film's. Alone this rejects **1917**,
      whose title is a year and whose uploads often carry no other;
    * the film's own words being absent. Alone this rejects every release
      under a translated title - "O Ultimo Tiro Certo" is One Last Shot,
      measured in the picker on the same evening, and shares no word with it.

    A release has to fail both to be dropped. A name with no year at all is
    kept, because there is then nothing to disagree with.
    """
    meta = meta or {}
    year = int(meta.get("year") or 0)
    if not year or meta.get("type") not in ("movie", ""):
        return ""
    name = release.normalise(
        _LEADING_GROUP.sub("", release.strip_site_tags(source.get("title") or "")))
    # A title may itself be a year - Blade Runner 2049 - so anything past next
    # year is a name rather than a date.
    years = [int(found) for found in _ANY_YEAR.findall(name)
             if int(found) <= year + 2]
    if not years or any(abs(found - year) <= 2 for found in years):
        return ""
    if _says_the_title(name, meta):
        return ""
    return "another production of the same name"


def _says_the_title(name, meta):
    """Do any of the film's names appear in this release name?"""
    return any(release.mentions(name, meta.get(key))
               for key in ("search_title", "title", "original_title")
               if meta.get(key))


# Words that describe where an episode sits in its own show, or how it was
# encoded, rather than naming a different show. "Attack on Titan Final
# Season" is Attack on Titan; "Naruto Shippuuden" is not Naruto. The
# technical half was missing entirely: "[HDTV 1080p][Cap.101]" put "hdtv" and
# "1080p" in front of the episode number the split regex actually found, and
# with no vocabulary for what a resolution or a codec looks like, both read
# as a second show's name. These are the same words `release.py`'s own
# tables already recognise; duplicating a handful of them here is cheaper
# than importing that machinery into a function that never parses a size or
# a codec, only asks "have I seen this word before".
_TECHNICAL = frozenset("""
480p 576p 720p 1080p 2160p 4k uhd fullhd fhd sd hdtv web webdl webrip
bluray brrip bdrip dvdrip hdrip
x264 x265 h264 h265 hevc avc av1 xvid divx hi10 hi10p 8bit 10bit
aac ac3 eac3 dts flac opus mp3 truehd atmos
bd hd
""".split())

_STRUCTURAL = _TECHNICAL | frozenset("""
season seasons final part parts cour tv series episode episodes cap ova ovas
oad oads special specials movie film complete batch box set volume vol arc
saga uncut uncensored dub dubbed sub subbed multi multisub remastered
hybrid proper repack
jakso episodio capitulo odcinek folge chapter chapters
""".split())

# Ordinary television is uploaded in every language it airs in, and the
# season word comes with it: "Stranger Things Stagione 1", "Separacion -
# Temporada 1". None of these name a show.
_WESTERN_STRUCTURAL = _TECHNICAL | frozenset("""
season seasons series episode episodes part parts complete final special
specials extra extras minisode minisodes proper repack remastered hybrid
uncut multi dub dubbed sub subbed subs
stagione temporada temporadas saison staffel sezon sezona seizoen
capitolo capitulo cap episodio folge
""".split())

# A colon, an apostrophe or a bare hyphen glues onto the letter beside it
# rather than separating two words - "Re:ZERO" is one token, "Boruto:" keeps
# its colon - so a release spelling the same title with a space ("Re Zero")
# or without the punctuation at all ("Boruto") could never match it. Unlike
# `release._JUNK`, dropped rather than reused globally: several of
# `release.py`'s own resolution and codec patterns are tuned against exactly
# what `normalise()` currently leaves in place, and this function is the only
# caller that needs punctuation gone rather than kept.
_STRAY_PUNCT = re.compile(r"[!?'\":,;-]+")


def _words(text):
    return [w for w in _STRAY_PUNCT.sub(" ", release.normalise(text)).split()
            if len(w) >= 3]


def _a_different_series(source, meta):
    """Does this release name a different show that shares a name with ours?

    Anime and ordinary television fail this in different shapes, so each has
    its own engine rather than one heuristic tuned to satisfy both. It used
    to be one, and every fix for one broke the other: anime needed romaji
    aliases and absolute numbers, ordinary television needed every language
    the show is sold in - and on a Hebrew interface the title is Hebrew, so
    before that Squid Game lost 98 of its 99 releases here. Films never come
    through this at all; `_a_different_film` is theirs.
    """
    meta = meta or {}
    if meta.get("type") != "episode":
        return ""
    if (meta.get("extra") or {}).get("anime"):
        return _a_different_anime_series(source, meta)
    return _a_different_western_series(source, meta)


# Where the show's name ends in an ordinary release: "S01E01", "S01.E01",
# "1x01", "S01x01", "Ep. 07", a bare "S05" ahead of "01", or an absolute
# number. The bare season marker and the "1x01" form are what a Spanish,
# Italian or Czech upload uses, and without them the episode's own title -
# "Capitolo Uno La Scomparsa Di Will Byers" - was read as a second show.
_WESTERN_MARKER = re.compile(
    r"\b(?:s\d{1,2}\s*(?:ep|e|x)\s*\d{1,4}|s?\d{1,2}x\d{1,4}|s\d{1,2}"
    r"|(?:episode|ep)\s*\d{1,4}|e\d{1,4}|\d{1,4})\b")


def _a_different_western_series(source, meta):
    """The check for everything that is not anime.

    The show is known by every name it has been sold under, because a
    release is named in the language of whoever uploaded it: "Juego de
    Tronos", "Il Trono di Spade" and "Hra o trůny" are all Game of Thrones.
    `translated_titles` is TMDB's list of those, and `english_title` covers a
    Hebrew interface, where `title` is Hebrew and `original_title` for a
    Korean drama is Korean - neither of which any release carries.
    """
    known = set()
    for key in ("title", "show_title", "original_title", "english_title"):
        known.update(_words(meta.get(key) or ""))
    for name in meta.get("translated_titles") or []:
        known.update(_words(name or ""))
    if not known:
        return ""
    name = release.normalise(
        _LEADING_GROUP.sub("", release.strip_site_tags(source.get("title") or "")))
    head = _WESTERN_MARKER.split(name, 1)[0]
    extra = [word for word in _words(head)
             if word not in known and word not in _WESTERN_STRUCTURAL
             and not word.isdigit()]
    if extra:
        return "another series of the same name"
    return ""


_ANIME_NUMBER = r"\d{1,4}(?:v\d{1,2}|[a-z])?"
_ANIME_MARKER = re.compile(
    r"\b(?:s\d{1,2}e%s|(?:episode|ep)\s*%s|e%s|%s)\b" % ((_ANIME_NUMBER,) * 4))


# Where an episode sits, spelled as one token: "2nd" Season, "S02", "TV2",
# "Season2", "Part2". Re:Zero's "2nd Season Part 2 - 02" and "S02 - E15" are
# season two of Re:Zero, and were rejected as another show for those words.
_ANIME_PLACE = re.compile(
    r"^(?:\d{1,2}(?:st|nd|rd|th)|s\d{1,2}|tv\d{1,2}|season\d{1,2}|part\d{1,2}"
    r"|cour\d{1,2})$")


def _glued(text):
    """A title's words with its punctuation removed rather than spaced.

    "Re:Zero" is uploaded as "ReZero" as often as "Re Zero", and the spaced
    fold alone never produced the one-word form.
    """
    return [word for word in re.sub(r"[!?'\":,;.\-]+", "",
                                     release.normalise(text)).split()
            if len(word) >= 3]


def _a_different_anime_series(source, meta):
    """The anime check.

    The trap is absolute numbering. Naruto 2x54 **is** absolute 106, and
    `Naruto Shippuuden 106` is episode 106 of a different series - so
    `matches_episode` says yes and is arithmetically right. The two
    name-keyed providers search for "Naruto" and answer with Shippuuden,
    Boruto and Next Generations; cached-only was hiding them, and listing
    uncached sources put them on the screen.

    This is the worst kind of wrong, because the file is exactly what it says
    it is and nothing downstream can catch it: it plays, it is the right
    length, and it is the wrong episode of the wrong show.

    So the series part of the name - everything before the number - may not
    carry a significant word that none of our own titles do. Words that place
    an episode *within* a show are not significant: "Final Season", "Part 2",
    "OVA" and "Box Set" all describe where we are, not what we are watching.
    """
    meta = meta or {}
    if meta.get("type") != "episode":
        return ""
    known = set()
    # The season's own name too: an arc is released under it. "Monogatari
    # Series Off & Monster Season" is season 5, and all twelve cached copies
    # of 5x11 were rejected for the words "off" and "monster".
    for key in ("title", "show_title", "original_title", "search_title",
                "english_title", "season_name"):
        known.update(_words(meta.get(key) or ""))
        known.update(_glued(meta.get(key) or ""))
    for alias in meta.get("aliases") or []:
        known.update(_words(alias or ""))
        known.update(_glued(alias or ""))
    # A scene release spells "&" out: "Monogatari Series OFF & MONSTER
    # Season" is uploaded as "MONOGATARI.Series.OFF.and.MONSTER.Season".
    if any("&" in (meta.get(key) or "") for key in
           ("title", "search_title", "season_name")) or any(
               "&" in (alias or "") for alias in meta.get("aliases") or []):
        known.add("and")
    if not known:
        return ""
    name = release.normalise(
        _LEADING_GROUP.sub("", release.strip_site_tags(source.get("title") or "")))
    # A long-running show numbers past 999 - One Piece is in four digits -
    # and a release pads to match: "S01E0001" has four digits after the E,
    # one more than this used to allow, so the marker never matched at all
    # and the episode's own subtitle ("I'm Luffy! The Man Who's...") was
    # read in its entirety as the name of a second show. "EP01" and "E001"
    # are the same marker with no season number in front, common on a
    # single-season show or an absolutely-numbered one, and neither used to
    # be recognised at all - "Death Note - EP01 - Rebirth" left "rebirth"
    # looking like the name of a second show, which is the episode's own
    # title with nowhere else for it to have come from. The optional trailing
    # letter is SubsPlease's own convention for an episode split in two -
    # "01A", "01B" - which glues on with no space and so never had a
    # boundary to split at either.
    # And a version suffix, "02v3" and "01v2", which is how a fansub group
    # marks a corrected re-release and glues on the same way.
    head = _ANIME_MARKER.split(name, 1)[0]
    extra = [word for word in _words(head)
             if word not in known and word not in _STRUCTURAL
             and not _ANIME_PLACE.match(word)
             and not word.isdigit()]
    if extra:
        return "another series of the same name"
    if _names_only_the_parent(head, meta):
        return "the show this one continues"
    return ""


def _names_only_the_parent(head, meta):
    """A release named by the first part of our name only, where the rest is
    part of the name - the show this one continues.

    Naruto Shippuden 3x55 offered "Naruto 055 [Nezumi]", from a pack called
    "Naruto 053-078": episode 55 of the original series, which says nothing
    the extra-word check could object to, because it says *less*. The same is
    "Dragon Ball - 17" for Dragon Ball Super. What is missing has to be part
    of the name, not a subtitle after a colon or a dash - "Frieren" for
    "Frieren: Beyond Journey's End" and "Re:Zero" for the long English title
    are the same show - and a release matching any name we go by in full is
    ours whatever else is true.
    """
    said = [word for word in _words(head) if word not in _STRUCTURAL
            and not _ANIME_PLACE.match(word) and not word.isdigit()]
    if not said:
        return False
    names = [meta.get(key) or "" for key in
             ("title", "show_title", "search_title", "english_title")]
    everything = names + list(meta.get("aliases") or [])
    for name in everything:
        if [word for word in _words(name) if not word.isdigit()] == said:
            return False
    for name in names:
        words = [word for word in _words(name) if not word.isdigit()]
        if len(words) <= len(said) or words[:len(said)] != said:
            continue
        text = release.normalise(name)
        cut = 0
        for word in said:
            cut = text.find(word, cut) + len(word)
        rest = text[cut:].lstrip()
        if rest and rest[0] not in ":-~([":
            return True
    return False


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
    # Anime's other names, which only anime carries: "Digimon Adventure
    # (2020)" is the reboot, and it says so under the name the 1999 series is
    # released as rather than TMDB's "Digimon: Digital Monsters".
    titles.update(release.normalise(alias or "")
                  for alias in meta.get("aliases") or [])
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


# Enough sources for the picker to be a choice rather than a list of one.
# The page is ten native rows, ten AI and five English, all drawn from this
# pool, so ten releases is what filling it takes.
ENOUGH_TO_CHOOSE_FROM = 10


def rank(sources, meta=None, runtime_hours=2.0, limit=None):
    """Filter, score and sort. Returns (kept, rejection counts)."""
    prefs = Preferences()
    meta = meta or {}
    # A Turkish drama is in Turkish, and that is not a foreign-language
    # release - it is the only honest one there is.
    item = meta.get("item") or meta
    prefs.original_language = (meta.get("original_language")
                               or item.get("original_language") or "").strip()
    if prefs.original_language:
        prefs.readable = prefs.readable | {prefs.original_language}
    remembered = remembered_group(meta)
    preferred = preferred_hashes(meta)
    in_cinemas = not prefs.allow_cam and _only_in_cinemas(meta)

    def why(source):
        # One judgement for both passes below. The second used to repeat the
        # checks by hand, and a check added to one and not the other is how
        # a film still in cinemas got its cams back: they failed the
        # resolution floor first, the floor stood aside, and the second pass
        # had never heard of the cinema rule.
        return (rejection_reason(source, prefs, runtime_hours)
                or (in_cinemas and "cam release")
                or _another_production(source, meta)
                or _a_different_series(source, meta)
                or _a_different_film(source, meta))

    kept = []
    rejected = {}
    already = set()
    for source in sources:
        reason = why(source)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        source["score"] = score(source, prefs, runtime_hours, remembered,
                                preferred)
        already.add(id(source))
        kept.append(source)

    if len(kept) < ENOUGH_TO_CHOOSE_FROM and rejected.get("below the resolution limit"):
        # A floor that leaves nothing to choose between is not a floor, it is
        # a wall. Measured on Naruto 2x54: a 2002 anime is natively 480p, so
        # demanding 720p asks for an upscale that mostly does not exist, and
        # four of its releases were refused for being exactly what the show
        # is - leaving one row on the screen.
        #
        # The minimum is there to stop a 480p rip being offered *instead of*
        # something better. With a full page of better ones it does that and
        # stays; with less than a page it is answering a question nobody
        # asked, and the answer is an empty screen.
        kodi.log("only %d sources cleared the %s floor, so it stands aside"
                 % (len(kept), settings.get("sources.min_resolution")))
        prefs.min_rank = -1
        rejected.pop("below the resolution limit", None)
        for source in sources:
            if id(source) in already:
                continue
            if why(source):
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
        _unwatchable(source, prefs),
        -settings.resolution_rank(source.get("quality")),
        _dubbed(source, prefs),
        -outlook.ranking_score(source),
        size_term,
        -(source.get("score") or 0.0),
    )


def _unwatchable(source, prefs):
    """1 for a release only in a language nobody here reads, else 0.

    WEIGHT_WRONG_LANGUAGE says a release like that should lose to any ordinary
    one, and as a weight it could not: the weighted score is the last thing
    this sort looks at, after size, so an Italian dub only lost to a release
    of exactly the same size. A 4K Italian dub beat a 1080p English release
    and autoplay took it. As its own term, ahead of resolution, it loses to
    every watchable release and still beats nothing at all.
    """
    if prefs is None:
        return 0
    return 1 if _wrong_language(source, prefs, prefs.original_language) else 0


def _dubbed(source, prefs):
    """1 for a dub of a show whose own language the viewer does not read.

    An anime is released twice, Japanese with subtitles and an English dub,
    and the Hebrew or AI subtitle is made from the original dialogue - so it
    follows the Japanese audio and not the English rewrite. After resolution,
    so it never costs picture, and before subtitles and size, so it decides
    between two releases of the same episode. The dub is still in the list
    and still marked DUB, for anybody who wants it.

    Not in kids mode, where a child who cannot read subtitles is exactly who a
    dub is for.
    """
    if prefs is None or source.get("dub") != "dub":
        return 0
    original = prefs.original_language
    if not original or original in prefs.languages:
        return 0
    from .. import kids
    return 0 if kids.enabled() else 1


CINEMA_WINDOW_DAYS = 180


def _only_in_cinemas(meta):
    """Is this a film that nobody can have a proper copy of yet?

    Spider-Man: Brand New Day, two months into its cinema run with no digital
    release anywhere, kept fifteen "sources" once the cam filter had taken
    thirty-four: "D.WEBRip", "1TamilBlasters HQ HD", "little blur but good
    rip", "kinda like webrip next best thing no ads". Every one a camera,
    renamed. The filter reads what a name *says*, and these say nothing, so
    the question is asked of the film instead: opened in a cinema within
    CINEMA_WINDOW_DAYS, and no digital, disc or television release anywhere
    in TMDB's release dates. Films only - a series airs, it has no cinema
    run - and a film TMDB knows no cinema date for is left alone.
    """
    if (meta or {}).get("type") != "movie":
        return False
    extra = ((meta.get("item") or {}).get("extra") or {})
    cinema, home = extra.get("cinema") or "", extra.get("home") or ""
    today = time.strftime("%Y-%m-%d")
    if not cinema or (home and home <= today):
        return False
    started = time.mktime(time.strptime(cinema, "%Y-%m-%d"))
    return (time.time() - started) / 86400.0 <= CINEMA_WINDOW_DAYS


def rank_all(sources, meta=None, runtime_hours=2.0):
    """Everything that passed the filters, for the "show all" view."""
    return rank(sources, meta, runtime_hours, limit=0)

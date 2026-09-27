# -*- coding: utf-8 -*-
"""How likely each source is to end up with Hebrew subtitles that fit.

The picker used to show quality, size and seeders - everything about the
picture and nothing about the words, which for a Hebrew-speaking household
is the part that decides whether a release is watchable at all. A 4K remux
with no Hebrew subtitle is worse than a 1080p WEB-DL with one.

Two different things are reported, and they are not the same claim:

* **embedded** - the release name says it carries Hebrew. That is a promise
  made by whoever named the file, so it is shown as what it is, a claim from
  the release name, and it is never given a percentage.
* **a score** - the best Hebrew subtitle any provider has, matched against
  *that specific release*. Same release name as the video is a near-certain
  fit; same group is very likely; nothing in common is a guess. This is the
  same scoring the subtitle chooser shows, so the number in the picker and
  the number in the chooser mean the same thing.

One search covers every source. The candidates come back once for the title
and are then scored locally against each release name, which is arithmetic
over a few dozen strings - the cost is one subtitle search, not one per row.
"""
from .. import cache, kodi, settings
from ..utils import release
from . import matcher

# How long the answer is kept. Subtitle catalogues change slowly, and this is
# only ever used to decorate a list.
TTL = 3600

EMBEDDED = "embedded"
EXTERNAL = "external"
NONE = "none"
# No Hebrew subtitle fits this release, but one in a language AI translates
# from does - so the Hebrew is made rather than found.
AI = "ai"
# The title is in Hebrew to begin with, so there is nothing to subtitle.
NATIVE = "native"

# How far a translation ranks below its source's own match. It also never
# ranks level with a Hebrew subtitle that clears the threshold: "use the LLM
# when nothing fits", stated as arithmetic.
AI_PENALTY = 5

# Release-name markers that claim Hebrew is inside the file. Deliberately
# narrow: "MULTI" and "DUAL" describe audio far more often than subtitles and
# are not enough on their own.
_HEBREW_MARKERS = ("hebsub", "hebsubs", "heb.sub", "heb-sub", "hebdub",
                   "hebrew", "nohebsub")


def _claims_hebrew(name):
    """Does this release name say it carries Hebrew?

    `nohebsub` is in the marker list on purpose so that it can be excluded
    here rather than matching as "hebsub" and claiming the opposite of what
    it says.
    """
    lowered = (name or "").lower().replace("_", ".")
    if "nohebsub" in lowered or "no.heb" in lowered:
        return False
    return any(marker in lowered for marker in _HEBREW_MARKERS)


def cache_key(meta, languages=None):
    ids = meta.get("ids") or {}
    parts = ["suboutlook", ids.get("imdb") or ids.get("tmdb"),
             meta.get("season"), meta.get("episode")]
    if languages:
        parts.append(",".join(languages))
    return cache.make_key(*parts)


def candidates(meta, refresh=False, strict=False):
    """Every Hebrew subtitle any provider has for this title.

    ``strict`` lets the source-cache schema migration distinguish a genuine
    empty catalogue answer from a failed request. Ordinary UI callers keep the
    historical best-effort empty-list behavior.

    Deliberately independent of the source list: it needs only the title, so
    it can be asked at the same time as the source providers rather than
    after them. That is what makes this free - it finishes while Torrentio is
    still answering, and nothing waits for it.
    """
    key = cache_key(meta)
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    try:
        from . import auto
        wanted = _hebrew_code()
        found = [c for c in auto.search_candidates(meta, [wanted])
                 if c.get("language") == wanted]
    except Exception:
        kodi.log_exception("could not look up subtitles")
        if strict:
            raise
        # Not cached. A failure is not a result: caching it meant one bad
        # moment - a provider down, the wifi dropping - told this title it had
        # no Hebrew subtitles for the next hour, and reopening the picker
        # could not undo it. `meta/anilist.py` already learned this one.
        return []
    cache.set(key, found, TTL)
    return found


def annotate(meta, sources, found=None):
    """Write the subtitle outlook onto each source, in place.

    Ranking reads it, and so does the picker, which is the point of putting
    it on the source rather than in a table beside it: one lookup, one
    answer, and the order the viewer sees is built from the same numbers the
    badge shows them.
    """
    if not sources:
        return sources
    from . import auto
    if auto.normalise_language(meta.get("original_language")) == _hebrew_code():
        # Every release of an Israeli film speaks Hebrew. Asking which has the
        # better Hebrew subtitle is a search whose answer changes nothing.
        for source in sources:
            source["subs_kind"] = NATIVE
            source["subs_score"] = 0
        return sources
    if found is None:
        found = candidates(meta, strict=True)
    _write(meta, sources, found)
    # Only when no release has Hebrew that fits is it worth asking what AI
    # could translate from. While one does, a translation cannot outrank it,
    # and the requests would buy nothing.
    if not _hebrew_fits(sources):
        extra = translation_candidates(meta)
        if extra:
            found = list(found) + extra
            _write(meta, sources, found)
    kodi.log("subtitle outlook: %d candidates over %d sources"
             % (len(found), len(sources)))
    return sources


def translation_candidates(meta):
    """Subtitles in the languages an AI translation would be made from.

    Asked for only once no release has Hebrew that fits, and cached under its
    own key, so a title that needed them once does not ask again.
    """
    from . import auto
    languages = auto.translation_source_languages(meta, [_hebrew_code()])
    if not languages:
        return []
    key = cache_key(meta, languages)
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        found = [c for c in auto.search_candidates(meta, languages)
                 if c.get("language") in languages]
    except Exception:
        kodi.log_exception("could not look up subtitles to translate from")
        return []
    cache.set(key, found, TTL)
    return found


def _write(meta, sources, found):
    for source in sources:
        entry = _for_one(meta, source, found)
        source["subs_kind"] = entry["kind"]
        source["subs_score"] = entry["score"]


def _hebrew_fits(sources):
    threshold = _threshold()
    return any(source.get("subs_kind") == EMBEDDED
               or (source.get("subs_kind") == EXTERNAL
                   and (source.get("subs_score") or 0) >= threshold)
               for source in sources)


# How many rows of each kind the picker opens on.
SPLIT_ROWS = 10
ENGLISH_ROWS = 5


def split_rows(meta, sources, limit=SPLIT_ROWS):
    """The picker's first page: the best releases for Hebrew, and for AI.

    Every release is judged twice - by its best-fitting Hebrew subtitle, and by
    the best-fitting subtitle in a language AI translates from - and the top
    `limit` of each are returned as copies marked `subs_mode` "native" or
    "llm". The same release can be in both lists; that is the point, because
    what is being compared is the two ways of getting Hebrew onto the one
    file, and the viewer's choices are the measurement.

    The AI rows pick their source language the way playback will
    (`context.source_bonus`: Arabic, then gender-marking languages, then
    English), so the language the row names is the one that gets translated.
    The percentage is how well that subtitle fits this release: its timing
    is what the translation inherits. The quality of the Hebrew itself is
    not known until it exists, and the row does not pretend otherwise.

    A third list, of up to ENGLISH_ROWS, is the releases with the best
    *English* subtitle, marked "english" - for somebody who would rather read
    English than a translation, which for anime is a real choice. It does not
    depend on a translation engine.

    Returns ([], [], []) for a title already in Hebrew, and no AI rows without
    a translation engine. A title with no Hebrew subtitle anywhere - most anime,
    and the Turkish dramas measured - has an empty first list and full second
    and third ones, and that is the case the whole page exists for.
    """
    from . import auto
    from .ai import context

    hebrew = _hebrew_code()
    # A Hebrew title still gets its Hebrew rows. It used to get none at all,
    # on the reasoning that an Israeli film is already in Hebrew and a
    # subtitle puts the dialogue on screen twice - which is true of a film
    # that is wholly in Hebrew and false of the ones this household watches.
    # **Fauda is half in Arabic**; Shtisel has Yiddish in it. Measured over 36
    # titles, these were the only two where the picker drew less than what was
    # available: Fauda had five Hebrew subtitles and a ceiling of 99, Shtisel
    # two and a ceiling of 100, and both screens were empty.
    #
    # The other two lists are still skipped, because translating into Hebrew
    # for a Hebrew show is work with no answer at the end of it.
    hebrew_title = auto.normalise_language(meta.get("original_language")) == hebrew
    native_found = candidates(meta)
    llm_found = [] if hebrew_title else translation_candidates(meta)
    english_found = [] if hebrew_title else english_candidates(meta, llm_found)
    native, llm, english = [], [], []
    for index, source in enumerate(sources):
        name = source.get("title") or ""
        target = matcher.target_from(meta, {
            "release": name,
            "group": source.get("group") or release.parse(name)["group"],
            "quality": source.get("quality") or "",
        })
        if _claims_hebrew(name) or "he" in (source.get("languages") or []):
            native_fit = 100
        else:
            native_fit = max([matcher.rate(c, target)[0] for c in native_found]
                             or [0])
        if native_fit > 0:
            native.append((_order(source, native_fit, index),
                           dict(source, subs_mode="native",
                                subs_fit=_as_percent(native_fit))))
        if hebrew_title:
            continue
        fits = []
        for candidate in llm_found:
            fit = matcher.rate(candidate, target)[0]
            if fit > 0:
                fits.append((fit, candidate.get("language", "")))
        # The same close-call rule the translation source uses: a language
        # bonus may break a tie and may not outrank a clearly better fit,
        # because a translation inherits its source's timing exactly.
        best = None
        top = max([fit for fit, _lang in fits] or [0])
        for fit, language in fits:
            bonus = (context.source_bonus(language)
                     if context.eligible_for_bonus(fit, top) else 0)
            if best is None or fit + bonus > best[0]:
                best = (fit + bonus, fit, language)
        if best is not None:
            llm.append((_order(source, best[1], index),
                        dict(source, subs_mode="llm",
                             subs_fit=_as_percent(best[1]),
                             subs_from=best[2])))
        english_fit = max([matcher.rate(c, target)[0] for c in english_found]
                          or [0])
        if english_fit > 0:
            english.append((_order(source, english_fit, index),
                            dict(source, subs_mode="english",
                                 subs_fit=_as_percent(english_fit))))
    native.sort(key=lambda row: row[0])
    llm.sort(key=lambda row: row[0])
    english.sort(key=lambda row: row[0])
    return ([row for _key, row in native[:limit]],
            [row for _key, row in llm[:limit]],
            [row for _key, row in english[:ENGLISH_ROWS]])



def english_candidates(meta, already=None):
    """English subtitles for this title, without asking twice.

    The translation round already asks for English whenever there is an
    engine, so those are reused; only without one is English asked for on its
    own, and that answer is cached like every other.
    """
    found = [c for c in (already or []) if c.get("language") == "en"]
    if found:
        return found
    key = cache_key(meta, ["en"])
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        from . import auto
        found = [c for c in auto.search_candidates(meta, ["en"])
                 if c.get("language") == "en"]
    except Exception:
        kodi.log_exception("could not look up English subtitles")
        return []
    cache.set(key, found, TTL)
    return found


def _order(source, fit, index):
    """Cached, then fit, then resolution, then the smaller file.

    Written out rather than left to the ranking's own order, because that
    order also weighs the Hebrew outlook and a dozen other signals - so two
    releases with the same fit and the same resolution came out in whichever
    order those put them, and the 8 GB one could sit above the 2 GB one. With
    everything that matters to the viewer equal, the smaller file is the
    better answer on a box with little storage and a wifi connection.
    """
    return (0 if source.get("cached") else 1, -fit,
            -settings.resolution_rank(source.get("quality")),
            source.get("size") or 0, index)


def for_sources(meta, sources, refresh=False):
    """The outlook keyed by infohash, for callers that want a table."""
    if not sources:
        return {}
    annotate(meta, sources, candidates(meta, refresh=refresh))
    return {(s.get("hash") or s.get("title") or ""):
            {"kind": s.get("subs_kind"), "score": s.get("subs_score")}
            for s in sources if (s.get("hash") or s.get("title"))}


def ranking_score(source):
    """One number for sorting: an embedded claim counts as a perfect match.

    A release that carries Hebrew needs no external subtitle at all, so for
    the purpose of ordering it is the best possible outcome rather than an
    unscored one.
    """
    if source.get("subs_kind") in (EMBEDDED, NATIVE):
        return 100
    if source.get("subs_kind") == AI:
        score = int(source.get("subs_score") or 0)
        return min(score - AI_PENALTY, _threshold() - 1)
    return int(source.get("subs_score") or 0)


def _for_one(meta, source, candidates):
    name = source.get("title") or ""
    if _claims_hebrew(name) or "he" in (source.get("languages") or []):
        return {"kind": EMBEDDED, "score": 0}
    if not candidates:
        return {"kind": NONE, "score": 0}

    target = matcher.target_from(meta, {
        "release": name,
        "group": source.get("group") or release.parse(name)["group"],
        "quality": source.get("quality") or "",
    })
    hebrew = _hebrew_code()
    best = translatable = 0
    for candidate in candidates:
        score = matcher.rate(candidate, target)[0]
        if candidate.get("language", hebrew) == hebrew:
            best = max(best, score)
        else:
            translatable = max(translatable, score)
    # A human Hebrew subtitle that clears the threshold is the better answer.
    # A translation is offered in its place only when the Hebrew on offer does
    # not fit this release and a subtitle in another language fits it better.
    if best < _threshold() and translatable > best:
        return {"kind": AI, "score": _as_percent(translatable)}
    if best <= 0:
        return {"kind": NONE, "score": 0}
    return {"kind": EXTERNAL, "score": _as_percent(best)}


def _as_percent(score):
    """The matcher's score, which is already a percentage.

    It was being divided by the sum of the weights to "convert" it, and it
    did not need converting: `score_candidate` clamps to 0..100 before
    returning. So everything was scaled down by a factor of 1.38 and the
    ceiling became unreachable - a subtitle matched by file hash, or one
    with the identical release name, is a certainty and scored 100, and the
    picker showed it as 72%. A release matched on group, source and
    resolution scored 72 and showed as 52%, which is what was on screen and
    what prompted "52% isn't good enough": it was 72% all along, and even
    that was being read as a worse answer than it is.
    """
    return max(1, min(100, int(round(score))))


def _hebrew_code():
    langs = settings.get_list("subs.languages") or ["he"]
    return langs[0]


def _threshold():
    return settings.get_int("subs.threshold", 70)

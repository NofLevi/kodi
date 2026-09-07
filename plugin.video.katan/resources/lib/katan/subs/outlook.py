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


def cache_key(meta):
    ids = meta.get("ids") or {}
    return cache.make_key("suboutlook", ids.get("imdb") or ids.get("tmdb"),
                          meta.get("season"), meta.get("episode"))


def candidates(meta, refresh=False):
    """Every Hebrew subtitle any provider has for this title.

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
        found = []
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
    if found is None:
        found = candidates(meta)
    for source in sources:
        entry = _for_one(meta, source, found)
        source["subs_kind"] = entry["kind"]
        source["subs_score"] = entry["score"]
    kodi.log("subtitle outlook: %d candidates over %d sources"
             % (len(found), len(sources)))
    return sources


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
    if source.get("subs_kind") == EMBEDDED:
        return 100
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
    best = 0
    for candidate in candidates:
        # score_candidate writes the score onto the candidate, so it is
        # scored against a copy - the same candidate is used for every source
        # and must not carry the last one's answer into the next.
        scratch = dict(candidate)
        matcher.score_candidate(scratch, target)
        best = max(best, int(scratch.get("score") or 0))
    if best <= 0:
        return {"kind": NONE, "score": 0}
    return {"kind": EXTERNAL, "score": _as_percent(best)}


def _as_percent(score):
    """The matcher's points as the percentage the chooser already shows."""
    ceiling = float(matcher.WEIGHT_EXACT_NAME + matcher.WEIGHT_GROUP
                    + matcher.WEIGHT_SOURCE + matcher.WEIGHT_RESOLUTION
                    + matcher.WEIGHT_CODEC)
    return max(1, min(100, int(round(score / ceiling * 100))))


def _hebrew_code():
    langs = settings.get_list("subs.languages") or ["he"]
    return langs[0]

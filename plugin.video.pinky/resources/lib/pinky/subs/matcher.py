"""Deciding which subtitle candidate is worth downloading.

This is the "high correlation" part. Candidates are scored before anything is
downloaded, so the pipeline fetches one file rather than a dozen. The signals,
in order of how much they are trusted:

1. A file hash match. Byte-identical file, so the timings cannot be wrong.
2. The same release group. Groups mux their own timings, so a subtitle made
   for a group release fits that release and usually only that one.
3. The same source and resolution, which usually implies the same runtime and
   the same placement of adverts or recaps.
4. A provider-reported sync percentage, where one is offered.

Nothing here touches the network.
"""
import functools
import re

from ..utils import release


def candidate_key(candidate, effective_language=None):
    """Provider-local download identity, scoped to extracted language."""
    provider = str(candidate.get("provider") or "").strip().lower()
    language = str(effective_language or candidate.get("language") or "").lower()
    handle = candidate.get("download")
    if handle not in (None, ""):
        return ("download", provider, str(handle), language)
    file_id = candidate.get("file_id")
    if file_id not in (None, ""):
        return ("file", provider, str(file_id), language)
    return ("release", provider,
            str(candidate.get("release") or "").strip().lower(), language)

# The scale is a probability that this subtitle fits this file, and the
# weights are chosen so the arithmetic lands where a person would. Each rung
# holds on its own; a matching codec adds five on top and is never needed to
# reach one:
#
#     100   the same file by hash, or the same release name. Certain - and
#           nothing else may claim it, so the additive path stops at 99.
#      95   the same group as well as the same source and resolution. Groups
#           mux their own timings, so a subtitle made for a group release
#           fits it.
#      70   the right title, the same source and the same resolution, a
#           different group. Likely: retail subtitles for a BluRay generally
#           fit every BluRay rip of the same cut.
#      55   the right title and the same source, nothing else known.
#      40   the right title and nothing else.
#       0   demonstrably the wrong episode.
#
# The 70 and the 95 used to be true only when the codec agreed as well.
# Resolution was worth 10, so title, source and resolution came to 65 - under
# the default threshold of 70 - and a film matched on everything except a
# codec token was used "below threshold" or not at all. Episodes hid it,
# because their episode bonus carried them over. The calibration test that
# was meant to guard the 70 happened to include an x264 on both sides, so it
# passed throughout.
#
# WEIGHT_TITLE is the piece that was missing, and it was the largest one. Every
# candidate in this list is the answer to a search for one specific film or
# show - by IMDb id, for the providers that take one - so knowing it is for
# the right title is evidence, and it was being scored as though it were
# worth nothing. That is why a subtitle agreeing on source, resolution *and*
# codec came out at 33%: three weak signals and no credit for the strong one.
# A viewer reading 33 next to the best Hebrew subtitle in existence for a film
# concludes the add-on cannot find subtitles, and they are right to.
WEIGHT_HASH = 100
WEIGHT_TITLE = 40
WEIGHT_GROUP = 25
WEIGHT_SOURCE = 15
WEIGHT_RESOLUTION = 15
WEIGHT_CODEC = 5

# Where evidence that is not certainty has to stop. 100 is reserved for the
# same file or the same name: the chooser shows a bare "100%" as exact, and
# group, source, resolution and codec together would otherwise add up to it.
ADDITIVE_CEILING = 99
WEIGHT_PROVIDER_SYNC = 15
WEIGHT_EXACT_NAME = 100

# Naming the right episode of the right series, which was worth twelve points
# and is worth far more than that.
#
# The symptom: an anime episode's picker drew every row at 52%, because 52 is
# what this arithmetic produces - 40 for the title and 12 for the episode -
# and for anime it is *all* the arithmetic there is. A subtitle called
# "Attack on Titan - S01E12 - Wound" carries no group, no source, no
# resolution and no codec to agree with a release called
# "[Leopard-Raws] Shingeki no Kyojin - S01E12", so the two facts it does
# state are the only two that can score. The viewer reads 52% next to a
# subtitle that is unambiguously the right episode.
#
# The number is documented as a probability that this subtitle fits this
# file, and two independent rulers say the probability is about three
# quarters, not one half. Against a hash-matched reference, candidates
# scoring under 55 measured a fit of 0.64, and 0.78 after re-timing. Against
# a second, differently-named upload of the same episode, they measured a
# median agreement of 0.75. So title-plus-episode is set to land on 70 - the
# threshold, and the ladder's "right title, same source and resolution" rung -
# rather than on 52.
#
# It changes no ordering, because every candidate for an episode either names
# it or is zeroed by PENALTY_WRONG_EPISODE. What it changes is the decision
# the threshold makes: at 52 a correct English subtitle was "below
# threshold", so an AI translation was preferred to it, which on Hikaru no Go
# meant a perfect file being fed to a model that was out of quota.
#
# Both samples are small - the download quota ran out mid-measurement - so
# this is due a re-run before it is treated as settled.
WEIGHT_EPISODE = 30

# Applied when a candidate is clearly for something else.
PENALTY_WRONG_EPISODE = -100
PENALTY_DIFFERENT_EDITION = -50


def score_candidate(candidate, target, video_hash=""):
    """Score one candidate in roughly 0..100, writing the answer onto it."""
    score, reason = rate(candidate, target, video_hash)
    candidate["score"] = score
    candidate["reason"] = reason
    return score


def rate(candidate, target, video_hash=""):
    """The same judgement as a (score, reason) pair, touching nothing.

    Split out because the source picker weighs *every* subtitle against
    *every* release - 240 sources against 32 candidates on a real search -
    and the only reason it was copying a dictionary 7,680 times was to keep
    `score_candidate` from writing its answer into a candidate that the next
    source would reuse. A function that returns its answer needs no copy.
    """
    if target.get("anime"):
        return _rate_anime(candidate, target, video_hash)
    return _rate_scene(candidate, target, video_hash)


def _rate_scene(candidate, target, video_hash=""):
    """The film and series scorer - and the first half of the anime one."""
    name = candidate.get("release") or candidate.get("name") or ""
    parsed = release.parse(name)

    # Explicit episode metadata is a hard contradiction and must be checked
    # before every certainty shortcut, including hash and identical names.
    if _contradicts_episode(parsed, target):
        return 0, "wrong episode"

    if _contradicts_title(parsed, name, target):
        return 0, "wrong title"

    if candidate.get("hash_match") or (
            video_hash and candidate.get("moviehash") == video_hash):
        # A hash is the strongest evidence there is, and it still loses to a
        # release name that says, in as many words, that this is a different
        # episode. Both happen: OpenSubtitles' index has one Breaking Bad
        # S01E01 hash registered against S01E07, against The Vampire Diaries
        # and against a Bollywood film, every row claiming `moviehash`.
        # Returning 100 here first meant the wrong-episode check below could
        # never run, so the worst kind of mismatch scored the highest mark
        # available.
        if not _contradicts_episode(parsed, target):
            return 100, "hash"
    total = WEIGHT_TITLE
    reasons = []

    if name and target.get("release"):
        if _same_name(name, target["release"]):
            return 100, "identical release name"

    target_editions = set(target.get("editions") or [])
    candidate_editions = set(parsed.get("editions") or [])
    if target_editions and candidate_editions \
            and target_editions.isdisjoint(candidate_editions):
        total += PENALTY_DIFFERENT_EDITION
        reasons.append("different edition")

    if parsed["group"] and parsed["group"] == target.get("group"):
        total += WEIGHT_GROUP
        reasons.append("group")

    if parsed["source"] != "unknown" and parsed["source"] == target.get("source"):
        total += WEIGHT_SOURCE
        reasons.append("source")

    # The "unknown" guard matters as much here as it does for source and
    # codec, and was missing: two names that both fail to state a resolution
    # were being credited for agreeing about nothing. It got worse when the
    # parser started returning "unknown" rather than assuming "sd".
    if (parsed["resolution"] != "unknown"
            and parsed["resolution"] == target.get("resolution")):
        total += WEIGHT_RESOLUTION
        reasons.append("resolution")

    if parsed["codec"] != "unknown" and parsed["codec"] == target.get("codec"):
        total += WEIGHT_CODEC
        reasons.append("codec")

    if target.get("type") == "episode":
        season = int(target.get("season") or 0)
        episode = int(target.get("episode") or 0)
        if parsed["season"] or parsed["episode"] or parsed["absolute"]:
            if release.matches_episode(parsed, season, episode,
                                       target.get("absolute")):
                total += WEIGHT_EPISODE
            else:
                total += PENALTY_WRONG_EPISODE
                reasons.append("wrong episode")

    sync = candidate.get("sync_percent")
    if sync:
        try:
            total += int(WEIGHT_PROVIDER_SYNC * min(1.0, float(sync) / 100.0))
            reasons.append("provider sync")
        except (TypeError, ValueError):
            pass

    if candidate.get("downloads"):
        # Popularity is weak evidence, worth a nudge and no more - and not
        # worth an exception. The line above already treats `sync_percent`
        # this way; this one did not, so a provider reporting "1,234" or
        # "many" raised out of `rank()` and cost the playback every candidate
        # from every provider, not just its own.
        try:
            total += min(6, int(candidate["downloads"]) // 500)
        except (TypeError, ValueError):
            pass

    return (max(0, min(ADDITIVE_CEILING, total)),
            ", ".join(reasons) or "title only")


# A track pulled out of a release's own MKV is published under that release's
# name with the track number glued on: "[AnimeRG] ... [pseudo]_track3_[eng]"
# is the English track of "[AnimeRG] ... [pseudo].mkv", in time by
# construction, and was losing its identical-name 100 to the suffix.
_EXTRACTED_TRACK = re.compile(r"(?:_track\d+)?(?:_\[[a-z]{2,3}\])?$", re.I)

# Where the cut came from, which is what decides timing for anime: a
# Blu-ray, a DVD, a broadcast or a web stream.
_ANIME_CUT = (("bd", re.compile(r"\b(?:bd|bdrip|bdremux|blu-?ray)\b", re.I)),
              ("dvd", re.compile(r"\bdvd(?:rip)?\b", re.I)),
              ("web", re.compile(r"\b(?:web|webrip|web-dl|cr|crunchyroll|"
                                 r"funimation|netflix|nf|amzn|hidive|b-global)\b",
                                 re.I)),
              ("tv", re.compile(r"\b(?:tv|hdtv|raw)\b", re.I)))

# A subtitle named only by its episode number - "055_LEG", "02.srt" - which
# the release parser, wanting a show name first, reads as saying nothing.
_BARE_NUMBER = re.compile(r"^\s*(\d{1,4})(?:v\d)?(?![pi]\b)(?:\D|$)", re.I)


def _anime_cut(name):
    for label, pattern in _ANIME_CUT:
        if pattern.search(name or ""):
            return label
    return ""


def _anime_leading_group(name):
    """The group a fansub name opens with, bracketed or not.

    OpenSubtitles strips the brackets: "[AnimeRG] Naruto ..." is filed as
    "AnimeRG. Naruto ...", which the release parser reads as a group of
    "pseudo" from its tail. For anime the group comes first.
    """
    text = (name or "").strip()
    bracket = re.match(r"^\[([^\]]{2,30})\]", text)
    if bracket:
        return release.normalise(bracket.group(1))
    word = re.match(r"^([A-Za-z0-9][A-Za-z0-9-]{1,29})[.\s_]", text)
    return release.normalise(word.group(1)) if word else ""


def _rate_anime(candidate, target, video_hash=""):
    """`rate` for anime: the film and series scorer, plus what it misses.

    A superset on purpose. Many anime subtitles are named scene-style and
    earn their source, resolution and codec points there; a separate ladder
    with smaller weights was tried first and the gate caught it lowering
    Attack on Titan, Death Note and Frieren. So this runs `_rate_scene` and
    only *adds* the evidence an anime name states that it cannot read: the
    fansub group at the front, a name that is only the episode number, a
    track extracted from the same MKV, and the Blu-ray or broadcast cut. The
    one thing that lowers a score is a bare number naming another episode.
    """
    name = candidate.get("release") or candidate.get("name") or ""
    parsed = release.parse(name)

    stated = bool(parsed["season"] or parsed["episode"] or parsed["absolute"])
    bare = None
    if not stated and target.get("type") == "episode":
        stem = _stem(name)
        found = _BARE_NUMBER.match(stem)
        # Only a name that is little more than the number: "055_LEG",
        # "02", "01 BNHA". "86 - Eighty Six - 01" starts with its title.
        if found and len(re.findall(r"[A-Za-z0-9]+", stem[found.end():])) > 2:
            found = None
        if found and int(found.group(1)) < 1900:
            bare = int(found.group(1))
            wanted = {int(target.get("episode") or 0),
                      int(target.get("absolute") or 0)} - {0}
            if bare not in wanted:
                return 0, "wrong episode"

    if name and target.get("release") and _same_name(
            _EXTRACTED_TRACK.sub("", _stem(name)), target["release"]):
        return 100, "identical release name"

    score, reason = _rate_scene(candidate, target, video_hash)
    if score in (0, 100):
        return score, reason
    reasons = [r for r in reason.split(", ") if r and r != "title only"]

    if bare is not None:
        score += WEIGHT_EPISODE
        reasons.append("episode")

    group = target.get("group") or ""
    if group and "group" not in reasons and _anime_leading_group(name) == group:
        score += WEIGHT_GROUP
        reasons.append("group")

    if "source" not in reasons:
        cut = _anime_cut(release.normalise(target.get("release") or ""))
        if cut and cut == _anime_cut(release.normalise(name)):
            score += WEIGHT_SOURCE
            reasons.append("cut")

    return (max(0, min(ADDITIVE_CEILING, score)),
            ", ".join(reasons) or "title only")


_A_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")


def _contradicts_title(parsed, name, target):
    """Does this name state that it is for something else?

    `WEIGHT_TITLE` is granted to every candidate on the grounds that it came
    back from a search for this title - and providers do not honour that.
    Asked for The Odyssey (2026) the best Hebrew subtitle in the list was
    **Doctor.Odyssey.S01E18.The.Wave.Part.2**, a television series, scored at
    70% for agreeing on source and resolution; the translation source was
    **The.Martian.2015**. Both read as "Hebrew subtitle, 70% fit" in the
    picker, and the first was then thrown out at playback for ending an hour
    before the film does - so the row promised Hebrew and the film played in
    English.

    Two things a name can say, and only things it *states*:

    * an episode, when what is playing is a film. A film has no S01E18, so
      this needs no second opinion and catches Doctor Odyssey;
    * a year nowhere near ours, on a name that does not carry our title's
      words. Either alone throws real subtitles away - a year rejects *1917*,
      whose title is a year, and missing words rejects every translated
      title - so it has to be both.

    Silence is not disagreement. A bare "Episode 2.srt" or an upload named
    after nothing states neither, and is left to the evidence below.
    """
    if target.get("type") == "movie" and (parsed.get("season")
                                          or parsed.get("episode")):
        return True
    year = int(target.get("year") or 0)
    if not year or not target.get("title"):
        return False
    years = [int(found) for found in _A_YEAR.findall(release.normalise(name))
             if int(found) <= year + 2]
    if not years or any(abs(found - year) <= 2 for found in years):
        return False
    return not release.mentions(name, target["title"])


def _contradicts_episode(parsed, target):
    """Does this name state an episode, and a different one from ours?

    Only a contradiction counts. A name that says nothing about episodes -
    "Episode 01 - Pilot.srt", or a bare hash upload - is not evidence against
    itself, and treating silence as disagreement would throw away most of the
    genuine hash matches there are.
    """
    if target.get("type") != "episode":
        return False
    if not (parsed["season"] or parsed["episode"] or parsed["absolute"]):
        return False
    return not release.matches_episode(parsed, int(target.get("season") or 0),
                                       int(target.get("episode") or 0),
                                       target.get("absolute"))


def explain(candidate):
    """The evidence behind a score, for a log or a tooltip."""
    return "%d%% (%s)" % (candidate.get("score", 0),
                          candidate.get("reason") or "title only")


def _same_name(left, right):
    return release.normalise(_stem(left)) == release.normalise(_stem(right))


@functools.lru_cache(maxsize=2048)
def _stem(name):
    """The release name a subtitle file was made for.

    Uses the same stripping as the group parser, so "X-AMIABLE.heb.srt" and
    "X-AMIABLE" are recognised as the same release. They are - one is the
    subtitle for the other - and this is the strongest match there is, so
    failing to see it cost a certain 100% and settled for a guess.

    Memoised for the reason `release.normalise` already gives in its own
    docstring, which applies here and was not acted on: the picker compares
    every subtitle against every release, so one candidate's stem is
    recomputed once per source. Profiled on the 240x32 case, this was 76,800
    calls for about 272 distinct strings, and the largest single cost in the
    pass.
    """
    return release.strip_subtitle_tags(name.rsplit("/", 1)[-1])


def target_from(meta, source=None):
    """What we are trying to match: the release the user is actually playing.

    The file name first, because for a season pack it is a different string
    from the torrent name and it is the one that matters. A pack called
    "Silo.S01.COMPLETE.1080p.WEB-DL-GRP" carries no episode number at all, so
    matching against it threw away the strongest signal there is - and every
    episode of that pack was matched against the same text, which is why one
    subtitle could look equally good for all ten.
    """
    source = source or meta.get("source") or {}
    release_name = (source.get("file_name") or source.get("release")
                    or meta.get("file_name") or "")
    parsed = release.parse(release_name)
    return {
        "release": release_name,
        "group": source.get("group") or parsed["group"],
        "source": parsed["source"],
        "resolution": source.get("quality") or parsed["resolution"],
        "codec": parsed["codec"],
        "editions": parsed["editions"],
        "title": meta.get("title") or "",
        "year": meta.get("year") or 0,
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        "absolute": meta.get("absolute"),
        "anime": bool((meta.get("extra") or {}).get("anime")),
    }


def rank(candidates, target, video_hash="", languages=None):
    """Score and sort candidates, best first, honouring language order."""
    order = {code: position for position, code in enumerate(languages or [])}
    for candidate in candidates:
        score_candidate(candidate, target, video_hash)

    def sort_key(candidate):
        language_rank = order.get(candidate.get("language", ""), len(order))
        evidence_rank = 0 if candidate.get("reason") == "hash" else 1
        return (language_rank, -candidate.get("score", 0), evidence_rank)

    return sorted(candidates, key=sort_key)


def best(candidates, target, threshold, video_hash="", languages=None):
    """The best candidate per language, and whether it clears the threshold.

    Returning per language matters: the Hebrew winner and the English winner
    are both useful, because a weak Hebrew match plus a strong English one is
    the case where AI translation gives the better result.
    """
    ranked = rank(candidates, target, video_hash, languages)
    winners = {}
    for candidate in ranked:
        language = candidate.get("language", "")
        if language not in winners:
            winners[language] = candidate
    for candidate in winners.values():
        candidate["accepted"] = candidate.get("score", 0) >= threshold
    return winners, ranked

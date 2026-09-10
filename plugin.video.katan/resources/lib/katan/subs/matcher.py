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
# weights are chosen so the arithmetic lands where a person would:
#
#     100   the same file by hash, or the same release name. Certain.
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
WEIGHT_RESOLUTION = 10
WEIGHT_CODEC = 5
WEIGHT_PROVIDER_SYNC = 15
WEIGHT_EXACT_NAME = 100
WEIGHT_EPISODE = 12

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
    name = candidate.get("release") or candidate.get("name") or ""
    parsed = release.parse(name)

    # Explicit episode metadata is a hard contradiction and must be checked
    # before every certainty shortcut, including hash and identical names.
    if _contradicts_episode(parsed, target):
        return 0, "wrong episode"

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
        # Popularity is weak evidence, worth a nudge and no more.
        total += min(6, int(candidate["downloads"]) // 500)

    return max(0, min(100, total)), ", ".join(reasons) or "title only"


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


def _stem(name):
    """The release name a subtitle file was made for.

    Uses the same stripping as the group parser, so "X-AMIABLE.heb.srt" and
    "X-AMIABLE" are recognised as the same release. They are - one is the
    subtitle for the other - and this is the strongest match there is, so
    failing to see it cost a certain 100% and settled for a guess.
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
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        "absolute": meta.get("absolute"),
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

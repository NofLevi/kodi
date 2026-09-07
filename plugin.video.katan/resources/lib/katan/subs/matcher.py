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

# Weights sum to 100 so the threshold setting reads as a percentage.
WEIGHT_HASH = 100
WEIGHT_GROUP = 45
WEIGHT_SOURCE = 15
WEIGHT_RESOLUTION = 12
WEIGHT_CODEC = 6
WEIGHT_PROVIDER_SYNC = 22
WEIGHT_EXACT_NAME = 60
WEIGHT_EPISODE = 10

# Applied when a candidate is clearly for something else.
PENALTY_WRONG_EPISODE = -100


def score_candidate(candidate, target, video_hash=""):
    """Score one candidate in roughly 0..100."""
    if candidate.get("hash_match") or (
            video_hash and candidate.get("moviehash") == video_hash):
        candidate["score"] = 100
        candidate["reason"] = "hash"
        return 100

    name = candidate.get("release") or candidate.get("name") or ""
    parsed = release.parse(name)
    total = 0
    reasons = []

    if name and target.get("release"):
        if _same_name(name, target["release"]):
            candidate["score"] = 100
            candidate["reason"] = "identical release name"
            return 100

    if parsed["group"] and parsed["group"] == target.get("group"):
        total += WEIGHT_GROUP
        reasons.append("group")

    if parsed["source"] != "unknown" and parsed["source"] == target.get("source"):
        total += WEIGHT_SOURCE
        reasons.append("source")

    if parsed["resolution"] == target.get("resolution"):
        total += WEIGHT_RESOLUTION
        reasons.append("resolution")

    if parsed["codec"] != "unknown" and parsed["codec"] == target.get("codec"):
        total += WEIGHT_CODEC
        reasons.append("codec")

    if target.get("type") == "episode":
        season = int(target.get("season") or 0)
        episode = int(target.get("episode") or 0)
        if parsed["season"] or parsed["episode"] or parsed["absolute"]:
            if release.matches_episode(parsed, season, episode):
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

    total = max(0, min(100, total))
    candidate["score"] = total
    candidate["reason"] = ", ".join(reasons) or "title only"
    return total


def _same_name(left, right):
    return release.normalise(_stem(left)) == release.normalise(_stem(right))


def _stem(name):
    stem = name.rsplit("/", 1)[-1]
    for extension in (".srt", ".sub", ".ass", ".ssa", ".mkv", ".mp4", ".avi"):
        if stem.lower().endswith(extension):
            stem = stem[:-len(extension)]
    return stem


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
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
    }


def rank(candidates, target, video_hash="", languages=None):
    """Score and sort candidates, best first, honouring language order."""
    order = {code: position for position, code in enumerate(languages or [])}
    for candidate in candidates:
        score_candidate(candidate, target, video_hash)

    def sort_key(candidate):
        language_rank = order.get(candidate.get("language", ""), len(order))
        return (language_rank, -candidate.get("score", 0))

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

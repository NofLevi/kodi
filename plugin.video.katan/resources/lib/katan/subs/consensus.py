# -*- coding: utf-8 -*-
"""Deciding which subtitle fits when there is nothing to check it against.

A hash match settles the question, and it is available **9% of the time** -
measured over a survey, not guessed. The other 91% is decided by how much a
filename resembles the release, and a survey of real playbacks says that is a
poor predictor of whether the subtitle is in time:

    score 66   fit 0.13   offset  -70.9s   Planet Earth
    score 62   fit 0.95   offset    0.0s   Ted Lasso
    score 77   fit 0.33   offset +176.4s   The Good Doctor
    score 58   fit 0.63   offset   -1.2s   Supernatural

The best-scoring one there is nearly three minutes out, and those offsets are
not drift - they are the wrong cut, a different broadcast, a version with the
recap left in. No amount of reading filenames finds that.

What does find it is the thing already on the table and thrown away: a typical
title comes back with **four** candidates, and 43 of 69 have three or more.
Two subtitles from different uploaders that agree on timing to within a second
are almost certainly both right, because there is no reason for two
independent people to be wrong in the same way. The one that disagrees with
both is wrong, and that can be known without a hash, without decoding a frame
of video and without trusting a single filename.

The cost is the reason this is careful. `sync.fit` is about 50ms for a
feature-length pair and a gzipped subtitle is tens of kilobytes, but this add-on
exists to run on a projector with a gigabyte of RAM shared with Android, and
"one subtitle download, not fifty" is a rule here. So: only when there is no
reference to check against, only when the name score is not already decisive,
never more than `MAX_CANDIDATES`, and fewer than that on a low-memory profile.
"""
from .. import kodi, settings
from . import sync

# Enough to break a tie, few enough to stay honest about the rule it bends.
# Two can only say "these agree" or "these differ"; three can say which one is
# the odd one out, which is the answer worth having.
MAX_CANDIDATES = 3
# Three compressed subtitle files are still only tens of kilobytes and are the
# minimum that can establish a majority. Saving one download here removes the
# proof entirely, so weak devices keep the same bounded evidence budget.
LOW_MEMORY_CANDIDATES = 3

# Above this the name is decisive on its own - an identical release name or a
# verified hash - and spending two more downloads to confirm it is waste.
DECISIVE_SCORE = 90

# Two subtitles agree when correlating them scores at least this and the shift
# between them is small. `sync.MIN_CONFIDENCE` is 0.45 for applying a
# correction; agreement is a stronger claim than that and is held to it.
AGREE_CONFIDENCE = 0.55
AGREE_SECONDS = 2.0
# A pair that needs framerate conversion describes compatible content, but not
# the same video timeline; using it as the majority clock would introduce drift.
AGREE_SCALE_DELTA = 0.0005


def wanted(top_score, has_reference):
    """Is it worth downloading more than one subtitle for this title?"""
    if has_reference:
        return False          # something trustworthy already settles it
    if top_score >= DECISIVE_SCORE:
        return False
    return settings.get_bool("subs.consensus", True)


def budget():
    """How many candidates may be fetched, given the device."""
    if settings.get("device.profile") == "low_memory":
        return LOW_MEMORY_CANDIDATES
    return MAX_CANDIDATES


def distinct(candidates, language, limit):
    """The top few candidates that are actually independent of each other.

    Two rows from the same provider naming the same release are one opinion
    twice over, and asking a question twice does not make the answer better.
    Different providers first, then different release names.
    """
    chosen = []
    seen_providers = set()
    seen_releases = set()
    for candidate in candidates:
        if candidate.get("language") != language:
            continue
        provider = candidate.get("provider", "")
        name = (candidate.get("release") or "").strip().lower()
        if name and name in seen_releases:
            continue
        # One per provider on the first pass, so three candidates are three
        # opinions rather than three files from one uploader.
        if provider in seen_providers and len(chosen) < limit:
            continue
        chosen.append(candidate)
        seen_providers.add(provider)
        if name:
            seen_releases.add(name)
        if len(chosen) >= limit:
            break
    return chosen


def agree(left, right):
    """Do these two subtitles tell the same story about time?

    Returns (agreed, confidence, offset). Correlation is symmetric enough for
    this purpose, and cheap: about 50ms for a feature-length pair.
    """
    try:
        offset, scale, confidence = sync.fit(left, right)
    except Exception:
        kodi.log_exception("comparing two subtitles failed")
        return False, 0.0, 0.0
    agreed = (confidence >= AGREE_CONFIDENCE
              and abs(offset) <= AGREE_SECONDS
              and abs(scale - 1.0) <= AGREE_SCALE_DELTA)
    return agreed, confidence, offset


def choose(fetched):
    """Pick the best-supported subtitle from what was downloaded.

    `fetched` is [(candidate, cues), ...] in score order. Returns
    (candidate, cues, report) - and never returns nothing: with no agreement
    anywhere the highest-scoring one is still the best guess available, and
    saying so is more use than refusing to show anything.
    """
    usable = [(candidate, cues) for candidate, cues in fetched if cues]
    if not usable:
        return None, [], {"reason": "nothing downloaded", "supported": 0}
    if len(usable) == 1:
        return usable[0][0], usable[0][1], {"reason": "only one candidate",
                                            "supported": 0}

    # Every pair, which is three comparisons for three candidates.
    support = [0] * len(usable)
    best_pair = (0.0, None)
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            agreed, confidence, offset = agree(usable[i][1], usable[j][1])
            if agreed:
                support[i] += 1
                support[j] += 1
                if confidence > best_pair[0]:
                    best_pair = (confidence, (i, j))
            kodi.log("consensus: %s vs %s -> %s (%.2f, %+.1fs)"
                     % (usable[i][0].get("provider"),
                        usable[j][0].get("provider"),
                        "agree" if agreed else "differ", confidence, offset))

    most = max(support)
    if most == 0:
        # Nobody agrees with anybody. That is worth saying rather than
        # pretending: it usually means several different cuts of the episode.
        candidate, cues = usable[0]
        return candidate, cues, {"reason": "no two candidates agreed",
                                 "supported": 0}

    # The agreeing group, and within it the one the name score liked best -
    # `usable` is already in score order, so the first is that one.
    for index, (candidate, cues) in enumerate(usable):
        if support[index] == most:
            return candidate, cues, {"reason": "agreed with %d other%s"
                                     % (most, "" if most == 1 else "s"),
                                     "supported": most}
    return usable[0][0], usable[0][1], {"reason": "unreachable",
                                        "supported": 0}


def _independent(left, right):
    """Different catalogues, or named uploaders inside one catalogue."""
    left_provider = left.get("provider", "")
    right_provider = right.get("provider", "")
    if not left_provider or not right_provider:
        return False
    if left_provider != right_provider:
        return True
    left_uploader = left.get("uploader", "")
    right_uploader = right.get("uploader", "")
    return bool(left_uploader and right_uploader
                and left_uploader != right_uploader)


def verification_candidates(candidates, wanted, limit):
    """Spend a tiny download budget on independent timing evidence.

    Keep the best target-language file, then prefer one candidate per other
    language. Different languages are independent subtitle timelines and give
    more evidence than near-duplicate uploads in the requested language.
    """
    if limit <= 0:
        return []
    target = [candidate for candidate in candidates
              if candidate.get("language") == wanted]
    other_languages = [candidate for candidate in candidates
                       if candidate.get("language")
                       and candidate.get("language") != wanted]
    # Cross-language proof needs a target plus two independent references.
    # Select the pair jointly: the first row per language can be a correlated
    # upload while a valid independent alternative sits immediately below it.
    if target and limit >= 3:
        for left_index, left in enumerate(other_languages):
            for right in other_languages[left_index + 1:]:
                if (left.get("language") != right.get("language")
                        and _independent(left, right)):
                    return [target[0], left, right]
    return distinct(candidates, wanted, limit)


def timeline_reference(fetched, wanted):
    """A reference timeline proved by two agreeing non-target languages.

    Two independent languages agreeing is evidence about the video cut without
    decoding audio. One other-language file alone is still only a guess.
    """
    others = [(candidate, cues) for candidate, cues in fetched
              if cues and candidate.get("language") != wanted]
    support = [0] * len(others)
    for left in range(len(others)):
        for right in range(left + 1, len(others)):
            if not _independent(others[left][0], others[right][0]):
                continue
            agreed, _confidence, _offset = agree(others[left][1], others[right][1])
            if agreed:
                support[left] += 1
                support[right] += 1
    if not support or max(support) == 0:
        return None, [], {"reason": "no cross-language agreement", "supported": 0}
    winner = max(range(len(others)),
                 key=lambda index: (support[index],
                                    others[index][0].get("score", 0)))
    candidate, cues = others[winner]
    return candidate, cues, {"reason": "cross-language timeline",
                              "supported": support[winner]}

"""Automatic subtitle synchronisation.

Name matching tells you a subtitle is *probably* right. This module makes a
subtitle *actually* fit, which is a different and better guarantee: it measures
where the speech is and slides the subtitle until the two line up.

How it works
------------
A subtitle is turned into a binary "someone is talking" signal, one bit per
time bin. The same is done for a reference timeline, which is normally the
embedded subtitle track already inside the video file. Sliding one signal past
the other and counting overlapping bits gives the offset that fits best.

Doing that in pure Python would be far too slow on a weak box, so the signals
are stored as Python big integers and the whole correlation at one lag becomes

    popcount(reference & (candidate << lag))

which runs at C speed inside the interpreter. A two hour film at 100 ms
resolution is a 72,000 bit integer, and scanning three minutes of possible
offsets costs a few milliseconds.

Two kinds of error are corrected:

* A constant offset, the usual "subtitles are three seconds late".
* Framerate drift, the classic PAL/NTSC mismatch where the subtitle starts in
  sync and slips further out as the film goes on. That is a linear scale, so a
  small set of known ratios is tried and the best scoring one wins.

The result is a linear fit plus a confidence score, so the caller can refuse a
bad alignment rather than silently making things worse.
"""
from .. import kodi
from . import srt

# Known framerate conversions, as scale factors applied to subtitle times.
FRAME_RATIOS = [
    1.0,
    24000.0 / 23976.0,      # 23.976 -> 24
    23976.0 / 24000.0,
    25.0 / 23.976,          # NTSC film -> PAL, the classic drift
    23.976 / 25.0,
    25.0 / 24.0,
    24.0 / 25.0,
    30000.0 / 29970.0,
    29970.0 / 30000.0,
]

COARSE_BIN_MS = 100
FINE_BIN_MS = 20
COARSE_MAX_OFFSET = 180.0    # seconds searched either way
FINE_MAX_OFFSET = 1.5

# Below this, an alignment is not trustworthy and the caller should keep the
# original timings rather than gamble.
MIN_CONFIDENCE = 0.45


def _popcount(value):
    """Bit count, using the fast builtin when the Python version has it."""
    try:
        return value.bit_count()          # Python 3.10+
    except AttributeError:
        return bin(value).count("1")


def activity_mask(cues, bin_ms, limit_bins=None, offset=0.0, scale=1.0):
    """Turn cues into a big integer where each set bit is a bin with speech."""
    mask = 0
    step = bin_ms / 1000.0
    for cue in cues:
        start = cue.start * scale + offset
        end = cue.end * scale + offset
        if end <= 0:
            continue
        first = int(max(0.0, start) / step)
        last = int(max(0.0, end) / step)
        if limit_bins is not None:
            if first >= limit_bins:
                continue
            last = min(last, limit_bins - 1)
        if last < first:
            last = first
        # Set the run of bits [first, last] in one shift-and-subtract.
        width = last - first + 1
        mask |= ((1 << width) - 1) << first
    return mask


def _score(reference_mask, candidate_mask, reference_bits, candidate_bits, bins):
    """Chance-corrected overlap, in roughly 0..1.

    Raw overlap is a trap: dialogue occupies a large share of a film, so two
    completely unrelated subtitle tracks still overlap around half the time and
    a naive score happily "aligns" the wrong episode. Subtracting the overlap
    expected from two independent signals of the same density makes the score
    mean what the threshold assumes: zero for unrelated, one for a true match.
    """
    smaller = min(reference_bits, candidate_bits)
    if smaller <= 0 or bins <= 0:
        return 0.0
    observed = _popcount(reference_mask & candidate_mask)
    expected = (reference_bits * candidate_bits) / float(bins)
    headroom = smaller - expected
    if headroom <= 0:
        return 0.0
    return max(0.0, (observed - expected) / headroom)


def _best_offset(reference, candidate, bin_ms, max_offset, scale=1.0,
                 reference_bits=None):
    """Search lags in both directions and return (offset_seconds, score)."""
    step = bin_ms / 1000.0
    max_lag = int(max_offset / step)

    candidate_mask = activity_mask(candidate, bin_ms, scale=scale)
    if not candidate_mask:
        return 0.0, 0.0
    reference_mask = activity_mask(reference, bin_ms)
    if not reference_mask:
        return 0.0, 0.0

    reference_bits = reference_bits or _popcount(reference_mask)
    candidate_bits = _popcount(candidate_mask)

    bins = max(reference_mask.bit_length(), candidate_mask.bit_length())

    best_lag, best = 0, -1.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            shifted = candidate_mask << lag
        else:
            shifted = candidate_mask >> (-lag)
        value = _score(reference_mask, shifted, reference_bits, candidate_bits, bins)
        if value > best:
            best, best_lag = value, lag
    return best_lag * step, best


def fit(candidate, reference):
    """Find the linear correction that maps candidate times onto reference.

    Returns (offset_seconds, scale, confidence). Apply with apply_fit().
    """
    if not candidate or not reference:
        return 0.0, 1.0, 0.0

    best = (0.0, 1.0, 0.0)
    for scale in FRAME_RATIOS:
        offset, score = _best_offset(reference, candidate, COARSE_BIN_MS,
                                     COARSE_MAX_OFFSET, scale=scale)
        if score > best[2]:
            best = (offset, scale, score)
        # A near perfect coarse match means the remaining ratios cannot win.
        if score > 0.9:
            break

    offset, scale, score = best
    if score <= 0:
        return 0.0, 1.0, 0.0

    # Refine the offset at high resolution around the coarse answer.
    shifted = [cue.shifted(offset, scale) for cue in candidate]
    delta, fine_score = _best_offset(reference, shifted, FINE_BIN_MS,
                                     FINE_MAX_OFFSET)
    if fine_score >= score:
        offset += delta
        score = fine_score

    return offset, scale, score


def apply_fit(cues, offset, scale=1.0):
    return srt.clamp_durations([cue.shifted(offset, scale) for cue in cues])


def synchronise(candidate, reference, minimum_confidence=MIN_CONFIDENCE):
    """Fit candidate onto reference, refusing to act on a weak match.

    Returns (cues, report). The cues are unchanged when confidence is too low,
    because a wrong shift is worse than the original timing.
    """
    offset, scale, confidence = fit(candidate, reference)
    report = {
        "offset": round(offset, 3),
        "scale": round(scale, 6),
        "confidence": round(confidence, 3),
        "applied": False,
        "reason": "",
    }

    if confidence < minimum_confidence:
        report["reason"] = "confidence below threshold"
        return candidate, report

    if abs(offset) < 0.05 and abs(scale - 1.0) < 1e-6:
        report["reason"] = "already in sync"
        return candidate, report

    report["applied"] = True
    kodi.log("subtitle sync: offset %.2fs scale %.5f confidence %.2f"
             % (offset, scale, confidence))
    return apply_fit(candidate, offset, scale), report


def estimate_quality(candidate, reference):
    """Confidence that two subtitle tracks describe the same content.

    Used to sanity check a downloaded subtitle before it is ever shown: a file
    for the wrong episode scores near zero however it is shifted.
    """
    return fit(candidate, reference)[2]

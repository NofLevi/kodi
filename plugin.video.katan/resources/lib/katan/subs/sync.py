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

Three kinds of error are corrected:

* A constant offset, the usual "subtitles are three seconds late".
* Framerate drift, the classic PAL/NTSC mismatch where the subtitle starts in
  sync and slips further out as the film goes on. That is a linear scale, so a
  small set of known ratios is tried and the best scoring one wins.
* **Splits**, where one file is cut differently from the other - an advert
  break, a recap left in, a director's cut. No single offset fixes those, and
  they are not rare: the worst case a survey of real playbacks found was 176
  seconds out at the end and fine at the start.

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

# Splits. One segment per ten minutes or so, because that is roughly the
# spacing of advert breaks and short enough to isolate a missing recap, while
# still leaving each segment enough dialogue to correlate honestly.
SEGMENT_MINUTES = 10.0
MIN_SEGMENTS = 2
MAX_SEGMENTS = 8
MIN_SEGMENT_CUES = 25        # below this a segment correlates with anything

# A segment already scoring this well where the global fit put it is left
# alone without searching at all, which is every segment of a healthy file and
# is what keeps splits nearly free. Measured on synthetic tracks a correctly
# placed segment scores 1.0 and unrelated content tops out at 0.18, so there
# is a wide gap to sit in; these are the two knobs to move if real files
# disagree.
SEGMENT_SETTLED = 0.6

# What a split has to be worth. alass pays a `--split-penalty` out of its
# rating for every break it introduces (default 7 of 1000, useful 5-20); the
# same idea stated the other way round - a segment keeps its own offset only
# when that both clears an absolute floor and beats staying put by a margin.
# The floor is the important half: without it, six blocks of an unrelated
# subtitle each find a different spurious offset and the wrong episode gets
# assembled into place piece by piece.
MIN_SEGMENT_SCORE = 0.5
SPLIT_MARGIN = 0.06


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
                 reference_bits=None, reference_mask=None):
    """Search lags in both directions and return (offset_seconds, score).

    `reference_mask` is accepted so a caller comparing many candidates against
    one reference - which is what looking for splits does - builds the
    reference side once rather than once per block.
    """
    step = bin_ms / 1000.0
    max_lag = int(max_offset / step)

    candidate_mask = activity_mask(candidate, bin_ms, scale=scale)
    if not candidate_mask:
        return 0.0, 0.0
    if reference_mask is None:
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


# --------------------------------------------------------------------------
# splits
# --------------------------------------------------------------------------


def _segment_count(cues):
    """How many pieces to cut this subtitle into, from how long it runs."""
    span = (cues[-1].end - cues[0].start) / 60.0 if cues else 0.0
    wanted = int(span / SEGMENT_MINUTES)
    wanted = max(MIN_SEGMENTS, min(MAX_SEGMENTS, wanted))
    # Never make a segment too short to say anything trustworthy.
    return max(1, min(wanted, len(cues) // MIN_SEGMENT_CUES))


def fit_segments(candidate, reference, offset=0.0, scale=1.0,
                 window=COARSE_MAX_OFFSET, margin=SPLIT_MARGIN):
    """Per-segment corrections on top of a global fit.

    Returns [(first_index, last_index, extra_offset), ...] covering every cue
    in order. A single entry with an extra offset of zero means the global fit
    was the whole answer, which is the common case and the cheap one.

    ponytail: segments are fixed cue-count blocks, not alass's dynamic program
    over optimal split points. That finds a break wherever it truly is; this
    finds it to within a block, which is enough to re-time both sides of an
    advert break and costs a fraction of a global fit instead of millions of
    interpreter operations on a four-core A53.
    """
    count = _segment_count(candidate)
    if count < 2 or not reference:
        return [(0, len(candidate) - 1, 0.0)]

    shifted = [cue.shifted(offset, scale) for cue in candidate]
    size = len(candidate) // count
    # Built once: every block is compared against the same reference, and
    # rebuilding a 72,000 bit mask eight times was most of the cost.
    mask = activity_mask(reference, COARSE_BIN_MS)
    bits = _popcount(mask)

    found = []
    for index in range(count):
        first = index * size
        last = len(candidate) - 1 if index == count - 1 else (first + size - 1)
        block = shifted[first:last + 1]
        # What this block already scores where the global fit put it. A block
        # that is fine there is left alone without a search, which is every
        # block of a healthy file - so the usual cost of looking for splits is
        # one zero-lag comparison each.
        _zero, base = _best_offset(reference, block, COARSE_BIN_MS, 0.0,
                                   reference_bits=bits, reference_mask=mask)
        local = 0.0
        if base < SEGMENT_SETTLED:
            local, score = _best_offset(reference, block, COARSE_BIN_MS,
                                        window, reference_bits=bits,
                                        reference_mask=mask)
            if (abs(local) < 0.05 or score < MIN_SEGMENT_SCORE
                    or score < base + margin):
                local = 0.0
        found.append((first, last, local))

    return _merge(found)


def _merge(segments):
    """Join neighbouring segments that agreed, so the count means something."""
    merged = []
    for first, last, local in segments:
        if merged and abs(merged[-1][2] - local) < 0.05:
            merged[-1] = (merged[-1][0], last, merged[-1][2])
        else:
            merged.append((first, last, local))
    return merged


def apply_segments(cues, offset, scale, segments):
    out = []
    for first, last, local in segments:
        for cue in cues[first:last + 1]:
            out.append(cue.shifted(offset + local, scale))
    return srt.clamp_durations(out)


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
        "segments": 1,
        "reason": "",
    }

    if confidence < minimum_confidence:
        report["reason"] = "confidence below threshold"
        return candidate, report

    # Splits are checked before "already in sync", because a file cut
    # differently is in sync for the first half and that is exactly how it
    # gets away with being wrong.
    segments = fit_segments(candidate, reference, offset, scale)
    report["segments"] = len(segments)
    if len(segments) > 1:
        report["applied"] = True
        report["reason"] = "%d segments" % len(segments)
        kodi.log("subtitle sync: %d segments, offsets %s"
                 % (len(segments),
                    ", ".join("%+.1fs" % (offset + local)
                              for _f, _l, local in segments)))
        return apply_segments(candidate, offset, scale, segments), report

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

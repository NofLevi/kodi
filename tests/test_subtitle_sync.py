"""The sync engine is what makes a subtitle actually fit, not merely match."""
import random

import pytest

from katan.subs import srt, sync


def make_cues(count=400, seed=1, start=12.0):
    """A plausible dialogue track: irregular gaps, varied line lengths."""
    rng = random.Random(seed)
    cues = []
    time = start
    for index in range(count):
        gap = rng.uniform(0.4, 6.0)
        length = rng.uniform(0.9, 4.0)
        time += gap
        cues.append(srt.Cue(index + 1, time, time + length, "line %d" % index))
        time += length
    return cues


def test_detects_a_constant_offset():
    reference = make_cues()
    late = [c.shifted(7.5) for c in reference]
    offset, scale, confidence = sync.fit(late, reference)
    assert confidence > 0.9
    assert offset == pytest.approx(-7.5, abs=0.05)
    assert scale == pytest.approx(1.0, abs=0.001)


def test_detects_a_negative_offset():
    reference = make_cues()
    early = [c.shifted(-4.2) for c in reference]
    offset, _, confidence = sync.fit(early, reference)
    assert confidence > 0.9
    assert offset == pytest.approx(4.2, abs=0.05)


def test_detects_pal_ntsc_framerate_drift():
    """The classic case: in sync at the start, minutes out by the end."""
    reference = make_cues(count=600)
    drift = 25.0 / 23.976
    drifted = [c.shifted(0.0, drift) for c in reference]
    assert abs(drifted[-1].start - reference[-1].start) > 60, "test data is not drifting"

    offset, scale, confidence = sync.fit(drifted, reference)
    assert confidence > 0.85
    corrected = sync.apply_fit(drifted, offset, scale)
    assert abs(corrected[-1].start - reference[-1].start) < 1.0
    assert abs(corrected[0].start - reference[0].start) < 1.0


def test_truncated_excerpt_cannot_be_verified_at_full_confidence():
    reference = make_cues(count=400, seed=7)
    excerpt = [reference[0].shifted(7.0)]
    _fixed, report = sync.synchronise(excerpt, reference)
    assert report["applied"] is False
    assert report["confidence"] < sync.MIN_CONFIDENCE


def test_synchronise_applies_a_real_shift():
    reference = make_cues()
    late = [c.shifted(9.0) for c in reference]
    fixed, report = sync.synchronise(late, reference)
    assert report["applied"] is True
    assert report["confidence"] > 0.9
    assert abs(fixed[0].start - reference[0].start) < 0.2


def test_synchronise_leaves_an_already_synced_track_alone():
    reference = make_cues()
    fixed, report = sync.synchronise(list(reference), reference)
    assert report["applied"] is False
    assert report["reason"] == "already in sync"
    assert fixed[0].start == reference[0].start


def test_synchronise_refuses_an_unrelated_subtitle():
    """A file for the wrong episode must not be shifted into place."""
    reference = make_cues(count=400, seed=1)
    unrelated = make_cues(count=400, seed=99, start=40.0)
    _, report = sync.synchronise(unrelated, reference)
    assert report["applied"] is False
    assert report["confidence"] < sync.MIN_CONFIDENCE


def test_quality_separates_right_from_wrong_content():
    reference = make_cues(seed=1)
    same_but_late = [c.shifted(11.0) for c in reference]
    different = make_cues(seed=42, start=33.0)
    assert sync.estimate_quality(same_but_late, reference) > 0.9
    assert sync.estimate_quality(different, reference) < 0.5


def test_activity_mask_marks_the_expected_bins():
    cues = [srt.Cue(1, 1.0, 2.0, "a"), srt.Cue(2, 5.0, 5.5, "b")]
    mask = sync.activity_mask(cues, 100)
    assert (mask >> 10) & 1, "1.0s should be active at 100ms bins"
    assert not (mask >> 30) & 1, "3.0s should be silent"
    assert (mask >> 50) & 1, "5.0s should be active"


def test_empty_input_is_safe():
    assert sync.fit([], make_cues()) == (0.0, 1.0, 0.0)
    assert sync.fit(make_cues(), []) == (0.0, 1.0, 0.0)
    cues, report = sync.synchronise([], [])
    assert cues == [] and report["applied"] is False


def test_sync_is_fast_enough_for_a_weak_device():
    """A feature-length alignment must stay well inside a second."""
    import time
    reference = make_cues(count=1400)
    late = [c.shifted(23.0) for c in reference]
    started = time.time()
    sync.fit(late, reference)
    elapsed = time.time() - started
    assert elapsed < 2.5, "alignment took %.2fs" % elapsed


# --------------------------------------------------------------------------
# splits: an advert break, a recap left in, a different cut
# --------------------------------------------------------------------------


def test_finds_a_mid_file_jump():
    """The case a single offset cannot fix, and the one a survey found.

    A subtitle that is 2s late for the first half and 122s late for the second
    is not drifting - the file it was made for has two minutes the video does
    not, or the other way round. alass exists for this; so does fit_segments.
    """
    reference = make_cues(count=600)
    half = len(reference) // 2
    broken = ([c.shifted(2.0) for c in reference[:half]]
              + [c.shifted(122.0) for c in reference[half:]])

    fixed, report = sync.synchronise(broken, reference)
    assert report["segments"] > 1, "no split found"
    assert report["applied"] is True
    assert abs(fixed[0].start - reference[0].start) < 0.5
    assert abs(fixed[-1].start - reference[-1].start) < 0.5


def test_a_clean_subtitle_is_not_split():
    """A splitter that finds structure in a good file is worse than none."""
    reference = make_cues(count=600)
    late = [c.shifted(6.0) for c in reference]
    fixed, report = sync.synchronise(late, reference)
    assert report["segments"] == 1
    assert abs(fixed[0].start - reference[0].start) < 0.2


def test_an_unrelated_subtitle_is_never_segmented():
    """Segmenting noise would let a wrong episode be shifted into place."""
    reference = make_cues(count=600, seed=1)
    unrelated = make_cues(count=600, seed=99, start=40.0)
    segments = sync.fit_segments(unrelated, reference)
    assert len(segments) == 1


def test_segments_cover_every_cue_exactly_once():
    reference = make_cues(count=600)
    half = len(reference) // 2
    broken = ([c.shifted(2.0) for c in reference[:half]]
              + [c.shifted(122.0) for c in reference[half:]])
    segments = sync.fit_segments(broken, reference, 0.0, 1.0)
    assert segments[0][0] == 0
    assert segments[-1][1] == len(broken) - 1
    for before, after in zip(segments, segments[1:]):
        assert after[0] == before[1] + 1
    assert len(sync.apply_segments(broken, 0.0, 1.0, segments)) == len(broken)


def test_a_short_subtitle_is_never_segmented():
    """Too few cues to say anything, so it says nothing."""
    assert len(sync.fit_segments(make_cues(count=20), make_cues(count=20))) == 1


def test_segmenting_stays_inside_its_budget():
    """The whole point is that this costs a fraction of the global fit."""
    import time
    reference = make_cues(count=1400)
    late = [c.shifted(23.0) for c in reference]
    started = time.time()
    sync.fit_segments(late, reference, -23.0, 1.0)
    elapsed = time.time() - started
    assert elapsed < 1.0, "segmenting took %.2fs" % elapsed


def test_a_split_too_broken_to_score_globally_is_still_repaired():
    """The case the threshold used to hide.

    Three different offsets means no single shift explains more than a third
    of the file, so the global confidence lands under MIN_CONFIDENCE and the
    old code refused before it ever looked for a split. That is circular: a
    file cut differently *cannot* score well as a whole.
    """
    reference = make_cues(count=900)
    third = len(reference) // 3
    broken = ([c.shifted(1.0) for c in reference[:third]]
              + [c.shifted(97.0) for c in reference[third:third * 2]]
              + [c.shifted(-64.0) for c in reference[third * 2:]])

    _offset, _scale, whole = sync.fit(broken, reference)
    assert whole < sync.MIN_CONFIDENCE, (
        "test data is not broken enough (%.2f)" % whole)

    fixed, report = sync.synchronise(broken, reference)
    assert report["applied"] is True, report["reason"]
    assert report["segments"] > 1
    assert report["confidence"] > whole
    assert abs(fixed[0].start - reference[0].start) < 0.5
    assert abs(fixed[-1].start - reference[-1].start) < 0.5


def test_pieces_that_do_not_beat_the_whole_are_dropped():
    """Given enough pieces anything can be fitted to anything.

    So the segmented result is re-scored after the fact and has to clear the
    same threshold a single shift does. An unrelated subtitle must come back
    untouched however many pieces it was cut into.
    """
    reference = make_cues(count=900, seed=1)
    unrelated = make_cues(count=900, seed=77, start=51.0)
    fixed, report = sync.synchronise(unrelated, reference)
    assert report["applied"] is False
    assert report["reason"] == "confidence below threshold"
    assert [c.start for c in fixed] == [c.start for c in unrelated]


# --------------------------------------------------------------------------
# a subtitle is untrusted input
# --------------------------------------------------------------------------


def test_an_absurd_timestamp_cannot_build_an_enormous_mask():
    """A cue may legally claim to end 999 hours in, and one did not have to.

    `srt._TIME` accepts three-digit hours, so a downloaded file can name a cue
    ending at 3.6 million seconds. At a 100 ms bin that is a **36-million-bit
    integer**, which `_best_offset` then shifts, ANDs and popcounts 3601 times
    per scale across nine scales - minutes to hours of a four-core A53, or
    MemoryError, from one bad download. The timeline is now clamped, so a
    ridiculous timestamp costs a ridiculous cue rather than the device.
    """
    absurd = [srt.Cue(1, 0.0, 2.0, "fine"),
              srt.Cue(2, 3_600_000.0, 3_600_002.0, "999 hours in")]
    mask = sync.activity_mask(absurd, sync.COARSE_BIN_MS)
    ceiling = int(sync.MAX_TIMELINE_SECONDS / (sync.COARSE_BIN_MS / 1000.0))
    assert mask.bit_length() <= ceiling, \
        "the mask should be clamped to a plausible running time"
    assert mask.bit_length() < 1_000_000, "a 36 Mbit integer is not a subtitle"


def test_a_subtitle_with_an_absurd_timestamp_is_refused_quickly():
    """And it must be refused rather than obeyed, inside a sane time."""
    import time

    reference = make_cues(400)
    nonsense = [srt.Cue(1, 0.0, 2.0, "one real line")]
    nonsense += [srt.Cue(i + 2, 3_600_000.0 + i * 5, 3_600_002.0 + i * 5,
                         "line %d" % i) for i in range(30)]

    started = time.time()
    _offset, _scale, confidence = sync.fit(nonsense, reference)
    elapsed = time.time() - started

    assert confidence < sync.MIN_CONFIDENCE, "this is not a fit, it is nonsense"
    assert elapsed < 5.0, "took %.1fs - unbounded input reached the correlator" % elapsed


def test_the_popcount_fallback_is_correct_and_is_the_one_devices_use():
    """`int.bit_count()` is Python 3.10; Kodi 21 ships 3.8.

    So the fallback is not a fallback on any device this add-on targets - it
    is the inner loop of the whole module, run 3601 times per scale across
    nine scales. It was `bin(value).count("1")`, which builds a
    72,000-character string every time.
    """
    import random

    rng = random.Random(7)
    for _ in range(200):
        value = rng.getrandbits(rng.randint(0, 3000))
        assert sync._popcount(value) == bin(value).count("1")
    assert sync._popcount(0) == 0


def test_alignment_stays_in_budget_on_the_python_kodi_ships(monkeypatch):
    """The timing budget has to hold on 3.8, which is where it never did.

    Measured before the fallback was fixed: 0.78s for a matching pair and
    10.33s for an unrelated one, on a desktop four to eight times faster than
    the projector. An unrelated pair is the common case - it is what refusing
    a wrong subtitle looks like - and `consensus` runs six to eight of them
    for one decision.
    """
    import time

    monkeypatch.setattr(sync, "_popcount",
                        lambda v: sum(v.to_bytes((v.bit_length() + 7) // 8, "big")
                                      .translate(sync._BITS_IN_BYTE)))

    reference = make_cues(1800)
    unrelated = make_cues(1800, seed=99)

    started = time.time()
    _offset, _scale, confidence = sync.fit(unrelated, reference)
    elapsed = time.time() - started

    assert confidence < sync.MIN_CONFIDENCE
    assert elapsed < 3.0, \
        "%.1fs on the fallback; the device is several times slower" % elapsed

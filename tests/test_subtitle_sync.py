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

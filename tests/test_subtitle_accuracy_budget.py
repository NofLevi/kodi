"""A deterministic accuracy gate for the cheap subtitle-only strategy.

No video decoding, speech model, network, or AI is used. The corpus simulates
constant offsets, slight cue-boundary variation, and unrelated episodes, then
holds both recall and false-positive rate to explicit limits.
"""
import random
import time

from katan.subs import srt, sync


def timeline(seed, count=180, offset=0.0, jitter=0.0):
    rng = random.Random(seed)
    cues = []
    position = 8.0
    for index in range(count):
        position += rng.uniform(0.35, 5.5)
        change = rng.uniform(-jitter, jitter) if jitter else 0.0
        start = max(0.0, position + offset + change)
        end = start + rng.uniform(0.8, 3.2)
        cues.append(srt.Cue(index + 1, start, end, "line %d" % index))
        position += end - start
    return cues


def perturb(cues, offset, seed=99, jitter=0.04):
    rng = random.Random(seed)
    return [srt.Cue(cue.index, max(0.0, cue.start + offset
                                   + rng.uniform(-jitter, jitter)),
                    max(0.1, cue.end + offset
                        + rng.uniform(-jitter, jitter)), cue.text)
            for cue in cues]


def test_subtitle_only_alignment_separates_matching_and_wrong_episodes():
    reference = timeline(11)
    matching = [perturb(reference, offset, seed=index)
                for index, offset in enumerate(
                    (-120.0, -17.0, 0.0, 8.5, 74.0, 150.0))]
    unrelated = [timeline(seed, offset=(seed - 30) * 2.0)
                 for seed in range(30, 36)]

    true_scores = [sync.fit(candidate, reference)[2] for candidate in matching]
    false_scores = [sync.fit(candidate, reference)[2] for candidate in unrelated]

    assert min(true_scores) >= sync.MIN_CONFIDENCE
    assert max(false_scores) < sync.MIN_CONFIDENCE



def test_cross_language_cue_segmentation_still_aligns():
    """Translations often merge two captions; timing evidence must survive."""
    reference = timeline(41, count=180)
    translated = []
    for index in range(0, len(reference), 2):
        pair = reference[index:index + 2]
        translated.append(srt.Cue(len(translated) + 1,
                                  pair[0].start + 12.0,
                                  pair[-1].end + 12.0,
                                  "translated"))

    offset, _scale, confidence = sync.fit(translated, reference)
    assert confidence >= sync.MIN_CONFIDENCE
    assert abs(offset + 12.0) < 0.2


def test_subtitle_only_accuracy_gate_fits_weak_hardware_budget():
    reference = timeline(21, count=240)
    candidates = [timeline(21, count=240, offset=offset)
                  for offset in (-90.0, -15.0, 11.0, 125.0)]

    started = time.monotonic()
    for candidate in candidates:
        sync.fit(candidate, reference)
    elapsed = time.monotonic() - started

    assert elapsed < 2.5, "four full fits took %.2fs" % elapsed

"""Choosing a subtitle when there is nothing trustworthy to check it against.

A hash match settles the question and is available 9% of the time, measured
over a survey. The other 91% is decided by how much a filename resembles the
release, and that predicts timing poorly - a score of 77 that was 176 seconds
out, a score of 62 that was perfect. Two subtitles from different uploaders
that agree on timing are almost certainly both right; the one disagreeing with
both is wrong, and that is knowable with no hash and no video.
"""
import pytest


def _cues(count=120, offset=0.0, step=2.0, text="line"):
    from katan.subs import srt
    return [srt.Cue(i + 1, offset + i * step, offset + i * step + 1.2,
                    "%s %d" % (text, i)) for i in range(count)]


# --------------------------------------------------------------------------
# when it is worth spending a second download
# --------------------------------------------------------------------------

def test_a_trustworthy_reference_makes_it_unnecessary(settings_module):
    from katan.subs import consensus
    assert not consensus.wanted(55, has_reference=True)


def test_a_decisive_name_makes_it_unnecessary(settings_module):
    """An identical release name or a verified hash needs no second opinion."""
    from katan.subs import consensus
    assert not consensus.wanted(100, has_reference=False)
    assert not consensus.wanted(95, has_reference=False)


def test_a_weak_score_is_exactly_the_case_for_it(settings_module):
    from katan.subs import consensus
    assert consensus.wanted(62, has_reference=False)
    assert consensus.wanted(40, has_reference=False)


def test_it_can_be_switched_off(settings_module):
    from katan.subs import consensus
    settings_module.set("subs.consensus", "false")
    assert not consensus.wanted(62, has_reference=False)


def test_a_low_memory_device_keeps_the_minimum_majority_budget(settings_module):
    """Three tiny files buy a real majority without audio decoding or AI."""
    from katan.subs import consensus

    settings_module.set("device.profile", "low_memory")
    assert consensus.budget() == 3
    settings_module.set("device.profile", "balanced")
    assert consensus.budget() == 3


# --------------------------------------------------------------------------
# picking which ones to ask
# --------------------------------------------------------------------------

def test_the_same_release_twice_is_one_opinion():
    from katan.subs import consensus

    rows = [
        {"provider": "wizdom", "language": "he", "release": "Silo.S01E01-PSA"},
        {"provider": "opensubtitles_rest", "language": "he",
         "release": "silo.s01e01-psa"},
        {"provider": "bsplayer", "language": "he", "release": "Other.Release"},
    ]
    picked = consensus.distinct(rows, "he", 3)
    names = [p["release"] for p in picked]
    assert "Other.Release" in names
    assert len(picked) == 2, names


def test_another_language_is_not_a_second_opinion():
    from katan.subs import consensus

    rows = [{"provider": "a", "language": "he", "release": "one"},
            {"provider": "b", "language": "en", "release": "two"}]
    assert len(consensus.distinct(rows, "he", 3)) == 1


# --------------------------------------------------------------------------
# the judgement
# --------------------------------------------------------------------------


@pytest.mark.parametrize("ratio", [25.0 / 23.976,
                                    24000.0 / 23976.0,
                                    30000.0 / 29970.0])
def test_framerate_conversion_is_not_timing_agreement(ratio):
    """Two cuts needing any known scale are not the same video timeline."""
    from katan.subs import consensus

    original = _cues(count=500)
    converted = [cue.shifted(0.0, ratio) for cue in original]
    agreed, confidence, _offset = consensus.agree(original, converted)
    assert confidence >= consensus.AGREE_CONFIDENCE
    assert agreed is False


def test_two_that_agree_beat_one_that_does_not():
    """The case this exists for: the top-scored subtitle is the odd one out."""
    from katan.subs import consensus

    top = ({"provider": "top", "score": 77}, _cues(offset=176.0))
    a = ({"provider": "a", "score": 62}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 58}, _cues(offset=0.3))

    chosen, cues, report = consensus.choose([top, a, b])
    assert chosen["provider"] in ("a", "b"), report
    assert report["supported"] >= 1
    assert cues


def test_the_better_scored_of_an_agreeing_pair_wins():
    from katan.subs import consensus

    a = ({"provider": "a", "score": 70}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 55}, _cues(offset=0.2))
    chosen, _cues_out, _report = consensus.choose([a, b])
    assert chosen["provider"] == "a"


def test_when_nobody_agrees_the_top_score_still_plays():
    """A tie-breaker, never a gate. Something beats nothing."""
    from katan.subs import consensus

    a = ({"provider": "a", "score": 70}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 60}, _cues(offset=140.0))
    chosen, cues, report = consensus.choose([a, b])
    assert chosen["provider"] == "a"
    assert cues
    assert report["supported"] == 0
    assert "agreed" in report["reason"]


def test_one_candidate_is_not_a_consensus():
    from katan.subs import consensus

    only = ({"provider": "a", "score": 70}, _cues())
    chosen, cues, report = consensus.choose([only])
    assert chosen["provider"] == "a" and cues
    assert report["supported"] == 0



def test_nothing_downloaded_is_reported_rather_than_crashed():
    from katan.subs import consensus

    chosen, cues, report = consensus.choose([({"provider": "a"}, [])])
    assert chosen is None and cues == []
    assert report["reason"] == "nothing downloaded"



def test_verification_budget_buys_independent_languages_not_duplicates():
    from katan.subs import consensus

    rows = [
        {"provider": "wizdom", "language": "he", "score": 78, "release": "A"},
        {"provider": "ktuvit", "language": "he", "score": 77, "release": "B"},
        {"provider": "open", "language": "en", "score": 70, "release": "C"},
        {"provider": "subdl", "language": "es", "score": 63, "release": "D"},
    ]

    picked = consensus.verification_candidates(rows, "he", 3)
    assert [entry["language"] for entry in picked] == ["he", "en", "es"]


def test_verification_candidates_never_exceed_the_device_budget():
    from katan.subs import consensus

    rows = [{"provider": str(i), "language": language, "score": 80 - i,
             "release": str(i)}
            for i, language in enumerate(("he", "en", "es", "fr", "de"))]
    assert len(consensus.verification_candidates(rows, "he", 2)) <= 2


def test_two_other_languages_can_prove_a_timing_reference():
    """Timing is language-independent; English and Spanish can verify a cut."""
    from katan.subs import consensus

    fetched = [
        ({"provider": "hebrew", "language": "he", "score": 75},
         _cues(offset=90.0, text="hebrew")),
        ({"provider": "english", "language": "en", "score": 68},
         _cues(offset=0.0, text="english")),
        ({"provider": "spanish", "language": "es", "score": 61},
         _cues(offset=0.2, text="spanish")),
    ]

    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate["language"] in ("en", "es")
    assert cues
    assert report["supported"] == 1



def test_two_languages_from_one_provider_are_not_independent_proof():
    from katan.subs import consensus

    fetched = [
        ({"provider": "one-archive", "language": "en", "score": 70}, _cues()),
        ({"provider": "one-archive", "language": "es", "score": 68}, _cues()),
    ]
    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0



def test_same_catalogue_different_uploaders_are_independent():
    from katan.subs import consensus

    fetched = [
        ({"provider": "open", "uploader": "alice", "language": "en", "score": 70},
         _cues()),
        ({"provider": "open", "uploader": "bob", "language": "es", "score": 68},
         _cues(offset=0.2)),
    ]
    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is not None and cues
    assert report["supported"] == 1


def test_verification_selection_skips_a_correlated_upload_for_an_independent_one():
    from katan.subs import consensus

    rows = [
        {"provider": "wizdom", "language": "he", "score": 75},
        {"provider": "open", "uploader": "same", "language": "en", "score": 70},
        {"provider": "open", "uploader": "same", "language": "es", "score": 69},
        {"provider": "open", "uploader": "other", "language": "es", "score": 68},
    ]
    picked = consensus.verification_candidates(rows, "he", 3)
    assert [candidate.get("uploader") for candidate in picked[1:]] == ["same", "other"]


def test_one_other_language_is_not_proof_of_the_video_timeline():
    from katan.subs import consensus

    fetched = [
        ({"provider": "hebrew", "language": "he", "score": 75}, _cues()),
        ({"provider": "english", "language": "en", "score": 68}, _cues()),
    ]

    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0

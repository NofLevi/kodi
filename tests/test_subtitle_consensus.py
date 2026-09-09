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


def test_a_low_memory_device_fetches_fewer(settings_module):
    """This runs on a projector with a gigabyte shared with Android."""
    from katan.subs import consensus

    settings_module.set("device.profile", "low_memory")
    assert consensus.budget() == consensus.LOW_MEMORY_CANDIDATES
    settings_module.set("device.profile", "balanced")
    assert consensus.budget() == consensus.MAX_CANDIDATES


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

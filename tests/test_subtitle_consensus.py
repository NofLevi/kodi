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
    from pinky.subs import srt
    return [srt.Cue(i + 1, offset + i * step, offset + i * step + 1.2,
                    "%s %d" % (text, i)) for i in range(count)]


# --------------------------------------------------------------------------
# when it is worth spending a second download
# --------------------------------------------------------------------------

def test_a_trustworthy_reference_makes_it_unnecessary(settings_module):
    from pinky.subs import consensus
    assert not consensus.wanted(55, has_reference=True)


def test_only_an_identity_match_makes_it_unnecessary(settings_module):
    """An identical release name or a verified hash needs no second opinion.

    Nothing else does. `matcher.rate` returns exactly 100 for those two and
    caps everything additive at 99, so 100 is the whole of "we know which
    file this is". 95 is an accumulation - "group, source, resolution" - and
    the module docstring is a list of high-scoring names that were minutes
    out of time.
    """
    from pinky.subs import consensus, matcher
    assert not consensus.wanted(100, has_reference=False)
    assert consensus.wanted(95, has_reference=False),         "a strong name is not an identity"
    assert consensus.wanted(matcher.ADDITIVE_CEILING, has_reference=False),         "the best possible accumulation is still not an identity"


def test_nothing_additive_can_reach_the_decisive_score():
    """The two constants have to stay in step or 100 stops meaning identity."""
    from pinky.subs import consensus, matcher
    assert matcher.ADDITIVE_CEILING < consensus.DECISIVE_SCORE
    assert matcher.WEIGHT_HASH >= consensus.DECISIVE_SCORE
    assert matcher.WEIGHT_EXACT_NAME >= consensus.DECISIVE_SCORE


def test_a_weak_score_is_exactly_the_case_for_it(settings_module):
    from pinky.subs import consensus
    assert consensus.wanted(62, has_reference=False)
    assert consensus.wanted(40, has_reference=False)


def test_it_can_be_switched_off(settings_module):
    from pinky.subs import consensus
    settings_module.set("subs.consensus", "false")
    assert not consensus.wanted(62, has_reference=False)


def test_a_low_memory_device_keeps_the_minimum_majority_budget(settings_module):
    """Three tiny files buy a real majority without audio decoding or AI."""
    from pinky.subs import consensus

    settings_module.set("device.profile", "low_memory")
    assert consensus.budget() == 3
    settings_module.set("device.profile", "balanced")
    assert consensus.budget() == 3


# --------------------------------------------------------------------------
# picking which ones to ask
# --------------------------------------------------------------------------

def test_the_same_release_twice_is_one_opinion():
    from pinky.subs import consensus

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
    from pinky.subs import consensus

    rows = [{"provider": "a", "language": "he", "release": "one"},
            {"provider": "b", "language": "en", "release": "two"}]
    assert len(consensus.distinct(rows, "he", 3)) == 1


# --------------------------------------------------------------------------
# the judgement
# --------------------------------------------------------------------------


def test_truncated_timeline_is_not_independent_consensus():
    from pinky.subs import consensus
    full = _cues(count=400)
    excerpt = full[:30]
    agreed, confidence, _offset = consensus.agree(excerpt, full)
    assert agreed is False
    assert confidence < consensus.AGREE_CONFIDENCE


@pytest.mark.parametrize("ratio", [25.0 / 23.976,
                                    24000.0 / 23976.0,
                                    30000.0 / 29970.0])
def test_framerate_conversion_is_not_identical_timeline_agreement(ratio):
    """Two cuts needing any known scale are not the same video timeline."""
    from pinky.subs import consensus

    original = _cues(count=500)
    converted = [cue.shifted(0.0, ratio) for cue in original]
    agreed, confidence, _offset = consensus.agree(original, converted)
    assert confidence >= consensus.AGREE_CONFIDENCE
    assert agreed is False


def test_distinct_does_not_spend_budget_on_mirrored_archives():
    from pinky.subs import consensus
    rows = [
        {"provider": "a", "language": "he", "release": "cut-a",
         "archive_fingerprint": "same"},
        {"provider": "b", "language": "he", "release": "cut-b",
         "archive_fingerprint": "same"},
        {"provider": "c", "language": "he", "release": "cut-c",
         "archive_fingerprint": "other"},
    ]
    assert [row["provider"] for row in consensus.distinct(rows, "he", 2)] == [
        "a", "c"]


def test_distinct_fills_budget_with_independent_same_provider_uploads():
    from pinky.subs import consensus
    rows = [
        {"provider": "one", "language": "he", "release": "cut-a",
         "uploader": "alice"},
        {"provider": "one", "language": "he", "release": "cut-b",
         "uploader": "bob"},
        {"provider": "one", "language": "he", "release": "cut-c",
         "uploader": "carol"},
    ]
    assert [row["release"] for row in consensus.distinct(rows, "he", 3)] == [
        "cut-a", "cut-b", "cut-c"]


def test_two_that_agree_beat_one_that_does_not():
    """The case this exists for: the top-scored subtitle is the odd one out."""
    from pinky.subs import consensus

    top = ({"provider": "top", "score": 77}, _cues(offset=176.0))
    a = ({"provider": "a", "score": 62}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 58}, _cues(offset=0.3))

    chosen, cues, report = consensus.choose([top, a, b])
    assert chosen["provider"] in ("a", "b"), report
    assert report["supported"] >= 1
    assert cues


def test_correlation_collapse_is_transitive_and_order_independent():
    from pinky.subs import consensus
    shared_timeline = _cues(offset=0.1, text="shared")
    rows = [
        ({"provider": "a", "language": "en", "score": 90,
          "archive_fingerprint": "archive"}, _cues(offset=0.0, text="a")),
        ({"provider": "b", "language": "es", "score": 80,
          "archive_fingerprint": "archive"}, shared_timeline),
        ({"provider": "c", "language": "fr", "score": 70,
          "archive_fingerprint": "other"}, shared_timeline),
    ]
    for ordered in (rows, list(reversed(rows)), [rows[1], rows[0], rows[2]]):
        collapsed = consensus._collapse_correlated(ordered)
        assert len(collapsed) == 1
        _candidate, _cues_out, report = consensus.timeline_reference(ordered, "he")
        assert report["supported"] == 0


def test_archive_mirror_group_contributes_only_one_vote():
    from pinky.subs import consensus
    fetched = [
        ({"provider": "a", "language": "he", "score": 80,
          "archive_fingerprint": "mirror"}, _cues(offset=0.0, text="a")),
        ({"provider": "b", "language": "he", "score": 75,
          "archive_fingerprint": "mirror"}, _cues(offset=0.1, text="b")),
        ({"provider": "c", "language": "he", "score": 70,
          "archive_fingerprint": "independent"}, _cues(offset=0.2, text="c")),
    ]
    chosen, _cues_out, report = consensus.choose(fetched)
    assert chosen["provider"] == "a"
    assert report["supported"] == 1


def test_cross_language_archive_mirror_group_contributes_only_one_vote():
    from pinky.subs import consensus
    fetched = [
        ({"provider": "a", "language": "en", "score": 80,
          "archive_fingerprint": "mirror"}, _cues(offset=0.0, text="english")),
        ({"provider": "b", "language": "es", "score": 75,
          "archive_fingerprint": "mirror"}, _cues(offset=0.1, text="spanish")),
        ({"provider": "c", "language": "fr", "score": 70,
          "archive_fingerprint": "independent"}, _cues(offset=0.2, text="french")),
    ]
    _chosen, _cues_out, report = consensus.timeline_reference(fetched, "he")
    assert report["supported"] == 1


def test_identical_same_language_timelines_are_not_independent_consensus():
    from pinky.subs import consensus

    fetched = [
        ({"provider": "catalogue-a", "language": "he", "score": 70},
         _cues(text="copy one")),
        ({"provider": "catalogue-b", "language": "he", "score": 68},
         _cues(text="copy two")),
    ]

    chosen, cues, report = consensus.choose(fetched)
    assert chosen["provider"] == "catalogue-a"
    assert cues
    assert report["supported"] == 0


def test_the_better_scored_of_an_agreeing_pair_wins():
    from pinky.subs import consensus

    a = ({"provider": "a", "score": 70}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 55}, _cues(offset=0.2))
    chosen, _cues_out, _report = consensus.choose([a, b])
    assert chosen["provider"] == "a"


def test_when_nobody_agrees_the_top_score_still_plays():
    """A tie-breaker, never a gate. Something beats nothing."""
    from pinky.subs import consensus

    a = ({"provider": "a", "score": 70}, _cues(offset=0.0))
    b = ({"provider": "b", "score": 60}, _cues(offset=140.0))
    chosen, cues, report = consensus.choose([a, b])
    assert chosen["provider"] == "a"
    assert cues
    assert report["supported"] == 0
    assert "agreed" in report["reason"]


def test_one_candidate_is_not_a_consensus():
    from pinky.subs import consensus

    only = ({"provider": "a", "score": 70}, _cues())
    chosen, cues, report = consensus.choose([only])
    assert chosen["provider"] == "a" and cues
    assert report["supported"] == 0



def test_nothing_downloaded_is_reported_rather_than_crashed():
    from pinky.subs import consensus

    chosen, cues, report = consensus.choose([({"provider": "a"}, [])])
    assert chosen is None and cues == []
    assert report["reason"] == "nothing downloaded"



def test_verification_budget_buys_independent_languages_not_duplicates():
    from pinky.subs import consensus

    rows = [
        {"provider": "wizdom", "language": "he", "score": 78, "release": "A"},
        {"provider": "ktuvit", "language": "he", "score": 77, "release": "B"},
        {"provider": "open", "language": "en", "score": 70, "release": "C"},
        {"provider": "subdl", "language": "es", "score": 63, "release": "D"},
    ]

    picked = consensus.verification_candidates(rows, "he", 3)
    assert [entry["language"] for entry in picked] == ["he", "en", "es"]


def test_verification_candidates_never_exceed_the_device_budget():
    from pinky.subs import consensus

    rows = [{"provider": str(i), "language": language, "score": 80 - i,
             "release": str(i)}
            for i, language in enumerate(("he", "en", "es", "fr", "de"))]
    assert len(consensus.verification_candidates(rows, "he", 2)) <= 2


def test_two_other_languages_can_prove_a_timing_reference():
    """Timing is language-independent; English and Spanish can verify a cut."""
    from pinky.subs import consensus

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
    from pinky.subs import consensus

    fetched = [
        ({"provider": "one-archive", "language": "en", "score": 70}, _cues()),
        ({"provider": "one-archive", "language": "es", "score": 68}, _cues()),
    ]
    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0



def test_identical_multilingual_timeline_is_one_correlated_source():
    from pinky.subs import consensus

    fetched = [
        ({"provider": "catalogue-a", "language": "en", "score": 70},
         _cues(text="English")),
        ({"provider": "catalogue-b", "language": "es", "score": 68},
         _cues(text="Spanish")),
    ]

    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0


def test_mirrored_archive_across_catalogues_is_not_independent_proof():
    from pinky.subs import consensus

    fetched = [
        ({"provider": "catalogue-a", "language": "en", "score": 70,
          "release": "Film.2024.1080p.WEB-DL-GRP",
          "archive_fingerprint": "shared-upload"}, _cues()),
        ({"provider": "catalogue-b", "language": "es", "score": 68,
          "release": "Film.2024.1080p.WEB-DL-GRP",
          "archive_fingerprint": "shared-upload"}, _cues(offset=0.2)),
    ]

    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0


def test_same_catalogue_different_uploaders_are_independent():
    from pinky.subs import consensus

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
    from pinky.subs import consensus

    rows = [
        {"provider": "wizdom", "language": "he", "score": 75},
        {"provider": "open", "uploader": "same", "language": "en", "score": 70},
        {"provider": "open", "uploader": "same", "language": "es", "score": 69},
        {"provider": "open", "uploader": "other", "language": "es", "score": 68},
    ]
    picked = consensus.verification_candidates(rows, "he", 3)
    assert [candidate.get("uploader") for candidate in picked[1:]] == ["same", "other"]


def test_one_other_language_is_not_proof_of_the_video_timeline():
    from pinky.subs import consensus

    fetched = [
        ({"provider": "hebrew", "language": "he", "score": 75}, _cues()),
        ({"provider": "english", "language": "en", "score": 68}, _cues()),
    ]

    candidate, cues, report = consensus.timeline_reference(fetched, "he")
    assert candidate is None and cues == []
    assert report["supported"] == 0


# --------------------------------------------------------------------------
# a ruler for the translation source, where no hash exists
# --------------------------------------------------------------------------


def _timeline(count, offset=0.0, step=3.0):
    from pinky.subs import srt
    return [srt.Cue(i + 1, offset + i * step, offset + i * step + 2.0, "line %d" % i)
            for i in range(count)]


class _Downloads(object):
    """A download budget that answers from a table and counts fetches."""

    def __init__(self, table):
        self.table = table
        self.fetched = []

    def fetch(self, candidate):
        self.fetched.append(candidate.get("release"))
        return self.table.get(candidate.get("release")) or []


def _candidate(release, language, reason="title", uploader=None):
    """One candidate. The uploader matters: `_independent` will not treat two
    files from one provider as two opinions unless different people put them
    there, which is the point of the whole exercise."""
    return {"release": release, "language": language, "reason": reason,
            "provider": "opensubtitles_rest",
            "uploader": uploader if uploader is not None else release}


def test_a_hash_still_wins_and_costs_nothing_extra():
    from pinky.subs import auto

    source = _candidate("src", "ar")
    hashed = _candidate("hashed", "en", reason="hash")
    other = _candidate("other", "pl")
    downloads = _Downloads({"hashed": _timeline(40), "other": _timeline(40)})

    assert auto.translation_reference([source, hashed, other], downloads,
                                      skip=source)
    assert downloads.fetched == ["hashed"], "no extra downloads when a hash exists"


def test_two_agreeing_languages_prove_a_timeline_without_a_hash():
    """This is the anime case: no hash exists and none ever will.

    What reaches the screen is a translation of an Arabic or English file, so
    the timing the viewer sees is that file's timing - and nothing used to
    look at it. Two independent languages that agree cannot both be wrong in
    the same way.
    """
    from pinky.subs import auto

    source = _candidate("src", "ar")
    # Near, not identical: two people typing the same episode land within a
    # fraction of a second of each other, and `_same_timeline` correctly
    # collapses byte-identical cues as one file wearing two names.
    downloads = _Downloads({"en-file": _timeline(60),
                            "pl-file": _timeline(60, offset=0.3)})

    reference = auto.translation_reference(
        [source, _candidate("en-file", "en"), _candidate("pl-file", "pl")],
        downloads, skip=source)
    assert reference, "two agreeing languages are a timeline"


def test_one_other_language_is_not_a_second_opinion():
    from pinky.subs import auto

    source = _candidate("src", "ar")
    downloads = _Downloads({"en-file": _timeline(60)})
    assert auto.translation_reference(
        [source, _candidate("en-file", "en")], downloads, skip=source) == []
    assert downloads.fetched == [], "nothing is fetched for a proof that cannot be made"


def test_two_uploads_in_one_language_are_not_two_opinions():
    """The point is independence, not count."""
    from pinky.subs import auto

    source = _candidate("src", "ar")
    downloads = _Downloads({"en-a": _timeline(60), "en-b": _timeline(60)})
    assert auto.translation_reference(
        [source, _candidate("en-a", "en"), _candidate("en-b", "en")],
        downloads, skip=source) == []


def test_the_source_language_is_never_its_own_ruler():
    from pinky.subs import auto

    source = _candidate("src", "ar")
    downloads = _Downloads({"ar-other": _timeline(60), "en-file": _timeline(60)})
    auto.translation_reference(
        [source, _candidate("ar-other", "ar"), _candidate("en-file", "en")],
        downloads, skip=source)
    assert "ar-other" not in downloads.fetched

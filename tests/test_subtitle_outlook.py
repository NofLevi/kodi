# -*- coding: utf-8 -*-
"""What the picker says about each source's subtitles.

For a Hebrew-speaking household this is at least as decisive as the picture:
a 4K remux with no Hebrew subtitle is worse than a 1080p WEB-DL with one, and
until now the picker said nothing about it at all.
"""
import pytest

from katan.subs import outlook


def source(title, info_hash="a" * 40, **extra):
    entry = {"title": title, "hash": info_hash, "provider": "torrentio",
             "quality": "1080p", "group": "", "languages": []}
    entry.update(extra)
    return entry


META = {"type": "movie", "title": "A Film", "year": 2020,
        "ids": {"imdb": "tt1", "tmdb": 1}}


# --------------------------------------------------------------------------
# what the release name claims
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "A.Film.2020.1080p.WEB-DL.HebSub-GRP.mkv",
    "A.Film.2020.1080p.WEBRip.HEBSUBS.x264.mkv",
    "A Film 2020 1080p heb.sub.mkv",
])
def test_a_release_that_says_it_has_hebrew_is_taken_at_its_word(name):
    assert outlook._claims_hebrew(name) is True


@pytest.mark.parametrize("name", [
    "A.Film.2020.1080p.WEB-DL.NoHebSub-GRP.mkv",
    "A.Film.2020.1080p.no.heb.subs.mkv",
])
def test_a_release_that_says_it_has_none_is_not_misread(name):
    """"NoHebSub" contains "hebsub", and matching on the substring alone
    turned a release announcing the absence of Hebrew into one promising it -
    the exact opposite of what it says."""
    assert outlook._claims_hebrew(name) is False


@pytest.mark.parametrize("name", [
    "A.Film.2020.1080p.MULTI.WEB-DL.mkv",
    "A Film 2020 DUAL 1080p.mkv",
    "A.Film.2020.1080p.WEB-DL.x264-GRP.mkv",
])
def test_multi_and_dual_are_not_a_hebrew_claim(name):
    """They describe audio far more often than subtitles, and a wrong
    promise here sends somebody to a film they cannot follow."""
    assert outlook._claims_hebrew(name) is False


# --------------------------------------------------------------------------
# scoring against what is actually available
# --------------------------------------------------------------------------


def _candidates(*names):
    return [{"name": name, "language": "he", "provider": "wizdom",
             "id": name} for name in names]


def test_an_exact_release_match_scores_far_above_a_stranger():
    same = outlook._for_one(
        META, source("A.Film.2020.1080p.WEB-DL.x264-GRP.mkv"),
        _candidates("A.Film.2020.1080p.WEB-DL.x264-GRP"))
    unrelated = outlook._for_one(
        META, source("A.Film.2020.2160p.BluRay.x265-OTHER.mkv"),
        _candidates("A.Film.2020.1080p.WEB-DL.x264-GRP"))

    assert same["kind"] == outlook.EXTERNAL
    assert same["score"] > unrelated["score"], (
        "the subtitle written for this exact release should score higher: "
        "%s against %s" % (same, unrelated))


def test_a_source_whose_name_promises_hebrew_is_marked_embedded():
    entry = outlook._for_one(META,
                             source("A.Film.2020.1080p.HebSub-GRP.mkv"),
                             _candidates("something else"))
    assert entry["kind"] == outlook.EMBEDDED
    assert entry["score"] == 0, \
        "somebody else's promise does not get a percentage of ours"


def test_a_hebrew_language_flag_counts_as_embedded():
    entry = outlook._for_one(META, source("A.Film.2020.mkv", languages=["he"]),
                             _candidates("x"))
    assert entry["kind"] == outlook.EMBEDDED


def test_no_candidates_at_all_says_so():
    entry = outlook._for_one(META, source("A.Film.2020.1080p.mkv"), [])
    assert entry["kind"] == outlook.NONE
    assert entry["score"] == 0


def test_a_score_is_a_percentage():
    entry = outlook._for_one(
        META, source("A.Film.2020.1080p.WEB-DL.x264-GRP.mkv"),
        _candidates("A.Film.2020.1080p.WEB-DL.x264-GRP"))
    assert 1 <= entry["score"] <= 100


def test_an_exact_match_is_a_hundred_percent():
    """The matcher already answers 0..100, and its answer was being divided
    by the sum of the weights to "convert" it - so the ceiling became
    unreachable. A subtitle with the identical release name is a certainty
    and scored 100; the picker showed 72%, and one matched on group, source
    and resolution scored 72 and showed 52%. That is what was on screen."""
    entry = outlook._for_one(
        META, source("A.Film.2020.1080p.WEB-DL.x264-GRP.mkv"),
        _candidates("A.Film.2020.1080p.WEB-DL.x264-GRP"))
    assert entry["score"] == 100


def test_the_matchers_score_is_not_rescaled():
    """Whatever the matcher says, that is the number shown."""
    from katan.subs import matcher

    for points in (12, 33, 72, 100):
        assert outlook._as_percent(points) == points


def test_one_candidate_is_scored_freshly_for_every_source():
    """score_candidate writes its answer onto the candidate it is given, so
    the same candidate reused across sources would carry the previous
    source's score into the next one."""
    candidates = _candidates("A.Film.2020.1080p.WEB-DL.x264-GRP")
    first = outlook._for_one(
        META, source("A.Film.2020.1080p.WEB-DL.x264-GRP.mkv"), candidates)
    second = outlook._for_one(
        META, source("Totally.Different.2019.480p.CAM.mkv"), candidates)
    assert first["score"] != second["score"]
    assert "score" not in candidates[0], \
        "the shared candidate must not be written to"


# --------------------------------------------------------------------------
# the whole lookup
# --------------------------------------------------------------------------


def test_every_source_gets_an_answer(monkeypatch):
    from katan.subs import auto

    monkeypatch.setattr(auto, "search_candidates",
                        lambda meta, languages, **kw: _candidates("A.Film"))
    sources = [source("A.Film.2020.1080p.mkv", "a" * 40),
               source("A.Film.2020.720p.mkv", "b" * 40)]
    found = outlook.for_sources(META, sources)
    assert set(found) == {"a" * 40, "b" * 40}


def test_a_subtitle_search_that_fails_leaves_the_picker_alone(monkeypatch):
    """This decorates a list that is already useful. It must never be the
    reason the list does not appear."""
    from katan.subs import auto

    def explode(meta, languages, **kw):
        raise RuntimeError("the provider went away")

    monkeypatch.setattr(auto, "search_candidates", explode)
    found = outlook.for_sources(META, [source("A.Film.2020.1080p.mkv")])
    assert found, "every source should still get an entry"
    assert all(e["kind"] == outlook.NONE for e in found.values())


def test_the_answer_is_remembered(monkeypatch):
    """One search covers every source, and covers the next open too."""
    from katan.subs import auto

    calls = []
    monkeypatch.setattr(auto, "search_candidates",
                        lambda meta, languages, **kw:
                            calls.append(1) or _candidates("A.Film"))
    sources = [source("A.Film.2020.1080p.mkv")]
    outlook.for_sources(META, sources)
    outlook.for_sources(META, sources)
    assert len(calls) == 1


def test_no_sources_asks_nothing(monkeypatch):
    from katan.subs import auto

    monkeypatch.setattr(auto, "search_candidates",
                        lambda *a, **k: pytest.fail("should not have asked"))
    assert outlook.for_sources(META, []) == {}

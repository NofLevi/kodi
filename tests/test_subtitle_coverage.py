# -*- coding: utf-8 -*-
"""A subtitle has to reach far enough into the film to be for it.

The check existed only in tools/survey_subtitles.py, as a line in a report:
"covers under 60% of runtime". The playback path had nothing of the kind, so
a subtitle that stopped two thirds of the way through - a shorter cut, one
part of a split file, an incomplete upload - was shown whenever its filename
scored well enough. And the report's own line had never counted anything,
because the runtime it read was always 0.
"""
import pytest

from katan.subs import auto, srt


MOVIE = {
    "type": "movie",
    "ids": {"imdb": "tt0111161", "tmdb": 278},
    "title": "The Shawshank Redemption",
    "year": 1994,
    "stream_url": "",
    "source": {"release": "Shawshank.1994.1080p.BluRay.x264-AMIABLE",
               "group": "amiable", "quality": "1080p"},
}

# Every cue is 2.5 seconds long and starts 4 seconds after the last, so
# `count` cues end at (count - 1) * 4 + 2.5 seconds.
RUNTIME = 50.0
FULL = 12        # last line at 46.5s: 93% of the runtime
SHORT = 6        # last line at 22.5s: 45% of the runtime


def cues(count):
    return [srt.Cue(i + 1, i * 4.0, i * 4.0 + 2.5, "line %d" % i)
            for i in range(count)]


def srt_bytes(count):
    return srt.dump(cues(count)).encode("utf-8")


def candidate(release, language="he", **kwargs):
    entry = {"provider": "opensubtitles", "language": language,
             "release": release, "download": release}
    entry.update(kwargs)
    return entry


class Player(object):
    """Only what the subtitle path asks a player for."""

    def __init__(self, total):
        self.total = total

    def getTotalTime(self):
        if isinstance(self.total, Exception):
            raise self.total
        return self.total


@pytest.fixture
def world(monkeypatch, settings_module):
    settings_module.set_many({
        "subs.languages": "he,en",
        "subs.threshold": "70",
        "subs.hash_match": "false",
        "subs.provider.wizdom": "true",
        "subs.provider.opensubtitles": "true",
        "subs.ai.enabled": "false",
    })
    state = {"candidates": [], "downloads": {}, "downloaded": []}

    def fake_search(meta, languages, video_hash="", **kwargs):
        return list(state["candidates"])

    def fake_download(cand, expect_language=None):
        state["downloaded"].append(cand.get("download"))
        data = state["downloads"].get(cand.get("download"), b"")
        parsed = srt.parse(srt.decode(data)) if data else []
        return srt.clean(parsed) if parsed else []

    monkeypatch.setattr(auto, "search_candidates", fake_search)
    monkeypatch.setattr(auto, "download_candidate", fake_download)
    return state


# --------------------------------------------------------------------------
# the rule itself
# --------------------------------------------------------------------------


def test_a_subtitle_that_stops_early_does_not_cover_the_film():
    assert auto.covers_runtime(cues(SHORT), RUNTIME) is False


def test_a_subtitle_that_reaches_the_end_covers_it():
    assert auto.covers_runtime(cues(FULL), RUNTIME) is True


def test_an_unknown_runtime_never_rejects_anything():
    """A live stream, or a player that has not learned the length yet. The
    check stands aside rather than guessing, because guessing wrong here
    means a viewer who had a subtitle loses it."""
    assert auto.covers_runtime(cues(SHORT), 0) is True


def test_the_players_length_wins_over_the_titles():
    """The player's figure is this file, cut and all; TMDB's is a rounded
    number for the title."""
    meta = dict(MOVIE, duration=9000)
    assert auto.runtime_of(Player(RUNTIME), meta) == RUNTIME


def test_the_title_runtime_is_the_fallback():
    meta = dict(MOVIE, duration=5400)
    assert auto.runtime_of(Player(0), meta) == 5400
    assert auto.runtime_of(Player(RuntimeError("stopped")), meta) == 5400
    assert auto.runtime_of(None, meta) == 5400


def test_nothing_known_is_zero():
    assert auto.runtime_of(None, MOVIE) == 0


# --------------------------------------------------------------------------
# in the playback path
# --------------------------------------------------------------------------


def test_a_short_subtitle_is_rejected_even_with_no_reference():
    """No hash reference is the usual case - it was 87% of titles - and it
    was exactly when nothing at all was checked."""
    report = {"reason": ""}
    budget = auto._DownloadBudget(3)
    kept, report = auto.verify_and_sync(cues(SHORT), {}, ["he", "en"],
                                        report, budget, RUNTIME)
    assert kept == []
    assert report["short"] is True
    assert "45%" in report["reason"]


def test_a_full_subtitle_passes_through_untouched():
    report = {"reason": ""}
    budget = auto._DownloadBudget(3)
    kept, report = auto.verify_and_sync(cues(FULL), {}, ["he", "en"],
                                        report, budget, RUNTIME)
    assert len(kept) == FULL
    assert not report.get("short")


def test_the_next_candidate_is_tried_when_the_best_is_too_short(world):
    """The identical release name scores 100 and is still the wrong file if
    it stops halfway. The next Hebrew subtitle that clears the threshold is
    downloaded instead, through the same fallback a disproved hash uses."""
    best = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    next_best = "Shawshank.1994.1080p.BluRay-OTHER"
    world["candidates"] = [candidate(best), candidate(next_best)]
    world["downloads"][best] = srt_bytes(SHORT)
    world["downloads"][next_best] = srt_bytes(FULL)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"],
                                         player=Player(RUNTIME))

    assert path, report
    assert len(srt.read(path)) == FULL, "the short subtitle was the one kept"
    assert world["downloaded"][:2] == [best, next_best]


def test_without_a_runtime_the_short_subtitle_is_still_used(world):
    """The same short file, with nobody knowing how long the film is. It is
    kept, because refusing it would be refusing on a guess."""
    best = "Shawshank.1994.1080p.BluRay.x264-AMIABLE"
    world["candidates"] = [candidate(best)]
    world["downloads"][best] = srt_bytes(SHORT)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"], player=None)

    assert path, report
    assert len(srt.read(path)) == SHORT


def test_playback_metadata_carries_the_runtime(monkeypatch):
    """The item had the runtime all along; `build_meta` never lifted it to
    where the subtitle path and the survey look."""
    from katan import play
    from katan.meta import tmdb

    detail = {"ids": {"tmdb": 278, "imdb": "tt0111161"},
              "title": "The Shawshank Redemption", "year": 1994,
              "duration": 142 * 60, "extra": {}}
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: detail)

    meta = play.build_meta({"type": "movie", "tmdb": "278"})
    assert meta["duration"] == 142 * 60

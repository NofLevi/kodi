# -*- coding: utf-8 -*-
"""Where the intro and credits are, and skipping the intro."""
import xbmc
import xbmcgui

from katan import kodi, player, skip

# IntroDB's answer for Breaking Bad 1x02, as fetched.
BREAKING_BAD_1X02 = {
    "imdb_id": "tt0903747", "season": 1, "episode": 2,
    "intro": {"start_sec": 314.5, "end_sec": 331, "confidence": 1,
              "submission_count": 5},
    "recap": None,
    "outro": {"start_sec": 2843, "end_sec": 2901, "confidence": 1,
              "submission_count": 1},
}

EPISODE = {"type": "episode", "ids": {"imdb": "tt0903747", "tmdb": 1396},
           "season": 1, "episode": 2, "title": "Breaking Bad"}


def answering(monkeypatch, payload):
    asked = []

    def fetch(imdb, season, episode):
        asked.append((imdb, season, episode))
        return payload

    monkeypatch.setattr(skip, "_fetch", fetch)
    return asked


# --------------------------------------------------------------------------
# the lookup
# --------------------------------------------------------------------------


def test_introdb_outro_is_our_credits(monkeypatch):
    answering(monkeypatch, BREAKING_BAD_1X02)
    assert skip.segments(EPISODE) == {"intro": [314.5, 331.0],
                                      "credits": [2843.0, 2901.0]}


def test_it_asks_with_the_series_imdb_id(monkeypatch):
    asked = answering(monkeypatch, BREAKING_BAD_1X02)
    skip.segments(EPISODE)
    assert asked == [("tt0903747", 1, 2)]


def test_films_and_episodes_without_an_imdb_id_are_not_asked(monkeypatch):
    asked = answering(monkeypatch, BREAKING_BAD_1X02)
    assert skip.segments({"type": "movie", "ids": {"imdb": "tt0111161"}}) == {}
    assert skip.segments(dict(EPISODE, ids={"kitsu": 49444})) == {}
    assert asked == []


def test_an_answer_is_kept(monkeypatch):
    asked = answering(monkeypatch, BREAKING_BAD_1X02)
    skip.segments(EPISODE)
    skip.segments(EPISODE)
    assert len(asked) == 1


def test_an_empty_answer_is_kept_too(monkeypatch):
    asked = answering(monkeypatch, {"intro": None, "recap": None, "outro": None})
    assert skip.segments(EPISODE) == {}
    skip.segments(EPISODE)
    assert len(asked) == 1


def test_a_failed_request_is_asked_again(monkeypatch):
    asked = answering(monkeypatch, None)
    skip.segments(EPISODE)
    skip.segments(EPISODE)
    assert len(asked) == 2


def test_an_unsure_segment_is_left_out(monkeypatch):
    payload = dict(BREAKING_BAD_1X02)
    payload["intro"] = dict(payload["intro"], confidence=0.2)
    answering(monkeypatch, payload)
    assert "intro" not in skip.segments(EPISODE)


# --------------------------------------------------------------------------
# the sanity rules
# --------------------------------------------------------------------------


def test_timings_that_fit_the_file_are_used():
    found = {"intro": [314.5, 331.0], "credits": [2843.0, 2901.0]}
    assert skip.usable(found, 2950) == {"intro": (314.5, 331.0),
                                        "credits": (2843.0, 2901.0)}


def test_an_intro_longer_than_any_opening_is_dropped():
    assert skip.usable({"intro": [60.0, 600.0]}, 2950) == {}


def test_an_intro_past_half_time_is_another_cut():
    assert skip.usable({"intro": [1600.0, 1650.0]}, 2950) == {}


def test_credits_before_half_time_are_another_cut():
    assert skip.usable({"credits": [900.0, 960.0]}, 2950) == {}


def test_credits_may_run_to_the_end():
    assert skip.usable({"credits": [2843.0, None]}, 2950) == {
        "credits": (2843.0, None)}


# --------------------------------------------------------------------------
# skipping
# --------------------------------------------------------------------------


def playing(monkeypatch, position, auto=True):
    from katan import settings
    settings.set("ui.auto_skip", "true" if auto else "false")
    monitor = player.KatanPlayer()
    monitor.meta = dict(EPISODE)
    monitor.total_time = 2950.0
    monitor._segments = {"intro": [314.5, 331.0], "credits": [2843.0, 2901.0]}
    monitor._intro_skipped = False
    monitor._bookmarked_at = 1e12           # keep the resume save out of it
    monkeypatch.setattr(monitor, "getTime", lambda: position)
    monkeypatch.setattr(monitor, "_maybe_prefetch", lambda: None)
    del xbmc.Player.SEEKS[:]
    del xbmcgui.NOTIFICATIONS[:]
    return monitor


def test_the_intro_is_skipped_when_asked(monkeypatch):
    monitor = playing(monkeypatch, 316.0)
    monitor.tick()
    assert xbmc.Player.SEEKS == [331.0]
    said = [message for _heading, message in xbmcgui.NOTIFICATIONS]
    assert kodi.localize(32529) in said


def test_nothing_is_skipped_unless_asked(monkeypatch):
    monitor = playing(monkeypatch, 316.0, auto=False)
    monitor.tick()
    assert xbmc.Player.SEEKS == []


def test_nothing_is_skipped_before_the_intro(monkeypatch):
    monitor = playing(monkeypatch, 120.0)
    monitor.tick()
    assert xbmc.Player.SEEKS == []


def test_seeking_back_into_the_intro_is_respected(monkeypatch):
    monitor = playing(monkeypatch, 316.0)
    monitor.tick()
    monitor.tick()
    assert xbmc.Player.SEEKS == [331.0], "the viewer went back to watch it"


def test_a_lookup_for_an_old_playback_is_dropped(monkeypatch):
    answering(monkeypatch, BREAKING_BAD_1X02)
    monitor = player.KatanPlayer()
    meta = dict(EPISODE)
    monitor.meta = meta
    monitor._segments = {}
    generation = monitor._playback_generation
    monitor._playback_generation += 1       # the next one started meanwhile
    monitor._segments_job(meta, generation)
    assert monitor._segments == {}


def test_a_lookup_for_this_playback_is_kept(monkeypatch):
    answering(monkeypatch, BREAKING_BAD_1X02)
    monitor = player.KatanPlayer()
    meta = dict(EPISODE)
    monitor.meta = meta
    monitor._segments = {}
    monitor._segments_job(meta, monitor._playback_generation)
    assert "intro" in monitor._segments

# -*- coding: utf-8 -*-
"""Resuming without Trakt.

Resume used to come from Trakt alone, so without a Trakt account every film
started from the beginning however far into it somebody had got.
"""
import io
import json

import pytest

from katan import bookmarks
from katan.meta import trakt_state

MOVIE = {"type": "movie", "title": "A Film", "ids": {"tmdb": 7}}
MOVIE_KEY = trakt_state.state_key("movie", {"tmdb": 7})


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------


def test_a_place_is_saved_and_cleared():
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    assert bookmarks.all_entries()[MOVIE_KEY]["position"] == 2040.0
    bookmarks.clear(MOVIE_KEY)
    assert MOVIE_KEY not in bookmarks.all_entries()


def test_the_oldest_places_go_first_past_the_limit(monkeypatch):
    monkeypatch.setattr(bookmarks, "LIMIT", 3)
    for number in range(5):
        monkeypatch.setattr(bookmarks.time, "time", lambda n=number: 1000 + n)
        bookmarks.save("movie:tmdb:%d" % number, 60.0, 7200.0)
    kept = bookmarks.all_entries()
    assert sorted(kept) == ["movie:tmdb:2", "movie:tmdb:3", "movie:tmdb:4"]


def test_a_corrupt_file_reads_as_nothing_rather_than_failing():
    """A box switched off at the wall mid-write leaves half a file."""
    with io.open(bookmarks.path(), "w", encoding="utf-8") as handle:
        handle.write('{"movie:tmdb:7": {"posi')
    assert bookmarks.all_entries() == {}


def test_nothing_but_times_is_written():
    """Only positions. No stream address - they are signed and short-lived,
    and a private link has no business on disk."""
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    text = io.open(bookmarks.path(), encoding="utf-8").read()
    assert "http" not in text
    assert json.loads(text)[MOVIE_KEY]["total"] == 7200.0


# --------------------------------------------------------------------------
# listings pick it up
# --------------------------------------------------------------------------


def test_without_trakt_a_listing_resumes_from_the_bookmark():
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    item = dict(MOVIE, resume={})
    trakt_state.annotate([item])
    assert item["resume"] == {"position": 2040.0, "total": 7200.0}


def test_trakt_wins_where_it_has_an_answer(monkeypatch):
    bookmarks.save(MOVIE_KEY, 60.0, 7200.0)
    monkeypatch.setattr(trakt_state, "enabled", lambda: True)
    monkeypatch.setattr(trakt_state, "_watched_map", lambda: {})
    monkeypatch.setattr(trakt_state, "_playback_map",
                        lambda: {MOVIE_KEY: {"progress": 50.0}})
    item = dict(MOVIE, duration=7200, resume={})
    trakt_state.annotate([item])
    assert item["resume"]["position"] == 3600.0


def test_an_episode_from_a_tmdb_list_finds_its_place():
    """Playback knows the show's id; TMDB's episode lists carry it as
    tmdb_show rather than show_ids, so the lookup has to read either."""
    key = trakt_state.state_key("episode", {"tmdb": 125988}, 1, 2)
    bookmarks.save(key, 900.0, 3000.0)
    episode = {"type": "episode", "season": 1, "episode": 2,
               "ids": {"tmdb": 2964687}, "extra": {"tmdb_show": 125988}}
    trakt_state.annotate([episode])
    assert episode.get("resume", {}).get("position") == 900.0


def test_marking_something_watched_forgets_its_place(monkeypatch):
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    trakt_state.mark_watched({"type": "movie", "tmdb": 7})
    assert MOVIE_KEY not in bookmarks.all_entries()


# --------------------------------------------------------------------------
# the player records it
# --------------------------------------------------------------------------


@pytest.fixture
def monitor(monkeypatch):
    from katan import player

    watcher = player.KatanPlayer()
    watcher.meta = dict(MOVIE)
    watcher.total_time = 0.0
    clock = {"now": 3600.0, "stopped": False}

    def get_time():
        if clock["stopped"]:
            raise RuntimeError("nothing is playing")
        return clock["now"]

    monkeypatch.setattr(watcher, "getTime", get_time)
    monkeypatch.setattr(watcher, "getTotalTime", lambda: 7200.0)
    monkeypatch.setattr(watcher, "_scrobble", lambda *a, **k: None)
    monkeypatch.setattr(watcher, "_remember_source", lambda *a, **k: None)
    watcher.clock = clock
    return watcher


def test_stopping_halfway_saves_the_place(monitor):
    monitor.tick()
    monitor.clock["stopped"] = True       # Kodi has stopped before telling us
    monitor.onPlayBackStopped()
    saved = bookmarks.all_entries().get(MOVIE_KEY)
    assert saved, "a stop halfway through left nothing to resume from"
    assert saved["position"] == pytest.approx(3600.0)


def test_finishing_forgets_the_place(monitor):
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    monitor.tick()
    monitor.onPlayBackEnded()
    assert MOVIE_KEY not in bookmarks.all_entries()


def test_stopping_in_the_credits_counts_as_finished(monitor):
    bookmarks.save(MOVIE_KEY, 2040.0, 7200.0)
    monitor.clock["now"] = 7000.0           # 97%
    monitor.tick()
    monitor.onPlayBackStopped()
    assert MOVIE_KEY not in bookmarks.all_entries()


def test_a_false_start_saves_nothing(monitor):
    monitor.clock["now"] = 30.0             # under 1%
    monitor.tick()
    monitor.onPlayBackStopped()
    assert MOVIE_KEY not in bookmarks.all_entries()

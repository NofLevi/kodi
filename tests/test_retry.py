# -*- coding: utf-8 -*-
"""When Kodi cannot open the stream autoplay chose, try the next source.

The fall-through to the next source only ran before playback, when a link
would not resolve or answer. Once Kodi had the URL, a stream its decoder could
not open ended on an error screen - while the next source in the list would
have played. On a 1 GB box that is the ordinary way for a film to fail.
"""
import json

import pytest
import xbmc
import xbmcgui

from katan import kodi, play, player
from katan.meta import tmdb


def source(title, info_hash, cached_by="torbox", **extra):
    entry = {"title": title, "hash": info_hash, "provider": "torrentio",
             "quality": "1080p", "size": 8 * 1024 ** 3, "seeders": 40,
             "cached": bool(cached_by), "cached_by": cached_by,
             "group": "GRP",
             "extra": {"meta": {"type": "movie", "season": None,
                                "episode": None, "absolute": None,
                                "title": "A Film"}}}
    entry.update(extra)
    return entry


@pytest.fixture
def film(monkeypatch, settings_module):
    settings_module.set("torbox.apikey", "a-key")
    settings_module.set("sources.autoplay", "true")
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": 278, "imdb": "tt0111161"}, "title": "A Film",
        "year": 1994, "art": {}, "original_title": "A Film"})
    monkeypatch.setattr(play, "_reachable", lambda url: True)
    del xbmc.Player.PLAYED[:]
    return settings_module


def handoff():
    raw = kodi.get_property(player.PLAYING_KEY)
    return json.loads(raw) if raw else None


# --------------------------------------------------------------------------
# what travels with the hand-off
# --------------------------------------------------------------------------


def test_the_next_sources_travel_with_the_handoff(film, monkeypatch):
    from katan.sources import aggregator

    sources = [source("Best", "a" * 40), source("Second", "b" * 40),
               source("Direct", "c" * 40,
                      url="https://cdn.example/x?token=SECRET"),
               source("Uncached", "d" * 40, cached_by=""),
               source("Third", "e" * 40), source("Fourth", "f" * 40)]
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: sources)
    monkeypatch.setattr(play, "_resolve", lambda s: "https://cdn.example/best")

    play.play(-1, {"type": "movie", "tmdb": "278"})

    carried = handoff()
    assert [f["title"] for f in carried["fallbacks"]] == ["Second", "Third"], \
        "a direct link and an uncached source cannot be re-opened safely"
    assert carried["_retry"] == 0


def test_the_handoff_carries_no_link_and_no_secret(film, monkeypatch):
    """The hand-off is a window property any add-on on the box can read, so
    it holds hashes and file identities - never a stream address."""
    from katan.sources import aggregator

    sources = [source("Best", "a" * 40), source("Second", "b" * 40),
               source("Direct", "c" * 40,
                      url="https://user:password@cdn.example/x?token=SECRET")]
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: sources)
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://cdn.example/best?token=SECRET")

    play.play(-1, {"type": "movie", "tmdb": "278"})

    text = json.dumps(handoff())
    for secret in ("http", "token", "SECRET", "password"):
        assert secret not in text, "%s reached the hand-off" % secret


def test_a_source_the_viewer_chose_carries_no_fallbacks(film, monkeypatch):
    """They asked for that release; playing another is worse than saying no."""
    from katan.sources import aggregator

    sources = [source("Best", "a" * 40), source("Second", "b" * 40)]
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: sources)
    monkeypatch.setattr(play, "_choose", lambda found, meta, force: found[0])
    monkeypatch.setattr(play, "_resolve", lambda s: "https://cdn.example/best")

    play.play(-1, {"type": "movie", "tmdb": "278"}, force_picker=True)
    assert "fallbacks" not in handoff()


def test_the_first_fallback_that_opens_is_the_one_used(film, monkeypatch):
    tried = []

    def resolve(candidate):
        tried.append(candidate["title"])
        return ("" if candidate["title"] == "Second"
                else "https://cdn.example/third")

    monkeypatch.setattr(play, "_resolve", resolve)
    carried = {"fallbacks": play._fallbacks_after(
        None, [source("Second", "b" * 40), source("Third", "e" * 40)])}

    candidate, url, remaining = play.resolve_fallback(carried)
    assert tried == ["Second", "Third"]
    assert candidate["title"] == "Third"
    assert url == "https://cdn.example/third"
    assert remaining == []


# --------------------------------------------------------------------------
# the service acts on it
# --------------------------------------------------------------------------


@pytest.fixture
def run_now(monkeypatch):
    """Workers run on the spot, so the test can see what they did."""

    class Immediate(object):
        def __init__(self, target=None, args=(), name=None):
            self.target, self.args, self.daemon = target, args, False

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(player.threading, "Thread", Immediate)


def failed_open(fallbacks, retry=0, age=0.0):
    """A hand-off Kodi has just failed to open, and the service watching."""
    player.set_now_playing({
        "type": "movie", "title": "A Film", "ids": {"tmdb": 278},
        "item": {"type": "movie", "title": "A Film", "ids": {}, "art": {}},
        "fallbacks": fallbacks, "_retry": retry,
        "stream_url": "https://cdn.example/first"})
    if age:
        stored = handoff()
        stored["_handoff_time"] -= age
        kodi.set_property(player.PLAYING_KEY, json.dumps(stored))
    del xbmc.Player.PLAYED[:]
    return player.KatanPlayer()


def _two_left():
    return play._fallbacks_after(None, [source("Second", "b" * 40),
                                        source("Third", "e" * 40)])


def test_a_stream_kodi_could_not_open_moves_on_to_the_next(film, monkeypatch,
                                                           run_now):
    monitor = failed_open(_two_left())
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://cdn.example/second")

    monitor.onPlayBackError()

    assert xbmc.Player.PLAYED, "nothing was played after the failed open"
    args, _kwargs = xbmc.Player.PLAYED[-1]
    assert args[0] == "https://cdn.example/second"
    carried = handoff()
    assert carried["_retry"] == 1
    assert [f["title"] for f in carried["fallbacks"]] == ["Third"]
    assert carried["source"]["release"] == "Second"


def test_a_stale_handoff_is_not_ours_to_act_on(film, monkeypatch, run_now):
    monitor = failed_open(_two_left(), age=player.HANDOFF_TTL + 5)
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://cdn.example/second")
    monitor.onPlayBackError()
    assert not xbmc.Player.PLAYED


def test_it_gives_up_after_two_fallbacks(film, monkeypatch, run_now):
    monitor = failed_open(_two_left(), retry=player.KatanPlayer.MAX_RETRIES)
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://cdn.example/second")
    monitor.onPlayBackError()
    assert not xbmc.Player.PLAYED


def test_an_error_after_the_stream_started_is_a_different_failure(
        film, monkeypatch, run_now):
    """A stream dying mid-film has its own questions - where to resume, and
    whether the next source is even the same cut."""
    monitor = failed_open(_two_left())
    monitor.meta = {"type": "movie", "title": "A Film"}   # AV had started
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://cdn.example/second")
    monitor.onPlayBackError()
    assert not xbmc.Player.PLAYED


def test_something_started_meanwhile_is_left_alone(film, monkeypatch, run_now):
    monitor = failed_open(_two_left())

    def resolve(candidate):
        monitor._playback_generation += 1       # another playback began
        return "https://cdn.example/second"

    monkeypatch.setattr(play, "_resolve", resolve)
    monitor.onPlayBackError()
    assert not xbmc.Player.PLAYED


def test_when_no_fallback_opens_it_says_so(film, monkeypatch, run_now):
    monitor = failed_open(_two_left())
    monkeypatch.setattr(play, "_resolve", lambda s: "")
    del xbmcgui.NOTIFICATIONS[:]

    monitor.onPlayBackError()

    assert not xbmc.Player.PLAYED
    said = [message for _heading, message in xbmcgui.NOTIFICATIONS]
    assert kodi.localize(32284) in said

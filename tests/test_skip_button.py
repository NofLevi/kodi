# -*- coding: utf-8 -*-
"""The Skip intro button over the episode."""
import xbmc

from katan import player
from katan.ui import skip_window

EPISODE = {"type": "episode", "ids": {"imdb": "tt0903747", "tmdb": 1396},
           "season": 1, "episode": 2, "title": "Breaking Bad"}


class Action(object):
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id

    def getButtonCode(self):
        return 0


def playing(monkeypatch, auto=False):
    from katan import settings
    settings.set("ui.auto_skip", "true" if auto else "false")
    monitor = player.KatanPlayer()
    monitor.meta = dict(EPISODE)
    monitor.total_time = 2950.0
    monitor._segments = {"intro": [314.5, 331.0], "credits": [2843.0, 2901.0]}
    monitor._intro_skipped = False
    monitor._skip_dismissed = False
    monitor._bookmarked_at = 1e12           # keep the resume save out of it
    monitor.at = 316.0
    monkeypatch.setattr(monitor, "getTime", lambda: monitor.at)
    monkeypatch.setattr(monitor, "_maybe_prefetch", lambda: None)
    del xbmc.Player.SEEKS[:]
    return monitor


def test_the_button_is_up_while_the_intro_plays(monkeypatch):
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    assert isinstance(button, skip_window.SkipButton)
    assert getattr(button, "shown", False), "shown, never doModal"


def test_it_goes_when_the_intro_ends(monkeypatch):
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    monitor.at = 400.0
    monitor.tick()
    assert button.closed
    assert monitor._skip_button is None


def test_ok_on_it_skips_the_intro(monkeypatch):
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    button.onClick(skip_window.BUTTON_SKIP)
    assert xbmc.Player.SEEKS == [331.0]
    assert button.closed
    monitor.at = 320.0                      # back into the intro on purpose
    monitor.tick()
    assert monitor._skip_button is None


def test_back_puts_it_away_for_the_episode(monkeypatch):
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    button.onAction(Action(92))
    assert button.closed
    monitor.tick()
    assert monitor._skip_button is None
    assert xbmc.Player.SEEKS == []


def test_ok_itself_does_not_put_it_away(monkeypatch):
    """OK reaches onAction as well as onClick; only onClick may act on it."""
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    button.onAction(Action(skip_window.ACTION_SELECT_ITEM))
    assert not button.closed


def test_no_button_when_skipping_is_automatic(monkeypatch):
    monitor = playing(monkeypatch, auto=True)
    monitor.tick()
    assert monitor._skip_button is None
    assert xbmc.Player.SEEKS == [331.0]


def test_stopping_takes_the_button_down(monkeypatch):
    monitor = playing(monkeypatch)
    monitor.tick()
    button = monitor._skip_button
    monitor.onPlayBackStopped()
    assert button.closed

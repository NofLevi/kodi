# -*- coding: utf-8 -*-
"""Katan's own next-episode card, for a box without Up Next."""
import pytest
import xbmc

from katan import player, router, upnext
from katan.ui import nextup_window

EPISODE = {"type": "episode", "ids": {"imdb": "tt0903747", "tmdb": 1396},
           "season": 1, "episode": 2, "title": "Breaking Bad"}
NEXT = {"type": "episode", "ids": {"tmdb": 1396}, "title": "Breaking Bad",
        "show_title": "Breaking Bad", "season": 1, "episode": 3,
        "episode_title": "...And the Bag's in the River", "art": {}}
NEXT_URL = router.url_for("episode", tmdb=1396, season=1, episode=3)


class Action(object):
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id

    def getButtonCode(self):
        return 0


@pytest.fixture
def no_upnext(monkeypatch):
    monkeypatch.setattr(upnext, "installed", lambda: False)
    monkeypatch.setattr(upnext, "next_episode", lambda meta: dict(NEXT))


def watching(monkeypatch, at, credits=True):
    monitor = player.KatanPlayer()
    monitor.meta = dict(EPISODE)
    monitor.total_time = 2950.0
    monitor._segments = ({"credits": [2843.0, None]} if credits else {})
    monitor._bookmarked_at = 1e12           # keep the resume save out of it
    monitor.at = at
    monkeypatch.setattr(monitor, "getTime", lambda: monitor.at)
    monkeypatch.setattr(monitor, "_maybe_prefetch", lambda: None)
    del xbmc.Player.PLAYED[:]
    return monitor


def test_the_card_comes_up_with_the_credits(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    card = monitor._next_card
    assert isinstance(card, nextup_window.NextUpCard)
    assert getattr(card, "shown", False), "shown, never doModal"
    assert card.getProperty("katan.next.number") == "S01E03"
    assert card.autoplay


def test_not_before_the_credits(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2700.0)
    monitor.tick()
    assert monitor._next_card is None


def test_the_countdown_starts_the_next_episode(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    card = monitor._next_card
    card.opened_at -= player.KatanPlayer.NEXT_COUNTDOWN + 1
    monitor.tick()
    assert xbmc.Player.PLAYED[-1][0][0] == NEXT_URL
    assert card.closed
    assert monitor.meta is None, "this one was finished as watched"


def test_play_now_does_not_wait(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    monitor._next_card.onClick(nextup_window.BUTTON_PLAY)
    assert xbmc.Player.PLAYED[-1][0][0] == NEXT_URL


def test_back_puts_it_away_and_nothing_starts(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    card = monitor._next_card
    card.onAction(Action(nextup_window.ACTION_NAV_BACK))
    assert card.closed
    card.opened_at -= 60
    monitor.tick()
    monitor.onPlayBackEnded()
    assert not xbmc.Player.PLAYED
    assert monitor._next_card is None


def test_the_arrows_do_not_put_it_away(monkeypatch, no_upnext):
    """They move between Play now and Close."""
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    monitor._next_card.onAction(Action(2))
    assert not monitor._next_card.closed


def test_without_credits_it_waits_for_the_end(monkeypatch, no_upnext):
    monitor = watching(monkeypatch, 2935.0, credits=False)
    monitor.tick()
    card = monitor._next_card
    assert card is not None and not card.autoplay
    card.opened_at -= 60
    monitor.tick()
    assert not xbmc.Player.PLAYED, "no countdown without knowing the credits"
    monitor.onPlayBackEnded()
    assert xbmc.Player.PLAYED[-1][0][0] == NEXT_URL


def test_a_film_that_ends_without_the_card_starts_nothing(monkeypatch,
                                                          no_upnext):
    monitor = watching(monkeypatch, 1000.0, credits=False)
    monitor.tick()
    monitor.onPlayBackEnded()
    assert not xbmc.Player.PLAYED


def test_up_next_draws_its_own(monkeypatch):
    monkeypatch.setattr(upnext, "installed", lambda: True)

    def must_not_ask(meta):
        raise AssertionError("two cards would both start the next episode")

    monkeypatch.setattr(upnext, "next_episode", must_not_ask)
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    assert monitor._next_card is None


def test_the_last_episode_has_no_card(monkeypatch):
    monkeypatch.setattr(upnext, "installed", lambda: False)
    monkeypatch.setattr(upnext, "next_episode", lambda meta: None)
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    assert monitor._next_card is None


def test_switched_off_with_up_next(monkeypatch, no_upnext, settings_module):
    settings_module.set("ui.upnext", "false")
    monitor = watching(monkeypatch, 2850.0)
    monitor.tick()
    assert monitor._next_card is None

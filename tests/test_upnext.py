"""Up Next gets the next episode, including across a season boundary."""
import base64
import json

import pytest

import xbmc
from katan import upnext


@pytest.fixture
def fake_show(monkeypatch):
    """A show with three episodes in season 1 and two in season 2."""
    from katan.meta import tmdb

    seasons = {
        1: [{"episode": n, "title": "S1E%d" % n, "plot": "p",
             "art": {"thumb": "t"}, "duration": 2400} for n in (1, 2, 3)],
        2: [{"episode": n, "title": "S2E%d" % n, "plot": "p",
             "art": {"thumb": "t"}, "duration": 2400} for n in (1, 2)],
    }
    monkeypatch.setattr(tmdb, "episodes",
                        lambda tmdb_id, season: seasons.get(int(season), []))
    return seasons


def base_meta(season, episode):
    return {"type": "episode", "ids": {"tmdb": 1399}, "title": "Show",
            "show_title": "Show", "season": season, "episode": episode,
            "art": {}, "plot": ""}


def test_next_episode_within_a_season(fake_show):
    nxt = upnext.next_episode(base_meta(1, 2))
    assert (nxt["season"], nxt["episode"]) == (1, 3)


def test_next_episode_rolls_into_the_following_season(fake_show):
    nxt = upnext.next_episode(base_meta(1, 3))
    assert (nxt["season"], nxt["episode"]) == (2, 1)


def test_no_next_episode_at_the_end_of_the_show(fake_show):
    assert upnext.next_episode(base_meta(2, 2)) is None


def test_notify_sends_a_well_formed_signal(fake_show):
    del xbmc.JSONRPC_CALLS[:]
    assert upnext.notify_upnext(base_meta(1, 1)) is True
    assert len(xbmc.JSONRPC_CALLS) == 1

    request = json.loads(xbmc.JSONRPC_CALLS[0])
    assert request["method"] == "JSONRPC.NotifyAll"
    assert request["params"]["message"] == "upnext_data"

    blob = request["params"]["data"][0]
    payload = json.loads(base64.b64decode(blob).decode("utf-8"))
    assert payload["current_episode"]["episode"] == 1
    assert payload["next_episode"]["episode"] == 2
    assert "action=episode" in payload["play_url"]
    assert "episode=2" in payload["play_url"]


def test_notify_is_a_no_op_for_movies(fake_show):
    del xbmc.JSONRPC_CALLS[:]
    assert upnext.notify_upnext({"type": "movie", "ids": {"tmdb": 1}}) is False
    assert not xbmc.JSONRPC_CALLS

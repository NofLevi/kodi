"""Dispatch every route the way Kodi does.

Kodi reports a broken handler as a blank screen, so exercising each route with
the network blocked is the cheapest way to catch wiring mistakes: a bad import,
a renamed helper, a route that calls something that no longer exists.
"""
import pytest

import xbmcgui
import xbmcplugin
from katan import router, settings


@pytest.fixture(autouse=True)
def offline(monkeypatch, settings_module):
    """No network, no windows, and the plain directory UI."""
    from katan import http

    monkeypatch.setattr(http, "request", lambda *a, **k: None)
    monkeypatch.setattr(http, "run_parallel", lambda tasks, **k: {})
    settings_module.set_many({
        "ui.window_home": "false",
        "ui.window_search": "false",
        "tmdb.apikey": "test-key",
        "vod.channels_url": "",
        "vod.series_url": "",
    })
    router._load_handlers()


def dispatch(action, **params):
    query = "&".join(["action=%s" % action] +
                     ["%s=%s" % (k, v) for k, v in params.items()])
    xbmcplugin.reset()
    router.dispatch(["plugin://plugin.video.katan/", "1", "?" + query])


NAVIGATION_ROUTES = [
    ("home", {}),
    ("row", {"id": "trending_movies"}),
    ("seasons", {"tmdb": "1399"}),
    ("episodes", {"tmdb": "1399", "season": "1"}),
    ("tools", {}),
    ("live_tv", {}),
    ("vod", {}),
    ("channels", {"kind": "tv"}),
    ("vod_module", {"module": "kan"}),
    ("vod_category", {"name": "x"}),
    ("search_query", {"q": "dune"}),
]


@pytest.mark.parametrize("action,params", NAVIGATION_ROUTES)
def test_navigation_routes_complete_without_error(action, params):
    del xbmcgui.NOTIFICATIONS[:]
    dispatch(action, **params)
    assert xbmcplugin.ENDED, "%s never closed its directory" % action


def test_home_lists_live_tv_and_vod_as_separate_entries():
    dispatch("home")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert any("action=live_tv" in u for u in urls), "live TV section is missing"
    assert any("action=vod" in u for u in urls), "VOD section is missing"


def test_home_does_not_offer_a_row_it_knows_is_empty():
    """A menu entry that opens an empty screen is a dead end.

    The custom window already hides these. The plain listing was still
    offering them, so a Trakt chart with nobody signed in, or the anime row
    while AniList refuses requests, was an entry that led nowhere.
    """
    from katan import cache, catalog

    row_id = catalog.enabled_rows()[0]["id"]
    cache.set(catalog.cache_key(row_id), [], 600)
    dispatch("home")

    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert not any("id=%s" % row_id in u for u in urls)


def test_a_row_that_has_never_been_warmed_is_still_offered():
    """Cold is not empty. Collapsing the two would hide every row on a fresh
    install, before anything has had a chance to load."""
    from katan import cache, catalog

    cache.delete_prefix("row|")
    dispatch("home")

    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    row_id = catalog.enabled_rows()[0]["id"]
    assert any("id=%s" % row_id in u for u in urls)
    assert cache.get(catalog.cache_key(row_id)) is None, \
        "the listing must not warm the row itself"


def test_home_offers_setup_when_tmdb_is_not_configured(settings_module):
    settings_module.set("tmdb.apikey", "")
    dispatch("home")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert any("action=setup" in u for u in urls)


def test_live_tv_lists_real_channels():
    dispatch("live_tv")
    assert len(xbmcplugin.ITEMS) > 20
    assert all("action=play_channel" in url
               for url, _item, _folder in xbmcplugin.ITEMS)


def test_vod_lists_broadcasters():
    dispatch("vod")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert urls and all("action=vod_module" in u for u in urls)


def test_playing_an_unknown_channel_fails_cleanly():
    dispatch("play_channel", id="does_not_exist")
    assert xbmcplugin.RESOLVED
    handle, succeeded, _item = xbmcplugin.RESOLVED[-1]
    assert succeeded is False


def test_playing_a_channel_resolves_a_url():
    dispatch("play_channel", id="ch_11")
    assert xbmcplugin.RESOLVED
    _handle, succeeded, item = xbmcplugin.RESOLVED[-1]
    assert succeeded is True
    assert item.getPath().startswith("http")


def test_an_unknown_action_is_reported_not_crashed():
    del xbmcgui.NOTIFICATIONS[:]
    dispatch("no_such_action")
    assert xbmcgui.NOTIFICATIONS, "the user should be told something went wrong"


def test_a_handler_that_raises_does_not_escape_dispatch(monkeypatch):
    @router.route("boom_for_test")
    def boom(params):
        raise RuntimeError("this should be contained")

    del xbmcgui.NOTIFICATIONS[:]
    dispatch("boom_for_test")
    assert xbmcgui.NOTIFICATIONS


def test_playback_without_a_debrid_account_explains_itself(settings_module):
    settings_module.set_many({"realdebrid.token": "", "torbox.apikey": "",
                              "premiumize.apikey": "", "alldebrid.apikey": ""})
    assert settings.configured_debrid() == []
    dispatch("movie", tmdb="693134")
    assert xbmcplugin.RESOLVED
    _handle, succeeded, _item = xbmcplugin.RESOLVED[-1]
    assert succeeded is False

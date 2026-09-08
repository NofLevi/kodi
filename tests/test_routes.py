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


def test_a_row_listing_offers_the_next_page(monkeypatch):
    """A Kodi directory is a fixed list with no scroll event to hang a fetch
    off, so the plain listing pages the way Kodi's own skins expect."""
    from katan import catalog

    monkeypatch.setattr(catalog, "load",
                        lambda row_id, page=1, **kw: [
                            {"type": "movie", "title": "Film %d" % page,
                             "ids": {"tmdb": page}, "art": {}}])
    dispatch("row", id="trending_movies")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert any("action=row" in u and "page=2" in u for u in urls), \
        "the row listing should end with a way to the next page"


def test_a_row_that_cannot_page_is_not_given_a_next_page(monkeypatch):
    """A Trakt list arrives whole, so asking for page two is a wasted call.

    This used to be about the live channels, which were also whole lists -
    and that was the bug: forty-six channels, twelve shown, and no way to
    reach the rest. They page now, because paging a list already in memory
    costs a slice.
    """
    from katan import catalog

    monkeypatch.setattr(catalog, "load",
                        lambda row_id, page=1, **kw: [
                            {"type": "movie", "title": "Film", "ids": {},
                             "art": {}}])
    dispatch("row", id="watchlist")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert not any("page=2" in u for u in urls)


def test_the_israeli_rows_can_be_scrolled_past_their_first_page():
    """Forty-six channels and eight hundred Kan programmes are not twelve."""
    from katan import catalog

    for row_id in ("israel_live", "israel_vod", "israel_radio"):
        assert catalog.has_more(row_id), \
            "%s stops after one page, so most of it is unreachable" % row_id


def test_an_empty_page_ends_rather_than_offering_another(monkeypatch):
    """Past the last page TMDB answers with an empty list, not an error."""
    from katan import catalog

    monkeypatch.setattr(catalog, "load", lambda row_id, page=1, **kw: [])
    dispatch("row", id="trending_movies", page="9")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert not any("page=10" in u for u in urls)


def test_a_nonsense_page_number_is_treated_as_the_first(monkeypatch):
    """A url is something a viewer can bookmark, edit and get wrong."""
    from katan import catalog

    seen = []
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, page=1, **kw: seen.append(page) or [])
    dispatch("row", id="trending_movies", page="banana")
    dispatch("row", id="trending_movies", page="-4")
    assert seen == [1, 1]


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


# --------------------------------------------------------------------------
# the subtitle dialog, which arrives through the plugin entry point
# --------------------------------------------------------------------------


def test_kodis_subtitle_search_does_not_open_the_search_window():
    """The bug this test exists for.

    Kodi runs the plugin source for a subtitle module belonging to an add-on
    that is also a video plugin: the dialog calls
    plugin://plugin.video.katan/?action=search&languages=English, and Kodi
    resolves that to main.py, never to subtitles.py. So action=search landed
    in the video search-window route, which tried to open a window over a
    modal dialog and failed, and the chooser said "no subtitles found" for
    every film ever tried.
    """
    assert router.is_subtitle_request(
        {"action": "search", "languages": "English",
         "preferredlanguage": "English"})


def test_the_addons_own_search_is_not_mistaken_for_a_subtitle_search():
    assert not router.is_subtitle_request({"action": "search"})
    assert not router.is_subtitle_request({"action": "search", "q": "dune"})


def test_the_unambiguous_subtitle_actions_are_recognised():
    """No route of ours is called manualsearch or download."""
    assert router.is_subtitle_request({"action": "manualsearch"})
    assert router.is_subtitle_request({"action": "download", "id": "x"})
    assert "manualsearch" not in router.registered_actions()
    assert "download" not in router.registered_actions()


def test_nothing_else_is_a_subtitle_request():
    for action in router.registered_actions():
        assert not router.is_subtitle_request({"action": action}), action


def test_a_subtitle_search_reaches_the_subtitle_service(monkeypatch):
    from katan.subs import service

    seen = []
    monkeypatch.setattr(service, "dispatch", lambda argv: seen.append(argv))
    dispatch("search", languages="English", preferredlanguage="English")
    assert seen, "the subtitle service should have been called"


def test_the_israeli_sections_are_listed_once_each():
    """They were rows and sections at the same time, under the same headings.

    "שידורים חיים" and "VOD ישראלי" each appeared twice on the plain home
    screen, one directly above the other. In the custom window they are
    horizontal rows of artwork and the sections are not drawn at all, so the
    duplication only ever showed here.
    """
    from katan import kodi as katan_kodi

    dispatch("home")
    labels = [item.getLabel() for _url, item, _folder in xbmcplugin.ITEMS]
    for string_id in (32217, 32218):
        heading = katan_kodi.localize(string_id)
        assert labels.count(heading) == 1, "%r appears %d times" % (
            heading, labels.count(heading))


def test_the_sections_still_point_at_the_section_routes():
    dispatch("home")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert any("action=live_tv" in u for u in urls)
    assert any("action=vod" in u for u in urls)
    assert not any("id=israel_live" in u for u in urls)
    assert not any("id=israel_vod" in u for u in urls)


def test_a_fresh_install_still_offers_what_needs_no_key(settings_module):
    """Israeli live TV and the on-demand catalogue need nothing at all.

    A fresh install showed exactly one line - "set up Katan" - with
    forty-three working channels behind a gate for a key they do not use.
    """
    from katan import kodi as katan_kodi

    settings_module.set("tmdb.apikey", "")
    dispatch("home")

    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert any("action=setup" in u for u in urls), "setup still comes first"
    assert any("action=live_tv" in u for u in urls)
    assert any("action=vod" in u for u in urls)
    assert any("action=tools" in u for u in urls)
    labels = [item.getLabel() for _url, item, _folder in xbmcplugin.ITEMS]
    assert labels[0] == katan_kodi.localize(32256)


def test_a_fresh_install_does_not_offer_rows_that_need_a_key(settings_module):
    settings_module.set("tmdb.apikey", "")
    dispatch("home")
    urls = [url for url, _item, _folder in xbmcplugin.ITEMS]
    assert not any("action=row" in u for u in urls), \
        "every catalogue row needs TMDB"


def test_the_window_opens_even_without_a_tmdb_key(monkeypatch,
                                                  settings_module):
    """A fresh install has no key, but the Israeli rows and Kitsu anime need
    none - so the window has content and a file list is the wrong thing to
    show somebody opening the add-on for the first time."""
    import xbmcplugin
    from katan.ui import handlers

    settings_module.set_many({"ui.window_home": "true", "tmdb.apikey": ""})

    opened = []
    monkeypatch.setattr("katan.ui.home_window.open_home",
                        lambda: opened.append(True))

    xbmcplugin.reset()
    handlers.home({})
    assert opened, "the plain listing was shown instead of the window"


def test_the_plain_listing_is_used_when_no_row_can_draw(monkeypatch,
                                                        settings_module):
    """The one case the fallback is for."""
    import xbmcplugin
    from katan import catalog
    from katan.ui import handlers

    settings_module.set_many({"ui.window_home": "true", "tmdb.apikey": ""})
    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])

    opened = []
    monkeypatch.setattr("katan.ui.home_window.open_home",
                        lambda: opened.append(True))

    xbmcplugin.reset()
    handlers.home({})
    assert not opened
    assert any("action=setup" in url for url, _i, _f in xbmcplugin.ITEMS)


def test_the_window_is_never_opened_inside_a_directory_call(monkeypatch,
                                                            settings_module):
    """Both orders are wrong, so it does neither.

    Ending the directory first lets Kodi navigate away and tear the window
    down. Ending it afterwards holds GetDirectory open for the life of the
    window - measured at twenty-one minutes - so Kodi sits behind a busy
    dialog, and on exit the stale directory completes, Kodi navigates, and the
    window reopens. That loop left Kodi stuck.
    """
    import xbmcplugin
    from katan import kodi
    from katan.ui import handlers

    settings_module.set_many({"ui.window_home": "true", "tmdb.apikey": "k"})

    opened = []
    ran = []
    monkeypatch.setattr("katan.ui.home_window.open_home",
                        lambda: opened.append(True))
    monkeypatch.setattr(kodi, "run_builtin", lambda command: ran.append(command))

    xbmcplugin.reset()
    kodi.set_plugin_handle(7)          # a directory call
    handlers.home({})

    assert not opened, "the window was opened while a directory was still open"
    assert xbmcplugin.ENDED, "the directory was left hanging"
    assert any("RunPlugin(" in c for c in ran), \
        "it should ask Kodi to run the plugin again without a directory"


def test_a_runplugin_invocation_opens_the_window_directly(monkeypatch,
                                                          settings_module):
    """The second invocation has no handle, so there is nothing to close."""
    import xbmcplugin
    from katan import kodi
    from katan.ui import handlers

    settings_module.set_many({"ui.window_home": "true", "tmdb.apikey": "k"})

    opened = []
    ran = []
    monkeypatch.setattr("katan.ui.home_window.open_home",
                        lambda: opened.append(True))
    monkeypatch.setattr(kodi, "run_builtin", lambda command: ran.append(command))

    xbmcplugin.reset()
    kodi.set_plugin_handle(-1)         # RunPlugin
    handlers.home({})

    assert opened, "the window should just open"
    assert not ran, "it must not ask for a third invocation"


# --------------------------------------------------------------------------
# choosing a source for an episode
#
# An episode's own TMDB id is not its show's, and every route below the
# context menu is keyed on the series. Sending the episode's id means
# build_meta asks TMDB for a show that does not exist, gets no title, and
# gives up - so the press does nothing whatsoever, which is what "choosing a
# source only works on films" looked like.
# --------------------------------------------------------------------------


def test_the_context_menu_asks_for_sources_by_the_shows_id():
    from katan.ui import listing

    episode = {
        "type": "episode", "title": "Freedom Day", "season": 1, "episode": 1,
        # The shape tmdb.episodes really returns: the episode's own id in
        # ids, and the series id kept beside it.
        "ids": {"tmdb": 2964686, "imdb": "tt14688458"},
        "extra": {"tmdb_show": 125988},
    }
    sources = [url for label, url in listing.context_menu(episode)
               if "action=sources" in url]
    assert sources, "an episode has to offer a source picker at all"
    assert "tmdb=125988" in sources[0], \
        "the picker has to be asked for the series, not the episode"
    assert "tmdb=2964686" not in sources[0]


def test_a_film_still_uses_its_own_id():
    from katan.ui import listing

    film = {"type": "movie", "title": "Fight Club", "ids": {"tmdb": 550}}
    sources = [url for label, url in listing.context_menu(film)
               if "action=sources" in url]
    assert sources and "tmdb=550" in sources[0]


def test_an_episode_with_no_show_id_falls_back_rather_than_vanishing():
    """Rows built somewhere other than tmdb.episodes carry no tmdb_show."""
    from katan.ui import listing

    episode = {"type": "episode", "season": 2, "episode": 3,
               "ids": {"tmdb": 4242}, "extra": {}}
    sources = [url for label, url in listing.context_menu(episode)
               if "action=sources" in url]
    assert sources and "tmdb=4242" in sources[0]

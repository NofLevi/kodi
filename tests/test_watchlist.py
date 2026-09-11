# -*- coding: utf-8 -*-
"""Taking something off the Trakt watchlist, which could not be done at all.

Anything added stayed on the home screen's watchlist row until somebody went
to trakt.tv in a browser - on a household whose only screen is a projector
driven by a remote. And adding never refreshed the row either, so it looked
as though nothing had happened until the cache expired hours later.
"""
import pytest

from katan.meta import trakt


class Response(object):
    def __init__(self, status):
        self.status_code = status


@pytest.fixture
def posted(monkeypatch):
    sent = []
    monkeypatch.setattr(trakt, "authorised", lambda: True)
    monkeypatch.setattr(trakt, "_post",
                        lambda path, payload: sent.append((path, payload))
                        or Response(200))
    return sent


def test_removing_posts_to_the_remove_endpoint(posted):
    assert trakt.remove_from_watchlist("movie", 550) is True
    assert posted == [("/sync/watchlist/remove",
                       {"movies": [{"ids": {"tmdb": 550}}]})]


def test_a_show_is_removed_as_a_show(posted):
    assert trakt.remove_from_watchlist("show", 1396) is True
    assert posted[0][1] == {"shows": [{"ids": {"tmdb": 1396}}]}


def test_signed_out_it_says_so_and_sends_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(trakt, "authorised", lambda: False)
    monkeypatch.setattr(trakt, "_post", lambda *args: sent.append(args))
    assert trakt.remove_from_watchlist("movie", 550) is False
    assert not sent


def test_what_the_watchlist_returns_is_marked_as_on_it(monkeypatch):
    """Nothing mirrors watchlist membership, so the row's own items carry it -
    which is what lets their menu offer Remove instead of Add."""
    rows = {
        "/users/me/watchlist/movies": [
            {"movie": {"title": "Fight Club", "year": 1999,
                       "ids": {"tmdb": 550, "imdb": "tt0137523"}}}],
        "/users/me/watchlist/shows": [],
    }
    monkeypatch.setattr(trakt, "authorised", lambda: True)
    monkeypatch.setattr(trakt, "_get", lambda path, **kw: rows.get(path, []))
    monkeypatch.setattr(trakt, "_fill_art", lambda entries: entries)

    listed = trakt.watchlist()
    assert listed, "the watchlist came back empty"
    assert all((item.get("extra") or {}).get("in_watchlist") for item in listed)


def test_the_menu_offers_remove_only_where_the_item_is_on_the_watchlist():
    from katan.ui import listing

    listed = {"type": "movie", "title": "Fight Club", "ids": {"tmdb": 550},
              "extra": {"in_watchlist": True}}
    urls = [url for _label, url in listing.context_menu(listed)]
    assert any("trakt_watchlist_remove" in url for url in urls)
    assert not any("trakt_watchlist_add" in url for url in urls)

    elsewhere = dict(listed, extra={})
    urls = [url for _label, url in listing.context_menu(elsewhere)]
    assert any("trakt_watchlist_add" in url for url in urls)
    assert not any("trakt_watchlist_remove" in url for url in urls)


@pytest.mark.parametrize("route,call", [
    ("trakt_watchlist_add", "add_to_watchlist"),
    ("trakt_watchlist_remove", "remove_from_watchlist"),
])
def test_either_change_refreshes_the_watchlist_row(monkeypatch, route, call):
    from katan import catalog, kodi
    from katan.ui import handlers

    cleared = []
    monkeypatch.setattr(catalog, "invalidate",
                        lambda row=None: cleared.append(row))
    monkeypatch.setattr(trakt, call, lambda item_type, tmdb_id: True)
    refreshed = []
    monkeypatch.setattr(kodi, "refresh_container",
                        lambda: refreshed.append(True))

    getattr(handlers, route)({"type": "movie", "tmdb": "550"})
    assert cleared == ["watchlist"]
    assert refreshed

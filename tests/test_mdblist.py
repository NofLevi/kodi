"""MDBList, which until now was a settings field with no code behind it.

MDBList returns external ids and almost no presentation, so the work is the
resolution through TMDB. These tests pin the two things that could go wrong
there: it must stay bounded, and it must keep the order the curator chose.

The live API needs a key, so everything here runs against the documented
shapes. What is verified is this add-on's behaviour, not MDBList's.
"""
import pytest

from katan import http, settings
from katan.meta import items, mdblist

KEY = "test-key-not-a-real-one"


def row(imdb_id, title, rank=None):
    entry = {"imdb_id": imdb_id, "title": title, "mediatype": "movie",
             "release_year": 2020}
    if rank is not None:
        entry["rank"] = rank
    return entry


@pytest.fixture
def keyed(settings_module):
    settings_module.set("mdblist.apikey", KEY)
    return settings_module


@pytest.fixture
def api(monkeypatch, keyed):
    calls = []
    state = {
        "/lists/user": [{"id": 7, "name": "My picks", "items": 3,
                         "description": "things"}],
        "/lists/top": [{"id": 9, "name": "Top week", "items": 50}],
        "/lists/7/items": [row("tt0001", "First", rank=1),
                           row("tt0002", "Second", rank=2),
                           row("tt0003", "Third", rank=3)],
    }

    def fake_get_json(url, default=None, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        for path, payload in state.items():
            if url.endswith(path):
                return payload
        return default

    monkeypatch.setattr(http, "get_json", fake_get_json)

    # Stand in for TMDB so no network and no key is needed.
    resolved = []

    def fake_find(imdb_id):
        resolved.append(imdb_id)
        if imdb_id == "tt0002":
            return None             # a title TMDB does not know
        return items.new_item("movie", ids={"imdb": imdb_id, "tmdb": 1},
                              title="Resolved %s" % imdb_id)

    from katan.meta import tmdb
    monkeypatch.setattr(tmdb, "find_by_imdb", fake_find)

    return {"calls": calls, "state": state, "resolved": resolved}


# --------------------------------------------------------------------------
# the key gate
# --------------------------------------------------------------------------


def test_nothing_happens_without_a_key(settings_module, no_network):
    settings_module.set("mdblist.apikey", "")
    assert mdblist.has_key() is False
    assert mdblist.my_lists() == []
    assert mdblist.top_lists() == []
    assert mdblist.list_items("7") == []


def test_the_key_is_sent_as_a_parameter(api):
    mdblist.my_lists()
    assert api["calls"][0][1]["apikey"] == KEY


def test_the_key_is_not_part_of_the_cache_key(api):
    """The cache is a file on disk; a credential does not belong in its keys."""
    from katan import cache
    mdblist.my_lists()
    assert KEY not in cache.make_key("mdblist", "/lists/user")


# --------------------------------------------------------------------------
# lists
# --------------------------------------------------------------------------


def test_user_lists_are_returned(api):
    found = mdblist.my_lists()
    assert found == [{"id": "7", "name": "My picks", "count": 3,
                      "description": "things"}]


def test_lists_are_cached(api):
    mdblist.my_lists()
    mdblist.my_lists()
    assert len([c for c in api["calls"] if "/lists/user" in c[0]]) == 1


def test_a_list_wrapped_in_an_object_is_understood(api):
    api["state"]["/lists/user"] = {"lists": [{"id": 1, "name": "wrapped"}]}
    assert mdblist.my_lists()[0]["name"] == "wrapped"


def test_a_nonsense_answer_yields_nothing(monkeypatch, keyed):
    monkeypatch.setattr(http, "get_json",
                        lambda url, default=None, **kw: {"error": "bad key"})
    assert mdblist.my_lists() == []


def test_a_service_that_does_not_answer_yields_nothing(monkeypatch, keyed):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: None)
    assert mdblist.my_lists() == []


# --------------------------------------------------------------------------
# resolving the items
# --------------------------------------------------------------------------


def test_items_are_resolved_through_tmdb(api):
    found = mdblist.list_items("7")
    assert [item["title"] for item in found] == \
        ["Resolved tt0001", "Resolved tt0003"]


def test_the_curators_order_is_kept(api):
    """Lookups finish out of order; the list must not be reordered by luck."""
    found = mdblist.list_items("7")
    assert found[0]["ids"]["imdb"] == "tt0001"
    assert found[-1]["ids"]["imdb"] == "tt0003"


def test_a_title_tmdb_does_not_know_is_dropped_not_blanked(api):
    found = mdblist.list_items("7")
    assert all(item["title"] for item in found)
    assert "tt0002" not in [item["ids"]["imdb"] for item in found]


def test_the_rank_is_carried_through(api):
    found = mdblist.list_items("7")
    assert found[0]["extra"]["mdblist_rank"] == 1


def test_an_entry_with_no_imdb_id_is_skipped(api):
    api["state"]["/lists/7/items"] = [{"title": "no ids"}]
    assert mdblist.list_items("7") == []


def test_a_split_payload_is_understood(api):
    """MDBList returns movies and shows separately on some lists."""
    api["state"]["/lists/7/items"] = {
        "movies": [row("tt0001", "a film")],
        "shows": [row("tt0003", "a show")],
    }
    assert len(mdblist.list_items("7")) == 2


def test_the_batch_is_bounded(api):
    """Forty lookups on a weak box must not be forty threads."""
    assert mdblist.WORKERS <= 4
    assert mdblist.MAX_ITEMS <= 50
    assert mdblist.DEADLINE <= 20


def test_a_huge_list_is_capped(api):
    api["state"]["/lists/7/items"] = [
        row("tt%04d" % n, "t%d" % n) for n in range(500)]
    assert len(mdblist.list_items("7")) <= mdblist.MAX_ITEMS


def test_an_empty_list_id_asks_for_nothing(api):
    assert mdblist.list_items("") == []
    assert not api["calls"]


# --------------------------------------------------------------------------
# the setting is no longer a phantom
# --------------------------------------------------------------------------


def test_the_setting_now_has_a_module_behind_it():
    assert "mdblist.apikey" in settings.DEFAULTS
    assert hasattr(mdblist, "list_items")


def test_the_menu_entry_is_hidden_without_a_key(settings_module):
    """A menu item that opens an empty screen is worse than no menu item."""
    settings_module.set("mdblist.apikey", "")
    assert not mdblist.has_key()

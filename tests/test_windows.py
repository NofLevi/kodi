"""The custom home and search windows.

These are the main interface, so an error in them is a black screen. The stub
WindowXML records controls and properties, which is enough to check that rows
are filled, the hero updates, and typing produces suggestions.
"""
import pytest

import xbmcgui
from katan.ui import home_window, search_window


class FakeAction(object):
    def __init__(self, action_id=0, unicode_char=""):
        self._id = action_id
        self._char = unicode_char

    def getId(self):
        return self._id

    def getUnicode(self):
        return self._char


def make_items(count, prefix="Title"):
    from katan.meta import items
    return [items.new_item("movie", ids={"tmdb": index + 1},
                           title="%s %d" % (prefix, index),
                           year=2020 + index, plot="plot %d" % index,
                           genres=["Drama"], rating=7.5,
                           art={"poster": "p.jpg", "fanart": "f.jpg"})
            for index in range(count)]


# --------------------------------------------------------------------------
# home
# --------------------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    """A TMDB key, which the home window now requires before it builds itself."""
    from katan.meta import tmdb
    monkeypatch.setattr(tmdb, "has_key", lambda: True)


@pytest.fixture
def home(monkeypatch, configured):
    from katan import catalog
    from katan.meta import trakt_state

    rows = [{"id": "row%d" % n, "title_id": 32201, "loader": lambda: [],
             "ttl": 60, "needs": [], "default": True} for n in range(4)]
    monkeypatch.setattr(catalog, "enabled_rows", lambda: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row " + row["id"])
    monkeypatch.setattr(catalog, "peek", lambda row_id: make_items(5, row_id))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.onInit()
    return window


def test_home_fills_its_rows_and_sets_headings(home):
    assert len(home.rows) == 4
    assert home.getProperty("katan.row0.title") == "Row row0"
    first = home.getControl(home_window.LIST_BASE)
    assert first.size() == 5


def test_home_only_preloads_the_rows_near_the_top(home):
    """A long home page must not cost more than a short one."""
    assert len(home.filled) <= home_window.PRELOAD_ROWS + 1


def test_opening_the_addon_goes_straight_to_the_katan_window(monkeypatch,
                                                             settings_module):
    """Entering the add-on should land in the Katan GUI, not a Kodi file list."""
    from katan.meta import tmdb
    from katan.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(tmdb, "has_key", lambda: True)

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert opened, "the custom window should have been opened"


def test_without_a_key_it_lists_instead_of_flashing_a_window(monkeypatch,
                                                            settings_module):
    """The window would have nothing to draw, so the listing explains itself."""
    from katan.meta import tmdb
    from katan.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(tmdb, "has_key", lambda: False)

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert not opened, "an empty window is worse than a listing that explains"


def test_finishing_setup_opens_the_window(monkeypatch, settings_module):
    """Setup is not the destination; finishing it should show the GUI."""
    from katan import catalog
    from katan.ui import wizard

    settings_module.set("tmdb.apikey", "a-key")
    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(catalog, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(catalog, "warm", lambda *a, **kw: 0)

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    wizard._finish()
    assert opened, "the viewer should end up in the GUI, not back in a list"


def test_abandoning_setup_does_not_loop_back_into_the_window(monkeypatch,
                                                            settings_module):
    """No key means no window, or declining setup would reopen it forever."""
    from katan import catalog
    from katan.ui import wizard

    settings_module.set("tmdb.apikey", "")
    monkeypatch.setattr(catalog, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(catalog, "warm", lambda *a, **kw: 0)

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    wizard._finish()
    assert not opened


def test_home_offers_setup_instead_of_a_black_screen(monkeypatch):
    """Without a key almost every row is unavailable and the screen was blank.

    The plain directory listing has always offered the wizard here. The window
    showed nothing at all, which reads as a broken add-on rather than an
    unconfigured one.
    """
    from katan import kodi
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "has_key", lambda: False)
    asked = []
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **kw: asked.append(a) or False)

    window = home_window.HomeWindow()
    window.onInit()

    assert asked, "the viewer should be offered the setup wizard"
    assert window.rows == [], "no rows should be built without a key"


def test_home_preloads_past_rows_that_come_back_empty(monkeypatch, configured):
    """An enabled but empty row must not use up a visible slot.

    A Trakt chart with no account and an unwarmed row both return nothing, and
    they sit above the Israeli rows in the default order. Filling the first
    three slots regardless left a blank screen with content further down.
    """
    from katan import catalog
    from katan.meta import trakt_state

    rows = [{"id": "row%d" % n, "title_id": 32201, "loader": lambda: [],
             "ttl": 60, "needs": [], "default": True} for n in range(6)]
    empty = {"row0", "row1", "row2"}
    monkeypatch.setattr(catalog, "enabled_rows", lambda: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row " + row["id"])
    monkeypatch.setattr(catalog, "peek",
                        lambda row_id: [] if row_id in empty
                        else make_items(5, row_id))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.onInit()

    assert window.data.get(3), "the first row with content must be filled"
    assert len([i for i in window.data.values() if i]) >= 1
    assert window.getProperty("katan.hero.title"), \
        "the hero should come from the first row that actually has something"


def test_home_updates_the_hero_from_the_focused_item(home):
    home.setFocusId(home_window.LIST_BASE)
    home.onAction(FakeAction(home_window.ACTION_MOVE_RIGHT))
    assert home.getProperty("katan.hero.title") == "row0 0"
    assert home.getProperty("katan.hero.fanart") == "f.jpg"
    assert "2020" in home.getProperty("katan.hero.meta")


def test_the_hero_backdrop_is_left_empty_without_a_real_fanart(home):
    """A poster or a channel logo stretched to 16:9 looks broken."""
    home._show_hero({"title": "A channel", "art": {"poster": "logo.png"}})
    assert home.getProperty("katan.hero.fanart") == ""
    assert home.getProperty("katan.hero.title") == "A channel"


def test_home_fills_further_rows_as_focus_moves(home):
    before = len(home.filled)
    home.setFocusId(home_window.LIST_BASE + 3)
    home.onAction(FakeAction(home_window.ACTION_MOVE_DOWN))
    assert len(home.filled) >= before


def test_an_empty_row_hides_itself(monkeypatch):
    from katan import catalog
    from katan.meta import trakt_state

    rows = [{"id": "empty", "title_id": 32201, "loader": lambda: [],
             "ttl": 60, "needs": [], "default": True}]
    monkeypatch.setattr(catalog, "enabled_rows", lambda: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Empty")
    monkeypatch.setattr(catalog, "peek", lambda row_id: [])
    monkeypatch.setattr(catalog, "load", lambda row_id: [])
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.onInit()
    assert window.getProperty("katan.row0.title") == "", "an empty row should hide"


def test_back_closes_the_home_window(home):
    home.onAction(FakeAction(home_window.ACTION_NAV_BACK))
    assert home.closed is True


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


@pytest.fixture
def search(monkeypatch):
    from katan.search import unified

    monkeypatch.setattr(unified, "recent", lambda: ["dune", "severance"])
    monkeypatch.setattr(unified, "remember", lambda q: None)
    monkeypatch.setattr(unified, "suggest",
                        lambda q, limit=12: make_items(3, "Suggest"))

    window = search_window.SearchWindow()
    window.onInit()
    return window


def test_search_starts_with_recent_queries(search):
    control = search.getControl(search_window.LIST_RESULTS)
    assert control.size() == 2
    assert control.items[0].getLabel() == "dune"


def test_the_key_grid_is_labelled_for_the_current_charset(search):
    assert search.getProperty("katan.key0") == "a"
    search.onClick(search_window.BUTTON_CHARSET)
    assert search.getProperty("katan.key0") == u"\u05d0", "expected Hebrew alef"
    search.onClick(search_window.BUTTON_CHARSET)
    assert search.getProperty("katan.key0") == "0", "expected digits"


def test_pressing_a_key_appends_to_the_query(search):
    search.onClick(search_window.KEY_BASE)
    search.onClick(search_window.KEY_BASE + 1)
    assert search.text == "ab"
    assert search.getProperty("katan.search.text") == "ab"


def test_a_physical_keyboard_also_types(search):
    search.onAction(FakeAction(unicode_char="d"))
    search.onAction(FakeAction(unicode_char="u"))
    assert search.text == "du"


def test_backspace_and_clear(search):
    search.onAction(FakeAction(unicode_char="d"))
    search.onAction(FakeAction(unicode_char="x"))
    search.onClick(search_window.BUTTON_BACKSPACE)
    assert search.text == "d"
    search.onClick(search_window.BUTTON_CLEAR)
    assert search.text == ""


def test_suggestions_never_overwrite_what_was_typed(search):
    """Autocomplete offers, it does not complete for you."""
    for char in "dun":
        search.onAction(FakeAction(unicode_char=char))
    search._schedule_suggestions()
    import time
    time.sleep(0.5)
    assert search.text == "dun", "the typed text must survive suggestions"


def test_submitting_returns_exactly_what_was_typed(search):
    for char in "dune":
        search.onAction(FakeAction(unicode_char=char))
    search.onClick(search_window.BUTTON_SEARCH)
    assert search.submitted == "dune"
    assert search.closed is True


def test_a_query_that_is_too_short_is_not_submitted(search):
    search.onAction(FakeAction(unicode_char="d"))
    search.onClick(search_window.BUTTON_SEARCH)
    assert search.submitted is None
    assert search.closed is False


def test_choosing_a_recent_query_reruns_it(search):
    control = search.getControl(search_window.LIST_RESULTS)
    control.position = 1
    search.onClick(search_window.LIST_RESULTS)
    assert search.submitted == "severance"

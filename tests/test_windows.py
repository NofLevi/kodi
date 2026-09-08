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


class Kodi21Action(object):
    """An action shaped like the real thing on Kodi 21.

    xbmcgui.Action there has getId, getButtonCode and the two amounts, and no
    getUnicode at all. The suite's own fake grew a getUnicode that Kodi does
    not have, so the search window called it on every keypress and raised an
    AttributeError that only showed up in a real Kodi.
    """

    def __init__(self, action_id=0):
        self._id = action_id

    def getId(self):
        return self._id

    def getButtonCode(self):
        return 0

    def getAmount1(self):
        return 0.0

    def getAmount2(self):
        return 0.0


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
    monkeypatch.setattr(catalog, "enabled_rows", lambda section=None: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row " + row["id"])
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: make_items(5, row_id))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.onInit()
    return window


def test_the_real_flow_fills_the_rows(monkeypatch, configured):
    """open_home calls prepare() and then Kodi calls onInit().

    Every other home test calls onInit() alone, so none of them exercised the
    order the add-on actually uses. When prepare() started working out the rows
    early, onInit's "have I run already" guard was reading self.rows and
    returned at once: the window drew its headings above completely empty rows.
    """
    from katan import catalog
    from katan.meta import trakt_state

    rows = [{"id": "row%d" % n, "title_id": 32201, "loader": lambda: [],
             "ttl": 60, "needs": [], "default": True} for n in range(3)]
    monkeypatch.setattr(catalog, "enabled_rows", lambda section=None: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row " + row["id"])
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: make_items(5, row_id))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.prepare()          # what open_home does before showing the window
    window.onInit()           # what Kodi does when it appears

    assert window.data, "prepare() must not stop onInit from filling the rows"
    assert window.getControl(home_window.LIST_BASE).size() == 5
    assert window.getProperty("katan.hero.title"), "the hero should be seeded"


def test_onInit_still_only_runs_once(home):
    """It fires again when a dialog closes; the second pass must be a no-op."""
    before = dict(home.data)
    home.onInit()
    assert home.data == before


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


def test_it_lists_instead_of_flashing_an_empty_window(monkeypatch,
                                                      settings_module):
    """An empty window is worse than a listing that explains itself.

    This used to key on the TMDB key, on the grounds that almost every row
    needs one. It does not any more: the Israeli channels and catalogue are
    bundled data and anime comes from Kitsu, so five rows draw with nothing
    configured, and a fresh install was being shown a file list while the
    window had content waiting. What actually decides is whether any row can
    draw at all.
    """
    from katan import catalog
    from katan.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert not opened, "an empty window is worse than a listing that explains"


def test_a_fresh_install_still_gets_the_window(monkeypatch, settings_module):
    """No key configured, but the Israeli and anime rows need none."""
    from katan.meta import tmdb
    from katan.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(tmdb, "has_key", lambda: False)

    opened = []
    import katan.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert opened, "a first-time viewer was shown a file list"


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


def test_home_draws_the_rows_that_need_no_key(monkeypatch):
    """Without a TMDB key the window used to close itself and offer a wizard.

    That was written when almost every row needed a key. It does not hold any
    more: the Israeli channels and catalogue are bundled data, Kitsu anime
    needs nothing, and the two public Trakt charts need only a client id. The
    catalog already drops rows whose credentials are missing, so what comes
    back is exactly what can be drawn - and closing over a modal yes/no meant
    a fresh install never once saw the home screen.
    """
    from katan import kodi
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "has_key", lambda: False)
    asked = []
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **kw: asked.append(a) or False)

    window = home_window.HomeWindow()
    window.onInit()

    assert not asked, "a modal over the window is what tore it down"
    assert not window.closed, "the window closed itself on a fresh install"
    assert window.rows, "the rows that need no key should still be drawn"
    assert window.section != "movies", (
        "it should move off the tab whose every row needs a key")


def test_home_still_closes_when_there_is_genuinely_nothing(monkeypatch):
    """The honest version of the guard that was removed."""
    from katan import catalog

    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])

    window = home_window.HomeWindow()
    window.onInit()
    assert window.closed, "a window with no rows at all is a black screen"


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
    monkeypatch.setattr(catalog, "enabled_rows", lambda section=None: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row " + row["id"])
    monkeypatch.setattr(catalog, "peek",
                        lambda row_id, section=None: [] if row_id in empty
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
    monkeypatch.setattr(catalog, "enabled_rows", lambda section=None: rows)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Empty")
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: [])
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, refresh=False, page=1,
                        section=None: [])
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
    assert search.getProperty("katan.key0") == "0", "expected digits"
    search.onClick(search_window.BUTTON_CHARSET)
    assert search.getProperty("katan.key0") == u"א", "expected Hebrew alef"


def test_pressing_a_key_appends_to_the_query(search):
    search.onClick(search_window.KEY_BASE)
    search.onClick(search_window.KEY_BASE + 1)
    assert search.text == "ab"
    assert search.getProperty("katan.search.text") == "ab"


def test_typing_survives_a_kodi_with_no_getUnicode(search):
    """Kodi 21's Action has no getUnicode, and calling it threw on every key.

    The on-screen grid is the input that always works, so an action the build
    cannot decode has to be ignored rather than fatal.
    """
    search.onAction(Kodi21Action())
    search.onAction(Kodi21Action())
    assert search.text == "", "an undecodable action types nothing"

    search.onClick(search_window.KEY_BASE)
    assert search.text == "a", "the grid must still work"


def test_every_charset_fills_the_key_grid(search):
    """A key with no character used to be hidden, and hidden cannot be focused."""
    for index, charset in enumerate(search_window.CHARSETS):
        assert len(charset) == search_window.KEY_COUNT, \
            "charset %d has %d keys, not %d" % (index, len(charset),
                                                search_window.KEY_COUNT)
    for index in range(search_window.KEY_COUNT):
        assert search.getProperty("katan.key%d" % index), \
            "key %d has no character, so it cannot take focus" % index


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


# --------------------------------------------------------------------------
# which keyboard the search window opens on
# --------------------------------------------------------------------------


def test_a_hebrew_interface_opens_on_the_hebrew_keyboard(settings_module):
    """Live TV and the on-demand catalogue are titled entirely in Hebrew.

    Opening on the Latin keyboard meant every one of those searches began
    with a trip to the charset button.
    """
    from katan.ui import search_window

    settings_module.set("ui.language", "he")
    assert search_window.initial_charset() == search_window.HEBREW
    assert search_window.SearchWindow().charset == search_window.HEBREW


def test_an_english_interface_opens_on_the_latin_keyboard(settings_module):
    from katan.ui import search_window

    settings_module.set("ui.language", "en")
    assert search_window.initial_charset() == search_window.LATIN
    assert search_window.SearchWindow().charset == search_window.LATIN


def test_the_charset_button_still_names_where_it_goes_next(settings_module):
    """It names the set it will move to, not the one you are on."""
    from katan.ui import search_window

    settings_module.set("ui.language", "he")
    window = search_window.SearchWindow()
    window.prepare()
    assert window.getProperty("katan.search.charset") == "ABC"

    window.onClick(search_window.BUTTON_CHARSET)
    assert window.getProperty("katan.search.charset") == "123"


def test_english_is_one_press_away_from_hebrew(settings_module):
    """The order used to be Latin, Hebrew, digits, so a Hebrew interface -
    the one this opens on - offered the number pad as its next set and
    reached English only on the second press. Somebody looking for English
    pressed once, got digits, and reasonably concluded there was none."""
    from katan.ui import search_window

    settings_module.set("ui.language", "he")
    window = search_window.SearchWindow()
    window.prepare()
    assert window.charset == search_window.HEBREW

    window.onClick(search_window.BUTTON_CHARSET)

    assert window.charset == search_window.LATIN
    assert window.getProperty("katan.key0") == "a"


def test_the_switch_moves_between_the_alphabets_before_the_digits():
    from katan.ui import search_window

    order = [search_window.CHARSET_NAMES[i]
             for i in (search_window.HEBREW, search_window.LATIN,
                       search_window.DIGITS)]
    assert order == ["אבג", "ABC", "123"]


def test_every_charset_fills_the_grid(settings_module):
    """A key with no character used to be hidden, and a hidden control cannot
    take focus, which left the grid with nothing focused at all."""
    from katan.ui import search_window

    for charset in search_window.CHARSETS:
        assert len(charset) == search_window.KEY_COUNT


def test_a_home_with_nothing_in_it_is_still_navigable(monkeypatch,
                                                      settings_module):
    """Every row hidden means row zero is not there to focus either.

    The screen would be black with no way off it but the back button. The top
    bar is always visible, so search, tools and the settings that will fix it
    stay reachable. This became easier to reach once an empty row started
    being remembered as empty.
    """
    from katan import catalog
    from katan.meta import tmdb
    from katan.ui import home_window

    monkeypatch.setattr(tmdb, "has_key", lambda: True)
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: [])
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, refresh=False, page=1,
                        section=None: [])

    window = home_window.HomeWindow()
    window.prepare()
    window.onInit()

    assert window.getFocusId() == home_window.BUTTON_SEARCH
    assert window.getProperty("katan.hero.title"), \
        "and it should say something rather than sit blank"


# --------------------------------------------------------------------------
# moving between rows, which Kodi was not doing
# --------------------------------------------------------------------------


class MoveAction(object):
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


def _home_with_rows(monkeypatch, filled):
    """A home window whose slots hold exactly the rows named in `filled`."""
    from katan import catalog
    from katan.meta import tmdb
    from katan.ui import home_window

    monkeypatch.setattr(tmdb, "has_key", lambda: True)
    window = home_window.HomeWindow()
    window.rows = [{"id": "r%d" % n, "title_id": 0} for n in range(5)]
    window.data = {n: [{"title": "item %d" % n, "type": "movie",
                        "ids": {}, "art": {}}] for n in filled}
    assert catalog is not None
    return window


def test_down_moves_to_the_next_row(monkeypatch):
    """It did not. Six presses and focus never left the first row, so the
    main screen showed one row and everything below it was visible and
    unreachable with a remote."""
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1, 2])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1
    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 2


def test_up_moves_back_and_then_to_the_top_bar(monkeypatch):
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE + 1)

    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.LIST_BASE
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.BUTTON_SEARCH


def test_an_empty_row_is_stepped_over(monkeypatch):
    """A row that came back empty has had its heading cleared and is hidden,
    so focusing it would land on a control that is not there."""
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 3])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 3


def test_down_past_the_last_row_stays_put(monkeypatch):
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE + 1)
    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1


def test_down_from_the_top_bar_goes_into_the_content(monkeypatch):
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [2])
    window.setFocusId(home_window.BUTTON_SEARCH)
    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 2


# --------------------------------------------------------------------------
# rows that grow as they are scrolled
# --------------------------------------------------------------------------


def _scrollable_home(monkeypatch, pages, paged=True):
    """A home window with one row whose pages come from `pages`.

    `pages` maps a page number to the items that page returns, so a test can
    say what the second page holds, or that there is not one.
    """
    from katan import catalog
    from katan.meta import tmdb, trakt_state
    from katan.ui import home_window

    monkeypatch.setattr(tmdb, "has_key", lambda: True)
    monkeypatch.setattr(catalog, "has_more", lambda row_id: paged)
    monkeypatch.setattr(catalog, "row_title", lambda row: "Row")
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: list(pages.get(1, [])))
    monkeypatch.setattr(catalog, "enabled_rows",
                        lambda section=None: [
                            {"id": "r0", "title_id": 0, "loader": None,
                             "ttl": 60, "needs": [], "default": True,
                             "paged": paged}])
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, page=1, **kw: list(pages.get(page, [])))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    window = home_window.HomeWindow()
    window.onInit()
    return window


def test_a_row_grows_when_the_selection_nears_its_end(monkeypatch):
    """Scrolling right used to run into a wall after one page, with nothing
    to say there was more, so the catalogue looked far smaller than it is."""
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)
    assert control.size() == 20

    control.selectItem(18)                 # inside EXTEND_MARGIN of the end
    window._extend_ahead()
    _settle()
    window._absorb()                       # what the next keypress does

    assert control.size() == 40, "the next page should have been appended"
    assert len(window.data[0]) == 40
    assert window.pages[0] == 2


def test_a_page_is_never_added_from_the_worker_thread(monkeypatch):
    """The fetch happens off the GUI thread; the append must not.

    This is the bug the viewer hit: adding items to a list Kodi is currently
    navigating makes it reflow under the cursor. Measured in a real Kodi, one
    append moved the selection from item 56 to item 0 - scrolling right faster
    than the page loaded threw you back to the start of the row.
    """
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)

    control.selectItem(18)
    window._extend_ahead()
    _settle()

    assert control.size() == 20, \
        "the worker must not touch the control - only fetch"
    assert window.pending, "the page should be waiting for the GUI thread"

    window._absorb()
    assert control.size() == 40
    assert not window.pending


def test_the_cursor_does_not_move_when_a_row_grows(monkeypatch):
    """Scrolling fast must feel like waiting, never like being thrown out.

    The stub list is better behaved than Kodi's, so this makes it misbehave
    the way the real one was measured to: the append drops the selection back
    to the start. `_absorb` has to put it back.
    """
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)
    control.selectItem(18)

    original = control.addItems

    def reflows(items):
        original(items)
        control.position = 0          # what a real Kodi list did at item 56

    monkeypatch.setattr(control, "addItems", reflows)

    window._extend_ahead()
    _settle()
    window._absorb()

    assert control.size() == 40, "the page still arrives"
    assert control.getSelectedPosition() == 18, \
        "the viewer stays where they were scrolling, not back at the start"


def test_the_cursor_is_put_back_without_asking_whether_it_moved(monkeypatch):
    """The restore must not be guarded on reading the position first.

    In Kodi both addItems and selectItem post thread messages rather than
    acting on the control immediately, so a getSelectedPosition() taken just
    after an append reads the state *before* it. Guarding the restore on "has
    the position changed?" therefore answered no every time and the restore
    never ran - the first version of this fix looked right, passed its tests
    against a synchronous stub, and did nothing at all in a real Kodi.
    """
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)
    control.selectItem(18)

    selected = []
    monkeypatch.setattr(control, "selectItem", lambda n: selected.append(n))

    window._extend_ahead()
    _settle()
    window._absorb()

    assert selected == [18], (
        "the position must be restored whatever the control claims it is; "
        "got %s" % selected)


def test_a_resting_mouse_pointer_does_not_page_through_the_catalogue(
        monkeypatch):
    """Kodi sends mouse-move actions while the pointer merely sits there, and
    moves the selection on hover. A pointer left near the end of a row fetched
    page after page on its own - 94 items in a row nobody had touched, during
    start-up, with no input at all."""
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)
    control.selectItem(19)
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOUSE_MOVE))
    _settle()
    assert not window.pending, "hovering is not scrolling"

    window.onAction(MoveAction(home_window.ACTION_MOVE_RIGHT))
    _settle()
    assert window.pending, "but pressing right still grows the row"


def test_a_keypress_absorbs_a_page_that_arrived(monkeypatch):
    """The GUI thread only exists between callbacks, so a page waits for one."""
    from katan.ui import home_window

    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)
    control = window.getControl(home_window.LIST_BASE)

    control.selectItem(18)
    window._extend_ahead()
    _settle()
    assert control.size() == 20

    window.onAction(MoveAction(home_window.ACTION_MOVE_RIGHT))
    assert control.size() == 40, "the next keypress should take the new page"


def test_a_row_is_left_alone_until_the_end_is_in_sight(monkeypatch):
    """A row nobody scrolls must cost exactly one page."""
    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages)

    window.getControl(home_window.LIST_BASE).selectItem(2)
    assert window._wants_more(0) is False
    window._extend_ahead()
    _settle()
    assert len(window.data[0]) == 20


def test_a_row_that_cannot_page_never_asks(monkeypatch):
    """The live channels and the VOD catalogue are whole lists, not pages."""
    pages = {1: make_items(20, "one"), 2: make_items(20, "two")}
    window = _scrollable_home(monkeypatch, pages, paged=False)

    window.getControl(home_window.LIST_BASE).selectItem(19)
    assert window._wants_more(0) is False


def test_a_row_that_runs_out_stops_being_asked(monkeypatch):
    """TMDB answers past the last page with an empty list rather than an
    error, so an exhausted row would otherwise be re-fetched on every press."""
    from katan.ui import home_window

    calls = []
    pages = {1: make_items(20, "one")}
    window = _scrollable_home(monkeypatch, pages)

    from katan import catalog
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, page=1, **kw: calls.append(page) or [])

    window.getControl(home_window.LIST_BASE).selectItem(19)
    window._extend_ahead()
    _settle()
    window._absorb()
    assert calls == [2]

    window._extend_ahead()
    _settle()
    assert calls == [2], "an exhausted row must not be asked again"
    assert window._wants_more(0) is False


def test_a_page_that_repeats_what_we_have_is_not_appended(monkeypatch):
    """A trending list reshuffles between requests and hands back items the
    row already holds. Appending those would grow the row with duplicates."""
    from katan.ui import home_window

    first = make_items(20, "one")
    window = _scrollable_home(monkeypatch, {1: first, 2: list(first)})

    window.getControl(home_window.LIST_BASE).selectItem(19)
    window._extend_ahead()
    _settle()
    window._absorb()

    assert len(window.data[0]) == 20


def test_one_repeated_page_does_not_end_the_row(monkeypatch):
    """This is what stopped the trending rows after a single page.

    Trending reshuffles, so page two legitimately repeats much of page one -
    and trending is the first row of both the Films and Series tabs, so that
    one duplicate page killed scrolling on the row most likely to be
    scrolled. It steps over the page and carries on.
    """
    from katan.ui import home_window

    first = make_items(20, "one")
    window = _scrollable_home(monkeypatch, {1: first, 2: list(first),
                                            3: make_items(20, "three")})

    window.getControl(home_window.LIST_BASE).selectItem(19)
    window._extend_ahead()
    _settle()
    window._absorb()
    assert 0 not in window.exhausted, "one repeated page is not the end"
    assert window.pages[0] == 2, "and the row moved past it"

    # The next attempt reaches page three, which has something new.
    window._extend_ahead()
    _settle()
    window._absorb()
    assert len(window.data[0]) == 40


def test_a_row_that_keeps_repeating_is_eventually_finished(monkeypatch):
    """A list that has genuinely run out should stop being asked."""
    from katan.ui import home_window

    first = make_items(20, "one")
    pages = {n: list(first) for n in range(1, 12)}
    window = _scrollable_home(monkeypatch, pages)

    window.getControl(home_window.LIST_BASE).selectItem(19)
    for _ in range(home_window.BARREN_PAGES):
        window._extend_ahead()
        _settle()
        window._absorb()

    assert 0 in window.exhausted
    assert len(window.data[0]) == 20


def test_a_row_stops_growing_at_the_ceiling(monkeypatch):
    """"Infinite" on a device with a gigabyte of RAM ends in a killed
    process, so the row has a ceiling."""
    from katan.ui import home_window

    window = _scrollable_home(monkeypatch, {1: make_items(20, "one")})
    window.data[0] = make_items(home_window.MAX_ITEMS, "many")
    window.getControl(home_window.LIST_BASE).selectItem(
        home_window.MAX_ITEMS - 1)
    assert window._wants_more(0) is False


def test_a_row_that_fails_to_grow_still_works(monkeypatch):
    """One page that will not load is not a reason to break the row."""
    from katan import catalog
    from katan.ui import home_window

    window = _scrollable_home(monkeypatch, {1: make_items(20, "one")})

    def explode(row_id, page=1, **kw):
        raise ValueError("TMDB is having a moment")

    monkeypatch.setattr(catalog, "load", explode)
    window.getControl(home_window.LIST_BASE).selectItem(19)
    window._extend_ahead()
    _settle()
    window._absorb()

    assert len(window.data[0]) == 20, "the row keeps what it already had"
    assert 0 not in window.exhausted, (
        "one failure is not the end of a row - a request can time out on a "
        "wireless projector and mean nothing at all")

    for _ in range(home_window.BARREN_PAGES):
        window._extend_ahead()
        _settle()
    assert 0 in window.exhausted, "but it does give up eventually"


def _settle():
    """Wait for the worker thread the window starts to finish."""
    import threading
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        workers = [t for t in threading.enumerate()
                   if t is not threading.current_thread() and t.daemon
                   and t.is_alive()]
        if not workers:
            return
        for worker in workers:
            worker.join(0.05)
    raise AssertionError("a home window worker never finished")


# --------------------------------------------------------------------------
# back on the home screen
# --------------------------------------------------------------------------


class BackAction(object):
    def getId(self):
        from katan.ui import home_window
        return home_window.ACTION_NAV_BACK


def test_back_leaves_katan_by_default(monkeypatch, settings_module):
    """Nobody's Kodi is taken over unless they asked for it."""
    from katan.ui import home_window

    window = _home_with_rows(monkeypatch, [0])
    assert settings_module.get_bool("ui.stay_in_katan") is False
    window.onAction(BackAction())
    assert window.closed is True


def test_back_stays_put_when_asked(monkeypatch, settings_module):
    """On a box that exists to run this add-on, backing out of the home screen
    lands on the Kodi interface this add-on replaces."""
    from katan.ui import home_window

    settings_module.set("ui.stay_in_katan", "true")
    window = _home_with_rows(monkeypatch, [0])
    window.onAction(BackAction())
    window.onAction(BackAction())
    assert window.closed is False


def test_staying_put_does_not_break_the_rest_of_the_window(monkeypatch,
                                                            settings_module):
    """Only the home screen holds on. Everything inside Katan still goes back,
    and the rest of the window still works - including the way out, which is
    the settings entry at the bottom of the rail."""
    from katan.ui import home_window

    settings_module.set("ui.stay_in_katan", "true")
    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.BUTTON_SEARCH, \
        "the top bar is still reachable"

    opened = []
    from katan import settings as settings_mod
    monkeypatch.setattr(settings_mod, "open_settings",
                        lambda *a, **k: opened.append(True))
    window.onClick(home_window.BUTTON_RAIL_SETTINGS)
    assert opened, "the switch that turns this off has to stay reachable"


def test_pressing_ok_on_a_key_does_not_run_a_search(search):
    """ACTION_SELECT_ITEM is 7, and it was named ACTION_ENTER here - so every
    press on the key grid submitted as well as typing. `_submit` wants two
    characters, so you could type exactly two and the *third* key closed the
    window and searched for the fragment. The keyboard looked broken because
    it was."""
    for _ in range(4):
        search.onAction(Kodi21Action(7))            # ACTION_SELECT_ITEM
        search.onClick(search_window.KEY_BASE)

    assert search.submitted is None, "OK on a letter ran a search"
    assert search.text == "aaaa"


def test_enter_still_submits(search):
    """135 is Kodi's real ACTION_ENTER, and a physical keyboard sends it."""
    search.onClick(search_window.KEY_BASE)
    search.onClick(search_window.KEY_BASE)
    search.onAction(Kodi21Action(search_window.ACTION_ENTER))

    assert search.submitted == "aa"


def test_the_search_button_still_submits(search):
    search.onClick(search_window.KEY_BASE)
    search.onClick(search_window.KEY_BASE)
    search.onClick(search_window.BUTTON_SEARCH)

    assert search.submitted == "aa"


def test_a_bundled_tmdb_key_is_used_when_the_setting_is_empty(monkeypatch,
                                                              settings_module):
    """Shipping a key is what makes a fresh install show the Films tab."""
    from katan.meta import tmdb

    settings_module.set("tmdb.apikey", "")
    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "shipped-key")
    assert tmdb.api_key() == "shipped-key"
    assert tmdb.has_key() is True


def test_the_setting_beats_the_bundled_key(monkeypatch, settings_module):
    """Anyone who wants their own quota just enters theirs."""
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "shipped-key")
    settings_module.set("tmdb.apikey", "my-own-key")
    assert tmdb.api_key() == "my-own-key"


def test_an_empty_tab_clears_the_hero_it_inherited(monkeypatch):
    """Setting only the title left the previous tab's plot and backdrop, so an
    empty Films tab read "Nothing to show right now" over another show's
    synopsis - which looks like a bug in the thing that is working."""
    from katan import catalog

    window = home_window.HomeWindow()
    window._show_hero({"title": "Attack on Titan", "plot": "Centuries ago...",
                       "year": 2013, "rating": 8.4,
                       "art": {"fanart": "aot.jpg"}})
    assert window.getProperty("katan.hero.plot")

    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])
    window.rows = []
    window._focus_first_row()

    assert window.getProperty("katan.hero.title")
    assert window.getProperty("katan.hero.plot") == ""
    assert window.getProperty("katan.hero.meta") == ""
    assert window.getProperty("katan.hero.fanart") == ""


def test_the_shipped_key_actually_reaches_the_catalog(monkeypatch):
    """The point of shipping a key: a fresh install has content in every tab.

    The suite blanks BUNDLED_KEY so it can still test the no-key path, so this
    is the one place that puts it back and checks it does what it is for.
    """
    from katan import catalog
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "a-shipped-key")
    assert tmdb.has_key(), "the bundled key should apply with nothing configured"

    for section in catalog.SECTION_ORDER:
        assert catalog.enabled_rows(section), "%s tab is empty" % section

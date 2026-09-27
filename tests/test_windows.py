"""The custom home and search windows.

These are the main interface, so an error in them is a black screen. The stub
WindowXML records controls and properties, which is enough to check that rows
are filled, the hero updates, and typing produces suggestions.
"""
import pytest

from pinky.ui import home_window, search_window


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
    from pinky.meta import items
    offset = sum((index + 1) * ord(char) for index, char in enumerate(prefix)) * 100
    return [items.new_item("movie", ids={"tmdb": offset + index + 1},
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
    from pinky.meta import tmdb
    monkeypatch.setattr(tmdb, "has_key", lambda: True)


@pytest.fixture
def home(monkeypatch, configured):
    from pinky import catalog
    from pinky.meta import trakt_state

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
    from pinky import catalog
    from pinky.meta import trakt_state

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
    assert window.getProperty("pinky.hero.title"), "the hero should be seeded"


def test_onInit_still_only_runs_once(home):
    """It fires again when a dialog closes; the second pass must be a no-op."""
    before = dict(home.data)
    home.onInit()
    assert home.data == before


def test_home_fills_its_rows_and_sets_headings(home):
    assert len(home.rows) == 4
    assert home.getProperty("pinky.row0.title") == "Row row0"
    first = home.getControl(home_window.LIST_BASE)
    assert first.size() == 5


def test_home_only_preloads_the_rows_near_the_top(home):
    """A long home page must not cost more than a short one."""
    assert len(home.filled) <= home_window.PRELOAD_ROWS + 1


def test_opening_the_addon_goes_straight_to_the_pinky_window(monkeypatch,
                                                             settings_module):
    """Entering the add-on should land in the Pinky GUI, not a Kodi file list."""
    from pinky.meta import tmdb
    from pinky.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(tmdb, "has_key", lambda: True)

    opened = []
    import pinky.ui.home_window as hw
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
    from pinky import catalog
    from pinky.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])

    opened = []
    import pinky.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert not opened, "an empty window is worse than a listing that explains"


def test_a_fresh_install_still_gets_the_window(monkeypatch, settings_module):
    """No key configured, but the Israeli and anime rows need none."""
    from pinky.meta import tmdb
    from pinky.ui import handlers

    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(tmdb, "has_key", lambda: False)

    opened = []
    import pinky.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    handlers.home({})
    assert opened, "a first-time viewer was shown a file list"


def test_finishing_setup_opens_the_window(monkeypatch, settings_module):
    """Setup is not the destination; finishing it should show the GUI."""
    from pinky import catalog
    from pinky.ui import wizard

    settings_module.set("tmdb.apikey", "a-key")
    settings_module.set("ui.window_home", "true")
    monkeypatch.setattr(catalog, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(catalog, "warm", lambda *a, **kw: 0)

    opened = []
    import pinky.ui.home_window as hw
    monkeypatch.setattr(hw, "open_home", lambda: opened.append(True))

    wizard._finish()
    assert opened, "the viewer should end up in the GUI, not back in a list"


def test_abandoning_setup_does_not_loop_back_into_the_window(monkeypatch,
                                                            settings_module):
    """No key means no window, or declining setup would reopen it forever."""
    from pinky import catalog
    from pinky.ui import wizard

    settings_module.set("tmdb.apikey", "")
    monkeypatch.setattr(catalog, "invalidate", lambda *a, **kw: None)
    monkeypatch.setattr(catalog, "warm", lambda *a, **kw: 0)

    opened = []
    import pinky.ui.home_window as hw
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
    from pinky import kodi
    from pinky.meta import tmdb

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
    from pinky import catalog

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
    from pinky import catalog
    from pinky.meta import trakt_state

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
    assert window.getProperty("pinky.hero.title"), \
        "the hero should come from the first row that actually has something"


def test_home_updates_the_hero_from_the_focused_item(home):
    home.setFocusId(home_window.LIST_BASE)
    home.onAction(FakeAction(home_window.ACTION_MOVE_RIGHT))
    assert home.getProperty("pinky.hero.title") == "row0 0"
    assert home.getProperty("pinky.hero.fanart") == "f.jpg"
    assert "2020" in home.getProperty("pinky.hero.meta")


def test_the_hero_backdrop_is_left_empty_without_a_real_fanart(home):
    """A poster or a channel logo stretched to 16:9 looks broken."""
    home._show_hero({"title": "A channel", "art": {"poster": "logo.png"}})
    assert home.getProperty("pinky.hero.fanart") == ""
    assert home.getProperty("pinky.hero.title") == "A channel"


def test_home_fills_further_rows_as_focus_moves(home):
    before = len(home.filled)
    home.setFocusId(home_window.LIST_BASE + 3)
    home.onAction(FakeAction(home_window.ACTION_MOVE_DOWN))
    assert len(home.filled) >= before


def test_an_empty_row_hides_itself(monkeypatch):
    from pinky import catalog
    from pinky.meta import trakt_state

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
    assert window.getProperty("pinky.row0.title") == "", "an empty row should hide"


def test_back_does_not_close_the_home_window(home, settings_module):
    """Escape used to drop the viewer into the Kodi interface this replaces.

    The home screen is the one place back has nowhere good to go. It still
    works everywhere inside Pinky - out of a film, out of the picker, out of
    a season - because those are places you can be finished with.
    """
    home.onAction(FakeAction(home_window.ACTION_NAV_BACK))
    assert home.closed is False

    settings_module.set("ui.stay_in_pinky", "false")
    home.onAction(FakeAction(home_window.ACTION_NAV_BACK))
    assert home.closed is True


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


@pytest.fixture
def search(monkeypatch):
    from pinky.search import unified

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




def _type_into_field(search, text, action_id=0):
    """What Kodi does with a physical keyboard: the edit control takes the key
    first, then the window's onAction runs with the field already updated."""
    search.setFocusId(search_window.EDIT_QUERY)
    search.getControl(search_window.EDIT_QUERY).setText(text)
    search.onAction(Kodi21Action(action_id))


def test_search_opens_ready_to_type(search):
    """With the grid focused, a keyboard's first keys run the keymap: measured
    in a real Kodi, Backspace was Back and walked out of the add-on."""
    assert search.getFocusId() == search_window.EDIT_QUERY


def test_a_physical_keyboard_types_into_the_field(search):
    """Kodi 21 gives an action no character, so the grid was the only input
    and a letter typed on a keyboard ran a keymap shortcut instead - one of
    them opens the PVR channel list. A focused edit control is the one place
    Kodi delivers keys as text."""
    _type_into_field(search, "d")
    _type_into_field(search, "du")
    assert search.text == "du"
    assert search.getProperty("pinky.search.text") == "du"


def test_backspace_in_the_field_deletes_one_character_not_two(search):
    _type_into_field(search, "dune")
    _type_into_field(search, "dun", search_window.ACTION_BACKSPACE)
    assert search.text == "dun"


def test_enter_in_the_field_searches_for_what_was_typed(search):
    _type_into_field(search, "dune")
    _type_into_field(search, "dune", search_window.ACTION_ENTER)
    assert search.submitted == "dune"


def test_enter_on_a_keyboard_arrives_as_select_and_still_searches(search):
    """Measured in Kodi 21: the Return key reaches the field as action 7."""
    _type_into_field(search, "hikaru")
    _type_into_field(search, "hikaru", search_window.ACTION_SELECT_ITEM)
    assert search.submitted == "hikaru"


def test_ok_on_an_empty_field_leaves_kodis_keyboard_open(search):
    """A remote pressing OK on the field wants to type, not to search."""
    _type_into_field(search, "", search_window.ACTION_SELECT_ITEM)
    assert search.submitted is None
    assert search.closed is not True



def test_typing_survives_a_kodi_with_no_getUnicode(search):
    """Kodi 21's Action has no getUnicode, and calling it threw on every key.

    The on-screen grid is the input that always works, so an action the build
    cannot decode has to be ignored rather than fatal.
    """
    search.onAction(Kodi21Action())
    search.onAction(Kodi21Action())
    assert search.text == "", "an undecodable action types nothing"

    _type_into_field(search, "a")
    assert search.text == "a", "the field must still work"



def test_a_physical_keyboard_also_types(search):
    search.setFocusId(search_window.BUTTON_SEARCH)  # not the field
    search.onAction(FakeAction(unicode_char="d"))
    search.onAction(FakeAction(unicode_char="u"))
    assert search.text == "du"



def test_suggestions_never_overwrite_what_was_typed(search):
    """Autocomplete offers, it does not complete for you."""
    search.setFocusId(search_window.BUTTON_SEARCH)  # not the field
    for char in "dun":
        search.onAction(FakeAction(unicode_char=char))
    search._schedule_suggestions()
    import time
    time.sleep(0.5)
    assert search.text == "dun", "the typed text must survive suggestions"


def test_submitting_searches_here_rather_than_handing_kodi_the_query(search):
    """Searching used to close this window and hand the query to the
    `search_query` directory route, which Kodi draws in its own video browser.
    That browser has no details window and no "Choose a source" button, so a
    search could not reach the source picker at all - while picking a
    *suggestion* could, because that path comes back through our own window.
    So the feature looked fine and failed only when somebody searched."""
    search.setFocusId(search_window.BUTTON_SEARCH)  # not the field
    for char in "dune":
        search.onAction(FakeAction(unicode_char=char))
    search.onClick(search_window.BUTTON_SEARCH)
    assert search.submitted == "dune"
    assert search.closed is False, "the results belong in this window"


def test_a_query_that_is_too_short_is_not_submitted(search):
    search.setFocusId(search_window.BUTTON_SEARCH)  # not the field
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








def test_a_home_with_nothing_in_it_is_still_navigable(monkeypatch,
                                                      settings_module):
    """Every row hidden means row zero is not there to focus either.

    The screen would be black with no way off it but the back button. The top
    bar is always visible, so search, tools and the settings that will fix it
    stay reachable. This became easier to reach once an empty row started
    being remembered as empty.
    """
    from pinky import catalog
    from pinky.meta import tmdb
    from pinky.ui import home_window

    monkeypatch.setattr(tmdb, "has_key", lambda: True)
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: [])
    monkeypatch.setattr(catalog, "load",
                        lambda row_id, refresh=False, page=1,
                        section=None: [])

    window = home_window.HomeWindow()
    window.prepare()
    window.onInit()

    assert window.getFocusId() == home_window.BUTTON_SEARCH
    assert window.getProperty("pinky.hero.title"), \
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
    from pinky import catalog
    from pinky.meta import tmdb
    from pinky.ui import home_window

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
    from pinky.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1, 2])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1
    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 2


def test_up_moves_back_and_then_to_the_top_bar(monkeypatch):
    from pinky.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE + 1)

    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.LIST_BASE
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.BUTTON_SEARCH


def test_an_empty_row_is_stepped_over(monkeypatch):
    """A row that came back empty has had its heading cleared and is hidden,
    so focusing it would land on a control that is not there."""
    from pinky.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 3])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 3


def test_down_past_the_last_row_stays_put(monkeypatch):
    from pinky.ui import home_window

    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE + 1)
    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1


def test_down_from_the_top_bar_goes_into_the_content(monkeypatch):
    from pinky.ui import home_window

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
    from pinky import catalog
    from pinky.meta import tmdb, trakt_state
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

    calls = []
    pages = {1: make_items(20, "one")}
    window = _scrollable_home(monkeypatch, pages)

    from pinky import catalog
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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

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
    from pinky.ui import home_window

    window = _scrollable_home(monkeypatch, {1: make_items(20, "one")})
    window.data[0] = make_items(home_window.MAX_ITEMS, "many")
    window.getControl(home_window.LIST_BASE).selectItem(
        home_window.MAX_ITEMS - 1)
    assert window._wants_more(0) is False


def test_a_row_that_fails_to_grow_still_works(monkeypatch):
    """One page that will not load is not a reason to break the row."""
    from pinky import catalog
    from pinky.ui import home_window

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
    """Wait for the worker thread the window starts to finish.

    By name, and that matters. Waiting for every daemon thread in the process
    passed here and failed twelve tests in CI, because a ThreadPoolExecutor's
    workers are daemon threads under Python 3.8 - what Kodi 21 ships and what
    CI runs - and non-daemon from 3.9 on. The shared HTTP pool is process-wide
    and deliberately outlives any one call, so once anything had used it those
    threads were alive forever and this could never return.
    """
    import threading
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        workers = [t for t in threading.enumerate()
                   if t is not threading.current_thread() and t.is_alive()
                   and t.name == "pinky-home-worker"]
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
        from pinky.ui import home_window
        return home_window.ACTION_NAV_BACK


def test_back_stays_in_pinky_by_default(monkeypatch, settings_module):
    """Because backing out of the home screen has nowhere good to go.

    This add-on is the interface on the box it was written for, so the thing
    behind its home screen is the Kodi interface it replaces. Leaving by
    accident - one press of Escape - put people somewhere nobody meant to be.
    """

    window = _home_with_rows(monkeypatch, [0])
    assert settings_module.get_bool("ui.stay_in_pinky") is True
    window.onAction(BackAction())
    assert window.closed is False


def test_old_home_page_cannot_land_after_section_switch(monkeypatch):
    import threading
    from pinky import catalog
    from pinky.meta import trakt_state
    from pinky.ui import home_window
    old_item = {"type": "movie", "title": "OLD", "ids": {}, "art": {}}
    new_item = {"type": "movie", "title": "NEW", "ids": {}, "art": {}}
    rows = {"movies": [{"id": "old", "title_id": 0}],
            "tv": [{"id": "new", "title_id": 0}]}
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(catalog, "enabled_rows", lambda section=None: rows[section])
    monkeypatch.setattr(catalog, "row_title", lambda row: row["id"])
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None:
                        [new_item] if row_id == "new" else [old_item])

    def load(row_id, page=1, section=None, **_kwargs):
        if row_id == "old" and page == 2:
            entered.set()
            release.wait(2)
            return [dict(old_item, title="OLD PAGE 2")]
        return [new_item] if row_id == "new" else [old_item]

    monkeypatch.setattr(catalog, "load", load)
    monkeypatch.setattr(catalog, "has_more", lambda _row_id: True)
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)
    window = home_window.HomeWindow()
    window.section = "movies"
    window.rows = rows["movies"]
    window.data[0] = [old_item]
    window.pages[0] = 1
    worker = threading.Thread(target=window._extend, args=(0,))
    worker.start()
    assert entered.wait(1)
    window._switch_section("tv")
    release.set()
    worker.join(1)
    window._absorb()
    assert [item["title"] for item in window.data[0]] == ["NEW"]


def test_clearing_short_query_invalidates_inflight_suggestions(monkeypatch):
    import threading
    import time
    from pinky.search import unified
    from pinky.ui import search_window
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(search_window.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(unified, "recent", lambda: ["recent"])

    def suggest(_query):
        entered.set()
        release.wait(2)
        return [{"type": "movie", "title": "stale", "ids": {}, "art": {}}]

    monkeypatch.setattr(unified, "suggest", suggest)
    window = search_window.SearchWindow()
    window._set_text("ab")
    assert entered.wait(1)
    window._set_text("")
    window._show_recent()
    release.set()
    time.sleep(0.02)
    assert window.entries == [{"type": "query", "title": "recent"}]


def test_back_still_leaves_when_the_switch_is_off(monkeypatch, settings_module):
    """A door with no handle on the inside is worse than the problem it solves."""

    settings_module.set("ui.stay_in_pinky", "false")
    window = _home_with_rows(monkeypatch, [0])
    window.onAction(BackAction())
    assert window.closed is True


def test_back_stays_put_when_asked(monkeypatch, settings_module):
    """On a box that exists to run this add-on, backing out of the home screen
    lands on the Kodi interface this add-on replaces."""

    settings_module.set("ui.stay_in_pinky", "true")
    window = _home_with_rows(monkeypatch, [0])
    window.onAction(BackAction())
    window.onAction(BackAction())
    assert window.closed is False


def test_staying_put_does_not_break_the_rest_of_the_window(monkeypatch,
                                                            settings_module):
    """Only the home screen holds on. Everything inside Pinky still goes back,
    and the rest of the window still works - including the way out, which is
    the settings entry at the bottom of the rail."""
    from pinky.ui import home_window

    settings_module.set("ui.stay_in_pinky", "true")
    window = _home_with_rows(monkeypatch, [0, 1])
    window.setFocusId(home_window.LIST_BASE)

    window.onAction(MoveAction(home_window.ACTION_MOVE_DOWN))
    assert window.getFocusId() == home_window.LIST_BASE + 1
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    window.onAction(MoveAction(home_window.ACTION_MOVE_UP))
    assert window.getFocusId() == home_window.BUTTON_SEARCH, \
        "the top bar is still reachable"

    # The way out is one press further in than it used to be: the rail button
    # opens Tools, and the settings dialog holding the switch is one of its
    # entries. A door with no handle on the inside would be worse than the
    # problem staying put solves, so the handle is what is checked here.
    from pinky.ui import handlers

    # By what it is, not where it sits: picking index 2 broke the moment an
    # entry above it was removed, and picked the kids toggle instead.
    settings_at = [n for n, (_s, url, _g) in enumerate(handlers.tool_entries())
                   if "action=open_settings" in url][0]
    offered = {}
    ran = []
    monkeypatch.setattr(home_window.kodi, "select",
                        lambda labels, heading="", **kw:
                        offered.setdefault("labels", labels) is None or settings_at)
    monkeypatch.setattr(home_window.kodi, "run_builtin", ran.append)
    window.onClick(home_window.BUTTON_RAIL_SETTINGS)

    assert offered["labels"], "the rail button has to offer something"
    assert ran and "action=open_settings" in ran[0],         "the switch that turns this off has to stay reachable"


def test_the_rail_button_reaches_everything_tools_offers(monkeypatch,
                                                         settings_module):
    """It used to open the settings dialog and nothing else.

    So the setup wizard, the accounts screen, the device report and checking
    for an update were reachable only from the plain directory listing - which
    the dashboard never shows. They were, in effect, not there.
    """
    from pinky.ui import handlers

    window = _home_with_rows(monkeypatch, [0])
    offered = {}
    monkeypatch.setattr(home_window.kodi, "select",
                        lambda labels, heading="", **kw:
                        offered.setdefault("labels", labels) is None or -1)
    monkeypatch.setattr(home_window.kodi, "run_builtin", lambda command: None)
    window.onClick(home_window.BUTTON_RAIL_SETTINGS)

    assert len(offered["labels"]) == len(handlers.tool_entries())

    # Nothing may be stranded: every entry is either something to run or a
    # group that leads to more, and the maintenance group is the only one.
    reachable = []
    for _string_id, url, is_group in handlers.tool_entries():
        if is_group:
            reachable.extend(u for _s, u, _g
                             in handlers.tool_entries(url.rsplit("group=", 1)[-1]))
        else:
            reachable.append(url)
    for action in ("check_update", "diagnostics", "clear_cache",
                   "open_settings", "kids_toggle"):
        assert any("action=%s" % action in url for url in reachable),             "%s is not reachable from the dashboard" % action

    # Accounts is deliberately absent. It is in the settings dialog, where
    # every service also has its own connect button, so a Tools entry would
    # repeat what is one press away.
    assert not any("action=accounts" in url for url in reachable), \
        "Accounts is back in Tools; it belongs in the settings dialog"


def test_a_group_opens_another_list_rather_than_a_directory(monkeypatch,
                                                            settings_module):
    """Opening the directory would navigate out of the window."""
    from pinky.ui import handlers

    window = _home_with_rows(monkeypatch, [0])
    seen = []
    ran = []
    # Choose the last entry - the group - then the first thing inside it.
    monkeypatch.setattr(home_window.kodi, "select",
                        lambda labels, heading="", **kw:
                        seen.append(list(labels)) or (len(labels) - 1
                                                      if len(seen) == 1 else 0))
    monkeypatch.setattr(home_window.kodi, "run_builtin", ran.append)
    window.onClick(home_window.BUTTON_RAIL_SETTINGS)

    assert len(seen) == 2, "the group has to open a second list"
    assert len(seen[1]) == len(handlers.tool_entries("maintenance"))
    assert ran and "RunPlugin(" in ran[0]
    assert "action=tools" not in ran[0], "a group must not be run as a directory"



def test_enter_still_submits(search):
    """135 is Kodi's real ACTION_ENTER. A physical keyboard's Return, measured
    on Kodi 21, sends 7 instead - see the Select test beside the field ones."""
    _type_into_field(search, "aa")
    search.setFocusId(search_window.BUTTON_SEARCH)
    search.onAction(Kodi21Action(search_window.ACTION_ENTER))

    assert search.submitted == "aa"


def test_the_search_button_still_submits(search):
    _type_into_field(search, "aa")
    search.onClick(search_window.BUTTON_SEARCH)

    assert search.submitted == "aa"


def test_a_bundled_tmdb_key_is_used_when_the_setting_is_empty(monkeypatch,
                                                              settings_module):
    """Shipping a key is what makes a fresh install show the Films tab."""
    from pinky.meta import tmdb

    settings_module.set("tmdb.apikey", "")
    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "shipped-key")
    assert tmdb.api_key() == "shipped-key"
    assert tmdb.has_key() is True


def test_the_setting_beats_the_bundled_key(monkeypatch, settings_module):
    """Anyone who wants their own quota just enters theirs."""
    from pinky.meta import tmdb

    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "shipped-key")
    settings_module.set("tmdb.apikey", "my-own-key")
    assert tmdb.api_key() == "my-own-key"


def test_an_empty_tab_clears_the_hero_it_inherited(monkeypatch):
    """Setting only the title left the previous tab's plot and backdrop, so an
    empty Films tab read "Nothing to show right now" over another show's
    synopsis - which looks like a bug in the thing that is working."""
    from pinky import catalog

    window = home_window.HomeWindow()
    window._show_hero({"title": "Attack on Titan", "plot": "Centuries ago...",
                       "year": 2013, "rating": 8.4,
                       "art": {"fanart": "aot.jpg"}})
    assert window.getProperty("pinky.hero.plot")

    monkeypatch.setattr(catalog, "enabled_rows", lambda *a, **k: [])
    window.rows = []
    window._focus_first_row()

    assert window.getProperty("pinky.hero.title")
    assert window.getProperty("pinky.hero.plot") == ""
    assert window.getProperty("pinky.hero.meta") == ""
    assert window.getProperty("pinky.hero.fanart") == ""


def test_the_shipped_key_actually_reaches_the_catalog(monkeypatch):
    """The point of shipping a key: a fresh install has content in every tab.

    The suite blanks BUNDLED_KEY so it can still test the no-key path, so this
    is the one place that puts it back and checks it does what it is for.
    """
    from pinky import catalog
    from pinky.meta import tmdb

    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "a-shipped-key")
    assert tmdb.has_key(), "the bundled key should apply with nothing configured"

    for section in catalog.SECTION_ORDER:
        assert catalog.enabled_rows(section), "%s tab is empty" % section


def test_the_home_screen_says_which_version_it_is(monkeypatch, settings_module):
    """Because the first question after any update is whether it took.

    Answering it used to mean opening a settings dialog or reading the log,
    on a device driven by a remote. Set in prepare() rather than onInit,
    which is where this window sets every property - a control Kodi has not
    yet decided is visible cannot be addressed.
    """
    from pinky import catalog
    from pinky.meta import tmdb

    monkeypatch.setattr(tmdb, "has_key", lambda: True)
    monkeypatch.setattr(catalog, "peek", lambda row_id, section=None: [])
    window = home_window.HomeWindow()
    window.prepare()

    shown = window.getProperty("pinky.version")
    assert shown, "the version has to be on screen"
    assert home_window.kodi.addon_version() in shown


# --------------------------------------------------------------------------
# the search keyboard is a hand-placed grid, so it needs hand-written navigation
# --------------------------------------------------------------------------


def test_every_key_on_the_search_keyboard_can_be_reached():
    """Arrows moved nowhere, because no button said where to go.

    Kodi derives navigation only inside a list or grouplist. These
    thirty-five buttons are absolutely positioned, so whatever the skin
    declares is all there is - and it declared nothing, which left the only
    usable key the one that happened to be focused.

    This reads the skin rather than the code: the bug was in the XML and a
    test of the Python would have passed throughout.
    """
    import os
    import xml.etree.ElementTree as ET

    from conftest import ROOT

    skin = os.path.join(ROOT, "plugin.video.pinky", "resources", "skins",
                        "default", "1080i", "pinky-search.xml")
    root = ET.parse(skin).getroot()

    buttons = {}
    every_id = set()
    for control in root.iter("control"):
        control_id = control.get("id")
        if control_id:
            every_id.add(int(control_id))
        if control.get("type") == "button" and control_id:
            buttons[int(control_id)] = control

    assert buttons, "no buttons found - has the skin been restructured?"

    unreachable = []
    for control_id, control in sorted(buttons.items()):
        for direction in ("onleft", "onright", "onup", "ondown"):
            node = control.find(direction)
            if node is None or not (node.text or "").strip():
                unreachable.append("%d has no %s" % (control_id, direction))
                continue
            target = int(node.text.strip())
            if target not in every_id:
                unreachable.append("%d %s points at %d, which does not exist"
                                   % (control_id, direction, target))
    assert not unreachable, "; ".join(unreachable[:8])

    # And every key must be arrived at from somewhere, or it can be seen and
    # never focused.
    arrived_at = set()
    for control in buttons.values():
        for direction in ("onleft", "onright", "onup", "ondown"):
            node = control.find(direction)
            if node is not None and node.text:
                arrived_at.add(int(node.text.strip()))
    orphans = sorted(set(buttons) - arrived_at)
    assert not orphans, "no arrow reaches %s" % orphans


def test_the_home_window_closes_when_kodi_is_shutting_down(monkeypatch):
    """Quitting Kodi used to leave the process alive, holding its files.

    `doModal` blocks until the window closes, and Kodi's abort does not close
    a window - so the plugin invocation holding the home window sat there:

        main.py: trigger Monitor abort request
        main.py: script didn't stop in 5 seconds - let's kill it

    The symptom was not a log line. Kodi appeared to close, the old process
    kept running, and starting Kodi again found it still holding its files and
    would not open at all.

    Only testable since the stub learned to signal an abort; before that
    `abortRequested` was hard-coded False and this whole class of bug was
    unreachable from the suite.
    """
    import threading
    import xbmc
    from pinky.ui import home_window

    closed = threading.Event()

    class FakeWindow(object):
        def close(self):
            closed.set()

    stop = threading.Event()
    xbmc.Monitor.ABORT = True
    try:
        thread = home_window._close_on_abort(FakeWindow(), stop)
        thread.join(2.0)
        assert closed.is_set(), "the window was not closed on shutdown"
    finally:
        xbmc.Monitor.ABORT = False
        stop.set()


def test_the_quit_watcher_stops_when_the_window_closes_normally(monkeypatch):
    """It must not outlive the window it watches: the thread is a daemon, but
    a thread per playback that never ends is still a leak."""
    import threading
    import xbmc
    from pinky.ui import home_window

    class FakeWindow(object):
        def close(self):
            raise AssertionError("closed a window nobody asked to close")

    xbmc.Monitor.ABORT = False
    stop = threading.Event()
    thread = home_window._close_on_abort(FakeWindow(), stop)
    stop.set()
    thread.join(2.0)
    assert not thread.is_alive(), "the watcher outlived its window"


def test_the_search_history_survives_the_cache_being_cleared(settings_module):
    """It was a cache row with a ninety day expiry, and the cache is
    size-capped with LRU eviction - so a busy evening of artwork and source
    lists could drop somebody's searches to make room. Everything else in
    there can be fetched again; this is the only copy."""
    from pinky import cache
    from pinky.search import unified

    unified.remember("hikaru no go")
    unified.remember(u"רמזור")
    cache.clear()

    assert unified.recent()[:2] == [u"רמזור", "hikaru no go"]


def test_a_repeated_search_moves_to_the_front_rather_than_doubling(settings_module):
    from pinky.search import unified

    unified.remember("black lagoon")
    unified.remember("top gun")
    unified.remember("BLACK LAGOON")
    assert unified.recent()[0] == "BLACK LAGOON"
    assert len([q for q in unified.recent() if q.lower() == "black lagoon"]) == 1


# --------------------------------------------------------------------------
# the microphone, which is Kodi's rather than ours
# --------------------------------------------------------------------------


def test_speak_hands_the_field_to_kodis_own_keyboard(search, monkeypatch):
    """The grid was written believing a television has no microphone this
    add-on can reach, so it served a page on the local network and drew a QR
    code for a phone to dictate into. The belief was wrong: Kodi's keyboard
    on Android hands its field to the system IME, and holding OK there starts
    Android's dictation. There is no API for it - there is only Kodi's
    keyboard, which a grid of buttons never opens."""
    from pinky import kodi

    monkeypatch.setattr(kodi, "has_voice_input", lambda: True)
    asked = {}

    def fake_keyboard(default="", heading=None, hidden=False):
        asked["default"], asked["heading"] = default, heading
        return "hikaru no go"

    monkeypatch.setattr(kodi, "keyboard", fake_keyboard)
    _type_into_field(search, "hik")
    search.onClick(search_window.BUTTON_VOICE)

    assert asked["default"] == "hik", "it must carry on from what was typed"
    assert asked["heading"], "the heading is where 'hold OK to speak' is said"
    assert search.submitted == "hikaru no go"


def test_cancelling_the_keyboard_keeps_what_was_already_typed(search, monkeypatch):
    from pinky import kodi

    monkeypatch.setattr(kodi, "has_voice_input", lambda: True)
    monkeypatch.setattr(kodi, "keyboard", lambda *a, **k: None)
    _type_into_field(search, "dune")
    search.onClick(search_window.BUTTON_VOICE)

    assert search.text == "dune", "an abandoned dictation must not clear it"
    assert search.submitted is None


def test_off_android_it_says_there_is_no_microphone(search, monkeypatch):
    """Voice typing is Android's, not Kodi's, so no other platform has it.
    Opening a keyboard that cannot listen would be the button lying."""
    from pinky import kodi

    monkeypatch.setattr(kodi, "has_voice_input", lambda: False)
    monkeypatch.setattr(kodi, "keyboard",
                        lambda *a, **k: pytest.fail("opened a deaf keyboard"))
    said = []
    monkeypatch.setattr(kodi, "notify", lambda msg, *a, **k: said.append(msg))
    search.onClick(search_window.BUTTON_VOICE)

    assert said and said[0] == kodi.localize(32524)


def test_voice_input_is_the_platform_question(monkeypatch):
    """It cannot see whether Kodi holds RECORD_AUDIO - a refused permission
    is a recogniser that opens and hears nothing, which only Android's own
    settings can fix."""
    import xbmc
    from pinky import kodi

    asked = []
    monkeypatch.setattr(xbmc, "getCondVisibility",
                        lambda cond: asked.append(cond) or True)
    assert kodi.has_voice_input() is True
    assert asked == ["System.Platform.Android"]

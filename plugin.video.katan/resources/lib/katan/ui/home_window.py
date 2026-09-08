"""The Netflix-style home screen.

Why a custom window instead of a skin fork: a skin is loaded for the whole
Kodi session and costs memory even when the add-on is closed, while this window
exists only while it is on screen. Kodi keeps running stock Estuary underneath.

The performance rules that matter here:

* Ten row controls exist in the XML. Only rows near the viewport are filled,
  and a row is filled once.
* Rows render from the SQLite cache the service warmed, so opening the window
  is a database read.
* Artwork is handed to Kodi as URLs; Kodi fetches and caches textures on its
  own threads.
"""
import threading

import xbmcgui

from .. import catalog, kodi, router
from ..meta import trakt_state
from . import listing

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_CONTEXT_MENU = 117
ACTION_MOUSE_MOVE = 107

# Any of these can change which item is under the cursor or the highlight, and
# the hero has to follow. Mouse movement matters because Kodi moves the
# selection on hover without firing a focus event.
MOVE_ACTIONS = (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, ACTION_MOVE_UP,
                ACTION_MOVE_DOWN, ACTION_MOUSE_MOVE)

# How many row controls exist in katan-home.xml. Ten was not a limit anybody
# would notice hitting - it was five rows quietly cut off the end of the list,
# and the Israeli live channels were among them: the add-on's own reason for
# existing, enabled, warmed by the service, and never drawn. `_pick_rows` now
# says so in the log if it ever happens again, and test_addon_integrity checks
# this number against the skin and against the largest section.
ROW_SLOTS = 24
LIST_BASE = 5000
# Search is the only thing on the top bar. Tools used to sit beside it, and
# every entry behind it opened a plain Kodi directory - which is two presses
# from Kodi's own home screen, and a door out of the interface for somebody
# who did not mean to open one. It is all in the settings dialog now.
BUTTON_SEARCH = 9010
BUTTON_NOW_PLAYING = 9013     # only on screen while something is playing
BUTTON_EXIT = 9014

# The section rail down the left-hand side. The ids run in the same order as
# catalog.SECTIONS, and an integrity test holds them to that. Settings is the
# last entry rather than a section - it opens the dialog and comes back - and
# it lives here rather than in the top bar because that is where somebody
# looks for it.
RAIL = 9019
SECTION_BASE = 9020
BUTTON_RAIL_SETTINGS = SECTION_BASE + len(catalog.SECTIONS)

PRELOAD_ROWS = 3          # rows filled before the window is shown
LOOKAHEAD = 2             # rows filled ahead of the focused one

# Rows grow as you scroll along them rather than ending at one page. The
# margin is how close to the end the selection has to get before the next page
# is fetched, and it is set from how fast a held-down remote actually moves:
# Kodi repeats at roughly eight items a second, so eight items is about a
# second of runway - enough for a TMDB page over the projector's wifi rather
# than over a desk's ethernet. A row nobody scrolls still costs exactly one
# page, which is the part that matters for a device with a gigabyte of RAM.
EXTEND_MARGIN = 8
# And a ceiling, because "infinite" on a device with a gigabyte of RAM is a
# promise that ends in the process being killed. TMDB pages are twenty items,
# so this is ten pages: far past where anyone is still browsing rather than
# searching, and about 200 list items, which is cheap - Kodi only decodes the
# artwork for the handful actually on screen.
MAX_ITEMS = 200

# How many pages in a row may come back with nothing new before the row is
# treated as finished. More than one, because a single repeated page is normal
# for a list that reshuffles between requests - and trending is exactly such a
# list, and is the first row of both the Films and Series tabs. Fewer than a
# handful, because a list that really has run out should stop being asked.
BARREN_PAGES = 3


class HomeWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(HomeWindow, self).__init__()
        self.rows = []            # row definitions actually shown
        self.data = {}            # slot index -> list of item dicts
        self.filled = set()       # slot indexes already populated
        self.loading = set()
        self.pages = {}           # slot index -> last page fetched
        self.exhausted = set()    # rows with nothing further to fetch
        self.extending = set()    # rows with a page in flight
        self.pending = {}         # slot index -> items fetched, not yet added
        self.barren = {}          # slot index -> pages running with nothing new
        self.section = catalog.DEFAULT_SECTION
        self.lock = threading.Lock()
        # Whether onInit has already done its work. This has to be its own flag
        # rather than "do we know the rows yet", because prepare() now works
        # them out before the window is shown: guarding on self.rows made
        # onInit return immediately and the window rendered its headings above
        # completely empty rows. The other three windows already use a flag.
        self.ready = False

    # -- lifecycle ---------------------------------------------------------

    def onInit(self):
        if self.ready:
            return                # onInit fires again when returning from a dialog
        self.ready = True

        # This used to close on the spot when there was no TMDB key, and offer
        # the wizard instead, because "almost every row is unavailable" and
        # filling the first slots would leave a black screen. Neither half is
        # true any more. `_pick_rows` asks the catalog, which already drops
        # rows whose credentials are missing, so the five that need none - the
        # Israeli channels, the Israeli catalogue, Kitsu anime and the two
        # public Trakt charts - are exactly what comes back. The check below
        # is the honest version of the same guard: close only when there is
        # genuinely nothing to draw.
        #
        # It mattered more than it looks. A viewer opening the add-on for the
        # first time got a modal yes/no over a window that then tore itself
        # down, so a fresh install never once showed the home screen.
        if not self.rows:
            self.rows = _pick_rows(self.section)
        if not self.rows:
            # The tab this opened on has nothing to draw. On a configured box
            # that means an outage; on a fresh one it means the Films tab,
            # every row of which needs a TMDB key, while Live TV has ten rows
            # of bundled Israeli channels sitting one tab away. Landing on the
            # empty one and closing is how a first run came to show a file
            # list instead of the home screen.
            for section in catalog.SECTION_ORDER:
                if section == self.section:
                    continue
                found = _pick_rows(section)
                if found:
                    kodi.log("home: %s is empty, opening on %s instead"
                             % (self.section, section))
                    self.section = section
                    self.rows = found
                    break
        if not self.rows:
            kodi.notify(kodi.localize(32256))
            self.close()
            return
        self.setProperty("katan.section", self.section)
        self._label_rail()
        self._lay_out_rows()

        self._focus_first_row()
        # Seed the hero from the first item rather than waiting for a focus
        # event, or the top of the screen stays blank until the user moves.
        self._seed_hero()
        self._fill_rest_async()

    def prepare(self):
        """Work out the rows and set their headings before the window is shown.

        A row group is only visible once its heading property is set, and a
        control that is not visible cannot take focus. Doing this inside onInit
        meant the property and the setFocusId landed in the same pass, before
        Kodi had re-evaluated visibility, so the first row refused focus:
        "Control 5000 in window 13000 has been asked to focus, but it can't".
        The arrow keys then moved around the top bar instead of the rows.

        Setting the properties on the window object before doModal means the
        groups are already visible the first time it renders.
        """
        self.rows = _pick_rows(self.section)
        self.setProperty("katan.section", self.section)
        self.setProperty("katan.version", "Katan %s" % kodi.addon_version())
        self._label_rail()
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))
        return bool(self.rows)

    def _lay_out_rows(self):
        """Title every row, then fill until three of them have something in.

        Preloading the *first* three would spend every visible slot on rows
        that are enabled but empty - a Trakt chart with no account, an anime
        row the service has not warmed - so it counts the ones that actually
        came back with items.

        Both the first paint and a change of tab do exactly this, and did it
        in two copies.
        """
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))

        filled = 0
        for index in range(len(self.rows)):
            if filled >= PRELOAD_ROWS:
                break
            self._fill(index)
            if self.data.get(index):
                filled += 1

    def _focus_first_row(self):
        """Focus the first row that actually has something in it.

        Rows that came back empty have had their heading cleared and are
        therefore hidden, so focusing row zero regardless would land on a
        control that is not there.
        """
        for index in sorted(self.data):
            if self.data.get(index):
                self.setFocusId(LIST_BASE + index)
                return True
        # Nothing filled: every row is hidden, so row zero is not there to
        # focus either and the screen would be black with no way off it but
        # the back button. The top bar is always visible, so the viewer can
        # still reach search, tools and the settings that will fix it. That
        # became easier to hit when an empty row started being remembered as
        # empty - a TMDB outage now paints this screen rather than a stale one.
        kodi.log("home: no row has anything in it, focusing the top bar")
        self.setFocusId(BUTTON_SEARCH)
        # Deliberately not "set up Katan". Every row being empty is usually an
        # outage rather than a missing key, and the rows that need no key at
        # all are in this list too - so a wizard is as likely to have nothing
        # to fix as something.
        #
        # The rest of the hero has to be cleared with it. Setting only the
        # title left the previous tab's year, rating, plot and backdrop
        # underneath, so an empty Films tab read "Nothing to show right now"
        # over Attack on Titan's synopsis - which looks like a bug in the
        # thing that *is* working rather than an empty tab.
        self._blank_hero(kodi.localize(32414))

    def _blank_hero(self, title=""):
        """Put a message in the hero and clear everything that described the
        item that used to be there."""
        self.setProperty("katan.hero.title", title)
        for name in ("plot", "meta", "fanart"):
            self.setProperty("katan.hero.%s" % name, "")

    def onAction(self, action):
        code = action.getId()
        # First thing, and before anything reads a position: this is the GUI
        # thread, which is the only place a list may be added to.
        self._absorb()
        if code in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            if self._stay_put():
                return
            self._cleanup()
            self.close()
            return
        if code == ACTION_MOVE_RIGHT and self._on_rail():
            # Back into the content. The skin points the rail at row 5000,
            # which is the wrong row whenever the first one came back empty -
            # and on the Live tab without the bundled data, every one of them.
            self._focus_first_row()
            return
        if code in (ACTION_MOVE_UP, ACTION_MOVE_DOWN) and self._move_row(code):
            return
        if code in MOVE_ACTIONS:
            self._update_hero()
            self._fill_ahead()
            # Deliberately not on mouse movement. Kodi sends ACTION_MOUSE_MOVE
            # while the pointer merely rests over the window, and Kodi moves
            # the selection on hover, so a pointer left anywhere near the end
            # of a row fetched page after page on its own: measured at 94
            # items in a row nobody had touched, during the startup wait, with
            # no input of any kind. Hovering is not scrolling.
            if code != ACTION_MOUSE_MOVE:
                self._extend_ahead()
        elif code == ACTION_CONTEXT_MENU:
            self._context_menu()

    def _quit(self):
        """Leave, one way or the other. Asks first, and offers both.

        The two are genuinely different and the difference is worth a menu
        rather than a guess. Minimise leaves Kodi running and drops to
        whatever is behind it - the Android launcher, the desktop - so coming
        back is immediate and nothing is reloaded. Close ends Kodi, which
        frees the memory: on a box with a gigabyte of it shared with Android,
        that is not a detail.
        """
        options = [kodi.localize(32477), kodi.localize(32478)]
        choice = kodi.select(options, kodi.localize(32473))
        if choice < 0:
            return
        self._cleanup()
        self.close()
        if choice == 0:
            kodi.log("minimising at the viewer's request", kodi.LOG_INFO)
            kodi.run_builtin("Minimize()")
        else:
            kodi.log("closing at the viewer's request", kodi.LOG_INFO)
            kodi.run_builtin("Quit()")

    def _stay_put(self):
        """Should back keep us here rather than drop out to Kodi?

        On a box that exists to run this add-on, backing out of the home
        screen lands on Kodi's own interface, which is not somewhere anyone
        wanted to go - it is the screen this add-on replaces. So with "stay in
        Katan" switched on, back does nothing here.

        Only *here*. Back still works everywhere inside Katan - out of a film's
        details, out of the source picker, out of a season's episodes - because
        those are places you can be finished with. The home screen is not.

        And there is deliberately still a way out: the top bar's settings
        button opens this add-on's own settings, where the switch is. A door
        with no handle on the inside is a worse idea than the problem it
        solves.
        """
        from .. import settings

        # If something is playing, back means "take me back to it". That is
        # the gesture anyone would try, and without it a film left running
        # behind this window can only be heard, never returned to.
        import xbmc
        if xbmc.getCondVisibility("Player.HasMedia"):
            kodi.log("back with something playing, returning to it")
            kodi.run_builtin("ActivateWindow(FullScreenVideo)")
            return True

        if not settings.get_bool("ui.stay_in_katan"):
            return False
        kodi.log("back on the home screen, staying in Katan")
        return True

    def _move_row(self, code):
        """Move up or down between rows, skipping the ones that are empty.

        Kodi does this itself when a skin says where up and down go. These
        lists say nothing - they only wire left and right, so a horizontal
        list wraps within itself - and Kodi's geometric fallback does not find
        its way out of the nested groups. The result was that **down never
        left the first row**: the main screen of the add-on showed one row and
        everything under it was visible and unreachable with a remote. Six
        presses, focus never moved, confirmed by asking Kodi which control it
        thought was focused.

        Python is the right place to fix it rather than the skin, because only
        Python knows which rows came back empty, and an explicit <ondown> at a
        hidden row would simply fail.
        """
        if self._on_rail():
            # The rail is a vertical grouplist, so Kodi already walks up and
            # down it correctly. Taking over here sent the first press
            # straight into the rows, which made the sections below Home
            # unreachable with a remote.
            return False

        current = self._focused_row()
        if current is None:
            # On the top bar: down goes into the content, up stays put.
            if code == ACTION_MOVE_DOWN:
                return self._focus_first_row()
            return False

        filled = sorted(index for index in self.data if self.data.get(index))
        if not filled:
            return False
        later = [i for i in filled if i > current] if code == ACTION_MOVE_DOWN \
            else [i for i in reversed(filled) if i < current]
        if not later:
            # Past the last row, or above the first: the top bar is up there.
            if code == ACTION_MOVE_UP:
                self.setFocusId(BUTTON_SEARCH)
                return True
            return False

        self.setFocusId(LIST_BASE + later[0])
        self._update_hero()
        self._fill_ahead()
        self._extend_ahead()
        return True

    # -- sections ----------------------------------------------------------

    def _label_rail(self):
        """Name the rail's entries from the catalog rather than from the XML.

        The skin could hold the string ids itself, and did at first. Putting
        them here keeps one list of sections instead of two that have to agree
        - adding a section to `catalog.SECTIONS` and forgetting the XML would
        otherwise give an unlabelled button that still worked.
        """
        for index, section_id in enumerate(catalog.section_ids()):
            self.setProperty("katan.rail%d.title" % index,
                             catalog.section_title(section_id))
        self.setProperty("katan.rail%d.title" % len(catalog.SECTIONS),
                         kodi.localize(32261))       # Settings

    def _switch_section(self, section):
        """Swap the whole set of rows for another section's.

        Everything per-row is thrown away, because every index now means a
        different row: the data, which pages have been fetched, which rows are
        finished, and any page still in flight. Keeping any of it would show
        one section's films under another section's heading.
        """
        if section == self.section:
            return
        kodi.log("switching to the %s section" % section)
        self.section = section
        self.setProperty("katan.section", section)
        self._rebuild()

    def _reload(self):
        """Draw this section again from scratch.

        Kids mode replaces the rows rather than filtering them, so switching
        it leaves every slot on screen holding something from the catalogue
        that no longer applies. `kodi.refresh_container` cannot help: it
        refreshes a *directory*, and this is a window.
        """
        kodi.log("reloading the %s section" % self.section)
        self._rebuild()

    def _rebuild(self):

        for index in range(ROW_SLOTS):
            self._set_title(index, "")
            try:
                self.getControl(LIST_BASE + index).reset()
            except Exception:
                pass          # a slot the skin has but this section never used

        self.data.clear()
        self.filled.clear()
        self.pages.clear()
        self.exhausted.clear()
        self.pending.clear()
        self.barren.clear()

        self.rows = _pick_rows(self.section)
        self._lay_out_rows()

        if not self._focus_first_row():
            # Nothing in this section yet. Leave focus on the rail rather than
            # sending it to the top bar: the viewer is picking sections, and
            # the next thing they will want is a different one.
            self.setFocusId(SECTION_BASE + catalog.section_ids().index(self.section))
        self._seed_hero()
        self._fill_rest_async()

    def _section_for(self, control_id):
        index = control_id - SECTION_BASE
        ids = catalog.section_ids()
        return ids[index] if 0 <= index < len(ids) else None

    def _on_rail(self):
        """Anywhere on the rail, settings included.

        Settings is not a section, so asking `_section_for` about it answers
        None - which would have made up, down and right behave on that one
        entry as though focus were in the rows.
        """
        return SECTION_BASE <= self.getFocusId() <= BUTTON_RAIL_SETTINGS

    def onClick(self, control_id):
        section = self._section_for(control_id)
        if section:
            self._switch_section(section)
            return
        if control_id == BUTTON_RAIL_SETTINGS:
            self._tools()
            return
        if control_id == BUTTON_NOW_PLAYING:
            kodi.run_builtin("ActivateWindow(FullScreenVideo)")
            return
        if control_id == BUTTON_EXIT:
            self._quit()
            return
        if control_id == BUTTON_SEARCH:
            self._open_search()
            return
        if LIST_BASE <= control_id < LIST_BASE + ROW_SLOTS:
            self._open_selected(control_id - LIST_BASE)

    def onFocus(self, control_id):
        if LIST_BASE <= control_id < LIST_BASE + ROW_SLOTS:
            self._absorb()
            self._fill_ahead()

    # -- filling rows ------------------------------------------------------

    def _set_title(self, index, title):
        self.setProperty("katan.row%d.title" % index, title or "")

    def _fill(self, index):
        """Populate one row slot from cache, falling back to a live fetch."""
        with self.lock:
            if index in self.filled or index in self.loading:
                return
            self.loading.add(index)
        try:
            row = self.rows[index]
            entries = catalog.peek(row["id"], section=self.section)
            if entries is None:
                entries = catalog.load(row["id"], section=self.section)
            entries = trakt_state.annotate(entries or [])
            if not entries:
                self._set_title(index, "")     # hides the whole group
                return
            self.data[index] = entries
            self.pages[index] = 1
            control = self.getControl(LIST_BASE + index)
            control.reset()
            control.addItems([listing.make_list_item(item) for item in entries])
        except Exception:
            kodi.log_exception("failed to fill home row %d" % index)
            self._set_title(index, "")
        finally:
            with self.lock:
                self.loading.discard(index)
                self.filled.add(index)

    def _fill_rest_async(self):
        """Fill the remaining rows on one worker thread, newest first.

        A single thread is used on purpose. Parallel filling would fight the
        GUI thread for the Python lock and make scrolling stutter.
        """
        def worker():
            for index in range(PRELOAD_ROWS, len(self.rows)):
                if kodi.abort_requested():
                    return
                self._fill(index)

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

    def _fill_ahead(self):
        """Make sure the rows just below the focused one are ready."""
        current = self._focused_row()
        if current is None:
            return
        for index in range(current, min(current + LOOKAHEAD + 1, len(self.rows))):
            if index not in self.filled:
                self._fill(index)

    # -- growing a row as it is scrolled -----------------------------------

    def _extend_ahead(self):
        """Fetch the next page of the focused row if the end is in sight.

        A row used to be one page and stop, so scrolling right ran into a wall
        after twenty items with no indication that there was more - the
        catalogue looked far smaller than it is. Now the row grows under the
        cursor.

        Two decisions worth keeping. It is done on a worker thread, because a
        page is an HTTP round trip and doing it on the GUI thread would freeze
        the scroll for as long as TMDB takes to answer, which is exactly the
        moment the viewer is holding the button down. And the new items are
        appended rather than the list being rebuilt: `reset()` would drop the
        selection back to the start, which on a device where the fetch takes a
        second reads as the row throwing the viewer out.
        """
        index = self._focused_row()
        if index is None or not self._wants_more(index):
            return
        with self.lock:
            if index in self.extending:
                return
            self.extending.add(index)

        thread = threading.Thread(target=self._extend, args=(index,))
        thread.daemon = True
        thread.start()

    def _wants_more(self, index):
        """Is the selection near the end of a row that has more to give?"""
        if index in self.exhausted or index in self.extending:
            return False
        entries = self.data.get(index)
        if not entries or len(entries) >= MAX_ITEMS:
            return False
        row = self.rows[index] if index < len(self.rows) else None
        if not row or not catalog.has_more(row["id"]):
            return False
        try:
            position = self.getControl(LIST_BASE + index).getSelectedPosition()
        except Exception:
            return False
        return position >= len(entries) - EXTEND_MARGIN

    def _extend(self, index):
        """Fetch one more page for a row. Runs on a worker thread.

        It only fetches. Putting the items into the control is left to
        `_absorb`, on the GUI thread, and that division is the whole point of
        this pair of methods rather than an abundance of caution - see there.
        """
        try:
            row = self.rows[index]
            page = self.pages.get(index, 1) + 1
            entries = catalog.load(row["id"], page=page,
                                   section=self.section) or []
            # A row that gives nothing back has reached its end. TMDB keeps
            # answering past the last page with an empty list rather than an
            # error, so this is the only signal there is, and remembering it
            # stops every further keypress asking again.
            if not entries:
                self.exhausted.add(index)
                kodi.log("row %s has no page %d" % (row["id"], page))
                return

            known = {item.get("id") or item.get("title")
                     for item in self.data.get(index, [])}
            fresh = [item for item in trakt_state.annotate(entries)
                     if (item.get("id") or item.get("title")) not in known]
            if not fresh:
                # A page of things we already have is not the end of the row,
                # and treating it as one is what stopped the trending rows
                # after a single page. Trending reshuffles between requests,
                # so page two legitimately repeats much of page one - and
                # since trending is the *first* row of both the Films and
                # Series tabs, that one duplicate page killed scrolling on
                # the row most likely to be scrolled.
                #
                # Step over it and try the next one. Give up only after
                # several in a row, which is what a list that has genuinely
                # run out looks like.
                self.pages[index] = page
                self.barren[index] = self.barren.get(index, 0) + 1
                kodi.log("row %s page %d repeated what we had (%d in a row)"
                         % (row["id"], page, self.barren[index]))
                if self.barren[index] >= BARREN_PAGES:
                    self.exhausted.add(index)
                return

            self.barren[index] = 0
            self.pages[index] = page
            self.pending[index] = fresh
        except Exception:
            # One failure is not the end of a row either - a request can time
            # out on a wireless projector and mean nothing at all.
            self.barren[index] = self.barren.get(index, 0) + 1
            if self.barren[index] >= BARREN_PAGES:
                self.exhausted.add(index)
            kodi.log_exception("failed to extend home row %d" % index)
        finally:
            with self.lock:
                self.extending.discard(index)

    def _absorb(self):
        """Put fetched pages into their controls. GUI thread only.

        This exists because of a bug the viewer described exactly: scrolling
        right faster than the next page loaded threw the selection back to the
        start of the row. Adding items to a list Kodi is *currently
        navigating*, from a worker thread, makes it reflow underneath the
        cursor - measured in a real Kodi, the cursor went 56 -> 0 on one
        append, and slid backwards by ten or twenty on several others. From
        the sofa that reads as the row throwing you out for scrolling too
        fast, which is the one thing an endless row must never do.

        So the worker only fetches, and the items are added here, inside a
        Kodi callback, which is the GUI thread. The position is read before
        and restored after regardless, because a list that has just changed
        length is entitled to move its own cursor and this is cheap insurance.
        """
        if not self.pending:
            return
        for index in sorted(self.pending):
            fresh = self.pending.pop(index, None)
            if not fresh:
                continue
            try:
                control = self.getControl(LIST_BASE + index)
                before = control.getSelectedPosition()
                self.data[index] = self.data.get(index, []) + fresh
                control.addItems(
                    [listing.make_list_item(item) for item in fresh])
                # Unconditionally, and this is the whole fix. Both addItems
                # and selectItem post *thread messages* rather than acting on
                # the control there and then, so getSelectedPosition() right
                # after an append reads the state before it - which is why
                # guarding this on "did the position change?" did nothing at
                # all, and the row still threw the viewer back to the start.
                # The messages are processed in the order they were posted, so
                # the rebind lands first and this lands on top of it.
                control.selectItem(before)
                kodi.log("row %s grew to %d items, cursor held at %d"
                         % (self.rows[index]["id"], len(self.data[index]),
                            before))
            except Exception:
                self.exhausted.add(index)
                kodi.log_exception("failed to add page to home row %d" % index)

    # -- selection ---------------------------------------------------------

    def _focused_row(self):
        control_id = self.getFocusId()
        if LIST_BASE <= control_id < LIST_BASE + ROW_SLOTS:
            return control_id - LIST_BASE
        return None

    def _focused_item(self):
        index = self._focused_row()
        if index is None:
            return None
        entries = self.data.get(index)
        if not entries:
            return None
        try:
            position = self.getControl(LIST_BASE + index).getSelectedPosition()
        except Exception:
            return None
        if 0 <= position < len(entries):
            return entries[position]
        return None

    def _seed_hero(self):
        """Fill the hero from the first row, before any focus event arrives."""
        for index in sorted(self.data):
            entries = self.data.get(index)
            if entries:
                self._show_hero(entries[0])
                return

    def _show_hero(self, item):
        art = item.get("art") or {}
        self.setProperty("katan.hero.title", item.get("title") or "")
        self.setProperty("katan.hero.plot", item.get("plot") or "")
        # Only a real backdrop goes behind the hero. Falling back to the poster
        # stretches a portrait image, or a channel logo, across the whole
        # screen, which looks worse than the plain background.
        self.setProperty("katan.hero.fanart", art.get("fanart") or "")
        self.setProperty("katan.hero.meta", _hero_meta(item))

    def _update_hero(self):
        item = self._focused_item()
        if item:
            self._show_hero(item)

    def _open_selected(self, index):
        entries = self.data.get(index)
        if not entries:
            return
        try:
            position = self.getControl(LIST_BASE + index).getSelectedPosition()
        except Exception:
            return
        if not (0 <= position < len(entries)):
            return
        item = entries[position]

        # Movies and shows get the details screen. Channels and VOD entries
        # have nothing to show there, so they act immediately.
        if item.get("type") in ("movie", "show"):
            from .details_window import open_details
            open_details(item)
            return

        url, is_folder = listing.target_url(item)
        if not url:
            return
        if is_folder:
            self._cleanup()
            self.close()
            kodi.activate_window(url)
        else:
            kodi.play_media(url)

    def _tools(self):
        """The Tools menu, without leaving the window.

        It used to open the settings dialog directly, so everything else Tools
        offers - the setup wizard, the accounts screen, the device report,
        checking for an update - was reachable only from the plain directory
        listing, which the dashboard never shows. They were, in effect, not
        there. Opening the directory instead would navigate out of this window
        into Kodi's file browser, which is the thing this add-on exists to
        avoid, so the entries are offered as a list and the chosen one runs in
        place. A group opens a second list rather than a directory, for the
        same reason.
        """
        from .handlers import tool_entries

        group = ""
        while True:
            entries = tool_entries(group)
            choice = kodi.select([kodi.localize(string_id)
                                  for string_id, _url, _group in entries],
                                 kodi.localize(32255))
            if not (0 <= choice < len(entries)):
                return
            _string_id, url, is_group = entries[choice]
            if not is_group:
                if "action=kids_toggle" in url:
                    # In place, not through RunPlugin: a plugin call is
                    # asynchronous, so the window would have no idea when to
                    # redraw - and kids mode replaces every row on screen.
                    from .handlers import toggle_kids

                    toggle_kids()
                    self._reload()
                    return
                kodi.run_builtin("RunPlugin(%s)" % url)
                return
            # A group's url is the same route with a group parameter, which is
            # what the directory listing follows. Here the name is enough.
            group = url.rsplit("group=", 1)[-1]

    def _context_menu(self):
        item = self._focused_item()
        if not item:
            return
        entries = listing.context_menu(item)
        if not entries:
            return
        choice = kodi.select([label for label, _ in entries], item.get("title", ""))
        if choice >= 0:
            kodi.run_builtin(entries[choice][1])

    def _open_search(self):
        from .search_window import open_search
        query = open_search(modal_result=True)
        if query:
            self._cleanup()
            self.close()
            kodi.activate_window(router.url_for("search_query", q=query))

    def _cleanup(self):
        for index in range(ROW_SLOTS):
            self.clearProperty("katan.row%d.title" % index)
        for name in ("title", "plot", "fanart", "meta"):
            self.clearProperty("katan.hero.%s" % name)
        self.clearProperty("katan.section")
        for index in range(len(catalog.SECTIONS) + 1):
            self.clearProperty("katan.rail%d.title" % index)
        self.data.clear()
        self.pages.clear()
        self.exhausted.clear()
        self.barren.clear()


def _pick_rows(section=catalog.HOME):
    """The rows this window will draw, and a complaint about any it cannot.

    The window has a fixed number of row controls, so a viewer who enables
    more rows than that gets the first ROW_SLOTS of them. That part is fine.
    What was not fine is that it happened in silence: the slice sat inline in
    two places, and the rows it removed had every appearance of working -
    enabled in the settings, warmed by the background service, present in the
    cache, listed by every tool that asks the catalog rather than the window.
    Only the screen disagreed, and a screen that is missing a row does not say
    which one.
    """
    rows = catalog.enabled_rows(section)
    if len(rows) <= ROW_SLOTS:
        return rows
    dropped = [row["id"] for row in rows[ROW_SLOTS:]]
    kodi.log("home: section %s has %d rows but only %d can be drawn, so these "
             "are not on the screen: %s"
             % (section, len(rows), ROW_SLOTS, ", ".join(dropped)),
             kodi.LOG_WARNING)
    return rows[:ROW_SLOTS]


def _hero_meta(item):
    """The small grey line under the hero title."""
    bits = []
    if item.get("year"):
        bits.append(str(item["year"]))
    if item.get("rating"):
        bits.append("%.1f" % item["rating"])
    genres = item.get("genres") or []
    if genres:
        bits.append(" / ".join(genres[:3]))
    if item.get("mpaa"):
        bits.append(item["mpaa"])
    return "  \u2022  ".join(bits)


def open_home():
    window = HomeWindow("katan-home.xml", kodi.addon_path(), "default", "1080i")
    try:
        # The headings are set before the window is shown so its row groups are
        # already visible on the first render and can take focus.
        window.prepare()
        kodi.clear_busy_dialogs()
        window.doModal()
    finally:
        del window

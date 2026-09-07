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

        # Without a TMDB key almost every row is unavailable, and the ones that
        # remain are the Israeli ones further down the list. Filling the first
        # few slots then leaves a completely black screen with no explanation,
        # which is what this window used to do. The plain directory listing has
        # always offered the setup wizard here, so this does too.
        if not self._require_setup():
            return

        if not self.rows:
            self.rows = _pick_rows(self.section)
        if not self.rows:
            kodi.notify(kodi.localize(32256))
            self.close()
            return
        self.setProperty("katan.section", self.section)
        self._label_rail()
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))

        # Preload until three rows actually have something in them rather than
        # the first three in the list. A row that is enabled but empty - a
        # Trakt chart with no account, an anime row the service has not warmed -
        # would otherwise use up every visible slot.
        filled = 0
        for index in range(len(self.rows)):
            if filled >= PRELOAD_ROWS:
                break
            self._fill(index)
            if self.data.get(index):
                filled += 1

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
        self._label_rail()
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))
        return bool(self.rows)

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
        # Not "set up Katan": _require_setup has already run and returned
        # True, so there *is* a TMDB key and setup is not the problem. Saying
        # so would send the viewer to a wizard that has nothing to fix.
        self.setProperty("katan.hero.title", kodi.localize(32414))

    def _require_setup(self):
        """Offer the wizard when there is no TMDB key, and close.

        Returns True when the window should carry on building itself.
        """
        from ..meta import tmdb
        if tmdb.has_key():
            return True

        self.close()
        if kodi.yes_no(kodi.localize(32256)):
            from .wizard import run
            run()
        return False

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
        """Leave. Asks first, because a family should not do this by accident.

        Kodi's own Quit, which is the right thing on the hardware this runs
        on: on Android it drops to the launcher and the system keeps the
        process warm, so coming back is instant - the "close it but keep it
        in the background" behaviour, without having to build it. On a desktop
        it simply closes.
        """
        if not kodi.yes_no(kodi.localize(32473), kodi.localize(32472)):
            return
        kodi.log("leaving at the viewer's request", kodi.LOG_INFO)
        self._cleanup()
        self.close()
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

        if not settings.get_bool("ui.stay_in_katan", False):
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

        self.rows = _pick_rows(section)
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))

        filled = 0
        for index in range(len(self.rows)):
            if filled >= PRELOAD_ROWS:
                break
            self._fill(index)
            if self.data.get(index):
                filled += 1

        if not self._focus_first_row():
            # Nothing in this section yet. Leave focus on the rail rather than
            # sending it to the top bar: the viewer is picking sections, and
            # the next thing they will want is a different one.
            self.setFocusId(SECTION_BASE + catalog.section_ids().index(section))
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
            from .. import settings
            settings.open_settings()
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
            entries = catalog.peek(row["id"])
            if entries is None:
                entries = catalog.load(row["id"])
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
            entries = catalog.load(row["id"], page=page) or []
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
                # Pages that repeat what we already have are not progress, and
                # a trending list reshuffling between requests does exactly
                # that. Stop rather than fetch page after page of duplicates.
                self.exhausted.add(index)
                return

            self.pages[index] = page
            self.pending[index] = fresh
        except Exception:
            # A row that cannot grow is still a row that works. Give up on
            # this one rather than letting it retry on every keypress.
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
        window.doModal()
    finally:
        del window

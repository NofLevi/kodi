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

ROW_SLOTS = 10
LIST_BASE = 5000
BUTTON_SEARCH = 9010
BUTTON_TOOLS = 9011
BUTTON_SETTINGS = 9012

PRELOAD_ROWS = 3          # rows filled before the window is shown
LOOKAHEAD = 2             # rows filled ahead of the focused one


class HomeWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(HomeWindow, self).__init__()
        self.rows = []            # row definitions actually shown
        self.data = {}            # slot index -> list of item dicts
        self.filled = set()       # slot indexes already populated
        self.loading = set()
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
            self.rows = catalog.enabled_rows()[:ROW_SLOTS]
        if not self.rows:
            kodi.notify(kodi.localize(32256))
            self.close()
            return
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
        self.rows = catalog.enabled_rows()[:ROW_SLOTS]
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
                return
        self.setFocusId(LIST_BASE)

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
        if code in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self._cleanup()
            self.close()
            return
        if code in MOVE_ACTIONS:
            self._update_hero()
            self._fill_ahead()
        elif code == ACTION_CONTEXT_MENU:
            self._context_menu()

    def onClick(self, control_id):
        if control_id == BUTTON_SEARCH:
            self._open_search()
            return
        if control_id == BUTTON_TOOLS:
            self._run("tools")
            return
        if control_id == BUTTON_SETTINGS:
            from .. import settings
            settings.open_settings()
            return
        if LIST_BASE <= control_id < LIST_BASE + ROW_SLOTS:
            self._open_selected(control_id - LIST_BASE)

    def onFocus(self, control_id):
        if LIST_BASE <= control_id < LIST_BASE + ROW_SLOTS:
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

    def _run(self, action, **params):
        self._cleanup()
        self.close()
        kodi.activate_window(router.url_for(action, **params))

    def _cleanup(self):
        for index in range(ROW_SLOTS):
            self.clearProperty("katan.row%d.title" % index)
        for name in ("title", "plot", "fanart", "meta"):
            self.clearProperty("katan.hero.%s" % name)
        self.data.clear()


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

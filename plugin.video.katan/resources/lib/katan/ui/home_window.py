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

MOVE_ACTIONS = (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, ACTION_MOVE_UP, ACTION_MOVE_DOWN)

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

    # -- lifecycle ---------------------------------------------------------

    def onInit(self):
        if self.rows:
            return                # onInit fires again when returning from a dialog
        self.rows = catalog.enabled_rows()[:ROW_SLOTS]
        if not self.rows:
            kodi.notify(kodi.localize(32256))
            self.close()
            return
        for index in range(len(self.rows)):
            self._set_title(index, catalog.row_title(self.rows[index]))
        for index in range(min(PRELOAD_ROWS, len(self.rows))):
            self._fill(index)
        self.setFocusId(LIST_BASE)
        self._update_hero()
        self._fill_rest_async()

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

    def _update_hero(self):
        item = self._focused_item()
        if not item:
            return
        art = item.get("art") or {}
        self.setProperty("katan.hero.title", item.get("title") or "")
        self.setProperty("katan.hero.plot", item.get("plot") or "")
        self.setProperty("katan.hero.fanart", art.get("fanart") or art.get("poster") or "")
        self.setProperty("katan.hero.meta", _hero_meta(item))

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
        window.doModal()
    finally:
        del window

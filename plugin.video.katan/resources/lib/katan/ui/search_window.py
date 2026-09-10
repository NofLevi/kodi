"""Search with live, non-forcing autocomplete.

Kodi's built-in keyboard is modal, so it cannot show suggestions while the user
types. This window therefore owns its own input, the way a TV app does: a key
grid on the left, suggestions on the right, updated as characters arrive.

Two properties of the suggestions matter and are deliberate:

* They are substring matches, not prefix-only, so typing a word from the middle
  of a title still finds it.
* They never overwrite what was typed. Pressing Search always runs the exact
  text the user entered, whether or not anything matched.
"""
import threading
import time

import xbmcgui

from .. import kodi, router
from ..meta import items as meta_items
from ..search import unified
from . import listing

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_BACKSPACE = 110

# 135, not 7. Kodi calls 7 ACTION_SELECT_ITEM - it is the OK button - so
# naming it ACTION_ENTER meant every press on the key grid ran a search as
# well as typing a letter. `_submit` wants two characters, so the effect was
# that you could type exactly two and the *third* key closed the window and
# searched for the fragment. The keyboard looked broken because it was.
ACTION_ENTER = 135

KEY_BASE = 4000
KEY_COUNT = 30
BUTTON_CHARSET = 3900
BUTTON_SPACE = 3901
BUTTON_BACKSPACE = 3902
BUTTON_CLEAR = 3903
BUTTON_SEARCH = 3904

# There is no microphone here, and there cannot be: Kodi's Python API exposes
# no audio capture at all, and on Android an add-on cannot reach the system
# speech recogniser either. What it can do is borrow the microphone that is
# already in the room. `pastebox` serves a one-field page on the local
# network, the phone opens it, and the phone's own keyboard has a dictation
# key - so the words are spoken into the phone and arrive here as text. The
# same mechanism already carries debrid keys, for the same reason: nothing
# should have to be typed on a television.
BUTTON_VOICE = 3905
LIST_RESULTS = 5100

DEBOUNCE_SECONDS = 0.25
MIN_QUERY = 2

# Every charset is padded to exactly KEY_COUNT. That is not cosmetic: the key
# buttons used to be hidden while their character property was empty, and a
# hidden control cannot take focus, so opening the window logged "Control 4000
# has been asked to focus, but it can't" and the grid was left with nothing
# focused. Filling every slot means every key is always there to focus.
#
# Hebrew first and Latin second, so one press of the switch moves between the
# two *alphabets*. The order used to be Latin, Hebrew, digits, which meant a
# Hebrew interface - the one this opens on - offered the number pad as its
# next set and reached English only on the second press. Somebody looking for
# English pressed once, got digits, and reasonably concluded there was none.
CHARSETS = [
    list("\u05d0\u05d1\u05d2\u05d3\u05d4\u05d5\u05d6\u05d7\u05d8\u05d9"
         "\u05db\u05dc\u05de\u05e0\u05e1\u05e2\u05e4\u05e6\u05e7\u05e8"
         "\u05e9\u05ea") + ["\u05da", "\u05dd", "\u05df", "\u05e3", "\u05e5",
                            "-", "'", "."],
    list("abcdefghijklmnopqrstuvwxyz") + ["-", "'", ":", "."],
    list("0123456789") + ["&", "+", "!", "?", ",", "(", ")", "-", "'", ":",
                          ".", "/", "#", "@", "*", "%", "=", "_", "\"", ";"],
]

assert all(len(charset) == KEY_COUNT for charset in CHARSETS), \
    "every charset has to fill the key grid exactly"

# Written in the script each one is, so the button reads as itself in any
# interface language.
CHARSET_NAMES = ["אבג", "ABC", "123"]

HEBREW, LATIN, DIGITS = 0, 1, 2


def initial_charset():
    """Which keyboard to open on.

    A Hebrew interface opened on the Latin keyboard, which is the wrong way
    round for an add-on whose live TV and on-demand catalogue are titled
    entirely in Hebrew: every one of those searches began with a trip to the
    charset button.

    The language question is answered by tmdb.language(), which already reads
    the ui.language setting and falls back to Kodi's own. A second opinion on
    "is this interface Hebrew" would be one more thing to keep in step.
    """
    try:
        from ..meta import tmdb
        return HEBREW if tmdb.language().startswith("he") else LATIN
    except Exception:
        return LATIN


def _typed_character(action):
    """The printable character an action carries, if this Kodi exposes one.

    Written defensively on purpose. Kodi 21's Action has no getUnicode at all,
    older and newer builds do, and an add-on that assumes either one crashes on
    the other. Anything unexpected here means "no character", never an error.
    """
    getter = getattr(action, "getUnicode", None)
    if not callable(getter):
        return ""
    try:
        char = getter()
    except Exception:
        return ""
    if not char or not isinstance(char, str):
        return ""
    return char if char.isprintable() else ""


class SearchWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super(SearchWindow, self).__init__()
        self.text = ""
        self.charset = initial_charset()
        self.entries = []
        self.generation = 0
        self.submitted = None
        self.lock = threading.Lock()
        self.ready = False

    # -- lifecycle ---------------------------------------------------------

    def prepare(self):
        """Label the key grid before the window is shown, so it can take focus."""
        self._paint_keys()
        self.setProperty("katan.search.text", "")

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self._paint_keys()
        self._set_text("")
        self._show_recent()
        self.setFocusId(KEY_BASE)

    def onAction(self, action):
        code = action.getId()
        if code in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()
            return
        if code == ACTION_BACKSPACE:
            self._backspace()
            return
        if code == ACTION_ENTER and self.getFocusId() != LIST_RESULTS:
            self._submit()
            return
        # A physical keyboard can send the character here, on the Kodi builds
        # that expose it. Kodi 21 does not: xbmcgui.Action has no getUnicode,
        # and calling it raised an AttributeError on every single keypress,
        # which the suite never saw because its fake Action had the method.
        # The on-screen grid is the input that always works; this is a bonus
        # where the build offers it.
        char = _typed_character(action)
        if char:
            self._append(char)

    def onClick(self, control_id):
        if KEY_BASE <= control_id < KEY_BASE + KEY_COUNT:
            keys = CHARSETS[self.charset]
            index = control_id - KEY_BASE
            if index < len(keys):
                self._append(keys[index])
            return
        if control_id == BUTTON_CHARSET:
            self.charset = (self.charset + 1) % len(CHARSETS)
            self._paint_keys()
        elif control_id == BUTTON_SPACE:
            self._append(" ")
        elif control_id == BUTTON_BACKSPACE:
            self._backspace()
        elif control_id == BUTTON_CLEAR:
            self._set_text("")
            self._show_recent()
        elif control_id == BUTTON_SEARCH:
            self._submit()
        elif control_id == BUTTON_VOICE:
            self._speak()
        elif control_id == LIST_RESULTS:
            self._open_selected()

    def _speak(self):
        """Take the query from a phone on the same network.

        Returns without touching anything if the viewer backs out or the
        page times out - an abandoned dictation must not clear what was
        already typed.
        """
        from .signin import receive_key

        spoken = receive_key(kodi.localize(32523),
                             placeholder=kodi.localize(32524))
        if not spoken:
            return
        self._set_text(spoken.strip())
        self._submit()

    # -- text entry --------------------------------------------------------

    def _paint_keys(self):
        keys = CHARSETS[self.charset]
        for index in range(KEY_COUNT):
            self.setProperty("katan.key%d" % index,
                             keys[index] if index < len(keys) else "")
        # The switch button names the set it will move to, not all three at
        # once. "ABC / Hebrew / 123" did not fit the button and was truncated
        # to "ABC / Hebr...", which named nothing useful.
        self.setProperty("katan.search.charset",
                         CHARSET_NAMES[(self.charset + 1) % len(CHARSETS)])

    def _append(self, char):
        self._set_text(self.text + char)

    def _backspace(self):
        if self.text:
            self._set_text(self.text[:-1])
            if len(self.text) < MIN_QUERY:
                self._show_recent()

    def _set_text(self, value):
        self.text = value
        self.setProperty("katan.search.text", value)
        self._schedule_suggestions()

    # -- suggestions -------------------------------------------------------

    def _schedule_suggestions(self):
        """Debounce: each keystroke supersedes the one before it.

        The generation counter is what keeps a slow reply from a previous
        keystroke overwriting the list for a newer one.
        """
        with self.lock:
            self.generation += 1
            generation = self.generation
        if len(self.text) < MIN_QUERY:
            return
        query = self.text

        def worker():
            time.sleep(DEBOUNCE_SECONDS)
            with self.lock:
                if generation != self.generation:
                    return                    # a newer keystroke won
            self._set_status(kodi.localize(32296))
            try:
                results = unified.suggest(query)
            except Exception:
                kodi.log_exception("suggestions failed")
                results = []
            with self.lock:
                if generation != self.generation:
                    return
            self._render(results, kodi.localize(32297, len(results)))

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

    def _show_recent(self):
        """With no query, offer the last few searches as one-press repeats."""
        history = unified.recent()[:10]
        if not history:
            self._render([], "")
            return
        control = self.getControl(LIST_RESULTS)
        control.reset()
        for query in history:
            li = xbmcgui.ListItem(label=query, label2=kodi.localize(32298), offscreen=True)
            li.setProperty("katan.query", query)
            control.addItem(li)
        self.entries = [{"type": "query", "title": q} for q in history]
        self._set_status(kodi.localize(32299))

    def _render(self, results, status):
        try:
            control = self.getControl(LIST_RESULTS)
            control.reset()
            for item in results:
                li = listing.make_list_item(item, label=meta_items.label(item))
                li.setLabel2(_subtitle(item))
                control.addItem(li)
            self.entries = list(results)
            self._set_status(status)
        except Exception:
            kodi.log_exception("could not render suggestions")

    def _set_status(self, text):
        self.setProperty("katan.search.status", text or "")

    # -- acting on a choice ------------------------------------------------

    def _submit(self):
        """Run the full search with exactly what was typed."""
        query = self.text.strip()
        if len(query) < MIN_QUERY:
            return
        unified.remember(query)
        self.submitted = query
        self.close()

    def _open_selected(self):
        try:
            position = self.getControl(LIST_RESULTS).getSelectedPosition()
        except Exception:
            return
        if not (0 <= position < len(self.entries)):
            return
        item = self.entries[position]

        if item.get("type") == "query":
            self._set_text(item["title"])
            self._submit()
            return

        unified.remember(self.text.strip() or item.get("title", ""))

        if item.get("type") in ("movie", "show"):
            from .details_window import open_details
            if open_details(item):
                self.close()
            return

        url, is_folder = listing.target_url(item)
        if not url:
            return
        self.close()
        if is_folder:
            kodi.activate_window(url)
        else:
            kodi.play_media(url, item)


def _subtitle(item):
    """The grey second line: year, type and rating."""
    bits = []
    kind = item.get("type")
    if kind == "movie":
        bits.append(kodi.localize(32300))
    elif kind == "show":
        bits.append(kodi.localize(32301))
    if (item.get("extra") or {}).get("anime"):
        bits.append(kodi.localize(32302))
    if item.get("year"):
        bits.append(str(item["year"]))
    if item.get("rating"):
        bits.append("%.1f" % item["rating"])
    return "  \u2022  ".join(bits)


def open_search(modal_result=False):
    """Open the window. Returns the submitted query when asked to."""
    window = SearchWindow("katan-search.xml", kodi.addon_path(), "default", "1080i")
    try:
        # Paint the keys before the window is shown, for the same reason the
        # home window sets its headings early: a control Kodi has not yet
        # decided is visible cannot take focus.
        window.prepare()
        kodi.clear_busy_dialogs()
        window.doModal()
        query = window.submitted
    finally:
        del window
    if modal_result:
        return query
    if query:
        kodi.activate_window(router.url_for("search_query", q=query))
    return query

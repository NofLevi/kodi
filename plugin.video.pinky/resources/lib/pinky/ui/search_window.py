"""Search, with suggestions as the query changes.

This used to draw its own thirty-key grid, so that suggestions could appear
while somebody typed - Kodi's keyboard is modal and cannot show them. The grid
was the wrong trade and it cost the one thing a television actually has: **the
microphone**. Kodi's own keyboard on Android hands its field to the system IME,
and holding OK there starts Android's dictation; that is what every add-on's
"voice search" is, the POV IL build included. A grid of buttons never sees any
of it.

So the entry points are Kodi's keyboard and the edit field, and what is left
here is the part worth keeping: the results, drawn in this window rather than
handed to Kodi's video browser, which is what keeps the source picker.

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
ACTION_SELECT_ITEM = 7

BUTTON_SEARCH = 3904

# The microphone is real and it is Kodi's, not ours. Kodi's Python API exposes
# no audio capture, but Kodi's *own* keyboard dialog on Android hands the field
# to the system IME, and holding OK on the remote there starts Android's
# dictation. That is what every other add-on's voice search is, including the
# POV IL build's: no API, just Kodi's keyboard on a platform that has voice
# typing behind it. Our key grid is a grid of buttons, so it never sees any of
# it - which is why this button exists, to hand the field back to Kodi for as
# long as somebody wants to talk to it.
#
# It needs Kodi to hold Android's RECORD_AUDIO permission. That is granted in
# Android's own settings and there is nothing here that can ask for it.
BUTTON_VOICE = 3905
LIST_RESULTS = 5100

# The text field. An edit control, so a physical keyboard or a phone remote can
# type into it; the key grid writes into the same control.
EDIT_QUERY = 3100

DEBOUNCE_SECONDS = 0.25
MIN_QUERY = 2


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
        self.entries = []
        self.generation = 0
        self.submitted = None
        self.lock = threading.Lock()
        self.ready = False

    # -- lifecycle ---------------------------------------------------------

    def prepare(self):
        """Set the properties before the window is shown, so it can take focus.

        A control Kodi has not yet decided is visible cannot be focused, which
        is why this is not done in onInit.
        """
        self.setProperty("pinky.search.text", "")
        self.setProperty("pinky.search.status", "")

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self._set_text("")
        self._show_recent()
        # The field. Someone at a keyboard just starts typing, and a letter
        # pressed anywhere else runs whatever Kodi's keymap binds it to - one
        # of them opens the PVR channel list. A remote presses OK on it and
        # gets Kodi's keyboard, which is where the microphone is.
        self.setFocusId(EDIT_QUERY)

    def onAction(self, action):
        code = action.getId()
        if code in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()
            return
        if self.getFocusId() == EDIT_QUERY:
            # The field has already applied the key - a character, a
            # backspace, a cursor move - before this callback runs, so the
            # only job here is to catch up with it. Handling backspace here as
            # well would delete two characters.
            self._sync_from_field()
            if code == ACTION_ENTER:
                self._submit()
            elif code == ACTION_SELECT_ITEM and len(self.text.strip()) >= MIN_QUERY:
                # Enter on a physical keyboard arrives as Select, not Enter -
                # measured - and Select on an edit control has already opened
                # Kodi's modal keyboard by the time this runs. With a query in
                # the field, Enter and OK both mean "search", so the keyboard
                # is closed again and the search runs. With nothing typed OK
                # still opens it, which is what a remote needs.
                kodi.run_builtin("Dialog.Close(virtualkeyboard,true)")
                self._submit()
            return
        self._sync_from_field()
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
        if control_id == EDIT_QUERY:
            # OK on the field opens Kodi's own keyboard, and nothing else
            # reports what it returned.
            self._sync_from_field()
            return
        if control_id == BUTTON_SEARCH:
            self._submit()
        elif control_id == BUTTON_VOICE:
            self._speak()
        elif control_id == LIST_RESULTS:
            self._open_selected()

    def _speak(self):
        """Hand the field to Kodi's keyboard, where the microphone lives.

        It used to serve a page on the local network and draw a QR code, so
        that a phone could do the dictating. That was written from the belief
        that a television has no microphone this add-on can reach, and the
        belief was wrong: the remote has one, Android has the recogniser, and
        Kodi's own keyboard is the door to both.

        Off Android there is no such door - no IME, no dictation - so the
        button says so rather than opening a keyboard that cannot listen. The
        viewer is at a desktop with a real keyboard in that case anyway.

        Returns without touching anything if the viewer backs out: an
        abandoned dictation must not clear what was already typed.
        """
        if not kodi.has_voice_input():
            kodi.notify(kodi.localize(32524))
            return
        spoken = kodi.keyboard(self.text, kodi.localize(32523))
        if not spoken:
            return
        self._set_text(spoken.strip())
        self._submit()

    # -- text entry --------------------------------------------------------

    def _append(self, char):
        self._set_text(self.text + char)

    def _backspace(self):
        if self.text:
            self._set_text(self.text[:-1])
            if len(self.text) < MIN_QUERY:
                self._show_recent()

    def _set_text(self, value, from_field=False):
        self.text = value
        self.setProperty("pinky.search.text", value)
        if not from_field:
            try:
                self.getControl(EDIT_QUERY).setText(value)
            except Exception:
                pass
        self._schedule_suggestions()

    def _sync_from_field(self):
        """Take whatever the edit control now holds as the query."""
        try:
            typed = self.getControl(EDIT_QUERY).getText()
        except Exception:
            return
        if typed is None or typed == self.text:
            return
        self._set_text(typed, from_field=True)
        if len(typed) < MIN_QUERY:
            self._show_recent()

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
            li.setProperty("pinky.query", query)
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
        self.setProperty("pinky.search.status", text or "")

    # -- acting on a choice ------------------------------------------------

    def _submit(self):
        """Run the full search and show the results here.

        This used to close the window and hand the query to the `search_query`
        *directory* route, which Kodi renders in its own video browser. That
        is a different interface with none of this one behind it: selecting a
        show there walks Kodi's seasons and episodes listings and plays
        whatever autoplay picks, so the source picker - the three lists this
        add-on exists to offer - was simply not reachable from a search. It
        was still reachable by pressing a *suggestion*, because that path goes
        through `_open_selected` and opens our own details window, so the
        feature appeared to work and only failed when somebody actually
        searched.

        The window already renders results and already routes them correctly.
        All that was missing was asking it to.
        """
        query = self.text.strip()
        if len(query) < MIN_QUERY:
            return
        unified.remember(query)
        self.submitted = query

        with self.lock:
            self.generation += 1
            generation = self.generation

        def worker():
            self._set_status(kodi.localize(32296))
            try:
                results = unified.search(query)
            except Exception:
                kodi.log_exception("search failed")
                results = []
            with self.lock:
                if generation != self.generation:
                    return              # something newer was typed meanwhile
            if results:
                self._render(results, kodi.localize(32297, len(results)))
            else:
                self._render([], kodi.localize(32258))

        thread = threading.Thread(target=worker, name="pinky-search")
        thread.daemon = True
        thread.start()

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
    window = SearchWindow("pinky-search.xml", kodi.addon_path(), "default", "1080i")
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
    # No hand-off to the `search_query` directory route: the window shows its
    # own results now, and routes a chosen one through our details window.
    # That route still exists for Kodi's own search and for a favourite.
    return query

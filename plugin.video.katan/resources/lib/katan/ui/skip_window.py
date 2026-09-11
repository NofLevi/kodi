# -*- coding: utf-8 -*-
"""The Skip intro button, drawn over the episode while its intro plays.

Opened with show(), never doModal. A modal dialog would block the service loop
that drives it, and the button has to come and go by itself as playback moves
into and out of the intro.

While it is up it has the remote, which is how every player's skip button
works: OK skips, and any other key puts it away for the rest of the episode.
That costs the first press of an arrow meant for seeking, and it is the
price of a button that can be reached at all.
"""
import xbmcgui

from .. import kodi

BUTTON_SKIP = 9210

# OK on the focused button arrives here as well as in onClick, and a mouse
# only moving over the screen is not a viewer saying no.
ACTION_SELECT_ITEM = 7
MOUSE_ACTIONS = range(100, 108)


class SkipButton(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(SkipButton, self).__init__()
        self.on_skip = None
        self.on_dismiss = None

    def onInit(self):
        try:
            self.setFocusId(BUTTON_SKIP)
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == BUTTON_SKIP and self.on_skip:
            self.on_skip()

    def onAction(self, action):
        action_id = action.getId()
        if action_id == ACTION_SELECT_ITEM or action_id in MOUSE_ACTIONS:
            return
        if self.on_dismiss:
            self.on_dismiss()


def open_button(on_skip, on_dismiss):
    """Put the button up; the caller closes it."""
    window = SkipButton("katan-skip.xml", kodi.addon_path(), "default", "1080i")
    window.on_skip = on_skip
    window.on_dismiss = on_dismiss
    window.show()
    return window

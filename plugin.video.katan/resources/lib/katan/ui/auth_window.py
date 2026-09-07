# -*- coding: utf-8 -*-
"""The sign-in screen: a code to scan, a link to type, and a progress bar.

Kodi's own progress dialog can hold text and a bar, which is what every device
flow in this add-on used to use. It cannot hold an image, and an image is the
entire point here - the difference between reading a URL and a six character
code off a television and pointing a phone at it.

The polling runs on a worker thread rather than in the window's own callbacks,
because a device flow waits minutes and a callback that sleeps is a frozen
interface. The worker only ever asks the caller's `poll` function what has
happened and writes the answer into window properties; closing the window is
done from `onAction` or by the worker setting a flag the window checks.
"""
import threading

import xbmcgui

from .. import kodi

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

BUTTON_CLOSE = 9110
PROGRESS = 9120


class AuthWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(AuthWindow, self).__init__()
        self.title = ""
        self.url = ""
        self.code = ""
        self.message = ""
        self.image = ""
        self.poll = None
        self.interval = 5
        self.ready = False
        self.stop = threading.Event()

    def prepare(self):
        """Properties before doModal, so the first render is complete.

        Same reason as the other windows: a control Kodi has not yet decided
        is visible cannot take focus, and a texture set after the first frame
        shows as a gap where the code should be.
        """
        self.setProperty("katan.auth.title", self.title)
        self.setProperty("katan.auth.url", self.url)
        self.setProperty("katan.auth.code", self.code)
        self.setProperty("katan.auth.message", self.message)
        self.setProperty("katan.auth.image", self.image)
        self.setProperty("katan.auth.status", "")

    def onInit(self):
        if self.ready:
            return
        self.ready = True
        self.prepare()
        try:
            self.setFocusId(BUTTON_CLOSE)
        except Exception:
            pass
        if self.poll:
            worker = threading.Thread(target=self._wait)
            worker.daemon = True
            worker.start()

    def _wait(self):
        """Ask the caller how things are going, until they are done."""
        while not self.stop.is_set():
            if self.stop.wait(self.interval):
                return
            remaining = self.poll()
            if remaining is None:
                self.stop.set()
                try:
                    self.close()
                except Exception:
                    pass
                return
            try:
                self.getControl(PROGRESS).setPercent(
                    max(0.0, min(100.0, (1.0 - remaining) * 100)))
            except Exception:
                pass

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.stop.set()
            self.close()

    def onClick(self, control_id):
        if control_id == BUTTON_CLOSE:
            self.stop.set()
            self.close()


def open_auth(title, url, code="", message="", poll=None, interval=5,
              scan=True):
    """Show the sign-in screen. Blocks until it closes.

    `scan` is False for the flows where a code is not wanted - there is no
    point drawing one for a URL somebody has already been told to open.
    """
    from . import signin

    window = AuthWindow("katan-auth.xml", kodi.addon_path(), "default", "1080i")
    window.title = title or ""
    window.url = url or ""
    window.code = code or ""
    window.message = message or ""
    window.poll = poll
    window.interval = max(1, int(interval))
    if scan and url:
        window.image = signin.code_image(url)
        if not window.image:
            # No code is not a failure. The link and the code are still on
            # screen and still work; that is the whole reason they are there.
            kodi.log("no QR code for %s, showing the link alone" % url)
    try:
        window.prepare()
        window.doModal()
    finally:
        window.stop.set()
        del window

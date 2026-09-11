# -*- coding: utf-8 -*-
"""Katan's own next-episode card, for a box without the Up Next add-on.

Up Next draws a better one and is preferred wherever it is installed; this
exists because on a fresh install it is not, and then the end of an episode was
the end of watching.

Opened with show(), never doModal, for the same reason as the skip button: the
service loop drives the countdown and has to keep running. Only back or Close
puts it away - the arrows move between its two buttons.
"""
import time

import xbmcgui

from .. import kodi

BUTTON_PLAY = 9310
BUTTON_CLOSE = 9311

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92


class NextUpCard(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(NextUpCard, self).__init__()
        self.on_play = None
        self.on_dismiss = None
        self.autoplay = False
        self.opened_at = 0.0

    def onInit(self):
        try:
            self.setFocusId(BUTTON_PLAY)
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == BUTTON_PLAY and self.on_play:
            self.on_play()
        elif control_id == BUTTON_CLOSE and self.on_dismiss:
            self.on_dismiss()

    def onAction(self, action):
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            if self.on_dismiss:
                self.on_dismiss()

    def set_countdown(self, seconds):
        self.setProperty("katan.next.countdown",
                         kodi.localize(32533, seconds) if seconds > 0 else "")


def open_card(nxt, countdown, on_play, on_dismiss):
    """Put the card up; the caller closes it.

    `countdown` is how many seconds until the next episode starts by itself,
    or 0 for a card that waits for this one to end.
    """
    window = NextUpCard("katan-nextup.xml", kodi.addon_path(), "default", "1080i")
    art = nxt.get("art") or {}
    window.setProperty("katan.next.number", "S%02dE%02d"
                       % (int(nxt.get("season") or 0), int(nxt.get("episode") or 0)))
    window.setProperty("katan.next.title",
                       nxt.get("episode_title") or nxt.get("show_title") or "")
    window.setProperty("katan.next.thumb", art.get("thumb") or art.get("fanart") or "")
    window.on_play = on_play
    window.on_dismiss = on_dismiss
    window.autoplay = countdown > 0
    window.opened_at = time.time()
    window.set_countdown(countdown)
    window.show()
    return window

# -*- coding: utf-8 -*-
"""Ask the box which buttons its remote really sends.

Two attempts at this were made by copying a keymap from a box that worked,
and both did nothing, for the same reason: Kodi already binds the features
those files named, so the buttons were never arriving as those features at
all. A copied keymap can only fix a remote identical to the one it came from.

So stop describing the remote and listen to it. A keymap is installed that
binds every input a television remote plausibly sends to a plugin call which
writes the name down; whoever is holding the remote presses the button they
mean, three times; whatever was written down is what that button sends on
that box. Then the real keymap is built from the answers.

Arrow keys and Select are never bound, because the probe is live while a
dialog is on screen and a box nobody can navigate is a box nobody can finish
this on. The probe comes out in a `finally`: a remote left half-learned is
worse than one never touched.
"""
import time

import xbmcgui

from .. import kodi, remotekeys

WAIT = 12.0          # seconds offered per button
STEP = 0.25
ROLES = (("Back", 32611), ("ContextMenu", 32612))


def _listen(action, prompt, dialog):
    """Wait for one button, pressed more than once. "" when nobody did."""
    started = time.time()
    while time.time() - started < WAIT:
        if dialog.iscanceled():
            return ""
        left = WAIT - (time.time() - started)
        dialog.update(int(100 - left / WAIT * 100), prompt)
        found = remotekeys.pressed_since(started)
        if found:
            return found
        kodi.sleep(int(STEP * 1000))
    return ""


def run():
    """Learn the remote. True when at least one button was understood."""
    if not kodi.yes_no(kodi.localize(32610), kodi.localize(32583)):
        return False
    if not remotekeys.probe_on():
        kodi.notify(kodi.localize(32319))
        return False

    roles, dialog = {}, xbmcgui.DialogProgress()
    try:
        dialog.create(kodi.localize(32583))
        for action, prompt_id in ROLES:
            remotekeys.forget()
            found = _listen(action, kodi.localize(prompt_id), dialog)
            if found:
                roles[action] = found
                kodi.log("remote: %s is %s" % (action, found), kodi.LOG_INFO)
    finally:
        dialog.close()
        remotekeys.probe_off()
        remotekeys.forget()

    if not roles:
        kodi.notify(kodi.localize(32613))
        return False
    if not remotekeys.install_learned(roles):
        kodi.notify(kodi.localize(32319))
        return False
    kodi.notify(kodi.localize(32614, len(roles)))
    return True

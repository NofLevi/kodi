# -*- coding: utf-8 -*-
"""Ask the box which buttons its remote really sends.

Two keymaps were shipped by copying one from a box where the remote worked,
and neither changed anything, for the same reason: Kodi already binds the
features those files named, so the projector's buttons were never arriving as
those features at all. A copied keymap can only fix a remote identical to the
one it came from.

So stop describing the remote and listen to it. A keymap is installed that
binds every input a television remote plausibly sends to a plugin call which
writes the name down; whoever is holding the remote presses the button they
mean, three times; whatever was written down is what that button sends on that
box. Then the real keymap is built from the answers.

The first question is "press OK", which is the control. It is a button we
already know works - it is how the viewer got to this screen. If OK is heard
and Back is not, the remote genuinely does not reach Kodi and no keymap can
help. If even OK is not heard, this screen is broken and should say so rather
than blame the remote. The first version asked neither, reported "nothing
arrived", and left both possibilities open.

The arrow keys are never bound: the probe is live while a dialog is on screen
and a box nobody can navigate is a box nobody can finish this on. The probe
comes out in a `finally` - a remote left half-learned is worse than one never
touched.
"""
import time

import xbmcgui

from .. import kodi, remotekeys

WAIT = 12.0          # seconds offered per button
STEP = 0.25
CONTROL = 32615      # "press OK" - proves the screen itself works
ROLES = (("Back", 32611), ("ContextMenu", 32612))


def _listen(prompt, dialog):
    """Wait for one button, pressed more than once. "" when nobody did."""
    remotekeys.forget()
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

    roles, heard_anything, dialog = {}, False, xbmcgui.DialogProgress()
    try:
        dialog.create(kodi.localize(32583))
        heard_anything = bool(_listen(kodi.localize(CONTROL), dialog))
        if heard_anything:
            for action, prompt_id in ROLES:
                found = _listen(kodi.localize(prompt_id), dialog)
                if found:
                    roles[action] = found
                    kodi.log("remote: %s is %s" % (action, found), kodi.LOG_INFO)
    finally:
        dialog.close()
        remotekeys.probe_off()
        remotekeys.forget()

    if not heard_anything:
        # Not the remote's fault as far as anyone can tell from here.
        kodi.log("remote probe heard nothing at all, not even OK",
                 kodi.LOG_INFO)
        kodi.notify(kodi.localize(32616))
        return False
    if not roles:
        kodi.notify(kodi.localize(32613))
        return False
    if not remotekeys.install_learned(roles):
        kodi.notify(kodi.localize(32319))
        return False
    kodi.notify(kodi.localize(32614, len(roles)))
    return True

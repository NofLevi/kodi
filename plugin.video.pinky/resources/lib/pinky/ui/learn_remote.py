# -*- coding: utf-8 -*-
"""Ask the box which buttons its remote really sends.

Two keymaps were shipped by copying one from a box where the remote worked,
and neither changed anything: Kodi's defaults for those features are not what
they were assumed to be, and the projector's buttons were never arriving as
them. A copied keymap can only fix a remote identical to the one it came from.

So stop describing the remote and listen to it. A keymap is installed that
binds every input a television remote plausibly sends to a plugin call which
writes the name down; whoever is holding the remote presses the button they
mean, three times; whatever was written down is what that button sends on that
box. Then the real keymap is built from the answers.

The first question is "press OK", which is the control: a button we already
know works, because it is how anybody reached this screen. If OK is heard and
Back is not, the remote genuinely does not reach Kodi. If even OK is not
heard, this screen is broken and should say so rather than blame the remote.

The screen is a window of our own with no buttons on it, and that is the
point. The first version used Kodi's progress dialog, which carries a Cancel
button - so the very first instruction, "press OK", pressed Cancel and closed
the screen. A question that cannot survive its own answer is not a question.
Nothing here can be pressed, nothing closes early, and each step ends on its
own clock.

The arrow keys are never bound: a box nobody can navigate is a box nobody can
finish this on. The probe comes out in a `finally` - a remote left
half-learned is worse than one never touched.
"""
import threading
import time

import xbmcgui

from .. import kodi, remotekeys

WAIT = 12.0          # seconds offered per button
STEP = 0.25
PROMPT = 9310        # the one label on the screen
CONTROL = 32615      # "press OK" - proves the screen itself works
ROLES = (("Back", 32611), ("ContextMenu", 32612))


class _Asking(xbmcgui.WindowXMLDialog):
    """A full-screen question with nothing on it that can be pressed.

    Modal, and driven from a thread it starts itself - the same shape as the
    sign-in window, which is the one window pattern this add-on has actually
    seen work on the projector.

    `onAction` is overridden to do nothing, which is what keeps a press of OK
    or Back from closing the window out from under the question being asked.
    It also writes down the action Kodi made of each press: a button that
    produces an action is a button Kodi can see, whatever the probe makes of
    it, and that is worth having in the log either way.
    """

    def __init__(self, *args, **kwargs):
        super(_Asking, self).__init__()
        self.seen = []
        self.roles = {}
        self.heard_anything = False
        self.done = threading.Event()

    def onInit(self):
        if self.done.is_set():
            return
        thread = threading.Thread(target=self._ask)
        thread.daemon = True
        thread.start()

    def onAction(self, action):
        try:
            self.seen.append(action.getId())
        except Exception:
            pass

    def say(self, text):
        try:
            self.getControl(PROMPT).setLabel(text)
        except Exception:
            pass

    def _listen(self, prompt):
        """Wait for one button, pressed more than once. "" when nobody did.

        Whatever has been heard so far is on screen while it waits, because
        the difference between "this box hears nothing" and "I have the wrong
        name for what it hears" is the whole question, and a screen that only
        counts down answers neither.
        """
        remotekeys.forget()
        started = time.time()
        while time.time() - started < WAIT:
            left = int(WAIT - (time.time() - started)) + 1
            self.say("%s   (%d)\n%s" % (prompt, left, self._so_far(started)))
            found = remotekeys.pressed_since(started)
            if found:
                return found
            kodi.sleep(int(STEP * 1000))
        return ""

    @staticmethod
    def _so_far(started):
        heard = [row.get("key") for row in remotekeys.heard()
                 if row.get("at", 0) >= started and row.get("key")]
        if not heard:
            return kodi.localize(32617)
        return "%s x%d" % (heard[-1], len(heard))

    def _ask(self):
        try:
            self.heard_anything = bool(self._listen(kodi.localize(CONTROL)))
            if self.heard_anything:
                for action, prompt_id in ROLES:
                    found = self._listen(kodi.localize(prompt_id))
                    if found:
                        self.roles[action] = found
                        kodi.log("remote: %s is %s" % (action, found),
                                 kodi.LOG_INFO)
        except Exception:
            kodi.log_exception("the remote screen gave up")
        finally:
            self.done.set()
            try:
                self.close()
            except Exception:
                kodi.log_exception("could not close the remote screen")


def run():
    """Learn the remote. True when at least one button was understood."""
    if not kodi.yes_no(kodi.localize(32610), kodi.localize(32583)):
        return False
    if not remotekeys.probe_on():
        kodi.notify(kodi.localize(32319))
        return False

    screen = _Asking("pinky-learn.xml", kodi.addon_path(), "default", "1080i")
    try:
        kodi.clear_busy_dialogs()
        screen.doModal()
    finally:
        screen.done.set()
        remotekeys.probe_off()
        remotekeys.forget()
    kodi.log("remote screen saw these actions: %s" % (screen.seen or "none"),
             kodi.LOG_INFO)

    if not screen.heard_anything:
        kodi.log("remote probe heard nothing at all, not even OK",
                 kodi.LOG_INFO)
        kodi.notify(kodi.localize(32616))
        return False
    if not screen.roles:
        kodi.notify(kodi.localize(32613))
        return False
    if not remotekeys.install_learned(screen.roles):
        kodi.notify(kodi.localize(32319))
        return False
    kodi.notify(kodi.localize(32614, len(screen.roles)))
    return True

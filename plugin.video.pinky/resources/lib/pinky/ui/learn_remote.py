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
        self.found = {}
        self.control = ""
        self.heard_anything = False
        self.done = threading.Event()

    def onInit(self):
        if self.done.is_set():
            return
        thread = threading.Thread(target=self._ask)
        thread.daemon = True
        thread.start()

    def onAction(self, action):
        """Every press, with the moment it arrived.

        This is the channel that demonstrably works. The keymap probe is the
        only way to learn a button Kodi has no action for, but the projector
        answered `[7, 7, 7]` to three presses of OK while the probe heard
        nothing at all - so what Kodi makes of a press is the evidence to
        lead with, and the probe is the second opinion.
        """
        try:
            self.seen.append({"id": action.getId(), "at": time.time()})
        except Exception:
            pass

    def _acted_since(self, moment, least=2):
        """The action id produced most often since `moment`, or None."""
        counts = {}
        for row in list(self.seen):
            if row.get("at", 0) >= moment:
                counts[row["id"]] = counts.get(row["id"], 0) + 1
        if not counts:
            return None
        best = max(counts, key=counts.get)
        return best if counts[best] >= least else None

    def say(self, text):
        try:
            self.getControl(PROMPT).setLabel(text)
        except Exception:
            pass

    def _listen(self, prompt):
        """Wait for one button, pressed more than once.

        Returns (name, action) - the probe's name for it, or "", and the
        action Kodi made of it, or None. Either one is an answer; both being
        empty is the only real "nothing happened".
        """
        remotekeys.forget()
        started = time.time()
        while time.time() - started < WAIT:
            left = int(WAIT - (time.time() - started)) + 1
            name = remotekeys.pressed_since(started)
            acted = self._acted_since(started)
            self.say("%s   (%d)\n%s"
                     % (prompt, left,
                        self._so_far(started, name, acted)))
            if name or acted is not None:
                kodi.sleep(400)   # let a third press land before deciding
                return (remotekeys.pressed_since(started),
                        self._acted_since(started))
            kodi.sleep(int(STEP * 1000))
        return "", None

    def _so_far(self, started, name, acted):
        heard = [row.get("key") for row in remotekeys.heard()
                 if row.get("at", 0) >= started and row.get("key")]
        bits = []
        if heard:
            bits.append("%s x%d" % (heard[-1], len(heard)))
        presses = [row for row in list(self.seen) if row.get("at", 0) >= started]
        if presses:
            bits.append("action %s x%d" % (presses[-1]["id"], len(presses)))
        return "   ".join(bits) or kodi.localize(32617)

    def _ask(self):
        try:
            name, acted = self._listen(kodi.localize(CONTROL))
            self.heard_anything = bool(name) or acted is not None
            self.control = name
            if not self.heard_anything:
                return
            for action, prompt_id in ROLES:
                name, acted = self._listen(kodi.localize(prompt_id))
                if name and name == self.control:
                    # OK again, not the button being asked for. Three presses
                    # of it arrive as one record and a straggler lands in the
                    # next question; binding that straggler is what took the
                    # OK key out of a projector for an evening.
                    kodi.log("ignoring %s as %s - that is the OK button"
                             % (name, action), kodi.LOG_INFO)
                    name = ""
                self.found[action] = (name, acted)
                if name:
                    self.roles[action] = name
                kodi.log("remote: %s -> probe %s, Kodi action %s"
                         % (action, name or "nothing", acted), kodi.LOG_INFO)
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
        kodi.log("the remote screen heard nothing at all, not even OK",
                 kodi.LOG_INFO)
        kodi.notify(kodi.localize(32616))
        return False

    # Two different answers, and they want different things said. A button
    # Kodi already has an action for is not a keymap problem at all - it
    # reaches Kodi, and whatever it fails to do afterwards is this add-on's
    # doing. Only a button Kodi makes nothing of needs binding, and only the
    # probe can name that one.
    already = [action for action, (name, acted) in screen.found.items()
               if not name and acted is not None]
    if already:
        kodi.log("Kodi already acts on: %s" % ", ".join(sorted(already)),
                 kodi.LOG_INFO)
    if not screen.roles:
        if already:
            kodi.notify(kodi.localize(32618))
            return True
        # Not reachable by any keymap - so offer the one button that is.
        if screen.control and kodi.yes_no(kodi.localize(32619),
                                          kodi.localize(32583)):
            if remotekeys.install_hold_fallback(screen.control):
                kodi.notify(kodi.localize(32620))
                return True
            kodi.notify(kodi.localize(32319))
            return False
        kodi.notify(kodi.localize(32613))
        return False
    if not remotekeys.install_learned(screen.roles):
        kodi.notify(kodi.localize(32319))
        return False
    kodi.notify(kodi.localize(32614, len(screen.roles)))
    return True

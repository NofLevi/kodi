# -*- coding: utf-8 -*-
"""Make a television remote behave, on the boxes where it does not.

Measured on a Byintek U4 projector: its remote reports to Android as a
joystick rather than a remote, so Kodi receives button *numbers* and has
nothing bound to them - Back does nothing, Menu does nothing. The buttonmap
already names those two "back" and "start"; what was missing was a keymap
giving those names an action.

The second half is Kodi's own default rather than the box's fault: Back
during fullscreen video minimises playback and leaves it running, which on a
projector reads as a film that will not close.

A keymap cannot ship inside an add-on - Kodi only reads them from the
profile - so this copies one in and asks Kodi to reload. Reversible: the
same screen takes it out again, and nothing else in the profile is touched.
"""
import io
import json
import os
import shutil
import time

import xbmc
import xbmcvfs

from . import kodi

FILE = "pinky-remote.xml"


def source():
    return os.path.join(kodi.addon_path(), "resources", "keymaps", FILE)


def keymap_dir():
    """Kodi's own keymap folder for this profile, made if it is missing."""
    path = xbmcvfs.translatePath("special://masterprofile/keymaps")
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            kodi.log_exception("could not make the keymap folder")
            return ""
    return path or ""


def target():
    folder = keymap_dir()
    return os.path.join(folder, FILE) if folder else ""


def installed():
    path = target()
    return bool(path) and os.path.isfile(path)


def install():
    """Copy the keymap in and reload. True when it is in place."""
    destination = target()
    if not destination:
        return False
    try:
        shutil.copyfile(source(), destination)
    except (IOError, OSError):
        kodi.log_exception("could not write the remote keymap")
        return False
    _reload()
    kodi.log("remote keymap installed")
    return True


def remove():
    """Take it out again and reload. True when it is gone."""
    destination = target()
    if not destination or not os.path.isfile(destination):
        return True
    try:
        os.remove(destination)
    except OSError:
        kodi.log_exception("could not remove the remote keymap")
        return False
    _reload()
    kodi.log("remote keymap removed")
    return True


def _reload():
    """Kodi reads keymaps at startup; this is how it is told to look again,
    so the buttons change under somebody's thumb rather than after a
    restart they were not told about."""
    try:
        xbmc.executebuiltin("ReloadKeymaps")
    except Exception:
        kodi.log_exception("could not reload the keymaps")


# --- Learning a remote we have never seen -------------------------------
#
# The shipped keymap was written by copying another box's profile, and on the
# Byintek projector it did nothing at all - because Kodi's own joystick keymap
# already binds the `back` and `start` features, so a file repeating those
# bindings changes nothing. The buttons were never arriving as those features.
#
# Guessing twice is enough. This asks the box instead: bind every input a
# television remote plausibly sends to a plugin call that writes down its
# name, then invite somebody to press the button they mean. Whatever is
# written down is what that button really sends, on that box, through that
# firmware - no log reading, no debug logging, no settings to find.

PROBE = "pinky-probe.xml"
HEARD = "remote-heard.json"

# Navigation and Select are deliberately absent: the probe has to stay
# installed while somebody uses a dialog, and a box whose arrow keys have
# been taken away is a box nobody can answer the question on.
PROBE_INPUTS = (
    ("joystick", ("b", "x", "y", "start", "back", "guide", "leftbumper",
                  "rightbumper", "leftthumbbutton", "rightthumbbutton")),
    ("keyboard", ("backspace", "escape", "menu", "c", "i", "tab")),
    ("remote", ("back", "title", "menu", "info", "display")),
    ("gamepad", ("back", "start", "b", "y")),
    ("appcommand", ("browser_back", "browser_home", "menu")),
)


def heard_file():
    return os.path.join(kodi.profile_path(), HEARD)


def probe_keymap():
    """The keymap that makes every candidate button announce itself."""
    rows = ["<keymap>", "  <global>"]
    for section, names in PROBE_INPUTS:
        rows.append("    <%s>" % section)
        for name in names:
            rows.append(
                "      <%s>RunPlugin(plugin://plugin.video.pinky/"
                "?action=learn&amp;key=%s.%s)</%s>" % (name, section, name, name))
        rows.append("    </%s>" % section)
    rows += ["  </global>", "</keymap>", ""]
    return "\n".join(rows)


def _write(path, text):
    try:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True
    except (IOError, OSError):
        kodi.log_exception("could not write %s" % path)
        return False


def probe_on():
    """Install the listening keymap. False when the folder is not writable."""
    folder = keymap_dir()
    if not folder or not _write(os.path.join(folder, PROBE), probe_keymap()):
        return False
    forget()
    _reload()
    return True


def probe_off():
    """Take the listening keymap out again, whatever else has happened."""
    folder = keymap_dir()
    if folder:
        try:
            os.remove(os.path.join(folder, PROBE))
        except OSError:
            pass
    _reload()


def record(key):
    """One press, written down. Called by the probe keymap through the
    plugin, so it has to be cheap and never raise into Kodi."""
    try:
        heard = _heard()
        heard.append({"key": key, "at": time.time()})
        with io.open(heard_file(), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(heard[-50:]))
        kodi.log("remote learned a press: %s" % key, kodi.LOG_INFO)
    except Exception:
        kodi.log_exception("could not write down a key press")


def _heard():
    try:
        with io.open(heard_file(), encoding="utf-8") as handle:
            found = json.loads(handle.read())
        return found if isinstance(found, list) else []
    except (IOError, OSError, ValueError):
        return []


def forget():
    try:
        os.remove(heard_file())
    except OSError:
        pass


def pressed_since(moment, least=2):
    """The input pressed most often since `moment`, or "".

    `least` presses rather than one because a remote that repeats, or a
    thumb that slips, should not decide what a button means. Asking for
    three and accepting two is the difference between a test somebody
    passes and one they retry.
    """
    counts = {}
    for row in _heard():
        if row.get("at", 0) >= moment and row.get("key"):
            counts[row["key"]] = counts.get(row["key"], 0) + 1
    if not counts:
        return ""
    best = max(counts, key=counts.get)
    return best if counts[best] >= least else ""


def learned_keymap(roles):
    """A keymap from {action: "section.name"} - what was actually pressed.

    Back is written twice over: once globally, and once for fullscreen video
    where Kodi's own default only minimises playback.
    """
    sections = {}
    for action, where in sorted(roles.items()):
        section, _, name = (where or "").partition(".")
        if section and name:
            sections.setdefault(section, []).append((name, action))
    rows = ["<keymap>", "  <global>"]
    for section in sorted(sections):
        rows.append("    <%s>" % section)
        for name, action in sections[section]:
            rows.append("      <%s>%s</%s>" % (name, action, name))
        rows.append("    </%s>" % section)
    rows += ["  </global>"]
    back = roles.get("Back")
    if back and "." in back:
        section, _, name = back.partition(".")
        rows += ["  <FullscreenVideo>", "    <%s>" % section,
                 "      <%s>Stop</%s>" % (name, name),
                 "    </%s>" % section, "  </FullscreenVideo>"]
    rows += ["</keymap>", ""]
    return "\n".join(rows)


def install_learned(roles):
    """Replace the shipped keymap with what this box actually sends."""
    destination = target()
    if not destination or not _write(destination, learned_keymap(roles)):
        return False
    _reload()
    kodi.log("remote keymap written from %d learned buttons" % len(roles),
             kodi.LOG_INFO)
    return True

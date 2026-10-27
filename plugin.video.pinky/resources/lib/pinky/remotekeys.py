# -*- coding: utf-8 -*-
"""Make a television remote behave, on the boxes where it does not.

A keymap cannot ship inside an add-on - Kodi only reads them from the
profile - so this writes one in and asks Kodi to reload. Reversible: the
same screen writes it again, and nothing else in the profile is touched.

What it writes is no longer a copy of a keymap from a box where the remote
happened to work. Two of those were shipped and neither changed anything,
because Kodi's own joystick keymap already binds the features they named:
the projector's buttons were never arriving as `back` and `start` at all.
The bindings now come from what the remote was heard to send - see the
second half of this file, and `ui/learn_remote.py`.
"""
import io
import os

import xbmc
import xbmcvfs

from . import kodi

FILE = "pinky-remote.xml"


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


def _reload():
    """Ask Kodi to read the keymaps again.

    `Action(reloadkeymaps)`, not `ReloadKeymaps`: it is an action name, and
    the builtin that runs an action by name is `Action`. Called the wrong way
    it is simply not a builtin, Kodi does nothing, and nothing says so - the
    projector's log has no second "load keymapping" line anywhere after the
    probe was installed, which is why the probe heard nothing it was
    listening for. Copied from script.fentastic.helper, which does this and
    works.
    """
    try:
        xbmc.executebuiltin("Action(reloadkeymaps)")
    except Exception:
        kodi.log_exception("could not reload the keymaps")


# --- The projector's remote --------------------------------------------
#
# Measured on a Byintek U4 on 20 October 2026, over FTP, by borrowing feature
# names from the buttonmap and pressing the keys: its Back key is joystick
# button 6 and its menu key button 7. Kodi's generated buttonmap named
# neither, and a button with no feature name produces no action at all -
# invisible to keymaps, to a sweep of all 156 virtual-key codes, and to any
# screen listening for presses. That is why two releases of remote fixes
# changed nothing: they were binding features the remote never sends.
#
# `back` and `start` were pointed at buttons 9 and 11, which are other keys.
# The left stick is dropped: the D-pad arrives as buttons 18-21 *and* as that
# stick's axes, so every press moved the selection twice.
#
# Opt-in, and only ever opt-in. A Mi Box's remote already works.

DEVICE_FILE = "Hi_keyboard_v0001_p0001_39b_2a.xml"
JOYSTICK = ' profile="game.controller.default"'

PROJECTOR_FEATURES = (
    ("a", 22), ("b", 1), ("back", 6), ("down", 20), ("guide", 10),
    ("left", 21), ("leftbumper", 12), ("leftthumb", 16), ("lefttrigger", 14),
    ("right", 19), ("rightbumper", 13), ("rightthumb", 17),
    ("righttrigger", 15), ("start", 7), ("up", 18), ("x", 3), ("y", 4),
)


def _write(path, text):
    try:
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return True
    except (IOError, OSError):
        kodi.log_exception("could not write %s" % path)
        return False


def buttonmap_dir():
    """Where peripheral.joystick keeps what it has learned about a device."""
    path = xbmcvfs.translatePath(
        "special://profile/addon_data/peripheral.joystick/resources/"
        "buttonmaps/xml/android")
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            kodi.log_exception("could not make the buttonmap folder")
            return ""
    return path or ""


def projector_buttonmap():
    rows = ['<?xml version="1.0" ?>', "<buttonmap>",
            '    <device name="Hi keyboard" provider="android" vid="0001" '
            'pid="0001" buttoncount="39" axiscount="2">',
            "        <configuration />",
            '        <controller id="game.controller.default">']
    for name, button in PROJECTOR_FEATURES:
        rows.append('            <feature name="%s" button="%d" />'
                    % (name, button))
    rows += ["        </controller>", "    </device>", "</buttonmap>", ""]
    return "\n".join(rows)


def projector_keymap():
    """What those two features should do once they have names.

    Kodi's own defaults for them are ActivateWindow(Home) and
    ActivateWindow(PlayerControls), which is neither key's job. And Kodi's
    Back during playback merely minimises the film and leaves it running,
    which on a projector reads as a film that will not close.

    During a film the menu key opens the player's own bottom bar - the `OSD`
    action, which is what Kodi binds `x` and `guide` to there. It used to open
    Pinky's subtitle list directly, on the reasoning that subtitles are what
    goes wrong mid-film; but that is one item on a bar that also holds audio
    track, subtitle offset, video settings and the playlist, and the key that
    reaches a menu should reach the menu. Subtitles are the first thing on it.
    """
    return "\n".join([
        "<keymap>",
        "  <global>",
        "    <joystick%s>" % JOYSTICK,
        "      <back>Back</back>",
        "      <start>ContextMenu</start>",
        "    </joystick>",
        "  </global>",
        "  <FullscreenVideo>",
        "    <joystick%s>" % JOYSTICK,
        "      <back>Stop</back>",
        "      <start>OSD</start>",
        "    </joystick>",
        "  </FullscreenVideo>",
        "</keymap>",
        "",
    ])


def apply_projector_remote():
    """Name the projector's buttons and bind them. True when both landed."""
    folder = buttonmap_dir()
    destination = target()
    if not folder or not destination:
        return False
    if not _write(os.path.join(folder, DEVICE_FILE), projector_buttonmap()):
        return False
    if not _write(destination, projector_keymap()):
        return False
    _reload()
    kodi.log("the projector's remote is set up: back=6, menu=7", kodi.LOG_INFO)
    return True


def projector_remote_chosen():
    from . import settings
    return settings.get_bool("ui.projector_remote", False)


def choose_projector_remote():
    """Apply it and remember, so it survives what might undo it."""
    from . import settings
    if not apply_projector_remote():
        return False
    settings.set("ui.projector_remote", True)
    return True


def _matches(path, wanted):
    try:
        with io.open(path, encoding="utf-8") as handle:
            return handle.read() == wanted
    except (IOError, OSError):
        return False


def reassert_projector_remote():
    """Put it back if something has taken it away. True when it rewrote.

    An add-on upgrade cannot lose these - they live in the profile, not in
    the add-on - but `peripheral.joystick` writes that buttonmap itself and
    will happily regenerate one without button 6 or 7 in it, at which point
    the remote silently goes back to having no Back key. So the choice is
    remembered and checked once per start, and only rewritten when what is
    on disk is not what was chosen.
    """
    if not projector_remote_chosen():
        return False
    folder = buttonmap_dir()
    if not folder:
        return False
    if (_matches(os.path.join(folder, DEVICE_FILE), projector_buttonmap())
            and _matches(target(), projector_keymap())):
        return False
    kodi.log("the projector's remote had been undone; putting it back",
             kodi.LOG_INFO)
    return apply_projector_remote()

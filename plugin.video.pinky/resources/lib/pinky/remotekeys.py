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
import os
import shutil

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

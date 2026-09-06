"""Thin wrappers over the Kodi Python API.

Everything that touches ``xbmc*`` lives here or in the ui package, so the rest
of the add-on stays importable under plain CPython for unit tests.
"""
import os
import sys
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.katan"

_ADDON = None


def addon():
    """Return a cached ``xbmcaddon.Addon`` instance."""
    global _ADDON
    if _ADDON is None:
        _ADDON = xbmcaddon.Addon(ADDON_ID)
    return _ADDON


def refresh_addon():
    """Drop the cached Addon object so settings written elsewhere are seen."""
    global _ADDON
    _ADDON = None


def addon_path():
    return xbmcvfs.translatePath(addon().getAddonInfo("path"))


def profile_path():
    """Per-user data directory, created on first use."""
    path = xbmcvfs.translatePath(addon().getAddonInfo("profile"))
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def subdir(*parts):
    """Return (and create) a directory beneath the profile path."""
    path = os.path.join(profile_path(), *parts)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def addon_version():
    return addon().getAddonInfo("version")


def kodi_major():
    """Kodi major version as an int (21 for Omega)."""
    try:
        return int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])
    except (ValueError, IndexError):
        return 0


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

LOG_DEBUG = xbmc.LOGDEBUG
LOG_INFO = xbmc.LOGINFO
LOG_WARNING = xbmc.LOGWARNING
LOG_ERROR = xbmc.LOGERROR


def log(message, level=LOG_DEBUG, component=None):
    prefix = "[Katan]" if not component else "[Katan/%s]" % component
    try:
        xbmc.log("%s %s" % (prefix, message), level)
    except Exception:  # pragma: no cover - logging must never raise
        pass


def log_error(message, component=None):
    log(message, LOG_ERROR, component)


def log_exception(context=""):
    """Log the active exception with a traceback, never re-raising."""
    import traceback

    log_error("%s\n%s" % (context, traceback.format_exc()))


class Timer(object):
    """Context manager that logs how long a block took."""

    def __init__(self, label, threshold_ms=0):
        self.label = label
        self.threshold_ms = threshold_ms
        self.start = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.elapsed_ms = (time.time() - self.start) * 1000
        if self.elapsed_ms >= self.threshold_ms:
            log("%s took %d ms" % (self.label, self.elapsed_ms))
        return False


# --------------------------------------------------------------------------
# strings and user interaction
# --------------------------------------------------------------------------


def localize(string_id, *args):
    """Return a localized string, optionally %-formatted."""
    try:
        text = addon().getLocalizedString(string_id)
    except Exception:
        text = ""
    if not text:
        text = str(string_id)
    if args:
        try:
            return text % args
        except (TypeError, ValueError):
            return text
    return text


def notify(message, heading=None, icon=None, time_ms=4000, sound=False):
    heading = heading or "Katan"
    icon = icon or os.path.join(addon_path(), "resources", "media", "icon.png")
    xbmcgui.Dialog().notification(heading, message, icon, time_ms, sound)


def ok_dialog(message, heading=None):
    return xbmcgui.Dialog().ok(heading or "Katan", message)


def yes_no(message, heading=None, yes_label="", no_label=""):
    return xbmcgui.Dialog().yesno(
        heading or "Katan", message, yeslabel=yes_label, nolabel=no_label
    )


def select(options, heading=None, preselect=-1, use_details=False):
    return xbmcgui.Dialog().select(
        heading or "Katan", options, preselect=preselect, useDetails=use_details
    )


def keyboard(default="", heading=None, hidden=False):
    """Modal text entry. Returns None when the user cancels."""
    kb = xbmc.Keyboard(default, heading or "Katan", hidden)
    kb.doModal()
    if not kb.isConfirmed():
        return None
    return kb.getText()


def busy_dialog(show=True):
    xbmc.executebuiltin("ActivateWindow(busydialognocancel)" if show
                        else "Dialog.Close(busydialognocancel)")


def run_builtin(command):
    xbmc.executebuiltin(command)


def activate_window(plugin_url):
    """Navigate the GUI to a plugin path, as if the user had clicked it."""
    xbmc.executebuiltin("ActivateWindow(Videos,%s,return)" % plugin_url)


def play_media(url):
    xbmc.executebuiltin("PlayMedia(%s)" % url)


def refresh_container():
    xbmc.executebuiltin("Container.Refresh")


def sleep(milliseconds):
    xbmc.sleep(milliseconds)


def abort_requested():
    return xbmc.Monitor().abortRequested()


# --------------------------------------------------------------------------
# window properties (cheap cross-process state)
# --------------------------------------------------------------------------

_HOME = 10000


def get_property(key):
    return xbmcgui.Window(_HOME).getProperty("katan.%s" % key)


def set_property(key, value):
    xbmcgui.Window(_HOME).setProperty("katan.%s" % key, str(value))


def clear_property(key):
    xbmcgui.Window(_HOME).clearProperty("katan.%s" % key)


def plugin_handle():
    """The handle Kodi passed to this plugin invocation, or -1."""
    try:
        return int(sys.argv[1])
    except (IndexError, ValueError):
        return -1

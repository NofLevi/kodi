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
    global _ADDON, _VERBOSE
    _ADDON = None
    _VERBOSE = None


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


_HAVE_ADDON = {}


def has_addon(addon_id):
    """Is another add-on installed and enabled?

    Asked once per process and remembered, because it is checked while
    building a channel list and constructing an Addon object is not free.
    """
    if addon_id not in _HAVE_ADDON:
        try:
            xbmcaddon.Addon(addon_id)
            _HAVE_ADDON[addon_id] = True
        except Exception:
            _HAVE_ADDON[addon_id] = False
    return _HAVE_ADDON[addon_id]


def has_adaptive():
    """Can this device play DASH?

    inputstream.adaptive ships with Kodi on most platforms but not all, and
    without it a DASH channel does not fail politely: Kodi opens nothing and
    says nothing. Twelve of the Israeli channels are DASH, so this is worth
    knowing before they are offered rather than after they are chosen.
    """
    return has_addon("inputstream.adaptive")


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------

LOG_DEBUG = xbmc.LOGDEBUG
LOG_INFO = xbmc.LOGINFO
LOG_WARNING = xbmc.LOGWARNING
LOG_ERROR = xbmc.LOGERROR


_VERBOSE = None


def verbose():
    """Has the viewer asked for this add-on's own messages in the log?

    Remembered for the process, because log() is called often and reading a
    Kodi setting is not free. refresh_addon() clears it, and the service calls
    that on every settings change.
    """
    global _VERBOSE
    if _VERBOSE is None:
        try:
            from . import settings
            _VERBOSE = settings.debug_enabled()
        except Exception:
            _VERBOSE = False
    return _VERBOSE


def log(message, level=LOG_DEBUG, component=None):
    """Write one line to the Kodi log.

    Everything here is DEBUG by default, and Kodi throws DEBUG away unless the
    whole application has debug logging switched on - which is a firehose
    nobody wants to read to find out why one film would not play. That made
    the "Verbose logging" setting a promise with nothing behind it:
    `settings.debug_enabled` existed and was never consulted, so the toggle
    did nothing at all.

    It now does the one useful thing it can. Switched on, this add-on's own
    messages are written at INFO, so they survive in an ordinary log without
    turning on Kodi's. Switched off - the default - nothing changes.
    """
    prefix = "[Katan]" if not component else "[Katan/%s]" % component
    if level == LOG_DEBUG and verbose():
        level = LOG_INFO
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


# Kodi's own busy dialogs. Both, because which one is up depends on how the
# plugin was invoked and there is no cheap way to ask.
BUSY_DIALOGS = ("busydialognocancel", "busydialog")


def clear_busy_dialogs():
    """Close Kodi's busy dialogs, and wait for them to actually go.

    A modal dialog refuses every other window: "Activate of window '13000'
    refused because there are active modal dialogs". Kodi puts a busy dialog
    up while a plugin runs as a script - which is what a context-menu entry
    is - so "choose a source" found its sources, asked for the picker, and
    the picker was refused. `doModal` then blocked on a window that was
    never shown, and the viewer waited on a screen where nothing happened.

    Closing is asynchronous, so this waits briefly rather than closing and
    hoping. Half a second is far longer than it takes and is only ever spent
    when a dialog was actually up.
    """
    if not xbmc.getCondVisibility("Window.IsActive(busydialognocancel)")             and not xbmc.getCondVisibility("Window.IsActive(busydialog)"):
        return False
    for name in BUSY_DIALOGS:
        xbmc.executebuiltin("Dialog.Close(%s,true)" % name)
    for _ in range(25):
        if not any(xbmc.getCondVisibility("Window.IsActive(%s)" % name)
                   for name in BUSY_DIALOGS):
            break
        xbmc.sleep(20)
    log("closed Kodi's busy dialog so our window could open")
    return True


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


_HANDLE = None


def set_plugin_handle(value):
    """Remember the handle the router was invoked with.

    `sys.argv` is right in Kodi and wrong everywhere else - under pytest it
    is pytest's own argv, so every handler saw -1 and the whole suite
    exercised the "no handle" path that Kodi only takes for RunPlugin. The
    router knows the real answer because it is handed argv; this is where it
    says so.
    """
    global _HANDLE
    try:
        _HANDLE = int(value)
    except (TypeError, ValueError):
        _HANDLE = None


def plugin_handle():
    """The handle Kodi passed to this plugin invocation, or -1."""
    if _HANDLE is not None:
        return _HANDLE
    try:
        return int(sys.argv[1])
    except (IndexError, ValueError):
        return -1

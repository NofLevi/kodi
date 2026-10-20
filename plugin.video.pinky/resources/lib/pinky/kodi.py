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

ADDON_ID = "plugin.video.pinky"

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
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def temp_dir():
    """A directory this add-on may write a temporary file into.

    Python's `tempfile` has nowhere to go inside Kodi's Android sandbox:
    TMPDIR is unset and /tmp does not exist, so `gettempdir()` raises
    FileNotFoundError - which is exactly what stopped every update on the
    projector, once while downloading and again while tidying up afterwards.

    `special://temp` is Kodi's own, defined on every platform it runs on, and
    it is cleared by Kodi rather than by us. The profile is the fallback for
    the case where even that cannot be resolved, because a slower path is
    better than an update that cannot happen.
    """
    try:
        path = xbmcvfs.translatePath("special://temp")
    except Exception:
        path = ""
    if path and os.path.isdir(path):
        return path
    return subdir("temp")


def subdir(*parts):
    """Return (and create) a directory beneath the profile path."""
    path = os.path.join(profile_path(), *parts)
    if not os.path.isdir(path):
        os.makedirs(path)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
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
    prefix = "[Pinky]" if not component else "[Pinky/%s]" % component
    if level == LOG_DEBUG and verbose():
        level = LOG_INFO
    try:
        xbmc.log("%s %s" % (prefix, message), level)
    except Exception:  # pragma: no cover - logging must never raise
        pass


def log_error(message, component=None):
    log(message, LOG_ERROR, component)


def log_exception(context=""):
    """Log exception type and stack locations, never exception values."""
    import os
    import sys
    import traceback

    exc_type, _value, trace = sys.exc_info()
    name = exc_type.__name__ if exc_type is not None else "Exception"
    frames = traceback.extract_tb(trace) if trace is not None else []
    locations = " <- ".join("%s:%d in %s" % (
        os.path.basename(frame.filename), frame.lineno, frame.name)
        for frame in frames)
    detail = "%s at %s" % (name, locations) if locations else name
    log_error("%s\n%s" % (context, detail))


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
    heading = heading or "Pinky"
    icon = icon or os.path.join(addon_path(), "resources", "media", "icon.png")
    xbmcgui.Dialog().notification(heading, message, icon, time_ms, sound)


def ok_dialog(message, heading=None):
    return xbmcgui.Dialog().ok(heading or "Pinky", message)


def yes_no(message, heading=None, yes_label="", no_label=""):
    return xbmcgui.Dialog().yesno(
        heading or "Pinky", message, yeslabel=yes_label, nolabel=no_label
    )


def select(options, heading=None, preselect=-1, use_details=False):
    return xbmcgui.Dialog().select(
        heading or "Pinky", options, preselect=preselect, useDetails=use_details
    )




# Kodi's own settings.xml: <maximumitems>3</maximumitems>.
KEYBOARD_LAYOUT_CAP = 3


def keyboard_layouts():
    """Every layout name Kodi knows, read out of its own layout files.

    The name is not written in those files. Each one holds
    `<layout language="Hebrew" layout="QWERTY">` and Kodi makes the name
    callers see - and the setting stores - by putting the two together:
    "Hebrew QWERTY". Read from a copy of Kodi 21 on 14 October 2026.

    Asking rather than assuming, because the name is what the setting stores
    and a name one word out is a layout that silently never appears.
    """
    folder = xbmcvfs.translatePath("special://xbmc/system/keyboardlayouts")
    names = []
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return names
    import xml.etree.ElementTree as ET
    for name in files:
        if not name.endswith(".xml"):
            continue
        try:
            root = ET.parse(os.path.join(folder, name)).getroot()
        except Exception:
            continue
        for element in root.iter("layout"):
            language = (element.get("language") or "").strip()
            shape = (element.get("layout") or "").strip()
            found = (" ".join(part for part in (language, shape) if part)
                     or element.get("name") or "")
            if found and found not in names:
                names.append(found)
    return names


def hebrew_layout():
    """What Kodi calls its Hebrew layout here, or "" when it has none."""
    for name in keyboard_layouts():
        if "hebrew" in name.lower() or u"עברית" in name:
            return name
    return ""


def _layout_list(value):
    """Kodi's layout setting, however it came back, as a list.

    It is declared `list[string]` with a `|` delimiter, and what arrives
    over JSON-RPC is a list on some boxes and one delimited string on
    others. Reading only the list shape is why the Mi Box ended up with
    Hebrew and the projector did not: the projector answered a string, and
    a string is not a list, and the function returned there without a word.
    """
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split("|") if part.strip()]
    return None


def ensure_hebrew_keyboard():
    """Put Hebrew among Kodi's own keyboard layouts, once.

    Kodi's keyboard is where this add-on sends anybody who wants to type or
    to talk, and it ships with **English QWERTY alone**: pressing the layout
    button cycled between English and accented Latin - the row of a-acute
    that reads as gibberish when what you wanted was Hebrew - so a catalogue
    titled entirely in Hebrew could not be searched in Hebrew at all.

    Three things have stopped this working, in order. The layout name was
    written out by hand, when Kodi builds it from two attributes of its own
    layout files. The "already done" flag was written *before* the attempt,
    so one failure was remembered forever as success. And the setting was
    read as a list when it can arrive as a delimited string, which failed
    silently and left nothing in the log to find it by.

    So nothing here returns quietly any more. Every path says what it saw,
    because a feature that fails without a word costs more to diagnose than
    it ever cost to write.

    Additive, and only ever additive: whatever else is configured stays, and
    a viewer who removes Hebrew again is not overruled on the next start.
    """
    from . import settings          # lazy: settings imports this module
    if settings.get_bool("ui.keyboard_hebrew_added", False):
        return False

    raw = _setting_value("locale.keyboardlayouts")
    layouts = _layout_list(raw)
    if layouts is None:
        log("Kodi would not say which keyboard layouts are on (%r)" % (raw,),
            LOG_INFO)
        return False

    wanted = hebrew_layout()
    if not wanted:
        log("Kodi here has no Hebrew keyboard layout; it offers %s"
            % (", ".join(keyboard_layouts()) or "nothing"), LOG_INFO)
        return False
    if wanted in layouts:
        log("Kodi already offers %s" % wanted, LOG_INFO)
        settings.set("ui.keyboard_hebrew_added", True)
        return False

    # Kodi caps this list at three. A box already holding three keeps them
    # and Hebrew is simply refused, so make room rather than fail: the
    # layout nobody chose is the one to drop.
    room = layouts + [wanted]
    if len(room) > KEYBOARD_LAYOUT_CAP:
        room = layouts[:KEYBOARD_LAYOUT_CAP - 1] + [wanted]
        log("keyboard layouts are capped at %d, so %s makes way for %s"
            % (KEYBOARD_LAYOUT_CAP, ", ".join(layouts[KEYBOARD_LAYOUT_CAP - 1:]),
               wanted), LOG_INFO)

    if not _set_setting_value("locale.keyboardlayouts", room):
        log("Kodi refused %s; it was holding %s"
            % (wanted, ", ".join(layouts)), LOG_INFO)
        return False
    settings.set("ui.keyboard_hebrew_added", True)
    log("added %s to Kodi's keyboard layouts, now %s"
        % (wanted, ", ".join(room)), LOG_INFO)
    return True


def _rpc(method, params):
    import json
    try:
        answer = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
    except Exception:
        log_exception("JSON-RPC %s failed" % method)
        return None
    if isinstance(answer, dict) and "error" in answer:
        log("JSON-RPC %s: %s" % (method, answer["error"]))
        return None
    return (answer or {}).get("result")


def _setting_value(name):
    result = _rpc("Settings.GetSettingValue", {"setting": name})
    return result.get("value") if isinstance(result, dict) else None


def _set_setting_value(name, value):
    return _rpc("Settings.SetSettingValue",
                {"setting": name, "value": value}) is True


def has_voice_input():
    """Whether holding OK on the remote can dictate into Kodi's keyboard.

    Android only, and it is Android doing it rather than Kodi: Kodi's keyboard
    dialog hands the field to the system IME, and the IME is what listens. No
    other platform Kodi runs on has that, so the answer is the platform.

    It cannot see whether Kodi actually holds the RECORD_AUDIO permission - a
    refused permission shows up as a recogniser that opens and hears nothing,
    which only Android's own settings can fix.
    """
    try:
        return bool(xbmc.getCondVisibility("System.Platform.Android"))
    except Exception:
        return False


def keyboard(default="", heading=None, hidden=False):
    """Modal text entry. Returns None when the user cancels."""
    kb = xbmc.Keyboard(default, heading or "Pinky", hidden)
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


def play_media(url, item=None):
    """Start playback outside a directory while preserving item properties."""
    if item is None:
        xbmc.executebuiltin("PlayMedia(%s)" % url)
        return
    from .ui import listing
    xbmc.Player().play(url, listing.make_list_item(item))


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
    return xbmcgui.Window(_HOME).getProperty("pinky.%s" % key)


def set_property(key, value):
    xbmcgui.Window(_HOME).setProperty("pinky.%s" % key, str(value))


def clear_property(key):
    xbmcgui.Window(_HOME).clearProperty("pinky.%s" % key)


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

# -*- coding: utf-8 -*-
"""Settings and accounts to a file, and back.

Kodi keeps the add-on's settings through every update, which is why nothing
here is needed for an update. It is for what an update is not: Pinky
uninstalled with its data, a box reset, or a second box to set up - the U4
and the Mi Box - where typing a TorBox key, a Gemini key and a Trakt sign-in
again with a remote is the cost being saved.

What goes in the file is every setting changed from its default, accounts
included, and the resume points. Not the cache, which rebuilds itself. The
file therefore holds every key the viewer has, and says so when it is
written; it is written only where they choose.
"""
import json
import time

from . import bookmarks, kodi, settings

FORMAT = 1
# Buttons, not values.
_NOT_SETTINGS = ("action.",)


def contents():
    """What a backup holds, as a dict ready to be written."""
    changed = {}
    for key, default in settings.DEFAULTS.items():
        if key.startswith(_NOT_SETTINGS):
            continue
        value = settings.get(key)
        if value is not None and str(value) != str(default):
            changed[key] = str(value)
    return {
        "pinky_backup": FORMAT,
        "version": kodi.addon_version(),
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "settings": changed,
        "resume_points": bookmarks.all_entries(),
    }


def restore(data):
    """Put a backup back. Returns how many settings it set.

    Only names this version still has are set - a backup from an older
    release may carry one that has since gone - and resume points are
    merged, the newer of two places for the same title winning.
    """
    if not isinstance(data, dict) or data.get("pinky_backup") != FORMAT:
        raise ValueError("not a Pinky backup")
    values = {}
    for key, value in (data.get("settings") or {}).items():
        if key in settings.DEFAULTS and not key.startswith(_NOT_SETTINGS):
            values[key] = str(value)
    if values:
        settings.set_many(values)
    places = data.get("resume_points")
    if isinstance(places, dict):
        bookmarks.merge(places)
    return len(values)


def export_to_file():
    """Ask where, and write the backup there."""
    import xbmcgui
    import xbmcvfs

    folder = xbmcgui.Dialog().browse(3, kodi.localize(32566), "files")
    if not folder:
        return ""
    name = "pinky-backup-%s.json" % time.strftime("%Y%m%d-%H%M")
    target = folder + name if folder.endswith(("/", "\\")) else folder + "/" + name
    text = json.dumps(contents(), ensure_ascii=False, indent=1, sort_keys=True)
    try:
        handle = xbmcvfs.File(target, "w")
        try:
            written = handle.write(bytearray(text.encode("utf-8")))
        finally:
            handle.close()
    except Exception:
        kodi.log_exception("could not write the backup")
        written = False
    if written is False:
        kodi.ok_dialog(kodi.localize(32572))
        return ""
    kodi.log("settings and accounts backed up to %s" % target)
    kodi.ok_dialog(kodi.localize(32567, name))
    return target


def import_from_file():
    """Ask which file, confirm, and restore it."""
    import xbmcgui
    import xbmcvfs

    path = xbmcgui.Dialog().browse(1, kodi.localize(32568), "files", ".json")
    if not path:
        return 0
    try:
        handle = xbmcvfs.File(path)
        try:
            raw = getattr(handle, "readBytes", handle.read)()
        finally:
            handle.close()
        data = json.loads(bytes(raw).decode("utf-8") if not isinstance(raw, str) else raw)
        if not isinstance(data, dict) or data.get("pinky_backup") != FORMAT:
            raise ValueError("not a Pinky backup")
    except Exception:
        kodi.log_exception("could not read %s as a backup" % path)
        kodi.ok_dialog(kodi.localize(32571))
        return 0
    if not kodi.yes_no(kodi.localize(32569, data.get("created") or "?")):
        return 0
    count = restore(data)
    kodi.log("restored %d settings from %s" % (count, path))
    kodi.ok_dialog(kodi.localize(32570, count))
    return count

"""Kodi's `xbmcaddon`, backed by an in-memory settings dict.

`runtime.boot` fills SETTINGS from the add-on's config.json, so Katan's
`settings.get(...)` reads the server's keys exactly as it reads Kodi's.
"""
import io
import os
import re

SETTINGS = {}
INFO = {}
STRINGS = {}


def reset(profile_dir, addon_dir):
    SETTINGS.clear()
    INFO.clear()
    INFO.update({
        "id": "plugin.video.katan",
        "name": "Katan",
        "version": "0.1.0",
        "path": addon_dir,
        "profile": profile_dir,
    })


class Addon(object):
    def __init__(self, id=None):
        self.id = id or INFO.get("id", "plugin.video.katan")

    def getAddonInfo(self, key):
        return INFO.get(key, "")

    def getSetting(self, key):
        return SETTINGS.get(key, "")

    def setSetting(self, key, value):
        SETTINGS[key] = str(value)

    def getSettingBool(self, key):
        return SETTINGS.get(key, "") == "true"

    def getSettingInt(self, key):
        try:
            return int(SETTINGS.get(key, "0"))
        except ValueError:
            return 0

    def getLocalizedString(self, string_id):
        return STRINGS.get(int(string_id), "")

    def openSettings(self):
        pass


def load_strings(po_path):
    """Katan's own strings.po, so `localize` returns real text."""
    STRINGS.clear()
    if not os.path.exists(po_path):
        return STRINGS
    text = io.open(po_path, encoding="utf-8").read()
    pattern = re.compile(r'msgctxt "#(\d+)"\s*\nmsgid "(.*?)"\s*\nmsgstr "(.*?)"',
                         re.DOTALL)
    for sid, msgid, msgstr in pattern.findall(text):
        STRINGS[int(sid)] = msgstr or msgid
    return STRINGS

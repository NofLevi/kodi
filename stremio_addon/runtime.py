# -*- coding: utf-8 -*-
"""Boot Katan's engines outside Kodi, with the server's own config.

The Kodi add-on is used as a library: its code is imported from
plugin.video.katan/resources/lib and never modified. What Kodi would supply -
settings, a profile directory, logging - comes from the shim modules and
from config.json.
"""
import io
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON_DIR = os.path.join(ROOT, "plugin.video.katan")
LIB = os.path.join(ADDON_DIR, "resources", "lib")
SHIM = os.path.join(HERE, "shim")
DATA = os.path.join(HERE, "data")
CONFIG = os.path.join(HERE, "config.json")

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 7000,
    # Every URL the add-on answers starts with this, so the tunnel address
    # alone is not enough to spend your Gemini quota. Made on first run.
    "access_token": "",
    "gemini_key": "",
    # An alias Google keeps pointing at the current Flash-Lite model, so the
    # retirement of any one version does not break translation.
    "gemini_model": "gemini-flash-lite-latest",
    "translate": True,
    "translate_ahead": True,
    "tmdb_key": "",
    "opensubtitles_apikey": "",
}

# config.json key -> Katan setting
SETTINGS = {
    "gemini_key": "subs.ai.gemini_key",
    "gemini_model": "subs.ai.gemini_model",
    "tmdb_key": "tmdb.apikey",
    "opensubtitles_apikey": "subs.opensubtitles.apikey",
}

_state = {"booted": False, "config": None}


def load_config(path=CONFIG, create=True):
    """config.json merged over the defaults; written with a token on first run."""
    config = dict(DEFAULTS)
    if os.path.isfile(path):
        with io.open(path, encoding="utf-8") as handle:
            config.update(json.load(handle))
    if create and not config.get("access_token"):
        config["access_token"] = secrets.token_urlsafe(12)
        save_config(config, path)
    return config


def save_config(config, path=CONFIG):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temporary = path + ".tmp"
    with io.open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(config, indent=2, ensure_ascii=False))
    os.replace(temporary, path)


def boot(config=None, data_dir=DATA):
    """Make `import katan` work, with this server's settings. Once per process."""
    if _state["booted"]:
        return _state["config"]
    config = dict(DEFAULTS, **(config or {}))
    for path in (LIB, SHIM):
        if path not in sys.path:
            sys.path.insert(0, path)

    import xbmcaddon

    profile = os.path.join(data_dir, "profile")
    if not os.path.isdir(profile):
        os.makedirs(profile)
    xbmcaddon.reset(profile, ADDON_DIR)
    xbmcaddon.load_strings(os.path.join(
        ADDON_DIR, "resources", "language", "resource.language.he_il",
        "strings.po"))

    from katan import kodi, settings

    kodi.refresh_addon()
    settings.set("subs.languages", "he,en")
    settings.set("subs.ai.enabled", "true" if config.get("translate") else "false")
    settings.set("subs.ai.engine", "gemini")
    # Fewer, larger requests: a 350-line episode in three calls rather than
    # seven, which is what decides how long the first episode waits.
    settings.set("subs.ai.chunk", "120")
    settings.set("cache.max_mb", "200")
    for key, setting in SETTINGS.items():
        if config.get(key):
            settings.set(setting, str(config[key]))

    # Katan's translator throws a whole file away over one line the model
    # skipped, after the quota has been spent on it. Lines left untranslated
    # keep their source text, which is better than no subtitle at all.
    from katan.subs.ai import translator
    translator.MIN_COMPLETION = 0.98

    _state.update(booted=True, config=config)
    return config


def config():
    return _state["config"] or dict(DEFAULTS)

"""Test setup: put the Kodi stubs and the add-on library on the import path.

Running the real add-on modules against stubs is the only way to catch import
errors, typos in Kodi API calls and settings drift without a Kodi install.
"""
import os
import shutil
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
ADDON_DIR = os.path.join(ROOT, "plugin.video.katan")
LIB_DIR = os.path.join(ADDON_DIR, "resources", "lib")
STUBS_DIR = os.path.join(TESTS_DIR, "stubs")

for path in (STUBS_DIR, LIB_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(autouse=True)
def kodi_environment(tmp_path):
    """Fresh settings, profile directory and caches for every test."""
    import xbmcaddon
    import xbmcgui
    import xbmcplugin

    profile = tmp_path / "profile"
    profile.mkdir()
    xbmcaddon.reset(str(profile), ADDON_DIR)
    xbmcaddon.load_strings(os.path.join(
        ADDON_DIR, "resources", "language", "resource.language.en_gb", "strings.po"))
    xbmcgui.Window.PROPERTIES.clear()
    del xbmcgui.NOTIFICATIONS[:]
    xbmcplugin.reset()

    from katan import cache, kodi
    from katan.meta import tmdb

    # The add-on ships a TMDB key so a fresh install has content. Tests must
    # not inherit it: "no key configured" is a real state the code still has
    # to handle - a revoked key, or somebody who removed it - and a suite that
    # cannot express it stops testing that path the day the key is added.
    # A test that wants a key sets the setting, which wins over the bundled one.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tmdb, "BUNDLED_KEY", "")

    kodi.refresh_addon()
    # The router remembers the handle it was invoked with, and a handle left
    # over from another test is exactly the sort of thing that makes a suite
    # depend on the order it runs in.
    kodi.set_plugin_handle(None)
    cache.close()

    yield

    monkeypatch.undo()
    cache.close()
    shutil.rmtree(str(profile), ignore_errors=True)


@pytest.fixture
def settings_module():
    from katan import settings
    return settings


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a test reaches the network by accident."""
    from katan import http

    def blocked(*args, **kwargs):
        raise AssertionError("test attempted a network request: %s" % (args,))

    monkeypatch.setattr(http, "request", blocked)
    return blocked

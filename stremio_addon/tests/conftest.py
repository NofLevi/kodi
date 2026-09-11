# -*- coding: utf-8 -*-
"""Boot Katan once for the whole run, in a throwaway data directory."""
import os
import sys

import pytest

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

import runtime  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def booted(tmp_path_factory):
    data = tmp_path_factory.mktemp("stremio-data")
    runtime.boot({"access_token": "", "gemini_key": ""}, data_dir=str(data))
    import subtitles
    subtitles.SUB_DIR = str(data / "subs")
    return data


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test reaches the network by accident."""
    from katan import http

    def blocked(*args, **kwargs):
        raise AssertionError("test attempted a network request: %s" % (args,))

    monkeypatch.setattr(http, "request", blocked)

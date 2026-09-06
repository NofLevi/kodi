"""Comet, a Stremio addon that fans out across many indexers server-side.

Its configuration is a base64 blob produced by its own web UI. The user pastes
that in, and we pass it through untouched.
"""
from ... import settings
from . import stremio

NAME = "comet"
BASE = "https://comet.elfhosted.com"


def search(meta):
    config = settings.get("sources.comet.config", "").strip()
    return stremio.fetch(settings.get("sources.comet.url", BASE),
                         config, meta, NAME)

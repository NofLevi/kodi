"""Torrentio.

Passing the debrid key in the config string is what makes this provider so
useful on a weak device: Torrentio then checks the cache itself and returns
ready-to-play links, so we skip a whole round of cache probing.
"""
from ... import settings
from . import stremio

NAME = "torrentio"
BASE = "https://torrentio.strem.fun"

# Sorting by quality then size puts the sensible releases first, which matters
# because we only keep the top handful anyway.
DEFAULT_OPTIONS = "sort=qualitysize"


def _config():
    parts = [DEFAULT_OPTIONS]
    extra = settings.get("sources.torrentio.options", "").strip()
    if extra:
        parts.append(extra)

    # Only one debrid key is sent. Sending several makes Torrentio slower for
    # no benefit, since we only need one service able to play the result.
    for name, key in (("torbox", settings.get("torbox.apikey")),
                      ("realdebrid", settings.get("realdebrid.token")),
                      ("premiumize", settings.get("premiumize.apikey")),
                      ("alldebrid", settings.get("alldebrid.apikey"))):
        if key.strip():
            parts.append("%s=%s" % (name, key.strip()))
            break
    return "|".join(parts)


def search(meta):
    return stremio.fetch(settings.get("sources.torrentio.url", BASE),
                         _config(), meta, NAME)

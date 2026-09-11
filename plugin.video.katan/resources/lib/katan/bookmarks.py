# -*- coding: utf-8 -*-
"""Where a film or an episode was stopped, kept on the device.

Resume used to come from Trakt alone, so without a Trakt account every film
started from the beginning however far into it somebody had got - and Trakt
cannot even be signed into on a remote without a registered application.

A JSON file in the profile rather than the SQLite cache, on purpose. The
cache evicts old entries once it reaches its size cap - 25 MB on the
low-memory profile - and "clear cache" wipes it, which is the first thing
anyone tries when something misbehaves. Neither should cost somebody their
place in a film.

Two processes use it: the service writes (the player lives there) and the
plugin reads (every listing it draws). So each keeps what it last read and
re-reads only when the file's modification time or size has moved.
"""
import io
import json
import os
import threading
import time

from . import kodi

FILENAME = "bookmarks.json"
LIMIT = 200             # plenty for one household; the oldest go first

_lock = threading.Lock()
_seen = {"stamp": None, "data": {}}


def path():
    return os.path.join(kodi.profile_path(), FILENAME)


def all_entries():
    """{state_key: {"position": s, "total": s, "at": epoch}}; {} when none."""
    with _lock:
        return dict(_read())


def save(key, position, total):
    """Remember `position` seconds into something `total` seconds long."""
    if not key or not position or float(position) <= 0:
        return
    with _lock:
        data = dict(_read())
        data[key] = {"position": round(float(position), 1),
                     "total": round(float(total or 0), 1),
                     "at": int(time.time())}
        if len(data) > LIMIT:
            oldest = sorted(data, key=lambda k: data[k].get("at", 0))
            for stale in oldest[:len(data) - LIMIT]:
                data.pop(stale, None)
        _write(data)


def clear(key):
    """Forget a place, once something has been finished."""
    with _lock:
        data = dict(_read())
        if data.pop(key, None) is not None:
            _write(data)


def _stamp(target):
    info = os.stat(target)
    return (info.st_mtime, info.st_size)


def _read():
    target = path()
    try:
        stamp = _stamp(target)
    except OSError:
        _seen.update(stamp=None, data={})
        return _seen["data"]
    if stamp != _seen["stamp"]:
        try:
            with io.open(target, encoding="utf-8") as handle:
                loaded = json.load(handle)
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            # A half-written file from a box switched off at the wall. Losing
            # the resume points is a nuisance; refusing to start is not an
            # option.
            kodi.log("resume points were unreadable, starting afresh")
            data = {}
        _seen.update(stamp=stamp, data=data)
    return _seen["data"]


def _write(data):
    target = path()
    temporary = target + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        # Replaced, not rewritten in place, so a reader never meets half a
        # file. Windows refuses the replace while a reader holds the file
        # open; that is logged and the next save tries again.
        os.replace(temporary, target)
    except OSError:
        kodi.log_exception("could not save a resume point")
        return
    try:
        _seen.update(stamp=_stamp(target), data=data)
    except OSError:
        _seen.update(stamp=None, data=data)

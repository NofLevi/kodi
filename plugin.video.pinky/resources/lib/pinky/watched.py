# -*- coding: utf-8 -*-
"""What has been watched to the end, kept on the device.

The resume point's twin, and it was simply missing. `trakt_state.annotate`
fills `playcount` from the Trakt mirror and from nowhere else, and that mirror
is empty without an account - which Trakt cannot be signed into on a remote,
because it needs an application registered with it first. So on the box most
people have, finishing an episode *deleted* the only record that it had been
touched: `player._keep_place` clears the resume point at 95% and nothing was
written in its place.

Everything downstream reads `playcount`, so everything downstream was wrong.
`details_window._first_unwatched` always answered the first episode in the
list, so pressing Play on a show somebody had worked through twenty episodes
of started it again at the beginning. The watched tick never appeared on any
episode. "Hide watched" hid nothing. And Continue Watching could only ever
show a part-watched episode, so a finished one vanished rather than advancing.

Anime is where it shows first, and that is not a coincidence: a fifty-episode
arc is the thing people watch in order, one after another, and it is the only
place a list that always starts at episode one is unmistakable.

The same shape as `bookmarks.py`, for the three reasons that file's own
docstring gives: two processes touch it, a half-written file has to start
afresh rather than raise, and it must not live in the SQLite cache, which is
LRU-evicted and wired to a "clear cache" button - the first thing anybody
presses when something misbehaves, and no reason to lose a year of viewing.

One `at` and no count. Every reader here treats `playcount` as a yes or no
(`not entry.get("playcount")`, `if item.get("playcount")`), so counting
rewatches would be a number nothing asks for - and the tick that notices 95%
runs every few seconds, which would have counted one episode thirty times.
"""
import io
import json
import os
import threading
import time

from . import kodi

FILENAME = "watched.json"
# Larger than the resume file's 200, because this is a history rather than a
# handful of places: a long-running anime is hundreds of episodes on its own.
# At roughly forty bytes an entry that is a file of about eighty kilobytes,
# and it is re-read only when it changes.
LIMIT = 2000

_lock = threading.Lock()
_seen = {"stamp": None, "data": {}}


def path():
    return os.path.join(kodi.profile_path(), FILENAME)


def all_entries():
    """{state_key: {"at": epoch}}; {} when nothing has been finished."""
    with _lock:
        return dict(_read())


def mark(key):
    """Remember that this was watched to the end.

    Writes nothing when the key is already there. That is not an
    optimisation: `_keep_place` is called by the playback tick as well as at
    the end, so between 95% and the credits this is asked thirty times for
    one episode, and a device with slow storage should not write the file
    thirty times to record the same fact.
    """
    if not key:
        return
    with _lock:
        data = dict(_read())
        if key in data:
            return
        data[key] = {"at": int(time.time())}
        if len(data) > LIMIT:
            oldest = sorted(data, key=lambda k: data[k].get("at", 0))
            for stale in oldest[:len(data) - LIMIT]:
                data.pop(stale, None)
        _write(data)


def merge(entries):
    """Take a backup's viewing history, keeping whatever either side has."""
    with _lock:
        data = dict(_read())
        changed = False
        for key, entry in (entries or {}).items():
            if not isinstance(entry, dict) or key in data:
                continue
            data[key] = {"at": int(entry.get("at") or 0)}
            changed = True
        if changed:
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
            kodi.log("the watched list was unreadable, starting afresh")
            data = {}
        _seen.update(stamp=stamp, data=data)
    return _seen["data"]


def _write(data):
    target = path()
    temporary = target + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        # Replaced rather than rewritten in place, so a reader never meets
        # half a file.
        os.replace(temporary, target)
    except OSError:
        kodi.log_exception("could not record what was watched")
        return
    try:
        _seen.update(stamp=_stamp(target), data=data)
    except OSError:
        _seen.update(stamp=None, data=data)

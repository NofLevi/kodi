# -*- coding: utf-8 -*-
"""What has been downloaded to the device, and what is still coming.

A JSON file in the profile, not a row in the SQLite cache, for the reasons
`bookmarks` already wrote down and one more of its own: the cache evicts its
oldest entries once it reaches its size cap, and "clear cache" is the first
thing anybody tries when something misbehaves. Losing a resume point that way
is a nuisance. Losing the record of a four-gigabyte film - while the film
itself stays on the disk, unreferenced and invisible - is worse than either.

Two processes use it, so it follows the same discipline as `bookmarks`: one
lock, an atomic replace rather than a rewrite in place, and a modification
stamp so the reader re-reads only when the writer has actually been.

**No debrid link is ever stored here.** A resolved URL carries the account
token - `play._resolve` is handed one and `play._fallbacks_after` refuses to
pass one between processes for that reason - so what is kept is the *source
record*: the torrent hash and the file id within it. The link is minted again
from those on every start and every resume, which costs one API call and is
how `torbox.sidecar_subtitles` already works.

The display snapshot is kept too. A downloaded film has to be listed and
played with no network at all, and every other row in this add-on rebuilds
its items from TMDB - so this is the one place that cannot.
"""
import hashlib
import io
import json
import os
import threading
import time

from .. import kodi

FILENAME = "downloads.json"
FOLDER = "downloads"

# Queued and waiting, being fetched now, finished, or given up on. A record
# never leaves the file on failure: the reason is worth showing, and the
# retry is worth offering.
QUEUED = "queued"
RUNNING = "downloading"
DONE = "done"
FAILED = "failed"

_lock = threading.Lock()
_seen = {"stamp": None, "data": {}}


def folder():
    """Where the files go. The setting when there is one, else the profile."""
    from .. import settings

    chosen = (settings.get("offline.folder") or "").strip()
    if chosen:
        if not os.path.isdir(chosen):
            try:
                os.makedirs(chosen)
            except OSError:
                kodi.log_exception("the downloads folder cannot be made")
                return kodi.subdir(FOLDER)
        return chosen
    return kodi.subdir(FOLDER)


def path():
    return os.path.join(kodi.profile_path(), FILENAME)


# -- identity ------------------------------------------------------------


def key_for(item_type, ids, season=None, episode=None):
    """The same key the resume points and the watched state use.

    Deliberately `trakt_state.state_key`, not one of this module's own: a
    downloaded episode has to line up with its bookmark so it resumes where
    it was left, and with its watched flag so the tick appears on it.
    """
    from ..meta import trakt_state

    return trakt_state.state_key(item_type, ids or {}, season, episode)


def key_for_request(request):
    """The key from route parameters alone - no TMDB call, so it works offline.

    This is what makes offline playback possible. `play.build_meta` needs
    TMDB for a title, and TMDB needs a network; the whole point of a download
    is to be watchable without one. The route already carries everything the
    key needs.
    """
    item_type = request.get("type") or "movie"
    ids = {"tmdb": request.get("tmdb"), "imdb": request.get("imdb")}
    if item_type == "episode":
        return key_for(item_type, ids, request.get("season"),
                       request.get("episode"))
    return key_for(item_type, ids)


def _safe_name(text):
    """An ASCII filename for a title that may have none.

    Lifted from `subs.auto._filename_key`, and for the reasons it records:
    `str.isalnum()` is true of Hebrew letters, so stripping to alphanumerics
    strips nothing from an Israeli title and writes the Hebrew straight onto
    the filesystem, which Android storage will not have. Lowercased, because
    Windows treats two names differing only in case as one file and Android
    treats them as two.
    """
    safe = "".join(c for c in (text or "").lower()
                   if (c.isalnum() and c.isascii()) or c in "-_")[:40]
    if safe:
        return safe
    return "t" + hashlib.sha1((text or "x").encode("utf-8")).hexdigest()[:12]


def file_for(record):
    """The full path this record's video belongs at.

    The release name is not used. It is the one string here that comes from a
    stranger - it arrives from a torrent index - and it routinely holds
    brackets, quotes, slashes and four hundred characters.
    """
    item = record.get("item") or {}
    stem = _safe_name(item.get("original_title") or item.get("title")
                      or record.get("key") or "")
    if record.get("type") == "episode":
        stem = "%s.s%02de%02d" % (stem, int(record.get("season") or 0),
                                  int(record.get("episode") or 0))
    source = record.get("source") or {}
    name = source.get("file_name") or ""
    extension = os.path.splitext(name)[1].lower()
    if not extension or len(extension) > 5 or not extension[1:].isalnum():
        extension = ".mkv"
    return os.path.join(folder(), stem + extension)


# -- reading -------------------------------------------------------------


def all_entries():
    """{key: record}; {} when nothing has ever been downloaded."""
    with _lock:
        return dict(_read())


def entry(key):
    with _lock:
        found = _read().get(key)
        return dict(found) if found else None


def find(request):
    """The finished download for this route, or None.

    Checks the file is still there: somebody who deletes a film with a file
    manager should get the stream back, not a dialog about a missing path.
    """
    record = entry(key_for_request(request))
    if not record or record.get("state") != DONE:
        return None
    target = record.get("file") or ""
    if not target or not os.path.isfile(target):
        kodi.log("%s was downloaded but the file is gone, so it will be "
                 "streamed instead" % record.get("key"), kodi.LOG_INFO)
        return None
    return record


def next_queued():
    """The oldest record still waiting, or None. One at a time, in order."""
    waiting = [r for r in all_entries().values()
               if r.get("state") in (QUEUED, RUNNING)]
    if not waiting:
        return None
    return sorted(waiting, key=lambda r: r.get("at") or 0)[0]


def snapshots():
    """The stored items, newest first, for the Downloaded row.

    Every item is the snapshot taken when it was queued, so this row draws
    with the network unplugged - which no other row in the add-on does.
    """
    out = []
    for key, record in all_entries().items():
        item = dict(record.get("item") or {})
        if not item:
            continue
        extra = dict(item.get("extra") or {})
        extra["downloaded"] = record.get("state")
        extra["offline_key"] = key
        extra["offline_bytes"] = int(record.get("got") or 0)
        extra["offline_size"] = int(record.get("size") or 0)
        item["extra"] = extra
        # While it is still coming, the bar under the poster shows how much of
        # it has arrived. It is the same property the resume bar uses, so the
        # skin needs nothing new - see `listing._set_start_offset`.
        size = int(record.get("size") or 0)
        if record.get("state") != DONE and size > 0:
            item["resume"] = {"position": int(record.get("got") or 0),
                              "total": size}
        out.append((record.get("at") or 0, item))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _at, item in out]


def total_bytes():
    """What the finished downloads take up, counting the files themselves."""
    total = 0
    for record in all_entries().values():
        target = record.get("file") or ""
        for candidate in (target, target + ".part"):
            try:
                total += os.path.getsize(candidate)
            except OSError:
                pass
    return total


# -- writing -------------------------------------------------------------


def queue(record):
    """Add something to the queue. Returns the key, or "" if it is already in.

    Deliberately refuses a duplicate rather than starting a second copy: the
    second would write to the same filename as the first.
    """
    key = record.get("key") or ""
    if not key:
        return ""
    with _lock:
        data = dict(_read())
        if key in data and data[key].get("state") in (QUEUED, RUNNING, DONE):
            return ""
        stored = dict(record)
        stored.setdefault("state", QUEUED)
        stored.setdefault("got", 0)
        stored.setdefault("note", "")
        stored["at"] = int(time.time())
        stored["file"] = stored.get("file") or file_for(stored)
        data[key] = stored
        _write(data)
    return key


def update(key, **fields):
    """Change a record in place. Silent when the record has been removed."""
    with _lock:
        data = dict(_read())
        record = data.get(key)
        if record is None:
            return False
        merged = dict(record)
        merged.update(fields)
        data[key] = merged
        _write(data)
        return True


def remove(key):
    """Forget a download and delete what is on the disk. True if it existed.

    The sidecar subtitles go with it. They are named after the video's stem,
    so they would otherwise sit in the folder for ever with nothing to play
    beside - and the next download of the same film would silently inherit
    them.
    """
    with _lock:
        data = dict(_read())
        record = data.pop(key, None)
        if record is None:
            return False
        _write(data)
    _delete_files(record)
    return True


def clear_all():
    """Delete every download. Returns how many records went."""
    records = list(all_entries().values())
    with _lock:
        _write({})
    for record in records:
        _delete_files(record)
    return len(records)


def _delete_files(record):
    target = record.get("file") or ""
    if not target:
        return
    stem = os.path.splitext(target)[0]
    gone = []
    for candidate in (target, target + ".part"):
        if _unlink(candidate):
            gone.append(os.path.basename(candidate))
    directory = os.path.dirname(target)
    try:
        names = os.listdir(directory)
    except OSError:
        names = []
    for name in names:
        # The subtitle this download wrote: same stem, ".<language>.srt".
        full = os.path.join(directory, name)
        if full != target and full.startswith(stem + ".") and name.endswith(".srt"):
            if _unlink(full):
                gone.append(name)
    if gone:
        kodi.log("removed %s" % ", ".join(gone), kodi.LOG_INFO)


def _unlink(target):
    try:
        os.remove(target)
        return True
    except OSError:
        return False


# -- the file ------------------------------------------------------------


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
            # A box switched off at the wall mid-write. The films are still on
            # the disk; refusing to start is not an option, so the index
            # starts afresh and they can be removed by hand.
            kodi.log("the download index was unreadable, starting afresh")
            data = {}
        _seen.update(stamp=stamp, data=data)
    return _seen["data"]


def _write(data):
    target = path()
    temporary = target + ".tmp"
    try:
        with io.open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(temporary, target)
    except OSError:
        kodi.log_exception("could not save the download index")
        return
    try:
        _seen.update(stamp=_stamp(target), data=data)
    except OSError:
        _seen.update(stamp=None, data=data)

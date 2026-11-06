# -*- coding: utf-8 -*-
"""Move the bytes. Runs in the service, one file at a time.

Modelled on `updater.download`, which is the only other place in the add-on
that reads a response body rather than parsing it, with the two things a film
needs and a 3 MB zip does not: it resumes, and it watches the free space.

Resume is the whole reason the link is not stored. A download interrupted by a
restart, an abort or a flat wifi comes back to a `.part` file and asks for the
rest of it with a Range header - but the debrid link it started with carries
the account token and must not be written to the disk, so it is minted again
from the stored source each time. That costs one API call against a TorBox
budget of three hundred a minute.
"""
import os
import shutil
import time

from .. import http, kodi, settings
from . import library

CHUNK = 64 * 1024

# How often the index learns how far it has got. Every chunk would mean a JSON
# write and an fsync every 64 KB, which on a television box is most of the
# work; every 8 MB is about once a second on house wifi and costs nothing.
PROGRESS_EVERY = 8 * 1024 * 1024

# Never fill the disk. Android gets unpleasant well before zero - and the box
# this runs on shares its storage with Kodi's own thumbnail and texture cache,
# which grows without asking.
MARGIN = 1024 ** 3

# Generous on purpose: this is a multi-gigabyte body over house wifi, and the
# read timeout applies between chunks rather than to the whole transfer.
TIMEOUT = (5, 60)


def run(monitor=None):
    """Work through the queue. Returns how many files were finished.

    `monitor` is the service's `xbmc.Monitor`. Without one this still works,
    which is what makes it testable.
    """
    done = 0
    while True:
        if _stopping(monitor):
            return done
        record = library.next_queued()
        if record is None:
            return done
        key = record.get("key") or ""
        if not key:
            return done
        if _one(record, monitor):
            done += 1
            continue
        # Still queued means it was put back rather than given up on - an
        # abort, or a connection that dropped. Stopping here lets the next
        # tick retry it; carrying on would spin on the same file.
        after = library.entry(key) or {}
        if after.get("state") in (library.QUEUED, library.RUNNING):
            return done


def _one(record, monitor=None):
    """Fetch one record to completion. True when the file is on the disk."""
    key = record["key"]
    if not (record.get("source") or {}).get("hash"):
        # Queued as an intention rather than a choice - a whole season. The
        # search happens here, in the service, one episode at a time, because
        # doing fourteen of them in the plugin would freeze the screen for a
        # minute and a half.
        source = _source_for(record)
        if not source:
            return _failed(key, kodi.localize(32629))
        library.update(key, source=source,
                       size=int(source.get("file_size") or 0))
        record = library.entry(key) or record

    target = record.get("file") or library.file_for(record)
    part = target + ".part"
    expected = int(record.get("size") or 0) \
        or int((record.get("source") or {}).get("file_size") or 0)

    folder = os.path.dirname(target)
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError:
        return _failed(key, kodi.localize(32631))

    got = _already(part)
    if expected and not _room_for(folder, expected - got):
        return _failed(key, kodi.localize(32630))

    url = _link(record)
    if not url:
        return _failed(key, kodi.localize(32629))

    library.update(key, state=library.RUNNING, file=target, size=expected,
                   got=got, note="")
    kodi.log("downloading %s to %s, %d bytes already here"
             % (key, os.path.basename(target), got), kodi.LOG_INFO)

    headers = {"Accept-Encoding": "identity"}
    if got:
        headers["Range"] = "bytes=%d-" % got
    response = http.get(url, stream=True, timeout=TIMEOUT, retries=1,
                        headers=headers)
    if response is None or response.status_code >= 400:
        return _failed(key, kodi.localize(32629))

    try:
        if got and response.status_code != 206:
            # The server ignored the Range and is sending the film from the
            # start. Appending that to what is already here would produce a
            # file of the right size and the wrong contents, which plays for
            # a while and then does not - the worst of the available
            # outcomes. Start again instead, and say so.
            kodi.log("%s does not do ranged reads, so %s starts again"
                     % (http._host(url), key), kodi.LOG_INFO)
            got = 0
            _unlink(part)
        total = _declared(response, got)
        if total and total != expected:
            # The server decides. The stored figure comes from a torrent
            # index and is an estimate; believing it over Content-Length
            # meant a file that was genuinely smaller than advertised came
            # up "short", went back to the queue and was retried for ever.
            expected = total
            library.update(key, size=expected)

        got = _write(key, part, response, got, expected, folder, monitor)
        if got is None:
            library.update(key, state=library.QUEUED)
            return False
        if (library.entry(key) or {}).get("state") == library.FAILED:
            # The disk filled. Saying "queued" below would hand it straight
            # back to the queue, which would fill the disk again on the next
            # tick, for ever.
            return False
    finally:
        try:
            response.close()
        except Exception:
            pass

    if expected and got != expected:
        # Short of the declared length: the connection dropped. The part file
        # stays, so the next pass asks for the rest rather than starting over.
        library.update(key, state=library.QUEUED, got=got,
                       note=kodi.localize(32632))
        return False

    try:
        os.replace(part, target)
    except OSError:
        return _failed(key, kodi.localize(32631))

    library.update(key, state=library.DONE, got=got, size=got, note="")
    kodi.log("%s is downloaded (%s)" % (key, _mb(got)), kodi.LOG_INFO)
    _subtitle(record, target)
    kodi.notify(kodi.localize(32628, _title(record)))
    return True


def _write(key, part, response, got, expected, folder, monitor):
    """Append the body to the part file. Returns the new total, or None if
    Kodi is shutting down."""
    since = 0
    checked = 0
    try:
        with open(part, "ab") as out:
            while True:
                if _stopping(monitor):
                    kodi.log("Kodi is stopping; %s keeps its %s and will "
                             "resume" % (key, _mb(got)), kodi.LOG_INFO)
                    return None
                # decode_content=False, because that is the only path through
                # either HTTP backend that streams: `.content` would pull a
                # whole film into memory on a box with a gigabyte of it.
                chunk = response.raw.read(CHUNK, decode_content=False)
                if not chunk:
                    break
                out.write(chunk)
                got += len(chunk)
                since += len(chunk)
                checked += len(chunk)
                if since >= PROGRESS_EVERY:
                    since = 0
                    library.update(key, got=got)
                if checked >= 64 * 1024 ** 2:
                    checked = 0
                    remaining = (expected - got) if expected else 0
                    if not _room_for(folder, remaining):
                        library.update(key, state=library.FAILED, got=got,
                                       note=kodi.localize(32630))
                        return got
    except Exception:
        kodi.log_exception("the download of %s broke off" % key)
    library.update(key, got=got)
    return got


# -- the link ------------------------------------------------------------


def _source_for(record):
    """Search for a source for an episode queued without one. {} if none fits.

    Takes the best **cached** one, which is the first in a ranked list that
    carries the flag. An uncached source would mean adding a torrent to the
    debrid service, and a season's worth of those would spend the whole
    sixty-an-hour budget on one series.
    """
    from .. import play
    from ..sources import aggregator
    from . import queueing

    meta = play.build_meta({
        "type": record.get("type") or "movie",
        "tmdb": (record.get("ids") or {}).get("tmdb"),
        "imdb": (record.get("ids") or {}).get("imdb"),
        "season": record.get("season"),
        "episode": record.get("episode"),
    })
    if not meta.get("title"):
        kodi.log("TMDB knows nothing about %s, so no source can be found"
                 % record.get("key"), kodi.LOG_INFO)
        return {}
    try:
        found = aggregator.find(meta) or []
    except Exception:
        kodi.log_exception("searching for %s failed" % record.get("key"))
        return {}
    for candidate in found:
        if candidate.get("cached"):
            return queueing.source_for_storage(candidate, meta)
    kodi.log("none of the %d sources for %s is cached"
             % (len(found), record.get("key")), kodi.LOG_INFO)
    return {}


def _link(record):
    """Mint a fresh debrid link from the stored source. "" when it cannot.

    The stored source is the same dict the picker chose, minus its `url`:
    `_resolve` returns a direct url when it finds one, so leaving it in would
    hand back a link saved weeks ago - and putting it on the disk in the first
    place is what this add-on refuses to do with anything bearing an account
    token.

    After the first successful resolve the source carries `file_id`, which is
    the first thing `base.pick_file` matches on - so every later resume picks
    the same file out of a season pack as the first attempt did, rather than
    re-deciding.
    """
    from .. import play

    source = dict(record.get("source") or {})
    source.pop("url", None)
    if not source:
        return ""
    url = play._resolve(source)
    if url:
        # What resolving taught us about which file this is, kept for the
        # next resume.
        learnt = {k: source.get(k) for k in
                  ("file_id", "file_index", "file_name", "file_size")
                  if source.get(k) is not None}
        if learnt:
            stored = dict(record.get("source") or {})
            stored.update(learnt)
            stored.pop("url", None)
            library.update(record["key"], source=stored)
    return url


# -- subtitles -----------------------------------------------------------


def _subtitle(record, target):
    """Put a subtitle beside the film, if one can be found.

    Beside it, not in the subtitle cache: `subs.auto.prune_cache` keeps that
    folder to sixty files by age and exempts nothing, so the subtitle for a
    film downloaded for a journey would be deleted by the next few things
    watched at home. Kodi loads `<stem>.<language>.srt` from the video's own
    folder without being asked.

    Everything here is best-effort. A film with no subtitle is still a film;
    a download that failed because a subtitle search did would not be.
    """
    if not settings.get_bool("offline.subtitles", True):
        return ""
    try:
        from ..subs import auto

        languages = settings.subtitle_languages() or ["he"]
        path, _report = auto.find_and_prepare(_meta_for(record, target),
                                              languages)
        if not path or not os.path.isfile(path):
            kodi.log("no subtitle was found for %s" % record["key"])
            return ""
        # The language is the last piece before .srt - Kodi reads it from
        # there, which is why `auto.name_for` puts its own variant marker in
        # front of it.
        language = os.path.splitext(os.path.splitext(path)[0])[1] or ".he"
        beside = os.path.splitext(target)[0] + language + ".srt"
        shutil.copyfile(path, beside)
        kodi.log("subtitle saved beside the film as %s"
                 % os.path.basename(beside), kodi.LOG_INFO)
        return beside
    except Exception:
        kodi.log_exception("could not save a subtitle beside %s"
                           % record.get("key"))
        return ""


def _meta_for(record, target):
    """The shape `subs.auto` expects, built from the stored record alone."""
    item = dict(record.get("item") or {})
    source = dict(record.get("source") or {})
    return {
        "type": record.get("type") or "movie",
        "ids": record.get("ids") or {},
        "title": item.get("title") or "",
        "original_title": item.get("original_title") or "",
        "original_language": item.get("original_language") or "",
        "year": item.get("year") or 0,
        "season": int(record.get("season") or 0),
        "episode": int(record.get("episode") or 0),
        "item": item,
        "duration": int(item.get("duration") or 0),
        "stream_url": target,
        "source": {
            "release": source.get("title", ""),
            "file_name": source.get("file_name", ""),
            "file_size": source.get("file_size") or 0,
            "file_index": source.get("file_index"),
            "file_id": source.get("file_id"),
            "torrent_hash": source.get("hash", ""),
            "provider": source.get("provider", ""),
            "quality": source.get("quality", ""),
        },
    }


# -- small things --------------------------------------------------------


def _stopping(monitor):
    if monitor is None:
        return False
    try:
        return bool(monitor.abortRequested())
    except Exception:
        return False


def _already(part):
    try:
        return int(os.path.getsize(part))
    except OSError:
        return 0


def _declared(response, got):
    """The whole file's length, from Content-Length or Content-Range."""
    ranged = (response.headers.get("Content-Range") or "").strip()
    if "/" in ranged:
        tail = ranged.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    try:
        length = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return length + got if length else 0


def _room_for(folder, wanted):
    """Is there space for `wanted` bytes, and a gigabyte left over?

    The first free-space check in the add-on. Without it a film simply
    stopped at whatever the disk ran out at, and the part file that was left
    looked exactly like one interrupted by a restart - so the next pass asked
    for the rest of it, and filled the disk again.
    """
    try:
        free = shutil.disk_usage(folder).free
    except Exception:
        # A path the platform will not report on. Refusing to download at all
        # would be worse than finding out the hard way.
        return True
    if free >= max(0, wanted) + MARGIN:
        return True
    kodi.log("%s has %s free, which is not enough for %s plus a margin"
             % (folder, _mb(free), _mb(wanted)), kodi.LOG_INFO)
    return False


def _failed(key, note):
    library.update(key, state=library.FAILED, note=note)
    record = library.entry(key) or {}
    kodi.log("download of %s failed: %s" % (key, note), kodi.LOG_INFO)
    kodi.notify("%s - %s" % (_title(record), note))
    return False


def _title(record):
    item = record.get("item") or {}
    title = item.get("title") or record.get("key") or "?"
    if record.get("type") == "episode":
        return "%s %dx%02d" % (title, int(record.get("season") or 0),
                               int(record.get("episode") or 0))
    return title


def _mb(count):
    count = int(count or 0)
    if count >= 1024 ** 3:
        return "%.1f GB" % (count / float(1024 ** 3))
    return "%d MB" % (count / float(1024 ** 2))


def _unlink(target):
    try:
        os.remove(target)
    except OSError:
        pass

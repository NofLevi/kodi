# -*- coding: utf-8 -*-
"""Which releases carry their own subtitles, for the picker to say so.

A release that ships subtitles beside the video is the best row on the page
and the picker had no way to say which one that was. Measured on Hikaru no
Go: one torrent holds seventy-six `.srt` files, one per episode, named to
match - and it looked exactly like the seven rows either side of it.

**Only the file list.** Asking the debrid service what is inside a torrent it
is already holding costs one lookup; on TorBox `createtorrent` answers
"Found Cached Torrent" in about half a second for something already cached,
and six of them through the shared pool take about a second.

Reading the *embedded* tracks is the other half of the question and is not
done here, because it is a different order of cost: it needs a real download
link and two ranged reads of the file itself. Measured on Top Gun: Maverick,
six sources through the four-worker pool took **eleven seconds** - and this
add-on has already learned once what holding the picker on a spinner for
twenty-six seconds feels like. What ships beside the video is knowable
cheaply; what is muxed inside it is not, and saying only the first is better
than making the viewer wait for both.

Cached sources only. `_find` adds a torrent that is not there, uncached adds
are capped at sixty an hour, and spending that quota to decorate a list
nobody asked to act on would be the picker charging for being opened.
"""
from .. import kodi, http

# Per release, because the answer is the torrent's and the torrent does not
# change. Kept for the session rather than written down: it is a fact about
# somebody's debrid account, not about the catalogue.
_KNOWN = {}

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

# How many rows are worth asking about. The picker draws six by default and
# thirteen with everything shown; past that the viewer is scrolling rather
# than choosing, and every one is a request against somebody's account.
MAX_ROWS = 8

# Short, because this decorates a list rather than deciding anything. A row
# that does not answer in time is drawn without the mark, which is exactly
# what it looked like before.
DEADLINE = 4.0


def _subtitle_count(files):
    return sum(1 for entry in files or []
               if str(entry.get("name") or entry.get("short_name") or "")
               .lower().endswith(SUBTITLE_EXTENSIONS))


def _ask(source):
    from ..debrid import registry

    client = registry.client_with_sidecars(source)
    if client is None:
        return 0
    try:
        files, _link_for = client.sidecar_subtitles(source)
    except Exception:
        kodi.log_exception("could not look inside %s" % source.get("hash", "")[:12])
        return 0
    return _subtitle_count(files)


def annotate(sources):
    """Mark the rows whose torrent carries subtitle files, in place."""
    asking = []
    for source in (sources or [])[:MAX_ROWS]:
        info_hash = (source.get("hash") or "").lower()
        if not info_hash or not source.get("cached"):
            continue
        if info_hash in _KNOWN:
            source["bundled_subs"] = _KNOWN[info_hash]
            continue
        asking.append((info_hash, source))

    if not asking:
        return sources

    answers = http.run_parallel(
        [(info_hash, (lambda s=source: _ask(s))) for info_hash, source in asking],
        workers=4, deadline=DEADLINE)
    for info_hash, source in asking:
        count = answers.get(info_hash) or 0
        _KNOWN[info_hash] = count
        source["bundled_subs"] = count

    marked = sum(1 for _h, source in asking if source.get("bundled_subs"))
    if marked:
        kodi.log("%d of %d releases carry their own subtitles"
                 % (marked, len(asking)))
    return sources


def forget():
    """Drop what is known, for a test or a changed account."""
    _KNOWN.clear()

# -*- coding: utf-8 -*-
"""Which releases ship subtitle files, for the picker to say so.

Beside the video, not inside it - a `Subs/` folder next to the `.mp4`, which
is what RARBG and most scene releases do. The tracks muxed *into* the
container are a different question and are not answered here; see the note
about cost below.

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

# Six, not four. Eight rows through four workers is two rounds, and a TorBox
# lookup is half a second when the torrent is already there and longer when
# it is not - so four dropped the second half of the list. Measured on Top
# Gun: Maverick, whose two RARBG releases both ship English subtitles and sit
# at rows seven and eight: at four seconds one of them was marked and the
# other was not, which reads as arbitrary rather than as "not known yet".
#
# A row that still does not answer is drawn without the mark, exactly as the
# whole list looked before this existed, and the answer is remembered - so a
# redraw, which "show all" is, fills in what the first pass missed.
DEADLINE = 6.0


def _languages(files):
    """Which languages the torrent ships, read off the filenames.

    The count on its own decided nothing. RARBG ships `Subs/4_English.srt`
    and `Subs/5_English.srt`, so "2 subtitle files" and "English" are the
    same fact, and only the second one tells a Hebrew household whether the
    row is worth taking.
    """
    from ..subs.providers import sidecar

    found = []
    for entry in files or []:
        name = str(entry.get("name") or entry.get("short_name") or "")
        if not name.lower().endswith(SUBTITLE_EXTENSIONS):
            continue
        # No default here: an unlabelled file beside a release is probably
        # English, but "probably" is not something to print next to a
        # language the viewer is deciding on.
        code = sidecar.language_of(name, default="")
        if code and code not in found:
            found.append(code)
    return found


def _ask(source):
    from ..debrid import registry

    client = registry.client_with_sidecars(source)
    if client is None:
        return []
    try:
        files, _link_for = client.sidecar_subtitles(source)
    except Exception:
        kodi.log_exception("could not look inside %s" % source.get("hash", "")[:12])
        return []
    return _languages(files)


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
        languages = answers.get(info_hash) or []
        _KNOWN[info_hash] = languages
        source["bundled_subs"] = languages

    marked = sum(1 for _h, source in asking if source.get("bundled_subs"))
    if marked:
        kodi.log("%d of %d releases carry their own subtitles"
                 % (marked, len(asking)))
    return sources


def forget():
    """Drop what is known, for a test or a changed account."""
    _KNOWN.clear()

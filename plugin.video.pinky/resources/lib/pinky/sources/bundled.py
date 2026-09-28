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
# change. Written down, not just held in memory: the background pass runs
# inside a plugin invocation, and Kodi tears that down the moment the picker
# closes - so what it learned died with it and the mark never appeared at
# all. A month is safe because the contents of a torrent are fixed.
_TTL = 30 * 24 * 3600


def _key(info_hash):
    from .. import cache
    return cache.make_key("sources", "bundled", info_hash)


def _remember(info_hash, languages):
    from .. import cache
    cache.set(_key(info_hash), languages, _TTL)


def _recall(info_hash):
    from .. import cache
    return cache.get(_key(info_hash))

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
UNLABELLED = "und"

# How many rows are worth asking about. The picker draws six by default and
# thirteen with everything shown; past that the viewer is scrolling rather
# than choosing, and every one is a request against somebody's account.
MAX_ROWS = 8

# Generous, because nobody is waiting on it any more - this runs on a
# background thread and the draw never blocks. Still bounded, because the
# work goes through the shared four-worker pool that everything else uses.
DEADLINE = 8.0


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
        # "und" for a file that names no language. The mark never prints it
        # - it shows only languages the viewer reads - but the anime engine
        # counts it as English, which is what an unlabelled subtitle beside
        # a fansub release is.
        code = sidecar.language_of(name, default="") or UNLABELLED
        if code not in found:
            found.append(code)
    return found


def recall(source):
    """What a cached source is already known to ship, without asking.

    A cache read and nothing else, so the picker can count it in a row's fit
    while it draws. None when it has not been asked yet.
    """
    info_hash = (source.get("hash") or "").lower()
    if not info_hash or not source.get("cached"):
        return None
    return _recall(info_hash)


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
    """Mark what is already known, and go and find out the rest.

    **Nothing waits for this.** It decorates a list, and a decoration may not
    cost the list: measured on Top Gun: Maverick, blocking for it spent the
    whole ceiling - six seconds, then three after the account listing was
    removed - on every single picker open, against a search that already
    takes eleven. Paying three seconds of somebody's evening for a mark on
    one row is not a trade worth making.

    So the draw uses what is remembered, which is instant, and a background
    thread fills in the rest for the next one. "Show all" is a redraw, and so
    is opening the picker again, which on a series happens every episode -
    so the marks appear, just not necessarily the first time.

    The thread is a daemon and its work is bounded by the shared pool. If the
    window closes and the plugin invocation is torn down first, nothing is
    lost that mattered: the list drew correctly without it.
    """
    asking = []
    for source in (sources or [])[:MAX_ROWS]:
        info_hash = (source.get("hash") or "").lower()
        if not info_hash or not source.get("cached"):
            continue
        known = _recall(info_hash)
        if known is not None:
            source["bundled_subs"] = known
            continue
        asking.append((info_hash, source))

    if asking:
        _learn_later([(info_hash, dict(source)) for info_hash, source in asking])
    return sources


def _learn_later(asking):
    """Ask about these in the background, for the next time the list is drawn."""
    import threading

    def work():
        try:
            answers = http.run_parallel(
                [(info_hash, (lambda s=source: _ask(s)))
                 for info_hash, source in asking],
                workers=4, deadline=DEADLINE)
        except Exception:
            kodi.log_exception("could not tell which releases carry subtitles")
            return
        marked = 0
        for info_hash, _source in asking:
            languages = answers.get(info_hash) or []
            _remember(info_hash, languages)
            marked += 1 if languages else 0
        if marked:
            kodi.log("%d of %d releases carry their own subtitles"
                     % (marked, len(asking)))

    thread = threading.Thread(target=work, name="pinky-embedded")
    thread.daemon = True
    thread.start()


def forget():
    """Drop what is known, for a test or a changed account."""
    from .. import cache
    cache.clear()

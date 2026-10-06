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

Reading the *embedded* tracks is the other half, and it is done here too -
in the background, never on the draw. It needs a real download link and one
ranged read of the start of the file, because Matroska declares its tracks
before the first cluster: about two seconds to resolve and two to read, so
blocking the picker on it for six rows was eleven seconds and is not done.
Remembered per file for a year, because a file's tracks never change. It is
the anime lever: modern fansubs mux their English subtitle into the MKV and
ship nothing beside it, and measured on Frieren 1x20 the English list went
from 95/91/91/80/80 to 100 on every row once the pass had looked.

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
DEADLINE = 20.0
# Half the shared pool at most. The pool is the one the source search, the
# subtitle search and playback's own resolve all use, and a background
# decoration holding every worker would make the next press wait on it.
BACKGROUND_WORKERS = 2
# Reading inside a file costs a resolve and a ranged read, about four
# seconds each, so fewer rows than the file list is asked for.
INSIDE_ROWS = 6


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


def annotate(sources, on_learnt=None):
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

    `on_learnt` is called, from that thread, once it has learnt that at least
    one release carries a subtitle - which is when a picker already on the
    screen is worth drawing again.
    """
    asking = []
    for position, source in enumerate((sources or [])[:MAX_ROWS]):
        info_hash = (source.get("hash") or "").lower()
        if not info_hash or not source.get("cached"):
            continue
        known = _recall(info_hash)
        if known is not None:
            source["bundled_subs"] = known
        else:
            asking.append((_key(info_hash), _remember_for(info_hash),
                           (lambda s=dict(source): _ask(s))))
        inside = recall_inside(source)
        if inside is not None:
            source["inside_subs"] = inside
        # A file whose tracks were learnt before readability was asked keeps
        # them for a year, so it is looked at once more rather than drawn as
        # "English inside" and translated from somebody's Arabic at 70%:
        # Naruto Shippuden 3x55's BDRip, in a real Kodi.
        if (inside is None or (inside and not _readable_known(source))) \
                and position < INSIDE_ROWS and _may_look_inside(source):
            asking.append((_inside_key(source), _remember_inside_for(source),
                           (lambda s=dict(source): _look_inside(s))))

    if asking:
        _learn_later(asking, on_learnt)
    return sources


def _remember_for(info_hash):
    return lambda languages: _remember(info_hash, languages or [])


# --------------------------------------------------------------------------
# the subtitle tracks inside the file
# --------------------------------------------------------------------------

# Matroska declares its tracks before the first cluster, so this much of the
# start of the file answers it. Measured on twelve cached anime releases:
# resolving the link about 2 s, reading this about 2 s, and every one of the
# twelve had a full English track inside.
INSIDE_READ = 512 * 1024
_INSIDE_TTL = 365 * 24 * 3600
# What could not be read - not an MKV, a dead link - is not asked again for a
# day, rather than on every open of the picker.
_UNKNOWN_TTL = 24 * 3600
_UNKNOWN = "?"


def _inside_key(source):
    """Per file, not per torrent: a season pack holds every episode."""
    from .. import cache
    meta = (source.get("extra") or {}).get("meta") or {}
    part = ("%sx%s" % (meta.get("season") or 0, meta.get("episode") or 0)
            if meta.get("type") == "episode" else "film")
    return cache.make_key("sources", "inside",
                          (source.get("hash") or "").lower(), part)


def recall_inside(source):
    """The languages of the full subtitle tracks inside this source's file.

    A cache read only. None until the background pass has looked; [] for a
    file known to have none, or that could not be read.
    """
    from .. import cache
    if not source.get("cached") or not source.get("hash"):
        return None
    known = cache.get(_inside_key(source))
    if known is None:
        return None
    return [] if known == [_UNKNOWN] else known


def _may_look_inside(source):
    name = (source.get("file_name") or source.get("title") or "").lower()
    return not name.endswith((".mp4", ".avi", ".m4v", ".ts", ".wmv"))


def _remember_inside_for(source):
    from .. import cache

    def remember(languages):
        if languages == [_UNKNOWN]:
            cache.set(_inside_key(source), [_UNKNOWN], _UNKNOWN_TTL)
        else:
            cache.set(_inside_key(source), languages, _INSIDE_TTL)
    return remember


def _look_inside(source):
    """Full subtitle tracks muxed into the file, by language.

    [_UNKNOWN] when the file could not be read, because `run_parallel` drops
    a task that answers None and it would be asked again on every open.

    Signs-and-songs and forced tracks are left out: they caption on-screen
    text, not dialogue. Resolved through the same service playback would
    use, on a copy, so nothing about it looks like playing the file.
    """
    from ..debrid import registry
    from ..subs import hasher
    from ..utils import matroska
    resolver = registry.resolver_for(source)
    link = resolver.resolve(dict(source)) if resolver is not None else ""
    data = hasher.read_range(link, 0, INSIDE_READ - 1) if link else None
    tracks = matroska.subtitle_tracks(data) if data else None
    if tracks is None:
        return [_UNKNOWN]
    _remember_readable(source, link)
    languages = []
    for track in tracks:
        if not track["partial"] and track["language"] not in languages:
            languages.append(track["language"])
    return languages


def _remember_readable(source, link):
    """Which of those tracks can be read out of the file, to translate from.

    A track inside the file is in time by construction, and for most anime
    it is the only English there is - but a translation needs the lines, and
    only a track the file indexes line by line gives them up without the
    whole video being downloaded. One more small read, in the same
    background pass, so the picker can promise it before it is pressed.
    """
    from .. import cache
    try:
        from ..subs import inside
        readable = inside.readable_languages(link)
    except Exception:
        kodi.log_exception("could not tell whether the tracks inside can be read")
        return
    if readable is None:
        # A read that failed is asked again on a later open, not filed.
        return
    cache.set(_readable_key(source), readable, _INSIDE_TTL)


def recall_readable(source):
    """Languages of the tracks inside this file that can be translated from.
    A cache read only; [] until the background pass has looked."""
    from .. import cache
    if not source.get("cached") or not source.get("hash"):
        return []
    return cache.get(_readable_key(source)) or []


def _readable_key(source):
    # Not "|text", which is what held the readings a refused request filed as
    # "nothing readable" for a year; renamed so every one of them is asked again.
    return _inside_key(source) + "|readable"


def _readable_known(source):
    from .. import cache
    return cache.get(_readable_key(source)) is not None


def _learn_later(asking, on_learnt=None):
    """Ask about these in the background, for the next time the list is drawn."""
    import threading

    def work():
        try:
            answers = http.run_parallel(
                [(key, ask) for key, _remember_it, ask in asking],
                workers=BACKGROUND_WORKERS, deadline=DEADLINE)
        except Exception:
            kodi.log_exception("could not tell which releases carry subtitles")
            return
        marked = 0
        for key, remember_it, _ask_it in asking:
            if key not in answers:
                continue            # past the deadline: ask again next time
            languages = answers.get(key)
            remember_it(languages)
            marked += 1 if languages and languages != [_UNKNOWN] else 0
        if marked:
            kodi.log("%d of %d releases carry their own subtitles"
                     % (marked, len(asking)))
            if on_learnt is not None:
                try:
                    on_learnt()
                except Exception:
                    kodi.log_exception("could not redraw with what was learnt")

    thread = threading.Thread(target=work, name="pinky-embedded")
    thread.daemon = True
    thread.start()


def forget():
    """Drop what is known, for a test or a changed account."""
    from .. import cache
    cache.clear()

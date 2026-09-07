"""From "the user pressed OK" to "Kodi has a URL".

The flow is deliberately short:

    resolve metadata -> find sources -> pick one -> resolve to a stream URL

Everything expensive happens behind a progress dialog the user can cancel, and
the source search is bounded by the shared worker pool and its deadline.
"""
from . import kodi, settings
from .ui import listing


def build_meta(request):
    """Turn route parameters into the metadata every later stage needs."""
    from .meta import tmdb

    item_type = request.get("type") or "movie"
    tmdb_id = request.get("tmdb")
    meta = {
        "type": item_type,
        "ids": {},
        "title": "",
        "year": 0,
        "season": int(request.get("season") or 0),
        "episode": int(request.get("episode") or 0),
    }

    if item_type == "movie":
        detail = tmdb.movie(tmdb_id) if tmdb_id else None
        if detail:
            meta["ids"] = detail["ids"]
            meta["title"] = detail["title"]
            meta["original_title"] = detail.get("original_title", "")
            meta["year"] = detail["year"]
            meta["art"] = detail.get("art", {})
            meta["item"] = detail
    else:
        show = tmdb.show(tmdb_id) if tmdb_id else None
        if show:
            meta["ids"] = show["ids"]
            meta["title"] = show["title"]
            meta["original_title"] = show.get("original_title", "")
            meta["year"] = show["year"]
            meta["show_title"] = show["title"]
            meta["art"] = show.get("art", {})
            episodes = tmdb.episodes(tmdb_id, meta["season"]) or []
            for episode in episodes:
                if episode.get("episode") == meta["episode"]:
                    meta["episode_title"] = episode.get("title", "")
                    meta["item"] = episode
                    meta["art"] = episode.get("art", meta["art"])
                    break

    if request.get("imdb") and not meta["ids"].get("imdb"):
        meta["ids"]["imdb"] = request["imdb"]
    return meta


def play(handle, request, force_picker=False):
    """Route entry point for playing a movie or an episode."""
    meta = build_meta(request)
    if not meta.get("title"):
        # The one failure with no explanation anywhere. An episode reached
        # this line and left "action episode took 1401 ms" in the log and
        # nothing else - no source search, no attempt, no reason. TMDB is the
        # only thing that supplies a title, so this is a TMDB id it does not
        # know, or a TMDB that did not answer, and either is worth saying.
        kodi.log("no title for %s tmdb=%s, so there is nothing to search for"
                 % (request.get("type"), request.get("tmdb")), kodi.LOG_INFO)
        kodi.notify(kodi.localize(32280))
        listing.resolve_failed(handle)
        return

    try:
        from .sources import aggregator
    except ImportError:
        kodi.ok_dialog(kodi.localize(32281))
        listing.resolve_failed(handle)
        return

    if not settings.configured_debrid():
        kodi.log("no debrid service is configured, so nothing can be played",
                 kodi.LOG_INFO)
        kodi.ok_dialog(kodi.localize(32282))
        listing.resolve_failed(handle)
        return

    sources = aggregator.find(meta)
    if not sources:
        # Worth a line even though there is a notification, because the
        # notification says the same thing whether the search just ran and
        # found nothing or a remembered empty answer was handed back. Those
        # want different responses - wait, or clear the cache - and only the
        # log can tell them apart, because a cached answer logs no search.
        kodi.log("no sources for %s (%s)"
                 % (meta.get("title", ""), _describe(meta)), kodi.LOG_INFO)
        kodi.notify(kodi.localize(32283))
        listing.resolve_failed(handle)
        return

    chosen = _choose(sources, meta, force_picker)
    if not chosen:
        # Either the viewer closed the picker, or - the case that used to be
        # invisible - autoplay was handed a list it could make nothing of. An
        # episode once reached this line with eighty-one sources behind it and
        # left no trace at all: no attempt, no message, nothing in the log,
        # and a remote that appeared not to have been pressed.
        kodi.log("nothing chosen from %d sources for %s"
                 % (len(sources), meta.get("title", "")), kodi.LOG_INFO)
        listing.resolve_failed(handle)
        return

    chosen, url = _resolve_any(chosen, sources, force_picker)
    if not url:
        # The last exit with nothing behind it. Between them, the five exits
        # in this function had four different silences, and an episode that
        # would not play left one line in the log saying how long it had
        # taken to not play. Every one of them says why now.
        kodi.log("could not open any of %d sources for %s %s"
                 % (len(sources), meta.get("title", ""), _describe(meta)),
                 kodi.LOG_INFO)
        kodi.notify(kodi.localize(32284))
        listing.resolve_failed(handle)
        return

    # Where the viewer got to last time, which lives in the Trakt mirror and
    # has to be put onto the item before Kodi is handed it. The rows already
    # carry it - that is what draws the progress bar under a poster - but the
    # item playback builds comes from TMDB and knows nothing about it, so
    # resuming worked from a plain listing and not from the Katan window.
    try:
        from .meta import trakt_state
        item = meta.get("item")
        if item:
            trakt_state.annotate([item])
    except Exception:
        kodi.log_exception("could not read the resume point")

    from . import player
    meta["source"] = {
        "group": chosen.get("group", ""),
        "provider": chosen.get("provider", ""),
        "quality": chosen.get("quality", ""),
        "release": chosen.get("title", ""),
        # The file the debrid service actually opened, which the client
        # records while picking it out of the torrent. For a season pack
        # these are different things and the difference is the whole ball
        # game: the torrent is "Silo.S01.COMPLETE.1080p.WEB-DL-GRP" and the
        # file is "Silo.S01E01.Freedom.Day.1080p.WEB-DL-GRP", and it is the
        # second one that subtitle sites index against.
        "file_name": chosen.get("file_name", ""),
    }
    meta["stream_url"] = url
    player.set_now_playing(meta)
    listing.resolve(handle, url, meta.get("item"))


# How many sources autoplay will try before giving up.
#
# Two numbers, because the cost of an attempt is not the same in both modes.
# With "cached only" on - the default, and what the low-memory profile
# enforces - no attempt can start a download, so the only cost is a round trip
# and the sixty-an-hour uncached quota is never touched. Being stingy there
# buys nothing and loses playbacks: The Matrix was refused by three sources in
# a row, each honestly ("still downloading", "no seeds", "no usable video
# file"), and giving up at that point left three more in the list untried.
#
# With uncached downloads allowed, an attempt can spend one of those sixty, so
# the small number stands.
RESOLVE_ATTEMPTS = 3
RESOLVE_ATTEMPTS_CACHED = 6


def _describe(meta):
    """"Silo S01E01" or "a film", for a log line."""
    if meta.get("type") == "episode":
        return "S%02dE%02d" % (int(meta.get("season") or 0),
                               int(meta.get("episode") or 0))
    return str(meta.get("year") or "")


def _resolve_any(chosen, sources, force_picker):
    """Resolve the chosen source, falling through to the next ones.

    A source can be flagged cached by the indexer and turn out not to be on
    the debrid service at all - TorBox will accept the magnet and report it as
    downloading, with no files. Giving up there told the viewer "could not
    play" while the second source in the list would have played immediately,
    which is what happened to The Dark Knight tonight.

    Only when the add-on picked the source itself. If the viewer chose one
    from the picker, that is the one they asked for, and quietly playing a
    different release would be worse than saying so.
    """
    url = _resolve(chosen)
    if force_picker:
        return chosen, url
    if url and _reachable(url):
        return chosen, url

    limit = RESOLVE_ATTEMPTS if _uncached_allowed() else RESOLVE_ATTEMPTS_CACHED
    tried = {id(chosen)}
    for candidate in sources:
        if len(tried) >= limit:
            kodi.log("gave up after %d sources, none of them playable" % limit)
            break
        if id(candidate) in tried:
            continue
        tried.add(id(candidate))
        kodi.log("falling through to the next source: %s"
                 % (candidate.get("title", "")[:70]))
        url = _resolve(candidate)
        if url and _reachable(url):
            return candidate, url
    return chosen, ""


# How long to wait for the first byte of a stream before deciding the link is
# not going to open. Generous, because this is a wireless projector and a slow
# answer is still an answer; short enough that a dead link does not look like
# a frozen add-on.
REACHABLE_TIMEOUT = 8

# How long a CDN host that would not answer is left alone. Short, because a
# node coming back is normal and being wrong here costs a playback; long
# enough to cover one viewer working through one film's sources.
DEAD_HOST_TTL = 300


def _dead_host_key(url):
    """The cache key for "this CDN node is not answering".

    Whole hosts fail rather than individual links: one evening's log had
    three sources for the same episode resolve to store-028, store-045 and
    store-028 again, and every one of them timed out at eight seconds. That
    is twenty-four seconds of waiting, sixteen of it spent finding out the
    same thing twice. A debrid service hands out links round-robin across its
    nodes, so remembering the node rather than the link is what makes the
    difference.
    """
    try:
        from urllib.parse import urlparse
    except ImportError:
        from urlparse import urlparse       # Python 2, which Kodi 21 is not
    host = urlparse(url).netloc
    return ("debrid|deadhost|%s" % host) if host else ""


def _reachable(url):
    """Does this URL actually give us a byte?

    A debrid service can hand back a link its own CDN will not serve. That is
    not theoretical: TorBox returned perfectly good links to
    store-028.wnam.tb-cdn.io and store-033.wnam.tb-cdn.io on the same evening,
    both of which accepted a TCP connection on 443 and then never answered -
    twenty-five seconds to nothing, from Kodi and from a browser alike. Kodi's
    only symptom was a black screen: the plugin had done its job, handed over
    a URL, and there was nothing in any log to say the link was dead.

    So the link is opened for one byte before it is handed over, and a link
    that will not give up a byte is treated like any other source that will
    not play - the next one is tried. One ranged request costs a few hundred
    milliseconds against a playback that would otherwise not have happened.

    Only a connection failure counts against it. An HTTP status does not: some
    CDNs answer a range request with 403 and the full file with 200, and
    refusing those would be worse than the problem being solved.
    """
    from . import cache, http

    key = _dead_host_key(url)
    if key and cache.get(key):
        kodi.log("skipping %s, it was not answering a moment ago"
                 % url.split("/")[2])
        return False

    response = http.get(url, headers={"Range": "bytes=0-0"},
                        timeout=REACHABLE_TIMEOUT, retries=0, stream=True)
    if response is None:
        kodi.log("the link came back but will not open: %s" % url[:80],
                 kodi.LOG_INFO)
        if key:
            cache.set(key, True, DEAD_HOST_TTL)
        return False
    try:
        response.close()
    except Exception:
        pass
    if key:
        # A node that answers clears its own black mark, so one slow moment
        # does not keep a working host out for five minutes.
        cache.delete(key)
    return True


def _choose(sources, meta, force_picker):
    """Autoplay the best source, or open the picker."""
    autoplay = settings.get_bool("sources.autoplay") and not force_picker
    if autoplay:
        return sources[0]
    try:
        from .ui.sources_window import pick_source
        return pick_source(sources, meta)
    except ImportError:
        labels = ["%s | %s | %s" % (s.get("quality", ""), _size(s), s.get("provider", ""))
                  for s in sources]
        index = kodi.select(labels, kodi.localize(32285))
        return sources[index] if index >= 0 else None


def _size(source):
    size = source.get("size") or 0
    return "%.2f GB" % (size / 1024.0 ** 3) if size else "?"


def _resolve(source):
    """Ask the debrid service that has this source cached for a stream URL.

    The choice of service is registry.resolver_for's job, not this function's.
    It used to be duplicated here and the copy had drifted: it never checked
    that the named service was still configured, so a source cached by an
    account the user had since removed was handed to a client with no key.

    This is also the one moment `allow_uncached` means anything, which is why
    it is decided here rather than carried along from the search: the setting
    can change between the two, and a stale answer here is the difference
    between a download starting and nothing happening at all.
    """
    from .debrid import registry

    source.setdefault("extra", {})
    source["extra"]["allow_uncached"] = _uncached_allowed()

    client = registry.resolver_for(source)
    if client is None:
        # Silence here reads as "the button did nothing". It happens for a
        # real reason - the source is cached on a service that is no longer
        # configured, or on none at all - and the reason is worth one line.
        kodi.log("no configured debrid service can open %s (cached by %s)"
                 % (source.get("title", "")[:60],
                    source.get("cached_by") or "nobody"))
        return ""
    try:
        return client.resolve(source) or ""
    except Exception:
        kodi.log_exception("resolving through %s failed" % client.name)
        return ""


def _uncached_allowed():
    """May a debrid service start a download for this playback?

    Three clients read this flag and nothing ever set it, so it was always
    false - which made "cached only: off" a trap. The picker would list
    sources the client then refused to open, and pressing play did nothing at
    all. Turning that setting off is the viewer saying they will wait for a
    download, so this is what it now means.

    Left on, which is the default and what the low-memory profile enforces,
    nothing changes: no download is ever started, and TorBox's sixty-an-hour
    uncached quota is not touched.
    """
    return not settings.get_bool("sources.cached_only")


def prefetch_next_episode(meta):
    """Warm the source list for the next episode, without resolving anything."""
    if meta.get("type") != "episode":
        return
    nxt = dict(meta)
    nxt["episode"] = int(meta.get("episode") or 0) + 1
    try:
        from .sources import aggregator
        aggregator.find(nxt, prefetch=True)
    except ImportError:
        return
    except Exception:
        kodi.log_exception("prefetch failed")

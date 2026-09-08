"""Finding sources: run the providers, learn what is cached, rank, return few.

The shape of this module is the answer to "why did the other add-on crash my
box". It does four things in a fixed order and refuses to do more:

1. Run only the providers that are enabled and relevant, on the shared worker
   pool, under one wall-clock deadline. A slow provider is abandoned, not
   waited for.
2. Merge duplicates by infohash, so three providers reporting the same torrent
   cost one cache check rather than three.
3. Ask each debrid service once, in batches, which hashes it can play now.
4. Filter and rank, then hand back a short list.

Results are cached, so pressing back and playing again is instant, and so the
next-episode prefetch costs nothing when the user gets there.
"""
import time

from .. import cache, http, kodi, settings
from . import model, scoring

TTL_RESULTS = 20 * 60          # a source list stays useful for a short while
TTL_EMPTY = 5 * 60             # remember failures briefly, but not for long

# Providers that only make sense for anime, and are skipped otherwise.
ANIME_PROVIDERS = ("nyaa", "animetosho")


def _provider_modules():
    """Import providers lazily, so an unused one costs nothing."""
    from .providers import (animetosho, comet, external, mediafusion, nyaa,
                            torrentio, torrentsdb, zilean)
    return {
        "torrentio": torrentio,
        "torrentsdb": torrentsdb,
        "comet": comet,
        "mediafusion": mediafusion,
        "zilean": zilean,
        "nyaa": nyaa,
        "animetosho": animetosho,
        "external": external,
    }


def _is_anime(meta):
    extra = meta.get("extra") or {}
    if extra.get("anime"):
        return True
    ids = meta.get("ids") or {}
    if ids.get("anilist") or ids.get("kitsu"):
        return True
    item = meta.get("item") or {}
    return bool((item.get("extra") or {}).get("anime"))


def _enabled_providers(meta):
    modules = _provider_modules()
    anime = _is_anime(meta)
    chosen = []
    for name in settings.enabled_source_providers():
        module = modules.get(name)
        if module is None:
            continue
        if name in ANIME_PROVIDERS and not anime:
            continue
        chosen.append((name, module))
    return chosen


def cache_key(meta):
    ids = meta.get("ids") or {}
    return cache.make_key("sources", meta.get("type"),
                          ids.get("imdb") or ids.get("tmdb") or meta.get("title"),
                          meta.get("season"), meta.get("episode"))


def find(meta, prefetch=False, force=False):
    """Return a ranked, short list of sources for a movie or episode."""
    return _top(_ranked(meta, prefetch=prefetch, force=force))


def _ranked(meta, prefetch=False, force=False):
    """Everything that survived the filters, best first.

    What is cached is the whole ranked list, not the handful the picker
    shows. Caching only the short list made "show all" a lie: it read back
    the same cache entry, so the toggle re-ranked eight sources and returned
    the same eight. Ranking a cached list again is a sort of a few hundred
    dictionaries, which costs nothing next to the search it replaces.
    """
    key = cache_key(meta)
    if not force:
        hit = cache.get(key)
        if hit is not None:
            return hit if prefetch else _recheck_cached(hit, meta)

    providers = _enabled_providers(meta)
    if not providers:
        kodi.log("no source providers are enabled")
        return []

    raw = _run_providers(providers, meta, quiet=prefetch)

    # An anime episode is asked for twice, under both of the addresses the
    # world files it under. Not as a fallback: making it conditional on the
    # first search finding nothing meant one bad name-match suppressed it
    # entirely, and that is not hypothetical - Bleach 2x47 came back with a
    # single wrongly matched result from a name index, which was enough to
    # stop the address that had the episode from ever being tried.
    also = _anime_address(meta, found_already=bool(raw))
    if also:
        # Only the providers that ask by id. The two that ask by name have
        # already been given their best question - the show's name and the
        # absolute number - and asking them again with a cour-relative number
        # is asking something they cannot answer correctly.
        by_id = [(n, m) for n, m in providers
                 if not getattr(m, "BY_NAME", False)]
        raw = list(raw) + _run_providers(by_id, also, quiet=prefetch)

    if not raw:
        cache.set(key, [], TTL_EMPTY)
        return []

    merged = model.dedupe(raw)
    _apply_meta(merged, meta)
    _check_debrid_cache(merged)
    _apply_subtitles(merged, meta)

    kept, rejected = scoring.rank_all(merged, meta, _runtime_hours(meta))
    # Both numbers, because they are different things and the log is read by
    # somebody wondering why the picker shows six rows. "66 after ranking"
    # next to a six-row picker reads as a contradiction; "66 kept, showing 6"
    # is the actual arrangement.
    kodi.log("sources: %d found, %d kept, showing %d%s"
             % (len(merged), len(kept), len(_top(kept)),
                _rejection_summary(rejected)))
    _remember_filtering(meta, len(merged), rejected)

    # Everything that was found, before the filters had their say. It is kept
    # so that "nothing survived" can be answered with something better than
    # "nothing found" - see `uncached`, below - without searching again.
    cache.set(unfiltered_key(meta), merged, TTL_RESULTS if merged else TTL_EMPTY)
    cache.set(key, kept, TTL_RESULTS if kept else TTL_EMPTY)
    return kept


def unfiltered_key(meta):
    return cache_key(meta) + "|raw"


def uncached(meta):
    """What there would be if "cached only" were off, ranked as usual.

    For a film that has been out a while this is empty or close to it -
    everything worth having is in somebody's debrid cache. For an episode
    that aired last week it is the whole answer: forty-four copies of a
    Bleach episode existed, every one of them found, and not one of them was
    on the account yet. Saying "no sources" to that is untrue and unhelpful.
    """
    found = cache.get(unfiltered_key(meta))
    if not found:
        return []
    prefs = scoring.Preferences()
    prefs.cached_only = False
    kept = []
    for source in found:
        if scoring.rejection_reason(source, prefs, _runtime_hours(meta)):
            continue
        source["score"] = scoring.score(source, prefs, _runtime_hours(meta))
        kept.append(source)
    kept.sort(key=lambda s: scoring.sort_key(s, prefs))
    return kept


def filter_key(meta):
    return cache_key(meta) + "|why"


def _remember_filtering(meta, found, rejected):
    """Keep why sources were dropped, for the picker to show.

    Until now this only went to the log, and only when the search actually
    ran. So a viewer looking at eight results for a film with sixty-four
    releases had no way at all to know that forty of them were camera
    recordings - the picker simply looked broken, and was reported as such.
    """
    cache.set(filter_key(meta),
              {"found": found,
               "reasons": sorted((rejected or {}).items(),
                                 key=lambda kv: -kv[1])},
              TTL_RESULTS)


def filter_report(meta):
    """How many were found and why most of them are not on the screen.

    Returns None when nothing is remembered, which is not the same as
    "nothing was dropped" and must not be shown as though it were.
    """
    return cache.get(filter_key(meta))


def _top(sources):
    """The handful the picker shows, which is the point of ranking at all."""
    limit = settings.get_int("sources.results")
    return sources[:limit] if limit else sources


def _run_providers(providers, meta, quiet=False):
    """Fan out under the shared cap, showing progress unless prefetching."""
    workers = max(1, settings.get_int("sources.workers"))
    deadline = max(4, settings.get_int("sources.timeout"))

    progress = None
    if not quiet:
        import xbmcgui
        progress = xbmcgui.DialogProgressBG()
        progress.create("Katan", kodi.localize(32331))

    found = []
    started = time.time()

    def on_result(name, sources):
        found.extend(sources)
        if progress is not None:
            elapsed = time.time() - started
            progress.update(int(min(99, (elapsed / deadline) * 100)),
                            message=kodi.localize(32332, len(found)))

    try:
        http.run_parallel(
            [(name, _guarded(module, meta)) for name, module in providers],
            workers=workers, deadline=deadline, on_result=on_result)
    finally:
        if progress is not None:
            progress.close()
    return found


def _guarded(module, meta):
    def call():
        return module.search(meta) or []
    return call


def _apply_subtitles(sources, meta):
    """Note what each source's Hebrew subtitles are likely to be.

    Ranking needs this, not only the picker: a release with a subtitle
    written for it is a better answer than a slightly larger one without,
    and autoplay should be making that choice too rather than leaving it to
    whoever happens to open the picker.

    The lookup only needs the title, so its one network call has already been
    warmed by the time the providers finish - and it is cached per title, so
    the second source search for the same film pays nothing at all. A failure
    leaves the sources unannotated, which ranks them as before.
    """
    try:
        from ..subs import outlook
        outlook.annotate(meta, sources)
    except Exception:
        kodi.log_exception("could not work out the subtitle outlook")


def _apply_meta(sources, meta):
    """Attach the playback context each debrid client needs for file picking."""
    context = {
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        # A fansub batch names its files absolutely, so picking the right one
        # out of a pack needs the absolute number as well as the pair.
        "absolute": meta.get("absolute"),
        "title": meta.get("title"),
    }
    for source in sources:
        source.setdefault("extra", {})
        source["extra"]["meta"] = context


def _check_debrid_cache(sources, recheck=False):
    """One batched question per service, never one request per source.

    Returns False when no service could answer, so the caller can tell "no
    service holds this" apart from "nobody was asked". Normally only the
    sources not already flagged are asked about; `recheck` asks about all of
    them and clears a flag that is no longer true.
    """
    from ..debrid import registry

    ask = [s["hash"] for s in sources
           if s.get("hash") and (recheck or not s.get("cached"))]
    if not ask:
        return True
    try:
        answers = registry.cached_map(ask)
    except Exception:
        kodi.log_exception("debrid cache lookup failed")
        return False
    for source in sources:
        service = answers.get(source.get("hash"))
        if service:
            source["cached"] = True
            source["cached_by"] = service
        elif recheck and source.get("hash"):
            source.pop("cached", None)
            source.pop("cached_by", None)
    return True


def _recheck_cached(sources, meta):
    """Re-confirm cache flags on a cached result list, and re-rank if they moved.

    The list itself stays valid for a while, but whether a service still holds
    a torrent can change, and playing a source that is no longer cached is the
    most annoying possible failure.

    This used to ask only about the sources already marked *un*cached, so a
    flag could go up and never come down - which is precisely the failure the
    paragraph above says it prevents. It now asks about all of them.

    Re-ranking afterwards is not decoration. Being cached is worth more than
    every other signal put together, and with `cached_only` on - the default -
    it decides whether a source is shown at all, so a flag that changed and an
    order that did not would put a source that can no longer play at the top.
    """
    if not sources:
        return sources
    before = [bool(s.get("cached")) for s in sources]
    if not _check_debrid_cache(sources, recheck=True):
        return sources
    if [bool(s.get("cached")) for s in sources] == before:
        return sources
    # Deliberately not written back to the cache. Re-ranking drops whatever
    # is no longer playable, and with `cached_only` on that is most of the
    # list; storing the shorter version would mean a source that became
    # cached again could not come back until the whole entry expired. The
    # stored list stays the full one and this correction is redone each time,
    # which the debrid client's own memo makes cheap.
    kept, _ = scoring.rank_all(sources, meta, _runtime_hours(meta))
    return kept


def _runtime_hours(meta):
    """Used for size sanity checks, so an episode is not judged like a film."""
    item = meta.get("item") or {}
    duration = item.get("duration") or 0
    if duration:
        return max(0.25, duration / 3600.0)
    return 0.75 if meta.get("type") == "episode" else 2.0


def _rejection_summary(rejected):
    if not rejected:
        return ""
    parts = ["%d %s" % (count, reason)
             for reason, count in sorted(rejected.items(),
                                         key=lambda kv: -kv[1])[:3]]
    return " (dropped: %s)" % ", ".join(parts)


def all_sources(meta):
    """Everything that passed the filters, for the "show all" action.

    Deliberately without the debrid re-check. This is called the moment the
    picker opens, which is seconds after the search that set those flags, and
    re-asking the service about every source is not free: on a film with a
    hundred sources it held the picker on a spinner for twenty-six seconds
    while it confirmed what it had just been told.

    The re-check exists so a flag can come *down* between a cached search
    result and playback, and it still runs where that matters - on the way
    into `find`, which is what playback uses. Being a few seconds stale in a
    list somebody is reading is not the same risk as being stale in the
    source about to be opened.
    """
    return _ranked(meta, prefetch=True)


def invalidate(meta=None):
    if meta:
        cache.delete(cache_key(meta))
    else:
        cache.delete_prefix("sources|")


def _anime_address(meta, found_already=False):
    """The same episode, addressed the way an anime index files it.

    Every provider keyed on an IMDb id asks for `imdb:season:episode`, and for
    anime that address is very often empty even when the episode is widely
    available - TMDB counts a whole multi-year arc as one season, and the
    trackers count each cour from one. Bleach 2x46 measured against Torrentio:
    nothing for `tt0434665:2:46`, nine sources for `kitsu:49444:6`.

    Only reached when the ordinary search found nothing, so it costs a Kitsu
    lookup on a search that has already failed and nothing at all otherwise.
    Returns a copy of `meta` carrying a kitsu id, or None.
    """
    if meta.get("type") != "episode" or (meta.get("ids") or {}).get("kitsu"):
        return None
    # Anime only, and only when TMDB named the season. Everything below costs
    # a Kitsu lookup and a second round of providers, and neither is worth
    # spending on a show whose numbering nobody disagrees about.
    if not (meta.get("extra") or {}).get("anime"):
        return None

    try:
        from ..meta import kitsu
        if not kitsu.available():
            return None
        found = None
        if meta.get("season_name"):
            # A named arc: TMDB folded several broadcast runs into one season,
            # so the number has to be spent across them.
            found = kitsu.episode_address(
                meta.get("search_title") or meta.get("title") or "",
                meta.get("episode"), meta.get("season_name"),
                meta.get("season_episodes") or 0)
        if not found and not found_already:
            # The ordinary shape: one TMDB season is one broadcast run, and
            # the season number is the address. Most anime seasons have no
            # name at all, which is why the branch above reached only a fifth
            # of them in a survey of 166 anime episodes.
            #
            # Only when the first address came back empty, unlike the named
            # arc above. Measured on KonoSuba: both addresses return the same
            # 39 sources for S03E05, so asking twice buys nothing and costs a
            # round of requests. A named arc is different because TMDB's
            # address is systematically wrong there rather than merely
            # sometimes empty.
            found = kitsu.season_address(
                [meta.get("search_title") or meta.get("title") or "",
                 meta.get("original_title") or ""],
                meta.get("season"), meta.get("episode"),
                meta.get("season_counts") or [])
    except Exception:
        kodi.log_exception("could not look up an anime address")
        return None
    if not found:
        return None

    kitsu_id, episode = found
    kodi.log("nothing for %s S%02dE%02d, trying kitsu:%s:%s"
             % (meta.get("title", ""), int(meta.get("season") or 0),
                int(meta.get("episode") or 0), kitsu_id, episode))
    retry = dict(meta)
    retry["ids"] = dict(meta.get("ids") or {}, kitsu=kitsu_id)
    # The kitsu address carries its own numbering, and stream_id prefers an
    # IMDb id when it sees one - so the IMDb id has to go, or the retry asks
    # the identical question a second time.
    retry["ids"].pop("imdb", None)
    retry["episode"] = episode
    return retry

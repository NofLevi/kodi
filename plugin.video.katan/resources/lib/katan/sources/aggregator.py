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
from ..meta import items as meta_items
from . import model, scoring

TTL_RESULTS = 20 * 60          # a source list stays useful for a short while
TTL_EMPTY = 5 * 60             # remember failures briefly, but not for long

# Providers that only make sense for anime, and are skipped otherwise.
ANIME_PROVIDERS = ("nyaa", "animetosho")


def _provider_modules():
    """Import providers lazily, so an unused one costs nothing."""
    from .providers import (animetosho, comet, external, mediafusion, nyaa,
                            torrentio, zilean)
    return {
        "torrentio": torrentio,
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
        if name == "israeli":
            continue                     # handled by the Israeli provider set
        chosen.append((name, module))
    return chosen


def cache_key(meta):
    ids = meta.get("ids") or {}
    return cache.make_key("sources", meta.get("type"),
                          ids.get("imdb") or ids.get("tmdb") or meta.get("title"),
                          meta.get("season"), meta.get("episode"))


def find(meta, prefetch=False, force=False):
    """Return a ranked, short list of sources for a movie or episode."""
    key = cache_key(meta)
    if not force:
        hit = cache.get(key)
        if hit is not None:
            return _recheck_cached(hit, meta) if not prefetch else hit

    providers = _enabled_providers(meta)
    if not providers:
        kodi.log("no source providers are enabled")
        return []

    raw = _run_providers(providers, meta, quiet=prefetch)
    if not raw:
        cache.set(key, [], TTL_EMPTY)
        return []

    merged = model.dedupe(raw)
    _apply_meta(merged, meta)
    _check_debrid_cache(merged)

    ranked, rejected = scoring.rank(merged, meta, _runtime_hours(meta))
    kodi.log("sources: %d found, %d after ranking%s"
             % (len(merged), len(ranked), _rejection_summary(rejected)))

    cache.set(key, ranked, TTL_RESULTS if ranked else TTL_EMPTY)
    return ranked


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


def _apply_meta(sources, meta):
    """Attach the playback context each debrid client needs for file picking."""
    context = {
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        "title": meta.get("title"),
    }
    for source in sources:
        source.setdefault("extra", {})
        source["extra"]["meta"] = context


def _check_debrid_cache(sources):
    """One batched question per service, never one request per source."""
    from ..debrid import registry

    unknown = [s["hash"] for s in sources if s.get("hash") and not s.get("cached")]
    if not unknown:
        return
    try:
        answers = registry.cached_map(unknown)
    except Exception:
        kodi.log_exception("debrid cache lookup failed")
        return
    for source in sources:
        service = answers.get(source.get("hash"))
        if service:
            source["cached"] = True
            source["cached_by"] = service


def _recheck_cached(sources, meta):
    """Re-confirm cache flags on a cached result list.

    The list itself stays valid for a while, but whether a service still holds
    a torrent can change, and playing a source that is no longer cached is the
    most annoying possible failure.
    """
    stale = [s for s in sources if s.get("cached")]
    if not stale:
        return sources
    _check_debrid_cache([s for s in sources if not s.get("cached")])
    return sources


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
    """Everything that passed the filters, for the "show all" action."""
    hit = cache.get(cache_key(meta))
    if hit is None:
        return find(meta)
    ranked, _ = scoring.rank_all(hit, meta, _runtime_hours(meta))
    return ranked


def invalidate(meta=None):
    if meta:
        cache.delete(cache_key(meta))
    else:
        cache.delete_prefix("sources|")

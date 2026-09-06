"""Bridge to a scraper module the user already has installed.

CocoScrapers and the Umbrella scrapers are large packages with their own update
cycles. Bundling either would undo the point of this add-on, so instead we use
one if it happens to be present and stay silent if it is not.

Their work still runs under our worker cap and deadline, because the call is
made from inside the aggregator pool like any other provider.
"""
from ... import kodi, settings
from ...sources import model

NAME = "external"

_MODULES = ("cocoscrapers", "umbrellascrapers")


def _load():
    for name in _MODULES:
        try:
            module = __import__("%s.sources" % name, fromlist=["sources"])
            return name, module
        except ImportError:
            continue
    return "", None


def available():
    return bool(_load()[1])


def search(meta):
    if not settings.get_bool("sources.provider.external"):
        return []
    name, module = _load()
    if not module:
        return []
    try:
        found = _invoke(module, meta)
    except Exception:
        kodi.log_exception("external scraper %s failed" % name)
        return []
    return [_convert(entry, name) for entry in found or []
            if isinstance(entry, dict)]


def _invoke(module, meta):
    """These packages expose a sources() entry point with a loose signature."""
    ids = meta.get("ids") or {}
    payload = {
        "imdb": ids.get("imdb", ""),
        "tmdb": str(ids.get("tmdb", "")),
        "title": meta.get("title", ""),
        "year": str(meta.get("year") or ""),
        "season": str(meta.get("season") or ""),
        "episode": str(meta.get("episode") or ""),
    }
    scraper = getattr(module, "Sources", None) or getattr(module, "sources", None)
    if scraper is None:
        return []
    instance = scraper() if isinstance(scraper, type) else scraper
    getter = getattr(instance, "getSources", None) or getattr(instance, "sources", None)
    if getter is None:
        return []
    return getter(payload) if callable(getter) else []


def _convert(entry, provider):
    title = entry.get("release_title") or entry.get("name") or entry.get("title", "")
    source = model.from_release_name(
        title,
        provider="%s:%s" % (NAME, provider),
        size=_bytes(entry),
        seeders=int(entry.get("seeders") or 0),
        info_hash=entry.get("hash") or entry.get("info_hash") or "",
        magnet=entry.get("magnet") or entry.get("url", ""))
    if entry.get("cached"):
        source["cached"] = True
        source["cached_by"] = entry.get("debrid", "")
    return source


def _bytes(entry):
    """These packages report size in gigabytes as often as in bytes."""
    for key in ("size_bytes", "filesize", "size"):
        value = entry.get(key)
        if not value:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return int(number) if number > 1024 ** 2 else int(number * 1024 ** 3)
    return 0

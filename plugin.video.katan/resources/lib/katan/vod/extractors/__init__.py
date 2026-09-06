"""Per-broadcaster episode listing and stream resolution.

Each broadcaster exposes its catalogue differently, so each gets a small module
with two functions:

    episodes(ref, mode)  -> a list of items
    stream(ref, mode)    -> (url, is_adaptive)

A broadcaster with no module yet simply returns nothing, which shows as an
empty programme rather than an error. That keeps the catalogue browsable while
the extractors are filled in one at a time.

Derived from the Idan Plus add-on by Fishenzon (github.com/Fishenzon/repo).
"""
from ... import kodi

_MODULES = {}


def _load():
    if _MODULES:
        return _MODULES
    from . import kan, mako, reshet
    _MODULES.update({"kan": kan, "keshet": mako, "reshet": reshet})
    return _MODULES


def module_for(name):
    return _load().get(name)


def supported():
    return sorted(_load())


def episodes(module, ref, mode=""):
    handler = module_for(module)
    if handler is None:
        kodi.log("no extractor for %s yet" % module)
        return []
    try:
        return handler.episodes(ref, mode) or []
    except Exception:
        kodi.log_exception("listing episodes for %s failed" % module)
        return []


def stream(module, ref, mode=""):
    handler = module_for(module)
    if handler is None:
        return "", False
    try:
        return handler.stream(ref, mode)
    except Exception:
        kodi.log_exception("resolving a %s stream failed" % module)
        return "", False

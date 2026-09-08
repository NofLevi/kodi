"""One anime catalogue made of two, so neither outage is visible.

AniList is preferred while it works: it carries the AniList id that SeaDex
release rankings are keyed on. Kitsu takes over when AniList refuses, which it
has been doing since 7 September 2026.

The choice is remembered for a short while rather than made per call. Without
that, every row and every search would pay for a failing request to the dead
service first, which on a weak device is exactly the kind of tax that makes an
add-on feel broken.
"""
import time

from .. import kodi

# How long a decision about which catalogue is alive stays good. Short enough
# that a recovered service is picked up within the hour, long enough that a
# dead one is not retried on every row.
DECISION_TTL = 1800

_state = {"backend": None, "checked": 0.0}


def _anilist():
    from . import anilist
    return anilist


def _kitsu():
    from . import kitsu
    return kitsu


def backend(force=False):
    """The catalogue module to use, deciding at most once per DECISION_TTL."""
    now = time.time()
    if not force and _state["backend"] is not None:
        if now - _state["checked"] < DECISION_TTL:
            return _state["backend"]

    chosen = _kitsu()
    try:
        if _anilist().trending(limit=1):
            chosen = _anilist()
    except Exception:
        kodi.log_exception("AniList check failed, using Kitsu")

    if chosen is not _state["backend"]:
        kodi.log("anime catalogue: %s" % name_of(chosen), kodi.LOG_INFO)
    _state["backend"] = chosen
    _state["checked"] = now
    return chosen


def name_of(module):
    return (getattr(module, "__name__", "") or "").rsplit(".", 1)[-1]


def current():
    """Which catalogue is in use, for the device report and the log."""
    return name_of(_state["backend"]) if _state["backend"] else "undecided"


def reset():
    """Forget the decision, so the next call re-checks."""
    _state["backend"] = None
    _state["checked"] = 0.0


def _call(method, *args, **kwargs):
    """Run a catalogue call, falling through to the other one if it fails.

    A fall-through re-decides rather than silently using the spare, so a
    service that dies mid-session is noticed once instead of on every row.
    """
    chosen = backend()
    try:
        found = getattr(chosen, method)(*args, **kwargs)
        if found:
            return found
    except Exception:
        kodi.log_exception("%s.%s failed" % (name_of(chosen), method))

    spare = _kitsu() if chosen is _anilist() else _anilist()
    try:
        found = getattr(spare, method)(*args, **kwargs)
    except Exception:
        kodi.log_exception("%s.%s failed as well" % (name_of(spare), method))
        return []
    if found:
        kodi.log("anime catalogue fell back to %s" % name_of(spare))
        reset()
    return found or []


def trending(limit=20, page=1):
    return _call("trending", limit=limit, page=page)


def seasonal(season=None, year=None, limit=20, page=1):
    return _call("seasonal", season=season, year=year, limit=limit, page=page)


def search(query, limit=20, page=1):
    if not query:
        return []
    return _call("search", query, limit=limit, page=page)


def details(item_id, source=""):
    """Details for a title, from whichever catalogue its id belongs to."""
    module = _kitsu() if source == "kitsu" else _anilist()
    try:
        return module.details(item_id)
    except Exception:
        kodi.log_exception("%s.details failed" % name_of(module))
        return None


def current_season():
    return backend().current_season()

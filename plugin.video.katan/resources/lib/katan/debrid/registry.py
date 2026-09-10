"""Lookup and coordination for the debrid services.

The aggregator only ever talks to this module, which is what keeps the rest of
the add-on free of per-service special cases.
"""
from .. import kodi, settings

_CLASSES = {}
_INSTANCES = {}


class CacheMap(dict):
    """Positive owners plus hashes for which a service gave an answer."""

    def __init__(self):
        super(CacheMap, self).__init__()
        self.known = set()


def _load():
    if _CLASSES:
        return _CLASSES
    from .alldebrid import AllDebrid
    from .premiumize import Premiumize
    from .realdebrid import RealDebrid
    from .torbox import TorBox
    for cls in (TorBox, RealDebrid, Premiumize, AllDebrid):
        _CLASSES[cls.name] = cls
    return _CLASSES


def names():
    return list(_load().keys())


def get(name):
    """Return a service instance by name, or None."""
    if name in _INSTANCES:
        return _INSTANCES[name]
    cls = _load().get(name)
    if cls is None:
        return None
    _INSTANCES[name] = cls()
    return _INSTANCES[name]


def configured():
    """Every service the user has actually signed in to, in preference order."""
    return [get(name) for name in settings.configured_debrid() if get(name)]


def preferred():
    services = configured()
    return services[0] if services else None


def cached_map(hashes):
    """Ask every configured service which of these hashes it can play now.

    Returns {hash: service_name}. Services that cannot answer cheaply return
    nothing, and their silence is not treated as a "no": the indexer flags the
    aggregator already collected stand in for them.
    """
    found = CacheMap()
    for service in configured():
        remaining = [h for h in hashes if h not in found]
        if not remaining:
            break
        try:
            answers = service.cached_with_memory(remaining)
        except Exception:
            kodi.log_exception("%s cache lookup failed" % service.name)
            continue
        found.known.update(answers)
        for info_hash, is_cached in answers.items():
            if is_cached:
                found[info_hash] = service.name
    return found


def resolver_for(source):
    """The service that should open this source.

    A source flagged as cached by a particular service goes to that service.
    Anything else goes to the preferred one.
    """
    name = source.get("cached_by")
    if name:
        service = get(name)
        if service is not None and service.configured():
            return service
    return preferred()


def account_summary():
    """One line per configured service, for the accounts screen."""
    rows = []
    for service in configured():
        try:
            info = service.account_info()
        except Exception:
            kodi.log_exception("%s account lookup failed" % service.name)
            info = None
        rows.append({
            "name": service.name,
            "label": service.label,
            "connected": bool(info),
            "user": (info or {}).get("user", ""),
            "plan": (info or {}).get("plan", ""),
            "expires": (info or {}).get("expires", ""),
            "is_free": (info or {}).get("is_free", False),
        })
    return rows


def forget_cache_status():
    for name in names():
        service = get(name)
        if service is not None:
            service.forget()

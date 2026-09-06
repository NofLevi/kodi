"""Plugin URL routing.

Handlers register themselves with the route decorator and are dispatched by the
action query parameter. Handler modules are imported lazily inside dispatch so
that a single plugin call only loads the code it actually needs, which keeps
per-invocation start-up cheap on slow hardware.
"""
import sys

try:
    from urllib.parse import urlencode, parse_qsl
except ImportError:  # pragma: no cover - Python 2 safety net
    from urllib import urlencode
    from urlparse import parse_qsl

from . import kodi

BASE_URL = "plugin://plugin.video.katan/"

_ROUTES = {}


def route(action):
    """Register a handler for ?action=<action>."""
    def decorator(fn):
        _ROUTES[action] = fn
        return fn
    return decorator


def url_for(action, **params):
    """Build a plugin:// URL for an action, dropping empty parameters."""
    query = {"action": action}
    for key, value in params.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        query[key] = value
    return "%s?%s" % (BASE_URL, urlencode(query))


def parse_params(query_string):
    """Turn ?a=1&b=2 into a dict."""
    return dict(parse_qsl(query_string.lstrip("?")))


def registered_actions():
    return sorted(_ROUTES)


def _load_handlers():
    """Import the modules that define routes.

    Kept in one place so adding a handler module is a one-line change, and so
    an import failure in one area cannot take down the whole plugin.
    """
    from .ui import handlers          # noqa: F401  (registers routes on import)


def dispatch(argv=None):
    """Entry point called from main.py."""
    argv = argv or sys.argv
    query = argv[2] if len(argv) > 2 else ""
    params = parse_params(query)
    action = params.get("action", "home")

    try:
        _load_handlers()
    except Exception:
        kodi.log_exception("failed to load route handlers")
        kodi.notify("Add-on failed to start, see the log")
        return

    handler = _ROUTES.get(action)
    if handler is None:
        kodi.log_error("no handler for action %r" % action)
        kodi.notify("Unknown action: %s" % action)
        return

    with kodi.Timer("action %s" % action, threshold_ms=250):
        try:
            handler(params)
        except Exception:
            kodi.log_exception("action %s failed" % action)
            kodi.notify("Something went wrong, see the log")

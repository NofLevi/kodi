# -*- coding: utf-8 -*-
"""The Stremio add-on protocol: request paths in, JSON out.

Stremio asks an add-on plain GET requests:

    /manifest.json
    /{resource}/{type}/{id}.json
    /{resource}/{type}/{id}/{extra}.json

where `extra` is a URL-encoded query string - `search=kan`, `skip=100`, or
for subtitles `videoHash=...&videoSize=...&filename=...`. Everything here is
pure: no Katan, no network, so the protocol can be tested on its own.
"""
import json

try:
    from urllib.parse import parse_qsl, unquote
except ImportError:                                   # pragma: no cover
    from urllib import unquote
    from urlparse import parse_qsl

RESOURCES = ("catalog", "meta", "stream", "subtitles")

# Stremio fetches from the web app as well as the native apps, so every
# response has to be readable cross-origin.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}


class Request(object):
    __slots__ = ("resource", "type", "id", "extra")

    def __init__(self, resource, kind, item_id, extra):
        self.resource = resource
        self.type = kind
        self.id = item_id
        self.extra = extra

    def __repr__(self):
        return "Request(%s, %s, %s, %r)" % (self.resource, self.type,
                                           self.id, self.extra)


def parse(path):
    """A Stremio resource request, or None when the path is not one."""
    path = (path or "").split("?", 1)[0].split("#", 1)[0]
    if not path.endswith(".json"):
        return None
    parts = path[:-len(".json")].strip("/").split("/")
    if len(parts) not in (3, 4) or parts[0] not in RESOURCES:
        return None
    resource, kind, item_id = parts[0], unquote(parts[1]), unquote(parts[2])
    if not kind or not item_id:
        return None
    extra = {}
    if len(parts) == 4:
        # Values arrive URI-encoded inside a path segment; parse_qsl undoes
        # that, and keeps a search for "kan 11" as "kan 11".
        extra = dict(parse_qsl(parts[3], keep_blank_values=True))
    return Request(resource, kind, item_id, extra)


def encode(payload):
    """JSON body bytes for a response."""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def cache_headers(seconds):
    """How long Stremio and anything in between may keep a response."""
    seconds = max(0, int(seconds))
    return {"Cache-Control": "max-age=%d, public" % seconds} if seconds else {
        "Cache-Control": "no-store"}

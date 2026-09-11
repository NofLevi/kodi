# -*- coding: utf-8 -*-
"""Katan for Stremio - the add-on server.

    python stremio_addon/server.py

It prints the address to install in Stremio. See README.md for the tunnel
that lets the projector reach it.
"""
import logging
import os
import re
import sys

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:                                   # pragma: no cover
    raise SystemExit("Python 3.7 or newer is needed")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import protocol  # noqa: E402
import runtime  # noqa: E402

VERSION = "0.1.0"
log = logging.getLogger("katan.server")

_SUB_PATH = re.compile(r"^/sub/([0-9a-f]{16})\.srt$")


def manifest():
    import vod

    ours = ["kv", "kl"]
    return {
        "id": "org.katan.stremio",
        "version": VERSION,
        "name": "Katan",
        "description": "VOD ישראלי (כאן, קשת, רשת, ספורט 5...), ערוצים בשידור "
                       "חי, וכתוביות בעברית - כולל תרגום לאנימה.",
        "resources": [
            "catalog",
            {"name": "meta", "types": ["series", "tv"], "idPrefixes": ours},
            {"name": "stream", "types": ["series", "tv"], "idPrefixes": ours},
            {"name": "subtitles", "types": ["movie", "series"],
             "idPrefixes": ["tt", "kitsu"]},
        ],
        "types": ["series", "tv", "movie"],
        "idPrefixes": ours + ["tt", "kitsu"],
        "catalogs": vod.catalogs(),
        "behaviorHints": {"configurable": False},
    }


def answer(path, base_url):
    """(status, body bytes, content type, extra headers) for one GET."""
    import subtitles
    import vod

    clean = path.split("?", 1)[0]
    if clean == "/manifest.json":
        return 200, protocol.encode(manifest()), "application/json", \
            protocol.cache_headers(3600)
    found = _SUB_PATH.match(clean)
    if found:
        data = subtitles.serve(found.group(1))
        if data is None:
            return 404, b"", "text/plain", {}
        return 200, data, "application/x-subrip; charset=utf-8", \
            protocol.cache_headers(0)
    request = protocol.parse(clean)
    if request is None:
        return 404, b"", "text/plain", {}
    try:
        if request.resource == "catalog":
            payload = {"metas": vod.catalog(request.type, request.id,
                                            request.extra)}
            seconds = 3600
        elif request.resource == "meta":
            found_meta = vod.meta(request.id)
            if not found_meta:
                return 404, protocol.encode({"meta": None}), \
                    "application/json", {}
            payload, seconds = {"meta": found_meta}, 1800
        elif request.resource == "stream":
            # Mako's tickets last ten minutes; a cached link must not outlive one.
            payload = {"streams": vod.streams(request.id), "cacheMaxAge": 300}
            seconds = 300
        else:
            payload = {"subtitles": subtitles.handle(request.id, request.extra,
                                                     base_url)}
            seconds = 0
    except Exception:
        log.exception("answering %s failed", clean)
        empty = {"catalog": "metas", "meta": "meta", "stream": "streams",
                 "subtitles": "subtitles"}[request.resource]
        return 200, protocol.encode({empty: [] if empty != "meta" else None}), \
            "application/json", protocol.cache_headers(0)
    return 200, protocol.encode(payload), "application/json", \
        protocol.cache_headers(seconds)


class Handler(BaseHTTPRequestHandler):
    server_version = "Katan/" + VERSION

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {})

    def do_GET(self):
        token = runtime.config().get("access_token") or ""
        path = self.path
        if token:
            prefix = "/" + token
            if not (path == prefix or path.startswith(prefix + "/")):
                return self._send(404, b"", "text/plain", {})
            path = path[len(prefix):] or "/"
        base = self._base_url(token)
        if path.split("?", 1)[0] in ("/", "/index.html"):
            return self._send(200, landing(base).encode("utf-8"),
                              "text/html; charset=utf-8", {})
        status, body, kind, headers = answer(path, base)
        self._send(status, body, kind, headers)

    def _base_url(self, token):
        scheme = self.headers.get("X-Forwarded-Proto") or "http"
        host = self.headers.get("Host") or "127.0.0.1"
        return "%s://%s%s" % (scheme, host, ("/" + token) if token else "")

    def _send(self, status, body, kind, headers):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in dict(protocol.CORS_HEADERS, **headers).items():
            self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)


def landing(base):
    url = base + "/manifest.json"
    return ("<!doctype html><meta charset=utf-8><title>Katan for Stremio</title>"
            "<body style='font-family:sans-serif;max-width:40em;margin:2em auto'>"
            "<h1>Katan for Stremio</h1>"
            "<p>Install in Stremio: Add-ons &rarr; paste this address:</p>"
            "<p><code>%s</code></p>"
            "<p><a href='stremio://%s'>Open in Stremio</a></p>"
            % (url, url.split("://", 1)[1]))


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = runtime.load_config()
    runtime.boot(config)
    server = ThreadingHTTPServer((config["host"], int(config["port"])), Handler)
    server.daemon_threads = True
    token = config.get("access_token")
    local = "http://127.0.0.1:%d%s/manifest.json" % (
        int(config["port"]), ("/" + token) if token else "")
    print("Katan for Stremio is running.")
    print("Install on this PC:  %s" % local)
    print("For the projector, put a tunnel in front of port %d and use its "
          "https address with the same path (see README.md)." % config["port"])
    if not config.get("gemini_key"):
        print("No Gemini key in config.json: Hebrew will be found, not translated.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

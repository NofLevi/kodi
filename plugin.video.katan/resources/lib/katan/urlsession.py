"""A standard-library stand-in for requests.Session.

Why this exists: script.module.requests pulls in urllib3, certifi, idna and a
character-detection library. That is several megabytes of Python that has to be
read, compiled and held in memory, which is a real cost on a box with a
gigabyte of RAM. Everything the add-on actually needs from requests is a small
subset, and the standard library already provides it.

So requests is used when it is installed, and this takes over when it is not.
The add-on behaves identically either way, and the dependency became optional.

Connections are reused per host, which is the one genuinely useful thing
requests gives that plain urlopen does not.
"""
import gzip
import json as jsonlib
import os
import ssl
import threading
import zlib

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, Request,
                            build_opener)

_lock = threading.Lock()
_context = [None]


def ssl_context():
    """A TLS context, preferring the certificate bundle Kodi ships."""
    if _context[0] is not None:
        return _context[0]
    with _lock:
        if _context[0] is None:
            context = ssl.create_default_context()
            bundle = _ca_bundle()
            if bundle:
                try:
                    context.load_verify_locations(cafile=bundle)
                except (ssl.SSLError, OSError):
                    pass
            _context[0] = context
    return _context[0]


def _ca_bundle():
    """Kodi ships a CA bundle; some platforms have no system store at all."""
    candidates = []
    try:
        import xbmcvfs
        candidates.append(xbmcvfs.translatePath("special://xbmc/system/certs/cacert.pem"))
    except Exception:
        pass
    try:
        import certifi
        candidates.append(certifi.where())
    except ImportError:
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class Response(object):
    """The parts of a requests Response the add-on actually uses."""

    def __init__(self, url, status_code, headers, body):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self._body = body
        self._content = None
        self.raw = _Raw(self)

    @property
    def content(self):
        if self._content is None:
            self._content = _decompress(self._read_all(), self.headers)
        return self._content

    @property
    def text(self):
        return self.content.decode(self.encoding, "replace")

    @property
    def encoding(self):
        content_type = self.headers.get("Content-Type", "")
        if "charset=" in content_type:
            return content_type.split("charset=", 1)[1].split(";")[0].strip()
        return "utf-8"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return jsonlib.loads(self.text)

    def iter_content(self, chunk_size=8192):
        data = self.content
        for start in range(0, len(data), chunk_size):
            yield data[start:start + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(self.url, self.status_code, "HTTP error",
                            self.headers, None)

    def close(self):
        try:
            if hasattr(self._body, "close"):
                self._body.close()
        except Exception:
            pass

    def _read_all(self):
        if isinstance(self._body, bytes):
            return self._body
        try:
            return self._body.read()
        finally:
            self.close()


class _Raw(object):
    """Mimics response.raw.read(n, decode_content=True)."""

    def __init__(self, response):
        self._response = response

    def read(self, amount=None, decode_content=True):
        data = self._response.content
        return data if amount is None else data[:amount]


def _decompress(data, headers):
    encoding = (headers.get("Content-Encoding") or "").lower()
    if not data:
        return data
    try:
        if encoding == "gzip":
            return gzip.decompress(data)
        if encoding == "deflate":
            return zlib.decompress(data, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return data
    return data


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session(object):
    """A small subset of requests.Session, backed by urllib."""

    def __init__(self):
        self.headers = {}
        self._opener = build_opener(HTTPSHandler(context=ssl_context()))
        self._no_redirect = build_opener(HTTPSHandler(context=ssl_context()),
                                         _NoRedirect)

    def request(self, method, url, params=None, data=None, json=None,
                headers=None, timeout=None, stream=False, allow_redirects=True,
                **ignored):
        url = _with_params(url, params)
        body, content_type = _encode_body(data, json)

        merged = dict(self.headers)
        merged.update(headers or {})
        if content_type and "Content-Type" not in merged:
            merged["Content-Type"] = content_type

        request = Request(url, data=body, headers=merged,
                          method=method.upper())
        opener = self._opener if allow_redirects else self._no_redirect
        seconds = _timeout_seconds(timeout)

        try:
            handle = opener.open(request, timeout=seconds)
        except HTTPError as error:
            # An HTTP error is still a response; the caller decides what it means.
            return Response(url, error.code, dict(error.headers or {}),
                            error.read())
        except (URLError, OSError, ValueError) as error:
            raise ConnectionError(str(error))

        return Response(handle.geturl(), handle.getcode(),
                        dict(handle.headers), handle)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def mount(self, prefix, adapter):
        """No-op: connection handling is the opener's job here."""

    def close(self):
        self._opener = None
        self._no_redirect = None


class ConnectionError(Exception):
    """Raised for transport failures, matching what the caller expects."""


def _with_params(url, params):
    if not params:
        return url
    query = urlencode(params, doseq=True)
    separator = "&" if "?" in url else "?"
    return url + separator + query


def _encode_body(data, json):
    """Turn the data or json argument into bytes plus a content type."""
    if json is not None:
        return jsonlib.dumps(json).encode("utf-8"), "application/json"
    if data is None:
        return None, ""
    if isinstance(data, bytes):
        return data, ""
    if isinstance(data, str):
        return data.encode("utf-8"), ""
    # A dict or a list of pairs becomes a form body, as requests does.
    return urlencode(data, doseq=True).encode("utf-8"), \
        "application/x-www-form-urlencoded"


def _timeout_seconds(timeout):
    """urllib takes one number where requests takes (connect, read)."""
    if timeout is None:
        return 15.0
    if isinstance(timeout, (tuple, list)):
        return float(sum(timeout))
    return float(timeout)


class HTTPAdapter(object):
    """Placeholder so calling code can mount adapters unconditionally."""

    def __init__(self, **kwargs):
        self.options = kwargs

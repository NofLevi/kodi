"""One shared HTTP session plus the only thread pool the add-on is allowed.

Two rules the whole code base depends on:

1. Every outbound request goes through this module, so connection reuse,
   timeouts and the user agent are decided in exactly one place.
2. Parallel work goes through run_parallel, which is bounded. Nothing in the
   add-on may create its own thread per provider; that is what makes other
   add-ons fall over on a low-end box.
"""
import threading
import time
import zlib
from concurrent import futures
from urllib.parse import urlsplit

from . import kodi, urlsession

# requests is used when it happens to be installed, because its connection
# pooling is genuinely better. It is not required: script.module.requests
# drags in urllib3, certifi, idna and a character detector, which is several
# megabytes of Python to load and hold on a device with a gigabyte of RAM.
# The standard-library session in urlsession.py covers everything this add-on
# actually asks of it, so the dependency is optional rather than mandatory.
try:
    import requests
    from requests.adapters import HTTPAdapter
    HAVE_REQUESTS = True
except ImportError:
    requests = None
    HTTPAdapter = urlsession.HTTPAdapter
    HAVE_REQUESTS = False

USER_AGENT = "Katan/0.1 (Kodi)"

DEFAULT_TIMEOUT = (5, 10)   # (connect, read) seconds
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_POOL_SIZE = 8              # simultaneous sockets; matches the worker cap

_session_lock = threading.Lock()
_session = None
_parallel_lock = threading.Lock()
_parallel_pool = None
_PARALLEL_WORKERS = 4

_CREDENTIAL_HEADERS = frozenset((
    "authorization", "proxy-authorization", "cookie", "cookie2",
    "api-key", "apikey", "x-api-key", "trakt-api-key",
))


if HAVE_REQUESTS:
    class _SafeRequestsSession(requests.Session):
        """Apply the urlsession redirect credential boundary to Requests."""

        def rebuild_auth(self, prepared_request, response):
            old = urlsplit(response.request.url)
            new = urlsplit(prepared_request.url)
            if old.scheme == "https" and new.scheme != "https":
                raise requests.exceptions.InvalidURL("refusing HTTPS downgrade")
            if (old.scheme.lower(), old.hostname, old.port) != (
                    new.scheme.lower(), new.hostname, new.port):
                for name in list(prepared_request.headers):
                    if name.lower() in _CREDENTIAL_HEADERS:
                        prepared_request.headers.pop(name, None)
            super(_SafeRequestsSession, self).rebuild_auth(prepared_request,
                                                           response)


class HttpError(Exception):
    """Raised for a non-retryable HTTP problem worth surfacing to the caller."""

    def __init__(self, message, status=None, url=None):
        super(HttpError, self).__init__(message)
        self.status = status
        self.url = url


def session():
    """Return the process-wide requests session."""
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            sess = _SafeRequestsSession() if HAVE_REQUESTS else urlsession.Session()
            sess.headers.update({
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            })
            if HAVE_REQUESTS:
                adapter = HTTPAdapter(
                    pool_connections=_POOL_SIZE,
                    pool_maxsize=_POOL_SIZE,
                    max_retries=0,      # retries are handled below, with backoff
                )
                sess.mount("http://", adapter)
                sess.mount("https://", adapter)
            _session = sess
    return _session


def close_session():
    global _session
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass
            _session = None


def close_parallel():
    """Cancel queued shared work during service shutdown."""
    global _parallel_pool
    with _parallel_lock:
        pool, _parallel_pool = _parallel_pool, None
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)


def _shared_pool():
    global _parallel_pool
    with _parallel_lock:
        if _parallel_pool is None:
            _parallel_pool = futures.ThreadPoolExecutor(
                max_workers=_PARALLEL_WORKERS)
        return _parallel_pool


def request(method, url, retries=1, backoff=0.6, timeout=None, raise_for_status=False,
            **kwargs):
    """Perform an HTTP request with a timeout and bounded retries.

    Retries cover connection errors, timeouts and 5xx/429 responses only.
    A 4xx other than 429 is returned as-is, because retrying it just burns the
    rate limit budget of services such as Real-Debrid.
    """
    max_bytes = kwargs.pop("max_bytes", MAX_RESPONSE_BYTES)
    caller_stream = bool(kwargs.get("stream"))
    kwargs.setdefault("timeout", timeout or DEFAULT_TIMEOUT)
    if not caller_stream:
        kwargs["stream"] = True
    attempt = 0
    last_error = None
    while attempt <= retries:
        try:
            response = session().request(method, url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                delay = _retry_after(response, backoff, attempt)
                kodi.log("HTTP %s from %s, retrying in %.1fs"
                         % (response.status_code, _host(url), delay))
                close = getattr(response, "close", None)
                if close is not None:
                    close()
                time.sleep(delay)
                attempt += 1
                continue
            if raise_for_status and response.status_code >= 400:
                raise HttpError("HTTP %s" % response.status_code,
                                response.status_code, url)
            if not caller_stream and not _buffer_response(response, max_bytes):
                if raise_for_status:
                    raise HttpError("response exceeds safe size", None, url)
                return None
            return response
        except HttpError:
            raise
        except Exception as error:      # requests raises a family of exceptions
            last_error = error
            if attempt >= retries:
                break
            time.sleep(backoff * (2 ** attempt))
            attempt += 1
    error_name = type(last_error).__name__ if last_error is not None else "error"
    kodi.log("request to %s failed (%s)" % (_host(url), error_name))
    if raise_for_status:
        raise HttpError("request failed (%s)" % error_name, None, url)
    return None


def _buffer_response(response, max_bytes):
    """Materialise a real response with a strict decoded-byte ceiling."""
    is_requests = bool(HAVE_REQUESTS and isinstance(response, requests.Response))
    is_stdlib = isinstance(response, urlsession.Response)
    if not is_requests and not is_stdlib:
        return True
    try:
        limit = max(1, int(max_bytes))
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > limit:
            response.close()
            return False
        if is_stdlib:
            encoding = (response.headers.get("Content-Encoding") or "identity").lower()
            body = getattr(response, "_body", None)
            if isinstance(body, bytes):
                encoded = body
            else:
                chunks = []
                total = 0
                while True:
                    chunk = response.raw.read(
                        min(64 * 1024, limit + 1 - total),
                        decode_content=False)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        response.close()
                        return False
                encoded = b"".join(chunks)
            if len(encoded) > limit:
                response.close()
                return False
            decoded = _bounded_decode(encoded, encoding, limit)
            if decoded is None:
                response.close()
                return False
            response._content = decoded
            response.close()
            return True
        chunks = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > limit:
                response.close()
                return False
            chunks.append(chunk)
        response._content = b"".join(chunks)
        response._content_consumed = True
        return True
    except (TypeError, ValueError, OverflowError, OSError):
        response.close()
        return False


def _bounded_decode(data, encoding, limit):
    if encoding in ("", "identity"):
        return data
    if encoding in ("gzip", "x-gzip"):
        output = []
        total = 0
        remaining = data
        members = 0
        try:
            while remaining and remaining.strip(b"\0"):
                members += 1
                if members > 8:
                    return None
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                part = decoder.decompress(remaining, limit + 1 - total)
                total += len(part)
                output.append(part)
                if total > limit or not decoder.eof or decoder.unconsumed_tail:
                    return None
                remaining = decoder.unused_data
            return b"".join(output)
        except zlib.error:
            return None
    if encoding == "deflate":
        for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decoder = zlib.decompressobj(window_bits)
                unpacked = decoder.decompress(data, limit + 1)
                room = limit + 1 - len(unpacked)
                if room > 0:
                    unpacked += decoder.flush(room)
                if (len(unpacked) <= limit and decoder.eof
                        and not decoder.unused_data and not decoder.unconsumed_tail):
                    return unpacked
            except zlib.error:
                continue
    return None


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


def get_json(url, default=None, **kwargs):
    """GET and decode JSON, returning default on any failure."""
    response = get(url, **kwargs)
    return _json_or(response, default)


def post_json(url, default=None, **kwargs):
    response = post(url, **kwargs)
    return _json_or(response, default)


def _json_or(response, default):
    if response is None or response.status_code >= 400:
        return default
    try:
        return response.json()
    except ValueError:
        return default


def _retry_after(response, backoff, attempt):
    """Honour a Retry-After header when the server sends one."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(10.0, float(header))
        except ValueError:
            pass
    return backoff * (2 ** attempt)


def _host(url):
    """Return only a safe hostname for logs, never URL credentials or paths."""
    if not isinstance(url, str):
        return "<unknown>"
    try:
        hostname = urlsplit(url).hostname
    except (TypeError, ValueError):
        return "<unknown>"
    if not hostname or not all(char.isalnum() or char in ".-:"
                               for char in hostname):
        return "<unknown>"
    return hostname.lower()


# --------------------------------------------------------------------------
# bounded parallelism
# --------------------------------------------------------------------------


def run_parallel(tasks, workers=4, deadline=12.0, on_result=None):
    """Run a batch on the process-wide bounded executor until its deadline."""
    tasks = list(tasks)
    if not tasks:
        return {}
    limit = max(1, min(int(workers), len(tasks), _PARALLEL_WORKERS))
    results = {}
    started = time.time()
    expires = started + max(0.0, float(deadline))
    pool = _shared_pool()
    iterator = iter(tasks)
    pending = {}

    def submit_one():
        try:
            name, fn = next(iterator)
        except StopIteration:
            return False
        pending[pool.submit(_guard, name, fn)] = name
        return True

    for _unused in range(limit):
        if not submit_one():
            break

    while pending:
        remaining = expires - time.time()
        if remaining <= 0:
            break
        done, _waiting = futures.wait(
            pending, timeout=remaining,
            return_when=futures.FIRST_COMPLETED)
        if not done:
            break
        for future in done:
            name = pending.pop(future)
            try:
                value = future.result()
            except Exception:
                kodi.log_exception("task %s raised" % name)
                value = None
            if value is not None:
                results[name] = value
                if on_result is not None:
                    try:
                        on_result(name, value)
                    except Exception:
                        kodi.log_exception("on_result callback for %s raised" % name)
            submit_one()

    if pending:
        unfinished = list(pending.values())
        kodi.log("deadline hit after %.1fs, dropped: %s"
                 % (time.time() - started, ", ".join(unfinished)))
        for future in pending:
            future.cancel()
    return results


def _guard(name, fn):
    """Run a task, converting any failure into None so one bad provider is not fatal."""
    try:
        return fn()
    except Exception:
        kodi.log_exception("provider %s failed" % name)
        return None


def backend():
    """Which HTTP implementation is in use, for the device report."""
    return "requests" if HAVE_REQUESTS else "standard library"

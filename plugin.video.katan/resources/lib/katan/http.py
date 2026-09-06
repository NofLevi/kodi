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
_POOL_SIZE = 8              # simultaneous sockets; matches the worker cap

_session_lock = threading.Lock()
_session = None


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
            sess = requests.Session() if HAVE_REQUESTS else urlsession.Session()
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


def request(method, url, retries=1, backoff=0.6, timeout=None, raise_for_status=False,
            **kwargs):
    """Perform an HTTP request with a timeout and bounded retries.

    Retries cover connection errors, timeouts and 5xx/429 responses only.
    A 4xx other than 429 is returned as-is, because retrying it just burns the
    rate limit budget of services such as Real-Debrid.
    """
    kwargs.setdefault("timeout", timeout or DEFAULT_TIMEOUT)
    attempt = 0
    last_error = None
    while attempt <= retries:
        try:
            response = session().request(method, url, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                delay = _retry_after(response, backoff, attempt)
                kodi.log("HTTP %s from %s, retrying in %.1fs"
                         % (response.status_code, _host(url), delay))
                time.sleep(delay)
                attempt += 1
                continue
            if raise_for_status and response.status_code >= 400:
                raise HttpError("HTTP %s" % response.status_code,
                                response.status_code, url)
            return response
        except HttpError:
            raise
        except Exception as error:      # requests raises a family of exceptions
            last_error = error
            if attempt >= retries:
                break
            time.sleep(backoff * (2 ** attempt))
            attempt += 1
    kodi.log("request to %s failed: %s" % (_host(url), last_error))
    if raise_for_status:
        raise HttpError(str(last_error), None, url)
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
    try:
        return url.split("/")[2]
    except IndexError:
        return url


# --------------------------------------------------------------------------
# bounded parallelism
# --------------------------------------------------------------------------


def run_parallel(tasks, workers=4, deadline=12.0, on_result=None):
    """Run callables concurrently and return the results that finished in time.

    tasks     - iterable of (name, callable) pairs
    workers   - hard cap on concurrent threads
    deadline  - wall-clock budget in seconds for the whole batch
    on_result - optional callback invoked as results arrive, for progressive UI

    Tasks still running when the deadline passes are abandoned. Their threads
    finish on their own, but the caller is never blocked behind a slow provider.
    """
    from concurrent import futures

    tasks = list(tasks)
    if not tasks:
        return {}
    workers = max(1, min(int(workers), len(tasks)))
    results = {}
    started = time.time()

    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_guard, name, fn): name for name, fn in tasks}
        try:
            for future in futures.as_completed(pending, timeout=deadline):
                name = pending[future]
                try:
                    value = future.result()
                except Exception:
                    kodi.log_exception("task %s raised" % name)
                    continue
                if value is None:
                    continue
                results[name] = value
                if on_result is not None:
                    try:
                        on_result(name, value)
                    except Exception:
                        kodi.log_exception("on_result callback for %s raised" % name)
        except futures.TimeoutError:
            unfinished = [n for f, n in pending.items() if not f.done()]
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

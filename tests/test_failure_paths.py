"""What happens when the internet misbehaves.

Every provider here is somebody else's free service, and they fail in ways a
happy-path test never sees: a rate limit, a truncated body, a socket that
accepts a connection and then says nothing. The add-on's job in all of those
is the same - lose that one provider, keep the search.

The one that matters most is the deadline. A provider that hangs is worse than
one that errors, because there is nothing to catch; only the clock ends it.
"""
import time

import pytest


# --------------------------------------------------------------------------
# the HTTP layer
# --------------------------------------------------------------------------


class Response(object):
    def __init__(self, status_code=200, body=b"{}", headers=None):
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", "replace")
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.text)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_retryable_status_is_retried_then_given_up_on(monkeypatch, status):
    from katan import http

    calls = []

    def session():
        class Session(object):
            def request(self, method, url, **kwargs):
                calls.append(url)
                return Response(status)
        return Session()

    monkeypatch.setattr(http, "session", session)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    response = http.get("https://example.test/x", retries=1)
    assert len(calls) == 2, "one retry, not none and not forever"
    assert response.status_code == status, "the caller still sees the failure"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_client_error_is_not_retried(monkeypatch, status):
    """Retrying a 404 just spends someone's rate limit to be told twice."""
    from katan import http

    calls = []

    def session():
        class Session(object):
            def request(self, method, url, **kwargs):
                calls.append(url)
                return Response(status)
        return Session()

    monkeypatch.setattr(http, "session", session)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    http.get("https://example.test/x", retries=2)
    assert len(calls) == 1


def test_retry_after_is_honoured_rather_than_the_backoff(monkeypatch):
    """Real-Debrid counts refused requests against the limit, so ignoring
    Retry-After digs the hole deeper."""
    from katan import http

    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    def session():
        class Session(object):
            def request(self, method, url, **kwargs):
                return Response(429, headers={"Retry-After": "7"})
        return Session()

    monkeypatch.setattr(http, "session", session)
    http.get("https://example.test/x", retries=1, backoff=0.5)
    assert slept and slept[0] == 7.0


def test_an_absurd_retry_after_is_capped(monkeypatch):
    """A service asking us to wait an hour must not freeze the search."""
    from katan import http

    slept = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr(http, "session", lambda: type(
        "S", (), {"request": lambda self, m, u, **k: Response(
            429, headers={"Retry-After": "3600"})})())

    http.get("https://example.test/x", retries=1)
    assert slept and slept[0] <= 10


def test_a_transport_failure_becomes_none_rather_than_an_exception(monkeypatch):
    """DNS failure and connection refused reach the provider as no answer."""
    from katan import http

    def session():
        class Session(object):
            def request(self, method, url, **kwargs):
                raise OSError("Name or service not known")
        return Session()

    monkeypatch.setattr(http, "session", session)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    assert http.get("https://nothing.invalid/x", retries=0) is None


@pytest.mark.parametrize("body", [
    b"",
    b"{",
    b'{"results": ',
    b"<html>502 Bad Gateway</html>",
    b"\x00\x01\x02",
])
def test_malformed_json_returns_the_default(monkeypatch, body):
    """A truncated body is the normal shape of a failure mid-transfer."""
    from katan import http

    monkeypatch.setattr(http, "session", lambda: type(
        "S", (), {"request": lambda self, m, u, **k: Response(200, body)})())

    assert http.get_json("https://example.test/x", default={"safe": True}) == \
        {"safe": True}


# --------------------------------------------------------------------------
# the worker pool, which is the rule the whole add-on rests on
# --------------------------------------------------------------------------


def test_a_provider_that_hangs_does_not_hold_up_the_others():
    """The reason bounded concurrency exists at all.

    A provider that errors is caught. One that hangs has nothing to catch, and
    a search that waits for it is a device that looks frozen. Only the clock
    ends this one.
    """
    from katan import http

    def quick():
        return ["fast"]

    def hangs():
        time.sleep(30)
        return ["slow"]

    started = time.time()
    found = http.run_parallel([("quick", quick), ("hangs", hangs)],
                              workers=2, deadline=1.5)
    elapsed = time.time() - started

    assert found.get("quick") == ["fast"]
    assert "hangs" not in found
    assert elapsed < 5, "the deadline did not end the wait (%.1fs)" % elapsed


def test_one_provider_raising_does_not_take_the_search_with_it():
    from katan import http

    def works():
        return ["ok"]

    def explodes():
        raise RuntimeError("the API changed shape")

    found = http.run_parallel([("works", works), ("explodes", explodes)],
                              workers=2, deadline=5)
    assert found.get("works") == ["ok"]
    assert "explodes" not in found


def test_every_provider_failing_returns_nothing_rather_than_raising():
    from katan import http

    def explodes():
        raise RuntimeError("down")

    assert http.run_parallel([("a", explodes), ("b", explodes)],
                             workers=2, deadline=5) == {}


def test_no_tasks_is_not_an_error():
    from katan import http
    assert http.run_parallel([], workers=4, deadline=5) == {}


def test_the_worker_count_never_exceeds_the_cap():
    """Four workers is the budget on a device with a gigabyte of RAM."""
    import threading

    from katan import http

    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def work():
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.1)
        with lock:
            live["now"] -= 1
        return ["x"]

    http.run_parallel([(str(n), work) for n in range(12)],
                      workers=3, deadline=10)
    assert live["peak"] <= 3, "ran %d at once" % live["peak"]


# --------------------------------------------------------------------------
# the aggregator when everything below it is down
# --------------------------------------------------------------------------


def test_no_sources_is_reported_rather_than_raised(monkeypatch,
                                                   settings_module):
    """Every debrid service down at once must reach the 'nothing found' path."""
    from katan import http
    from katan.sources import aggregator

    monkeypatch.setattr(http, "run_parallel", lambda tasks, **k: {})
    meta = {"type": "movie", "ids": {"imdb": "tt0000001"}, "title": "X",
            "year": 2024}
    assert aggregator.find(meta, force=True) == []


def test_a_debrid_service_that_raises_does_not_stop_the_ranking(monkeypatch,
                                                               settings_module):
    from katan.debrid import registry

    def explodes(hashes):
        raise RuntimeError("service is down")

    monkeypatch.setattr(registry, "cached_map", explodes)

    from katan.sources import aggregator, model
    sources = [model.from_release_name("Movie.2024.1080p.WEB-DL-X",
                                       provider="t", info_hash="a" * 40)]
    aggregator._check_debrid_cache(sources)
    assert sources[0]["cached"] is False, "unknown is not the same as cached"

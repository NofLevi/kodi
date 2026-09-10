"""HTTP logging must never expose URL credentials or query tokens."""
import pytest

from katan import http


def test_request_failure_log_redacts_url_from_exception(monkeypatch):
    secret_url = "https://user:password@cdn.example/sub?token=TOPSECRET"
    logs = []

    class BrokenSession(object):
        @staticmethod
        def request(*args, **kwargs):
            raise ValueError("bad URL %s" % secret_url)

    monkeypatch.setattr(http, "session", lambda: BrokenSession())
    monkeypatch.setattr(http.kodi, "log", logs.append)
    assert http.request("GET", secret_url, retries=0) is None
    text = " ".join(logs)
    assert "cdn.example" in text
    assert "TOPSECRET" not in text
    assert "password" not in text


def test_stdlib_buffered_gzip_is_bounded_and_decoded(monkeypatch):
    import gzip
    from katan import http, urlsession

    response = urlsession.Response(
        "https://example.invalid", 200, {"Content-Encoding": "gzip"},
        gzip.compress(b"ok"))
    monkeypatch.setattr(http, "session", lambda: type(
        "Session", (), {"request": lambda *a, **k: response})())
    received = http.request("GET", "https://example.invalid", retries=0)
    assert received is response
    assert received.content == b"ok"

    bomb = urlsession.Response(
        "https://example.invalid", 200, {"Content-Encoding": "gzip"},
        gzip.compress(b"x" * 4096))
    assert http._buffer_response(bomb, 64) is False


def test_stdlib_redirect_strips_api_keys_across_origins():
    from urllib.request import Request
    from katan.urlsession import _SafeRedirect

    request = Request("https://api.example/start", headers={
        "X-Api-Key": "SYNTH", "Api-Key": "SYNTH",
        "Trakt-Api-Key": "SYNTH", "Authorization": "Bearer SYNTH"})
    redirected = _SafeRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://other.example/end")
    assert redirected is not None
    lowered = {name.lower() for name, _value in redirected.header_items()}
    assert not lowered.intersection({"authorization", "api-key", "x-api-key",
                                     "trakt-api-key"})


def test_requests_backend_refuses_downgrade_and_strips_api_keys():
    if not http.HAVE_REQUESTS:
        pytest.skip("Requests is optional")
    from requests import Response
    from requests.adapters import BaseAdapter

    class Adapter(BaseAdapter):
        def __init__(self, target):
            self.target = target
            self.seen = []
        def send(self, request, **kwargs):
            self.seen.append(request)
            response = Response()
            response.request = request
            response.url = request.url
            response._content = b""
            if len(self.seen) == 1:
                response.status_code = 302
                response.headers["Location"] = self.target
            else:
                response.status_code = 200
            return response
        def close(self):
            pass

    session = http._SafeRequestsSession()
    cross = Adapter("https://other.invalid/final")
    session.mount("https://", cross)
    session.get("https://origin.invalid/start", headers={
        "Authorization": "Bearer SYNTH", "Cookie": "sid=SYNTH",
        "X-Api-Key": "SYNTH", "Trakt-Api-Key": "SYNTH",
    })
    assert "X-Api-Key" not in cross.seen[1].headers
    assert "Trakt-Api-Key" not in cross.seen[1].headers

    downgrade = Adapter("http://other.invalid/final")
    session.mount("https://", downgrade)
    session.mount("http://", downgrade)
    with pytest.raises(Exception):
        session.get("https://origin.invalid/start")


@pytest.mark.parametrize(("url", "expected"), [
    ("https://cdn.example?X-Amz-Signature=TOPSECRET", "cdn.example"),
    ("https://user:password@cdn.example/sub?token=secret", "cdn.example"),
    ("https://cdn.example:8443/sub", "cdn.example"),
    ("relative/path?token=secret", "<unknown>"),
    (7, "<unknown>"),
])
def test_log_host_never_contains_url_secrets(url, expected):
    assert http._host(url) == expected

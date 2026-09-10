"""The standard-library HTTP session that replaces requests.

This is the default path on a plain Kodi install, so it has to behave like the
subset of requests the add-on relies on.
"""
import json


from katan import urlsession


def test_cross_origin_redirect_strips_credentials():
    original = urlsession.Request(
        "https://api.example/start",
        headers={"Authorization": "Bearer SYNTH_SECRET",
                 "Cookie": "sid=SYNTH_COOKIE", "X-Safe": "yes"})
    redirected = urlsession._SafeRedirect().redirect_request(
        original, None, 302, "Found", {}, "https://cdn.example/file")
    lowered = {key.lower(): value for key, value in redirected.header_items()}
    assert "authorization" not in lowered
    assert "cookie" not in lowered
    assert lowered["x-safe"] == "yes"


def test_https_redirect_cannot_downgrade_to_http():
    original = urlsession.Request("https://api.example/start")
    assert urlsession._SafeRedirect().redirect_request(
        original, None, 302, "Found", {}, "http://api.example/file") is None


def test_query_parameters_are_appended():
    assert urlsession._with_params("http://x/y", None) == "http://x/y"
    assert urlsession._with_params("http://x/y", {"a": "1"}) == "http://x/y?a=1"
    assert urlsession._with_params("http://x/y?b=2", {"a": "1"}) == "http://x/y?b=2&a=1"


def test_repeated_parameters_are_supported():
    """Cache checks send the same key many times."""
    url = urlsession._with_params("http://x", {"hash": ["a", "b"]})
    assert url.count("hash=") == 2


def test_json_bodies_are_encoded_with_a_content_type():
    body, content_type = urlsession._encode_body(None, {"a": 1})
    assert json.loads(body.decode("utf-8")) == {"a": 1}
    assert content_type == "application/json"


def test_form_bodies_are_url_encoded():
    body, content_type = urlsession._encode_body({"a": "1", "b": "2"}, None)
    assert b"a=1" in body and b"b=2" in body
    assert content_type == "application/x-www-form-urlencoded"


def test_a_list_of_pairs_becomes_a_repeated_form_field():
    """Premiumize wants items[] repeated, not a JSON array."""
    body, _ = urlsession._encode_body([("items[]", "x"), ("items[]", "y")], None)
    assert body.count(b"items%5B%5D=") == 2


def test_raw_bytes_pass_through_untouched():
    body, content_type = urlsession._encode_body(b"already encoded", None)
    assert body == b"already encoded"
    assert content_type == ""


def test_a_requests_style_timeout_tuple_is_accepted():
    assert urlsession._timeout_seconds((5, 10)) == 15.0
    assert urlsession._timeout_seconds(7) == 7.0
    assert urlsession._timeout_seconds(None) > 0


def test_the_response_exposes_what_callers_use():
    body = json.dumps({"hello": "world"}).encode("utf-8")
    response = urlsession.Response("http://x", 200,
                                   {"Content-Type": "application/json"}, body)
    assert response.status_code == 200
    assert response.ok is True
    assert response.json() == {"hello": "world"}
    assert response.text.startswith("{")
    assert response.content == body
    assert response.raw.read(5) == body[:5]
    assert list(response.iter_content(4))[0] == body[:4]


def test_raw_bounded_read_does_not_materialise_a_stream():
    class Body(object):
        def __init__(self):
            self.calls = []

        def read(self, amount=None):
            self.calls.append(amount)
            return b"abcdefgh" if amount is None else b"abcdefgh"[:amount]

        def close(self):
            pass

    body = Body()
    response = urlsession.Response("http://x", 206,
                                   {"Content-Encoding": "identity"}, body)
    assert response.raw.read(5, decode_content=False) == b"abcde"
    assert body.calls == [5]
    assert response._content is None


def test_gzip_bodies_are_decompressed():
    import gzip
    payload = json.dumps({"a": 1}).encode("utf-8")
    response = urlsession.Response("http://x", 200,
                                   {"Content-Encoding": "gzip",
                                    "Content-Type": "application/json"},
                                   gzip.compress(payload))
    assert response.json() == {"a": 1}


def test_a_charset_in_the_content_type_is_honoured():
    text = u"\u05e2\u05d1\u05e8\u05d9\u05ea"
    response = urlsession.Response("http://x", 200,
                                   {"Content-Type": "text/plain; charset=cp1255"},
                                   text.encode("cp1255"))
    assert response.encoding == "cp1255"
    assert response.text == text


def test_an_http_error_is_a_response_not_an_exception():
    """A 404 is information, not a transport failure."""
    response = urlsession.Response("http://x", 404, {}, b"nope")
    assert response.status_code == 404
    assert response.ok is False


def test_a_certificate_bundle_is_found():
    """Some platforms have no system trust store, so Kodi ships one."""
    context = urlsession.ssl_context()
    assert context is not None


def test_the_http_layer_reports_which_backend_it_uses():
    from katan import http
    assert http.backend() in ("requests", "standard library")

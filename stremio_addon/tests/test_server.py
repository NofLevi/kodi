# -*- coding: utf-8 -*-
import json
import threading

try:
    from urllib.request import urlopen
    from urllib.error import HTTPError
except ImportError:                                   # pragma: no cover
    from urllib2 import urlopen, HTTPError

import pytest

import runtime
import server


def test_the_manifest():
    found = server.manifest()
    assert found["id"] == "org.katan.stremio"
    names = [r if isinstance(r, str) else r["name"] for r in found["resources"]]
    assert names == ["catalog", "meta", "stream", "subtitles"]
    assert any(c["id"] == "kv-kan" for c in found["catalogs"])


def test_an_unknown_path_is_404():
    assert server.answer("/nope", "http://x")[0] == 404


def test_a_missing_subtitle_is_404():
    assert server.answer("/sub/aaaaaaaaaaaaaaaa.srt", "http://x")[0] == 404


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setitem(runtime._state["config"], "access_token", "secret")
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.daemon = True
    thread.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


def test_served_over_http_with_cors(running):
    response = urlopen(running + "/secret/manifest.json")
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert json.loads(response.read().decode("utf-8"))["name"] == "Katan"


def test_the_token_is_required(running):
    with pytest.raises(HTTPError) as caught:
        urlopen(running + "/manifest.json")
    assert caught.value.code == 404

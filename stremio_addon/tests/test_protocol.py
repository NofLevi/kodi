# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol  # noqa: E402


def test_a_plain_resource_request():
    request = protocol.parse("/meta/series/katan:kan:123.json")
    assert (request.resource, request.type, request.id, request.extra) == (
        "meta", "series", "katan:kan:123", {})


def test_extra_is_a_query_string():
    request = protocol.parse("/catalog/series/katan-kan/search=%D7%9B%D7%90%D7%9F%2011.json")
    assert request.extra == {"search": "כאן 11"}


def test_subtitle_extras_arrive_together():
    request = protocol.parse(
        "/subtitles/series/tt0426711:2:1/"
        "videoHash=abc123&videoSize=734003200&filename=Hikaru.No.Go.EP31.mkv.json")
    assert request.id == "tt0426711:2:1"
    assert request.extra == {"videoHash": "abc123", "videoSize": "734003200",
                             "filename": "Hikaru.No.Go.EP31.mkv"}


def test_ids_may_be_encoded():
    assert protocol.parse("/stream/movie/katan%3Areshet%3A9.json").id == \
        "katan:reshet:9"


def test_anything_else_is_not_a_request():
    for path in ("/", "/manifest.json", "/configure", "/catalog/series.json",
                 "/nonsense/series/x.json", "/meta/series/x", "/sub/abc.srt"):
        assert protocol.parse(path) is None, path


def test_a_query_string_on_the_url_is_ignored():
    assert protocol.parse("/meta/series/x.json?t=1").id == "x"


def test_cache_headers():
    assert protocol.cache_headers(600) == {"Cache-Control": "max-age=600, public"}
    assert protocol.cache_headers(0) == {"Cache-Control": "no-store"}

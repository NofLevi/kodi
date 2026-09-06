"""Now 14, Sport 1 and 891FM: the three smaller Israeli broadcasters.

Each fails in its own characteristic way, and those are what these cover: a
platform that answers 500 unless you name your tenant, a feed that returns
Hebrew as escape sequences, and an archive addressed by hour rather than by id.
"""
import json

import pytest

from katan import http
from katan.vod import extractors
from katan.vod.extractors import now14, radio891, sport1


class Response(object):
    def __init__(self, text, status=200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status


# --------------------------------------------------------------------------
# Now 14
# --------------------------------------------------------------------------

SERIES_DOC = {
    "title": "a programme",
    "seasons": [
        {"title": "August", "episodes": [
            {"title": "one", "videoUrl": "https://cdn/one/hls/master.m3u8",
             "image": "https://img/one.jpg", "keywords": "a plot",
             "date": 1788205553000},
            {"title": "no video", "image": "https://img/x.jpg"},
        ]},
        {"title": "September", "episodes": [
            {"title": "two", "videoUrl": "https://cdn/two/hls/master.m3u8",
             "image": "https://img/two.jpg", "date": None},
        ]},
    ],
}


@pytest.fixture
def c14(monkeypatch):
    calls = []

    def fake_get_json(url, default=None, **kwargs):
        calls.append(kwargs.get("headers") or {})
        return SERIES_DOC

    monkeypatch.setattr(http, "get_json", fake_get_json)
    return calls


def test_now14_names_its_tenant(c14):
    """The platform answers 500, not 401, if x-tenant-id is missing."""
    now14.episodes("https://insight-api-shared.univtec.com/x/series/1")
    assert c14[0].get("x-tenant-id") == "channel14"
    assert "c14.co.il" in c14[0].get("Referer", "")


def test_now14_lists_episodes_across_seasons(c14):
    found = now14.episodes("https://insight-api-shared.univtec.com/x/series/1")
    assert [item["title"] for item in found] == ["one", "two"]


def test_now14_skips_an_episode_with_no_video(c14):
    found = now14.episodes("https://insight-api-shared.univtec.com/x/series/1")
    assert all(item["extra"]["ref"].startswith("http") for item in found)


def test_now14_converts_a_millisecond_stamp_to_a_date(c14):
    found = now14.episodes("https://insight-api-shared.univtec.com/x/series/1")
    assert found[0]["premiered"].startswith("2026-")
    assert found[1]["premiered"] == "", "a missing date is blank, not 1970"


def test_now14_caches_the_reduced_series(c14):
    """The raw document is 2.8 MB; it must not be parsed twice."""
    url = "https://insight-api-shared.univtec.com/x/series/1"
    now14.episodes(url)
    now14.episodes(url)
    assert len(c14) == 1


def test_now14_plays_the_episode_url_directly(c14):
    url, adaptive = now14.stream("https://cdn/one/hls/master.m3u8")
    assert url == "https://cdn/one/hls/master.m3u8"
    assert adaptive is True


def test_now14_refuses_a_reference_that_is_not_a_url(c14):
    assert now14.episodes("1234") == []
    assert now14.stream("1234") == ("", False)


def test_now14_survives_a_service_that_does_not_answer(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: None)
    assert now14.episodes("https://insight-api-shared.univtec.com/x/1") == []


# --------------------------------------------------------------------------
# Sport 1
# --------------------------------------------------------------------------

CATEGORY_PAGE = '<script>pageGlobals = {"id": "1190", "x": 1};</script>'
# The feed returns HTML inside JSON, with Hebrew left as escape sequences.
FEED = (r'{"html":"<div class=\"video-card\">'
        r'<a href=\"/israeli-soccer/video/1942140/\">'
        r'<img data-lazy=\"https://img/a.jpg\">'
        r'<h3>תקציר</h3></a></div>"}')
CLIP_PAGE = ('<iframe id="walla-iframe-video" '
             'src="https://player.walla.co.il/?media=4097416&x=1"></iframe>')


@pytest.fixture
def s1(monkeypatch):
    def fake_get(url, **kwargs):
        if "/wp-json/" in url:
            return Response(FEED)
        if "/video/1942140" in url:
            return Response(CLIP_PAGE)
        return Response(CATEGORY_PAGE)

    def fake_get_json(url, default=None, **kwargs):
        if "dal.walla.co.il" in url:
            return {"result": "success", "data": {"video": {
                "stream_urls": [{"stream_url": "https://walla/low.m3u8"},
                                {"stream_url": "https://walla/high.m3u8"}]}}}
        return default

    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(http, "get_json", fake_get_json)


def test_sport1_decodes_hebrew_left_as_escapes(s1):
    """A unicode_escape round trip mangles this; the JSON parser does not."""
    found = sport1.episodes("https://sport1.maariv.co.il/vod/ligat-haal")
    assert found, "expected a clip card"
    assert found[0]["title"] == u"תקציר"


def test_sport1_makes_a_relative_clip_link_absolute(s1):
    found = sport1.episodes("https://sport1.maariv.co.il/vod/ligat-haal")
    assert found[0]["extra"]["ref"].startswith("https://sport1.maariv.co.il/")


def test_sport1_follows_the_clip_page_to_walla(s1):
    url, adaptive = sport1.stream(
        "https://sport1.maariv.co.il/israeli-soccer/video/1942140/")
    assert url == "https://walla/high.m3u8", "the last rendition is the best"
    assert adaptive is True


def test_sport1_falls_back_to_a_single_walla_url(monkeypatch, s1):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: {
        "result": "success",
        "data": {"video": {"stream_urls": [], "url": "https://walla/one.m3u8"}}})
    assert sport1.stream(
        "https://sport1.maariv.co.il/israeli-soccer/video/1942140/"
    )[0] == "https://walla/one.m3u8"


def test_sport1_gives_up_quietly_when_walla_refuses(monkeypatch, s1):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: {
        "result": "failure"})
    assert sport1.stream(
        "https://sport1.maariv.co.il/israeli-soccer/video/1942140/"
    ) == ("", False)


def test_sport1_gives_up_when_no_league_id_is_on_the_page(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response("<html></html>"))
    assert sport1.episodes("https://sport1.maariv.co.il/vod/x") == []


# --------------------------------------------------------------------------
# 891FM
# --------------------------------------------------------------------------

SHOW_PAGE = ('<div class="day">'
             '<a href="https://oles.tv/891fm/archive/2026-07-28/?play=15:00">'
             '<i></i> 2026-07-28</a>'
             '<a href="https://oles.tv/891fm/archive/2026-08-04/?play=15:00">'
             '<i></i> 2026-08-04</a>'
             '</div><div class="tools"></div>')
ARCHIVE_PAGE = ('<script>var DATA = {"14:00": {"stream": '
                '"https://oles.tv/videos/891fm/__archive/20260728140000.mp3"}, '
                '"15:00": {"stream": '
                '"https://oles.tv/videos/891fm/__archive/20260728150000.mp3"}};'
                '</script>')


@pytest.fixture
def fm891(monkeypatch):
    def fake_get(url, **kwargs):
        return Response(ARCHIVE_PAGE if "?play=" in url else SHOW_PAGE)

    monkeypatch.setattr(http, "get", fake_get)


def test_891fm_lists_broadcast_days_newest_first(fm891):
    found = radio891.episodes("https://www.oles.tv/891fm/shows/x/")
    assert len(found) == 2
    assert found[0]["premiered"] == "2026-08-04", "newest broadcast first"


def test_891fm_picks_the_hour_out_of_the_schedule(fm891):
    url, adaptive = radio891.stream(
        "https://oles.tv/891fm/archive/2026-07-28/?play=15:00")
    assert url.endswith("20260728150000.mp3")


def test_891fm_reports_an_mp3_as_not_adaptive(fm891):
    """A file is not a manifest; claiming otherwise stops it playing."""
    _url, adaptive = radio891.stream(
        "https://oles.tv/891fm/archive/2026-07-28/?play=15:00")
    assert adaptive is False


def test_891fm_gives_up_when_the_hour_is_not_broadcast(fm891):
    assert radio891.stream(
        "https://oles.tv/891fm/archive/2026-07-28/?play=03:00") == ("", False)


def test_891fm_gives_up_on_a_page_with_no_schedule(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: Response("<html></html>"))
    assert radio891.stream("https://oles.tv/x/?play=15:00") == ("", False)
    assert radio891.episodes("https://oles.tv/891fm/shows/x/") == []


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_every_catalogue_broadcaster_now_has_an_extractor():
    """The catalogue promised seven broadcasters and delivered two."""
    from katan.vod import library
    listed = {name for name, _count in library.modules()}
    missing = listed - set(extractors.supported())
    assert not missing, "no extractor for %s" % sorted(missing)


@pytest.mark.parametrize("module", ["14tv", "sport1", "891fm"])
def test_the_new_modules_are_registered(module):
    assert module in extractors.supported()
    assert extractors.module_for(module) is not None

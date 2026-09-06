"""Sport 5, whose whole catalogue arrives as two large JSON documents.

Two things here are worth protecting. The indexes must be reduced before they
are cached, because the raw pair is six megabytes on a device with a gigabyte
of RAM. And the clip URLs are player pages rather than manifests, so the real
stream has to be lifted out of a query parameter or Kodi is handed HTML.
"""
import json

import pytest

from katan import cache, http
from katan.vod import extractors
from katan.vod.extractors import sport5

MANIFEST = "https://sport5api.akamaized.net/Redirector/sport5/m/HLS/playlist.m3u8"
PLAYER = ("https://watch.sport5.co.il?src=" + MANIFEST +
          "&live=False&referrer=https://vod.sport5.co.il/&autoplay=true")


def clip(title, stream_url, bak=""):
    return {"title": title, "stream_url": stream_url, "stream_url_bak": bak,
            "img_upload": "https://img/%s.jpg" % title, "abstract": "a clip"}


VIDEO_DOC = {"Category": {"Category": [
    {"ID": "147", "Name": u"חדש על המדף",
     "Items": {"Item": [clip("one", PLAYER), clip("two", PLAYER)]}},
    {"ID": "3779", "Name": u"ליגת האלופות",
     "Items": {"Item": clip("solo", PLAYER)},        # a lone item is a dict
     "Category": [{"ID": "3780", "Name": "season 1",
                   "Items": {"Item": [clip("nested", PLAYER)]}}]},
    {"ID": "9999", "Name": "broken",
     "Items": {"Item": [clip("dead", "about:blank;", PLAYER)]}},
]}}

RADIO_DOC = {"data": {
    "root": {"type": "folder", "children": ["f1"]},
    "f1": {"type": "folder", "name": "a show", "children": ["g1"]},
    "g1": {"type": "episode", "name": u"פרק 57",
           "url": "https://rge/i/video/delivery/aa/bb/cc/ep57",
           "imageUrl": "https://img/ep57.jpg", "description": "an episode",
           "time": "2017/12/31 18:40"},
    "g2": {"type": "episode", "name": "already a manifest",
           "url": "https://rge/x/playlist.m3u8", "time": "2026/1/5 10:00"},
}}


@pytest.fixture
def feeds(monkeypatch):
    """Serve the two documents as the real hosts do, and count the fetches."""
    calls = []

    class FakeResponse(object):
        def __init__(self, payload, bom):
            body = json.dumps(payload).encode("utf-8")
            # The video index really is served with a byte order mark.
            self.content = (b"\xef\xbb\xbf" + body) if bom else body
            self.status_code = 200

    def fake_get(url, **kwargs):
        calls.append(url)
        if "VodCentertDS" in url:
            return FakeResponse(VIDEO_DOC, bom=True)
        if "data.json" in url:
            return FakeResponse(RADIO_DOC, bom=False)
        return None

    monkeypatch.setattr(http, "get", fake_get)
    return calls


# --------------------------------------------------------------------------
# reducing before caching
# --------------------------------------------------------------------------


def test_the_video_index_is_reduced_to_the_fields_shown(feeds):
    index = sport5.video_index()
    assert set(index) == {"147", "3779", "9999"}
    assert index["147"][0] == {
        "t": "one", "u": MANIFEST, "i": "https://img/one.jpg", "d": "a clip"}


def test_a_category_gathers_the_clips_inside_its_seasons(feeds):
    """A season is a nested category, and its clips belong to the programme."""
    titles = [c["t"] for c in sport5.video_index()["3779"]]
    assert titles == ["solo", "nested"]


def test_the_index_is_fetched_once_and_then_cached(feeds):
    sport5.video_index()
    sport5.video_index()
    sport5.episodes("147")
    assert len([c for c in feeds if "VodCentertDS" in c]) == 1


def test_a_byte_order_mark_does_not_break_the_parse(feeds):
    """Served as text/plain with a BOM, which requests decodes as latin-1."""
    assert sport5.video_index(), "the BOM must be stripped, not parsed"


def test_the_radio_index_keeps_only_playable_leaves(feeds):
    index = sport5.radio_index()
    assert set(index) == {"g1", "g2"}, "folders are not episodes"
    assert index["g1"]["u"].endswith("/ep57")


# --------------------------------------------------------------------------
# lifting the manifest out of the player page
# --------------------------------------------------------------------------


def test_the_manifest_is_lifted_out_of_the_player_url():
    assert sport5.unwrap(PLAYER) == MANIFEST


def test_a_plain_manifest_is_left_alone():
    assert sport5.unwrap(MANIFEST) == MANIFEST


def test_a_clip_with_about_blank_falls_back_to_the_spare_link(feeds):
    """The broadcaster leaves "about:blank;" in stream_url on some clips."""
    clips = sport5.video_index()["9999"]
    assert len(clips) == 1
    assert clips[0]["u"] == MANIFEST


# --------------------------------------------------------------------------
# listing and playing
# --------------------------------------------------------------------------


def test_a_category_lists_its_clips(feeds):
    found = sport5.episodes("147")
    assert [item["title"] for item in found] == ["one", "two"]
    for item in found:
        assert item["extra"]["module"] == "sport5"
        assert "action=play_vod" in item["extra"]["url"]


def test_a_radio_guid_lists_that_one_episode(feeds):
    found = sport5.episodes("g1", mode="22")
    assert len(found) == 1
    assert found[0]["title"] == u"פרק 57"
    assert found[0]["premiered"] == "2017-12-31"


def test_a_radio_date_is_padded_for_kodi(feeds):
    assert sport5.episodes("g2", mode="22")[0]["premiered"] == "2026-01-05"


def test_playing_a_clip_returns_the_manifest(feeds):
    url, adaptive = sport5.stream(MANIFEST)
    assert url == MANIFEST
    assert adaptive is True


def test_playing_a_player_url_still_unwraps_it(feeds):
    assert sport5.stream(PLAYER)[0] == MANIFEST


def test_a_radio_episode_gets_its_manifest_appended(feeds):
    url, _adaptive = sport5.stream("g1", mode="22")
    assert url == "https://rge/i/video/delivery/aa/bb/cc/ep57/master.m3u8"


def test_a_radio_url_that_is_already_a_manifest_is_not_doubled(feeds):
    assert sport5.stream("g2", mode="22")[0] == "https://rge/x/playlist.m3u8"


# --------------------------------------------------------------------------
# failing quietly
# --------------------------------------------------------------------------


def test_an_unknown_category_lists_nothing(feeds):
    assert sport5.episodes("nope") == []


def test_an_unknown_guid_plays_nothing(feeds):
    assert sport5.stream("nope") == ("", False)


def test_an_empty_reference_asks_for_nothing(feeds):
    assert sport5.episodes("") == []
    assert sport5.stream("") == ("", False)
    assert not feeds


def test_a_feed_that_does_not_answer_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, **kw: None)
    assert sport5.video_index() == {}
    assert sport5.episodes("147") == []


def test_a_feed_that_is_not_json_is_not_a_crash(monkeypatch):
    class Bad(object):
        content = b"<html>nope</html>"
        status_code = 200

    monkeypatch.setattr(http, "get", lambda url, **kw: Bad())
    assert sport5.video_index() == {}


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_sport5_is_registered_as_an_extractor():
    assert "sport5" in extractors.supported()
    assert extractors.module_for("sport5") is sport5


def test_the_catalogue_routes_sport5_through_the_extractor():
    from katan.vod import library
    entries = library.by_module("sport5", limit=5)
    assert entries
    for entry in entries:
        assert entry["extra"]["module"] == "sport5"

"""Reshet 13, whose catalogue comes from Kaltura OTT.

The interesting part of this extractor is not the API plumbing, it is that
Reshet's own season and episode metadata is wrong and the Hebrew title is the
only reliable source. These tests pin that down, because getting it wrong
silently reorders a programme and breaks picking the next unwatched episode.
"""
import pytest

from katan import http
from katan.vod import extractors
from katan.vod.extractors import reshet

SEASON = u"עונה"        # "season"
EPISODE = u"פרק"             # "episode"
SHOW = u"אהבה חדשה"


def meta(value):
    return {"value": value}


def asset(asset_id, name, season_meta=99, episode_meta=99, runtime="52:50"):
    """An episode as the live API returns one, wrong metadata included."""
    return {
        "id": asset_id,
        "name": name,
        "metas": {"SeasonNumber": meta(season_meta),
                  "EpisodeNumber": meta(episode_meta),
                  "RunTime": meta(runtime),
                  "LongSummary": meta("a summary")},
        "images": [{"url": "https://images.example/%s.jpg" % asset_id}],
    }


def title(season, episode, name):
    return u"%s, %s %d, %s %d: %s" % (SHOW, SEASON, season, EPISODE,
                                      episode, name)


@pytest.fixture
def api(monkeypatch):
    """A stand-in Kaltura OTT that records every call."""
    calls = []
    state = {"episodes": [], "sources": []}

    def fake_post_json(url, default=None, **kwargs):
        calls.append(url)
        body = kwargs.get("json") or {}
        if "anonymousLogin" in url:
            return {"result": {"ks": "test-session", "objectType":
                               "KalturaLoginSession"}}
        if "asset/action/list" in url:
            return {"result": {"totalCount": len(state["episodes"]),
                               "objects": state["episodes"]}}
        if "getPlaybackContext" in url:
            return {"result": {"sources": state["sources"]}}
        return default

    monkeypatch.setattr(http, "post_json", fake_post_json)
    return {"calls": calls, "state": state}


# --------------------------------------------------------------------------
# the numbering, which the broadcaster gets wrong
# --------------------------------------------------------------------------


def test_season_and_episode_come_from_the_title(api):
    """SeasonNumber says 4 and EpisodeNumber says 16; the title says 2 and 4."""
    api["state"]["episodes"] = [
        asset("1", title(2, 4, "the big one"), season_meta=4, episode_meta=16)]

    found = reshet.episodes("6")
    assert len(found) == 1
    assert found[0]["season"] == 2
    assert found[0]["episode"] == 4


def test_a_title_with_no_comma_still_parses(api):
    """Some entries read "season 1 episode 11" with no comma between them."""
    api["state"]["episodes"] = [
        asset("1", u"%s, %s 1 %s 11: x" % (SHOW, SEASON, EPISODE))]
    assert reshet.episodes("6")[0]["episode"] == 11


def test_an_episode_with_no_season_is_season_one(api):
    api["state"]["episodes"] = [asset("1", u"%s, %s 7: x" % (SHOW, EPISODE))]
    item = reshet.episodes("6")[0]
    assert (item["season"], item["episode"]) == (1, 7)


def test_metadata_is_used_only_when_the_title_has_no_numbers(api):
    api["state"]["episodes"] = [
        asset("1", u"a trailer", season_meta=3, episode_meta=8)]
    item = reshet.episodes("6")[0]
    assert (item["season"], item["episode"]) == (3, 8)


def test_episodes_are_ordered_for_a_viewer(api):
    """The API returns them effectively unsorted; a viewer wants 1, 2, 3."""
    api["state"]["episodes"] = [
        asset("c", title(2, 1, "c")),
        asset("a", title(1, 1, "a")),
        asset("d", title(2, 2, "d")),
        asset("b", title(1, 2, "b")),
    ]
    order = [(i["season"], i["episode"]) for i in reshet.episodes("6")]
    assert order == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_the_programme_name_is_stripped_from_the_episode_title(api):
    api["state"]["episodes"] = [asset("1", title(2, 4, "the big one"))]
    assert reshet.episodes("6")[0]["title"] == "the big one"


def test_a_title_with_no_numbering_is_left_alone(api):
    api["state"]["episodes"] = [asset("1", u"a special")]
    assert reshet.episodes("6")[0]["title"] == u"a special"


def test_a_running_time_becomes_seconds(api):
    api["state"]["episodes"] = [asset("1", title(1, 1, "x"), runtime="52:50")]
    assert reshet.episodes("6")[0]["duration"] == 52 * 60 + 50


def test_a_missing_running_time_is_zero_not_a_crash(api):
    api["state"]["episodes"] = [asset("1", title(1, 1, "x"), runtime="")]
    assert reshet.episodes("6")[0]["duration"] == 0


# --------------------------------------------------------------------------
# staying cheap
# --------------------------------------------------------------------------


def test_the_session_is_fetched_once_and_reused(api):
    api["state"]["episodes"] = [asset("1", title(1, 1, "x"))]
    reshet.episodes("6")
    reshet.episodes("7")
    reshet.episodes("8")
    logins = [c for c in api["calls"] if "anonymousLogin" in c]
    assert len(logins) == 1, "a session lasts a day; do not log in per listing"


def test_episodes_are_capped(api):
    """A programme with hundreds of episodes must not hand the UI all of them."""
    assert reshet.MAX_EPISODES <= 200


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------


def test_a_clear_source_is_preferred_over_a_drm_one(api):
    api["state"]["sources"] = [
        {"format": "applehttp", "url": "https://example/drm.m3u8",
         "drm": [{"scheme": "FairPlay"}]},
        {"format": "applehttp", "url": "https://example/clear.m3u8", "drm": []},
    ]
    url, adaptive = reshet.stream("2025866")
    assert url == "https://example/clear.m3u8"
    assert adaptive is True


def test_a_drm_only_source_is_still_returned(api):
    """Better to let the player report the problem than to show nothing."""
    api["state"]["sources"] = [
        {"format": "applehttp", "url": "https://example/drm.m3u8",
         "drm": [{"scheme": "FairPlay"}]}]
    url, _adaptive = reshet.stream("1")
    assert url == "https://example/drm.m3u8"


def test_no_sources_is_not_a_crash(api):
    api["state"]["sources"] = []
    assert reshet.stream("1") == ("", False)


def test_an_empty_reference_asks_for_nothing(api):
    assert reshet.episodes("") == []
    assert reshet.stream("") == ("", False)
    assert not api["calls"]


# --------------------------------------------------------------------------
# failing quietly
# --------------------------------------------------------------------------


def test_an_api_error_yields_no_episodes(monkeypatch):
    monkeypatch.setattr(http, "post_json", lambda url, default=None, **kw: {
        "result": {"error": {"code": "500", "message": "nope"}}})
    assert reshet.episodes("6") == []
    assert reshet.stream("1") == ("", False)


def test_a_service_that_does_not_answer_yields_no_episodes(monkeypatch):
    monkeypatch.setattr(http, "post_json", lambda url, default=None, **kw: None)
    assert reshet.episodes("6") == []


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_reshet_is_registered_as_an_extractor():
    assert "reshet" in extractors.supported()
    assert extractors.module_for("reshet") is reshet


def test_the_catalogue_routes_reshet_through_the_extractor(api):
    """The bundled ids are Kaltura SeriesIDs, so they need no translation."""
    from katan.vod import library
    entries = library.by_module("reshet", limit=3)
    assert entries, "expected Reshet programmes in the bundled catalogue"
    for entry in entries:
        assert entry["extra"]["module"] == "reshet"
        assert str(entry["extra"]["ref"]).isdigit()


# --------------------------------------------------------------------------
# the adverts, which are not in the stream anybody actually plays
# --------------------------------------------------------------------------

@pytest.mark.parametrize("external,expected", [
    # What the OTT playback source really carries: entry, then flavour.
    ("1_1p23hf83_1_fop805w6", "1_1p23hf83"),
    # Several flavours of one entry, comma separated.
    ("1_1p23hf83_1_fop805w6,1_1p23hf83_1_s6xebfi3", "1_1p23hf83"),
    ("0_abc12345_1_zzz", "0_abc12345"),
    # Anything else is refused rather than guessed: a wrong entry id does not
    # fail, it plays somebody else's programme.
    ("", ""),
    (None, ""),
    ("nonsense", ""),
    ("_", ""),
    ("x_1p23hf83_1_fop805w6", ""),
])
def test_the_kaltura_entry_is_read_off_the_playback_source(external, expected):
    from katan.vod import kaltura

    assert kaltura.entry_id(external) == expected


def test_reshet_asks_plain_kaltura_rather_than_the_ad_inserting_endpoint(
        monkeypatch):
    """The OTT endpoint answers with server-side ad insertion.

    hub13.g-mana.live splices the adverts into the same HLS timeline as the
    programme - measured on one episode, fifteen chapters, opening on an
    advert. There is no period to skip. The entry underneath is on plain
    Kaltura with nothing inserted, and its id is already on the source we
    have, so this costs one request and no change to how anything is listed.
    """
    from katan.vod import kaltura
    from katan.vod.extractors import reshet

    monkeypatch.setattr(reshet, "session", lambda refresh=False: "ks")
    monkeypatch.setattr(reshet, "_call", lambda *a, **k: {"sources": [{
        "url": "https://api.frp1.ott.kaltura.com/.../a.m3u8",
        "externalId": "1_1p23hf83_1_fop805w6",
        "format": "applehttp",
    }]})

    asked = {}

    def clean(entry, partner, referer, prefer_dash=False):
        asked.update(entry=entry, partner=partner)
        return "https://cdnapisec.kaltura.com/p/2748741/.../a.m3u8", True

    monkeypatch.setattr(kaltura, "playback_url", clean)

    url, adaptive = reshet.stream("2507482")
    assert asked["entry"] == "1_1p23hf83"
    # 5031 is the OTT account and resolves nothing on cdnapisec.
    assert asked["partner"] == 2748741
    assert "cdnapisec" in url and adaptive


def test_reshet_still_plays_when_the_clean_route_cannot_answer(monkeypatch):
    """A viewer with adverts is better off than one with a black screen."""
    from katan.vod import kaltura
    from katan.vod.extractors import reshet

    monkeypatch.setattr(reshet, "session", lambda refresh=False: "ks")
    monkeypatch.setattr(reshet, "_call", lambda *a, **k: {"sources": [{
        "url": "https://api.frp1.ott.kaltura.com/.../a.m3u8",
        "externalId": "1_1p23hf83_1_fop805w6",
        "format": "applehttp",
    }]})
    monkeypatch.setattr(kaltura, "playback_url",
                        lambda *a, **k: ("", False))

    url, adaptive = reshet.stream("2507482")
    assert url.startswith("https://api.frp1.ott.kaltura.com/"), url
    assert adaptive

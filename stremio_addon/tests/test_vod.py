# -*- coding: utf-8 -*-
import vod


def entry(title, season=None, episode=None, group="", ref=None):
    item = {"title": title, "extra": {"module": "kan", "ref": ref or title}}
    if season is not None:
        item["season"] = season
    if episode is not None:
        item["episode"] = episode
    if group:
        item["extra"]["group"] = group
    return item


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------


def test_an_id_carries_its_ref_back():
    ref = "https://kan.org.il/content/kan/kan-11/p-11486/?x=1&y=ע"
    item_id = vod.vod_id("kan", "2", ref)
    assert ":" not in item_id.split(":", 3)[3]
    assert vod.parse_id(item_id) == ("vod", "kan", "2", ref)


def test_no_mode_round_trips_as_empty():
    assert vod.parse_id(vod.vod_id("reshet", "", "57")) == ("vod", "reshet", "", "57")


def test_live_and_foreign_ids():
    assert vod.parse_id("kl:ch_11") == ("live", "ch_11", "", "")
    assert vod.parse_id("tt0111161") is None
    assert vod.parse_id("kv:kan:2:!!!") is None


# --------------------------------------------------------------------------
# seasons - Katan's grouping rules (tests/test_vod_seasons.py), for Stremio
# --------------------------------------------------------------------------


def test_numbered_seasons_keep_their_numbers_in_order():
    found = vod.seasons([entry("a", 10), entry("b", 2), entry("c", 1),
                         entry("d", 4)])
    assert [s for s, _m, _g in found] == [1, 2, 4, 10]


def test_one_numbered_season_keeps_its_number():
    found = vod.seasons([entry("a", 3), entry("b", 3)])
    assert [(s, len(m)) for s, m, _g in found] == [(3, 2)]


def test_nothing_numbered_is_one_season():
    found = vod.seasons([entry("a"), entry("b"), entry("c")])
    assert [(s, [e["title"] for e in m]) for s, m, _g in found] == \
        [(1, ["a", "b", "c"])]


def test_month_groups_are_numbered_in_the_broadcasters_order():
    found = vod.seasons([entry("a", group="אפריל 2026"),
                         entry("b", group="מרץ 2026"),
                         entry("c", group="אפריל 2026")])
    assert [(s, g, len(m)) for s, m, g in found] == [
        (1, "אפריל 2026", 2), (2, "מרץ 2026", 1)]


def test_an_unfiled_entry_goes_to_specials():
    found = vod.seasons([entry("a", 1), entry("b", 2), entry("loose")])
    assert found[-1][0] == 0
    assert [e["title"] for e in found[-1][1]] == ["loose"]


def test_episode_numbers_fall_back_to_order():
    assert vod.episode_numbers([entry("a", episode=3), entry("b", episode=1)]) == [3, 1]
    assert vod.episode_numbers([entry("a", episode=2), entry("b")]) == [1, 2]
    assert vod.episode_numbers([entry("a", episode=2), entry("b", episode=2)]) == [1, 2]


def test_meta_lists_every_episode_with_a_date(monkeypatch):
    from katan.vod import extractors, library

    monkeypatch.setattr(library, "get", lambda module, ref: {
        "title": "הפרק", "plot": "p", "art": {"poster": "https://x/p.jpg"}})
    monkeypatch.setattr(extractors, "episodes", lambda m, r, mode="": [
        dict(entry("one", 1, 1), premiered="2024-05-01"),
        entry("two", 1, 2), entry("three", 2, 1)])
    built = vod._build_meta("kan", "2", "https://kan.org.il/p")
    assert built["name"] == "הפרק"
    assert [(v["season"], v["episode"]) for v in built["videos"]] == [
        (1, 1), (1, 2), (2, 1)]
    assert built["videos"][0]["released"] == "2024-05-01T00:00:00.000Z"
    assert built["videos"][1]["released"] == vod.UNDATED
    assert vod.parse_id(built["videos"][2]["id"])[3] == "three"


# --------------------------------------------------------------------------
# streams
# --------------------------------------------------------------------------


def test_youtube_hand_off_becomes_a_yt_id(monkeypatch):
    from katan.vod import extractors
    monkeypatch.setattr(extractors, "stream", lambda m, r, mode="": (
        "plugin://plugin.video.youtube/play/?video_id=dQw4w9WgXcQ", False))
    assert vod.streams(vod.vod_id("kan", "", "x"))[0]["ytId"] == "dQw4w9WgXcQ"


def test_hls_goes_through_stremios_server(monkeypatch):
    from katan.vod import extractors
    monkeypatch.setattr(extractors, "stream", lambda m, r, mode="": (
        "https://cdn/master.m3u8", True))
    stream = vod.streams(vod.vod_id("reshet", "", "1"))[0]
    assert stream["url"] == "https://cdn/master.m3u8"
    assert stream["behaviorHints"]["notWebReady"] is True
    assert stream["behaviorHints"]["bingeGroup"] == "katan-reshet"


def test_now14_sends_its_players_referer(monkeypatch):
    from katan.vod import extractors
    monkeypatch.setattr(extractors, "stream", lambda m, r, mode="": (
        "https://c14/master.m3u8", True))
    stream = vod.streams(vod.vod_id("14tv", "", "x"))[0]
    assert stream["behaviorHints"]["proxyHeaders"]["request"]["Referer"] == \
        "https://vod.c14.co.il/"


def test_nothing_to_play_is_no_stream(monkeypatch):
    from katan.vod import extractors
    monkeypatch.setattr(extractors, "stream", lambda m, r, mode="": ("", False))
    assert vod.streams(vod.vod_id("kan", "", "x")) == []


# --------------------------------------------------------------------------
# catalogues, from the bundled library
# --------------------------------------------------------------------------


def test_every_broadcaster_has_a_catalogue():
    ids = [c["id"] for c in vod.catalogs()]
    for expected in ("kv-kan", "kv-keshet", "kv-reshet", "kv-sport5",
                     "kv-search", "kv-radio", "kl-live"):
        assert expected in ids
    assert "kv-891fm" not in ids, "radio has its own catalogue"


def test_sport5_video_leaves_the_radio_out():
    metas = vod.catalog("series", "kv-sport5", {})
    assert metas and len(metas) <= vod.PAGE
    for preview in metas:
        assert vod.parse_id(preview["id"])[2] != vod.RADIO_MODE


def test_kan_offers_only_what_plays():
    """Kan's radio and podcasts are in the index, and none of them play."""
    for skip in (0, vod.PAGE, 2 * vod.PAGE, 3 * vod.PAGE):
        for preview in vod.catalog("series", "kv-kan", {"skip": str(skip)}):
            assert vod.parse_id(preview["id"])[2] == "2"
    kan = next(c for c in vod.catalogs() if c["id"] == "kv-kan")
    genre = [e for e in kan["extra"] if e["name"] == "genre"]
    options = genre[0]["options"] if genre else []
    assert "כאן פודקאסטים" not in options and "כאן 88" not in options
    assert "כאן חינוכית 23" not in options, "kankids.org.il does not play"


def test_kan_kids_site_is_left_out():
    assert not vod.playable({"extra": {
        "module": "kan", "mode": "2",
        "ref": "https://www.kankids.org.il/content/kids/hinuchit-main/p-869745/"}})
    assert vod.playable({"extra": {
        "module": "kan", "mode": "2",
        "ref": "https://www.kan.org.il/content/kan/kan-11/p-11486/"}})


def test_paging():
    first = vod.catalog("series", "kv-kan", {})
    second = vod.catalog("series", "kv-kan", {"skip": str(vod.PAGE)})
    assert len(first) == vod.PAGE
    assert not set(m["id"] for m in first) & set(m["id"] for m in second)


def test_search():
    assert vod.catalog("series", "kv-search", {"search": ""}) == []
    assert vod.catalog("series", "kv-search", {"search": "ספורט"})


def test_a_blank_name_falls_back_to_the_description():
    news = {"title": "\n        \n    ",
            "plot": "חדשות הערב - כל ערב בשעה 20:00. מהדורת החדשות",
            "extra": {"category": "כאן 11"}}
    assert vod.display_name(news) == "חדשות הערב"
    assert vod.display_name({"title": " ", "plot": "",
                             "extra": {"category": "כאן 11"}}) == "כאן 11"
    assert vod.display_name({"title": "פאודה"}) == "פאודה"


def test_posters_only_from_the_web():
    assert vod._usable({"poster": "special://home/addons/x.png"}) is None
    assert vod._usable({"thumb": "https://x/t.jpg"}) == "https://x/t.jpg"


def test_live_channels_are_listed():
    metas = vod.catalog("tv", "kl-live", {})
    assert metas and all(m["id"].startswith("kl:") for m in metas)

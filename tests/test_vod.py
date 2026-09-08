"""Israeli live TV and the on-demand catalogue, kept as separate sections."""
import json
import os

import pytest


def test_the_israeli_rows_page_through_everything_they_have():
    """Forty-six channels were reachable twelve at a time and no further.

    `_slice` is what makes the rest of them reachable: the data is already
    in memory, so a page is a slice and there is no reason a row built from
    it should stop at one.
    """
    from katan import catalog

    items = ["item %d" % n for n in range(46)]
    limit = catalog.row_limit()

    first = catalog._slice(items, 1)
    second = catalog._slice(items, 2)
    assert len(first) == limit
    assert len(second) == limit
    assert not set(first) & set(second), "pages must not repeat each other"

    gathered = []
    page = 1
    while True:
        chunk = catalog._slice(items, page)
        if not chunk:
            break
        gathered.extend(chunk)
        page += 1
    assert gathered == items, "every channel has to be reachable eventually"


def test_a_page_past_the_end_is_empty_rather_than_wrapping():
    """An empty page is how a row knows it has finished."""
    from katan import catalog
    assert catalog._slice(["a", "b"], 9) == []
    assert catalog._slice([], 1) == []
    assert catalog._slice(None, 1) == []


def test_page_zero_is_treated_as_the_first():
    from katan import catalog
    assert catalog._slice(["a", "b"], 0) == catalog._slice(["a", "b"], 1)

from katan.vod import channels, library


@pytest.fixture(autouse=True)
def local_data(settings_module):
    """Use the bundled seed rather than any remote list."""
    settings_module.set_many({"vod.channels_url": "", "vod.series_url": ""})
    channels.refresh()
    library.refresh()


# --------------------------------------------------------------------------
# live TV
# --------------------------------------------------------------------------


def test_the_bundled_channel_list_loads():
    table = channels.load()
    assert len(table) > 40
    assert "ch_11" in table, "Kan 11 should be present"


def test_television_and_radio_are_separate_lists():
    tv = channels.live_channels(kind="tv")
    radio = channels.radio_stations()
    assert tv and radio
    assert not set(c["ids"]["channel"] for c in tv) & \
        set(c["ids"]["channel"] for c in radio)


def test_channels_are_ordered_the_way_broadcasters_number_them():
    tv = channels.live_channels(kind="tv")
    indexes = [c["extra"]["index"] for c in tv]
    assert indexes == sorted(indexes)


def test_every_channel_item_can_be_opened():
    for channel in channels.live_channels(kind="tv"):
        assert channel["extra"]["url"].startswith("plugin://")
        assert "action=play_channel" in channel["extra"]["url"]


def test_a_direct_link_resolves_as_is():
    url, headers, adaptive = channels.resolve("ch_11")
    assert url.startswith("https://")
    assert url.endswith(".m3u8") or ".m3u8?" in url


def test_a_referer_is_carried_through(no_network):
    """Reshet checks the Referer, so it has to reach the player."""
    entry = channels.get("ch_13")
    assert entry, "Reshet 13 should be in the seed data"
    url, headers, _adaptive = channels.resolve("ch_13")
    if entry["linkDetails"].get("referer"):
        assert headers.get("Referer")
        combined, _ = channels.play_url("ch_13")
        assert "Referer" in combined


def test_a_relative_path_gets_its_cdn_host():
    entry = channels.get("ch_12")
    assert entry and entry["linkDetails"]["link"].startswith("/")
    url, _headers, _adaptive = channels.resolve("ch_12")
    assert url.startswith("http"), "a relative path must be made absolute"


def test_an_unknown_channel_fails_quietly():
    assert channels.resolve("nope") == ("", {}, False)
    assert channels.play_url("nope") == ("", False)


# --------------------------------------------------------------------------
# the VOD catalogue
# --------------------------------------------------------------------------


def test_the_bundled_catalogue_loads():
    assert len(library.load()) > 1000


def test_the_catalogue_is_grouped_by_broadcaster():
    modules = library.modules()
    assert modules
    names = [name for name, _count in modules]
    assert "kan" in names and "keshet" in names
    assert modules[0][1] >= modules[-1][1], "expected the biggest first"


def test_programmes_carry_a_playable_route():
    entries = library.by_module("kan", limit=5)
    assert entries
    for entry in entries:
        assert "action=vod_show" in entry["extra"]["url"]
        assert entry["extra"]["module"] == "kan"


def test_search_finds_a_hebrew_title():
    """The catalogue is Hebrew, so search has to work on Hebrew substrings."""
    sample = library.load()[0]["n"]
    needle = sample[1:4] if len(sample) > 4 else sample
    results = library.search(needle, limit=50)
    assert results, "expected a substring match"
    assert any(needle in item["title"] for item in results)


def test_search_ignores_a_query_that_is_too_short():
    assert library.search("a") == []
    assert library.search("") == []


def test_relative_kan_posters_are_made_absolute():
    for entry in library.by_module("kan", limit=200):
        poster = entry["art"].get("poster", "")
        assert not poster.startswith("/"), "a relative poster would not load"


def test_the_catalogue_row_returns_a_spread_of_broadcasters():
    row = library.newest_episodes(limit=20)
    assert row
    studios = {tuple(item["studio"]) for item in row}
    assert len(studios) > 1, "the row should not be one broadcaster only"


def test_titles_feed_the_search_index():
    titles = library.all_titles()
    assert len(titles) == len(library.load())
    assert all(item["title"] for item in titles[:50])


def test_channels_known_to_be_broken_are_hidden(settings_module):
    """A list where a third of the entries fail is worse than a shorter one."""
    settings_module.set("vod.show_broken_channels", "false")
    shown = channels.live_channels(kind="tv")
    table = channels.load()

    broken_ids = {key for key, value in table.items()
                  if value.get("type") == "tv" and value.get("working") is False}
    assert broken_ids, "the probe should have marked some channels"

    shown_ids = {c["ids"]["channel"] for c in shown}
    assert not (shown_ids & broken_ids)


def test_broken_channels_can_be_shown_on_request(settings_module):
    hidden = len(channels.live_channels(kind="tv"))
    settings_module.set("vod.show_broken_channels", "true")
    everything = len(channels.live_channels(kind="tv"))
    assert everything > hidden
    assert everything - hidden == channels.hidden_count("tv")


# --------------------------------------------------------------------------
# DASH channels, which need an add-on that is not always there
#
# check_channels.py asks the URL for a few bytes and calls that working. A DASH
# stream needs inputstream.adaptive to play at all, so twelve channels were
# recorded as working and did not play: Kodi opened nothing and said nothing.
# --------------------------------------------------------------------------


def test_dash_channels_are_hidden_without_inputstream_adaptive(monkeypatch):
    from katan import kodi

    monkeypatch.setattr(kodi, "has_adaptive", lambda: True)
    with_adaptive = {c["ids"]["channel"] for c in channels.live_channels(kind="tv")}

    monkeypatch.setattr(kodi, "has_adaptive", lambda: False)
    without = {c["ids"]["channel"] for c in channels.live_channels(kind="tv")}

    assert without < with_adaptive, "DASH channels should drop off"
    for channel_id in with_adaptive - without:
        entry = channels.get(channel_id)
        assert channels._needs_adaptive(entry), \
            "%s was hidden but does not need DASH" % channel_id


def test_a_dash_channel_is_recognised_by_its_flag_or_its_url():
    assert channels._needs_adaptive({"linkDetails": {"adaptive": True}})
    assert channels._needs_adaptive({"linkDetails": {"link": "https://x/a.mpd"}})
    assert not channels._needs_adaptive({"linkDetails": {"link": "https://x/a.m3u8"}})
    assert not channels._needs_adaptive({})


def test_hls_channels_are_unaffected(monkeypatch):
    from katan import kodi

    monkeypatch.setattr(kodi, "has_adaptive", lambda: False)
    shown = channels.live_channels(kind="tv")
    assert shown, "the HLS channels must still be listed"
    for channel in shown:
        assert not channels._needs_adaptive(
            channels.get(channel["ids"]["channel"]))


def test_the_hidden_count_includes_unplayable_dash(monkeypatch):
    from katan import kodi

    monkeypatch.setattr(kodi, "has_adaptive", lambda: False)
    shown = len(channels.live_channels(kind="tv"))
    everything = len(channels.live_channels(kind="tv", include_broken=True))
    assert everything - shown == channels.hidden_count("tv")


def test_the_channels_that_remain_all_resolve_to_a_url():
    """Whatever is listed must at least produce something playable."""
    unresolved = []
    for channel in channels.live_channels(kind="tv"):
        url, _headers, _adaptive = channels.resolve(channel["ids"]["channel"])
        if not url:
            unresolved.append(channel["title"])
    assert not unresolved, unresolved


def test_a_channel_with_an_unreadable_position_sorts_last(tmp_path,
                                                          monkeypatch):
    """channels.json is regenerated by tooling, so a stray value is possible.

    One bad entry must cost that one entry its place in the order, not take
    down the whole channel list with a ValueError.
    """
    seed = tmp_path / "channels.json"
    seed.write_text(json.dumps({
        "ch_ok": {"name": "Two", "index": 2, "type": "tv", "module": "tv",
                  "image": "", "tvgID": "", "working": True,
                  "linkDetails": {"link": "https://example.com/a.m3u8"}},
        "ch_bad": {"name": "Broken", "index": "twelve", "type": "tv",
                   "module": "tv", "image": "", "tvgID": "", "working": True,
                   "linkDetails": {"link": "https://example.com/b.m3u8"}},
    }), encoding="utf-8")
    monkeypatch.setattr(channels, "seed_path", lambda: str(seed))
    channels.refresh()

    listed = channels.live_channels(kind="tv")
    assert [c["title"] for c in listed] == ["Two", "Broken"]


def test_a_category_stays_inside_its_broadcaster():
    """The counts beside these names are worked out per broadcaster.

    The link used to drop the broadcaster, so opening "Drama (12)" under Kan
    would list every broadcaster's drama - a different list, and a longer one
    than the number beside it promised.

    Nobody has seen that happen, and this test says why: in today's catalogue
    no category name is used by two broadcasters, so the two answers coincide.
    That is an accident of the data, which tooling regenerates, and not
    something the code arranged - which is exactly what this test is for.
    """
    modules = set()
    for module, _count in [(m, 0) for m in ("kan",)]:
        for name, count in library.categories(module):
            listed = library.by_category(name, module=module)
            assert len(listed) == count, "%s / %s" % (module, name)
            modules |= set(item["extra"]["module"] for item in listed)
    assert modules <= {"kan"}


def test_a_category_across_every_broadcaster_is_still_available():
    """Without a module it is a genuine cross-broadcaster view, not a bug."""
    everything = library.categories()
    if not everything:
        return
    name = everything[0][0]
    assert len(library.by_category(name)) >= len(
        library.by_category(name, module="kan"))


def test_a_catalogue_entry_with_a_null_poster_still_lists(tmp_path,
                                                          monkeypatch):
    """A JSON null is a present key, so a get() default never fires for it."""
    entry = {"m": "kan", "u": "/programme", "n": u"תוכנית", "d": None,
             "i": None, "o": "", "c": None}
    item = library._to_item(entry)
    assert item["art"]["poster"] == ""
    assert item["title"] == u"תוכנית"
    assert item["plot"] == ""


def test_updating_the_bundled_data_invalidates_the_cache(tmp_path, monkeypatch):
    """A data fix that only takes effect tomorrow is not a fix."""
    import json
    import os
    import time

    seed = tmp_path / "channels.json"
    seed.write_text(json.dumps({
        "ch_a": {"name": "A", "index": 1, "type": "tv", "module": "tv",
                 "image": "", "tvgID": "", "working": True,
                 "linkDetails": {"link": "https://example.com/a.m3u8"}},
    }), encoding="utf-8")
    monkeypatch.setattr(channels, "seed_path", lambda: str(seed))
    channels.refresh()

    first = channels.load()
    assert set(first) == {"ch_a"}

    # Ship a new list, as a release or a remote update would.
    time.sleep(1.1)
    seed.write_text(json.dumps({
        "ch_a": {"name": "A", "index": 1, "type": "tv", "module": "tv",
                 "image": "", "tvgID": "", "working": True,
                 "linkDetails": {"link": "https://example.com/a.m3u8"}},
        "ch_b": {"name": "B", "index": 2, "type": "tv", "module": "tv",
                 "image": "", "tvgID": "", "working": True,
                 "linkDetails": {"link": "https://example.com/b.m3u8"}},
    }), encoding="utf-8")
    os.utime(str(seed), None)

    assert set(channels.load()) == {"ch_a", "ch_b"}, \
        "the new bundle should be picked up without waiting for the TTL"


def test_html_entities_do_not_reach_the_screen(tmp_path, monkeypatch):
    """The broadcasters' pages are scraped, so their entities travel into the
    catalogue and out again. "ירדן ודידי בפיג&#x27;מה" is a real programme,
    and that is exactly what the row said."""
    seed = tmp_path / "series.json"
    seed.write_text(json.dumps([
        {"m": "kan", "u": "/a", "n": "\u05d1\u05e4\u05d9\u05d2&#x27;\u05de\u05d4",
         "d": "\u05d0 &amp; \u05d1", "i": "", "o": "", "c": "\u05d9\u05dc\u05d3\u05d9\u05dd &#x27;"},
    ]), encoding="utf-8")
    monkeypatch.setattr(library, "seed_path", lambda: str(seed))
    library.refresh()

    item = library.by_module("kan")[0]
    assert "&#x27;" not in item["title"]
    assert item["title"] == "\u05d1\u05e4\u05d9\u05d2'\u05de\u05d4"
    assert item["plot"] == "\u05d0 & \u05d1"


def test_a_cleaned_category_is_the_one_you_can_search_for():
    """Cleaning only on the way to the screen would leave search and
    by_category matching text nobody can type."""
    from katan.vod import library as lib

    for name, _count in lib.categories():
        assert "&#" not in name and "&amp;" not in name, name


def test_a_vod_programme_opens_as_a_folder(settings_module, monkeypatch):
    """It is a programme with episodes, not a stream.

    Marking it playable makes Kodi ask a directory route for a URL to play;
    the route answers with a directory, and nothing happens at all. It never
    showed while browsing, because the VOD screens build their own
    directories - only search sends these through target_url, which is
    exactly where "the search finds it and it will not open" came from.
    """
    from katan.ui import listing
    from katan.vod import library

    library.refresh()
    entry = next(e for e in library.load() if e.get("n"))
    item = library._to_item(entry)

    url, is_folder = listing.target_url(item)
    assert "action=vod_show" in url
    assert is_folder is True, "a programme has to open as a folder"


def test_a_live_channel_is_still_playable(settings_module):
    """The two share a type and must not share an answer."""
    from katan.ui import listing
    from katan.vod import channels

    channels.refresh()
    channel = channels.live_channels(limit=1)[0]
    url, is_folder = listing.target_url(channel)
    assert "action=play_channel" in url
    assert is_folder is False, "a channel is a stream, not a folder"

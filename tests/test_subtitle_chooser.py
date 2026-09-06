"""The subtitle chooser hierarchy.

What the viewer sees, in order:

    embedded tracks   already in the file, so exactly in time
    exact matches     the same release, or the same file by hash
    everything else   the estimated match, shown as an estimate

The labels matter as much as the order. "100% embedded" says why it is
trustworthy; a bare "82%" says plainly that it is a guess.
"""
import pytest

import xbmc
from katan.subs import embedded, service


@pytest.fixture
def player_with_tracks():
    """Pretend the playing file carries several subtitle tracks."""
    def install(tracks):
        xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {"subtitles": tracks}
        return tracks
    yield install
    xbmc.JSONRPC_RESULTS.clear()


# --------------------------------------------------------------------------
# finding what is inside the file
# --------------------------------------------------------------------------


def test_embedded_tracks_are_read_from_the_player(player_with_tracks):
    player_with_tracks([
        {"index": 0, "language": "eng", "name": "English"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ])
    found = embedded.streams()
    assert [s["language"] for s in found] == ["en", "he"]
    assert [s["index"] for s in found] == [0, 1]


def test_language_names_map_onto_codes(player_with_tracks):
    player_with_tracks([
        {"index": 0, "language": "Hebrew", "name": "Hebrew"},
        {"index": 1, "language": "iw", "name": "עברית"},
        {"index": 2, "language": "ara", "name": "Arabic"},
    ])
    assert [s["language"] for s in embedded.streams()] == ["he", "he", "ar"]


def test_candidates_follow_the_wanted_language_order(player_with_tracks):
    player_with_tracks([
        {"index": 0, "language": "eng", "name": "English"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ])
    found = embedded.candidates(["he", "en"])
    assert [c["language"] for c in found] == ["he", "en"]
    assert all(c["embedded"] for c in found)
    assert all(c["provider"] == embedded.PROVIDER for c in found)


def test_unwanted_languages_are_left_out(player_with_tracks):
    player_with_tracks([
        {"index": 0, "language": "fre", "name": "French"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ])
    assert [c["language"] for c in embedded.candidates(["he"])] == ["he"]


def test_forced_and_signs_tracks_are_marked_partial(player_with_tracks):
    """A forced track translates captions, not the dialogue."""
    player_with_tracks([
        {"index": 0, "language": "heb", "name": "Hebrew (Forced)"},
        {"index": 1, "language": "heb", "name": "Hebrew"},
    ])
    found = embedded.candidates(["he"])
    full = [c for c in found if not c["partial"]][0]
    forced = [c for c in found if c["partial"]][0]
    assert full["score"] > forced["score"]
    assert found[0] is full, "the full track should come first"


def test_no_tracks_is_not_an_error(player_with_tracks):
    player_with_tracks([])
    assert embedded.streams() == []
    assert embedded.candidates(["he"]) == []


# --------------------------------------------------------------------------
# what the label says
# --------------------------------------------------------------------------


def test_an_embedded_track_says_one_hundred_percent_and_embedded():
    label = service.match_label(
        {"embedded": True, "score": 100, "partial": False})
    assert label.startswith("100%")
    assert "embedded" in label


def test_a_forced_embedded_track_says_so():
    label = service.match_label(
        {"embedded": True, "score": 70, "partial": True})
    assert "70%" in label
    assert "forced" in label


def test_an_exact_match_says_only_one_hundred_percent():
    label = service.match_label({"score": 100, "reason": "hash"})
    assert label == "100%"
    assert "embedded" not in label
    assert "estimate" not in label


def test_anything_else_is_shown_as_an_estimate():
    label = service.match_label({"score": 82, "reason": "group"})
    assert label.startswith("82%")
    assert "estimate" in label


def test_a_weak_match_is_still_honest_about_being_a_guess():
    assert "estimate" in service.match_label({"score": 12})
    assert "estimate" in service.match_label({"score": 0})


def test_a_ninety_nine_percent_match_is_not_called_exact():
    """Only an exact match earns the bare 100%."""
    assert service.match_label({"score": 99}) != "100%"


# --------------------------------------------------------------------------
# the order the chooser presents
# --------------------------------------------------------------------------


@pytest.fixture
def chooser(monkeypatch, settings_module, player_with_tracks):
    """Drive the real subtitle dialog handler with controlled inputs."""
    import xbmcplugin
    from katan.subs import auto

    settings_module.set("subs.languages", "he,en")

    state = {"found": []}
    monkeypatch.setattr(auto, "video_hash_for", lambda meta: "")
    monkeypatch.setattr(auto, "search_candidates",
                        lambda meta, languages, video_hash="": list(state["found"]))
    monkeypatch.setattr(service, "_current_meta",
                        lambda: {"type": "movie", "ids": {}, "title": "X",
                                 "source": {"release": "X.2024.1080p.WEB-DL-FLUX",
                                            "group": "flux"}})

    def run(tracks, candidates):
        player_with_tracks(tracks)
        state["found"] = candidates
        xbmcplugin.reset()
        service.dispatch(["plugin://plugin.video.katan/", "1",
                          "?action=search"])
        return [item.label2 for _url, item, _folder in xbmcplugin.ITEMS]

    return run


def downloadable(release, language="he", **kwargs):
    entry = {"provider": "wizdom", "language": language, "release": release,
             "download": release}
    entry.update(kwargs)
    return entry


def test_embedded_tracks_are_listed_before_downloads(chooser):
    labels = chooser(
        [{"index": 0, "language": "heb", "name": "Hebrew"}],
        [downloadable("X.2024.1080p.WEB-DL-FLUX")])

    assert len(labels) == 2
    assert "embedded" in labels[0], "the embedded track should lead"
    assert "embedded" not in labels[1]


def test_an_exact_download_beats_a_weaker_one(chooser):
    labels = chooser([], [
        downloadable("Something.Else.2019.DVDRip-XYZ"),
        downloadable("X.2024.1080p.WEB-DL-FLUX"),
    ])
    assert labels[0].startswith("100%")
    assert "estimate" in labels[1]


def test_the_full_hierarchy_reads_top_to_bottom(chooser):
    """Embedded, then exact, then estimates, each labelled for what it is."""
    labels = chooser(
        [{"index": 0, "language": "heb", "name": "Hebrew"}],
        [
            downloadable("X.2024.1080p.WEB-DL-FLUX"),
            downloadable("X.2024.720p.HDTV-OTHER"),
        ])

    assert len(labels) == 3
    assert labels[0].startswith("100%") and "embedded" in labels[0]
    assert labels[1] == labels[1] and labels[1].startswith("100%")
    assert "embedded" not in labels[1]
    assert "estimate" in labels[2]


def test_an_empty_chooser_tells_the_viewer(chooser):
    import xbmcgui
    del xbmcgui.NOTIFICATIONS[:]
    labels = chooser([], [])
    assert labels == []
    assert xbmcgui.NOTIFICATIONS


def test_choosing_an_embedded_track_switches_the_player(monkeypatch):
    """It returns no file: Kodi keeps playing and just changes stream."""
    import xbmcplugin

    switched = []
    monkeypatch.setattr(embedded, "select",
                        lambda index: switched.append(index) or True)

    xbmcplugin.reset()
    service.dispatch(["plugin://plugin.video.katan/", "1",
                      "?action=download&provider=embedded&id=2&language=he"])

    assert switched == ["2"]
    assert xbmcplugin.ITEMS == [], "an embedded pick returns no file"
    assert xbmcplugin.ENDED

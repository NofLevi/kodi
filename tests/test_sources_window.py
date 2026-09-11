"""The source picker.

This was the only window with no tests, and it was shipping a crash that made
it useless: building the badge for a cached source raised, and since cached
sources rank first, the first row took out the whole list. The viewer got a
title, no status, a blank button and nothing to choose from.

It survived because sources.autoplay is on by default, so the picker only
opens when someone deliberately asks to choose.
"""
import pytest

from katan import kodi
from katan.ui import sources_window


def source(title, **kwargs):
    entry = {"title": title, "hash": "a" * 40, "provider": "torrentio",
             "providers": ["torrentio"], "quality": "1080p",
             "size": 8 * 1024 ** 3, "seeders": 42, "languages": [],
             "hdr": [], "audio": "unknown", "cached": False, "cached_by": ""}
    entry.update(kwargs)
    return entry


CACHED = source("Film.2024.1080p.WEB-DL-GRP", cached=True, cached_by="torbox")
PLAIN = source("Film.2024.720p.WEB-DL-OTHER", quality="720p")


@pytest.fixture
def picker():
    window = sources_window.SourcesWindow()
    window.short = [CACHED, PLAIN]
    window.full = [CACHED, PLAIN, source("Film.2024.2160p-THIRD")]
    window.meta = {"type": "movie", "title": "Film", "year": 2024}
    window.prepare()
    window.onInit()
    return window


# --------------------------------------------------------------------------
# the crash
# --------------------------------------------------------------------------


def test_a_cached_source_does_not_crash_the_badge():
    """The bug: .strip() bound to the tuple, not the formatted string."""
    badge = sources_window._badge(CACHED)
    assert badge, "a cached source must produce a badge, not raise"
    assert "1080P" in badge


def test_the_list_actually_fills(picker):
    control = picker.getControl(sources_window.LIST_SOURCES)
    assert control.size() == 2, "the picker rendered nothing at all before"


# --------------------------------------------------------------------------
# what the badge says about subtitles
# --------------------------------------------------------------------------


def _subs_line(entry):
    """The subtitle line a row would show.

    Its own property, because squeezed onto the badge the whole right-hand
    column was cut off. Read straight off the source, because the aggregator
    works it out before ranking - the badge and the order the rows are in
    come from the same numbers and cannot disagree.
    """
    row = dict(CACHED, subs_kind=entry["kind"], subs_score=entry["score"])
    return sources_window._list_item(row).getProperty("subs")


def test_a_release_carrying_hebrew_says_so():
    from katan.subs import outlook

    line = _subs_line({"kind": outlook.EMBEDDED, "score": 0})
    assert kodi.localize(32474) in line
    assert "%" not in line, \
        "a claim from the release name does not get a percentage of ours"


def test_an_external_subtitle_shows_how_well_it_matches():
    from katan.subs import outlook

    assert "82" in _subs_line({"kind": outlook.EXTERNAL, "score": 82})


def test_nothing_found_says_nothing_found():
    from katan.subs import outlook

    assert kodi.localize(32476) in _subs_line({"kind": outlook.NONE,
                                               "score": 0})


def test_the_subtitle_line_is_separate_from_the_badge():
    """They are on different rows of the layout, and the badge must not grow
    to include the subtitle text - that is what cut it off."""
    from katan.subs import outlook

    item = sources_window._list_item(
        dict(CACHED, subs_kind=outlook.EXTERNAL, subs_score=82))
    assert "82" not in item.getProperty("badge")
    assert "1080P" in item.getProperty("badge")
    assert "82" in item.getProperty("subs")


def test_a_name_match_is_labelled_as_an_estimate():
    """Before playback nothing has been downloaded or fitted.

    The number is how alike two filenames are, and measured against real
    timing that predicts little: subtitles whose names scored 70-89 fitted in
    none of three cases, while a 62 fitted at 0.95. It is a guess, and the
    picker now says so in the same word the subtitle chooser already used.
    """
    from katan.subs import outlook

    line = _subs_line({"kind": outlook.EXTERNAL, "score": 82})
    assert "82" in line
    assert "estimate" in line.lower()


def test_a_source_nobody_looked_up_draws_without_a_subtitle_line():
    """The lookup can fail or be skipped, and a row must still draw."""
    assert sources_window._badge(CACHED)
    assert "1080P" in sources_window._badge(CACHED)
    assert sources_window._list_item(CACHED).getProperty("subs") == ""


def test_every_source_renders_even_the_awkward_ones():
    """One bad row used to abandon the whole list."""
    awkward = [
        source("No quality", quality=""),
        source("Unknown quality", quality="unknown"),
        source("No size at all", size=0, seeders=0),
        source("Hebrew", languages=["he"]),
        source("HDR", hdr=["hdr10", "dv"]),
        source("Cached elsewhere", cached=True, cached_by="realdebrid"),
        source("No providers", providers=[], provider=""),
    ]
    window = sources_window.SourcesWindow()
    window.short = awkward
    window.meta = {"title": "x"}
    window.prepare()
    window.onInit()
    assert window.getControl(sources_window.LIST_SOURCES).size() == len(awkward)


# --------------------------------------------------------------------------
# what the viewer reads
# --------------------------------------------------------------------------


def test_the_service_is_named_the_way_it_names_itself(picker):
    """"TorBox", not the module id shouted in capitals."""
    badge = sources_window._badge(CACHED)
    assert "TorBox" in badge
    assert "TORBOX" not in badge


def test_providers_are_named_not_module_ids():
    detail = sources_window._detail(
        source("x", providers=["torrentio", "mediafusion"]))
    assert "Torrentio" in detail and "MediaFusion" in detail
    assert "mediafusion" not in detail


def test_an_unknown_quality_is_not_called_sd():
    """Labelling an unknown release SD is a claim, not a fallback."""
    assert "SD" not in sources_window._badge(source("x", quality=""))
    assert "SD" not in sources_window._badge(source("x", quality="unknown"))


def test_a_known_quality_is_shown():
    assert "2160P" in sources_window._badge(source("x", quality="2160p"))


# --------------------------------------------------------------------------
# the window's own furniture
# --------------------------------------------------------------------------


def test_the_title_and_toggle_are_set_before_the_window_is_shown():
    """The toggle button's whole label is a property; unset it renders blank."""
    window = sources_window.SourcesWindow()
    window.short = [CACHED]
    window.meta = {"type": "movie", "title": "Film", "year": 2024}
    window.prepare()

    assert window.getProperty("katan.sources.title") == "Film (2024)"
    assert window.getProperty("katan.sources.toggle"), "the button would be blank"
    assert window.getProperty("katan.sources.status")


def test_an_episode_heading_says_which_episode():
    heading = sources_window._heading(
        {"type": "episode", "title": "Show", "season": 2, "episode": 5})
    assert "2x05" in heading


def test_showing_all_switches_the_list(picker):
    assert picker.getControl(sources_window.LIST_SOURCES).size() == 2
    picker.onClick(sources_window.BUTTON_TOGGLE)
    assert picker.getControl(sources_window.LIST_SOURCES).size() == 3


def test_choosing_a_row_returns_that_source(picker):
    control = picker.getControl(sources_window.LIST_SOURCES)
    control.selectItem(1)
    picker.onClick(sources_window.LIST_SOURCES)
    assert picker.chosen is PLAIN


def test_refresh_closes_and_asks_for_another_search(picker):
    picker.onClick(sources_window.BUTTON_REFRESH)
    assert picker.refresh_requested is True


def test_an_empty_list_still_says_something():
    window = sources_window.SourcesWindow()
    window.short = []
    window.meta = {"title": "Film", "year": 2024}
    window.prepare()
    window.onInit()
    assert window.getProperty("katan.sources.status"), \
        "an empty picker must explain itself, not just be blank"


# --------------------------------------------------------------------------
# DUB, SUB and DUAL, which were parsed and never once drawn
# --------------------------------------------------------------------------

def test_the_dub_reaches_the_badge():
    """Anime publishes the same episode twice and the name is the only clue.

    `release.parse` has read this into a `dub` field all along and
    `model.label` has rendered it all along - but `model.label` has no callers,
    and the picker builds its own label, so it had never been on a screen. Two
    rows of the same resolution, size and group are indistinguishable without
    it, so choosing the dub meant starting one and backing out.
    """
    from katan.ui import sources_window

    for value, expected in (("dub", "DUB"), ("sub", "SUB"), ("dual", "DUAL")):
        badge = sources_window._badge({"quality": "1080p", "dub": value})
        assert expected in badge, "%r is not in %r" % (expected, badge)


def test_no_dub_claim_adds_nothing():
    """It is blank for everything that is not anime, and that is deliberate.

    "MULTI.SUBS" on a live-action film is a claim about its subtitles and says
    nothing at all about the audio, so an empty field must stay invisible
    rather than becoming a third state on every row.
    """
    from katan.ui import sources_window

    badge = sources_window._badge({"quality": "1080p", "dub": ""})
    assert badge == "1080P", badge

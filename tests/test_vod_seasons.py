# -*- coding: utf-8 -*-
"""A VOD programme opens on its seasons, the way every other one does.

Fifty-two episodes of "החברים של נאור" over four seasons were one flat list,
and Reshet's own numbering was already there to group them by. The rule is
that the *broadcaster* decides: where it numbers seasons there are season
folders, where it files by month there are months, and where it numbers
nothing - a sports channel's clips, a nightly news programme - the flat list
is left exactly as it was, because one folder called "Season 0" is worse than
no folder at all.
"""
import pytest

import xbmcplugin
from katan import kodi
from katan.meta import items as meta_items
from katan.ui import handlers


@pytest.fixture(autouse=True)
def handle():
    kodi.set_plugin_handle(1)
    xbmcplugin.reset()


def episode(title, season=0, group="", module="reshet", ref="p1"):
    extra = {"url": "plugin://plugin.video.katan/?action=play_vod",
             "module": module, "ref": ref}
    if group:
        extra["group"] = group
    return meta_items.new_item("vod", ids={"vod": title}, title=title,
                               season=season, extra=extra)


def run(entries, monkeypatch, **params):
    from katan.vod import extractors
    monkeypatch.setattr(extractors, "episodes",
                        lambda module, ref, mode="": list(entries))
    handlers.vod_show(dict({"module": "reshet", "ref": "p1"}, **params))
    return xbmcplugin.ITEMS


def labels(rows):
    return [item.getLabel() for _url, item, _folder in rows]


def folders(rows):
    return [item.getLabel() for _url, item, is_folder in rows if is_folder]


# --------------------------------------------------------------------------
# numbered seasons
# --------------------------------------------------------------------------


def test_a_programme_with_seasons_opens_on_them(monkeypatch):
    entries = ([episode("a%d" % n, season=1) for n in range(3)]
               + [episode("b%d" % n, season=2) for n in range(4)])

    rows = run(entries, monkeypatch)

    assert len(rows) == 2
    assert len(folders(rows)) == 2


def test_one_season_is_left_flat(monkeypatch):
    """A folder you have to open to find the only thing inside it."""
    entries = [episode("a%d" % n, season=1) for n in range(6)]

    rows = run(entries, monkeypatch)

    assert len(rows) == 6
    assert folders(rows) == []


def test_a_broadcaster_that_numbers_nothing_is_left_alone(monkeypatch):
    """Sport clips and a nightly news programme have no seasons, and one
    folder called "Season 0" is worse than the list it replaced."""
    entries = [episode("clip %d" % n) for n in range(9)]

    rows = run(entries, monkeypatch)

    assert len(rows) == 9
    assert folders(rows) == []


def test_choosing_a_season_lists_only_its_episodes(monkeypatch):
    entries = ([episode("a%d" % n, season=1) for n in range(3)]
               + [episode("b%d" % n, season=2) for n in range(4)])

    rows = run(entries, monkeypatch, season="2")

    assert labels(rows) == ["b0", "b1", "b2", "b3"]


def test_seasons_are_ordered_by_number(monkeypatch):
    """Reshet returns them in an order of its own, and somebody looking for
    season three should not have to hunt for it."""
    entries = [episode("x", season=n) for n in (10, 2, 1, 4)]

    rows = run(entries, monkeypatch)
    seasons = [url.split("season=")[-1] for url, _i, _f in rows]

    assert seasons == ["1", "2", "4", "10"]


# --------------------------------------------------------------------------
# groups that are not seasons
# --------------------------------------------------------------------------


def test_a_broadcaster_that_files_by_month_says_month(monkeypatch):
    """Now 14 groups a programme by month - "אפריל 2026" - and calling that
    "Season 1" would invent something the broadcaster never said."""
    entries = ([episode("a", group=u"אפריל 2026")] * 3
               + [episode("b", group=u"מאי 2026")] * 2)

    rows = run(entries, monkeypatch)

    assert folders(rows) == [u"אפריל 2026",
                             u"מאי 2026"]


def test_named_groups_keep_the_broadcasters_order(monkeypatch):
    """It is usually chronological and there is nothing better to sort by."""
    entries = [episode("a", group="December"), episode("b", group="April")]

    assert folders(run(entries, monkeypatch)) == ["December", "April"]


# --------------------------------------------------------------------------
# the entries that belong to no season
# --------------------------------------------------------------------------


def test_an_unfiled_entry_does_not_cancel_the_grouping(monkeypatch):
    """Kan puts a "watch the first episode" link on the programme page. One
    of those used to be a reason to abandon grouping entirely, which left a
    four-season programme as one list of six hundred and twelve."""
    entries = ([episode("watch the first one")]
               + [episode("a%d" % n, season=1) for n in range(3)]
               + [episode("b%d" % n, season=2) for n in range(3)])

    rows = run(entries, monkeypatch)

    assert len(folders(rows)) == 2
    assert labels(rows)[-1] == "watch the first one", "shown after the seasons"


def test_the_unfiled_entry_is_still_playable(monkeypatch):
    entries = ([episode("loose")]
               + [episode("a", season=1), episode("b", season=2)])

    rows = run(entries, monkeypatch)

    assert rows[-1][2] is False, "a link to one episode is not a folder"


# --------------------------------------------------------------------------
# what each broadcaster contributes
# --------------------------------------------------------------------------


def test_kan_reads_the_season_out_of_the_address():
    """It is the only place Kan puts it."""
    from katan.vod.extractors import kan

    assert kan._season_of(
        "https://www.kan.org.il/content/kan/kan-actual/p-12463/s4/1094610/") == 4
    assert kan._season_of(
        "https://www.kan.org.il/content/kan/kan-actual/p-11544/1097335/") == 0
    assert kan._season_of(
        "https://www.kan.org.il/content/kan/kan-actual/p-12463/shorts/853091/") == 0


@pytest.mark.parametrize("link,is_season", [
    ("https://www.mako.co.il/mako-vod-keshet/eretz_nehederet-s20", True),
    ("https://www.mako.co.il/mako-vod-keshet/eretz_nehederet-s20/", True),
    ("https://www.mako.co.il/mako-vod-keshet/the_roommates-s1", True),
    ("https://www.mako.co.il/mako-vod-keshet/eretz_nehederet-s20/"
     "VOD-5dd3698d83a5381026.htm", False),
])
def test_mako_tells_a_season_page_from_a_video(link, is_season):
    """Mako lists a long-running programme as its seasons, and each of those
    was offered as something to play - so choosing one asked the player for a
    directory and nothing happened. An episode's address ends in its own VOD
    document; a season's does not."""
    from katan.vod.extractors import mako

    assert mako._is_a_season(link) is is_season

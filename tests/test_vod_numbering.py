# -*- coding: utf-8 -*-
"""Episode numbers for Israeli VOD, read out of the broadcaster's titles.

Only Reshet ever filled these fields in. Kan set a season and no episode;
Mako, Now14, Sport1, Sport5 and the radio set neither - so most of a
programme listed as a column of bare names with nothing to say which episode
was which. The broadcasters do number them; they write it in the title.
"""
import pytest

from pinky.meta import items
from pinky.vod import numbering

SEASON_EPISODE = u"\u05e2\u05d5\u05e0\u05d4 2 \u05e4\u05e8\u05e7 4"
EPISODE_ONLY = u"\u05e4\u05e8\u05e7 12"
A_DATE = u"\u05d7\u05d3\u05e9\u05d5\u05ea 12.11.2026"
JUST_A_NAME = u"\u05e0\u05d1\u05e1\u05d5"


def test_a_title_with_both_numbers():
    assert numbering.season_episode(SEASON_EPISODE) == (2, 4)


def test_a_title_with_only_an_episode():
    assert numbering.season_episode(EPISODE_ONLY) == (0, 12)


def test_a_date_is_not_an_episode_number():
    """A nightly news programme is titled by date, and 2026 is not episode
    2026 - four digits are refused for exactly this."""
    assert numbering.season_episode(A_DATE) == (0, 0)


def test_a_name_on_its_own_stays_unnumbered():
    assert numbering.season_episode(JUST_A_NAME) == (0, 0)


def test_numbering_fills_what_the_provider_left_empty():
    entries = [{"type": "vod", "title": EPISODE_ONLY, "season": 3}]
    numbering.number(entries)
    assert entries[0]["episode"] == 12
    assert entries[0]["season"] == 3, "a season from the URL beats a guess"


def test_a_provider_that_numbered_itself_is_not_overruled():
    """Reshet reads these from its API, which beats any title parse."""
    entries = [{"type": "vod", "title": EPISODE_ONLY, "season": 2, "episode": 4}]
    numbering.number(entries)
    assert entries[0]["episode"] == 4


def test_nothing_but_vod_items_are_touched():
    entries = [{"type": "movie", "title": EPISODE_ONLY}]
    numbering.number(entries)
    assert "episode" not in entries[0]


def test_a_numbered_vod_episode_is_labelled_like_one():
    """It used to fall through to the plain-title branch and show the name
    alone, which is the bug as it was reported."""
    assert items.label({"type": "vod", "title": "X", "season": 2,
                        "episode": 4}) == "2x04. X"


def test_without_a_season_the_episode_number_still_shows():
    assert items.label({"type": "vod", "title": "X", "episode": 12}) == "12. X"


def test_an_unnumbered_vod_item_is_left_as_it_was():
    assert items.label({"type": "vod", "title": "X"}) == "X"

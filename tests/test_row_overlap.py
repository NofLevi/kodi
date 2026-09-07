# -*- coding: utf-8 -*-
"""A tab should not show the same posters three times under three headings.

The rows in a tab ask TMDB overlapping questions and always will: "trending
this week" and "popular" are different questions with much the same answer,
and measured against the live API they shared six films out of twelve. So a
row shows what is left after the rows above it have taken theirs.

Deliberately only in a tab. The mixed home listing has no top-to-bottom order
to inherit priority from, so it is left exactly as it was.
"""
import pytest

from katan import cache, catalog


def film(tmdb_id):
    return {"type": "movie", "ids": {"tmdb": tmdb_id},
            "title": "Film %d" % tmdb_id, "year": 2024}


def films(*ids):
    return [film(i) for i in ids]


@pytest.fixture
def tab(monkeypatch, settings_module):
    """A three-row tab whose contents this test writes directly."""
    settings_module.set("ui.row_items", "12")

    rows = [
        {"id": "first", "title_id": 1, "loader": lambda page: [], "ttl": 60,
         "needs": [], "default": True, "paged": True, "sections": ["movies"]},
        {"id": "second", "title_id": 2, "loader": lambda page: [], "ttl": 60,
         "needs": [], "default": True, "paged": True, "sections": ["movies"]},
        {"id": "third", "title_id": 3, "loader": lambda page: [], "ttl": 60,
         "needs": [], "default": True, "paged": True, "sections": ["movies"]},
    ]
    monkeypatch.setattr(catalog, "enabled_rows",
                        lambda section=None: list(rows))
    monkeypatch.setattr(catalog, "by_id",
                        lambda row_id: next((r for r in rows
                                             if r["id"] == row_id), None))

    def put(row_id, items):
        cache.set(catalog.cache_key(row_id), items, 60)
        catalog.forget_claims()

    catalog.forget_claims()
    return put


def ids_of(entries):
    return [e["ids"]["tmdb"] for e in entries]


def test_a_row_does_not_repeat_what_the_row_above_it_shows(tab):
    tab("first", films(1, 2, 3, 4))
    tab("second", films(3, 4, 5, 6))

    assert ids_of(catalog.peek("second", section="movies")) == [5, 6]


def test_the_row_above_keeps_everything(tab):
    """Priority runs downwards, so the first row is never touched."""
    tab("first", films(1, 2, 3))
    tab("second", films(1, 2, 3))

    assert ids_of(catalog.peek("first", section="movies")) == [1, 2, 3]


def test_removal_is_cumulative_down_the_tab(tab):
    tab("first", films(1, 2))
    tab("second", films(2, 3))
    tab("third", films(1, 2, 3, 4))

    assert ids_of(catalog.peek("third", section="movies")) == [4]


def test_a_plain_listing_is_left_exactly_as_it_was(tab):
    """No tab, no top-to-bottom order, nothing to inherit priority from."""
    tab("first", films(1, 2, 3))
    tab("second", films(1, 2, 3))

    assert ids_of(catalog.peek("second")) == [1, 2, 3]


def test_a_wholly_redundant_row_is_shown_rather_than_emptied(tab):
    """A row every one of whose items appears above it is genuinely
    redundant, and showing it half-empty is a worse answer than showing it as
    it was. Hiding it is a decision for whoever chose the rows."""
    tab("first", films(1, 2, 3))
    tab("second", films(1, 2, 3))

    assert ids_of(catalog.peek("second", section="movies")) == [1, 2, 3]


def test_a_row_above_that_is_not_warmed_yet_claims_nothing(tab):
    """It keeps its own items when it does arrive, because it is the one
    above - so the row below must not be emptied on a guess."""
    tab("second", films(1, 2, 3))

    assert ids_of(catalog.peek("second", section="movies")) == [1, 2, 3]


def test_the_row_is_still_trimmed_to_the_row_length(tab, settings_module):
    """More is cached than is drawn, so that a row losing half its page can
    fill up again from what it already fetched. What reaches the screen is
    still one row's worth."""
    settings_module.set("ui.row_items", "6")
    tab("first", films(*range(100, 110)))
    tab("second", films(*range(1, 21)))

    assert len(catalog.peek("second", section="movies")) == 6


def test_a_row_that_changes_is_noticed(tab):
    """The claims are memoised - eighteen cache reads per row draw is a
    hundred milliseconds of nothing on the device this is written for - so
    the memo has to be dropped when a row's contents change."""
    tab("first", films(1, 2, 3))
    tab("second", films(1, 2, 3, 4))
    assert ids_of(catalog.peek("second", section="movies")) == [4]

    tab("first", films(9))
    assert ids_of(catalog.peek("second", section="movies")) == [1, 2, 3, 4]


def test_invalidating_the_catalog_drops_the_claims(tab):
    tab("first", films(1, 2))
    tab("second", films(1, 2, 3))
    catalog.peek("second", section="movies")

    catalog.invalidate()

    assert ids_of(catalog.peek("second", section="movies") or []) == []

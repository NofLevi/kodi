# -*- coding: utf-8 -*-
"""Which rows a tab shows, and in which order.

Measured before any of this: the Films tab showed **four** rows - continue,
because you watched, trending, Israeli films. Every genre, Top rated and New
releases were defined, labelled, translated and `default=False`, so not one
of them had ever appeared on a screen. The question "are our genres any
good" could not be answered by looking, because they were not there.
"""
from pinky import catalog

MOVIES_WANTED = [
    "continue", "because_you_watched",
    "trending_movies", "movies_new", "movies_blockbusters",
    "top_rated_movies", "israeli_movies",
    "movies_action", "movies_comedy", "movies_drama", "movies_thriller",
    "movies_scifi", "movies_horror", "movies_animation", "movies_documentary",
    "movies_nineties", "movies_eighties",
]

SHOWS_WANTED = [
    "continue", "because_you_watched",
    "trending_shows", "shows_new", "airing_today", "top_rated_shows",
    "israeli_shows", "anime_trending", "foreign_dramas",
    "shows_drama", "shows_comedy", "shows_crime", "shows_scifi",
    "shows_documentary", "shows_reality",
]


def _shown(section):
    on = {row["id"] for row in catalog.rows() if row.get("default", True)}
    members = {row["id"] for row in catalog.rows()
               if section in (row.get("sections") or ())}
    order = catalog.SECTION_ORDER.get(section, [])
    shown = [r for r in order if r in members and r in on]
    shown += [r for r in sorted(members) if r not in order and r in on]
    return shown


def test_the_films_tab_is_what_was_asked_for():
    assert _shown(catalog.MOVIES) == MOVIES_WANTED


def test_the_series_tab_is_what_was_asked_for():
    assert _shown(catalog.SHOWS) == SHOWS_WANTED


def test_the_decades_come_last():
    """Browsing by year is a mood somebody arrives at, not one they open
    the screen in."""
    shown = _shown(catalog.MOVIES)
    assert shown[-2:] == ["movies_nineties", "movies_eighties"]


def test_every_genre_row_has_a_vote_floor():
    """Without one a genre row is not trending, it is noisy: TMDB popularity
    decays with time, so it already reads as current, but an obscure title
    with a spike of activity outranks what people are actually watching."""
    assert catalog.GENRE_VOTES["movie"] >= 100
    assert catalog.GENRE_VOTES["tv"] >= 20


def test_the_genre_query_asks_for_current_and_well_rated(monkeypatch):
    asked = {}

    class FakeTmdb(object):
        @staticmethod
        def discover(media_type, page=1, **filters):
            asked.update(filters, media_type=media_type)
            return []

    monkeypatch.setattr(catalog, "_tmdb", lambda: FakeTmdb)
    catalog._by_genre("movie", 28, 1)

    assert asked["sort_by"] == "popularity.desc"
    assert asked["vote_count.gte"] == catalog.GENRE_VOTES["movie"]
    assert asked["with_genres"] == "28"

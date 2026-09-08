"""The details screen: information, seasons, episodes and the actions."""
import pytest

from katan.ui import details_window


class FakeAction(object):
    def __init__(self, action_id=0):
        self._id = action_id

    def getId(self):
        return self._id

    def getUnicode(self):
        return ""


MOVIE = {
    "type": "movie",
    "ids": {"tmdb": 693134, "imdb": "tt15239678"},
    "title": "Dune: Part Two",
    "year": 2024,
    "plot": "Paul unites with the Fremen.",
    "rating": 8.2,
    "mpaa": "12",
    "duration": 166 * 60,
    "genres": ["Science Fiction", "Adventure"],
    "art": {"poster": "p.jpg", "fanart": "f.jpg"},
    "cast": [{"name": "Timothee Chalamet", "role": "Paul"},
             {"name": "Zendaya", "role": "Chani"}],
    "extra": {"trailer": "plugin://plugin.video.youtube/play/?video_id=abc"},
}

SHOW = {
    "type": "show",
    "ids": {"tmdb": 1399},
    "title": "A Show",
    "year": 2011,
    "plot": "Things happen.",
    "art": {"poster": "p.jpg"},
    "extra": {},
}


@pytest.fixture
def window(monkeypatch):
    """A details window with TMDB replaced by a small fake series."""
    from katan.meta import tmdb, trakt_state

    seasons = [
        {"type": "season", "ids": {"tmdb": 1399}, "title": "Season 1",
         "season": 1, "art": {}, "extra": {"episode_count": 3, "tmdb_show": 1399}},
        {"type": "season", "ids": {"tmdb": 1399}, "title": "Season 2",
         "season": 2, "art": {}, "extra": {"episode_count": 2, "tmdb_show": 1399}},
    ]
    episodes = {
        1: [{"type": "episode", "ids": {}, "title": "One", "season": 1,
             "episode": n, "art": {}, "premiered": "2011-04-%02d" % n,
             "duration": 3000, "playcount": 1 if n == 1 else 0,
             "extra": {"tmdb_show": 1399}} for n in (1, 2, 3)],
        2: [{"type": "episode", "ids": {}, "title": "Two", "season": 2,
             "episode": n, "art": {}, "premiered": "2012-04-%02d" % n,
             "duration": 3000, "playcount": 0,
             "extra": {"tmdb_show": 1399}} for n in (1, 2)],
    }
    monkeypatch.setattr(tmdb, "has_key", lambda: False)
    monkeypatch.setattr(tmdb, "seasons", lambda tmdb_id: list(seasons))
    monkeypatch.setattr(tmdb, "episodes",
                        lambda tmdb_id, season: list(episodes.get(int(season), [])))
    monkeypatch.setattr(trakt_state, "annotate", lambda entries: entries)

    def build(item):
        instance = details_window.DetailsWindow()
        instance.item = item
        instance.onInit()
        return instance

    return build


def test_a_movie_shows_its_information(window):
    detail = window(MOVIE)
    assert detail.getProperty("katan.detail.title") == "Dune: Part Two"
    assert "Paul unites" in detail.getProperty("katan.detail.plot")
    assert detail.getProperty("katan.detail.poster") == "p.jpg"
    assert detail.getProperty("katan.detail.fanart") == "f.jpg"

    meta = detail.getProperty("katan.detail.meta")
    assert "2024" in meta and "8.2" in meta and "166 min" in meta
    assert "Zendaya" in detail.getProperty("katan.detail.cast")


def test_a_movie_has_no_season_list(window):
    detail = window(MOVIE)
    assert detail.getProperty("katan.detail.listheading") == ""


def test_a_show_lists_its_seasons(window):
    detail = window(SHOW)
    assert detail.getProperty("katan.detail.listheading")
    assert len(detail.entries) == 2
    assert detail.entries[0]["type"] == "season"


def test_choosing_a_season_lists_its_episodes(window):
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 1
    detail.onClick(details_window.LIST_CONTENT)
    assert detail.season == 2
    assert len(detail.entries) == 2
    assert all(e["type"] == "episode" for e in detail.entries)


def test_back_returns_to_the_seasons_before_closing(window):
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 0
    detail.onClick(details_window.LIST_CONTENT)
    assert detail.season == 1

    detail.onAction(FakeAction(details_window.ACTION_NAV_BACK))
    assert detail.season is None
    assert detail.closed is False, "the first back should leave the episode list"

    detail.onAction(FakeAction(details_window.ACTION_NAV_BACK))
    assert detail.closed is True


def test_playing_a_show_picks_the_next_unwatched_episode(window):
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 0
    detail.onClick(details_window.LIST_CONTENT)      # season 1

    nxt = detail._next_unwatched()
    assert nxt["episode"] == 2, "episode 1 is already watched"


def test_play_works_on_a_show_without_opening_a_season_first(window):
    """Play is the control that has focus when the window opens.

    It used to fail every first press: the window shows seasons, and this
    looked only for an episode among them, so the viewer had to drill into a
    season before the Play button would do anything - which is the work the
    button exists to save.
    """
    detail = window(SHOW)
    assert all(e.get("type") == "season" for e in detail.entries), \
        "the window should be showing seasons at this point"

    nxt = detail._next_unwatched()
    assert nxt is not None, "Play must work on the first press"
    assert nxt["season"] == 1 and nxt["episode"] == 2


def test_it_walks_on_to_the_next_season_when_one_is_finished(window,
                                                             monkeypatch):
    from katan.meta import tmdb
    watched = [dict(e, playcount=1) for e in
               tmdb.episodes(1399, 1)]
    monkeypatch.setattr(tmdb, "episodes",
                        lambda tmdb_id, season: watched if int(season) == 1
                        else [{"type": "episode", "ids": {}, "title": "Two",
                               "season": 2, "episode": 1, "art": {},
                               "premiered": "2012-04-01", "duration": 3000,
                               "playcount": 0, "extra": {"tmdb_show": 1399}}])
    detail = window(SHOW)
    nxt = detail._next_unwatched()
    assert nxt["season"] == 2, "season one is finished, so move on"


def test_specials_are_not_what_play_next_means(window, monkeypatch):
    """Season zero is extras. "Play the next episode" never means those."""
    detail = window(SHOW)
    detail.entries = [
        {"type": "season", "season": 0, "title": "Specials", "ids": {},
         "art": {}},
        {"type": "season", "season": 1, "title": "Season 1", "ids": {},
         "art": {}},
    ]
    assert detail._season_numbers() == [1, 0]


def test_playing_a_show_with_no_seasons_loaded_says_so(window):
    detail = window(SHOW)
    detail.entries = []
    assert detail._next_unwatched() is None


def test_the_trailer_button_is_hidden_without_one(window):
    assert window(MOVIE).getProperty("katan.detail.trailer")
    plain = dict(MOVIE, extra={})
    assert window(plain).getProperty("katan.detail.trailer") == ""


def test_properties_are_cleared_on_close(window):
    detail = window(MOVIE)
    detail.onAction(FakeAction(details_window.ACTION_NAV_BACK))
    assert detail.getProperty("katan.detail.title") == ""


def test_opening_details_for_nothing_is_safe():
    assert details_window.open_details(None) is False


# --------------------------------------------------------------------------
# the small print under each row
# --------------------------------------------------------------------------


def test_a_season_with_one_episode_reads_as_one(window):
    """Hebrew takes the singular after one. "1 פרקים" is the plural, and Silo
    has exactly such a season."""
    from katan import kodi

    one = details_window._row_subtitle(
        {"type": "season", "season": 4, "extra": {"episode_count": 1}})
    many = details_window._row_subtitle(
        {"type": "season", "season": 1, "extra": {"episode_count": 10}})

    assert one == kodi.localize(32415)
    assert one != many
    assert "10" in many


def test_a_season_with_no_count_says_nothing(window):
    assert details_window._row_subtitle(
        {"type": "season", "season": 1, "extra": {}}) == ""


def test_an_episode_runtime_is_localised(window):
    """The last "min" left in a window that is otherwise entirely Hebrew."""
    from katan import kodi

    line = details_window._row_subtitle(
        {"type": "episode", "premiered": "2023-05-04", "duration": 62 * 60})
    assert "2023-05-04" in line
    assert kodi.localize(32234, 62) in line


# --------------------------------------------------------------------------
# choosing a source for a series
#
# A film has one thing to choose a source for and a show has forty, so
# "choose a source" on a series has to reach a particular episode. Reading the
# list cursor cannot do it: getting from episode nine back up to the button
# walks the selection up with it, so the cursor genuinely is on episode one by
# the time the button is pressed. It asks instead.
# --------------------------------------------------------------------------


def _picker_calls(monkeypatch):
    """Record what the window asked the picker for, instead of running it."""
    calls = []
    monkeypatch.setattr(details_window.DetailsWindow, "_play",
                        lambda self, entry, force_picker=False:
                        calls.append((entry, force_picker)))
    return calls


def _into_season_one(window_factory):
    detail = window_factory(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 0
    detail.onClick(details_window.LIST_CONTENT)          # into season 1
    return detail


def test_choosing_a_source_asks_which_episode(window, monkeypatch):
    """Any episode has to be reachable, including one the cursor is not on."""
    detail = _into_season_one(window)
    asked = {}
    monkeypatch.setattr(details_window.kodi, "select",
                        lambda options, heading=None, **kw:
                        asked.setdefault("options", options) is None or 2)

    calls = _picker_calls(monkeypatch)
    detail.onClick(details_window.BUTTON_SOURCES)

    assert len(asked["options"]) == len(detail.entries)
    assert len(calls) == 1
    entry, force_picker = calls[0]
    assert force_picker is True
    assert entry["episode"] == detail.entries[2]["episode"]


def test_the_question_opens_on_the_episode_being_looked_at(window, monkeypatch):
    """So the obvious way round - move down, come back up, press it - works."""
    detail = _into_season_one(window)
    detail.getControl(details_window.LIST_CONTENT).position = 1
    seen = {}
    monkeypatch.setattr(details_window.kodi, "select",
                        lambda options, heading=None, preselect=-1, **kw:
                        seen.setdefault("preselect", preselect) is None or -1)

    calls = _picker_calls(monkeypatch)
    detail.onClick(details_window.BUTTON_SOURCES)

    assert seen["preselect"] == 1
    assert calls == []              # cancelling the question plays nothing


def test_the_context_menu_opens_the_picker_for_that_episode(window,
                                                            monkeypatch):
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 0
    detail.onClick(details_window.LIST_CONTENT)
    detail.getControl(details_window.LIST_CONTENT).position = 1

    calls = _picker_calls(monkeypatch)
    detail.onAction(FakeAction(details_window.ACTION_CONTEXT_MENU))

    assert len(calls) == 1
    entry, force_picker = calls[0]
    assert force_picker is True
    assert entry["episode"] == detail.entries[1]["episode"]


def test_a_season_is_not_something_a_source_can_be_chosen_for(window,
                                                              monkeypatch):
    """On the season list the selection is a season, so this has to fall back
    to what Play would start rather than offering sources for "Season 2"."""
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 1

    calls = _picker_calls(monkeypatch)
    detail.onClick(details_window.BUTTON_SOURCES)

    assert len(calls) == 1
    entry, force_picker = calls[0]
    assert force_picker is True
    assert entry["type"] == "episode", "a season is not playable"


def test_a_film_still_chooses_a_source_for_itself(window, monkeypatch):
    detail = window(MOVIE)
    calls = _picker_calls(monkeypatch)

    detail.onClick(details_window.BUTTON_SOURCES)

    assert calls == [(detail.item, True)]


def test_play_still_means_the_next_unwatched_episode(window, monkeypatch):
    """Play keeps what it always meant. Only the source picker follows the
    highlight, because that is the one that has to name an episode."""
    detail = window(SHOW)
    detail.getControl(details_window.LIST_CONTENT).position = 0
    detail.onClick(details_window.LIST_CONTENT)
    detail.getControl(details_window.LIST_CONTENT).position = 0   # episode 1

    calls = _picker_calls(monkeypatch)
    detail.onClick(details_window.BUTTON_PLAY)

    assert len(calls) == 1
    entry, force_picker = calls[0]
    assert force_picker is False
    assert entry["episode"] == 2, "episode 1 is already watched"

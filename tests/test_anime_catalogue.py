"""Anime comes from two catalogues, so one outage is not a dead feature.

AniList began refusing every request on 7 September 2026, which emptied the
anime row and made anime search return nothing. Kitsu is the second source,
and it is chosen automatically.
"""
import pytest

from katan.meta import anime, kitsu


# One record in the shape Kitsu actually returns.
RECORD = {
    "id": "46474",
    "type": "anime",
    "attributes": {
        "canonicalTitle": "Sousou no Frieren",
        "titles": {"en": "Frieren: Beyond Journey's End",
                   "en_jp": "Sousou no Frieren",
                   "ja_jp": "葬送のフリーレン"},
        "synopsis": "An elf mage outlives her party.",
        "posterImage": {"medium": "https://media.kitsu.app/poster.jpg"},
        "coverImage": {"original": "https://media.kitsu.app/cover.jpg"},
        "averageRating": "88.83",
        "userCount": 41234,
        "episodeCount": 28,
        "episodeLength": 24,
        "startDate": "2023-09-29",
        "subtype": "TV",
        "status": "finished",
        "ageRating": "PG",
    },
}


# --------------------------------------------------------------------------
# converting what Kitsu returns
# --------------------------------------------------------------------------


def test_a_record_becomes_a_normalised_item():
    item = kitsu.to_item(RECORD)
    assert item["type"] == "show"
    assert item["title"] == "Frieren: Beyond Journey's End"
    assert item["year"] == 2023
    assert item["plot"].startswith("An elf mage")
    assert item["art"]["poster"].endswith("poster.jpg")
    assert item["art"]["fanart"].endswith("cover.jpg")
    assert item["extra"]["anime"] is True
    assert item["extra"]["episodes"] == 28


def test_the_kitsu_id_is_stored_as_an_id_not_buried():
    """Torrentio's anime endpoint speaks kitsu ids, so it has to be reachable."""
    item = kitsu.to_item(RECORD)
    assert item["ids"]["kitsu"] == 46474
    assert isinstance(item["ids"]["kitsu"], int)


def test_the_kitsu_id_reaches_the_source_layer():
    from katan.sources.providers import stremio

    item = kitsu.to_item(RECORD)
    kind, identifier = stremio.stream_id(
        {"type": "episode", "ids": item["ids"], "season": 1, "episode": 3})
    assert (kind, identifier) == ("series", "kitsu:46474:3")


def test_ratings_are_converted_from_percent_to_ten():
    """Kitsu rates out of 100 as a string; everything else here is out of 10."""
    assert kitsu.to_item(RECORD)["rating"] == 8.9


def test_a_missing_rating_is_zero_not_a_crash():
    record = {"id": "1", "attributes": dict(RECORD["attributes"],
                                            averageRating=None)}
    assert kitsu.to_item(record)["rating"] == 0.0


def test_films_are_typed_as_movies():
    record = {"id": "9", "attributes": dict(RECORD["attributes"],
                                            subtype="movie")}
    assert kitsu.to_item(record)["type"] == "movie"
    assert kitsu.to_item(RECORD)["type"] == "show"


def test_an_english_title_is_preferred_over_the_romaji():
    item = kitsu.to_item(RECORD)
    assert item["title"] == "Frieren: Beyond Journey's End"
    assert item["original_title"] == "Sousou no Frieren"


def test_a_record_with_no_usable_title_is_dropped():
    record = {"id": "1", "attributes": {"titles": {}, "canonicalTitle": ""}}
    assert kitsu.to_item(record) is None


def test_junk_is_handled_rather_than_raising():
    for junk in (None, {}, {"id": None}, {"id": "abc", "attributes": {}}):
        assert kitsu.to_item(junk) is None


# --------------------------------------------------------------------------
# choosing between the two
# --------------------------------------------------------------------------


class FakeCatalogue(object):
    def __init__(self, name, results=None, fails=False):
        self.__name__ = "katan.meta." + name
        self.results = results if results is not None else [{"title": name}]
        self.fails = fails
        self.calls = 0

    def trending(self, limit=20, page=1):
        self.calls += 1
        if self.fails:
            raise RuntimeError("service is down")
        return list(self.results)

    def search(self, query, limit=20, page=1):
        self.calls += 1
        if self.fails:
            raise RuntimeError("service is down")
        return list(self.results)


@pytest.fixture(autouse=True)
def fresh_decision():
    anime.reset()
    yield
    anime.reset()


def install(monkeypatch, anilist, kitsu_module):
    monkeypatch.setattr(anime, "_anilist", lambda: anilist)
    monkeypatch.setattr(anime, "_kitsu", lambda: kitsu_module)


def test_anilist_is_preferred_while_it_answers(monkeypatch):
    """It carries the AniList id that SeaDex rankings are keyed on."""
    alive = FakeCatalogue("anilist")
    spare = FakeCatalogue("kitsu")
    install(monkeypatch, alive, spare)

    assert anime.trending()[0]["title"] == "anilist"
    assert anime.current() == "anilist"
    assert spare.calls == 0


def test_kitsu_takes_over_when_anilist_refuses(monkeypatch):
    dead = FakeCatalogue("anilist", fails=True)
    spare = FakeCatalogue("kitsu")
    install(monkeypatch, dead, spare)

    assert anime.trending()[0]["title"] == "kitsu"
    assert anime.current() == "kitsu"


def test_an_empty_answer_counts_as_being_down(monkeypatch):
    """A service returning nothing is as useless as one returning an error."""
    empty = FakeCatalogue("anilist", results=[])
    spare = FakeCatalogue("kitsu")
    install(monkeypatch, empty, spare)
    assert anime.trending()[0]["title"] == "kitsu"


def test_the_decision_is_remembered_rather_than_remade(monkeypatch):
    """Otherwise every row pays for a failing request to the dead service."""
    dead = FakeCatalogue("anilist", fails=True)
    spare = FakeCatalogue("kitsu")
    install(monkeypatch, dead, spare)

    anime.trending()
    calls_after_first = dead.calls
    anime.trending()
    anime.search("x")

    assert dead.calls == calls_after_first, "the dead service was retried"


def test_a_service_that_dies_mid_session_falls_through(monkeypatch):
    alive = FakeCatalogue("anilist")
    spare = FakeCatalogue("kitsu")
    install(monkeypatch, alive, spare)

    assert anime.trending()[0]["title"] == "anilist"
    alive.fails = True
    assert anime.trending()[0]["title"] == "kitsu", "it should fall through"


def test_both_down_returns_nothing_rather_than_raising(monkeypatch):
    install(monkeypatch, FakeCatalogue("anilist", fails=True),
            FakeCatalogue("kitsu", fails=True))
    assert anime.trending() == []
    assert anime.search("anything") == []


def test_an_empty_query_never_reaches_a_catalogue(monkeypatch):
    alive = FakeCatalogue("anilist")
    install(monkeypatch, alive, FakeCatalogue("kitsu"))
    assert anime.search("") == []
    assert alive.calls == 0

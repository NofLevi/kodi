"""The anime catalog, and what it does when the service will not answer.

AniList began refusing every request on 7 September 2026 - 403 with "The
AniList API has been temporarily disabled due to severe stability issues", to
any User-Agent including a browser one. So the interesting behaviour here is
not the happy path, it is that an outage upstream produces a hidden row and a
log line rather than a broken screen.
"""
import pytest

from katan import http
from katan.meta import anilist

MEDIA = {
    "id": 21,
    "idMal": 21,
    "title": {"english": "One Piece", "romaji": "One Piece", "native": ""},
    "description": "A boy sets out to <b>sea</b>.",
    "coverImage": {"large": "https://img/one-piece.jpg"},
    "bannerImage": "https://img/one-piece-banner.jpg",
    "averageScore": 88,
    "popularity": 500000,
    "episodes": 1100,
    "duration": 24,
    "genres": ["Action", "Adventure"],
    "format": "TV",
    "status": "RELEASING",
    "season": "FALL",
    "seasonYear": 1999,
    "startDate": {"year": 1999, "month": 10, "day": 20},
}


class FakeResponse(object):
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def answers(monkeypatch):
    """Reply to the one POST anilist makes, and record that it was made."""
    calls = []

    def install(response):
        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return response
        monkeypatch.setattr(http, "post", fake_post)
        return calls

    return install


# --------------------------------------------------------------------------
# reading a normal answer
# --------------------------------------------------------------------------


def test_a_media_node_becomes_an_ordinary_item(answers):
    answers(FakeResponse(200, {"data": {"Page": {"media": [MEDIA]}}}))
    found = anilist.trending(limit=5)

    assert len(found) == 1
    show = found[0]
    assert show["type"] == "show"
    assert show["title"] == "One Piece"
    assert show["ids"]["anilist"] == 21
    assert show["extra"]["anime"] is True, \
        "the aggregator uses this to decide whether to run the anime providers"
    assert show["rating"] == 8.8, "AniList scores out of 100, Kodi out of 10"
    assert "<b>" not in show["plot"]


def test_a_film_is_a_movie_not_a_show(answers):
    answers(FakeResponse(200, {"data": {"Page": {"media": [
        dict(MEDIA, format="MOVIE")]}}}))
    assert anilist.trending(limit=5)[0]["type"] == "movie"


def test_a_node_with_no_id_is_dropped(answers):
    answers(FakeResponse(200, {"data": {"Page": {"media": [
        MEDIA, {"title": {"english": "nothing"}}]}}}))
    assert len(anilist.trending(limit=5)) == 1


# --------------------------------------------------------------------------
# the outage
# --------------------------------------------------------------------------


def test_a_refused_request_is_an_empty_row_not_an_exception(answers):
    """This is the live situation, not a hypothetical."""
    answers(FakeResponse(403, {"errors": [{
        "message": "The AniList API has been temporarily disabled due to "
                   "severe stability issues.", "status": 403}]}))
    assert anilist.trending(limit=5) == []
    assert anilist.seasonal(limit=5) == []
    assert anilist.search("naruto", limit=5) == []
    assert anilist.details(21) is None


def test_the_refusal_is_logged_in_the_services_own_words(answers, monkeypatch):
    """"HTTP 403" alone would look like a bug at this end."""
    logged = []
    monkeypatch.setattr(anilist.kodi, "log",
                        lambda message, *a, **k: logged.append(message))
    answers(FakeResponse(403, {"errors": [{
        "message": "The AniList API has been temporarily disabled due to "
                   "severe stability issues.", "status": 403}]}))
    anilist.trending(limit=5)

    assert any("temporarily disabled" in line for line in logged), logged


def test_a_refusal_that_explains_nothing_still_logs_the_code(answers,
                                                             monkeypatch):
    logged = []
    monkeypatch.setattr(anilist.kodi, "log",
                        lambda message, *a, **k: logged.append(message))
    answers(FakeResponse(503, None))
    assert anilist.trending(limit=5) == []
    assert any("503" in line for line in logged), logged


def test_no_response_at_all_is_survivable(answers):
    answers(None)
    assert anilist.trending(limit=5) == []


def test_a_failure_is_not_cached_as_a_result(answers, monkeypatch):
    """An outage must not become a row that stays empty for three hours."""
    calls = answers(FakeResponse(403, {"errors": []}))
    anilist.trending(limit=5)
    anilist.trending(limit=5)
    assert len(calls) == 2, "a refusal should be retried, not remembered"


def test_a_success_is_cached(answers):
    calls = answers(FakeResponse(200, {"data": {"Page": {"media": [MEDIA]}}}))
    anilist.trending(limit=5)
    anilist.trending(limit=5)
    assert len(calls) == 1


def test_an_empty_search_never_asks(answers):
    calls = answers(FakeResponse(200, {"data": {"Page": {"media": []}}}))
    assert anilist.search("", limit=5) == []
    assert not calls

"""The OpenSubtitles that needs no account, which is the only usable one.

`opensubtitles.com` wants a registered consumer and gives five downloads a day
free. This add-on fetches one subtitle per playback, so that is five programmes
a day for a household, and the next tier is $20 a month.

`rest.opensubtitles.org` is the older search API and still answers
anonymously - measured against it live, not against a belief about it. What is
pinned here is the shape of the request and the two things that had already
gone wrong once each.
"""
import gzip
import io
import json

import pytest


@pytest.fixture
def provider():
    from katan.subs.providers import opensubtitles_rest
    return opensubtitles_rest


def _asked(monkeypatch, provider, payload=None):
    """Capture the URL built, and answer with whatever the test wants."""
    seen = {}

    def get_json(url, default=None, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return payload if payload is not None else []

    monkeypatch.setattr(provider.http, "get_json", get_json)
    return seen


def test_a_title_query_is_lowercased(monkeypatch, provider):
    """One capital letter turned every search into a failure.

    A capitalised query answers 302 to a redirect this add-on cannot follow,
    and urllib reports that as `getaddrinfo failed` - which reads like the
    network being down rather than like the one character that caused it.
    Measured: "Silo" 302, "silo" 200; "Mousetrap" 302, "mousetrap" 200 with
    five results.
    """
    seen = _asked(monkeypatch, provider)
    provider.search({"type": "episode", "title": "Mousetrap", "season": 1,
                     "episode": 2, "ids": {}}, None, ["en"])
    assert "query-mousetrap" in seen["url"], seen["url"]
    assert "Mousetrap" not in seen["url"]


def test_an_imdb_id_is_preferred_to_a_title(monkeypatch, provider):
    """Far more precise, and it sidesteps the redirect entirely."""
    seen = _asked(monkeypatch, provider)
    provider.search({"type": "episode", "title": "Silo", "season": 1,
                     "episode": 1, "ids": {"imdb": "tt14688458"}}, None, ["he"])
    assert "imdbid-14688458" in seen["url"]
    assert "query-" not in seen["url"]


def test_the_episode_and_season_reach_the_path(monkeypatch, provider):
    seen = _asked(monkeypatch, provider)
    provider.search({"type": "episode", "title": "silo", "season": 3,
                     "episode": 7, "ids": {}}, None, ["en"])
    assert "season-3" in seen["url"] and "episode-7" in seen["url"]


def test_a_film_asks_for_no_season(monkeypatch, provider):
    seen = _asked(monkeypatch, provider)
    provider.search({"type": "movie", "title": "fight club", "ids": {}},
                    None, ["en"])
    assert "season-" not in seen["url"] and "episode-" not in seen["url"]


def test_two_letter_languages_become_three(monkeypatch, provider):
    """The API speaks ISO 639-2; everything else here speaks two letters."""
    seen = _asked(monkeypatch, provider)
    provider.search({"type": "movie", "title": "x", "ids": {}}, None, ["he"])
    assert "sublanguageid-heb" in seen["url"]

    assert provider.supports("he") and provider.supports("en")
    assert not provider.supports("kl")


def test_a_language_it_does_not_know_is_skipped(monkeypatch, provider):
    seen = _asked(monkeypatch, provider)
    found = provider.search({"type": "movie", "title": "x", "ids": {}},
                            None, ["kl"])
    assert found == [] and "url" not in seen


def test_results_become_candidates(monkeypatch, provider):
    rows = [{"MovieReleaseName": "Silo.S01E01.1080p-PSA",
             "SubDownloadLink": "https://dl/1", "SubDownloadsCnt": "284908",
             "MatchedBy": "moviehash"},
            {"SubFileName": "other.srt", "SubDownloadLink": "https://dl/2",
             "SubDownloadsCnt": "nonsense", "MatchedBy": "fulltext"},
            {"MovieReleaseName": "no link here"}]
    _asked(monkeypatch, provider, rows)
    found = provider.search({"type": "movie", "title": "x", "ids": {}},
                            None, ["he"])
    assert len(found) == 2, "the entry with no download link must be dropped"
    assert found[0]["release"] == "Silo.S01E01.1080p-PSA"
    assert found[0]["downloads"] == 284908
    assert found[0]["hash_match"] is True
    # A count that is not a number must not take the search down with it.
    assert found[1]["downloads"] == 0
    assert found[1]["hash_match"] is False


def test_the_service_not_answering_is_not_a_crash(monkeypatch, provider):
    _asked(monkeypatch, provider, None)
    monkeypatch.setattr(provider.http, "get_json",
                        lambda url, default=None, **kw: None)
    assert provider.search({"type": "movie", "title": "x", "ids": {}},
                           None, ["he"]) == []


def test_a_download_is_gunzipped(monkeypatch, provider):
    """This API serves .gz where Wizdom serves a zip."""
    body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nhello\r\n"
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as handle:
        handle.write(body)
    packed = buffer.getvalue()

    monkeypatch.setattr(provider.common, "fetch_bytes",
                        lambda url, **kw: packed)
    assert provider.download({"download": "https://dl/1",
                              "language": "he"}) == body


def test_something_that_is_not_gzip_does_not_raise(monkeypatch, provider):
    """An error page where a subtitle was expected is a normal Tuesday."""
    monkeypatch.setattr(provider.common, "fetch_bytes",
                        lambda url, **kw: b"\x1f\x8bnot really gzip")
    assert provider.download({"download": "https://dl/1", "language": "he"}) == b""


def test_nothing_downloaded_is_empty_rather_than_none(monkeypatch, provider):
    monkeypatch.setattr(provider.common, "fetch_bytes", lambda url, **kw: b"")
    assert provider.download({"download": "https://dl/1", "language": "he"}) == b""


def test_it_is_asked_by_the_pipeline_and_ships_on(settings_module):
    """It needs no account, so there is no reason for it to be off.

    A provider that is enabled and silently empty is the thing this project
    bans for source providers, and `subs.provider.opensubtitles` is exactly
    that today: on, with no key, contributing nothing.
    """
    from katan.subs import auto

    assert settings_module.get_bool("subs.provider.opensubtitles_rest")
    assert "opensubtitles_rest" in auto._modules()
    names = [name for name, _module in auto._providers()]
    assert "opensubtitles_rest" in names
    # Wizdom first: Hebrew-only and fast. This one before the keyed service.
    assert names.index("wizdom") < names.index("opensubtitles_rest")

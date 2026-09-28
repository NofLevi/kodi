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

import pytest


@pytest.fixture
def provider():
    from pinky.subs.providers import opensubtitles_rest
    return opensubtitles_rest


def _asked(monkeypatch, provider, payload=None):
    """Capture the URL built, and answer with whatever the test wants.

    The default answer is one row rather than none, because an empty id
    query is now followed by a name query - so a test that asks "what URL
    was built" would otherwise be shown the fallback's.
    """
    seen = {}
    if payload is None:
        payload = [{"SubFileName": "x.srt", "SubDownloadLink": "https://x/1.gz",
                    "SubLanguageID": "eng"}]

    def get_json(url, default=None, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return list(payload)

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
    assert "query-" not in seen["url"], "the id is asked first, on its own"


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


# --------------------------------------------------------------------------
# anime is filed under more than one numbering
# --------------------------------------------------------------------------


def _asked_every(monkeypatch, module, answer=None):
    """Every URL or params dict a provider asked with, in order.

    Answers with one row by default. An empty answer is not neutral any
    more: an id query that finds nothing is followed by a name query, so a
    test about *numbering* has to say the id query worked or it measures the
    fallback instead.
    """
    seen = []
    if answer is None:
        answer = [{"SubFileName": "x.srt", "SubDownloadLink": "https://x/1.gz",
                   "SubLanguageID": "heb"}]

    def get_json(url, default=None, params=None, **kwargs):
        seen.append(params if params is not None else url)
        return list(answer)

    monkeypatch.setattr(module.http, "get_json", get_json)
    return seen


ANIME = {"type": "episode", "title": "reborn", "season": 8, "episode": 14,
         "absolute": 149, "ids": {"imdb": "tt0993038"}}


def test_an_anime_episode_is_asked_for_by_its_absolute_number_too(
        monkeypatch, provider):
    """TMDB numbers Reborn's episode 149 as season 8 episode 14. OpenSubtitles
    files it the way IMDb does, from the first episode. Asking one numbering
    found nothing, or found episode 14 - which is how anime came to get no
    subtitle on 48% of the titles surveyed."""
    seen = _asked_every(monkeypatch, provider)
    provider.search(ANIME, None, ["he"])
    assert any("season-8" in url and "episode-14" in url for url in seen), seen
    assert any("season-1" in url and "episode-149" in url for url in seen), seen


def test_an_ordinary_episode_is_asked_for_once(monkeypatch, provider):
    """No absolute number means not anime, and costs nothing extra."""
    seen = _asked_every(monkeypatch, provider)
    provider.search({"type": "episode", "title": "silo", "season": 2,
                     "episode": 3, "ids": {"imdb": "tt14688458"}}, None, ["he"])
    assert len(seen) == 1, seen


def test_an_absolute_number_that_is_the_same_asks_once(monkeypatch, provider):
    """A first season counted from one is already absolute: asking twice for
    the same thing is a request spent on nothing."""
    seen = _asked_every(monkeypatch, provider)
    provider.search(dict(ANIME, season=1, episode=30, absolute=30), None, ["he"])
    assert len(seen) == 1, seen


def test_the_numberings_are_ordered_most_likely_first():
    from pinky.subs.providers import common

    assert common.episode_numberings(ANIME) == [(8, 14), (1, 149)]
    assert common.episode_numberings({"type": "movie"}) == []
    assert common.episode_numberings(
        {"type": "episode", "season": 2, "episode": 3}) == [(2, 3)]


def test_wizdom_asks_for_both_numberings_as_well(monkeypatch):
    """Hebrew-only, and asked by IMDb id and number the same way - so the same
    numbering problem, and the same fix."""
    from pinky.subs.providers import wizdom

    seen = _asked_every(monkeypatch, wizdom)
    wizdom.search(ANIME, None, ["he"])
    asked = [(params.get("season"), params.get("episode")) for params in seen]
    assert asked == [(8, 14), (1, 149)], asked


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
             "UserNickName": "uploader-a", "MatchedBy": "moviehash"},
            {"SubFileName": "other.srt", "SubDownloadLink": "https://dl/2",
             "SubDownloadsCnt": "nonsense", "MatchedBy": "fulltext"},
            {"MovieReleaseName": "no link here"}]
    _asked(monkeypatch, provider, rows)
    found = provider.search({"type": "movie", "title": "x",
                             "ids": {"imdb": "tt0137523"}}, None, ["he"])
    assert len(found) == 2, "the entry with no download link must be dropped"
    assert found[0]["release"] == "Silo.S01E01.1080p-PSA"
    assert found[0]["downloads"] == 284908
    assert found[0]["uploader"] == "uploader-a"
    # These rows carry no id of their own, so nothing can be verified and
    # nothing is claimed - `moviehash` alone is not enough, because the index
    # has one file's hash filed under three different programmes.
    assert found[0]["hash_match"] is False
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
    from pinky.subs import auto

    assert settings_module.get_bool("subs.provider.opensubtitles_rest")
    assert "opensubtitles_rest" in auto._modules()
    names = [name for name, _module in auto._providers()]
    assert "opensubtitles_rest" in names
    # Wizdom first: Hebrew-only and fast. This one before the keyed service.
    assert names.index("wizdom") < names.index("opensubtitles_rest")


# --------------------------------------------------------------------------
# BSPlayer, which answers only to a file hash
# --------------------------------------------------------------------------

BSPLAYER_LOGIN = (
    '<?xml version="1.0"?><SOAP-ENV:Envelope><SOAP-ENV:Body>'
    '<ns1:logInResponse><return xsi:type="ns1:SubtitlesResult">'
    '<result xsi:type="xsd:string">200</result>'
    '<status xsi:type="xsd:string">OK</status>'
    '<data xsi:type="xsd:string">TOKEN123</data>'
    "</return></ns1:logInResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>")

BSPLAYER_SEARCH = (
    '<?xml version="1.0"?><SOAP-ENV:Envelope><SOAP-ENV:Body>'
    '<ns1:searchSubtitlesResponse><return>'
    '<status xsi:type="xsd:string">OK</status><data>'
    '<item><subID xsi:type="xsd:string">1</subID>'
    '<subLang xsi:type="xsd:string">heb</subLang>'
    '<subName xsi:type="xsd:string">Game.of.Thrones.S04E01.srt</subName>'
    '<subDownloadsCnt xsi:type="xsd:string">910</subDownloadsCnt>'
    '<subDownloadLink xsi:type="xsd:string">https://dl/1.gz</subDownloadLink>'
    "</item>"
    '<item><subLang xsi:type="xsd:string">eng</subLang>'
    '<subName xsi:type="xsd:string">english.srt</subName>'
    '<subDownloadsCnt xsi:type="xsd:string">x</subDownloadsCnt>'
    '<subDownloadLink xsi:type="xsd:string">https://dl/2.gz</subDownloadLink>'
    "</item>"
    '<item><subLang xsi:type="xsd:string">heb</subLang>'
    '<subName xsi:type="xsd:string">no link</subName></item>'
    "</data></return></ns1:searchSubtitlesResponse>"
    "</SOAP-ENV:Body></SOAP-ENV:Envelope>")


class _Reply(object):
    status_code = 200

    def __init__(self, text):
        self.text = text


@pytest.fixture
def bsplayer():
    from pinky.subs.providers import bsplayer as module
    return module


def test_no_hash_means_no_request_at_all(monkeypatch, bsplayer):
    """An empty hash is an HTTP 500 here, not an empty result.

    So there is nothing to learn by asking, and asking costs a round trip on
    every playback where the hash could not be computed.
    """
    called = []
    monkeypatch.setattr(bsplayer.http, "post",
                        lambda *a, **k: called.append(a) or _Reply(""))
    assert bsplayer.search({}, None, ["he"], "", 0) == []
    assert bsplayer.search({}, None, ["he"], "abc", 0) == []
    assert bsplayer.search({}, None, ["he"], "", 123) == []
    assert not called, "it asked anyway"


def test_a_hash_search_becomes_candidates(monkeypatch, bsplayer):
    replies = [_Reply(BSPLAYER_LOGIN), _Reply(BSPLAYER_SEARCH)]
    sent = []

    def post(url, **kwargs):
        sent.append(kwargs.get("data", b"").decode("utf-8"))
        return replies.pop(0)

    monkeypatch.setattr(bsplayer.http, "post", post)
    found = bsplayer.search({}, None, ["he", "en"], "46e33be00464c12e",
                            1929150976)

    assert "46e33be00464c12e" in sent[1]
    assert "1929150976" in sent[1]
    assert "heb,eng" in sent[1]
    assert "TOKEN123" in sent[1], "the session token was not carried over"

    assert len(found) == 2, "the item with no download link must be dropped"
    assert found[0]["language"] == "he"
    assert found[0]["downloads"] == 910
    # Everything here matched the file itself, which is the whole point.
    assert all(c["hash_match"] for c in found)
    assert found[1]["downloads"] == 0, "an unparseable count must not raise"


def test_the_response_tags_carry_attributes(bsplayer):
    """`<data>` matches nothing here; `<data[^>]*>` does.

    Every tag comes back as `<data xsi:type="xsd:string">`, and a pattern
    written without that reads every field as empty - which looks exactly like
    the service returning nothing.
    """
    assert bsplayer._field("data", BSPLAYER_LOGIN) == "TOKEN123"
    assert bsplayer._field("status", BSPLAYER_LOGIN) == "OK"


def test_no_session_is_not_a_crash(monkeypatch, bsplayer):
    monkeypatch.setattr(bsplayer.http, "post", lambda *a, **k: _Reply(""))
    assert bsplayer.search({}, None, ["he"], "abc", 123) == []


def test_it_is_registered_and_gets_the_size(settings_module):
    """The size is the half that is easy to drop on the floor.

    bsplayer ships off since its hosts stopped answering, so this switches it
    on: what is being checked is the wiring, which has to keep working for
    whenever the service comes back.
    """
    from pinky.subs import auto

    settings_module.set("subs.provider.bsplayer", "true")
    assert "bsplayer" in auto._modules()
    assert "bsplayer" in [name for name, _m in auto._providers()]


# --------------------------------------------------------------------------
# whose file is this hash filed under?
# --------------------------------------------------------------------------

def _hash_rows():
    """The real answer for one Breaking Bad S01E01 hash, trimmed."""
    return [
        {"MovieReleaseName": "The Vampire Diaries S01E01", "MatchedBy": "moviehash",
         "SubDownloadLink": "https://dl/vd", "SeriesIMDBParent": "1405406",
         "IDMovieImdb": "1486497"},
        {"MovieReleaseName": "Kabhi Alvida Naa Kehna", "MatchedBy": "moviehash",
         "SubDownloadLink": "https://dl/ka", "SeriesIMDBParent": "0",
         "IDMovieImdb": "449999"},
        {"MovieReleaseName": "Breaking.Bad.S01E01.720p.HDTV-BiA",
         "MatchedBy": "moviehash", "SubDownloadLink": "https://dl/bb",
         "SeriesIMDBParent": "903747", "IDMovieImdb": "959621"},
    ]


def test_another_programme_filed_under_our_hash_is_dropped(monkeypatch,
                                                           provider):
    """Uploaders mis-register hashes, and the index keeps every claim.

    Without this the first row wins at 100 - above every other kind of
    evidence - and the viewer gets The Vampire Diaries over Breaking Bad.
    """
    _asked(monkeypatch, provider, _hash_rows())
    meta = {"type": "episode", "title": "Breaking Bad", "season": 1,
            "episode": 1, "ids": {"imdb": "tt0903747"}}
    found = provider.search(meta, None, ["en"])
    names = [c["release"] for c in found]
    assert not any("Vampire" in n for n in names), names
    assert any("Breaking.Bad" in n for n in names), names


def test_title_identity_alone_cannot_claim_a_hash_match(monkeypatch, provider):
    """IMDb agreement is necessary but not our hash request and exact size."""
    _asked(monkeypatch, provider, _hash_rows())
    meta = {"type": "episode", "title": "Breaking Bad", "season": 1,
            "episode": 1, "ids": {"imdb": "tt0903747"}}
    found = provider.search(meta, None, ["en"])
    assert found
    assert not any(candidate["hash_match"] for candidate in found)


def test_title_query_never_claims_our_file_hash(monkeypatch, provider):
    rows = [{"MovieReleaseName": "Breaking.Bad.S01E01.720p.HDTV-BiA",
             "MatchedBy": "moviehash", "SubDownloadLink": "https://dl/bb",
             "SeriesIMDBParent": "903747", "MovieByteSize": "478600575"}]
    _asked(monkeypatch, provider, rows)
    meta = {"type": "episode", "title": "Breaking Bad", "season": 1,
            "episode": 1, "ids": {"imdb": "tt0903747"}}

    found = provider.search(meta, None, ["en"])

    assert found and found[0]["hash_match"] is False


def test_hash_claim_requires_exact_file_size(monkeypatch, provider):
    rows = [
        {"MovieReleaseName": "exact", "MatchedBy": "moviehash",
         "SubDownloadLink": "https://dl/exact", "IDMovieImdb": "137523",
         "MovieByteSize": "478600575"},
        {"MovieReleaseName": "wrong-size", "MatchedBy": "moviehash",
         "SubDownloadLink": "https://dl/wrong", "IDMovieImdb": "137523",
         "MovieByteSize": "478600576"},
    ]

    def get_json(url, default=None, **kwargs):
        return rows if "moviehash-" in url else []

    monkeypatch.setattr(provider.http, "get_json", get_json)
    meta = {"type": "movie", "title": "Fight Club",
            "ids": {"imdb": "tt0137523"}}
    found = provider.search(meta, None, ["en"], "abc", 478600575)

    claims = {candidate["release"]: candidate["hash_match"]
              for candidate in found}
    assert claims == {"exact": True, "wrong-size": False}


def test_with_no_id_of_our_own_nothing_is_claimed(monkeypatch, provider):
    """Unverifiable is not the same as verified. 100 is too high for a maybe."""
    _asked(monkeypatch, provider, _hash_rows())
    meta = {"type": "episode", "title": "Breaking Bad", "season": 1,
            "episode": 1, "ids": {}}
    found = provider.search(meta, None, ["en"])
    assert len(found) == 3, "nothing can be excluded without an id either"
    assert not any(c["hash_match"] for c in found)


def test_a_hash_is_asked_about_as_well_as_a_title(monkeypatch, provider):
    """Two different questions, and the hash one is the valuable one."""
    urls = []

    def get_json(url, default=None, **kwargs):
        urls.append(url)
        return []

    monkeypatch.setattr(provider.http, "get_json", get_json)
    provider.search({"type": "episode", "title": "silo", "season": 1,
                     "episode": 1, "ids": {}}, None, ["en"],
                    "ff73982902e896f2", 478600575)
    assert any("moviehash-ff73982902e896f2" in u for u in urls), urls
    assert any("moviebytesize-478600575" in u for u in urls), urls
    assert any("query-silo" in u for u in urls), urls


# --------------------------------------------------------------------------
# an id that finds nothing is not the same as there being nothing
# --------------------------------------------------------------------------


def _row(name="A subtitle", parent="99999999"):
    return {"SubFileName": name, "SubDownloadLink": "https://x/1.gz",
            "SeriesIMDBParent": parent, "SubLanguageID": "eng",
            "MovieReleaseName": name}


def _asked_many(monkeypatch, provider, answers):
    """Answer each URL in turn, recording every one that was asked."""
    urls = []

    def get_json(url, default=None, **kwargs):
        urls.append(url)
        return answers.pop(0) if answers else []

    monkeypatch.setattr(provider.http, "get_json", get_json)
    return urls


def test_an_empty_id_query_falls_back_to_the_name(monkeypatch, provider):
    """Measured on Hikaru no Go 2x02, which is S01E32 to everybody but TMDB.

    `imdbid-0426711` knows English for episodes 1 to 30 and stops, while the
    same season and episode asked by name returns "Hikaru No Go S01E32.en.vtt".
    An upload is filed against an id only if whoever uploaded it said so.
    """
    urls = _asked_many(monkeypatch, provider, [[], [_row()]])
    found = provider.search({"type": "episode", "show_title": "Hikaru no Go",
                             "title": "Team Formed!", "season": 1, "episode": 32,
                             "ids": {"imdb": "tt0426711"}}, None, ["en"])
    assert len(urls) == 2
    assert "imdbid-0426711" in urls[0]
    assert "query-hikaru%20no%20go" in urls[1]
    assert found, "the name query's answer must be kept"


def test_the_name_query_costs_nothing_when_the_id_answers(monkeypatch, provider):
    urls = _asked_many(monkeypatch, provider, [[_row(parent="426711")]])
    provider.search({"type": "episode", "show_title": "Silo", "title": "Freedom Day",
                     "season": 1, "episode": 1,
                     "ids": {"imdb": "tt426711"}}, None, ["en"])
    assert len(urls) == 1, "one request in the common case"


def test_a_name_query_is_not_thrown_away_for_disagreeing_with_the_id(
        monkeypatch, provider):
    """One show can have several IMDb entries and uploaders pick one.

    Hikaru no Go has at least 0426711, 0303461 and 13364846, and the only
    English subtitle for episode 32 is under the third. Having gone around
    the id because it found nothing, discarding the answer for disagreeing
    with that same id is the search cancelling itself out.
    """
    _asked_many(monkeypatch, provider, [[], [_row(parent="13364846")]])
    found = provider.search({"type": "episode", "show_title": "Hikaru no Go",
                             "title": "x", "season": 1, "episode": 32,
                             "ids": {"imdb": "tt0426711"}}, None, ["en"])
    assert found, "a different IMDb entry for the same show is not another show"


def test_a_series_is_asked_for_under_the_show_name(monkeypatch, provider):
    """"The Last Day of the Preliminaries" is not what anybody filed under."""
    urls = _asked_many(monkeypatch, provider, [[], []])
    provider.search({"type": "episode", "show_title": "Hikaru no Go",
                     "title": "The Last Day of the Preliminaries",
                     "season": 2, "episode": 2,
                     "ids": {"imdb": "tt0426711"}}, None, ["en"])
    assert "query-hikaru%20no%20go" in urls[1]
    assert "preliminaries" not in urls[1]


def test_an_anime_romaji_alias_is_asked_alongside_the_english_name(
        monkeypatch, provider):
    """Naruto Shippuden 1x01: "naruto shippuuden" returned a HorribleSubs
    upload "naruto shippuden" did not, because this index matches its own
    title mapping rather than a filename and the two names do not resolve
    to identically the same rows. Both are worth a request, not one or the
    other - the id query already failed, so this is the one path where
    subtitles for a real anime release can go permanently unreached."""
    urls = _asked_many(monkeypatch, provider, [[], [_row()], []])
    found = provider.search({
        "type": "episode", "title": "Naruto Shippuden",
        "search_title": "Naruto Shippuden",
        "aliases": ["Naruto Shippuuden"],
        "season": 1, "episode": 1, "ids": {"imdb": "tt0988824"}}, None, ["en"])
    assert len(urls) == 3
    assert "query-naruto%20shippuden" in urls[1]
    assert "query-naruto%20shippuuden" in urls[2]
    assert found, "the alias query's answer must be kept"


def test_the_alias_query_still_runs_when_the_english_name_already_answered(
        monkeypatch, provider):
    """A name query that already found something must not stop there - the
    live measurement is that "naruto shippuden" and "naruto shippuuden"
    return partly different rows, not one a subset of the other."""
    urls = _asked_many(monkeypatch, provider, [[], [_row()], [_row()]])
    provider.search({
        "type": "episode", "title": "Naruto Shippuden",
        "search_title": "Naruto Shippuden",
        "aliases": ["Naruto Shippuuden"],
        "season": 1, "episode": 1, "ids": {"imdb": "tt0988824"}}, None, ["en"])
    assert len(urls) == 3, "the alias must still be asked after a hit"


def test_search_title_is_preferred_over_a_possibly_hebrew_title(
        monkeypatch, provider):
    """`meta["title"]` follows the UI language and can be Hebrew, which this
    index answers with nothing. `search_title` is the anime-specific English
    name computed for exactly this reason, and must win over both `title`
    and `show_title` when it is present."""
    urls = _asked_many(monkeypatch, provider, [[], []])
    provider.search({
        "type": "episode", "title": u"נארוטו", "show_title": u"נארוטו",
        "search_title": "Naruto Shippuden",
        "season": 1, "episode": 1, "ids": {"imdb": "tt0988824"}}, None, ["en"])
    assert "query-naruto%20shippuden" in urls[1]


def test_no_more_than_two_aliases_are_ever_asked(monkeypatch, provider):
    """A title with three regional respellings costs four requests total -
    the id, the name, two aliases - not one per alias."""
    urls = _asked_many(monkeypatch, provider, [[], [], [], []])
    provider.search({
        "type": "episode", "title": "Show", "search_title": "Show",
        "aliases": ["Alias One", "Alias Two", "Alias Three"],
        "season": 1, "episode": 1, "ids": {"imdb": "tt1"}}, None, ["en"])
    assert len(urls) == 4, "the id, the name, and two aliases - not three"
    assert "alias%20three" not in urls[-1], "the third alias is never asked"

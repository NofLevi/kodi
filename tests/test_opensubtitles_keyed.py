"""The keyed OpenSubtitles API must not overstate incomplete evidence."""
import pytest


def _meta():
    return {"type": "movie", "title": "Fight Club", "year": 1999,
            "ids": {"imdb": "tt0137523", "tmdb": 550}}


def _search(monkeypatch, settings_module, attributes, video_hash="hash", size=1234):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(
        opensubtitles, "_ask",
        lambda *args, **kwargs: {"data": [{"attributes": attributes}]})
    return opensubtitles.search(_meta(), None, ["en"], video_hash, size)


def test_multifile_release_is_not_offered_as_incomplete_cd1(monkeypatch,
                                                            settings_module):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999.2CD",
        "files": [{"file_id": 11, "file_name": "cd1.srt"},
                  {"file_id": 12, "file_name": "cd2.srt"}],
    })
    assert found == []


def test_declared_multicd_release_is_rejected_even_if_only_cd1_is_returned(
        monkeypatch, settings_module):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999.CD1", "nb_cd": 2,
        "files": [{"file_id": 11, "file_name": "cd1.srt"}],
    })
    assert found == []


@pytest.mark.parametrize("nb_cd", [None, "", 0, False, [], {}, 1.5, "1.0"])
def test_explicit_malformed_disc_count_is_rejected(monkeypatch, settings_module,
                                                    nb_cd):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999", "nb_cd": nb_cd,
        "files": [{"file_id": 11, "file_name": "movie.srt"}],
    })
    assert found == []


def test_oversized_disc_count_skips_only_bad_entry(monkeypatch,
                                                     settings_module):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    huge = "9" * 5000
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "data": [
            {"attributes": {"language": "en", "nb_cd": huge,
                            "files": [{"file_id": 10}]}},
            {"attributes": {"language": "en", "release": "valid",
                            "files": [{"file_id": 11}]}},
        ]})
    found = opensubtitles.search(_meta(), None, ["en"], "hash", 1234)
    assert [item["download"] for item in found] == [11]


@pytest.mark.parametrize("files", [
    {"file_id": 11}, "file", [None], [{}], [{"file_id": None}],
    [{"file_id": ""}], [{"file_id": "   "}], [{"file_id": False}],
    [{"file_id": 0}], [{"file_id": []}], [{"file_id": {}}],
    [{"file_id": "9" * 5000}], [{"file_id": 1 << 100}],
])
def test_malformed_files_and_ids_are_rejected(monkeypatch, settings_module,
                                               files):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999", "files": files,
    })
    assert found == []


def test_missing_file_id_is_not_an_undownloadable_candidate(monkeypatch,
                                                             settings_module):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999",
        "files": [{"file_name": "movie.srt"}],
    })
    assert found == []


@pytest.mark.parametrize("bad_count", [
    "bad", "1.5", 1.5, float("nan"), float("inf"), [], {}, "9" * 5000,
])
def test_malformed_download_count_does_not_discard_valid_results(
        monkeypatch, settings_module, bad_count):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "data": [
            {"attributes": {"language": "en", "download_count": bad_count,
                            "files": [{"file_id": 10}]}},
            {"attributes": {"language": "en", "download_count": 50,
                            "files": [{"file_id": 11}]}},
        ]})
    found = opensubtitles.search(_meta(), None, ["en"], "hash", 1234)
    assert [item["downloads"] for item in found] == [0, 50]


def test_malformed_language_is_rejected_before_matching(monkeypatch,
                                                         settings_module):
    from pinky.subs import matcher
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "data": [
            {"attributes": {"language": ["en"], "files": [{"file_id": 10}]}},
            {"attributes": {"language": "en", "release": "Fight.Club.1999",
                            "files": [{"file_id": 11}]}},
        ]})
    found = opensubtitles.search(_meta(), None, ["en"], "hash", 1234)
    winners, ranked = matcher.best(found, _meta(), 0, languages=["en"])
    assert list(winners) == ["en"]
    assert [item["download"] for item in ranked] == [11]


@pytest.mark.parametrize("bad_release", [["release"], {"name": "release"}, 7, True])
def test_malformed_release_is_safe_for_consensus(monkeypatch, settings_module,
                                                  bad_release):
    from pinky.subs import consensus
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "data": [
            {"attributes": {"language": "en", "release": bad_release,
                            "files": [{"file_id": 10}]}},
            {"attributes": {"language": "en", "release": "valid",
                            "files": [{"file_id": 11}]}},
        ]})
    found = opensubtitles.search(_meta(), None, ["en"], "hash", 1234)
    selected = consensus.distinct(found, "en", 3)
    assert [item["download"] for item in found] == [10, 11]
    assert all(isinstance(item["release"], str) for item in found)
    assert selected


def test_exact_size_is_sent_as_a_hash_query_constraint(monkeypatch,
                                                        settings_module):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    captured = {}

    def ask(*args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return {"data": []}

    monkeypatch.setattr(opensubtitles, "_ask", ask)
    opensubtitles.search(_meta(), None, ["en"], "hash", 987654321)
    assert captured["moviebytesize"] == 987654321


@pytest.mark.parametrize("size", [
    None, "", 0, -1, False, True, 1.5, "1234.0", "bad", float("nan"),
    float("inf"), [], {}, "9" * 5000, 1 << 100,
])
def test_invalid_size_is_ignored_without_losing_results(monkeypatch,
                                                         settings_module,
                                                         size):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    captured = {}

    def ask(*args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return {"data": [{"attributes": {
            "language": "en", "release": "Fight.Club.1999",
            "files": [{"file_id": 11}],
        }}]}

    monkeypatch.setattr(opensubtitles, "_ask", ask)
    found = opensubtitles.search(_meta(), None, ["en"], "hash", size)
    assert "moviebytesize" not in captured
    assert len(found) == 1


def test_download_rejects_truthy_non_object_response(monkeypatch,
                                                      settings_module):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles, "_ask",
                        lambda *args, **kwargs: "malformed")
    assert opensubtitles.download({"download": 11}) == b""


def test_malformed_remaining_does_not_block_valid_download(monkeypatch,
                                                           settings_module):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "remaining": "bad", "link": "https://example.invalid/subtitle"})
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes",
                        lambda *args, **kwargs: b"subtitle")
    monkeypatch.setattr(opensubtitles.common, "extract_subtitle",
                        lambda data, language="", candidate=None: data)
    assert opensubtitles.download({"download": 11}) == b"subtitle"


@pytest.mark.parametrize("bad_link", [7, True, [], {}, "", "   ", "file:///tmp/x"])
def test_download_rejects_non_http_link(monkeypatch, settings_module, bad_link):
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles, "_ask",
                        lambda *args, **kwargs: {"link": bad_link})
    called = []
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes",
                        lambda *args, **kwargs: called.append(True))
    assert opensubtitles.download({"download": 11}) == b""
    assert called == []


def test_keyed_zip_selects_candidate_language(monkeypatch, settings_module):
    import io
    import zipfile
    from pinky.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a.english.srt", b"ENGLISH")
        bundle.writestr("z.hebrew.srt", b"HEBREW")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles, "_ask", lambda *args, **kwargs: {
        "link": "https://example.invalid/sub.zip"})
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes",
                        lambda *args, **kwargs: archive.getvalue())
    found = opensubtitles.download({"download": 11, "language": "he"})
    assert found == b"HEBREW"


def test_keyed_api_hash_claim_is_not_exact_without_size_proof(monkeypatch,
                                                               settings_module):
    found = _search(monkeypatch, settings_module, {
        "language": "en", "release": "Fight.Club.1999",
        "moviehash_match": True,
        "feature_details": {"imdb_id": 9999999, "tmdb_id": 999},
        "files": [{"file_id": 11, "file_name": "movie.srt"}],
    })
    assert len(found) == 1
    assert found[0]["hash_match"] is False


class _Answer:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def shared(monkeypatch):
    """A published list of three keys, and a record of every request."""
    from pinky.subs.providers import opensubtitles
    monkeypatch.setattr(opensubtitles, "_shuffled", [])
    monkeypatch.setattr(opensubtitles.random, "shuffle", lambda keys: None)
    fetched = []

    def get_json(url, **kwargs):
        fetched.append(url)
        return [{"OS_API_KEY_NAME": "a", "OS_API_KEY_VALUE": "k1"},
                {"OS_API_KEY_NAME": "b", "OS_API_KEY_VALUE": " k2 "},
                {"OS_API_KEY_NAME": "c"}, "junk",
                {"OS_API_KEY_NAME": "d", "OS_API_KEY_VALUE": "k3"}]

    monkeypatch.setattr(opensubtitles.http, "get_json", get_json)
    asked = []

    def answering(statuses):
        def request(method, url, headers=None, params=None, **kwargs):
            asked.append((headers["Api-Key"], dict(params or {})))
            status = statuses[len(asked) - 1] if len(asked) <= len(statuses) else 200
            if status is None:
                return None
            return _Answer(status, {"data": [], "total_count": 0,
                                    "link": "https://example.invalid/s"})
        monkeypatch.setattr(opensubtitles.http, "request", request)
    return opensubtitles, fetched, asked, answering


def test_with_no_key_of_its_own_it_uses_the_published_ones(shared, settings_module):
    opensubtitles, fetched, asked, answering = shared
    answering([200])
    opensubtitles.search(_meta(), None, ["he"])
    assert fetched == [opensubtitles.KEYS_URL]
    assert [key for key, _params in asked] == ["k1"]
    assert opensubtitles.configured()


def test_the_viewers_own_key_goes_first_and_the_list_is_not_fetched(
        shared, settings_module):
    opensubtitles, fetched, asked, answering = shared
    settings_module.set("subs.opensubtitles.apikey", "mine")
    answering([200])
    opensubtitles.search(_meta(), None, ["he"])
    assert [key for key, _params in asked] == ["mine"] and fetched == []


def test_a_refused_key_is_followed_by_the_next(shared, settings_module,
                                               monkeypatch):
    """A wrong key answers 503 and a spent one 406, measured; each is about
    the key, so another may work."""
    opensubtitles, _fetched, asked, answering = shared
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes", lambda link: b"x")
    monkeypatch.setattr(opensubtitles.common, "extract_subtitle",
                        lambda data, language="", candidate=None: data)
    answering([503, 406, 200])
    assert opensubtitles.download({"download": 11}) == b"x"
    assert [key for key, _params in asked] == ["k1", "k2", "k3"]


def test_the_walk_is_bounded_and_ends_where_another_key_cannot_help(
        shared, settings_module):
    opensubtitles, _fetched, asked, answering = shared
    answering([503] * 10)
    assert opensubtitles.download({"download": 11}) == b""
    assert len(asked) == 3, "every key there is, and no more"
    del asked[:]
    answering([None])                      # the connection failed
    assert opensubtitles.download({"download": 11}) == b""
    assert len(asked) == 1
    del asked[:]
    answering([429])                       # this box is asking too fast
    assert opensubtitles.download({"download": 11}) == b""
    assert len(asked) == 1


def test_a_list_that_fails_to_arrive_keeps_the_one_it_had(shared, settings_module,
                                                         monkeypatch):
    opensubtitles, fetched, _asked, _answering = shared
    assert opensubtitles._shared_keys() == ["k1", "k2", "k3"]
    assert opensubtitles._shared_keys() == ["k1", "k2", "k3"]
    assert len(fetched) == 1, "kept a day"
    from pinky import cache
    held = cache.get("opensubtitles.keys")
    cache.set("opensubtitles.keys", dict(held, at=0), 3600)
    monkeypatch.setattr(opensubtitles.http, "get_json", lambda url, **kwargs: None)
    assert opensubtitles._shared_keys() == ["k1", "k2", "k3"]


def test_an_episode_is_asked_under_the_shows_id(shared, settings_module):
    opensubtitles, _fetched, asked, answering = shared
    answering([200, 200])
    meta = {"type": "episode", "title": "Wind", "show_title": "Naruto Shippuden",
            "season": 3, "episode": 55, "absolute": 108,
            "ids": {"imdb": "tt0988824"}}
    opensubtitles.search(meta, None, ["en"])
    first, second = (params for _key, params in asked)
    assert (first["parent_imdb_id"], first["season_number"],
            first["episode_number"]) == ("0988824", 3, 55)
    assert "imdb_id" not in first
    assert (second["season_number"], second["episode_number"]) == (1, 108), \
        "the next numbering, because the first found nothing"


def test_a_full_page_is_asked_again_a_language_at_a_time(settings_module,
                                                        monkeypatch):
    """Fight Club in Hebrew and English: 129 rows, a page of fifty, and six
    of them Hebrew - against thirty-three Hebrew asked alone."""
    from pinky.subs.providers import opensubtitles

    def row(number, language):
        return {"attributes": {"language": language, "release": "r%d" % number,
                               "files": [{"file_id": number}]}}
    asked = []

    def ask(method, path, tries, params=None, **kwargs):
        languages = dict(params)["languages"]
        asked.append(languages)
        if languages == "en,he":
            return {"total_count": 129,
                    "data": [row(n, "en") for n in range(44)]
                    + [row(100 + n, "he") for n in range(6)]}
        count = 33 if languages == "he" else 50
        return {"total_count": count,
                "data": [row(200 + n, languages) for n in range(count)]}

    monkeypatch.setattr(opensubtitles, "_ask", ask)
    found = opensubtitles.search(_meta(), None, ["he", "en"])
    assert asked == ["en,he", "he", "en"]
    assert sum(1 for item in found if item["language"] == "he") == 33


def test_a_regional_variant_is_its_language(monkeypatch, settings_module):
    from pinky.subs.providers import opensubtitles
    asked = []

    def ask(method, path, tries, params=None, **kwargs):
        asked.append(dict(params)["languages"])
        return {"data": [{"attributes": {"language": "pt-BR", "release": "r",
                                         "files": [{"file_id": 1}]}}]}

    monkeypatch.setattr(opensubtitles, "_ask", ask)
    found = opensubtitles.search(_meta(), None, ["pt"])
    assert asked == ["pt-br,pt-pt"] and found[0]["language"] == "pt"

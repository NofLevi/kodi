"""The keyed OpenSubtitles API must not overstate incomplete evidence."""
import pytest


def _meta():
    return {"type": "movie", "title": "Fight Club", "year": 1999,
            "ids": {"imdb": "tt0137523", "tmdb": 550}}


def _search(monkeypatch, settings_module, attributes, video_hash="hash", size=1234):
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(
        opensubtitles.http, "get_json",
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
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    huge = "9" * 5000
    monkeypatch.setattr(opensubtitles.http, "get_json", lambda *args, **kwargs: {
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
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles.http, "get_json", lambda *args, **kwargs: {
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
    from katan.subs import matcher
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles.http, "get_json", lambda *args, **kwargs: {
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
    from katan.subs import consensus
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles.http, "get_json", lambda *args, **kwargs: {
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
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    captured = {}

    def get_json(*args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return {"data": []}

    monkeypatch.setattr(opensubtitles.http, "get_json", get_json)
    opensubtitles.search(_meta(), None, ["en"], "hash", 987654321)
    assert captured["moviebytesize"] == 987654321


@pytest.mark.parametrize("size", [
    None, "", 0, -1, False, True, 1.5, "1234.0", "bad", float("nan"),
    float("inf"), [], {}, "9" * 5000, 1 << 100,
])
def test_invalid_size_is_ignored_without_losing_results(monkeypatch,
                                                         settings_module,
                                                         size):
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    captured = {}

    def get_json(*args, **kwargs):
        captured.update(kwargs.get("params") or {})
        return {"data": [{"attributes": {
            "language": "en", "release": "Fight.Club.1999",
            "files": [{"file_id": 11}],
        }}]}

    monkeypatch.setattr(opensubtitles.http, "get_json", get_json)
    found = opensubtitles.search(_meta(), None, ["en"], "hash", size)
    assert "moviebytesize" not in captured
    assert len(found) == 1


def test_download_rejects_truthy_non_object_response(monkeypatch,
                                                      settings_module):
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles.http, "post_json",
                        lambda *args, **kwargs: "malformed")
    assert opensubtitles.download({"download": 11}) == b""


def test_malformed_remaining_does_not_block_valid_download(monkeypatch,
                                                           settings_module):
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles.http, "post_json", lambda *args, **kwargs: {
        "remaining": "bad", "link": "https://example.invalid/subtitle"})
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes",
                        lambda *args, **kwargs: b"subtitle")
    monkeypatch.setattr(opensubtitles.common, "extract_subtitle",
                        lambda data, language="", candidate=None: data)
    assert opensubtitles.download({"download": 11}) == b"subtitle"


@pytest.mark.parametrize("bad_link", [7, True, [], {}, "", "   ", "file:///tmp/x"])
def test_download_rejects_non_http_link(monkeypatch, settings_module, bad_link):
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles.http, "post_json",
                        lambda *args, **kwargs: {"link": bad_link})
    called = []
    monkeypatch.setattr(opensubtitles.common, "fetch_bytes",
                        lambda *args, **kwargs: called.append(True))
    assert opensubtitles.download({"download": 11}) == b""
    assert called == []


def test_keyed_zip_selects_candidate_language(monkeypatch, settings_module):
    import io
    import zipfile
    from katan.subs.providers import opensubtitles
    settings_module.set("subs.opensubtitles.apikey", "test-key")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a.english.srt", b"ENGLISH")
        bundle.writestr("z.hebrew.srt", b"HEBREW")
    monkeypatch.setattr(opensubtitles, "configured", lambda: True)
    monkeypatch.setattr(opensubtitles.http, "post_json", lambda *args, **kwargs: {
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

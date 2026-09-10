"""Regression coverage for defects found by the cross-component audit."""
import zipfile


def _auth(settings):
    settings.set_many({
        "trakt.client_id": "synthetic-client",
        "trakt.client_secret": "synthetic-secret",
        "trakt.access_token": "synthetic-token",
        "trakt.expires": "9999999999",
    })


def test_cached_dedupe_keeps_one_resolvable_torrent_identity():
    from katan.sources import model
    first = model.from_release_name(
        "Film.2024.1080p.WEB.x264-GRP.mkv", "one", size=2 * 1024 ** 3,
        info_hash="a" * 40)
    cached = model.from_release_name(
        "Film.2024.1080p.WEB.x264-GRP.mkv", "two", size=2 * 1024 ** 3,
        info_hash="b" * 40)
    cached.update(cached=True, cached_by="torbox",
                  magnet="magnet:?xt=urn:btih:" + "b" * 40)
    merged = model.dedupe([first, cached])[0]
    assert merged["hash"] == "b" * 40
    assert "b" * 40 in merged["magnet"]


def test_unknown_debrid_cache_answer_does_not_erase_indexer_evidence(monkeypatch):
    from katan.debrid import registry
    from katan.sources import aggregator
    answer = registry.CacheMap()
    monkeypatch.setattr(registry, "cached_map", lambda hashes: answer)
    source = {"hash": "c" * 40, "cached": True, "cached_by": "realdebrid"}
    aggregator._check_debrid_cache([source], recheck=True)
    assert source["cached_by"] == "realdebrid"


def test_direct_provider_url_does_not_require_debrid():
    from katan import play
    url = "https://media.example.invalid/video"
    assert play._resolve({"url": url}) == url


def test_file_picker_honours_hint_and_skips_bad_irrelevant_rows():
    from katan.debrid import base
    files = [
        {"name": "readme.txt", "size": "unknown"},
        {"name": "Wanted.Movie.mkv", "size": 10 ** 9, "id": 10},
        {"name": "Different.Bonus.mkv", "size": 2 * 10 ** 9, "id": 11},
    ]
    chosen = base.DebridService().pick_file(
        files, {"file_name": "Wanted.Movie.mkv"}, {"type": "movie"})
    assert chosen["id"] == 10


def test_biggest_setting_really_prefers_biggest(settings_module):
    from katan.sources import model, scoring
    settings_module.set_many({
        "sources.size_preference": "biggest", "sources.cached_only": "false",
        "sources.max_size_gb": "80", "sources.min_resolution": "720p",
        "sources.max_resolution": "1080p", "sources.allow_hevc": "true",
        "sources.allow_av1": "true", "sources.allow_hdr": "true",
    })
    small = model.from_release_name("Film.1080p.WEB.x264-A", "x",
                                    size=2 * 1024 ** 3, info_hash="d" * 40)
    big = model.from_release_name("Film.1080p.WEB.x264-B", "x",
                                  size=8 * 1024 ** 3, info_hash="e" * 40)
    ranked, _reason = scoring.rank([small, big])
    assert ranked[0]["size"] == big["size"]


def test_no_id_source_cache_includes_movie_year():
    from katan.sources import aggregator
    one = aggregator.cache_key({"type": "movie", "title": "Collision",
                                "year": 1990, "ids": {}})
    two = aggregator.cache_key({"type": "movie", "title": "Collision",
                                "year": 2026, "ids": {}})
    assert one != two


def test_malformed_animetosho_row_does_not_hide_valid_rows(monkeypatch):
    from katan.sources.providers import animetosho
    payload = [None, {"title": "Show - 01 [1080p].mkv",
                      "info_hash": "f" * 40, "total_size": 10 ** 9,
                      "seeders": 5}]
    monkeypatch.setattr(animetosho.http, "get_json", lambda *a, **k: payload)
    assert len(animetosho.search(
        {"type": "episode", "title": "Show", "episode": 1})) == 1


def test_failed_trakt_pull_keeps_activity_dirty(monkeypatch, settings_module):
    from katan.meta import trakt
    _auth(settings_module)
    activity = {"movies": {"watched_at": "2026-01-01T00:00:00Z"}}
    monkeypatch.setattr(trakt, "last_activities", lambda: activity)
    assert trakt.needs_sync()
    monkeypatch.setattr(trakt, "_get", lambda *a, **k: None)
    assert trakt.sync_state() is False
    assert trakt.needs_sync()


def test_trakt_sync_paginates_and_requests_episode_progress(
        monkeypatch, settings_module):
    from katan import cache
    from katan.meta import trakt, trakt_state
    _auth(settings_module)
    calls = []

    def get(path, **kwargs):
        calls.append((path, kwargs))
        if path == "/sync/watched/movies":
            page = kwargs.get("page")
            if page in (1, 2):
                return [{"plays": 1, "movie": {"ids": {"tmdb": page}}}]
        return []

    monkeypatch.setattr(trakt, "_get", get)
    assert trakt.sync_state()
    watched = cache.get(trakt_state.WATCHED_KEY) or {}
    assert trakt_state.state_key("movie", {"tmdb": 2}) in watched
    show = next(kwargs for path, kwargs in calls
                if path == "/sync/watched/shows")
    assert show["extended"] == "progress"


def _update_zip(path, extra=(), compress=zipfile.ZIP_STORED):
    with zipfile.ZipFile(str(path), "w", compress) as archive:
        archive.writestr("plugin.video.katan/addon.xml",
                         '<addon id="plugin.video.katan" version="1.2.3"/>')
        archive.writestr("plugin.video.katan/main.py", "pass\n")
        for name, data in extra:
            archive.writestr(name, data)
    return str(path)


def test_update_rejects_duplicate_canonical_manifest(tmp_path):
    from katan import updater
    path = _update_zip(tmp_path / "duplicate.zip", [
        ("plugin.video.katan/./addon.xml",
         '<addon id="synthetic.wrong" version="9"/>')])
    assert updater._is_sane_zip(path, expected_version="1.2.3") is False


def test_update_rejects_archive_bomb_and_incomplete_release(tmp_path):
    from katan import updater
    bomb = _update_zip(tmp_path / "bomb.zip", [
        ("plugin.video.katan/large.bin", b"A" * (33 * 1024 * 1024))],
        zipfile.ZIP_DEFLATED)
    assert updater._is_sane_zip(bomb, expected_version="1.2.3") is False
    incomplete = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(str(incomplete), "w") as archive:
        archive.writestr("plugin.video.katan/addon.xml",
                         '<addon id="plugin.video.katan" version="1.2.3"/>')
    assert updater._is_sane_zip(str(incomplete)) is False


def test_malformed_versions_are_not_accepted_by_prefix():
    from katan import updater
    assert updater.parse_version("1.2.3trailing") == (0, 0, 0)
    assert updater.parse_version("9" * 5000) == (0, 0, 0)


def test_secret_bearing_cache_values_stay_out_of_sqlite(monkeypatch):
    from katan import cache
    from katan.sources import aggregator
    from katan.vod import entitlement, kaltura

    signed = "https://cdn.invalid/SYNTH_PATH?sig=SYNTH_SIGNED_VALUE"
    ticket = "hdnea=SYNTH_PERSISTED_TICKET"
    monkeypatch.setattr(kaltura, "_request", lambda *a: [
        {}, {}, {"sources": [{"url": signed, "format": "applehttp",
                               "drm": False}]}])
    monkeypatch.setattr(entitlement, "_mint_mako", lambda *a: ticket)
    assert kaltura.playback_url("entry", "1", "https://ref.invalid")[0] == signed
    assert entitlement.mako_ticket("", refresh=True) == ticket
    cache.volatile_set(aggregator.cache_key(
        {"type": "movie", "ids": {"tmdb": 1}}), [{"url": signed}], 60)
    cache.set("harmless", {"ok": True}, 60)
    with open(cache.db_path(), "rb") as handle:
        raw = handle.read()
    assert b"SYNTH_SIGNED_VALUE" not in raw
    assert b"SYNTH_PERSISTED_TICKET" not in raw


def test_retry_closes_stream_before_next_attempt(monkeypatch):
    from katan import http

    class Response:
        headers = {}
        def __init__(self, status):
            self.status_code = status
            self.closed = False
        def close(self):
            self.closed = True

    first, second = Response(503), Response(200)
    queue = [first, second]

    class Session:
        def request(self, *args, **kwargs):
            return queue.pop(0)

    monkeypatch.setattr(http, "session", lambda: Session())
    assert http.get("https://example.invalid", stream=True,
                    retries=1, backoff=0) is second
    assert first.closed

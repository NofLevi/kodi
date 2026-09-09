"""The orchestration: run providers, merge, ask about caching, rank, cache.

The pieces underneath are well covered; the order they run in was not, and
two bugs lived in exactly that gap - "show all" returning the short list, and
a cache flag that could go up but never come down.
"""
import pytest

from katan.sources import aggregator, model


def make(title, **kwargs):
    defaults = {"size": 4 * 1024 ** 3, "seeders": 40}
    defaults.update(kwargs)
    info_hash = defaults.pop("info_hash", None) or ("%040x" % abs(hash(title)))
    return model.from_release_name(title, provider=defaults.pop("provider", "test"),
                                   info_hash=info_hash, **defaults)


META = {"type": "movie", "ids": {"imdb": "tt0111161"}, "title": "A Film",
        "year": 1994}


@pytest.fixture(autouse=True)
def rank_uncached_too(settings_module):
    """These tests are about orchestration, not about the cached-only filter.

    sources.cached_only ships as true and drops an uncached source outright,
    so leaving it on would mean ranking an empty list in every test that is
    not about caching. The ones that are set it themselves.
    """
    settings_module.set("sources.cached_only", "false")


@pytest.fixture
def twenty_sources(monkeypatch):
    """Twenty distinct sources from one fake provider, all of them playable."""
    sources = [make("A.Film.1994.1080p.BluRay.x264-G%02d" % n,
                    info_hash="%040d" % n, seeders=100 - n)
               for n in range(20)]

    class FakeProvider(object):
        calls = 0

        @staticmethod
        def search(meta):
            FakeProvider.calls += 1
            return [dict(s) for s in sources]

    monkeypatch.setattr(aggregator, "_enabled_providers",
                        lambda meta: [("torrentio", FakeProvider)])
    monkeypatch.setattr(aggregator, "_check_debrid_cache",
                        lambda sources, recheck=False: True)
    return FakeProvider


# --------------------------------------------------------------------------
# top-K and "show all"
# --------------------------------------------------------------------------


def test_find_returns_only_the_top_few(twenty_sources, settings_module):
    settings_module.set("sources.results", "8")
    assert len(aggregator.find(META)) == 8


def test_show_all_really_does_show_them_all(twenty_sources, settings_module):
    """The bug this file was written for.

    Only the truncated list was cached, and all_sources read back the same
    cache entry, so the toggle re-ranked eight sources and handed back the
    same eight. The full set existed nowhere once find() had returned.
    """
    settings_module.set("sources.results", "8")
    short = aggregator.find(META)
    full = aggregator.all_sources(META)

    assert len(short) == 8
    assert len(full) == 20, "show all must show more than the short list"
    assert [s["hash"] for s in full[:8]] == [s["hash"] for s in short], \
        "and it must start with the same sources, in the same order"


def test_show_all_searches_when_nothing_is_cached_yet(twenty_sources):
    """Opening "show all" first must not return an empty list."""
    assert len(aggregator.all_sources(META)) == 20
    assert twenty_sources.calls == 1


def test_a_second_search_comes_from_the_cache(twenty_sources):
    aggregator.find(META)
    aggregator.find(META)
    aggregator.all_sources(META)
    assert twenty_sources.calls == 1, "the providers should run once"


def test_a_legacy_cached_source_gets_subtitle_accuracy(monkeypatch, registry):
    """An upgrade must not leave the picker blank until a 20-minute cache expires."""
    from katan import cache

    old = make("A.Film.1994.1080p.WEB-DL.x264-GRP", info_hash="a" * 40)
    assert "subs_kind" not in old
    cache.set(aggregator.cache_key(META), [old], aggregator.TTL_RESULTS)
    registry({"a" * 40})

    calls = []

    def annotate(sources, meta):
        calls.append(1)
        for source in sources:
            source["subs_kind"] = "external"
            source["subs_score"] = 82
        return True

    monkeypatch.setattr(aggregator, "_apply_subtitles", annotate)
    found = aggregator.find(META)
    assert found[0]["subs_kind"] == "external"
    assert found[0]["subs_score"] == 82
    aggregator.find(META)
    assert len(calls) == 1, "the upgraded cache must not be annotated again"


def test_a_failed_legacy_cache_upgrade_is_not_persisted(
        monkeypatch, registry):
    """A provider failure must not renew stale sources or create a retry loop."""
    from katan import cache
    from katan.subs import auto

    old = make("A.Film.1994.1080p.WEB-DL.x264-GRP", info_hash="a" * 40)
    cache.set(aggregator.cache_key(META), [old], aggregator.TTL_RESULTS)
    registry({"a" * 40})

    def fail(*args, **kwargs):
        raise IOError("subtitle catalogue unavailable")

    monkeypatch.setattr(auto, "search_candidates", fail)
    writes = []
    monkeypatch.setattr(cache, "set",
                        lambda *args, **kwargs: writes.append(args))

    found = aggregator.find(META)
    assert "subs_kind" not in found[0]
    assert writes == [], "an incomplete migration must not renew the cache TTL"


def test_forcing_a_search_ignores_the_cache(twenty_sources):
    aggregator.find(META)
    aggregator.find(META, force=True)
    assert twenty_sources.calls == 2


def test_the_results_limit_is_the_users(twenty_sources, settings_module):
    settings_module.set("sources.results", "3")
    assert len(aggregator.find(META)) == 3
    assert len(aggregator.all_sources(META)) == 20


def test_no_providers_means_no_sources(monkeypatch):
    monkeypatch.setattr(aggregator, "_enabled_providers", lambda meta: [])
    assert aggregator.find(META) == []


def test_a_provider_that_finds_nothing_is_remembered_briefly(monkeypatch):
    class Empty(object):
        calls = 0

        @staticmethod
        def search(meta):
            Empty.calls += 1
            return []

    monkeypatch.setattr(aggregator, "_enabled_providers",
                        lambda meta: [("torrentio", Empty)])
    assert aggregator.find(META) == []
    assert aggregator.find(META) == []
    assert Empty.calls == 1, "an empty answer is cached too, briefly"


# --------------------------------------------------------------------------
# re-checking what is cached
# --------------------------------------------------------------------------


class FakeRegistry(object):
    """Stands in for debrid/registry, recording what it was asked."""

    def __init__(self, cached_hashes):
        self.cached = set(cached_hashes)
        self.asked = []

    def cached_map(self, hashes):
        self.asked.append(list(hashes))
        return {h: "torbox" for h in hashes if h in self.cached}


@pytest.fixture
def registry(monkeypatch):
    """Install a fake debrid registry and hand the test back the fake."""
    from katan.debrid import registry as real

    def install(cached_hashes):
        fake = FakeRegistry(cached_hashes)
        monkeypatch.setattr(real, "cached_map", fake.cached_map)
        return fake

    return install


@pytest.fixture
def two_sources(monkeypatch):
    good = make("A.Film.1994.1080p.BluRay.x264-GOOD", info_hash="a" * 40,
                seeders=10)
    other = make("A.Film.1994.1080p.WEB-DL.x264-OTHER", info_hash="b" * 40,
                 seeders=500)

    class FakeProvider(object):
        @staticmethod
        def search(meta):
            return [dict(good), dict(other)]

    monkeypatch.setattr(aggregator, "_enabled_providers",
                        lambda meta: [("torrentio", FakeProvider)])
    return FakeProvider


def test_a_cached_source_is_flagged_and_wins(two_sources, registry):
    registry({"a" * 40})
    found = aggregator.find(META)
    assert found[0]["hash"] == "a" * 40, "cached beats 50x the seeders"
    assert found[0]["cached_by"] == "torbox"


def test_a_flag_that_is_no_longer_true_is_cleared(two_sources, registry):
    """The other bug this file was written for.

    _recheck_cached asked only about the sources marked *un*cached, so a flag
    could go up and never come down - which is exactly the failure its own
    docstring said it existed to prevent. The viewer pressed play on a source
    the service had since evicted.
    """
    fake = registry({"a" * 40})
    found = aggregator.find(META)
    assert found[0]["cached"] is True

    fake.cached.clear()                       # the service evicted it
    found = aggregator.find(META)             # served from the result cache

    assert not found[0].get("cached")
    assert "cached_by" not in found[0]


def test_losing_the_flag_reorders_the_list(two_sources, registry):
    """Being cached is worth more than everything else put together.

    So a flag that changed and an order that did not would leave a source
    that can no longer play sitting at the top of the picker.
    """
    fake = registry({"a" * 40})
    assert aggregator.find(META)[0]["hash"] == "a" * 40

    fake.cached = {"b" * 40}
    assert aggregator.find(META)[0]["hash"] == "b" * 40


def test_dropping_out_of_the_cache_removes_it_when_cached_only_is_on(
        two_sources, registry, settings_module):
    settings_module.set("sources.cached_only", "true")
    fake = registry({"a" * 40})
    assert len(aggregator.find(META)) == 1

    fake.cached.clear()
    assert aggregator.find(META) == [], "nothing is playable now, and it says so"


def test_the_recheck_asks_about_every_source(two_sources, registry):
    fake = registry({"a" * 40})
    aggregator.find(META)
    first = len(fake.asked)
    aggregator.find(META)

    assert len(fake.asked) > first
    assert set(fake.asked[-1]) == {"a" * 40, "b" * 40}, \
        "including the ones already flagged, which is the whole point"


def test_a_debrid_service_that_will_not_answer_leaves_the_list_alone(
        two_sources, monkeypatch):
    """Silence is not a "no".

    Clearing the flags when the lookup itself failed would empty the picker
    every time the network hiccuped, with cached_only on.
    """
    from katan.debrid import registry as real

    fake = FakeRegistry({"a" * 40})
    monkeypatch.setattr(real, "cached_map", fake.cached_map)
    found = aggregator.find(META)
    assert found[0]["cached"] is True

    def broken(hashes):
        raise IOError("no route to host")

    monkeypatch.setattr(real, "cached_map", broken)
    found = aggregator.find(META)
    assert found[0]["cached"] is True, "a failed lookup must not clear anything"


def test_prefetching_never_asks_the_debrid_services(two_sources, registry):
    """The prefetch runs while something else is playing, so it stays quiet."""
    fake = registry({"a" * 40})
    aggregator.find(META)
    asked = len(fake.asked)
    aggregator.find(META, prefetch=True)
    assert len(fake.asked) == asked


# --------------------------------------------------------------------------
# the rest of the orchestration
# --------------------------------------------------------------------------


def test_the_same_torrent_from_three_providers_is_asked_about_once(monkeypatch,
                                                                   registry):
    shared = "c" * 40
    fake = registry({shared})

    def provider(name):
        class P(object):
            @staticmethod
            def search(meta):
                return [make("A.Film.1994.1080p.BluRay-X", info_hash=shared,
                             provider=name)]
        return P

    monkeypatch.setattr(aggregator, "_enabled_providers",
                        lambda meta: [(n, provider(n))
                                      for n in ("torrentio", "comet",
                                                "mediafusion")])
    found = aggregator.find(META)
    assert len(found) == 1
    assert fake.asked[0] == [shared], "merged first, then asked once"


def test_each_source_carries_the_playback_context(two_sources, registry):
    """The debrid client needs season and episode to pick a file from a pack."""
    registry(set())
    meta = dict(META, type="episode", season=2, episode=5)
    found = aggregator.find(meta)
    context = found[0]["extra"]["meta"]
    assert context["season"] == 2 and context["episode"] == 5
    assert context["title"] == "A Film"


def test_a_provider_that_raises_does_not_take_the_search_down(monkeypatch,
                                                              registry):
    registry(set())

    class Broken(object):
        @staticmethod
        def search(meta):
            raise ValueError("the indexer returned nonsense")

    class Works(object):
        @staticmethod
        def search(meta):
            return [make("A.Film.1994.1080p.BluRay-OK", info_hash="d" * 40)]

    monkeypatch.setattr(aggregator, "_enabled_providers",
                        lambda meta: [("torrentio", Broken), ("comet", Works)])
    assert len(aggregator.find(META)) == 1


def test_anime_only_providers_are_skipped_for_a_film(settings_module,
                                                     monkeypatch):
    monkeypatch.setattr(
        settings_module, "enabled_source_providers",
        lambda: ["torrentio", "nyaa", "animetosho"])
    chosen = [name for name, _ in aggregator._enabled_providers(META)]
    assert chosen == ["torrentio"]


def test_anime_gets_its_providers(settings_module, monkeypatch):
    monkeypatch.setattr(
        settings_module, "enabled_source_providers",
        lambda: ["torrentio", "nyaa", "animetosho"])
    anime = dict(META, ids={"imdb": "tt0111161", "anilist": 21})
    chosen = [name for name, _ in aggregator._enabled_providers(anime)]
    assert chosen == ["torrentio", "nyaa", "animetosho"]


def test_an_episode_is_not_judged_by_a_films_runtime():
    """The size sanity check would drop every episode if it expected two hours."""
    assert aggregator._runtime_hours({"type": "episode"}) == 0.75
    assert aggregator._runtime_hours({"type": "movie"}) == 2.0
    assert aggregator._runtime_hours(
        {"type": "movie", "item": {"duration": 9000}}) == 2.5


def test_invalidating_forgets_one_title_or_all_of_them(twenty_sources):
    aggregator.find(META)
    aggregator.invalidate(META)
    aggregator.find(META)
    assert twenty_sources.calls == 2

    aggregator.invalidate()
    aggregator.find(META)
    assert twenty_sources.calls == 3


def test_a_correction_is_not_written_back_over_the_full_list(two_sources,
                                                             registry,
                                                             settings_module):
    """Re-ranking drops what is no longer playable, and with cached_only on
    that can be most of the list. Storing the shorter version would mean a
    source that became cached again could not come back until the whole entry
    expired."""
    from katan import cache
    from katan.sources import aggregator as agg

    settings_module.set("sources.cached_only", "true")
    fake = registry({"a" * 40, "b" * 40})
    assert len(agg.find(META)) == 2, "both were cached when the search ran"

    fake.cached.clear()                       # the service evicted both
    assert agg.find(META) == [], "nothing is playable right now"

    stored = cache.get(agg.cache_key(META))
    assert len(stored) == 2, \
        "the stored list should still hold both, not the filtered version"

    fake.cached = {"a" * 40, "b" * 40}        # and they come back
    assert len(agg.find(META)) == 2


# --------------------------------------------------------------------------
# when nothing is cached, but plenty exists
# --------------------------------------------------------------------------


def test_uncached_sources_are_still_there_when_nothing_is_cached(
        settings_module, monkeypatch):
    """"Cached only" is right for a film and wrong for last week's episode.

    Forty-four copies of one Bleach episode were found, every one of them
    real, and not one was on the debrid account yet - so the viewer was told
    "no sources found", which was untrue.
    """
    from katan.sources import aggregator

    settings_module.set_many({"sources.cached_only": "true",
                              "sources.min_resolution": "720p",
                              "sources.max_size_gb": "80"})
    meta = {"type": "episode", "title": "Bleach", "season": 2, "episode": 46,
            "ids": {"imdb": "tt0434665"}}

    fresh = [
        {"title": "Bleach.S02E46.1080p.WEB-DL-A", "hash": "a" * 40,
         "quality": "1080p", "size": 1024 ** 3, "cached": False,
         "seeders": 30, "languages": [], "hdr": [], "codec": "h264"},
        {"title": "Bleach.S02E46.720p.WEB-DL-B", "hash": "b" * 40,
         "quality": "720p", "size": 700 * 1024 ** 2, "cached": False,
         "seeders": 12, "languages": [], "hdr": [], "codec": "h264"},
    ]
    from katan import cache
    cache.set(aggregator.unfiltered_key(meta), fresh, 600)

    waiting = aggregator.uncached(meta)
    assert len(waiting) == 2, "both exist; neither is ready"
    assert waiting[0]["quality"] == "1080p", "still ranked properly"


def test_nothing_at_all_is_still_nothing(settings_module):
    """The fallback must not invent sources that were never found."""
    from katan.sources import aggregator

    meta = {"type": "movie", "title": "A Film", "ids": {"imdb": "tt1"}}
    assert aggregator.uncached(meta) == []


def test_the_fallback_still_honours_every_other_filter(settings_module):
    """Only "not cached" is set aside. A cam is still a cam."""
    from katan import cache
    from katan.sources import aggregator

    settings_module.set_many({"sources.cached_only": "true",
                              "sources.allow_cam": "false",
                              "sources.min_resolution": "720p",
                              "sources.max_size_gb": "80"})
    meta = {"type": "movie", "title": "A Film", "ids": {"imdb": "tt2"}}
    cache.set(aggregator.unfiltered_key(meta), [
        {"title": "A.Film.2026.1080p.CAM.x264-RIP", "hash": "c" * 40,
         "quality": "1080p", "size": 1024 ** 3, "cached": False,
         "seeders": 5, "languages": [], "hdr": [], "codec": "h264"},
    ], 600)

    assert aggregator.uncached(meta) == [], \
        "a camera recording is not rescued by nothing being cached"


# --------------------------------------------------------------------------
# the anime address, and what it costs
#
# A named arc means TMDB's address is the wrong address rather than merely a
# sometimes-empty one, so the providers that ask by id are asked at the Kitsu
# address *instead of* the TMDB one. Asking both was half again as many
# requests through a four-worker cap, and the viewer waits through every one:
# Bleach 2x47 took 4217 ms asking both and 1043 ms asking once, for the same
# three sources.
# --------------------------------------------------------------------------


def test_a_named_arc_replaces_the_address_it_does_not_add_to_it(monkeypatch):
    from katan.sources import aggregator

    asked = []

    class ById(object):
        BY_NAME = False

        def search(self, meta):
            asked.append(("byid", (meta.get("ids") or {}).get("kitsu"),
                          meta.get("episode")))
            return []

    class ByName(object):
        BY_NAME = True

        def search(self, meta):
            asked.append(("byname", (meta.get("ids") or {}).get("kitsu"),
                          meta.get("episode")))
            return []

    meta = {"type": "episode", "title": "Bleach", "season": 2, "episode": 46,
            "ids": {"imdb": "tt0434665"}}
    address = dict(meta, episode=6, ids={"kitsu": "49444"})

    aggregator._run_providers([("a", ById()), ("b", ByName())], meta,
                              quiet=True, also=address)

    assert len(asked) == 2, "one question each, not two each"
    by_id = [a for a in asked if a[0] == "byid"][0]
    by_name = [a for a in asked if a[0] == "byname"][0]
    assert by_id[1] == "49444" and by_id[2] == 6, \
        "the id provider is asked at the Kitsu address"
    assert by_name[1] is None and by_name[2] == 46, \
        "the name provider keeps the show's own numbering"


def test_without_an_anime_address_everything_is_asked_once(monkeypatch):
    from katan.sources import aggregator

    asked = []

    class Provider(object):
        def search(self, meta):
            asked.append(meta.get("episode"))
            return []

    aggregator._run_providers([("a", Provider()), ("b", Provider())],
                              {"type": "episode", "episode": 3}, quiet=True)
    assert asked == [3, 3]

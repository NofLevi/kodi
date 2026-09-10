"""Behaviour tests for the pieces every other feature sits on."""
import time



# --------------------------------------------------------------------------
# settings defaults
#
# DEFAULTS is the single registry of what a setting means when nobody has
# touched it, and get_bool was quietly ignoring it: it handed get() an empty
# string as the fallback, and get() returns any fallback that is not None
# instead of consulting DEFAULTS. So every boolean that had never been written
# read as false whatever DEFAULTS said - autoplay, cached_only, prefer_hebrew,
# source_memory and thirteen others, all of which ship as true.
# --------------------------------------------------------------------------


def test_an_unset_boolean_uses_its_registered_default():
    from katan import settings
    assert settings.DEFAULTS["sources.autoplay"] == "true"
    assert settings.get_bool("sources.autoplay") is True
    assert settings.DEFAULTS["kids.enabled"] == "false"
    assert settings.get_bool("kids.enabled") is False


def test_every_declared_boolean_reads_back_as_declared():
    """One assertion for the whole table, so a new setting cannot slip."""
    from katan import settings
    wrong = []
    for key, value in settings.DEFAULTS.items():
        if value not in ("true", "false"):
            continue
        if settings.get_bool(key) is not (value == "true"):
            wrong.append(key)
    assert not wrong, "these booleans do not read back as declared: %s" % wrong


def test_an_explicit_default_still_wins_when_unset(settings_module):
    from katan import settings
    assert settings.get_bool("nothing.declared.here", True) is True
    assert settings.get_bool("nothing.declared.here", False) is False


def test_a_written_value_beats_the_default(settings_module):
    from katan import settings
    settings_module.set("sources.autoplay", "false")
    assert settings.get_bool("sources.autoplay") is False


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def test_cache_round_trips_unicode_and_structure():
    from katan import cache
    payload = {"title": u"\u05e1\u05e8\u05d8", "items": [1, 2, {"a": None}]}
    cache.set("k", payload, 60)
    assert cache.get("k") == payload


def test_cache_respects_ttl():
    from katan import cache
    cache.set("k", {"v": 1}, 60)
    assert cache.get("k") is not None
    cache.set("expired", {"v": 1}, 1)
    time.sleep(1.1)
    assert cache.get("expired") is None


def test_cache_compresses_large_values():
    from katan import cache
    big = {"rows": [{"title": "x" * 200} for _ in range(100)]}
    cache.set("big", big, 60)
    assert cache.get("big") == big
    stats = cache.stats()
    assert stats["entries"] == 1
    # the compressed payload must be far smaller than the raw JSON
    assert stats["payload_bytes"] < 20000


def test_cached_helper_only_calls_the_producer_on_a_miss():
    from katan import cache
    calls = []

    def producer():
        calls.append(1)
        return {"v": len(calls)}

    first = cache.cached("key", producer, 60)
    second = cache.cached("key", producer, 60)
    assert first == second
    assert len(calls) == 1


def test_cached_helper_does_not_store_empty_results():
    from katan import cache
    assert cache.cached("empty", lambda: None, 60) is None
    assert cache.cached("empty", lambda: {"v": 1}, 60) == {"v": 1}


def test_prune_evicts_least_recently_used_when_over_the_cap():
    """Payloads must be incompressible, or zlib hides them from the size cap."""
    import binascii
    import os

    from katan import cache, settings
    settings.set("cache.max_mb", "10")
    for index in range(80):
        blob = binascii.hexlify(os.urandom(120000)).decode("ascii")
        cache.set("entry%02d" % index, {"blob": blob}, 3600)
    assert cache.stats()["payload_bytes"] > 10 * 1024 * 1024

    cache.get("entry00")           # make the oldest key the most recently used
    cache.prune()

    assert cache.stats()["entries"] < 80, "prune did not evict anything"
    assert cache.get("entry00") is not None, "prune evicted a recently used key"
    assert cache.stats()["payload_bytes"] <= 10 * 1024 * 1024


def test_make_key_is_stable_and_compact():
    from katan import cache
    assert cache.make_key("tmdb", "/movie", 5) == cache.make_key("tmdb", "/movie", 5)
    long_key = cache.make_key("x" * 300, u"\u05e2\u05d1\u05e8\u05d9\u05ea")
    assert len(long_key) < 130
    assert cache.make_key("a", "b") != cache.make_key("a", "c")


# --------------------------------------------------------------------------
# router
# --------------------------------------------------------------------------


def test_url_for_round_trips_through_parse_params():
    from katan import router
    url = router.url_for("episodes", tmdb=1399, season=2)
    assert url.startswith("plugin://plugin.video.katan/?")
    params = router.parse_params(url.split("?", 1)[1])
    assert params == {"action": "episodes", "tmdb": "1399", "season": "2"}


def test_url_for_drops_empty_values_but_keeps_zero():
    from katan import router
    params = router.parse_params(
        router.url_for("x", a=None, b="", c=0, d=False, e="v").split("?", 1)[1])
    assert "a" not in params and "b" not in params
    assert params["c"] == "0"
    assert params["d"] == "0"
    assert params["e"] == "v"


def test_all_routes_register_without_error():
    from katan import router
    router._load_handlers()
    actions = router.registered_actions()
    for expected in ("home", "row", "search", "seasons", "episodes", "movie",
                     "episode", "tools", "setup"):
        assert expected in actions


# --------------------------------------------------------------------------
# "Metadata cache (hours)", which used to be a switch that did nothing
# --------------------------------------------------------------------------


def test_the_metadata_cache_setting_actually_sets_the_ttl(settings_module):
    """It was in the settings dialog with a default of 6, and TTL_LIST was a
    constant of exactly six hours that nothing connected to it."""
    from katan.meta import tmdb

    assert tmdb.list_ttl() == 6 * 3600, "the shipped default"
    settings_module.set("cache.meta_hours", "24")
    assert tmdb.list_ttl() == 24 * 3600


def test_a_silly_metadata_cache_value_is_floored(settings_module):
    """An advanced setting should not let someone turn every home screen into
    a fresh round of network calls."""
    from katan.meta import tmdb

    settings_module.set("cache.meta_hours", "0")
    assert tmdb.list_ttl() == 600
    settings_module.set("cache.meta_hours", "not a number")
    assert tmdb.list_ttl() == 6 * 3600


def test_the_ttl_is_read_per_call_not_at_import(settings_module, monkeypatch):
    """A default argument is evaluated once and would never notice a change."""
    from katan import cache
    from katan.meta import tmdb

    monkeypatch.setattr(tmdb, "api_key", lambda: "k")
    monkeypatch.setattr(tmdb, "language", lambda: "en-GB")
    seen = []
    monkeypatch.setattr(cache, "cached",
                        lambda key, producer, ttl: seen.append(ttl) or {})

    settings_module.set("cache.meta_hours", "12")
    tmdb._call("/trending/movie/day")
    settings_module.set("cache.meta_hours", "3")
    tmdb._call("/trending/movie/day")

    assert seen == [12 * 3600, 3 * 3600]

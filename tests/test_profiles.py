"""Performance profiles, which is how a weak device gets sane defaults."""
import pytest

import xbmc
from katan import profiles, settings


def test_low_memory_actually_lowers_every_budget():
    """A profile that does not reduce load would be decoration."""
    low = profiles.LOW_MEMORY
    balanced = profiles.BALANCED
    assert int(low["sources.workers"]) < int(balanced["sources.workers"])
    assert int(low["cache.max_mb"]) < int(balanced["cache.max_mb"])
    assert int(low["sources.results"]) < int(balanced["sources.results"])
    assert int(low["sources.max_size_gb"]) < int(balanced["sources.max_size_gb"])
    assert int(low["subs.cache_files"]) < int(balanced["subs.cache_files"])
    assert int(low["sources.timeout"]) < int(balanced["sources.timeout"])


def test_no_profile_exceeds_the_ceiling_of_the_next_one_up():
    order = ["low_memory", "balanced", "powerful"]
    ranks = [settings.resolution_rank(
        profiles.PROFILES[name]["sources.max_resolution"]) for name in order]
    assert ranks == sorted(ranks)


def test_low_memory_reduces_concurrent_load_not_the_interface():
    """The device runs a full Kodi skin fine. What broke it was many scrapers
    and subtitle fetches at once, so that is what the profile cuts."""
    low = profiles.LOW_MEMORY
    balanced = profiles.BALANCED

    enabled_low = sum(1 for k, v in low.items()
                      if k.startswith("sources.provider.") and v == "true")
    enabled_balanced = sum(1 for k, v in balanced.items()
                           if k.startswith("sources.provider.") and v == "true")
    assert enabled_low <= enabled_balanced
    assert low["sources.prefetch_next"] == "false"
    assert low["sources.allow_hdr"] == "false"

    # The custom window is cheaper than the skin the device already runs.
    assert low["ui.window_home"] == "true"


def test_applying_a_profile_writes_the_settings():
    settings.set("sources.workers", "6")
    changed = profiles.apply("low_memory")
    assert changed > 0
    assert settings.get("sources.workers") == "2"
    assert profiles.current() == "low_memory"


def test_applying_the_same_profile_twice_changes_nothing():
    profiles.apply("balanced")
    assert profiles.apply("balanced") == 0


def test_an_unknown_profile_is_ignored():
    profiles.apply("balanced")
    assert profiles.apply("nonsense") == 0
    assert profiles.current() == "balanced"


@pytest.mark.parametrize("free,expected", [
    ("120MB", "low_memory"),
    ("800MB", "balanced"),
    ("1500MB", "balanced"),
])
def test_the_recommendation_follows_free_memory(monkeypatch, free, expected):
    monkeypatch.setattr(xbmc, "getInfoLabel",
                        lambda label: free if label == "System.FreeMemory" else "")
    name, _why = profiles.recommend()
    assert name == expected


def test_a_projector_that_already_runs_a_full_build_gets_balanced(monkeypatch):
    """The U4 runs Kodi with a skin and several add-ons, so it is not fragile."""
    labels = {"System.FreeMemory": "800MB", "System.ScreenMode": "1280x720p"}
    monkeypatch.setattr(xbmc, "getInfoLabel", lambda label: labels.get(label, ""))
    name, _why = profiles.recommend()
    assert name == "balanced"


def test_every_profile_key_is_a_real_setting():
    """A typo here would silently do nothing."""
    unknown = set()
    for values in profiles.PROFILES.values():
        unknown |= set(values) - set(settings.DEFAULTS)
    assert not unknown, sorted(unknown)


def test_all_profiles_set_the_same_keys():
    """Switching profiles must not leave a stale value from the previous one."""
    keys = [set(values) for values in profiles.PROFILES.values()]
    assert keys[0] == keys[1] == keys[2]


def test_low_memory_shrinks_the_artwork_budget():
    """Kodi caches decoded bitmaps, so poster width is the real memory lever."""
    widths = {"w185": 185, "w342": 342, "w500": 500}
    low = widths[profiles.LOW_MEMORY["ui.poster_size"]]
    balanced = widths[profiles.BALANCED["ui.poster_size"]]
    assert low < balanced

    # A poster is roughly width * 1.5 height * 4 bytes once decoded.
    def megabytes(width, rows, per_row):
        return width * (width * 1.5) * 4 * rows * per_row / (1024.0 * 1024.0)

    low_mb = megabytes(low, 3, int(profiles.LOW_MEMORY["ui.row_items"]))
    balanced_mb = megabytes(balanced, 3, int(profiles.BALANCED["ui.row_items"]))
    assert low_mb < balanced_mb / 2, "the low profile should roughly halve it"


def test_the_poster_size_setting_is_actually_used(settings_module):
    from katan.meta import items

    settings_module.set("ui.poster_size", "w185")
    assert "/w185/" in items.image_url("/abc.jpg")
    settings_module.set("ui.poster_size", "w342")
    assert "/w342/" in items.image_url("/abc.jpg")


def test_an_absolute_url_is_left_alone(settings_module):
    url = "https://example.com/poster.jpg"
    from katan.meta import items
    assert items.image_url(url) == url


def test_the_row_length_setting_is_actually_used(settings_module):
    from katan import catalog

    settings_module.set("ui.row_items", "12")
    assert catalog.row_limit() == 12
    settings_module.set("ui.row_items", "3")
    assert catalog.row_limit() >= 6, "a floor stops a row becoming useless"


def test_every_profile_can_describe_itself():
    """The description sits beside a Hebrew label in the chooser."""
    from katan import profiles

    for name in profiles.PROFILES:
        text = profiles.describe(name)
        assert text and "%" not in text, name
        assert profiles.PROFILES[name]["sources.max_resolution"] in text

    assert profiles.describe("no such profile") == ""


def test_the_recommendation_comes_with_a_readable_reason(monkeypatch):
    """It is shown to the viewer, so it goes through the string table."""
    from katan import profiles

    monkeypatch.setattr(profiles, "_free_megabytes", lambda: 200)
    name, why = profiles.recommend()
    assert name == "low_memory"
    assert why and why != "32405", "the string id itself is not a reason"
    assert "200" in why

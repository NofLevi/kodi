"""The source pipeline: merging duplicates, filtering, and ranking."""
import pytest

from katan.sources import model, scoring


def make(title, **kwargs):
    defaults = {"size": 4 * 1024 ** 3, "seeders": 40}
    defaults.update(kwargs)
    info_hash = defaults.pop("info_hash", None) or ("%040x" % abs(hash(title)))
    return model.from_release_name(title, provider=defaults.pop("provider", "test"),
                                   info_hash=info_hash, **defaults)


@pytest.fixture(autouse=True)
def rank_uncached_too(settings_module):
    """Let the ranking tests see uncached sources.

    sources.cached_only really does default to true, and rejection_reason
    drops an uncached source outright, so with the shipped default a test
    about size or language or release group would rank an empty list. The
    tests that are about caching set this themselves and override this.

    These tests were passing for the wrong reason until get_bool was fixed:
    an unset boolean used to read false regardless of DEFAULTS, so nothing
    was ever filtered.
    """
    settings_module.set("sources.cached_only", "false")


# --------------------------------------------------------------------------
# hashes and merging
# --------------------------------------------------------------------------


def test_normalise_hash_accepts_magnets_and_bare_hashes():
    raw = "AABBCCDDEEFF00112233445566778899AABBCCDD"
    assert model.normalise_hash(raw) == raw.lower()
    assert model.normalise_hash("magnet:?xt=urn:btih:%s&dn=x" % raw) == raw.lower()
    assert model.normalise_hash("") == ""
    assert model.normalise_hash("not a hash") == ""


def test_normalise_hash_converts_base32_magnets():
    """Older magnets carry a 32 character base32 hash instead of hex."""
    base32 = "VUV4Z3PO7YARCIZTIRKWM54ETGVLXTG5"
    result = model.normalise_hash("magnet:?xt=urn:btih:%s" % base32)
    assert len(result) == 40
    assert all(c in "0123456789abcdef" for c in result)


def test_dedupe_merges_the_same_torrent_from_several_providers():
    shared = "1" * 40
    partial = make("Movie.2024.1080p.WEB-DL-X", info_hash=shared, size=0,
                   seeders=0, provider="a")
    detailed = make("Movie.2024.1080p.WEB-DL.DDP5.1.H264-FLUX", info_hash=shared,
                    size=6 * 1024 ** 3, seeders=120, provider="b")
    detailed["cached"] = True
    detailed["cached_by"] = "torbox"

    merged = model.dedupe([partial, detailed])
    assert len(merged) == 1
    row = merged[0]
    assert row["size"] == 6 * 1024 ** 3, "the known size should win"
    assert row["seeders"] == 120
    assert row["cached"] and row["cached_by"] == "torbox"
    assert row["group"] == "flux", "the more detailed title should win"
    assert set(row["providers"]) == {"a", "b"}


def test_dedupe_keeps_genuinely_different_torrents():
    first = make("Movie.2024.1080p.WEB-DL-A", info_hash="a" * 40)
    second = make("Movie.2024.2160p.WEB-DL-B", info_hash="b" * 40)
    assert len(model.dedupe([first, second])) == 2


def test_magnet_is_built_from_a_hash_when_absent():
    source = make("Movie.2024.1080p.WEB-DL-X", info_hash="c" * 40)
    magnet = model.magnet_for(source, trackers=["udp://tracker"])
    assert magnet.startswith("magnet:?xt=urn:btih:" + "c" * 40)
    assert "tr=udp://tracker" in magnet


# --------------------------------------------------------------------------
# filtering
# --------------------------------------------------------------------------


@pytest.fixture
def prefs(settings_module):
    settings_module.set_many({
        "sources.max_resolution": "1080p",
        "sources.min_resolution": "480p",
        "sources.max_size_gb": "12",
        "sources.allow_hevc": "true",
        "sources.allow_av1": "false",
        "sources.allow_hdr": "false",
        "sources.allow_cam": "false",
        "sources.cached_only": "false",
        "sources.prefer_hebrew": "true",
        "sources.size_preference": "balanced",
        "sources.results": "8",
    })
    return scoring.Preferences()


def test_resolution_ceiling_is_enforced(prefs):
    too_big = make("Movie.2024.2160p.WEB-DL.H264-X")
    assert "above the resolution limit" in scoring.rejection_reason(too_big, prefs)


def test_disabled_codecs_are_rejected(prefs):
    assert "AV1" in scoring.rejection_reason(make("Movie.2024.1080p.WEB.AV1-X"), prefs)
    assert scoring.rejection_reason(make("Movie.2024.1080p.WEB.x265-X"), prefs) == ""


def test_hdr_is_rejected_when_the_device_cannot_show_it(prefs):
    hdr = make("Movie.2024.1080p.WEB-DL.HDR.H264-X")
    assert "HDR" in scoring.rejection_reason(hdr, prefs)


def test_cam_releases_are_rejected(prefs):
    assert "cam" in scoring.rejection_reason(make("Movie.2024.HDCAM.x264-X"), prefs)


def test_oversized_files_are_rejected(prefs):
    huge = make("Movie.2024.1080p.BluRay.REMUX-X", size=40 * 1024 ** 3)
    assert "size limit" in scoring.rejection_reason(huge, prefs)


def test_implausibly_small_files_are_rejected(prefs):
    """A 40 MB file claiming to be 1080p is mislabelled or a fake."""
    tiny = make("Movie.2024.1080p.WEB-DL-X", size=40 * 1024 ** 2)
    assert "too small" in scoring.rejection_reason(tiny, prefs)


def test_cached_only_hides_uncached_sources(settings_module):
    settings_module.set("sources.cached_only", "true")
    prefs = scoring.Preferences()
    uncached = make("Movie.2024.1080p.WEB-DL-X")
    cached = make("Movie.2024.1080p.WEB-DL-Y")
    cached["cached"] = True
    assert scoring.rejection_reason(uncached, prefs) == "not cached"
    assert scoring.rejection_reason(cached, prefs) == ""


# --------------------------------------------------------------------------
# ranking
# --------------------------------------------------------------------------


def test_cached_sources_always_beat_uncached_ones(prefs, settings_module):
    """On a weak box, ready-to-play beats every other consideration."""
    best_uncached = make("Movie.2024.1080p.BluRay.x264-FLUX", seeders=5000)
    worse_cached = make("Movie.2024.720p.WEB-DL.x264-NOBODY", seeders=2)
    worse_cached["cached"] = True
    worse_cached["cached_by"] = "torbox"

    ranked, _ = scoring.rank([best_uncached, worse_cached])
    assert ranked[0] is worse_cached


def test_ranking_prefers_the_resolution_ceiling(prefs):
    low = make("Movie.2024.480p.WEB-DL-X")
    high = make("Movie.2024.1080p.WEB-DL-X")
    ranked, _ = scoring.rank([low, high])
    assert ranked[0]["quality"] == "1080p"


def test_hebrew_releases_are_promoted_when_preferred(settings_module):
    settings_module.set("sources.prefer_hebrew", "true")
    plain = make("Movie.2024.1080p.WEB-DL.H264-AAA")
    hebrew = make("Movie.2024.1080p.WEB-DL.HebSub.H264-BBB")
    ranked, _ = scoring.rank([plain, hebrew])
    assert "he" in ranked[0]["languages"]


def test_a_remembered_release_group_wins_a_close_call(settings_module, monkeypatch):
    """Keeping the same group across episodes keeps subtitles consistent."""
    settings_module.set("sources.source_memory", "true")
    monkeypatch.setattr(scoring, "remembered_group",
                        lambda meta: {"group": "flux"})
    other = make("Show.S01E02.1080p.WEB-DL.H264-NTB", seeders=900)
    same = make("Show.S01E02.1080p.WEB-DL.H264-FLUX", seeders=10)
    ranked, _ = scoring.rank([other, same], meta={"ids": {"tmdb": 1}})
    assert ranked[0]["group"] == "flux"


def test_rank_limits_how_much_is_returned(settings_module):
    settings_module.set("sources.results", "3")
    sources = [make("Movie.2024.1080p.WEB-DL.H264-G%d" % n) for n in range(20)]
    ranked, _ = scoring.rank(sources)
    assert len(ranked) == 3


def test_rank_reports_why_things_were_dropped(settings_module):
    settings_module.set("sources.allow_hdr", "false")
    sources = [
        make("Movie.2024.1080p.WEB-DL.HDR.H264-A"),
        make("Movie.2024.HDCAM.x264-B"),
        make("Movie.2024.1080p.WEB-DL.H264-C"),
    ]
    ranked, rejected = scoring.rank(sources)
    assert len(ranked) == 1
    assert rejected.get("HDR is switched off") == 1
    assert rejected.get("cam release") == 1


def test_balanced_size_preference_avoids_the_extremes(settings_module):
    """A moderate bitrate streams better than a remux on weak wifi."""
    settings_module.set_many({"sources.size_preference": "balanced",
                              "sources.max_size_gb": "80"})
    tiny = make("Movie.2024.1080p.WEB-DL.H264-A", size=900 * 1024 ** 2)
    middle = make("Movie.2024.1080p.WEB-DL.H264-B", size=7 * 1024 ** 3)
    huge = make("Movie.2024.1080p.WEB-DL.H264-C", size=23 * 1024 ** 3)
    ranked, _ = scoring.rank([tiny, middle, huge])
    assert ranked[0] is middle


def test_smallest_size_preference_picks_the_smallest(settings_module):
    settings_module.set_many({"sources.size_preference": "smallest",
                              "sources.max_size_gb": "80"})
    small = make("Movie.2024.1080p.WEB-DL.H264-A", size=2 * 1024 ** 3)
    huge = make("Movie.2024.1080p.WEB-DL.H264-C", size=20 * 1024 ** 3)
    ranked, _ = scoring.rank([huge, small])
    assert ranked[0] is small


def test_label_is_readable():
    source = make("Movie.2024.1080p.WEB-DL.x265-FLUX", size=5 * 1024 ** 3,
                  seeders=88)
    text = model.label(source)
    assert "1080P" in text and "5.00 GB" in text and "FLUX" in text

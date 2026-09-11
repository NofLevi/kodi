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


DV_ONLY = "Movie.2024.1080p.WEB-DL.DV.H265-GRP"


def test_dolby_vision_alone_is_its_own_decision(settings_module, prefs):
    """Allowing HDR says nothing about Dolby Vision with no fallback.

    A release with an HDR10 or HDR10+ layer beside Dolby Vision plays that
    layer on a screen without it. One with Dolby Vision alone has nothing to
    fall back to, and on a display that cannot decode it the picture comes
    out purple and green.
    """
    settings_module.set("sources.allow_hdr", "true")
    hdr_on = scoring.Preferences()

    assert scoring.rejection_reason(make(DV_ONLY), hdr_on) == \
        "Dolby Vision without an HDR10 fallback"
    assert scoring.rejection_reason(
        make("Movie.2024.1080p.WEB-DL.DV.HDR10.H265-GRP"), hdr_on) == ""
    assert scoring.rejection_reason(
        make("Movie.2024.1080p.WEB-DL.DV.HDR10+.H265-GRP"), hdr_on) == ""

    settings_module.set("sources.allow_dv", "true")
    assert scoring.rejection_reason(make(DV_ONLY), scoring.Preferences()) == ""


def test_hdr_switched_off_still_explains_a_dolby_vision_release(prefs):
    """The broader reason wins: with HDR off, "HDR is switched off" is the
    one that tells somebody what to change."""
    assert scoring.rejection_reason(make(DV_ONLY), prefs) == \
        "HDR is switched off"


def test_every_rejection_reason_has_a_label():
    """The picker translates each reason it reports. A reason added without
    a label is shown to a Hebrew-speaking household in English."""
    import inspect
    import re

    from katan.ui import sources_window

    reasons = set(re.findall(r'return "([^"]+)"',
                             inspect.getsource(scoring.rejection_reason)))
    assert reasons, "no reasons found - has rejection_reason changed shape?"
    missing = reasons - set(sources_window.REASON_STRINGS)
    assert not missing, "no label for %s" % sorted(missing)


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


# --------------------------------------------------------------------------
# the order the viewer asked for: resolution, then subtitles, then size
# --------------------------------------------------------------------------


def test_resolution_decides_before_anything_else(settings_module):
    """The best picture that is allowed, first."""
    settings_module.set_many({"sources.max_resolution": "2160p",
                              "sources.min_resolution": "720p",
                              "sources.max_size_gb": "80"})
    small720 = make("Movie.2024.720p.WEB-DL-A", size=1 * 1024 ** 3)
    big1080 = make("Movie.2024.1080p.WEB-DL-B", size=9 * 1024 ** 3)
    ranked, _ = scoring.rank([small720, big1080])
    assert ranked[0] is big1080, \
        "a smaller file must not beat a better picture"


def test_the_resolution_ceiling_is_what_highest_means(settings_module):
    """"Highest" means highest allowed. That is what the setting is for."""
    settings_module.set_many({"sources.max_resolution": "1080p",
                              "sources.min_resolution": "720p",
                              "sources.max_size_gb": "80"})
    ranked, rejected = scoring.rank([
        make("Movie.2024.2160p.WEB-DL-A", size=20 * 1024 ** 3),
        make("Movie.2024.1080p.WEB-DL-B", size=9 * 1024 ** 3)])
    assert len(ranked) == 1 and "1080p" in ranked[0]["title"]
    assert "above the resolution limit" in rejected


def test_subtitles_decide_between_equal_pictures(settings_module):
    """Same resolution, so the one with subtitles that fit wins - which for
    a Hebrew-speaking household is what makes it watchable at all."""
    settings_module.set_many({"sources.max_size_gb": "80",
                              "sources.min_resolution": "720p"})
    poor = make("Movie.2024.1080p.WEB-DL-A", size=2 * 1024 ** 3)
    good = make("Movie.2024.1080p.WEB-DL-B", size=8 * 1024 ** 3)
    poor["subs_kind"], poor["subs_score"] = "external", 20
    good["subs_kind"], good["subs_score"] = "external", 95

    ranked, _ = scoring.rank([poor, good])
    assert ranked[0] is good, \
        "better subtitles must win even though the file is larger"


def test_a_release_carrying_hebrew_counts_as_a_perfect_match(settings_module):
    settings_module.set_many({"sources.max_size_gb": "80",
                              "sources.min_resolution": "720p"})
    embedded = make("Movie.2024.1080p.HebSub-A", size=8 * 1024 ** 3)
    external = make("Movie.2024.1080p.WEB-DL-B", size=2 * 1024 ** 3)
    embedded["subs_kind"], embedded["subs_score"] = "embedded", 0
    external["subs_kind"], external["subs_score"] = "external", 90

    ranked, _ = scoring.rank([embedded, external])
    assert ranked[0] is embedded, \
        "a release that needs no external subtitle is the best outcome"


def test_the_smallest_file_breaks_the_tie(settings_module):
    """Equal picture, equal subtitles: the one that starts soonest and takes
    least of a small device's cache."""
    settings_module.set_many({"sources.size_preference": "smallest",
                              "sources.min_resolution": "720p",
                              "sources.max_size_gb": "80"})
    light = make("Movie.2024.1080p.WEB-DL-A", size=2 * 1024 ** 3)
    heavy = make("Movie.2024.1080p.WEB-DL-B", size=18 * 1024 ** 3)
    for entry in (light, heavy):
        entry["subs_kind"], entry["subs_score"] = "external", 90

    ranked, _ = scoring.rank([heavy, light])
    assert ranked[0] is light


def test_a_cached_source_still_wins_everything(settings_module):
    """On a device that cannot wait for a download, an uncached source is
    not a slightly worse option - it is a different thing."""
    settings_module.set_many({"sources.cached_only": "false",
                              "sources.min_resolution": "720p",
                              "sources.max_resolution": "2160p",
                              "sources.max_size_gb": "80"})
    uncached = make("Movie.2024.2160p.WEB-DL-A", size=20 * 1024 ** 3)
    cached = make("Movie.2024.720p.WEB-DL-B", size=1 * 1024 ** 3)
    # `make` funnels anything it does not recognise into `extra`, so these
    # have to be set on the source itself rather than passed in.
    cached["cached"] = True
    uncached["cached"] = False
    uncached["subs_kind"], uncached["subs_score"] = "external", 100

    ranked, _ = scoring.rank([uncached, cached])
    assert ranked[0] is cached


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


# --------------------------------------------------------------------------
# the same file in two different torrents
#
# The infohash cannot see this and it is what the viewer is actually looking
# at: a popular release gets re-uploaded, and each upload is a different
# torrent of byte-identical content. Measured on a real search, Silo S01E01
# had one file occupying positions 1 to 8 - and the picker shows six rows, so
# the viewer was offered one option six times and told it was six.
# --------------------------------------------------------------------------

TWIN = "The.Matrix.1999.1080p.BrRip.x264.YIFY.mp4"
GB = 1024 ** 3


def test_the_same_file_in_two_torrents_becomes_one_row():
    merged = model.dedupe([
        make(TWIN, size=1990000000, info_hash="a" * 40),
        make(TWIN, size=1990000000, info_hash="b" * 40),
    ])
    assert len(merged) == 1


def test_the_survivor_keeps_every_provider_that_had_it():
    """The picker credits the providers, and losing one would make a second
    scraper look as though it had found nothing."""
    merged = model.dedupe([
        make(TWIN, size=1990000000, info_hash="a" * 40, provider="torrentio"),
        make(TWIN, size=1990000000, info_hash="b" * 40, provider="torrentsdb"),
    ])
    assert sorted(merged[0]["providers"]) == ["torrentio", "torrentsdb"]


def test_the_survivor_keeps_the_best_seeder_count():
    merged = model.dedupe([
        make(TWIN, size=1990000000, info_hash="a" * 40, seeders=12),
        make(TWIN, size=1990000000, info_hash="b" * 40, seeders=2081),
    ])
    assert merged[0]["seeders"] == 2081


def test_one_cached_copy_makes_the_row_cached():
    """Being cached is what decides whether a source can play at all, so it
    must survive the merge whichever copy carried it."""
    first = make(TWIN, size=1990000000, info_hash="a" * 40)
    second = make(TWIN, size=1990000000, info_hash="b" * 40)
    second["cached"] = True
    second["cached_by"] = "torbox"

    merged = model.dedupe([first, second])

    assert merged[0]["cached"] is True
    assert merged[0]["cached_by"] == "torbox"


def test_a_few_bytes_apart_is_still_the_same_file():
    """One provider counts the torrent and another the file inside it."""
    merged = model.dedupe([
        make(TWIN, size=1990000000, info_hash="a" * 40),
        make(TWIN, size=1990000512, info_hash="b" * 40),
    ])
    assert len(merged) == 1


def test_the_same_name_at_a_different_size_is_a_different_encode():
    """Matching on the name alone would let a 1080p rip eat the 720p one that
    shares its name, which is the opposite of showing a viewer their options."""
    merged = model.dedupe([
        make("The.Matrix.1999.mkv", size=2 * GB, info_hash="a" * 40),
        make("The.Matrix.1999.mkv", size=8 * GB, info_hash="b" * 40),
    ])
    assert len(merged) == 2


def test_the_same_size_with_a_different_name_is_a_different_film():
    merged = model.dedupe([
        make("The.Matrix.1999.1080p.mkv", size=2 * GB, info_hash="a" * 40),
        make("Dune.Part.Two.2024.1080p.mkv", size=2 * GB, info_hash="b" * 40),
    ])
    assert len(merged) == 2


def test_a_source_with_no_size_is_left_alone():
    """There is nothing to confirm the name with, and collapsing on a name by
    itself is how a good release disappears into a badly named one."""
    merged = model.dedupe([
        make(TWIN, size=0, info_hash="a" * 40),
        make(TWIN, size=0, info_hash="b" * 40),
    ])
    assert len(merged) == 2


def test_the_same_torrent_still_merges_by_infohash():
    """The exact answer, and still the first one applied."""
    merged = model.dedupe([
        make(TWIN, size=1990000000, info_hash="a" * 40, provider="torrentio"),
        make(TWIN, size=0, info_hash="a" * 40, provider="comet", seeders=99),
    ])
    assert len(merged) == 1
    assert merged[0]["seeders"] == 99


def test_a_re_upload_renamed_on_the_way_is_the_same_file():
    """These two sat one above the other in the picker for a Silo episode."""
    merged = model.dedupe([
        make("silo.s01e01.1080p.web.h264-ggwp.mkv",
             size=4885000000, info_hash="a" * 40),
        make("silo.s01e01.1080p.web.h264-ggwp[eztv.re].mkv",
             size=4885000000, info_hash="b" * 40),
    ])
    assert len(merged) == 1


def test_a_prefix_on_the_show_name_does_not_make_a_new_release():
    merged = model.dedupe([
        make("Silo.S01E01.Freedom.Day.MULTi.1080p.ATVP.WEB-DL.DD5.1.H264-Ralf.mkv",
             size=5150000000, info_hash="a" * 40),
        make("Silo8.S01E01.Freedom.Day.MULTi.1080p.ATVP.WEB-DL.DD5.1.H264-Ralf.mkv",
             size=5150000000, info_hash="b" * 40),
    ])
    assert len(merged) == 1


def test_the_same_group_at_a_different_resolution_is_a_different_release():
    """A group publishes several encodes and a viewer choosing between them
    is the whole point of the picker."""
    merged = model.dedupe([
        make("Show.S01E01.1080p.WEB.H264-NTb.mkv",
             size=3 * GB, info_hash="a" * 40),
        make("Show.S01E01.720p.WEB.H264-NTb.mkv",
             size=3 * GB, info_hash="b" * 40),
    ])
    assert len(merged) == 2


def test_two_groups_at_the_same_size_stay_apart():
    merged = model.dedupe([
        make("Show.S01E01.1080p.WEB.H264-NTb.mkv",
             size=3 * GB, info_hash="a" * 40),
        make("Show.S01E01.1080p.WEB.H264-GGWP.mkv",
             size=3 * GB, info_hash="b" * 40),
    ])
    assert len(merged) == 2


def test_an_unnamed_reupload_joins_the_one_group_it_can_belong_to():
    """A name mangled past the point where the group can be read, beside the
    release it came from at the same size."""
    merged = model.dedupe([
        make("Silo.S01E01.MULTi.1080p.WEB-DL.H264-Ralf.mkv",
             size=5150000000, info_hash="a" * 40),
        make("Silo.S01E01.MULTi.1080p.WEB-DL.H264-Ralf-PSOTNIK HT.mkv",
             size=5150000000, info_hash="b" * 40),
    ])
    assert len(merged) == 1
    assert merged[0]["group"] == "ralf"


def test_it_does_not_join_when_it_could_belong_to_either():
    """Measured on a real search, six releases of one Silo episode share
    4977 MB - LostFilm, EniaHD, an Italian one, a Spanish one. An unnamed
    source at that size could be any of them, and folding it into whichever
    came first would hide a language somebody needs."""
    merged = model.dedupe([
        make("Silo.S01E01.1080p.WEB-DL.H264-LostFilm.mkv",
             size=5220000000, info_hash="a" * 40),
        make("Silo.S01E01.1080p.WEB-DL.H264-EniaHD.mkv",
             size=5220000000, info_hash="b" * 40),
        make("Silo - Temporada 1 [WEB-DL 1080p][Dual].mkv",
             size=5220000000, info_hash="c" * 40),
    ])
    assert len(merged) == 3


def test_a_site_stamp_does_not_make_a_second_row():
    """"[ OxTorrent.com ] Les evades (1994) - 1080p" is the same file as
    "Les evades (1994) - 1080p", and both were in the picker."""
    merged = model.dedupe([
        make("Les evades (1994) - 1080p FR EN x264 ac3 mHDgz.mkv",
             size=3790000000, info_hash="a" * 40),
        make("[ OxTorrent.com ] Les evades (1994) - 1080p FR EN x264 ac3 mHDgz.mkv",
             size=3790000000, info_hash="b" * 40),
    ])
    assert len(merged) == 1


# --------------------------------------------------------------------------
# a release that says what language it is in
# --------------------------------------------------------------------------

def _scored(title, **extra):
    from katan.sources import scoring
    from katan.utils import release

    parsed = release.parse(title)
    source = {"title": title, "resolution": parsed["resolution"],
              "codec": parsed["codec"], "languages": parsed["languages"],
              "group": parsed["group"], "size": 2 * 1024 ** 3, "seeders": 50}
    source.update(extra)
    return scoring.score(source, scoring.Preferences(), 1.0)


def test_a_foreign_release_loses_to_an_ordinary_one(settings_module):
    """This picked an Italian dub of a Korean series and played it.

    "Mousetrap.Identita.Rubata.1x02.Episodio.02.ITA.KOR..." parsed to no
    languages at all, because the table only knew Hebrew, English and multi -
    so nothing could rank it below the releases somebody could actually watch,
    and autoplay took it. The viewer got Italian audio and two Italian
    subtitle tracks out of the file.
    """
    settings_module.set("subs.languages", "he,en")
    foreign = _scored("Mousetrap.Identita.Rubata.1x02.Episodio.02.ITA.KOR."
                      "1080p.NFRip.AAC.x265-Pir8")
    ordinary = _scored("Mousetrap.S01E02.1080p.WEB.h264-ETHEL")
    assert foreign < ordinary, "%s vs %s" % (foreign, ordinary)


def test_a_release_claiming_nothing_is_not_penalised(settings_module):
    """Most releases name no language, and an empty list means "not stated".

    Reading it as "not English" would push the ordinary case below everything
    and invert the whole ranking.
    """
    from katan.sources import scoring

    settings_module.set("subs.languages", "he,en")
    plain = {"title": "Silo.S01E01.1080p.WEB.H264-CAKES", "languages": [],
             "resolution": "1080p", "size": 2 * 1024 ** 3, "seeders": 50}
    assert not scoring._wrong_language(plain, scoring.Preferences())


@pytest.mark.parametrize("languages,penalised", [
    (["it", "ko"], True),
    (["es"], True),
    (["tr"], True),
    # Neutral: a multi-audio release usually carries the original track, and
    # English is readable by anyone who got this far.
    (["multi"], False),
    (["en"], False),
    (["it", "en"], False),
    # Hebrew is always acceptable - it is why this add-on exists.
    (["he"], False),
    (["he", "ru"], False),
    ([], False),
])
def test_which_language_claims_count_as_wrong(settings_module, languages,
                                              penalised):
    from katan.sources import scoring

    settings_module.set("subs.languages", "he,en")
    source = {"title": "x", "languages": languages}
    assert scoring._wrong_language(source, scoring.Preferences()) is penalised


def test_the_language_list_is_the_viewers_own(settings_module):
    """Somebody who reads Spanish should not have Spanish ranked down."""
    from katan.sources import scoring

    settings_module.set("subs.languages", "es")
    source = {"title": "x", "languages": ["es"]}
    assert not scoring._wrong_language(source, scoring.Preferences())


# --------------------------------------------------------------------------
# foreign-language drama, which is watched here in its own language
# --------------------------------------------------------------------------

def _score_for(title, original, languages="he,en"):
    from katan.sources import scoring
    from katan.utils import release

    import katan.settings as settings_module
    settings_module.set("subs.languages", languages)
    parsed = release.parse(title)
    source = {"title": title, "quality": parsed["resolution"],
              "codec": parsed["codec"], "languages": parsed["languages"],
              "group": parsed["group"], "size": 2 * 1024 ** 3, "seeders": 80}
    prefs = scoring.Preferences()
    prefs.original_language = original
    return scoring.score(source, prefs, 1.0)


def test_a_shows_own_language_is_never_the_wrong_language(settings_module):
    """Spanish, Turkish and Italian dramas are watched here, in their own
    language, with Hebrew subtitles.

    The wrong-language rule read every honest release of them as unwatchable:
    a Turkish serial scored -210 against +190 for an English redub of the same
    episode, so the dub won. Nothing was ever *removed* - the rule only sorts,
    and `rejection_reason` is what filters - but the answer was backwards for
    the person actually watching.
    """
    turkish = _score_for("Kurulus.Osman.S05E01.TURKISH.1080p.WEB-DL.x264-GRP",
                         original="tr")
    dubbed = _score_for("Kurulus.Osman.S05E01.ENGLISH.DUB.1080p.WEB-DL.x264-GRP",
                        original="tr")
    assert turkish > dubbed, "%s vs %s" % (turkish, dubbed)


@pytest.mark.parametrize("original,title", [
    ("es", "La.Casa.de.Papel.S01E01.SPANISH.1080p.NF.WEB-DL-NTb"),
    ("it", "Mare.Fuori.S01E01.ITALIAN.1080p.WEB-DL.x264-GRP"),
    ("tr", "Kurulus.Osman.S05E01.TURKISH.1080p.WEB-DL.x264-GRP"),
])
def test_the_original_beats_an_untagged_release_of_a_foreign_show(
        settings_module, original, title):
    plain = _score_for("Some.Show.S01E01.1080p.WEB-DL.x264-GRP", original)
    assert _score_for(title, original) > plain


def test_an_english_show_is_untouched_by_any_of_this(settings_module):
    """The preference applies only when the show's language is one the viewer
    does not read. For an English series every release is "the original", and
    a bonus applied to everything is the same as no bonus and only slower."""
    plain = _score_for("Silo.S01E01.1080p.WEB.H264-CAKES", original="en")
    tagged = _score_for("Silo.S01E01.ENGLISH.1080p.WEB.H264-CAKES",
                        original="en")
    italian = _score_for("Silo.S01E01.ITA.1080p.WEB.H264-CAKES", original="en")
    assert plain == tagged
    assert italian < plain, "an Italian dub of an English show still sinks"


def test_the_original_language_reaches_the_item():
    """It comes from TMDB and nothing carried it before."""
    from katan.meta import items

    movie = items.from_tmdb_movie({"id": 1, "title": "x",
                                   "original_language": "tr"})
    assert movie["original_language"] == "tr"
    assert items.new_item("movie")["original_language"] == ""

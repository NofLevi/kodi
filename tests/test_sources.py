"""The source pipeline: merging duplicates, filtering, and ranking."""
import pytest

from pinky.sources import model, scoring


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
    a label is shown to a Hebrew-speaking household in English.

    Scans every function `rank()` actually combines into its rejection dict,
    not just `rejection_reason` alone - "another series of the same name"
    was added to `_a_different_series` and reached a real screen in English
    before this test was widened to see it, because the narrower scan only
    ever looked at one of the four functions that can produce a reason.
    """
    import inspect
    import re

    from pinky.ui import sources_window

    reasons = set()
    for fn in (scoring.rejection_reason, scoring._another_production,
              scoring._a_different_series, scoring._a_different_film):
        reasons |= set(re.findall(r'return "([^"]+)"', inspect.getsource(fn)))
    assert reasons, "no reasons found - has rank()'s reason chain changed shape?"
    missing = reasons - set(sources_window.REASON_STRINGS)
    assert not missing, "no label for %s" % sorted(missing)


# The anime engine. Each of these is a real anime release that the
# shared check used to reject; they are pinned to the anime engine so a
# change to ordinary television cannot reach them.
ANIME = {"anime": True}


def test_a_macron_in_tmdbs_title_does_not_reject_the_real_series():
    """TMDB spells this show "Naruto Shippūden"; every release spells it
    "Shippuuden" or "Shippuden" in plain ASCII. Before `release.normalise`
    folded diacritics, that macron was a word no release could ever match,
    so `_a_different_series` treated every real Naruto Shippuden release as
    a different show sharing the name - live, 28 of 29 sources for Naruto
    Shippuden 12x244 were hidden this way, 17 of them by this exact reason."""
    meta = {"type": "episode", "extra": ANIME, "title": "Naruto Shippūden"}
    source = {"title": "[TorrentsDB] Naruto Shippuuden - 244 - "
                        "Killer Bee and Motoi"}
    assert scoring._a_different_series(source, meta) == ""


def test_a_genuinely_different_series_is_still_caught():
    """The fold only removes a diacritic mismatch - it must not blunt the
    check `_a_different_series` exists for: Shippuuden really is a different
    show from a plain "Naruto" query."""
    meta = {"type": "episode", "extra": ANIME, "title": "Naruto"}
    source = {"title": "Naruto Shippuuden 106"}
    assert scoring._a_different_series(source, meta) == \
        "another series of the same name"


def test_a_romaji_alias_is_not_a_different_series():
    """TMDB's own title for a show is English; a release named after its
    Japanese romaji title - "Shingeki no Kyojin" for Attack on Titan - is
    the same show under its other name, not a different one sharing this
    one. `meta["aliases"]` is where `play._name_it_the_way_the_indexes_do`
    puts that name for exactly this check to read."""
    meta = {"type": "episode", "extra": ANIME, "title": "Attack on Titan",
           "aliases": ["Shingeki no Kyojin"]}
    source = {"title": "[Erai-raws] Shingeki no Kyojin - 01 "
                        "[1080p][Multiple Subtitle].mkv"}
    assert scoring._a_different_series(source, meta) == ""


def test_stray_punctuation_does_not_split_a_matching_word():
    """A colon, an apostrophe or a bare hyphen glues onto its neighbour
    rather than separating two words, so "Boruto:" (TMDB's own title) and
    "Boruto" (the release) were permanently different tokens, and "Re:ZERO"
    with no space collapsed into one token nothing could ever match against
    a release spelling it "Re Zero"."""
    meta = {"type": "episode", "extra": ANIME, "title": "Boruto: Naruto Next Generations"}
    assert scoring._a_different_series(
        {"title": "[Judas] Boruto - 01.mkv"}, meta) == ""

    meta = {"type": "episode", "extra": ANIME, "title": "Re:ZERO -Starting Life "
                                        "in Another World-"}
    assert scoring._a_different_series(
        {"title": "[Anime Time] Re Zero - 01.mkv"}, meta) == ""


def test_a_four_digit_episode_number_is_recognised():
    """One Piece is past a thousand episodes and a release pads to match:
    "S01E0001" has four digits after the E, one more than the marker used
    to allow, so it was never recognised as an episode number at all and
    the episode's own subtitle was read as the name of a second show."""
    meta = {"type": "episode", "extra": ANIME, "title": "One Piece", "aliases": ["One Piece"]}
    source = {"title": "One Piece - S01E0001 - I'm Luffy! The Man Who's "
                        "Gonna Be King of the Pirates!.mkv"}
    assert scoring._a_different_series(source, meta) == ""


def test_a_bare_episode_marker_with_no_season_is_recognised():
    """"EP01" and "E001" are the same marker with no season number in
    front - ordinary on a single-season show - and neither used to split
    the name at all, so the episode's own title read as a second show's
    name: "Death Note - EP01 - Rebirth" flagged on the word "rebirth"."""
    meta = {"type": "episode", "extra": ANIME, "title": "Death Note"}
    assert scoring._a_different_series(
        {"title": "Death Note - EP01 - Rebirth.mkv"}, meta) == ""
    assert scoring._a_different_series(
        {"title": "Death Note.E001.1080p.BluRay.x265-GROUP.mkv"}, meta) == ""


def test_a_resolution_or_codec_word_is_not_evidence_of_a_different_show():
    """"[HDTV 1080p][Cap.101]" put "hdtv" and "1080p" in front of the
    episode number this function actually found, and with no vocabulary for
    what a resolution or a codec looks like, both read as a second show's
    name - "cap" is Spanish release convention for "episode" and was the
    same kind of gap."""
    meta = {"type": "episode", "extra": ANIME, "title": "One Piece", "aliases": ["One Piece"]}
    source = {"title": "One Piece [HDTV 1080p][Cap.101](wolfmax4k.com).mkv"}
    assert scoring._a_different_series(source, meta) == ""


def test_a_split_episode_suffix_is_recognised():
    """SubsPlease releases some episodes in two parts, "01A" and "01B",
    glued to the number with no space - which never had a boundary to
    split at, so the episode's own Japanese-romaji title following it read
    as a second show's name."""
    meta = {"type": "episode", "extra": ANIME, "title": "Re:ZERO -Starting Life in "
                                        "Another World-",
           "aliases": ["Re:Zero kara Hajimeru Isekai Seikatsu"]}
    source = {"title": "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu "
                        "- 01A (1080p) [39286AC6].mkv"}
    assert scoring._a_different_series(source, meta) == ""


DIGIMON = {"type": "episode", "extra": ANIME, "year": 1999,
           "title": "Digimon: Digital Monsters",
           "aliases": ["Digimon Adventure", "Digimon Digital Monsters"]}


def test_an_anime_version_suffix_ends_the_show_name():
    """"02v3" is a fansub group's corrected re-release of episode 2."""
    source = {"title": "[Keyword] Digimon Adventure 02v3 [BD][720p][10bit].mkv"}
    assert scoring._a_different_series(source, DIGIMON) == ""


def test_an_anime_episode_word_in_another_language_is_not_a_show_name():
    source = {"title": "Digimon_Adventure_Jakso_02.avi"}
    assert scoring._a_different_series(source, DIGIMON) == ""


def test_an_anime_reboot_is_caught_under_its_release_name():
    """Knowing "Digimon Adventure" must not let the 2020 reboot through:
    its name says 2020 straight after the title, and the 1999 series'."""
    source = {"title": "Digimon Adventure (2020) - S01E02 - War Game.mkv"}
    assert scoring._another_production(source, DIGIMON) ==         "another production of the same name"
    assert scoring._another_production(
        {"title": "Digimon Adventure - S01E02 - The Birth of Greymon.mkv"},
        DIGIMON) == ""


# The engine for everything that is not anime. Real releases from Torrentio
# and TorrentsDB, with metadata built the way a Hebrew interface builds it.
GOT = {"type": "episode", "title": u"משחקי הכס", "show_title": u"משחקי הכס",
       "original_title": "Game of Thrones", "english_title": "Game of Thrones",
       "translated_titles": ["Juego de tronos", "Il Trono di Spade",
                             u"Hra o trůny", "Gra o tron"]}


def test_a_korean_drama_on_a_hebrew_interface_keeps_its_releases():
    """`title` is Hebrew and `original_title` is Korean, and every release
    of Squid Game is called "Squid Game": 98 of 99 were rejected as another
    show until the English name was known."""
    meta = {"type": "episode", "title": u"משחק הדיונון",
            "show_title": u"משחק הדיונון", "original_title": u"오징어 게임",
            "english_title": "Squid Game"}
    source = {"title": "Squid.Game.S01E01.1080p.NF.WEB-DL.DDP5.1.x264-NTb.mkv"}
    assert scoring._a_different_series(source, meta) == ""


def test_a_release_named_in_another_language_is_the_same_show():
    for name in ("Juego de Tronos 01x01 M1080.www.pctnew.com.mkv",
                 "Trono.Di.Spade.S01EP01.L.Inverno.Sta.Arrivando.BDMux.1080p.mkv",
                 u"Hra o trůny - 01x01 - Zima se blíží.mkv"):
        assert scoring._a_different_series({"title": name}, GOT) == "", name


def test_every_way_an_episode_is_numbered_ends_the_show_name():
    """"1X01", "S01.E01", "S01.Ep01" and a bare "S05" ahead of "01" were not
    recognised, so the episode's own title was read as a second show."""
    stranger = {"type": "episode", "title": "Stranger Things"}
    breaking = {"type": "episode", "title": "Breaking Bad"}
    assert scoring._a_different_series({"title": "Stranger Things - 1X01 - "
                                        "Chapter One The Vanishing Of Will "
                                        "Byers.mkv"}, stranger) == ""
    assert scoring._a_different_series(
        {"title": "Breaking.Bad.S01.E01.BDRip.1080p-SOFCJ.mkv"}, breaking) == ""
    assert scoring._a_different_series(
        {"title": "Breaking Bad S05 01.mkv"}, breaking) == ""
    assert scoring._a_different_series(
        {"title": "Game.of.Thrones.S01.Ep01.1080p.BluRay.DTS.x264-ESiR.mkv"},
        GOT) == ""


def test_a_season_word_in_another_language_is_not_a_show_name():
    stranger = {"type": "episode", "title": "Stranger Things"}
    source = {"title": "Stranger Things Stagione 1 Ep. 07 Capitolo Sette 720p "
                       "Ita Eng.mkv"}
    assert scoring._a_different_series(source, stranger) == ""


def test_a_genuinely_different_show_is_still_caught():
    """Torrentio answers for Game of Thrones with "The Game", for Stranger
    Things with Sealab 2021 and for Money Heist with Little House on the
    Prairie. Knowing every translation must not let these through."""
    assert scoring._a_different_series(
        {"title": "The Game 2025 S01E01 1080p MY5 WEB-DL AAC2 0 H 264-RAWR"},
        GOT) == "another series of the same name"
    assert scoring._a_different_series(
        {"title": "Sealab 2021 (2000) S01E01 - Radio Free Sealab.1080p.mkv"},
        {"type": "episode", "title": "Stranger Things"}) == \
        "another series of the same name"
    heist = {"type": "episode", "title": "Money Heist",
             "original_title": "La casa de papel",
             "translated_titles": ["La casa di carta", "Dom z papieru"]}
    assert scoring._a_different_series(
        {"title": "La Casa De La Pradera (2026) [HDTV 1080p][Cap.101].mkv"},
        heist) == "another series of the same name"


def test_the_two_engines_do_not_share_their_name_lists():
    """Anime's romaji aliases are the anime engine's alone, and a series'
    translations the other's. Tuning one must never move the other."""
    release_name = {"title": "Shingeki no Kyojin - 01 [1080p].mkv"}
    western = {"type": "episode", "title": "Attack on Titan",
               "aliases": ["Shingeki no Kyojin"]}
    anime = dict(western, extra={"anime": True})
    assert scoring._a_different_series(release_name, western) == \
        "another series of the same name"
    assert scoring._a_different_series(release_name, anime) == ""


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
    from pinky.sources import scoring
    from pinky.utils import release

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
    from pinky.sources import scoring

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
    from pinky.sources import scoring

    settings_module.set("subs.languages", "he,en")
    source = {"title": "x", "languages": languages}
    assert scoring._wrong_language(source, scoring.Preferences()) is penalised


def test_the_language_list_is_the_viewers_own(settings_module):
    """Somebody who reads Spanish should not have Spanish ranked down."""
    from pinky.sources import scoring

    settings_module.set("subs.languages", "es")
    source = {"title": "x", "languages": ["es"]}
    assert not scoring._wrong_language(source, scoring.Preferences())


# --------------------------------------------------------------------------
# foreign-language drama, which is watched here in its own language
# --------------------------------------------------------------------------

def _score_for(title, original, languages="he,en"):
    from pinky.sources import scoring
    from pinky.utils import release

    import pinky.settings as settings_module
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
    from pinky.meta import items

    movie = items.from_tmdb_movie({"id": 1, "title": "x",
                                   "original_language": "tr"})
    assert movie["original_language"] == "tr"
    assert items.new_item("movie")["original_language"] == ""


def test_a_series_in_its_own_language_is_not_foreign_for_an_episode():
    """The ranking reads the series' language from the meta, because an
    episode's item has none; a Turkish release of a Turkish series must not
    take the wrong-language penalty."""
    from pinky.sources import scoring
    turkish = {"title": "Show.S01E03.1080p.WEB-DL.TURKISH-GRP", "hash": "t" * 40,
               "quality": "1080p", "languages": ["tr"], "cached": True,
               "size": 2 * 1024 ** 3, "seeders": 10, "provider": "torrentio"}
    meta = {"type": "episode", "original_language": "tr",
            "item": {"original_language": ""}}
    kept, _rejected = scoring.rank([dict(turkish)], meta, 1.0, limit=0)
    blind, _rejected = scoring.rank([dict(turkish)], {"type": "episode",
                                    "item": {"original_language": ""}}, 1.0, limit=0)
    assert kept[0]["score"] > blind[0]["score"]



# --------------------------------------------------------------------------
# a film in cinemas: cam releases, and releases of something else entirely
# --------------------------------------------------------------------------

SPIDEY = {"type": "movie", "title": "Spider-Man: Brand New Day", "year": 2026}


@pytest.mark.parametrize("name", [
    "SpiderMan-Brand.New.Day.2026.1080p.PREHD ENG-LiNE.x264.AAC-1-Vegamovies.tw.mkv",
    "Spider-Man- Brand New Day 2026.1080p.HQ Pre.Multi.AAC 2.0.x264.mkv",
    "SpiderMan_Brand_New_Day_2026_720p_V4_HDTC_Multi_LiNE_x264_HDHub4u",
    "Some.Film.2026.1080p.HDTC.WEBRip.x264",
])
def test_the_cam_family_is_recognised_as_a_cam(name):
    """A film still in cinemas is uploaded under every one of these and the
    picker offered ten of them with `cam releases` switched off - PREHD, HQ
    Pre, HDTC and LiNE all parsed as `unknown`, so the filter never saw them.
    The last one also claims WEBRip, and the cam tag has to win."""
    from pinky.utils import release

    assert release.parse(name)["source"] == "cam"


@pytest.mark.parametrize("name", [
    "The.Thin.Red.Line.1998.1080p.BluRay.x264-AMIABLE",
    "Multi.Lines.Documentary.2020.1080p.WEB-DL",
])
def test_a_film_with_line_in_its_name_is_not_a_cam(name):
    """LiNE is line audio, and it is matched only beside a language or
    quality word, because The Thin Red Line is a film."""
    from pinky.utils import release

    assert release.parse(name)["source"] != "cam"


@pytest.mark.parametrize("meta,name,rejected", [
    (SPIDEY, "Marvel Studios Iron Man 2008 1080p MA WEB-DL DDP5 1 H 264-SARVO.mkv", True),
    (SPIDEY, "Spider-Man.Brand.New.Day.2026.1080p", False),
    (SPIDEY, "SpiderMan-BrandNewDay-1080p-English.mp4", False),
    ({"type": "movie", "title": "Top Gun: Maverick", "year": 2022},
     "Top.Gun.1986.1080p.BluRay.x264-AMIABLE", True),
    # A translated title shares no word with the English one - measured in
    # the picker, this is One Last Shot - so the year is what saves it.
    ({"type": "movie", "title": "One Last Shot", "year": 2026},
     "O Ultimo Tiro Certo 2026 WEB-DL 1080p x264 DUAL 5.1.mkv", False),
    # A title that is itself a year, with no release year beside it. The
    # year test alone rejects this one, which is why there are two.
    ({"type": "movie", "title": "1917", "year": 2019},
     "1917.BluRay.1080p.x264-GRP", False),
    ({"type": "movie", "title": "Blade Runner 2049", "year": 2017},
     "Blade.Runner.2049.1080p.BluRay.x264", False),
])
def test_a_release_of_a_different_film_is_dropped(meta, name, rejected):
    """Asked for Spider-Man: Brand New Day, Torrentio answered with Iron Man
    2008 under the right film's address. `_another_production` cannot catch
    it: that reads the year directly after the title and this name does not
    begin with the title at all."""
    from pinky.sources import scoring

    assert bool(scoring._a_different_film({"title": name}, meta)) is rejected


# --------------------------------------------------------------------------
# a floor that leaves nothing to choose between
# --------------------------------------------------------------------------


def _at(resolution, index, **extra):
    entry = {"title": "Show.S01E01.%s.WEB-DL.x264-GRP%d" % (resolution, index),
             "hash": "%040d" % index, "provider": "torrentio",
             "quality": resolution, "size": 900 * 1024 ** 2, "seeders": 20,
             "languages": [], "hdr": [], "audio": "unknown",
             "cached": True, "cached_by": "torbox"}
    entry.update(extra)
    return entry


def test_the_resolution_floor_stands_aside_when_the_page_cannot_be_filled(
        settings_module):
    """Naruto 2x54 is a 2002 anime, natively 480p, so demanding 720p asks for
    an upscale that mostly does not exist - four of its releases were refused
    for being exactly what the show is, leaving one row on the screen.

    Measured after: Hikaru no Go 1x02 went from 8 kept to 9, 2x02 from 8 to
    9, One Piece 1x40 from 2 to 3.
    """
    from pinky.sources import scoring

    settings_module.set("sources.min_resolution", "720p")
    sources = [_at("480p", n) for n in range(6)] + [_at("1080p", 90)]
    kept, rejected = scoring.rank(sources, {"type": "episode", "title": "Show"},
                                  limit=0)

    assert len(kept) == 7, "one row is not a choice"
    assert "below the resolution limit" not in rejected


def test_it_stays_put_when_there_is_a_page_of_better_ones(settings_module):
    """The minimum is there to stop a 480p rip being offered instead of
    something better. With a full page of better ones it does that."""
    from pinky.sources import scoring

    settings_module.set("sources.min_resolution", "720p")
    sources = [_at("1080p", n) for n in range(scoring.ENOUGH_TO_CHOOSE_FROM)]
    sources += [_at("480p", 90), _at("480p", 91)]
    kept, rejected = scoring.rank(sources, {"type": "episode", "title": "Show"},
                                  limit=0)

    assert len(kept) == scoring.ENOUGH_TO_CHOOSE_FROM
    assert rejected.get("below the resolution limit") == 2


def test_nothing_is_counted_twice_when_the_floor_stands_aside(settings_module):
    from pinky.sources import scoring

    settings_module.set("sources.min_resolution", "720p")
    sources = [_at("1080p", 1), _at("480p", 2)]
    kept, _rejected = scoring.rank(sources, {"type": "episode", "title": "Show"},
                                   limit=0)

    assert len(kept) == 2
    assert len({id(s) for s in kept}) == 2


def test_a_different_rejection_is_still_honoured_on_the_second_pass(
        settings_module):
    """Standing aside is about the floor and nothing else - a cam is still a
    cam."""
    from pinky.sources import scoring

    settings_module.set("sources.min_resolution", "720p")
    settings_module.set("sources.allow_cam", "false")
    sources = [_at("480p", 1),
               _at("480p", 2, title="Show.S01E01.480p.HDTC.x264-GRP")]
    kept, _rejected = scoring.rank(sources, {"type": "episode", "title": "Show"},
                                   limit=0)

    assert len(kept) == 1, "the cam came back in with the 480p ones"


# --------------------------------------------------------------------------
# a batch is not one oversized file
# --------------------------------------------------------------------------


def test_a_batchs_size_is_measured_per_episode(settings_module):
    """Naruto 2x54: the only genuine (non-Boruto, non-Shippuuden) release
    Torrentio had beyond the single cached episode was a 13.2 GB batch of
    episodes 53-106, refused whole against an 8 GB ceiling built for one
    file. 13.2 GB over 54 episodes is 244 MB an episode - entirely ordinary -
    and the flat ceiling was punishing it purely for being a batch."""
    from pinky.sources import scoring

    settings_module.set_many({"sources.max_size_gb": "8",
                              "sources.cached_only": "false",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    batch = _at("480p", 1,
               title="Naruto Episodes 53-106 Dual Audio DVDrip [DarkDream]",
               size=int(13.2 * 1024 ** 3))
    assert scoring.rejection_reason(batch, prefs) == ""


def test_a_batch_whose_average_is_still_oversized_is_still_refused(
        settings_module):
    """Scaling by episode count is not a loophole - a genuinely bloated
    batch still has to clear the ceiling per episode."""
    from pinky.sources import scoring

    settings_module.set_many({"sources.max_size_gb": "8",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    batch = _at("1080p", 2, title="Show - 01-02 [1080p]",
               size=int(20 * 1024 ** 3))  # 10 GB an episode
    assert scoring.rejection_reason(batch, prefs) == "larger than the size limit"


def test_a_bare_season_pack_with_no_episode_count_keeps_the_flat_ceiling(
        settings_module):
    """A season marker with no numeric range - "Season 2 COMPLETE" - states
    no episode count, so there is nothing to divide by and it stays on the
    ceiling exactly as before."""
    from pinky.sources import scoring

    settings_module.set_many({"sources.max_size_gb": "8",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    pack = _at("1080p", 3, title="Show.S01.COMPLETE.1080p.WEB-DL",
              size=int(60 * 1024 ** 3))
    assert scoring.rejection_reason(pack, prefs) == "larger than the size limit"


def test_a_single_oversized_file_is_unaffected(settings_module):
    from pinky.sources import scoring

    settings_module.set_many({"sources.max_size_gb": "8",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    huge = _at("2160p", 4, title="Movie.2024.2160p.BluRay.x265-GRP",
              size=int(13.2 * 1024 ** 3))
    assert scoring.rejection_reason(huge, prefs) == "larger than the size limit"


# --------------------------------------------------------------------------
# a torrent nobody is seeding cannot become anything else
# --------------------------------------------------------------------------


def test_an_uncached_source_with_no_seeders_is_refused(settings_module):
    """It is not a quality trade-off like a low resolution or an oversized
    batch - a torrent with zero seeders has nothing for a debrid service to
    fetch from, ever. Measured on Naruto 2x54: the one surviving batch that
    named the right episode had zero seeders, and would have been offered
    next to a genuinely working cached copy as though the two were the same
    kind of thing."""
    from pinky.sources import scoring

    settings_module.set_many({"sources.cached_only": "false",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    dead = _at("1080p", 1, cached=False, seeders=0)
    assert scoring.rejection_reason(dead, prefs) == "no seeders"


def test_an_uncached_source_with_seeders_is_fine(settings_module):
    from pinky.sources import scoring

    settings_module.set_many({"sources.cached_only": "false",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    alive = _at("1080p", 2, cached=False, seeders=1)
    assert scoring.rejection_reason(alive, prefs) == ""


def test_a_cached_source_needs_no_seeders_at_all(settings_module):
    """It is already sitting on the debrid service's own storage and does
    not need the swarm any more."""
    from pinky.sources import scoring

    settings_module.set_many({"sources.cached_only": "false",
                              "sources.min_resolution": "sd",
                              "sources.max_resolution": "2160p"})
    prefs = scoring.Preferences()

    stored = _at("1080p", 3, cached=True, seeders=0)
    assert scoring.rejection_reason(stored, prefs) == ""


def test_an_arc_is_released_under_its_season_name():
    """Monogatari season 5 is "Monogatari Series OFF & MONSTER Season", and
    every cached copy of 5x11 was rejected for "off" and "monster" - and for
    "and", which is how a scene release spells "&"."""
    meta = {"type": "episode", "extra": ANIME, "title": "Monogatari",
            "season_name": "MONOGATARI Series OFF & MONSTER Season"}
    for name in ("[SubsPlease] Monogatari Series - Off & Monster Season - 11 (1080p).mkv",
                 "MONOGATARI.Series.OFF.and.MONSTER.Season.S01E11.1080p.WEB.H264.mkv"):
        assert scoring._a_different_series({"title": name}, meta) == "", name


def test_a_chapter_is_an_episode_word_for_anime():
    """"Monster - Chapter 03 - Murder Case DVDRip" is the real Monster."""
    meta = {"type": "episode", "extra": ANIME, "title": "Monster"}
    assert scoring._a_different_series(
        {"title": "Monster - Chapter 03 - Murder Case DVDRip x265 AC-3 2.0 Kira [SEV].mkv"},
        meta) == ""
    assert scoring._a_different_series(
        {"title": "[SubsPlease] Monster Eater - 03 (1080p).mkv"}, meta) ==         "another series of the same name"


def test_an_ordinal_or_a_glued_title_is_not_another_show():
    """Re:Zero's "2nd Season Part 2 - 02", "S02 - E15" and "ReZero" are all
    season two of Re:Zero."""
    meta = {"type": "episode", "extra": ANIME,
            "title": "Re:ZERO -Starting Life in Another World-",
            "aliases": ["Re:Zero kara Hajimeru Isekai Seikatsu"]}
    for name in ("[Erai-raws] Re.Zero kara Hajimeru Isekai Seikatsu 2nd Season Part 2 - 02 [1080p].mkv",
                 "Re Zero S02 - E15 [40].mkv",
                 "[DB]ReZero kara Hajimeru Isekai Seikatsu 2nd Season Part 2_-_02_(Dual Audio).mkv",
                 "Re Zero kara Hajimeru Isekai Seikatsu TV2 [15].mkv"):
        assert scoring._a_different_series({"title": name}, meta) == "", name


@pytest.mark.parametrize("title, release_name, rejected", [
    # The show it continues, named by the first part of our name only.
    ("Naruto Shippūden", "Naruto 055 [Nezumi] [A139713D].mkv", True),
    ("Naruto Shippūden", "Naruto S01E01 - Enter Naruto Uzumaki!.mp4", True),
    ("Dragon Ball Super", "[Group] Dragon Ball - 17.mkv", True),
    # Ours: the name in full, or a name whose missing part is a subtitle.
    ("Naruto Shippūden", "[AnimeRG] Naruto Shippuden - 055 [1080p].mkv", False),
    ("Frieren: Beyond Journey's End", "[SubsPlease] Frieren - 20 (1080p).mkv", False),
    ("Re:ZERO -Starting Life in Another World-", "[Erai-raws] Re Zero - 40.mkv", False),
])
def test_a_release_naming_the_show_this_one_continues(title, release_name, rejected):
    """Naruto Shippuden 3x55 offered "Naruto 055 [Nezumi]" out of a pack
    called "Naruto 053-078" - episode 55 of the original series."""
    from pinky.sources import scoring
    meta = {"type": "episode", "title": title, "search_title": title,
            "extra": {"anime": True}}
    why = scoring._a_different_series({"title": release_name}, meta)
    assert bool(why) is rejected, why

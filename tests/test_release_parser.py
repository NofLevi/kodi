"""The release parser drives both source ranking and subtitle matching."""
import pytest

from katan.utils import release


@pytest.mark.parametrize("name,expected", [
    ("The.Matrix.1999.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1-FraMeSToR", "2160p"),
    ("Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX", "1080p"),
    ("Some.Show.S01E02.720p.HDTV.x264-KILLERS", "720p"),
    ("Old.Movie.1975.480p.DVDRip.XviD-GROUP", "480p"),
    # Not "sd". A name that does not say its resolution has told us nothing,
    # and calling that standard definition is a claim - one that had the
    # minimum-resolution filter throwing away 1080p anime releases, whose
    # names conventionally omit it.
    ("Random release without markers", "unknown"),
])
def test_resolution_detection(name, expected):
    assert release.parse(name)["resolution"] == expected


@pytest.mark.parametrize("name,expected", [
    ("Dune.2021.1080p.WEB-DL.H264-FLUX", "web"),
    ("Dune.2021.1080p.BluRay.x265-RARBG", "bluray"),
    ("Show.S01E01.HDTV.x264-LOL", "hdtv"),
    ("Movie.2024.HDCAM.x264-SUNSCREEN", "cam"),
])
def test_source_detection(name, expected):
    assert release.parse(name)["source"] == expected


@pytest.mark.parametrize("name,expected", [
    ("A.2024.1080p.WEB.H265-X", "h265"),
    ("A.2024.1080p.WEB.x264-X", "h264"),
    ("A.2024.1080p.WEB.AV1-X", "av1"),
    ("A.2004.DVDRip.XviD-X", "xvid"),
])
def test_codec_detection(name, expected):
    assert release.parse(name)["codec"] == expected


def test_hdr_flags_do_not_double_count():
    parsed = release.parse("Movie.2024.2160p.WEB-DL.DV.HDR10+.HEVC-GROUP")
    assert "dv" in parsed["hdr"]
    assert "hdr10plus" in parsed["hdr"]
    assert "hdr" not in parsed["hdr"], "HDR10+ already implies HDR"


@pytest.mark.parametrize("name,expected", [
    ("The.Movie.2024.1080p.WEB-DL.H264-FLUX", "flux"),
    ("[SubsPlease] Frieren - 12 (1080p) [ABCD1234].mkv", "subsplease"),
    ("Show.S01E01.1080p.WEB.h264-KOGi.mkv", "kogi"),
    ("No group here 1080p", ""),
])
def test_release_group_extraction(name, expected):
    assert release.release_group(name) == expected


def test_group_extraction_ignores_quality_tags():
    assert release.release_group("Movie.2024.WEB-1080p") == ""


@pytest.mark.parametrize("name,season,episode", [
    ("Show.S02E07.1080p.WEB.H264-X", 2, 7),
    ("Show.2x07.720p.HDTV-X", 2, 7),
    ("Show.s02e07.1080p", 2, 7),
])
def test_season_episode_parsing(name, season, episode):
    parsed = release.parse(name)
    assert (parsed["season"], parsed["episode"]) == (season, episode)


def test_absolute_episode_numbering_for_anime():
    parsed = release.parse("[SubsPlease] Frieren - 12 (1080p) [ABCD].mkv")
    assert parsed["absolute"] == 12
    assert release.matches_episode(parsed, 1, 12)


def test_matches_episode_accepts_season_packs():
    pack = release.parse("Show.S02.1080p.WEB-DL.H264-X")
    assert release.matches_episode(pack, 2, 5), "a season pack should still match"
    assert not release.matches_episode(pack, 3, 5)


def test_matches_episode_rejects_the_wrong_episode():
    parsed = release.parse("Show.S02E07.1080p.WEB.H264-X")
    assert not release.matches_episode(parsed, 2, 8)
    assert not release.matches_episode(parsed, 1, 7)


def test_hebrew_language_detection():
    assert "he" in release.parse("Movie.2024.1080p.WEB-DL.HebSub-X")["languages"]
    assert "he" in release.parse(u"\u05e1\u05e8\u05d8 2024 1080p \u05e2\u05d1\u05e8\u05d9\u05ea")["languages"]
    assert "he" not in release.parse("Movie.2024.1080p.WEB-DL-X")["languages"]


def test_proper_and_repack_are_flagged():
    assert release.parse("Movie.2024.PROPER.1080p.WEB-X")["proper"]
    assert release.parse("Movie.2024.REPACK.1080p.WEB-X")["proper"]
    assert not release.parse("Movie.2024.1080p.WEB-X")["proper"]


def test_size_label_is_human_readable():
    assert release.size_label(0) == ""
    assert release.size_label(5 * 1024 ** 3) == "5.00 GB"
    assert release.size_label(700 * 1024 ** 2) == "700 MB"


def test_parse_never_raises_on_junk():
    for junk in (None, "", "   ", 12345, u"\u05e2\u05d1\u05e8\u05d9\u05ea", "-" * 200):
        parsed = release.parse(junk)
        assert parsed["resolution"] in ("unknown", "sd", "480p", "720p",
                                        "1080p", "2160p")


def test_an_unreadable_resolution_is_not_filtered_out():
    """A release that does not name its resolution must not be refused for
    being below a minimum it never claimed to be under.

    Fansub names routinely omit it: thirteen of the forty-four copies of one
    Bleach episode were thrown away this way, several of them 1080p.
    """
    from katan.sources import scoring

    class Prefs(object):
        allow_cam = allow_hevc = allow_av1 = allow_hdr = True
        cached_only = False
        max_size = 0
        max_rank = 2      # 720p ceiling
        min_rank = 2      # 720p floor

    unreadable = {"title": "[Late] Bleach TYBW 46 v2 (Web, x264, 10b. EAC3)",
                  "quality": "unknown", "codec": "h264", "hdr": [], "size": 0}
    assert scoring.rejection_reason(unreadable, Prefs()) == ""

    too_low = dict(unreadable, quality="480p")
    assert scoring.rejection_reason(too_low, Prefs()) ==         "below the resolution limit"


# --------------------------------------------------------------------------
# subtitle file names
#
# A subtitle is named after the video and then decorated: the language,
# sometimes a "forced" or "sdh" flag, and a subtitle extension. All of it
# lands after the release group, which is the strongest subtitle-matching
# signal there is - and all of it was hiding it, in exactly the file names
# where it matters most.
# --------------------------------------------------------------------------


def test_a_language_suffix_is_not_the_release_group():
    """This one was live: Wizdom returns names like YIFY-heb, which read as a
    release by a group called "heb"."""
    assert release.release_group(
        "The.Film.1994.1080p.x264.YIFY-heb") == "yify"


def test_a_subtitle_extension_does_not_hide_the_group():
    """Only video extensions were stripped, so every .srt lost its group."""
    assert release.release_group(
        "The.Film.2024.1080p.BluRay.x264-AMIABLE.srt") == "amiable"


def test_stacked_tags_all_come_off():
    assert release.release_group(
        "The.Film.2024.1080p.WEB-DL.x264-NTb.forced.heb.srt") == "ntb"


def test_the_release_underneath_is_recovered():
    assert release.strip_subtitle_tags(
        "The.Film.2024.1080p.BluRay.x264-AMIABLE.heb.srt") == \
        "The.Film.2024.1080p.BluRay.x264-AMIABLE"


def test_a_plain_release_name_is_left_alone():
    name = "The.Film.2024.1080p.BluRay.x264-AMIABLE"
    assert release.strip_subtitle_tags(name) == name


def test_a_group_is_not_eaten_for_looking_like_a_language():
    """The tags only come off the end, one separator at a time, so a group
    that happens to contain one is safe."""
    assert release.release_group(
        "The.Film.2024.1080p.BluRay.x264-HEBITS") == "hebits"


def test_a_dot_separated_group_is_still_a_group():
    """Common enough to matter: this is the form Wizdom returns."""
    assert release.release_group(
        "The.Film.1994.1080p.x264.YIFY") == "yify"


def test_a_plain_title_has_no_group():
    """Otherwise the last word of every title becomes a release group, and
    "The.Office" is by a group called Office."""
    assert release.release_group("The.Office") == ""
    assert release.release_group("Breaking.Bad") == ""


def test_a_quality_token_at_the_end_is_not_a_group():
    assert release.release_group("The.Film.2024.BluRay.1080p") == ""
    assert release.release_group("The.Film.2024.1080p.HDR") == ""
    assert release.release_group("The.Film.2024.WEB.x265") == ""


def test_a_year_at_the_end_is_not_a_group():
    assert release.release_group("The.Film.1080p.BluRay.1994") == ""


def test_a_trailing_bracket_is_the_site_not_the_group():
    """It is where a torrent was re-hosted, or a CRC. The release is ggwp."""
    assert release.release_group(
        "silo.s01e01.1080p.web.h264-ggwp[eztv.re].mkv") == "ggwp"
    assert release.release_group(
        "Show.S01E01.1080p.WEB.H264-NTb[TGx]") == "ntb"


def test_stacked_trailing_brackets_all_come_off():
    assert release.release_group(
        "Show.S01E01.1080p.WEB.H264-NTb [eztv] [TGx]") == "ntb"


def test_a_leading_bracket_is_still_the_group():
    """That is exactly how anime names its group, which is why only the
    trailing brackets are treated as noise."""
    assert release.release_group(
        "[SubsPlease] Bleach - 46 (1080p) [A1B2C3D4].mkv") == "subsplease"


def test_a_site_in_a_leading_bracket_is_not_the_group():
    """A domain is the tell. The release is Ralf, not COOL-TORENTS.PL."""
    assert release.release_group(
        "[COOL-TORENTS.PL]Silo.S01E01.1080p.WEB-DL.H264-Ralf.mkv") == "ralf"
    assert release.release_group(
        "[ OxTorrent.com ] The.Film.2024.1080p.BluRay.x264-AMIABLE") == "amiable"


def test_the_site_stamp_comes_off_the_name_as_well():
    assert release.strip_site_tags(
        "[ OxTorrent.com ] Les evades (1994) - 1080p x264.mkv") == \
        "Les evades (1994) - 1080p x264.mkv"


def test_an_anime_group_in_the_same_position_survives():
    assert release.strip_site_tags(
        "[SubsPlease] Bleach - 46 (1080p).mkv").startswith("[SubsPlease]")

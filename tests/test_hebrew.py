# -*- coding: utf-8 -*-
"""Hebrew is not an edge case here, it is half the catalogue.

The Israeli VOD index is 2,810 programmes titled entirely in Hebrew, the
interface ships in Hebrew, and the subtitles this add-on exists to find are
Hebrew. So the things that break on non-Latin text - encodings, substring
search, filenames, mixed-script release names - are not exotic failures to
guard against, they are the normal path.
"""
import os

import pytest


ALEF = u"\u05d0"
HEBREW_TITLE = u"\u05d4\u05d0\u05d7 \u05d4\u05d2\u05d3\u05d5\u05dc"   # Big Brother
HEBREW_WORD = u"\u05e2\u05d1\u05e8\u05d9\u05ea"                        # "Hebrew"


# --------------------------------------------------------------------------
# encodings, where Hebrew subtitles actually live
# --------------------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-8", "cp1255", "iso-8859-8"])
def test_hebrew_subtitles_decode_from_the_encodings_they_arrive_in(encoding):
    """Hebrew subtitle files in the wild are cp1255 as often as UTF-8."""
    from katan.subs import srt

    text = u"1\n00:00:01,000 --> 00:00:03,000\n%s\n\n" % HEBREW_WORD
    decoded = srt.decode(text.encode(encoding))
    assert HEBREW_WORD in decoded, "%s round trip lost the Hebrew" % encoding


def test_a_utf8_byte_order_mark_does_not_become_part_of_the_first_cue():
    """Windows subtitle editors write one, and it lands in the first line."""
    from katan.subs import srt

    text = u"\ufeff1\n00:00:01,000 --> 00:00:03,000\n%s\n\n" % HEBREW_WORD
    cues = srt.parse(srt.decode(text.encode("utf-8")))
    assert len(cues) == 1
    assert cues[0].text == HEBREW_WORD
    assert u"\ufeff" not in cues[0].text


def test_hebrew_is_recognised_as_hebrew():
    from katan.subs import srt

    hebrew = [srt.Cue(1, 0.0, 2.0, HEBREW_TITLE)]
    english = [srt.Cue(1, 0.0, 2.0, "Just some English dialogue here")]
    assert srt.looks_hebrew(hebrew) is True
    assert srt.looks_hebrew(english) is False


def test_a_mostly_english_file_labelled_hebrew_is_not_mistaken_for_it():
    """Providers mislabel, and showing English to somebody who asked for
    Hebrew is the complaint this whole pipeline exists to avoid."""
    from katan.subs import srt

    cues = [srt.Cue(n, n, n + 1, "English line number %d" % n) for n in range(30)]
    cues.append(srt.Cue(99, 99, 100, HEBREW_WORD))
    assert srt.looks_hebrew(cues) is False


# --------------------------------------------------------------------------
# searching a Hebrew catalogue
# --------------------------------------------------------------------------


def test_a_hebrew_substring_finds_the_programme(settings_module):
    """Somebody types three letters from the middle of a Hebrew title."""
    from katan.vod import library

    library.refresh()
    everything = library.load()
    assert everything, "the bundled catalogue should not be empty"

    title = next(entry["n"] for entry in everything if len(entry.get("n", "")) > 5)
    needle = title[2:5]
    results = library.search(needle, limit=50)
    assert results, "no result for %r inside %r" % (needle, title)
    assert any(needle in item["title"] for item in results)


def test_hebrew_search_is_not_confused_by_surrounding_whitespace():
    from katan.vod import library

    library.refresh()
    title = next(e["n"] for e in library.load() if len(e.get("n", "")) > 5)
    assert library.search("  %s  " % title[:4], limit=20)


def test_a_single_hebrew_letter_is_too_short_to_search():
    """Otherwise the first keystroke returns most of the catalogue."""
    from katan.vod import library
    assert library.search(ALEF) == []


def test_the_suggestion_index_matches_hebrew(settings_module, monkeypatch):
    from katan.search import unified

    unified.invalidate_index()
    title = None
    from katan.vod import library
    library.refresh()
    for entry in library.load():
        if len(entry.get("n", "")) > 6:
            title = entry["n"]
            break
    assert title

    found = unified.suggest(title[:5], limit=10)
    assert any(title[:5] in item.get("title", "") for item in found)


def test_the_israeli_catalogue_is_listed_before_tmdb(settings_module,
                                                     monkeypatch):
    """Because for an Israeli programme TMDB's entry is the one that fails.

    Searching a Hebrew title returns two things: the VOD entry, which plays,
    and TMDB's record of the same show, which has no sources anywhere -
    Israeli television is not on the trackers. Listed the other way round, the
    first result is the one that cannot play, which reads to anybody using it
    as "the search did not find it".
    """
    from katan.search import unified
    from katan.vod import library

    library.refresh()
    entry = next(e for e in library.load() if len(e.get("n", "")) > 6)
    title = entry["n"]

    # TMDB answering with a plausible show for the same query, the way it does
    # for a real Israeli programme.
    monkeypatch.setattr(unified, "_tmdb_search", lambda query: [
        {"type": "show", "title": "The English Name", "ids": {"tmdb": 4242}}])
    monkeypatch.setattr(unified, "_anilist_search", lambda query: [])

    found = unified.search(title)
    assert found, "the Hebrew title found nothing at all"
    assert found[0].get("type") == "vod",         "the entry that plays has to be the first one offered"
    assert title in found[0].get("title", "")


def test_a_hebrew_suggestion_offers_the_israeli_entry_first(settings_module,
                                                            monkeypatch):
    """The same order, while the viewer is still typing."""
    from katan.search import unified
    from katan.vod import library

    library.refresh()
    unified.invalidate_index()
    entry = next(e for e in library.load() if len(e.get("n", "")) > 6)
    monkeypatch.setattr(unified, "_tmdb_search", lambda query: [])

    found = unified.suggest(entry["n"][:6], limit=10)
    assert found
    assert found[0].get("type") == "vod"


# --------------------------------------------------------------------------
# mixed scripts, which is what an Israeli release name looks like
# --------------------------------------------------------------------------


def test_a_hebrew_title_beside_a_latin_release_group_still_parses():
    """The group is the strongest subtitle-matching signal there is, and it
    sits at the end of a name whose front half is Hebrew."""
    from katan.utils import release

    name = u"%s.2024.1080p.WEB-DL.H264-FLUX" % HEBREW_TITLE
    parsed = release.parse(name)
    assert parsed["resolution"] == "1080p"
    assert parsed["codec"] == "h264"
    assert parsed["group"] == "flux"


def test_the_hebrew_language_hint_is_found_in_either_script():
    from katan.utils import release

    assert "he" in release.parse("Movie.2024.1080p.WEB-DL.HebSub-X")["languages"]
    assert "he" in release.parse(u"Movie 2024 1080p %s" % HEBREW_WORD)["languages"]
    assert "he" not in release.parse("Movie.2024.1080p.WEB-DL-X")["languages"]


def test_a_wholly_hebrew_name_does_not_crash_the_parser():
    from katan.utils import release

    parsed = release.parse(u"%s %s" % (HEBREW_TITLE, HEBREW_WORD))
    assert parsed["resolution"] in ("unknown", "sd", "480p", "720p",
                                    "1080p", "2160p")


def test_an_episode_number_is_read_out_of_a_hebrew_name():
    from katan.utils import release

    parsed = release.parse(u"%s S02E07 1080p WEB-DL" % HEBREW_TITLE)
    assert (parsed["season"], parsed["episode"]) == (2, 7)


# --------------------------------------------------------------------------
# Hebrew on disk, which Android is fussier about than Windows
# --------------------------------------------------------------------------


def test_a_subtitle_filename_never_carries_hebrew_onto_the_filesystem():
    """Android storage and Kodi's own path handling both have opinions about
    non-ASCII filenames, and a subtitle that cannot be written is a subtitle
    that never appears. The name is built from ids, not from the title."""
    from katan.subs import auto

    meta = {"type": "movie", "ids": {}, "title": HEBREW_TITLE}
    name = auto.name_for(meta, "he")
    assert name.encode("ascii"), "the filename must survive ASCII encoding"
    assert name.endswith(".he.srt")


def test_a_hebrew_titled_subtitle_can_actually_be_written_and_read(
        settings_module):
    from katan.subs import auto, srt

    cues = [srt.Cue(1, 0.0, 2.0, HEBREW_TITLE),
            srt.Cue(2, 3.0, 5.0, HEBREW_WORD)]
    meta = {"type": "episode", "ids": {"tmdb": 1399}, "title": HEBREW_TITLE,
            "season": 2, "episode": 7}

    path = auto.store(meta, "he", cues)
    assert path and os.path.isfile(path)

    back = srt.read(path)
    assert [cue.text for cue in back] == [HEBREW_TITLE, HEBREW_WORD]


def test_two_hebrew_titles_do_not_collide_on_disk(settings_module):
    """Stripping to ASCII must not turn every Hebrew title into one filename."""
    from katan.subs import auto

    first = auto.name_for({"type": "movie", "ids": {"tmdb": 11},
                           "title": HEBREW_TITLE}, "he")
    second = auto.name_for({"type": "movie", "ids": {"tmdb": 22},
                            "title": HEBREW_WORD}, "he")
    assert first != second


# --------------------------------------------------------------------------
# labels, where right-to-left text meets a fixed-width control
# --------------------------------------------------------------------------


def test_a_hebrew_label_is_not_reversed_or_mangled_by_the_builders():
    """Kodi renders RTL itself. Anything that reorders the string here would
    render backwards on screen, which no test that only counts characters
    would notice."""
    from katan.meta import items

    item = items.new_item("movie", title=HEBREW_TITLE, year=2024)
    label = items.label(item)
    assert HEBREW_TITLE in label
    assert label.index(HEBREW_TITLE) == 0, "the title should lead the label"


def test_a_hebrew_episode_label_keeps_its_numbering():
    from katan.meta import items

    item = items.new_item("episode", title=HEBREW_WORD, season=2, episode=7)
    label = items.label(item)
    assert "2x07" in label
    assert HEBREW_WORD in label


def test_a_hebrew_title_survives_the_cache_round_trip(settings_module):
    """The cache stores JSON, and a bad ensure_ascii would silently mangle it."""
    from katan import cache
    from katan.meta import items

    item = items.new_item("movie", title=HEBREW_TITLE, plot=HEBREW_WORD)
    cache.set("hebrew-item", [item], 60)
    back = cache.get("hebrew-item")
    assert back[0]["title"] == HEBREW_TITLE
    assert back[0]["plot"] == HEBREW_WORD


def test_a_hebrew_search_term_makes_a_usable_cache_key():
    from katan import cache

    key = cache.make_key("search", HEBREW_TITLE)
    assert key
    assert key == cache.make_key("search", HEBREW_TITLE)
    assert key != cache.make_key("search", HEBREW_WORD)


# --------------------------------------------------------------------------
# which script a subtitle is actually written in
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (u"\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd", "he"),
    ("Hello there, sheriff", "en"),
    (u"\u3053\u3093\u306b\u3061\u306f\u4e16\u754c", "ja"),
    (u"\uc548\ub155\ud558\uc138\uc694", "ko"),
    (u"\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440", "ru"),
    (u"\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645", "ar"),
    # Nothing alphabetic to go on is "" rather than a guess.
    ("12:34  ---  ?!", ""),
])
def test_the_script_a_subtitle_is_written_in(text, expected):
    """A mislabelled upload is common and applying one is silent.

    Measured on eighteen titles: The Godfather's best *Hebrew* candidate came
    back written in Arabic. Script is not language - it cannot tell French
    from Italian - but it tells Hebrew from Arabic from English, which is the
    mistake that actually happens.
    """
    from katan.subs import srt

    cues = [srt.Cue(1, 0.0, 2.0, text)]
    assert srt.detect_script(cues) == expected


def test_looks_hebrew_is_the_same_answer(monkeypatch):
    """One implementation, so the two cannot drift apart."""
    from katan.subs import srt

    hebrew = [srt.Cue(1, 0.0, 2.0, u"\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd")]
    arabic = [srt.Cue(1, 0.0, 2.0, u"\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645")]
    assert srt.looks_hebrew(hebrew) is True
    assert srt.looks_hebrew(arabic) is False
    assert srt.detect_script(arabic) == "ar"


def test_an_empty_subtitle_is_not_a_language():
    from katan.subs import srt

    assert srt.detect_script([]) == ""
    assert srt.looks_hebrew([]) is False


# --------------------------------------------------------------------------
# SubStation Alpha, which is what anime subtitles usually are
# --------------------------------------------------------------------------

SSA = r"""[Script Info]
Title: Something
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Default,Arial,20

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.78,0:00:05.16,Default,,0,0,0,,Dive Massively\NOnline Game.
Dialogue: 0,0:00:05.16,0:00:08.66,Default,,0,0,0,,{\i1}Also known as a DMMO-RPG.{\i0}
Dialogue: 0,0:00:08.67,0:00:13.88,Default,,0,0,0,,A line, with a comma in it.
Comment: 0,0:00:20.00,0:00:22.00,Default,,0,0,0,,not dialogue
"""


def test_substation_alpha_is_parsed():
    """Refusing it threw away real subtitles.

    Over a survey, *every* download that failed to parse was SSA - and half of
    what OpenSubtitles returns for Overlord is. It is the house format for
    anime, which is the part of the catalogue that can least afford to lose a
    provider.
    """
    from katan.subs import srt

    cues = srt.parse(SSA)
    assert len(cues) == 3, [c.text for c in cues]
    assert abs(cues[0].start - 0.78) < 0.01
    assert abs(cues[0].end - 5.16) < 0.01
    # Centiseconds, not milliseconds: reading .78 as 0.078s would shift every
    # cue by most of a second.
    assert abs(cues[2].end - 13.88) < 0.01


def test_ssa_text_is_cleaned_up():
    from katan.subs import srt

    cues = srt.parse(SSA)
    # A hard break is a literal backslash-N in the file, not an escape.
    assert cues[0].text == "Dive Massively\nOnline Game."
    # Override tags are styling, not words.
    assert cues[1].text == "Also known as a DMMO-RPG."
    # Text is the last column and may hold commas of its own.
    assert cues[2].text == "A line, with a comma in it."


def test_a_comment_line_is_not_a_cue():
    from katan.subs import srt

    assert all("not dialogue" not in c.text for c in srt.parse(SSA))


def test_srt_still_parses_as_srt():
    """The detector must not claim ordinary files."""
    from katan.subs import srt

    text = "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
    assert not srt._looks_like_ssa(text)
    cues = srt.parse(text)
    assert len(cues) == 1 and cues[0].text == "hello"


def test_the_column_order_is_read_not_assumed():
    """Some files carry extra columns; index 9 would then be a style name."""
    from katan.subs import srt

    odd = (u"[Events]\n"
           u"Format: Start, End, Text\n"
           u"Dialogue: 0:00:01.00,0:00:02.50,hello there\n")
    cues = srt.parse(odd)
    assert len(cues) == 1
    assert cues[0].text == "hello there"
    assert abs(cues[0].start - 1.0) < 0.01

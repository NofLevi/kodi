"""A title's own language: what TMDB says it was made in, put to work.

Four decisions hang off it, and each was checked against what the add-on did
before: an Israeli film searched for Hebrew subtitles it does not need, the
Series tab had no row for the dramas actually watched here, an anime's English
dub could be picked over the Japanese release its subtitles follow, and a
dual-audio file played whichever track Kodi's default landed on.
"""
import pytest

from katan.sources import scoring


# --------------------------------------------------------------------------
# a Hebrew title needs no subtitle
# --------------------------------------------------------------------------


def test_an_israeli_title_gets_no_automatic_subtitle(monkeypatch, settings_module):
    from katan.subs import auto

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en"})
    called = []
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda *a, **k: called.append("search") or ("", {}))
    monkeypatch.setattr(auto, "use_embedded",
                        lambda *a, **k: called.append("embedded") or False)
    auto.on_playback_started(object(), {"title": "Fauda", "original_language": "he"})
    assert called == []


def test_a_foreign_title_still_gets_one(monkeypatch, settings_module):
    from katan.subs import auto

    settings_module.set_many({"subs.auto": "true", "subs.languages": "he,en",
                              "subs.embedded_first": "false"})
    called = []
    monkeypatch.setattr(auto, "find_and_prepare",
                        lambda *a, **k: called.append("search") or ("", {}))
    auto.on_playback_started(object(), {"title": "Parasite", "original_language": "ko"})
    assert called == ["search"]


def test_the_picker_asks_no_subtitle_question_for_a_hebrew_title(monkeypatch):
    from katan.subs import outlook

    def must_not_search(*args, **kwargs):
        raise AssertionError("searched for subtitles for a Hebrew title")

    monkeypatch.setattr(outlook, "candidates", must_not_search)
    monkeypatch.setattr(outlook, "translation_candidates", must_not_search)
    sources = [{"title": "Fauda.S01E01.1080p.WEB-DL", "hash": "a" * 40}]
    outlook.annotate({"ids": {"tmdb": 1}, "original_language": "he"}, sources)
    assert sources[0]["subs_kind"] == outlook.NATIVE


def test_the_picker_says_hebrew_audio_rather_than_no_hebrew_found():
    from katan import kodi
    from katan.subs import outlook
    from katan.ui import sources_window

    line = sources_window._subtitle_badge({"subs_kind": outlook.NATIVE})
    assert line == kodi.localize(32537)
    assert line != kodi.localize(32476)


# --------------------------------------------------------------------------
# a row for the dramas watched here
# --------------------------------------------------------------------------


def test_the_series_tab_has_a_turkish_spanish_and_italian_drama_row(monkeypatch):
    from katan import catalog
    from katan.meta import tmdb

    asked = {}
    monkeypatch.setattr(tmdb, "discover",
                        lambda media_type, page=1, **filters:
                        asked.update(filters, media_type=media_type) or [])
    row = next(r for r in catalog.rows() if r["id"] == "foreign_dramas")
    row["loader"](1)
    assert asked["media_type"] == "tv"
    assert asked["with_original_language"] == "tr|es|it"
    assert asked["with_genres"] == str(catalog.GENRE_TV["drama"])
    assert "foreign_dramas" in catalog.SECTION_ORDER[catalog.SHOWS]


# --------------------------------------------------------------------------
# the original audio before a dub, and never a language nobody reads
# --------------------------------------------------------------------------


def _source(title, quality="1080p", size=2, **extra):
    from katan.sources import model
    entry = model.new_source(title=title, provider="torrentio",
                             info_hash=(title * 3)[:40], size=size * 1024 ** 3,
                             seeders=20)
    from katan.utils import release
    parsed = release.parse(title)
    entry.update(cached=True, quality=quality, languages=parsed["languages"],
                 dub=parsed["dub"])
    entry.update(extra)
    return entry


def _order(sources, original):
    kept, _rejected = scoring.rank(sources, {"type": "episode",
                                             "original_language": original},
                                   0.5, limit=0)
    return [source["title"] for source in kept]


def test_an_anime_dub_ranks_below_the_original_audio(settings_module):
    settings_module.set("subs.languages", "he,en")
    sub = "[Group] Hikaru no Go - 05 [1080p]"
    dub = "[Group] Hikaru no Go - 05 [1080p] [English Dub]"
    order = _order([_source(dub, size=1), _source(sub, size=3)], "ja")
    assert order.index(sub) < order.index(dub), order


def test_the_dub_is_still_offered(settings_module):
    """Preferred, not removed: the picker keeps it for a manual choice."""
    settings_module.set("subs.languages", "he,en")
    dub = "[Group] Hikaru no Go - 05 [1080p] [English Dub]"
    assert dub in _order([_source(dub)], "ja")


def test_a_dub_is_not_pushed_down_in_kids_mode(settings_module):
    """A child who cannot read subtitles is exactly who a dub is for."""
    settings_module.set_many({"subs.languages": "he,en", "kids.enabled": "true"})
    prefs = scoring.Preferences()
    prefs.original_language = "ja"
    assert scoring._dubbed({"dub": "dub"}, prefs) == 0


def test_a_dub_of_a_show_in_a_readable_language_is_not_pushed_down(settings_module):
    settings_module.set("subs.languages", "he,en")
    source = _source("Show.S01E01.1080p.WEB.English.Dub-GRP")
    prefs = scoring.Preferences()
    prefs.original_language = "en"
    assert scoring._dubbed(dict(source, dub="dub"), prefs) == 0


def test_a_release_nobody_here_can_watch_loses_even_at_a_higher_resolution(settings_module):
    """WEIGHT_WRONG_LANGUAGE promised this and, sorted after size, could not
    keep it: a higher-resolution Italian dub beat an English release."""
    settings_module.set("subs.languages", "he,en")
    italian = "Mousetrap.1x02.ITA.1080p.WEB-DL.x265-GRP"
    english = "Mousetrap.S01E02.720p.WEB.h264-ETHEL"
    order = _order([_source(italian), _source(english, quality="720p")], "en")
    assert order == [english, italian]


# --------------------------------------------------------------------------
# the audio track the file starts on
# --------------------------------------------------------------------------


def _tracks(streams, current):
    import xbmc
    xbmc.JSONRPC_RESULTS["Player.GetProperties"] = {
        "audiostreams": streams, "currentaudiostream": {"index": current}}


JAPANESE_ENGLISH = [{"index": 0, "language": "eng", "name": "English"},
                    {"index": 1, "language": "jpn", "name": "Japanese"}]


@pytest.fixture
def monitor(settings_module):
    import xbmc
    from katan import player
    watcher = player.KatanPlayer()
    yield watcher
    xbmc.JSONRPC_RESULTS.pop("Player.GetProperties", None)


def test_the_original_audio_is_chosen_on_a_dual_audio_file(monitor):
    from katan.subs import embedded
    _tracks(JAPANESE_ENGLISH, current=0)
    monitor.meta = {"original_language": "ja"}
    monitor._choose_audio_track(embedded)
    assert getattr(monitor, "audio_stream", None) == 1


def test_nothing_is_switched_when_the_original_is_already_playing(monitor):
    from katan.subs import embedded
    _tracks(JAPANESE_ENGLISH, current=1)
    monitor.meta = {"original_language": "ja"}
    monitor._choose_audio_track(embedded)
    assert getattr(monitor, "audio_stream", None) is None


def test_nothing_is_switched_when_the_original_is_not_in_the_file(monitor):
    from katan.subs import embedded
    _tracks(JAPANESE_ENGLISH, current=0)
    monitor.meta = {"original_language": "ko"}
    monitor._choose_audio_track(embedded)
    assert getattr(monitor, "audio_stream", None) is None


def test_kids_mode_chooses_hebrew_audio(monitor, settings_module):
    from katan.subs import embedded
    settings_module.set("kids.enabled", "true")
    _tracks(JAPANESE_ENGLISH + [{"index": 2, "language": "heb", "name": "Hebrew"}],
            current=1)
    monitor.meta = {"original_language": "ja"}
    monitor._choose_audio_track(embedded)
    assert monitor.audio_stream == 2


@pytest.mark.parametrize("title,dub", [
    ("[Group] Hikaru no Go - 05 [1080p] [MULTI]", "dual"),
    ("Show.S01E01.MULTi.DUB.1080p.WEB-DL-GRP", "dual"),
    ("Anime - 05 (1080p) [Multi-Subs] [English Dub]", "dub"),
    ("A.Film.2020.1080p.MULTI.SUBS.WEB-DL-GRP", ""),
])
def test_multi_audio_is_as_good_as_dual_and_multi_subs_is_not_audio(title, dub):
    from katan.utils import release
    assert release.parse(title)["dub"] == dub


def test_a_multi_release_is_not_pushed_down_like_a_dub(settings_module):
    settings_module.set("subs.languages", "he,en")
    prefs = scoring.Preferences()
    prefs.original_language = "ja"
    multi = _source("Show.S01E01.MULTi.DUB.1080p.WEB-DL-GRP")
    assert scoring._dubbed(multi, prefs) == 0

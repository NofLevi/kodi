"""The whole subtitle decision, from candidates to a file on disk."""
import os

import pytest

from katan.subs import auto, srt


MOVIE = {
    "type": "movie",
    "ids": {"imdb": "tt15239678", "tmdb": 693134},
    "title": "Dune: Part Two",
    "year": 2024,
    "stream_url": "",
    "source": {"release": "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX",
               "group": "flux", "quality": "1080p"},
}


def srt_bytes(count=30, offset=0.0, text="hello"):
    cues = [srt.Cue(i + 1, offset + i * 4.0, offset + i * 4.0 + 2.5,
                    "%s %d" % (text, i)) for i in range(count)]
    return srt.dump(cues).encode("utf-8")


@pytest.fixture
def pipeline(monkeypatch, settings_module):
    """Replace the providers with a controllable fake."""
    settings_module.set_many({
        "subs.auto": "true",
        "subs.languages": "he,en",
        "subs.threshold": "70",
        "subs.hash_match": "false",
        "subs.ai.enabled": "false",
        "subs.provider.wizdom": "true",
        "subs.provider.opensubtitles": "false",
        "subs.provider.subsource": "false",
    })

    state = {"candidates": [], "downloads": {}, "downloaded": []}

    def fake_search(meta, languages, video_hash=""):
        return list(state["candidates"])

    def fake_download(candidate):
        state["downloaded"].append(candidate.get("release"))
        data = state["downloads"].get(candidate.get("release"), b"")
        cues = srt.parse(srt.decode(data)) if data else []
        return srt.clean(cues) if cues else []

    monkeypatch.setattr(auto, "search_candidates", fake_search)
    monkeypatch.setattr(auto, "download_candidate", fake_download)
    return state


def candidate(release, language="he", **kwargs):
    entry = {"provider": "wizdom", "language": language, "release": release,
             "download": release}
    entry.update(kwargs)
    return entry


def test_a_matching_hebrew_subtitle_is_downloaded_and_stored(pipeline):
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path and os.path.isfile(path)
    assert path.endswith(".he.srt")
    assert report["translated"] is False
    assert srt.read(path), "the stored file should be readable SRT"


def test_only_one_subtitle_is_ever_downloaded(pipeline):
    """The point of scoring first is to not fetch a dozen files."""
    good = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [
        candidate("Dune.Part.Two.2024.720p.HDTV-AAA"),
        candidate(good),
        candidate("Dune.Part.Two.2024.DVDRip-BBB"),
    ]
    pipeline["downloads"][good] = srt_bytes()

    auto.find_and_prepare(MOVIE, ["he", "en"])
    assert pipeline["downloaded"] == [good]


def test_no_candidates_is_reported_not_crashed(pipeline):
    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path == ""
    assert report["reason"] == "no candidates"


def test_a_weak_match_is_still_used_as_a_last_resort(pipeline):
    weak = "Some.Unrelated.Release.2019.DVDRip-XYZ"
    pipeline["candidates"] = [candidate(weak)]
    pipeline["downloads"][weak] = srt_bytes()

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path, "an imperfect subtitle beats none"
    assert report["reason"] == "below threshold, used anyway"


def test_english_is_translated_when_no_hebrew_is_good_enough(pipeline, monkeypatch,
                                                            settings_module):
    settings_module.set("subs.ai.enabled", "true")
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(english, language="en")]
    pipeline["downloads"][english] = srt_bytes(count=12, text="english")

    from katan.subs.ai import translator

    def fake_translate(cues, language, on_progress=None):
        return [srt.Cue(c.index, c.start, c.end, "HE " + c.text) for c in cues]

    monkeypatch.setattr(translator, "available", lambda: True)
    monkeypatch.setattr(translator, "translate", fake_translate)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert report["translated"] is True
    assert report["source_language"] == "en"

    cues = srt.read(path)
    assert cues[0].text.startswith("HE ")
    assert cues[0].start == 0.0, "translation must not move timings"


def test_a_hash_matched_reference_re_times_the_chosen_subtitle(pipeline):
    """The Hebrew file is 8 seconds out; the English hash match proves it."""
    hebrew = "Dune.Part.Two.2024.1080p.BluRay-OTHER"
    english = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [
        candidate(hebrew, language="he", sync_percent=100),
        candidate(english, language="en", moviehash="abc", hash_match=True),
    ]
    pipeline["downloads"][hebrew] = srt_bytes(count=60, offset=8.0)
    pipeline["downloads"][english] = srt_bytes(count=60, offset=0.0)

    path, report = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert path
    assert report["synchronised"] is True, "the offset should have been corrected"

    cues = srt.read(path)
    assert abs(cues[0].start - 0.0) < 0.3, "expected the 8 second shift removed"


def test_cached_subtitles_are_reused_without_searching(pipeline):
    name = "Dune.Part.Two.2024.1080p.WEB-DL.H264-FLUX"
    pipeline["candidates"] = [candidate(name)]
    pipeline["downloads"][name] = srt_bytes()

    first, _ = auto.find_and_prepare(MOVIE, ["he", "en"])
    assert auto.cached_subtitle(MOVIE, "he") == first

    pipeline["downloaded"] = []
    assert auto.cached_subtitle(MOVIE, "he"), "a second play should hit the cache"
    assert pipeline["downloaded"] == []


def test_the_subtitle_folder_is_capped(settings_module):
    settings_module.set("subs.cache_files", "10")
    directory = auto.subtitle_dir()
    for index in range(25):
        with open(os.path.join(directory, "f%02d.srt" % index), "w") as handle:
            handle.write("1\n00:00:01,000 --> 00:00:02,000\nx\n\n")
    auto.prune_cache()
    remaining = [n for n in os.listdir(directory) if n.endswith(".srt")]
    assert len(remaining) == 10


def test_episode_and_movie_names_do_not_collide():
    movie = auto.name_for(MOVIE, "he")
    episode = auto.name_for({"type": "episode", "ids": {"imdb": "tt0903747"},
                             "season": 2, "episode": 7}, "he")
    assert movie != episode
    assert "s02e07" in episode

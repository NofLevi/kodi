# -*- coding: utf-8 -*-
"""The subtitle that ships inside the release.

The best subtitle for a release is the one that came with it, and it was
sitting in the torrent the whole time. Measured on Hikaru no Go 2x03, the
source the picker had been offering all week: 152 files, 76 of them `.srt`,
one per episode, named to match the video. 25 KB, 356 cues, in time by
construction because whoever made the release typed it against that cut.
"""
import pytest

from pinky.subs import matcher
from pinky.subs.providers import sidecar

VIDEO = "Hikaru.No.Go.TV.EP33.BluRay.1080p.AC3.x264-CHD.mkv"


def _file(name, ident=1):
    return {"id": ident, "name": name, "size": 25092}


def test_the_matching_subtitle_is_found_among_seventy_six():
    """A season pack holds one per episode and only one of them is ours."""
    files = [_file("pack/Hikaru.No.Go.TV.EP%02d.BluRay.1080p.AC3.x264-CHD.srt" % n, n)
             for n in range(1, 77)]
    files.append(_file("pack/" + VIDEO, 200))

    ranked = sidecar.rank_files(files, VIDEO)
    assert ranked, "the episode's own subtitle must be found"
    assert ranked[0]["name"].endswith("EP33.BluRay.1080p.AC3.x264-CHD.srt")
    assert len(ranked) <= sidecar.MAX_FILES, \
        "seventy-six candidates is work a projector does not need"


def test_the_video_itself_is_not_a_subtitle():
    ranked = sidecar.rank_files([_file("pack/" + VIDEO)], VIDEO)
    assert ranked == []


def test_a_release_with_one_unnamed_subtitle_still_offers_it():
    """Nothing matched the video's stem, so the only file there is ours."""
    ranked = sidecar.rank_files([_file("Subs/2_English.srt")], VIDEO)
    assert len(ranked) == 1


def test_the_language_is_read_from_the_name_when_it_says_so():
    assert sidecar.language_of("Some.Release.he.srt") == "he"
    assert sidecar.language_of("Some.Release.heb.srt") == "he"
    assert sidecar.language_of("Some.Release.Hebrew.srt") == "he"
    assert sidecar.language_of("Some.Release.pol.ass") == "pl"


def test_an_unlabelled_subtitle_is_assumed_english():
    """Which is what one beside a fansub release almost always is - and the
    guess is safe, because `download_candidate` refuses cues whose script is
    not the language asked for."""
    assert sidecar.language_of(VIDEO.replace(".mkv", ".srt")) == "en"


def test_a_number_in_the_name_is_not_a_language():
    assert sidecar.language_of("Episode.33.srt") == "en"


def test_the_matcher_scores_it_as_the_same_release():
    """It is not told anything. The subtitle's name *is* the release name, so
    `_same_name` recognises it and returns 100 - which is what a file that
    shipped with the release deserves."""
    target = matcher.target_from(
        # `absolute` is what `build_meta` supplies for anime: EP33 is season
        # two episode three counted from the first, and without it the name
        # reads as the wrong episode.
        {"type": "episode", "title": "Hikaru no Go", "show_title": "Hikaru no Go",
         "season": 2, "episode": 3, "absolute": 33, "ids": {}},
        source={"release": VIDEO})
    score, reason = matcher.rate(
        {"release": VIDEO.replace(".mkv", ""), "language": "en",
         "provider": sidecar.NAME}, target)
    assert score == 100 and "identical" in reason


def test_nothing_is_asked_without_a_torrent(no_network):
    assert sidecar.search({"source": {}}, None, ["he", "en"]) == []
    assert sidecar.search({}, None, ["he", "en"]) == []


def test_a_service_that_cannot_look_inside_a_torrent_is_not_asked(monkeypatch,
                                                                 no_network):
    """Only TorBox lists the files in a torrent. The others resolve one file
    and have no reason to know what else is in there."""
    from pinky.debrid import registry

    monkeypatch.setattr(registry, "resolver_for", lambda source: object())
    assert sidecar.search({"source": {"torrent_hash": "a" * 40}}, None,
                          ["he"]) == []


def test_the_provider_is_actually_asked(settings_module):
    """It was written, registered in `_modules`, and left out of the ordered
    list `_providers` iterates - so it was never asked once. The log said so
    by saying nothing at all."""
    from pinky.subs import auto

    names = [name for name, _module in auto._providers()]
    assert "sidecar" in names, names
    assert names[0] == "sidecar", \
        "it needs no judgement, so it is asked before anything that does"


def test_the_playback_record_is_the_shape_that_gets_asked(monkeypatch):
    """`source_record` calls the infohash `torrent_hash`; a search result
    calls it `hash`. `_find` wanted the second and was handed the first, so
    the provider raised, the exception was swallowed as "could not list", and
    the whole feature was silently absent. One shape does not become the
    other by hoping."""
    from pinky.debrid import torbox

    client = torbox.TorBox()
    asked = {}

    def create(source):
        asked.update(source)
        return {"id": 1, "cached": True, "download_finished": True,
                "files": [{"id": 2, "name": "x.srt"}]}

    # `_create`, not `_find`: the expensive fallback downloads the whole
    # account and this runs once per row of a picker somebody is waiting on.
    monkeypatch.setattr(client, "_create", create)
    monkeypatch.setattr(client, "configured", lambda: True)

    files, _link_for = client.sidecar_subtitles({"torrent_hash": "b" * 40})
    assert asked.get("hash") == "b" * 40
    assert len(files) == 1


def test_no_infohash_means_no_lookup(monkeypatch):
    from pinky.debrid import torbox

    client = torbox.TorBox()
    monkeypatch.setattr(client, "configured", lambda: True)
    monkeypatch.setattr(client, "_create",
                        lambda source: (_ for _ in ()).throw(AssertionError("asked")))
    files, _link = client.sidecar_subtitles({})
    assert files == []


def test_the_account_list_is_never_downloaded_for_a_decoration(monkeypatch):
    """`_find` falls back to `_existing`, which cannot ask about one hash and
    so pulls the whole account - 466 KB and two to four seconds - and this
    runs once per row of a picker somebody is waiting on."""
    from pinky.debrid import torbox

    client = torbox.TorBox()
    monkeypatch.setattr(client, "configured", lambda: True)
    monkeypatch.setattr(client, "_create", lambda source: None)
    monkeypatch.setattr(client, "_existing",
                        lambda h: (_ for _ in ()).throw(AssertionError("account list")))
    assert client.sidecar_subtitles({"torrent_hash": "d" * 40})[0] == []

# -*- coding: utf-8 -*-
import os
import threading

import english
import subtitles


def test_what_stremio_addresses():
    assert subtitles.parse_address("tt0426711:2:1") == {
        "scheme": "imdb", "id": "tt0426711", "season": 2, "episode": 1}
    assert subtitles.parse_address("tt0111161")["episode"] == 0
    assert subtitles.parse_address("kitsu:114:31") == {
        "scheme": "kitsu", "id": "114", "season": 1, "episode": 31}
    assert subtitles.parse_address("kv:kan:2:abc") is None


def test_the_answer_is_immediate_and_points_at_the_file(monkeypatch):
    started = []
    monkeypatch.setattr(subtitles, "start",
                        lambda *args, **kwargs: started.append(args))
    found = subtitles.handle("tt0426711:2:1",
                             {"filename": "Hikaru.No.Go.TV.EP31.mkv"},
                             "http://127.0.0.1:7000/t")
    key = subtitles.key_for("tt0426711:2:1", "Hikaru.No.Go.TV.EP31.mkv")
    assert found == [{"id": "katan-he", "lang": "heb",
                      "url": "http://127.0.0.1:7000/t/sub/%s.srt" % key}]
    assert started and started[0][0] == key


def test_foreign_ids_get_nothing():
    assert subtitles.handle("kv:kan:2:abc", {}, "http://x") == []


def test_serving_waits_for_the_job_then_gives_the_file(monkeypatch):
    from katan.subs import srt

    key = "0123456789abcdef"
    job = subtitles.Job(key)
    subtitles._jobs[key] = job

    def finish():
        subtitles.save(key, [srt.Cue(1, 1.0, 2.0, "שלום")])
        job.done.set()

    threading.Timer(0.2, finish).start()
    data = subtitles.serve(key)
    assert "שלום" in data.decode("utf-8")
    os.remove(subtitles.path_for(key))


def test_an_unfinished_translation_is_served_as_far_as_it_got(monkeypatch):
    from katan.subs import srt

    monkeypatch.setattr(subtitles, "WAIT_SECONDS", 0.05)
    key = "fedcba9876543210"
    job = subtitles.Job(key)
    job.partial = [srt.Cue(1, 1.0, 2.0, "חצי"), srt.Cue(2, 3.0, 4.0, "half")]
    subtitles._jobs[key] = job
    assert "חצי" in subtitles.serve(key).decode("utf-8")


def test_no_job_no_file():
    assert subtitles.serve("aaaaaaaaaaaaaaaa") is None


# --------------------------------------------------------------------------
# the next episode's file name
# --------------------------------------------------------------------------


def test_next_filename():
    cases = [
        ("Hikaru.No.Go.TV.EP31.BluRay.1080p.AC3.x264-CHD.mkv", [1, 31],
         "Hikaru.No.Go.TV.EP32.BluRay.1080p.AC3.x264-CHD.mkv"),
        ("[BlueLobster] Hikaru no Go - 31 [480p].mkv", [1, 31],
         "[BlueLobster] Hikaru no Go - 32 [480p].mkv"),
        ("Show.S02E09.1080p.WEB-GRP.mkv", [9], "Show.S02E10.1080p.WEB-GRP.mkv"),
        ("[Group] Title - 09 [1080p][ABCDEF12].mkv", [9],
         "[Group] Title - 10 [1080p][ABCDEF12].mkv"),
        ("[Koten_Gars] Hikaru no Go - 02 [BD][h.264][1080p][AC3+FLAC] [DBFA7AE5].mkv",
         [2], "[Koten_Gars] Hikaru no Go - 03 [BD][h.264][1080p][AC3+FLAC] [DBFA7AE5].mkv"),
    ]
    for name, numbers, expected in cases:
        assert subtitles.next_filename(name, numbers) == expected, name
    assert subtitles.next_filename("Movie.2019.1080p.mkv", [5]) == ""
    assert subtitles.next_filename("", [1]) == ""


# --------------------------------------------------------------------------
# English sources
# --------------------------------------------------------------------------

KOTEN_GARS = [
    {"id": 17, "type": "other", "info": {"name": "lt-54313.TTF"}, "size": 62536},
    {"id": 279370, "type": "subtitle",
     "info": {"codec": "ASS", "lang": "eng", "name": "Signs & Songs [KG]",
              "default": 1, "forced": 0}, "size": 20621},
    {"id": 279371, "type": "subtitle",
     "info": {"codec": "ASS", "lang": "eng", "name": "English Dialogue [KG]",
              "default": 0, "forced": 0}, "size": 50774},
    {"id": 279372, "type": "subtitle",
     "info": {"codec": "ASS", "lang": "eng", "name": "R1 [DVD]",
              "default": 0, "forced": 0}, "size": 31634},
    {"id": 279377, "type": "chapters", "size": 3841},
]


def test_the_dialogue_track_not_signs_and_songs():
    assert english.pick_track(KOTEN_GARS)["id"] == 279371


def test_only_signs_is_nothing():
    assert english.pick_track(KOTEN_GARS[:2]) is None


def test_same_file_ignores_folders_and_case():
    assert english.same_file("Batch/[G] Show - 01.MKV", "[g] show - 01.mkv")
    assert not english.same_file("[G] Show - 02.mkv", "[G] Show - 01.mkv")


def test_animetosho_search_queries():
    assert english._queries(
        "[Koten_Gars] Hikaru no Go - 02 [BD][h.264][1080p][AC3+FLAC] [DBFA7AE5].mkv") == [
        "[Koten_Gars] Hikaru no Go - 02 [BD][h.264][1080p][AC3+FLAC]",
        "[Koten_Gars] Hikaru no Go - 02"]


def test_kitsunekko_listings():
    index = ('<a href="/dirlist.php?dir=subtitles%2F">up</a>'
             '<a href="/dirlist.php?dir=subtitles%2FHikaru+no+Go%2F">Hikaru no Go</a>'
             '<a href="/dirlist.php?dir=subtitles%2F.hack+Quantum%2F">x</a>')
    assert english.parse_folders(index) == {"hikaru no go": "Hikaru no Go",
                                            "hack quantum": ".hack Quantum"}
    folder = '<a href="subtitles/Hikaru no Go/hikaru.no.go.zip">zip</a>'
    assert english.parse_files(folder) == ["subtitles/Hikaru no Go/hikaru.no.go.zip"]


def test_the_subtitle_made_for_the_same_release_wins():
    playing = "Hikaru.No.Go.TV.EP31.BluRay.1080p.AC3.x264-CHD.mkv"
    same = english.likeness("Hikaru.No.Go.TV.EP31.BluRay.1080p.AC3.x264-CHD.srt",
                            playing)
    other = english.likeness("[Judas] Hikaru no Go - 31.ass", playing)
    assert same > other


def test_kitsunekko_picks_the_episode_out_of_a_zip(monkeypatch):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for n in (30, 31, 32):
            archive.writestr(
                "Hikaru.No.Go.TV.EP%02d.BluRay.1080p.AC3.x264-CHD.srt" % n,
                "1\n00:00:01,000 --> 00:00:02,000\nEpisode %d\n" % n)
    monkeypatch.setattr(english, "_folders",
                        lambda: {"hikaru no go": "Hikaru no Go"})
    monkeypatch.setattr(english, "_folder_files",
                        lambda folder: ["subtitles/Hikaru no Go/hikaru.no.go.zip"])
    monkeypatch.setattr(english, "_download", lambda path: buffer.getvalue())
    cues, label = english.from_kitsunekko(
        ["Hikaru no Go"], 2, 1, 31,
        "Hikaru.No.Go.TV.EP31.BluRay.1080p.AC3.x264-CHD.mkv")
    assert cues[0].text == "Episode 31"
    assert "EP31" in label

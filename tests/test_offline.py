# -*- coding: utf-8 -*-
"""Downloading for offline viewing: the index, and the bytes.

The two things worth testing hard are the ones that cost somebody a film:
a resume that appends the wrong bytes to a part file, and a disk-full that
gets retried for ever. Both have a test here that fails without its guard.
"""
import io
import json
import os

import pytest

from pinky import kodi
from pinky.offline import downloader, library, queueing


# --------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch, settings_module):
    """A profile and a downloads folder of this test's own."""
    profile = tmp_path / "profile"
    downloads = tmp_path / "films"
    profile.mkdir(exist_ok=True)
    downloads.mkdir(exist_ok=True)
    monkeypatch.setattr(kodi, "profile_path", lambda: str(profile))
    monkeypatch.setattr(kodi, "subdir", lambda *parts: str(downloads))
    settings_module.set("offline.folder", str(downloads))
    settings_module.set("offline.subtitles", "false")
    library._seen.update(stamp=None, data={})
    monkeypatch.setattr(kodi, "notify", lambda *a, **kw: None)
    return {"profile": str(profile), "downloads": str(downloads)}


SOURCE = {"title": "A Film 1080p BluRay", "hash": "a" * 40,
          "provider": "torrentio", "quality": "1080p", "cached": True,
          "cached_by": "torbox", "file_size": 2048, "size": 2048}

META = {"type": "movie", "ids": {"tmdb": 278, "imdb": "tt0111161"},
        "title": "The Shawshank Redemption", "season": 0, "episode": 0,
        "item": {"type": "movie", "ids": {"tmdb": 278},
                 "title": "The Shawshank Redemption", "year": 1994,
                 "duration": 8520, "art": {"poster": "p.jpg"},
                 "plot": "Andy digs.", "extra": {"trailer": "x"}}}


class FakeRaw(object):
    """response.raw, handing out the body in the sizes it is asked for."""

    def __init__(self, body, break_after=None):
        self.body = body
        self.at = 0
        self.break_after = break_after
        self.reads = 0

    def read(self, amount=None, decode_content=True):
        assert decode_content is False, (
            "reading with decode_content=True pulls the whole film into "
            "memory - see urlsession._Raw.read")
        self.reads += 1
        if self.break_after is not None and self.reads > self.break_after:
            return b""
        chunk = self.body[self.at:self.at + (amount or len(self.body))]
        self.at += len(chunk)
        return chunk


class FakeResponse(object):
    def __init__(self, body, status=200, headers=None, break_after=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self.raw = FakeRaw(body, break_after)
        self.closed = False

    def close(self):
        self.closed = True


def serve(monkeypatch, body, status=200, headers=None, break_after=None,
          seen=None):
    """Make every http.get answer with this body, and record the headers."""
    from pinky import http

    def fake_get(url, **kwargs):
        if seen is not None:
            seen.append(kwargs.get("headers") or {})
        return FakeResponse(body, status, headers, break_after)

    monkeypatch.setattr(http, "get", fake_get)


def queued(monkeypatch, body=b"", size=None):
    """One queued movie whose link resolves and whose body is `body`."""
    monkeypatch.setattr(downloader, "_link", lambda record: "http://x/f.mkv")
    source = dict(SOURCE)
    if size is not None:
        source["file_size"] = size
    return queueing.one(META, source)


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------


def test_a_queued_film_is_remembered_with_its_snapshot():
    key = queueing.one(META, dict(SOURCE))
    assert key == "movie:tmdb:278"

    record = library.entry(key)
    assert record["state"] == library.QUEUED
    assert record["item"]["title"] == "The Shawshank Redemption"
    assert record["item"]["art"]["poster"] == "p.jpg", \
        "the row has to draw with no network, so the art is stored"
    assert record["file"].endswith(".mkv")


def test_no_debrid_link_is_ever_written_to_the_disk():
    """A resolved link carries the account token. That rule is the reason
    resume re-resolves instead of reusing what it started with."""
    queueing.one(META, dict(SOURCE, url="https://cdn/secret?token=hunter2"))
    written = io.open(library.path(), encoding="utf-8").read()
    assert "hunter2" not in written and "token" not in written
    assert "url" not in json.loads(written)["movie:tmdb:278"]["source"]


def test_the_same_film_is_not_queued_twice():
    assert queueing.one(META, dict(SOURCE))
    assert queueing.one(META, dict(SOURCE)) == "", \
        "a second copy would write to the first one's filename"


def test_an_uncached_source_is_refused(monkeypatch):
    said = []
    monkeypatch.setattr(kodi, "notify", lambda *a, **kw: said.append(a))
    assert queueing.one(META, dict(SOURCE, cached=False)) == ""
    assert said, "the reason belongs on screen"
    assert library.all_entries() == {}


def test_a_hebrew_title_gets_a_filename_the_disk_will_take():
    """str.isalnum() is true of Hebrew, so "strip to alphanumerics" stripped
    nothing and Android refused the write."""
    meta = dict(META, item=dict(META["item"], title=u"רמזור",
                                original_title=u"רמזור"))
    key = queueing.one(meta, dict(SOURCE))
    name = os.path.basename(library.entry(key)["file"])
    assert name.isascii() and name.endswith(".mkv"), name


def test_an_unreadable_index_starts_afresh_rather_than_raising():
    queueing.one(META, dict(SOURCE))
    with io.open(library.path(), "w", encoding="utf-8") as handle:
        handle.write(u"{half a fi")
    library._seen.update(stamp=None, data={})
    assert library.all_entries() == {}, "the films are still on the disk"


def test_removing_a_download_takes_its_part_file_and_subtitles(fresh):
    key = queueing.one(META, dict(SOURCE))
    target = library.entry(key)["file"]
    stem = os.path.splitext(target)[0]
    for name in (target, target + ".part", stem + ".he.srt", stem + ".en.srt"):
        io.open(name, "w", encoding="utf-8").write(u"x")
    keep = os.path.join(fresh["downloads"], "something.else.srt")
    io.open(keep, "w", encoding="utf-8").write(u"x")

    assert library.remove(key) is True
    assert library.entry(key) is None
    for name in (target, target + ".part", stem + ".he.srt", stem + ".en.srt"):
        assert not os.path.exists(name), name
    assert os.path.exists(keep), "another film's subtitle is not ours to delete"


def test_clearing_takes_everything():
    queueing.one(META, dict(SOURCE))
    episode = dict(META, type="episode", season=2, episode=4)
    queueing.one(episode, dict(SOURCE, hash="b" * 40))
    assert len(library.all_entries()) == 2
    assert library.clear_all() == 2
    assert library.all_entries() == {}


def test_find_works_from_route_parameters_alone():
    """The whole point: no TMDB call, because offline there is no TMDB."""
    key = queueing.one(META, dict(SOURCE))
    io.open(library.entry(key)["file"], "w", encoding="utf-8").write(u"film")
    library.update(key, state=library.DONE)

    found = library.find({"type": "movie", "tmdb": "278", "imdb": None})
    assert found is not None and found["key"] == key


def test_a_film_deleted_behind_our_back_is_streamed_instead():
    key = queueing.one(META, dict(SOURCE))
    library.update(key, state=library.DONE)
    assert library.find({"type": "movie", "tmdb": "278"}) is None, \
        "the record says done and the file is gone, so stream it"


# --------------------------------------------------------------------------
# the bytes
# --------------------------------------------------------------------------


def test_a_clean_download_lands_as_a_whole_file(monkeypatch):
    body = b"x" * 2048
    key = queued(monkeypatch, size=2048)
    serve(monkeypatch, body, 200, {"Content-Length": "2048"})

    assert downloader.run() == 1
    record = library.entry(key)
    assert record["state"] == library.DONE
    assert io.open(record["file"], "rb").read() == body
    assert not os.path.exists(record["file"] + ".part"), \
        "the part file is renamed, not left beside the film"


def test_a_resume_asks_for_the_rest_and_appends_it(monkeypatch):
    key = queued(monkeypatch, size=2048)
    part = library.entry(key)["file"] + ".part"
    with open(part, "wb") as handle:
        handle.write(b"a" * 1000)

    sent = []
    serve(monkeypatch, b"b" * 1048, 206,
          {"Content-Range": "bytes 1000-2047/2048",
           "Content-Length": "1048"}, seen=sent)

    assert downloader.run() == 1
    assert sent[0]["Range"] == "bytes=1000-"
    body = io.open(library.entry(key)["file"], "rb").read()
    assert body == b"a" * 1000 + b"b" * 1048


def test_a_server_that_ignores_range_starts_again_rather_than_corrupting(
        monkeypatch):
    """A 200 to a Range request is the whole film. Appending it to what is
    already here gives a file of the right size and the wrong contents, which
    plays for a while and then does not."""
    key = queued(monkeypatch, size=2048)
    part = library.entry(key)["file"] + ".part"
    with open(part, "wb") as handle:
        handle.write(b"a" * 1000)

    serve(monkeypatch, b"x" * 2048, 200, {"Content-Length": "2048"})
    assert downloader.run() == 1
    assert io.open(library.entry(key)["file"], "rb").read() == b"x" * 2048


def test_a_short_read_keeps_the_part_file_and_stays_queued(monkeypatch):
    key = queued(monkeypatch, size=200000)
    serve(monkeypatch, b"x" * 200000, 200, {"Content-Length": "200000"},
          break_after=2)         # two chunks, then the connection goes

    assert downloader.run() == 0
    record = library.entry(key)
    assert record["state"] == library.QUEUED, "it has to be retried"
    assert os.path.exists(record["file"] + ".part")
    assert not os.path.exists(record["file"]), \
        "half a film must never be playable by accident"


def test_a_full_disk_is_refused_and_not_retried_for_ever(monkeypatch):
    """Marked failed, not queued. Queued would fill the disk again every
    minute, for ever, with a notification each time."""
    import shutil

    key = queued(monkeypatch, size=50 * 1024 ** 3)
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: type("U", (), {"free": 2 * 1024 ** 3})())
    serve(monkeypatch, b"x" * 16)

    assert downloader.run() == 0
    record = library.entry(key)
    assert record["state"] == library.FAILED
    assert record["note"]
    assert library.next_queued() is None, "nothing is waiting any more"


def test_an_abort_leaves_it_resumable(monkeypatch):
    class Stopping(object):
        def abortRequested(self):
            return True

    key = queued(monkeypatch, size=2048)
    serve(monkeypatch, b"x" * 2048, 200, {"Content-Length": "2048"})

    assert downloader.run(Stopping()) == 0
    assert library.entry(key)["state"] in (library.QUEUED, library.RUNNING)
    assert not os.path.exists(library.entry(key)["file"])


def test_a_link_that_cannot_be_minted_fails_with_a_reason(monkeypatch):
    monkeypatch.setattr(downloader, "_link", lambda record: "")
    key = queueing.one(META, dict(SOURCE))
    assert downloader.run() == 0
    assert library.entry(key)["state"] == library.FAILED
    assert library.entry(key)["note"]


def test_the_link_is_minted_from_the_stored_source_not_a_saved_url(
        monkeypatch):
    from pinky import play

    asked = []

    def fake_resolve(source):
        asked.append(dict(source))
        source["file_id"] = 7           # what a real resolve records
        return "http://x/f.mkv"

    monkeypatch.setattr(play, "_resolve", fake_resolve)
    key = queueing.one(META, dict(SOURCE, url="https://cdn/old?token=stale"))
    serve(monkeypatch, b"x" * 16, 200, {"Content-Length": "16"})

    downloader.run()
    assert asked and "url" not in asked[0], \
        "a saved url would be returned straight back out of _resolve"
    assert asked[0]["hash"] == "a" * 40
    assert library.entry(key)["source"]["file_id"] == 7, \
        "which file it is, kept so the next resume picks the same one"


# --------------------------------------------------------------------------
# a whole season
# --------------------------------------------------------------------------


def test_a_season_queues_every_episode_without_searching(monkeypatch):
    episodes = [{"type": "episode", "season": 3, "episode": n,
                 "title": "Ep %d" % n, "ids": {}, "art": {},
                 "extra": {}} for n in (1, 2, 3)]
    assert queueing.season(1399, 3, episodes, {"title": "A Show",
                                               "art": {"poster": "s.jpg"}}) == 3

    records = library.all_entries()
    assert len(records) == 3
    one = records["episode:tmdb:1399:3:2"]
    assert one["source"] == {}, "the service searches, not the screen"
    assert one["item"]["show_title"] == "A Show"
    assert one["item"]["extra"]["tmdb_show"] == 1399


def test_an_episode_queued_without_a_source_gets_one_from_the_service(
        monkeypatch):
    from pinky import play
    from pinky.sources import aggregator

    episodes = [{"type": "episode", "season": 3, "episode": 1, "ids": {},
                 "title": "Ep 1", "art": {}, "extra": {}}]
    queueing.season(1399, 3, episodes, {"title": "A Show", "art": {}})

    monkeypatch.setattr(play, "build_meta", lambda request: dict(
        META, type="episode", title="A Show", season=3, episode=1))
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: [
        dict(SOURCE, title="uncached", cached=False),
        dict(SOURCE, title="the cached one", cached=True)])
    monkeypatch.setattr(downloader, "_link", lambda record: "http://x/f.mkv")
    serve(monkeypatch, b"x" * 2048, 200, {"Content-Length": "2048"})

    assert downloader.run() == 1
    stored = library.entry("episode:tmdb:1399:3:1")["source"]
    assert stored["title"] == "the cached one", \
        "an uncached source would spend the hourly torrent-add budget"

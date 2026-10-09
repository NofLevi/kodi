# -*- coding: utf-8 -*-
"""The console: the log, the add-on's files, and the repairs, from a laptop.

Everything that went wrong on the projector so far was diagnosed by pulling
the log over FTP and putting a file back. This does that from the add-on
itself - which means it is the add-on, not an FTP server, deciding what may
be read and what may be written.
"""
import io
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from pinky import webconsole


# --------------------------------------------------------------------------
# what it may touch
# --------------------------------------------------------------------------


def test_the_data_folder_may_be_changed_and_the_code_may_not():
    """Reading a shipped file answers "is the box running what I think";
    deleting one answers nothing and breaks the add-on."""
    found = webconsole.roots()
    assert found["data"][1] is True
    assert found["addon"][1] is False


def test_a_path_that_climbs_out_is_simply_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(webconsole, "roots",
                        lambda: {"data": (str(tmp_path), True)})
    for attempt in ("data/../../secrets", "data/../..", "../etc/passwd",
                    "elsewhere/x", "data/sub/../../../out"):
        full, _writable = webconsole.resolve(attempt)
        assert full == "", attempt


def test_a_path_inside_the_root_resolves(tmp_path, monkeypatch):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "settings.xml").write_text("<settings/>")
    monkeypatch.setattr(webconsole, "roots",
                        lambda: {"data": (str(tmp_path), True)})
    full, writable = webconsole.resolve("data/sub/settings.xml")
    assert full == os.path.realpath(str(tmp_path / "sub" / "settings.xml"))
    assert writable is True


def test_the_listing_offers_delete_only_where_writing_is_allowed(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cache.db").write_bytes(b"x" * 2048)
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "addon.xml").write_text("<addon/>")
    monkeypatch.setattr(webconsole, "roots", lambda: {
        "data": (str(tmp_path / "data"), True),
        "addon": (str(tmp_path / "code"), False)})
    writable = webconsole.listing("data")
    read_only = webconsole.listing("addon")
    assert "cache.db" in writable and "wipe(" in writable
    assert "addon.xml" in read_only and "wipe(" not in read_only


def test_the_tail_is_the_end_of_the_file(tmp_path):
    path = tmp_path / "kodi.log"
    path.write_text("\n".join("line %d" % n for n in range(5000)))
    text = webconsole.tail(str(path), limit=200)
    assert len(text) <= 200
    assert "line 4999" in text


def test_the_tail_of_nothing_is_nothing():
    assert webconsole.tail("") == ""
    assert webconsole.tail("/no/such/file.log") == ""


# --------------------------------------------------------------------------
# the repairs
# --------------------------------------------------------------------------


def test_clearing_the_cache_clears_the_cache(settings_module):
    from pinky import cache

    cache.set("console.test", {"a": 1}, 600)
    assert cache.get("console.test") is not None
    webconsole.run_action("cache")
    assert cache.get("console.test") is None


def test_rebuilding_the_rows_asks_the_catalogue(monkeypatch):
    from pinky import catalog

    called = []
    monkeypatch.setattr(catalog, "invalidate", lambda *a, **k: called.append("invalidate"))
    monkeypatch.setattr(catalog, "warm", lambda *a, **k: called.append("warm"))
    webconsole.run_action("rows")
    assert called == ["invalidate", "warm"]


def test_an_unknown_repair_says_so():
    assert webconsole.run_action("nonsense") == webconsole.kodi.localize(32599)


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _post(url, fields=None, timeout=10):
    data = urllib.parse.urlencode(fields or {}).encode("utf-8")
    with urllib.request.urlopen(url, data=data, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


@pytest.fixture
def console(tmp_path, monkeypatch):
    """A console whose roots are a sandbox, with a log to read."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.xml").write_text("<settings/>")
    (data / "throwaway.srt").write_text("1\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "kodi.log").write_text("the last line of the log\n")
    monkeypatch.setattr(webconsole, "roots", lambda: {
        "data": (str(data), True), "logs": (str(logs), False)})
    monkeypatch.setattr(webconsole, "log_path", lambda: str(logs / "kodi.log"))
    return data


def _visit(errand, lifetime=5):
    """Open the console, do something with it, close it."""
    done = threading.Event()
    results = {}

    def ready(url):
        try:
            errand(url, results)
        except Exception as error:          # reported, not swallowed
            results["error"] = "%s: %s" % (type(error).__name__, error)
        finally:
            done.set()

    webconsole.serve(lifetime=lifetime, on_ready=ready, cancelled=done)
    assert "error" not in results, results.get("error")
    return results


def test_the_page_and_the_log_are_served(console):
    def errand(url, results):
        results["page"] = _get(url)
        results["log"] = _get(url + "log")

    results = _visit(errand)
    assert "Pinky" in results["page"] and "<form" not in results["page"]
    assert "the last line of the log" in results["log"]


def test_the_whole_log_can_be_downloaded(console):
    def errand(url, results):
        results["whole"] = _get(url + "log?whole=1")

    assert "the last line of the log" in _visit(errand)["whole"]


def test_files_can_be_browsed_and_fetched(console):
    def errand(url, results):
        results["roots"] = _get(url + "files?path=")
        results["data"] = _get(url + "files?path=data")
        results["file"] = _get(url + "file?path=" + urllib.parse.quote("data/settings.xml"))

    results = _visit(errand)
    assert "data/" in results["roots"]
    assert "settings.xml" in results["data"]
    assert results["file"] == "<settings/>"


def test_a_file_can_be_deleted_inside_the_data_folder(console):
    def errand(url, results):
        results["reply"] = _post(url + "delete", {"path": "data/throwaway.srt"})

    _visit(errand)
    assert not (console / "throwaway.srt").exists()
    assert (console / "settings.xml").exists()


def test_nothing_outside_the_roots_can_be_deleted(console, tmp_path):
    outsider = tmp_path / "precious.txt"
    outsider.write_text("keep me")

    def errand(url, results):
        _post(url + "delete", {"path": "data/../precious.txt"})
        _post(url + "delete", {"path": "../precious.txt"})

    _visit(errand)
    assert outsider.exists(), "a path that climbed out was followed"


def test_a_repair_can_be_run_from_the_page(console, monkeypatch):
    ran = []
    monkeypatch.setattr(webconsole, "run_action", lambda name: ran.append(name) or "done")

    def errand(url, results):
        results["said"] = _post(url + "run?what=cache")

    assert _visit(errand)["said"] == "done"
    assert ran == ["cache"]


def test_the_address_without_the_code_is_not_the_console(console):
    def errand(url, results):
        root = url.rsplit("/", 2)[0]
        for attempt in (root + "/", root + "/log", root + "/wrongcode/log"):
            try:
                _get(attempt)
                results.setdefault("leaked", []).append(attempt)
            except urllib.error.HTTPError as error:
                assert error.code == 404, attempt

    assert not _visit(errand).get("leaked")

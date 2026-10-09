"""Upgrading must never cost the viewer their keys.

Twenty-two credentials live in this add-on - four debrid services, Trakt,
TMDB, MDBList, OpenSubtitles, Gemini. Re-entering those on a projector
with a remote control is the difference between an update people accept and
one they refuse, so the promise this file holds is: an update changes the code
and nothing else.

That promise is kept by where things are written, not by care taken during the
update. Kodi preserves `userdata/addon_data`, and the add-on writes nothing
inside its own folder. The first test is the one that matters, because it is
the one that would notice if that ever stopped being true.
"""
import io
import os
import sqlite3
import zipfile

import pytest

from conftest import ADDON_DIR


CREDENTIAL_MARKERS = ("apikey", "token", "secret", "password", "client_id",
                      "_key", "user", "refresh", "expires")


def credential_keys(settings):
    return sorted(key for key in settings.DEFAULTS
                  if any(marker in key for marker in CREDENTIAL_MARKERS))


# --------------------------------------------------------------------------
# where state lives, which is the whole promise
# --------------------------------------------------------------------------


def test_the_addon_writes_nothing_inside_its_own_folder():
    """An update replaces the add-on folder, so anything written there is lost.

    This reads the source rather than the behaviour on purpose: the failure it
    guards against is somebody adding a write months from now, and by the time
    a runtime test could see it the data is already being lost.
    """
    import re

    lib = os.path.join(ADDON_DIR, "resources", "lib", "pinky")
    writes = re.compile(r"open\s*\(\s*[^)]*addon_path\(\)|"
                        r"makedirs\s*\(\s*[^)]*addon_path\(\)|"
                        r"srt\.write\s*\(\s*[^)]*addon_path\(\)")

    offenders = []
    for folder, dirs, files in os.walk(lib):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with io.open(path, encoding="utf-8") as handle:
                if writes.search(handle.read()):
                    offenders.append(os.path.relpath(path, lib))

    assert not offenders, (
        "these write inside the add-on folder, which an update wipes: %s"
        % offenders)


def test_every_runtime_path_is_under_the_profile(settings_module):
    from pinky import cache, kodi

    profile = os.path.normcase(kodi.profile_path())
    for path in (cache.db_path(), kodi.subdir("subtitles"), kodi.subdir("qr")):
        assert os.path.normcase(path).startswith(profile), path


# --------------------------------------------------------------------------
# settings across a version bump
# --------------------------------------------------------------------------


def test_every_credential_survives_a_version_bump(settings_module):
    """The add-on version changing must not touch a single stored value."""
    import xbmcaddon

    keys = credential_keys(settings_module)
    assert len(keys) > 15, "expected the full set of credentials, found %d" % len(keys)

    for index, key in enumerate(keys):
        settings_module.set(key, "value-%d" % index)

    xbmcaddon.INFO["version"] = "0.2.0"     # what an update changes
    from pinky import kodi
    kodi.refresh_addon()

    for index, key in enumerate(keys):
        assert settings_module.get(key) == "value-%d" % index, key


def test_a_setting_stored_but_no_longer_declared_does_not_break_reading(
        settings_module):
    """A renamed or removed id leaves its old value behind in the file."""
    import xbmcaddon

    xbmcaddon.SETTINGS["pinky.some.retired.setting"] = "leftover"
    settings_module.set("tmdb.apikey", "still here")

    assert settings_module.get("tmdb.apikey") == "still here"
    assert settings_module.get("pinky.some.retired.setting") == "leftover"


def test_a_setting_missing_from_storage_falls_back_to_its_default(
        settings_module):
    """A new release adds settings the stored file has never heard of."""
    import xbmcaddon

    xbmcaddon.SETTINGS.clear()
    assert settings_module.get("sources.max_resolution") == \
        settings_module.DEFAULTS["sources.max_resolution"]
    assert settings_module.get_bool("subs.auto") is True


def test_a_garbage_value_does_not_crash_a_typed_read(settings_module):
    """A hand-edited settings file is a real thing people do."""
    settings_module.set("sources.workers", "not a number")
    settings_module.set("cache.max_mb", "")

    assert settings_module.get_int("sources.workers", 4) == 4
    assert settings_module.get_int("cache.max_mb", 25) == 25
    assert settings_module.get_bool("subs.auto") in (True, False)


# --------------------------------------------------------------------------
# the cache across a schema change
# --------------------------------------------------------------------------


def _write_old_cache(path, columns="key TEXT PRIMARY KEY, value BLOB"):
    """A cache.db as an older release would have left it."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE kv (%s)" % columns)
    conn.execute("INSERT INTO kv (key, value) VALUES ('old', 'data')")
    conn.commit()
    conn.close()


def test_a_cache_from_an_older_schema_still_works(settings_module):
    """CREATE TABLE IF NOT EXISTS silently accepts a table of the wrong shape.

    Every read and write then fails on a missing column, and because the cache
    swallows its own errors the add-on keeps running with a cache that stores
    nothing - slow, and with no symptom pointing at the cause.
    """
    from pinky import cache

    cache.close()
    _write_old_cache(cache.db_path())

    cache.set("after-upgrade", {"hello": "world"}, 60)
    assert cache.get("after-upgrade") == {"hello": "world"}, \
        "the cache stopped storing anything after a schema change"


def test_a_corrupt_cache_is_rebuilt_rather_than_fatal(settings_module):
    from pinky import cache

    cache.close()
    with io.open(cache.db_path(), "wb") as handle:
        handle.write(b"this is not a database, it is a truncated download")

    cache.set("k", {"v": 1}, 60)
    assert cache.get("k") == {"v": 1}


def test_a_cache_directory_that_does_not_exist_yet_is_created(settings_module):
    """A fresh install has no profile directory until something writes."""
    import shutil

    from pinky import cache, kodi

    cache.close()
    shutil.rmtree(kodi.profile_path(), ignore_errors=True)

    cache.set("k", {"v": 1}, 60)
    assert cache.get("k") == {"v": 1}


# --------------------------------------------------------------------------
# installing a release
# --------------------------------------------------------------------------


def _release_zip(path, version="0.2.0", addon_xml=True, corrupt=False):
    with zipfile.ZipFile(path, "w") as archive:
        if addon_xml:
            archive.writestr(
                "plugin.video.pinky/addon.xml",
                '<?xml version="1.0"?>\n<addon id="plugin.video.pinky" '
                'version="%s" name="Pinky"/>' % version)
        archive.writestr("plugin.video.pinky/main.py", "print('hi')\n")
    if corrupt:
        with io.open(path, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            handle.truncate(handle.tell() // 2)
    return path


def test_release_with_wrong_embedded_identity_is_refused(tmp_path):
    from pinky import updater
    path = str(tmp_path / "wrong-id.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.video.pinky/addon.xml",
                         '<addon id="repository.unrelated" version="9.9.9"/>')
        archive.writestr("plugin.video.pinky/main.py", "pass\n")
    assert updater._is_sane_zip(path) is False


def test_release_version_must_match_the_index(tmp_path):
    from pinky import updater
    path = _release_zip(str(tmp_path / "wrong-version.zip"), version="9.9.9")
    assert updater._is_sane_zip(path, expected_version="1.2.3") is False


def test_a_good_release_passes_the_sanity_check(tmp_path):
    from pinky import updater

    path = _release_zip(str(tmp_path / "good.zip"))
    assert updater._is_sane_zip(path) is True


@pytest.mark.parametrize("make,why", [
    (lambda p: _release_zip(p, corrupt=True), "a truncated download"),
    (lambda p: _release_zip(p, addon_xml=False), "no addon.xml"),
])
def test_a_bad_release_is_refused(tmp_path, make, why):
    """A projector on wifi produces half-downloads, and installing one would
    leave an add-on that cannot start."""
    from pinky import updater

    assert updater._is_sane_zip(make(str(tmp_path / "bad.zip"))) is False, why


def test_something_that_is_not_a_zip_at_all_is_refused(tmp_path):
    from pinky import updater

    path = str(tmp_path / "notazip.zip")
    with io.open(path, "wb") as handle:
        handle.write(b"<html>404 Not Found</html>")
    assert updater._is_sane_zip(path) is False


def test_a_release_with_unparseable_addon_xml_is_refused(tmp_path):
    from pinky import updater

    path = str(tmp_path / "broken.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.video.pinky/addon.xml", "<addon><not closed")
    assert updater._is_sane_zip(path) is False


@pytest.mark.parametrize("installed,latest,newer", [
    ("0.1.0", "0.1.1", True),
    ("0.1.0", "0.2.0", True),
    ("0.1.0", "1.0.0", True),
    ("0.2.0", "0.1.9", False),
    ("0.1.1", "0.1.1", False),
    ("0.9.0", "0.10.0", True),
])
def test_version_comparison_is_numeric_not_alphabetical(installed, latest, newer):
    """0.10.0 sorts before 0.9.0 as text, which would skip a release."""
    from pinky import updater

    assert (updater.parse_version(latest) >
            updater.parse_version(installed)) is newer


def test_an_unreadable_version_never_looks_newer():
    from pinky import updater

    assert updater.parse_version("") == (0, 0, 0)
    assert updater.parse_version(None) == (0, 0, 0)
    assert updater.parse_version("garbage") == (0, 0, 0)


def test_check_says_nothing_when_the_index_is_unreachable(monkeypatch):
    from pinky import http, updater

    monkeypatch.setattr(http, "get", lambda *a, **k: None)
    assert updater.check() is None


def test_check_says_nothing_when_the_index_is_not_xml(monkeypatch):
    from pinky import http, updater

    class Response(object):
        status_code = 200
        content = b"<html>this is a login page</html>"

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    assert updater.check() is None


def test_check_builds_the_zip_url_from_the_index_url(monkeypatch,
                                                     settings_module):
    from pinky import http, updater

    settings_module.set("update.url", "https://example.pages.dev/addons.xml")

    class Response(object):
        status_code = 200
        content = (b'<addons><addon id="plugin.video.pinky" version="9.9.9"/>'
                   b'</addons>')

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    latest, zip_url = updater.check()
    assert latest == "9.9.9"
    assert zip_url == ("https://example.pages.dev/zips/plugin.video.pinky/"
                       "plugin.video.pinky-9.9.9.zip")


def test_an_index_that_does_not_list_this_addon_is_not_an_update(monkeypatch):
    from pinky import http, updater

    class Response(object):
        status_code = 200
        content = b'<addons><addon id="repository.pinky" version="9.9.9"/></addons>'

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    assert updater.check() is None


def test_the_cache_rebuilds_even_while_another_connection_holds_the_file(
        settings_module):
    """Two threads holding a connection each is the normal state here: the
    service writes while the UI reads. On Windows the open handle stops the
    file being deleted, so a rebuild that only knows how to delete would leave
    the cache broken for the rest of the session."""
    from pinky import cache

    cache.close()
    _write_old_cache(cache.db_path())

    holder = sqlite3.connect(cache.db_path())
    holder.execute("SELECT 1").fetchone()
    try:
        cache.set("k", {"v": 1}, 60)
        assert cache.get("k") == {"v": 1}
    finally:
        holder.close()


# --------------------------------------------------------------------------
# the whole thing, on the real add-on
# --------------------------------------------------------------------------


def test_installing_a_real_release_keeps_every_key(tmp_path, monkeypatch):
    """The promise of this file, exercised rather than reasoned about.

    Builds a release from the actual add-on tree, installs it over an actual
    installed copy, and checks that the version moved and the viewer's keys,
    their subtitles and their profile did not.
    """
    import xbmcaddon

    from pinky import kodi, updater

    addons = tmp_path / "addons"
    addons.mkdir()
    installed = addons / "plugin.video.pinky"
    profile = tmp_path / "addon_data" / "plugin.video.pinky"
    profile.mkdir(parents=True)

    import shutil
    shutil.copytree(ADDON_DIR, str(installed))

    (profile / "settings.xml").write_text(
        '<settings version="2">\n'
        '  <setting id="tmdb.apikey">MY-TMDB-KEY</setting>\n'
        '  <setting id="torbox.apikey">MY-TORBOX-KEY</setting>\n'
        "</settings>\n", encoding="utf-8")
    (profile / "subtitles").mkdir()
    (profile / "subtitles" / "kept.he.srt").write_text("1\n", encoding="utf-8")

    xbmcaddon.reset(str(profile), str(installed))
    xbmcaddon.INFO["version"] = "0.1.0"
    kodi.refresh_addon()

    # The version this add-on currently declares, read rather than spelled
    # out: hard-coding it meant the first release ever cut broke this test,
    # and a test that fails on release day teaches people to ignore it.
    import xml.etree.ElementTree as ET
    current = ET.parse(os.path.join(ADDON_DIR, "addon.xml")).getroot().get("version")

    release = str(tmp_path / "release.zip")
    with zipfile.ZipFile(release, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                full = os.path.join(folder, name)
                arc = "plugin.video.pinky/" + os.path.relpath(
                    full, ADDON_DIR).replace(os.sep, "/")
                if arc == "plugin.video.pinky/addon.xml":
                    with io.open(full, encoding="utf-8") as handle:
                        archive.writestr(arc, handle.read().replace(
                            'version="%s"' % current, 'version="0.9.9"', 1))
                else:
                    archive.write(full, arc)

    assert updater._is_sane_zip(release), "the real add-on failed its own check"
    assert updater.apply(release) is True

    on_disk = ET.parse(str(installed / "addon.xml")).getroot().get("version")
    assert on_disk == "0.9.9", "the update did not land"

    kept = (profile / "settings.xml").read_text(encoding="utf-8")
    assert "MY-TMDB-KEY" in kept and "MY-TORBOX-KEY" in kept
    assert (profile / "subtitles" / "kept.he.srt").is_file()

    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.pinky"]
    assert not leftovers, "staging or backup left behind: %s" % leftovers


def test_the_addon_path_may_end_in_a_separator(tmp_path, monkeypatch):
    r"""Real Kodi hands one back with a trailing slash. The stub does not.

    That single character broke the whole update on a real box while every
    test here passed: `target + ".old"` named a file *inside* the folder being
    replaced instead of a sibling of it, and os.path.dirname returned the
    add-on folder itself, so the staging directory was unpacked inside the
    thing it was meant to replace.

        OSError: [WinError 87] The parameter is incorrect:
          '...\addons\plugin.video.pinky\'
          -> '...\addons\plugin.video.pinky\.old'
    """
    import zipfile
    from pinky import kodi, updater

    addons = tmp_path / "addons"
    installed = addons / "plugin.video.pinky"
    (installed / "resources").mkdir(parents=True)
    (installed / "addon.xml").write_text(
        '<addon id="plugin.video.pinky" version="0.1.1"/>', encoding="utf-8")

    release = str(tmp_path / "release.zip")
    with zipfile.ZipFile(release, "w") as archive:
        archive.writestr("plugin.video.pinky/addon.xml",
                         '<addon id="plugin.video.pinky" version="0.9.9"/>')
        archive.writestr("plugin.video.pinky/main.py", "pass\n")
        archive.writestr("plugin.video.pinky/resources/marker.txt", "new")

    # The trailing separator is the whole point of this test.
    monkeypatch.setattr(kodi, "addon_path", lambda: str(installed) + os.sep)
    assert updater.apply(release) is True, "the trailing separator broke it"

    import xml.etree.ElementTree as ET
    assert ET.parse(str(installed / "addon.xml")).getroot().get("version") == "0.9.9"
    assert (installed / "resources" / "marker.txt").is_file()
    # And nothing was left inside the add-on folder or beside it.
    assert not (installed / ".old").exists()
    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.pinky"]
    assert not leftovers, "left behind %s" % leftovers


def test_the_whole_upgrade_runs_over_http(tmp_path, monkeypatch):
    """Tools -> Check for updates, end to end, over a real socket.

    Every part of this was already covered alone - the index parse, the
    version compare, the zip sanity check, the folder swap. What was not
    covered is the join: an index served over HTTP, a zip fetched from the
    URL *derived* from it, and the add-on that comes out the other side. That
    join is where the real defects have been, because the download URL is
    built from the version rather than stored.

    Served locally rather than from the published site, so the test does not
    depend on the internet or on which release happens to be live.
    """
    import shutil
    import threading
    import xml.etree.ElementTree as ET
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    import xbmcaddon

    from pinky import kodi, updater

    addons = tmp_path / "addons"
    addons.mkdir()
    installed = addons / "plugin.video.pinky"
    profile = tmp_path / "addon_data" / "plugin.video.pinky"
    profile.mkdir(parents=True)
    shutil.copytree(ADDON_DIR, str(installed))
    (profile / "settings.xml").write_text(
        '<settings version="2">\n'
        '  <setting id="tmdb.apikey">MY-TMDB-KEY</setting>\n'
        "</settings>\n", encoding="utf-8")

    xbmcaddon.reset(str(profile), str(installed))
    xbmcaddon.INFO["version"] = "0.0.1"
    kodi.refresh_addon()

    # A published repository, laid out exactly as build.py writes one.
    site = tmp_path / "site"
    (site / "zips" / "plugin.video.pinky").mkdir(parents=True)
    (site / "addons.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<addons><addon id="plugin.video.pinky" version="0.0.2"/></addons>',
        encoding="utf-8")

    current = ET.parse(os.path.join(ADDON_DIR, "addon.xml")).getroot().get("version")
    release = str(site / "zips" / "plugin.video.pinky"
                  / "plugin.video.pinky-0.0.2.zip")
    with zipfile.ZipFile(release, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                full = os.path.join(folder, name)
                arc = "plugin.video.pinky/" + os.path.relpath(
                    full, ADDON_DIR).replace(os.sep, "/")
                if arc == "plugin.video.pinky/addon.xml":
                    with io.open(full, encoding="utf-8") as handle:
                        archive.writestr(arc, handle.read().replace(
                            'version="%s"' % current, 'version="0.0.2"', 1))
                else:
                    archive.write(full, arc)

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site), **kwargs)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % server.server_address[1]
        monkeypatch.setattr(updater, "index_url", lambda: base + "/addons.xml")

        # The lookup, over HTTP.
        found = updater.check()
        assert found is not None, "an update was published and not offered"
        latest, zip_url = found
        assert latest == "0.0.2"
        assert zip_url == (base + "/zips/plugin.video.pinky"
                           "/plugin.video.pinky-0.0.2.zip")

        # The download, over HTTP, including its own sanity check.
        path = updater.download(zip_url)
        assert path, "the release did not download"
        try:
            assert updater.apply(path) is True
        finally:
            updater._remove(path)
    finally:
        server.shutdown()

    on_disk = ET.parse(str(installed / "addon.xml")).getroot().get("version")
    assert on_disk == "0.0.2", "the upgrade did not land"
    assert "MY-TMDB-KEY" in (profile / "settings.xml").read_text(encoding="utf-8")
    assert (installed / "resources" / "lib" / "pinky" / "updater.py").is_file()
    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.pinky"]
    assert not leftovers, "left behind %s" % leftovers


# --------------------------------------------------------------------------
# a release that changes a great deal, which is the case nobody rehearses
# --------------------------------------------------------------------------

def _install(tmp_path):
    """A real installed copy with real user state beside it."""
    import shutil

    import xbmcaddon
    from pinky import kodi

    addons = tmp_path / "addons"
    addons.mkdir()
    installed = addons / "plugin.video.pinky"
    profile = tmp_path / "addon_data" / "plugin.video.pinky"
    profile.mkdir(parents=True)
    shutil.copytree(ADDON_DIR, str(installed))

    (profile / "settings.xml").write_text(
        '<settings version="2">\n'
        '  <setting id="tmdb.apikey">MY-TMDB-KEY</setting>\n'
        '  <setting id="torbox.apikey">MY-TORBOX-KEY</setting>\n'
        "</settings>\n", encoding="utf-8")
    (profile / "subtitles").mkdir()
    (profile / "subtitles" / "kept.he.srt").write_text("1\n", encoding="utf-8")
    (profile / "cache.db").write_bytes(b"SQLite format 3\x00")

    xbmcaddon.reset(str(profile), str(installed))
    kodi.refresh_addon()
    return addons, installed, profile


def _release(tmp_path, version, drop=(), add=(), name="release.zip"):
    """A release zip built from the real tree, with files taken out and put in.

    Built by hand rather than with tools/build.py because the point is to
    describe a release that differs a lot from the installed one, and the
    builder can only describe the tree as it is.
    """
    import xml.etree.ElementTree as ET

    current = ET.parse(os.path.join(ADDON_DIR, "addon.xml")).getroot().get(
        "version")
    drop = set(drop)
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(ADDON_DIR):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for entry in files:
                full = os.path.join(folder, entry)
                relative = os.path.relpath(full, ADDON_DIR).replace(os.sep, "/")
                if relative in drop:
                    continue
                arc = "plugin.video.pinky/" + relative
                if relative == "addon.xml":
                    with io.open(full, encoding="utf-8") as handle:
                        archive.writestr(arc, handle.read().replace(
                            'version="%s"' % current,
                            'version="%s"' % version, 1))
                else:
                    archive.write(full, arc)
        for relative, body in add:
            archive.writestr("plugin.video.pinky/" + relative, body)
    return path


def test_a_release_that_changes_a_great_deal_still_lands(tmp_path):
    """Every upgrade so far has been one patch version and a few files.

    A rewrite is the case that has never been rehearsed, and it is the one
    where the interesting failure lives: a module the new release dropped is
    still on disk, still importable, and still shadowing whatever replaced it.
    `apply` renames the folder rather than merging into it, so removal is by
    construction - but nothing here had ever checked that, and "by
    construction" is exactly the kind of claim that stops being true quietly.
    """
    from pinky import updater

    addons, installed, profile = _install(tmp_path)

    # Take out a whole subsystem and put a differently shaped one back.
    lib = os.path.join("resources", "lib", "pinky")
    doomed = sorted(
        os.path.join(lib, "subs", name).replace(os.sep, "/")
        for name in os.listdir(os.path.join(ADDON_DIR, lib, "subs"))
        if name.endswith(".py"))
    assert len(doomed) > 5, "expected a subsystem worth deleting"

    newcomers = [("resources/lib/pinky/captions/__init__.py", "X = 1\n")]
    newcomers += [("resources/lib/pinky/captions/part%02d.py" % i,
                   "VALUE = %d\n" % i) for i in range(40)]

    zip_path = _release(tmp_path, "3.0.0", drop=doomed, add=newcomers)
    assert updater.apply(zip_path) is True

    import xml.etree.ElementTree as ET
    landed = ET.parse(str(installed / "addon.xml")).getroot().get("version")
    assert landed == "3.0.0", "a major jump did not land"

    for relative in doomed:
        assert not (installed / relative).exists(), \
            "%s survived a release that dropped it" % relative
    for relative, _body in newcomers:
        assert (installed / relative).is_file(), \
            "%s never arrived" % relative
    # The folder itself survives - its subpackages were not dropped - but
    # nothing that was taken out may still be sitting in it.
    survivors = [n for n in os.listdir(
        str(installed / "resources" / "lib" / "pinky" / "subs"))
        if n.endswith(".py")]
    assert not survivors, "dropped modules still on disk: %s" % survivors

    # And the whole reason any of this is careful.
    kept = (profile / "settings.xml").read_text(encoding="utf-8")
    assert "MY-TMDB-KEY" in kept and "MY-TORBOX-KEY" in kept
    assert (profile / "subtitles" / "kept.he.srt").is_file()
    assert (profile / "cache.db").read_bytes().startswith(b"SQLite format 3")

    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.pinky"]
    assert not leftovers, "staging or backup left behind: %s" % leftovers


def test_a_swap_that_fails_puts_the_installed_copy_back(tmp_path, monkeypatch):
    """The rollback exists and has never been made to run.

    Everything else refuses a bad release before touching the installed copy.
    This is the other failure: the zip is fine, the old folder has already
    been renamed out of the way, and putting the new one in place is what
    fails - a projector with no space left, or Android holding a file open.
    Getting that wrong leaves no add-on at all rather than an old one.
    """
    from pinky import updater

    addons, installed, profile = _install(tmp_path)
    before = sorted(os.listdir(str(installed)))

    real_rename = os.rename
    calls = []

    def rename(source, destination):
        calls.append(source)
        # The first rename moves the installed copy aside; the second puts
        # the new one in. Fail that one, exactly as a full disk would.
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real_rename(source, destination)

    monkeypatch.setattr(os, "rename", rename)

    zip_path = _release(tmp_path, "3.0.0")
    assert updater.apply(zip_path) is False, "a failed swap reported success"

    # Without this the test passes for the wrong reason: any earlier refusal
    # also returns False and leaves the installed copy alone, and then this
    # proves nothing about the rollback it is named after.
    assert len(calls) >= 3, (
        "the swap was never reached, so no rollback happened: %d renames"
        % len(calls))

    assert installed.is_dir(), "the add-on is gone entirely"
    assert sorted(os.listdir(str(installed))) == before, \
        "the installed copy came back changed"
    assert (profile / "settings.xml").read_text(encoding="utf-8").count(
        "MY-TMDB-KEY") == 1

    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.pinky"]
    assert not leftovers, "left behind after a failure: %s" % leftovers


# --------------------------------------------------------------------------
# the test channel: what the branch is now, not what was released
# --------------------------------------------------------------------------

def _index(version):
    return ('<?xml version="1.0" encoding="UTF-8"?><addons>'
            '<addon id="plugin.video.pinky" version="%s"/></addons>'
            % version).encode("utf-8")


class _Answer(object):
    status_code = 200

    def __init__(self, content):
        self.content = content


@pytest.mark.parametrize("channel,expected", [
    ("stable", "github.com/NofLevi/kodi/releases/latest"),
    ("test", "raw.githubusercontent.com/NofLevi/kodi/test-channel"),
])
def test_the_channel_chooses_the_index(settings_module, channel, expected):
    from pinky import updater

    settings_module.set("update.channel", channel)
    assert expected in updater.index_url()


def test_an_explicit_url_still_beats_the_channel(settings_module):
    """`update.url` is the escape hatch for the host moving. It has to win."""
    from pinky import updater

    settings_module.set("update.channel", "test")
    settings_module.set("update.url", "https://elsewhere.example/addons.xml")
    assert updater.index_url() == "https://elsewhere.example/addons.xml"


@pytest.mark.parametrize("installed,published,offered", [
    # Two test builds in a row sort *equal* under parse_version, because the
    # pre-release tag is past the three numbers it reads. A greater-than would
    # never offer the second one.
    ("0.0.5~dev.11", "0.0.5~dev.12", True),
    # And going back to stable means installing something numerically older,
    # which a greater-than would refuse outright, stranding anyone who tried
    # a test build on that test build for ever.
    ("0.0.5~dev.12", "0.0.4", True),
    # The same build is not an update.
    ("0.0.5~dev.12", "0.0.5~dev.12", False),
])
def test_the_test_channel_offers_any_difference(settings_module, monkeypatch,
                                                installed, published, offered):
    import xbmcaddon

    from pinky import http, kodi, updater

    settings_module.set("update.channel", "test")
    xbmcaddon.INFO["version"] = installed
    kodi.refresh_addon()
    monkeypatch.setattr(http, "get",
                        lambda *a, **k: _Answer(_index(published)))

    result = updater.check()
    assert bool(result) is offered, \
        "installed %s, published %s" % (installed, published)
    if offered:
        assert result[0] == published
        assert "raw.githubusercontent.com/NofLevi/kodi/test-channel" in result[1]


def test_the_stable_channel_still_refuses_to_go_backwards(settings_module,
                                                          monkeypatch):
    """The looser rule must not leak into the channel most people are on."""
    import xbmcaddon

    from pinky import http, kodi, updater

    settings_module.set("update.channel", "stable")
    xbmcaddon.INFO["version"] = "0.0.9"
    kodi.refresh_addon()
    monkeypatch.setattr(http, "get", lambda *a, **k: _Answer(_index("0.0.4")))
    assert updater.check() is None


# --------------------------------------------------------------------------
# what an update leaves behind
# --------------------------------------------------------------------------


def test_an_update_removes_everything_it_leaves_behind(tmp_path, monkeypatch):
    """The folder swap is not the whole story.

    The add-on's own source is replaced wholesale, so the previous version
    cannot survive inside it. Everything *around* it can, and does: a backup
    whose removal lost a race, staging from a process that was killed, and -
    the one that actually accumulates - Kodi's package cache, which keeps one
    zip per release forever and is where somebody finds an old one months
    later and installs it by hand.
    """
    from pinky import kodi, updater

    addons = tmp_path / "addons"
    packages = addons / "packages"
    packages.mkdir(parents=True)
    temporary = tmp_path / "temp"
    temporary.mkdir()
    # Kodi's own temp, not Python's: there is none of Python's on Android.
    monkeypatch.setattr(kodi, "temp_dir", lambda: str(temporary))

    (addons / "plugin.video.pinky.old").mkdir()
    (addons / "plugin.video.pinky.old" / "addon.xml").write_text("<addon/>")
    (addons / "pinky-staging-abc123").mkdir()
    (addons / "pinky-staging-abc123" / "junk.txt").write_text("x")
    (packages / "plugin.video.pinky-0.0.1.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (packages / "plugin.video.pinky-0.0.2.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (temporary / "pinky-update-old.zip").write_bytes(b"PK")

    # Not ours, and not to be touched.
    (addons / "plugin.video.other").mkdir()
    (packages / "plugin.video.elsewhere-1.0.0.zip").write_bytes(b"PK")
    (temporary / "somebody-else.zip").write_bytes(b"PK")

    removed = updater.sweep(str(addons))

    assert not (addons / "plugin.video.pinky.old").exists()
    assert not (addons / "pinky-staging-abc123").exists()
    assert not (packages / "plugin.video.pinky-0.0.1.zip").exists()
    assert not (packages / "plugin.video.pinky-0.0.2.zip").exists()
    assert not (temporary / "pinky-update-old.zip").exists()
    assert len(removed) == 5

    assert (addons / "plugin.video.other").is_dir(), "removed another add-on"
    assert (packages / "plugin.video.elsewhere-1.0.0.zip").is_file()
    assert (temporary / "somebody-else.zip").is_file()


def test_the_zip_being_installed_is_never_swept(tmp_path, monkeypatch):
    """`keep` exists because the install is still holding this file.

    apply() sweeps on success, and on the repository path the zip it was
    handed lives in `packages/` - the very folder being cleared. Removing it
    there would be pulling the file out from under the install that is still
    reading it.
    """
    import tempfile as tempfile_module

    from pinky import updater

    addons = tmp_path / "addons"
    packages = addons / "packages"
    packages.mkdir(parents=True)
    monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(tmp_path))

    current = packages / "plugin.video.pinky-0.0.2.zip"
    current.write_bytes(b"PK")
    (packages / "plugin.video.pinky-0.0.1.zip").write_bytes(b"PK")

    updater.sweep(str(addons), keep=str(current))

    assert current.is_file(), "swept the zip it was installing"
    assert not (packages / "plugin.video.pinky-0.0.1.zip").exists()


def test_a_real_update_sweeps_as_part_of_installing(tmp_path, monkeypatch):
    """End to end: apply() itself does the tidying, not the caller.

    Anything that has to be remembered separately is a thing that will be
    forgotten, and this is meant to happen on every update however it was
    started - the Tools entry, or Kodi's own repository.
    """
    import tempfile as tempfile_module

    from pinky import kodi, updater

    addons = tmp_path / "addons"
    installed = addons / "plugin.video.pinky"
    packages = addons / "packages"
    packages.mkdir(parents=True)
    installed.mkdir()
    (installed / "addon.xml").write_text(
        '<addon id="plugin.video.pinky" version="0.0.1"/>', encoding="utf-8")
    (installed / "gone-upstream.py").write_text("# deleted in the new version")
    monkeypatch.setattr(tempfile_module, "gettempdir", lambda: str(tmp_path))

    # Leftovers of the kind a previous update strews about.
    (addons / "plugin.video.pinky.old").mkdir()
    (packages / "plugin.video.pinky-0.0.1.zip").write_bytes(b"PK")

    release = str(tmp_path / "release.zip")
    with zipfile.ZipFile(release, "w") as archive:
        archive.writestr("plugin.video.pinky/addon.xml",
                         '<addon id="plugin.video.pinky" version="0.0.2"/>')
        archive.writestr("plugin.video.pinky/main.py", "# new\n")

    monkeypatch.setattr(kodi, "addon_path", lambda: str(installed))
    monkeypatch.setattr(updater, "_is_sane_zip",
                        lambda path, expected_version=None: True)
    assert updater.apply(release) is True

    import xml.etree.ElementTree as ET
    assert ET.parse(str(installed / "addon.xml")).getroot().get(
        "version") == "0.0.2"
    assert not (installed / "gone-upstream.py").exists(), \
        "a file deleted upstream survived the update"
    assert not (addons / "plugin.video.pinky.old").exists()
    assert not (packages / "plugin.video.pinky-0.0.1.zip").exists()
    leftovers = [n for n in os.listdir(str(addons))
                 if n not in ("plugin.video.pinky", "packages")]
    assert not leftovers, "left behind %s" % leftovers


# --------------------------------------------------------------------------
# where the zip is depends on who serves the index
# --------------------------------------------------------------------------


def test_a_release_asset_sits_beside_the_index_not_under_a_tree():
    """GitHub Releases has no directory tree; a Pages repository is one.

    The Kodi repository layout is `<datadir>/<id>/<id>-<version>.zip`, and the
    Pages site is exactly that. A release's assets are flat names under
    `/releases/latest/download/`, so deriving the repository path there asks
    for a URL that has never existed - which fails as "no update available"
    rather than as an error anybody would notice.
    """
    from pinky import updater

    release = updater.zip_url_for(
        "https://github.com/NofLevi/kodi/releases/latest/download/addons.xml",
        "0.0.7")
    assert release == ("https://github.com/NofLevi/kodi/releases/latest/"
                       "download/plugin.video.pinky-0.0.7.zip")

    pages = updater.zip_url_for("https://noflevi.github.io/kodi/addons.xml",
                                "0.0.7")
    assert pages == ("https://noflevi.github.io/kodi/zips/"
                     "plugin.video.pinky/plugin.video.pinky-0.0.7.zip")

    # The test channel is a branch, which is a tree like the Pages site.
    branch = updater.zip_url_for(
        "https://raw.githubusercontent.com/NofLevi/kodi/test-channel/"
        "addons.xml", "0.0.7~dev.3")
    assert branch.endswith(
        "test-channel/zips/plugin.video.pinky/"
        "plugin.video.pinky-0.0.7~dev.3.zip")


def test_the_shipped_update_url_is_the_release_alias():
    """`latest` rather than a version, or every release needs a code change."""
    from pinky import updater

    assert updater.DEFAULT_INDEX == (
        "https://github.com/NofLevi/kodi/releases/latest/download/addons.xml")
    assert "/releases/latest/" in updater.DEFAULT_INDEX


# Every account setting this add-on has ever shipped. A name that disappears
# from settings.xml is a value Kodi drops on the next save - a key or a token
# gone on update, which is the one thing an update must never cost. Add to
# this list; never take from it without a migration that carries the value.
ACCOUNT_SETTINGS_EVER_SHIPPED = (
    "alldebrid.apikey", "mdblist.apikey", "premiumize.apikey",
    "premiumize.client_id", "premiumize.token", "realdebrid.client_id",
    "realdebrid.client_secret", "realdebrid.expires", "realdebrid.refresh",
    "realdebrid.token", "subs.ai.gemini_key", "subs.ai.openai_key",
    "subs.ai.openrouter_key",
    "subs.opensubtitles.apikey", "subs.opensubtitles.password",
    "subs.opensubtitles.user", "tmdb.apikey", "torbox.apikey",
    "trakt.access_token", "trakt.client_id", "trakt.client_secret",
    "trakt.expires", "trakt.refresh_token", "trakt.user",
)


def test_no_account_setting_ever_disappears_from_settings_xml(settings_module):
    import os
    import re
    xml = open(os.path.join(ADDON_DIR, "resources", "settings.xml"), encoding="utf-8").read()
    declared = set(re.findall(r'setting id="([^"]+)"', xml))
    gone = [key for key in ACCOUNT_SETTINGS_EVER_SHIPPED if key not in declared]
    assert not gone, "renamed or removed, so lost on the next update: %s" % gone
    assert set(credential_keys(settings_module)) <= set(ACCOUNT_SETTINGS_EVER_SHIPPED), \
        "a new account setting: add it to ACCOUNT_SETTINGS_EVER_SHIPPED"


class _Refreshed(object):
    def __init__(self, payload, status=200):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_a_refresh_answer_with_no_token_signs_nobody_out(settings_module, monkeypatch):
    from pinky.debrid import realdebrid
    settings_module.set_many({"realdebrid.token": "OLD", "realdebrid.refresh": "R",
                              "realdebrid.client_id": "C", "realdebrid.client_secret": "S"})
    for answer in ({}, {"access_token": ""}, ValueError("not json"), ["a list"]):
        monkeypatch.setattr(realdebrid.http, "post", lambda *a, **k: _Refreshed(answer))
        assert realdebrid.RealDebrid()._refresh() == ""
        assert settings_module.get("realdebrid.token") == "OLD"
        assert settings_module.get("realdebrid.refresh") == "R"


def test_a_trakt_refresh_that_leaves_out_the_refresh_token_keeps_it(settings_module,
                                                                     monkeypatch):
    """An empty refresh token is a sign-out three months later."""
    from pinky.meta import trakt
    settings_module.set_many({"trakt.access_token": "OLD", "trakt.refresh_token": "KEEP"})
    monkeypatch.setattr(trakt, "_get", lambda *a, **k: None)
    monkeypatch.setattr(trakt.http, "post", lambda *a, **k: _Refreshed(
        {"access_token": "NEW", "expires_in": 7776000, "created_at": 1}))
    assert trakt.refresh_token() == "NEW"
    assert settings_module.get("trakt.refresh_token") == "KEEP"
    monkeypatch.setattr(trakt.http, "post", lambda *a, **k: _Refreshed(ValueError("html")))
    assert trakt.refresh_token() == ""
    assert settings_module.get("trakt.access_token") == "NEW"


# --------------------------------------------------------------------------
# the sandbox an Android box actually gives us
#
# Measured on the projector, 9 October 2026, with 0.0.3 published and the box
# still on 0.0.2:
#
#   [Pinky] update tidy-up failed
#   FileNotFoundError ... updater.py:365 in sweep <- tempfile.gettempdir
#   [Pinky] action check_update failed
#   FileNotFoundError ... updater.py:173 in download <- tempfile ...
#
# Python's tempfile has nowhere to go inside Kodi's Android sandbox: TMPDIR is
# unset and /tmp does not exist, so the update could neither download nor
# clean up. Kodi's own special://temp exists on every platform it runs on.
# --------------------------------------------------------------------------


def _android(monkeypatch):
    """A tempfile module as useless as the one on the box."""
    import tempfile

    def nowhere(*args, **kwargs):
        raise FileNotFoundError(
            "No usable temporary directory found in ['/tmp', '/var/tmp']")

    monkeypatch.setattr(tempfile, "gettempdir", nowhere)


def test_the_temp_directory_is_kodis_own(monkeypatch):
    import os
    from pinky import kodi

    _android(monkeypatch)
    path = kodi.temp_dir()
    assert path and os.path.isdir(path), path


def test_an_update_downloads_where_tempfile_cannot(monkeypatch, tmp_path):
    """The download asked tempfile where to write and was told nothing."""
    from pinky import http, updater

    _android(monkeypatch)

    class Raw(object):
        def __init__(self):
            self.left = [b"PK\x03\x04payload"]

        def read(self, size, decode_content=False):
            return self.left.pop() if self.left else b""

    class Response(object):
        status_code = 200
        headers = {"Content-Length": "13"}
        raw = Raw()

        def close(self):
            pass

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    monkeypatch.setattr(updater, "_is_sane_zip", lambda path, expected_version=None: True)
    path = updater.download("https://example.test/pinky.zip")
    assert path, "the download produced nothing"
    assert os.path.isfile(path)
    os.remove(path)


def test_tidying_up_survives_a_box_with_no_temp(monkeypatch, tmp_path):
    """`sweep` listed tempfile's directory, which is where it threw."""
    from pinky import updater

    _android(monkeypatch)
    addons = tmp_path / "addons"
    (addons / "packages").mkdir(parents=True)
    stale = addons / "packages" / "plugin.video.pinky-0.0.1.zip"
    stale.write_bytes(b"old")
    removed = updater.sweep(str(addons))
    assert str(stale) in removed

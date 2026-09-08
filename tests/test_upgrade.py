"""Upgrading must never cost the viewer their keys.

Twenty-two credentials live in this add-on - four debrid services, Trakt,
TMDB, MDBList, OpenSubtitles, Ktuvit, Gemini. Re-entering those on a projector
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
                      "_key", "user")


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

    lib = os.path.join(ADDON_DIR, "resources", "lib", "katan")
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
    from katan import cache, kodi

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
    from katan import kodi
    kodi.refresh_addon()

    for index, key in enumerate(keys):
        assert settings_module.get(key) == "value-%d" % index, key


def test_a_setting_stored_but_no_longer_declared_does_not_break_reading(
        settings_module):
    """A renamed or removed id leaves its old value behind in the file."""
    import xbmcaddon

    xbmcaddon.SETTINGS["katan.some.retired.setting"] = "leftover"
    settings_module.set("tmdb.apikey", "still here")

    assert settings_module.get("tmdb.apikey") == "still here"
    assert settings_module.get("katan.some.retired.setting") == "leftover"


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
    from katan import cache

    cache.close()
    _write_old_cache(cache.db_path())

    cache.set("after-upgrade", {"hello": "world"}, 60)
    assert cache.get("after-upgrade") == {"hello": "world"}, \
        "the cache stopped storing anything after a schema change"


def test_a_corrupt_cache_is_rebuilt_rather_than_fatal(settings_module):
    from katan import cache

    cache.close()
    with io.open(cache.db_path(), "wb") as handle:
        handle.write(b"this is not a database, it is a truncated download")

    cache.set("k", {"v": 1}, 60)
    assert cache.get("k") == {"v": 1}


def test_a_cache_directory_that_does_not_exist_yet_is_created(settings_module):
    """A fresh install has no profile directory until something writes."""
    import shutil

    from katan import cache, kodi

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
                "plugin.video.katan/addon.xml",
                '<?xml version="1.0"?>\n<addon id="plugin.video.katan" '
                'version="%s" name="Katan"/>' % version)
        archive.writestr("plugin.video.katan/main.py", "print('hi')\n")
    if corrupt:
        with io.open(path, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            handle.truncate(handle.tell() // 2)
    return path


def test_a_good_release_passes_the_sanity_check(tmp_path):
    from katan import updater

    path = _release_zip(str(tmp_path / "good.zip"))
    assert updater._is_sane_zip(path) is True


@pytest.mark.parametrize("make,why", [
    (lambda p: _release_zip(p, corrupt=True), "a truncated download"),
    (lambda p: _release_zip(p, addon_xml=False), "no addon.xml"),
])
def test_a_bad_release_is_refused(tmp_path, make, why):
    """A projector on wifi produces half-downloads, and installing one would
    leave an add-on that cannot start."""
    from katan import updater

    assert updater._is_sane_zip(make(str(tmp_path / "bad.zip"))) is False, why


def test_something_that_is_not_a_zip_at_all_is_refused(tmp_path):
    from katan import updater

    path = str(tmp_path / "notazip.zip")
    with io.open(path, "wb") as handle:
        handle.write(b"<html>404 Not Found</html>")
    assert updater._is_sane_zip(path) is False


def test_a_release_with_unparseable_addon_xml_is_refused(tmp_path):
    from katan import updater

    path = str(tmp_path / "broken.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plugin.video.katan/addon.xml", "<addon><not closed")
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
    from katan import updater

    assert (updater.parse_version(latest) >
            updater.parse_version(installed)) is newer


def test_an_unreadable_version_never_looks_newer():
    from katan import updater

    assert updater.parse_version("") == (0, 0, 0)
    assert updater.parse_version(None) == (0, 0, 0)
    assert updater.parse_version("garbage") == (0, 0, 0)


def test_check_says_nothing_when_the_index_is_unreachable(monkeypatch):
    from katan import http, updater

    monkeypatch.setattr(http, "get", lambda *a, **k: None)
    assert updater.check() is None


def test_check_says_nothing_when_the_index_is_not_xml(monkeypatch):
    from katan import http, updater

    class Response(object):
        status_code = 200
        content = b"<html>this is a login page</html>"

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    assert updater.check() is None


def test_check_builds_the_zip_url_from_the_index_url(monkeypatch,
                                                     settings_module):
    from katan import http, updater

    settings_module.set("update.url", "https://example.pages.dev/addons.xml")

    class Response(object):
        status_code = 200
        content = (b'<addons><addon id="plugin.video.katan" version="9.9.9"/>'
                   b'</addons>')

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    latest, zip_url = updater.check()
    assert latest == "9.9.9"
    assert zip_url == ("https://example.pages.dev/zips/plugin.video.katan/"
                       "plugin.video.katan-9.9.9.zip")


def test_an_index_that_does_not_list_this_addon_is_not_an_update(monkeypatch):
    from katan import http, updater

    class Response(object):
        status_code = 200
        content = b'<addons><addon id="repository.katan" version="9.9.9"/></addons>'

    monkeypatch.setattr(http, "get", lambda *a, **k: Response())
    assert updater.check() is None


def test_the_cache_rebuilds_even_while_another_connection_holds_the_file(
        settings_module):
    """Two threads holding a connection each is the normal state here: the
    service writes while the UI reads. On Windows the open handle stops the
    file being deleted, so a rebuild that only knows how to delete would leave
    the cache broken for the rest of the session."""
    from katan import cache

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

    from katan import kodi, updater

    addons = tmp_path / "addons"
    addons.mkdir()
    installed = addons / "plugin.video.katan"
    profile = tmp_path / "addon_data" / "plugin.video.katan"
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
                arc = "plugin.video.katan/" + os.path.relpath(
                    full, ADDON_DIR).replace(os.sep, "/")
                if arc == "plugin.video.katan/addon.xml":
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

    leftovers = [n for n in os.listdir(str(addons)) if n != "plugin.video.katan"]
    assert not leftovers, "staging or backup left behind: %s" % leftovers

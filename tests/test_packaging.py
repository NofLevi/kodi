"""The built add-on must stay small and must not ship junk."""
import os
import subprocess
import sys
import zipfile

import pytest

from conftest import ROOT

# The whole point of the project is that it stays small on a weak device.
MAX_ZIP_KB = 600


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    # Into a temporary directory, never into the repository's own `repo/`.
    # Building there meant a full test run rewrote a committed artifact, which
    # made every push a Cloudflare deployment that published nothing and let a
    # zip be built from source that was never committed.
    out = str(tmp_path_factory.mktemp("repo"))
    result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build.py"),
                             "--out", out],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # Read the version rather than spell it out. Hard-coding 0.1.0 meant the
    # first release this project ever cut broke five packaging tests, which is
    # exactly the moment you least want the suite lying to you.
    import xml.etree.ElementTree as ET
    version = ET.parse(os.path.join(ROOT, "plugin.video.katan",
                                    "addon.xml")).getroot().get("version")
    path = os.path.join(out, "zips", "plugin.video.katan",
                        "plugin.video.katan-%s.zip" % version)
    assert os.path.isfile(path), result.stdout
    return path


def test_the_addon_zip_stays_small(built):
    kilobytes = os.path.getsize(built) / 1024.0
    assert kilobytes < MAX_ZIP_KB, "the add-on grew to %.0f KB" % kilobytes


def test_the_zip_contains_no_build_junk(built):
    with zipfile.ZipFile(built) as archive:
        names = archive.namelist()
    junk = [n for n in names
            if "__pycache__" in n or n.endswith((".pyc", ".orig"))
            or os.path.basename(n) in ("_fix.py", ".DS_Store")]
    assert not junk, junk


def test_the_zip_is_rooted_at_the_addon_id(built):
    """Kodi refuses an archive that does not unpack into <addon.id>/."""
    with zipfile.ZipFile(built) as archive:
        roots = {name.split("/")[0] for name in archive.namelist()}
    assert roots == {"plugin.video.katan"}


def test_the_zip_carries_everything_the_addon_needs(built):
    with zipfile.ZipFile(built) as archive:
        names = set(archive.namelist())
    for required in (
            "plugin.video.katan/addon.xml",
            "plugin.video.katan/main.py",
            "plugin.video.katan/service.py",
            "plugin.video.katan/subtitles.py",
            "plugin.video.katan/resources/settings.xml",
            "plugin.video.katan/resources/data/channels.json",
            "plugin.video.katan/resources/data/vod_series.json",
            "plugin.video.katan/resources/skins/default/1080i/katan-home.xml",
            "plugin.video.katan/resources/language/resource.language.he_il/strings.po",
    ):
        assert required in names, "missing %s" % required


def test_the_repository_index_lists_both_addons(built):
    # The freshly built one, not the committed copy: this is checking that
    # build.py writes a coherent index, and the committed one is checked
    # against the live site by tools/e2e.py instead.
    index = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(built))),
                         "addons.xml")
    text = open(index, encoding="utf-8").read()
    assert 'id="plugin.video.katan"' in text
    assert 'id="repository.katan"' in text
    checksum = open(index + ".md5", encoding="utf-8").read().strip()
    import hashlib
    assert checksum == hashlib.md5(text.encode("utf-8")).hexdigest()


def test_the_published_site_can_say_a_file_is_missing(built):
    """A 404.html, or Cloudflare answers every wrong path with 200 and HTML.

    Pages treats a site with a root index.html and no 404.html as a
    single-page application and serves that index for anything unmatched,
    status 200. `updater.check` decides on `status_code >= 400`, so a
    withdrawn or mistyped version would read as present and fail later.
    """
    out = os.path.dirname(os.path.dirname(os.path.dirname(built)))
    page = os.path.join(out, "404.html")
    assert os.path.isfile(page), "no 404.html: every missing path answers 200"

    # And it must not appear in the listings Kodi walks.
    for folder, _dirs, _files in os.walk(out):
        listing = os.path.join(folder, "index.html")
        if os.path.isfile(listing):
            assert "404.html" not in open(listing, encoding="utf-8").read()


def test_the_build_can_name_an_output_folder_on_another_drive():
    """Windows os.path.relpath raises across drives, and --out is a tempdir.

    CI checks out on D: and pytest's tmpdir is on C:, so a line that does
    nothing but print a filename raised ValueError and took every packaging
    test with it - and, because the suite runs first, the packaging check and
    the offline end-to-end run never happened on Windows at all. The whole
    half of the matrix was dark and the run had been red for days.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build

    foreign = "Z:" + os.sep + "a" + os.sep + "out.zip" if os.name == "nt" \
        else "/somewhere/else/out.zip"
    assert build.shown(foreign)
    assert build.shown(os.path.join(ROOT, "repo", "addons.xml"))

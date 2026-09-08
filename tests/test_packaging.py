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
def built():
    result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build.py")],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # Read the version rather than spell it out. Hard-coding 0.1.0 meant the
    # first release this project ever cut broke five packaging tests, which is
    # exactly the moment you least want the suite lying to you.
    import xml.etree.ElementTree as ET
    version = ET.parse(os.path.join(ROOT, "plugin.video.katan",
                                    "addon.xml")).getroot().get("version")
    path = os.path.join(ROOT, "repo", "zips", "plugin.video.katan",
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
    index = os.path.join(ROOT, "repo", "addons.xml")
    text = open(index, encoding="utf-8").read()
    assert 'id="plugin.video.katan"' in text
    assert 'id="repository.katan"' in text
    checksum = open(index + ".md5", encoding="utf-8").read().strip()
    import hashlib
    assert checksum == hashlib.md5(text.encode("utf-8")).hexdigest()

"""The built add-on must stay small and must not ship junk."""
import os
import subprocess
import sys
import zipfile

import pytest

from conftest import ROOT

# The whole point of the project is that it stays small on a weak device.
#
# 700, raised from 600 deliberately and recorded here rather than nudged when
# it next goes red. The number was set when the zip was 424 KB and it did its
# job: it caught the growth to 614. What it caught is not waste, though -
# measured member by member, the add-on is 385 KB of Python and 137 KB of the
# Israeli VOD catalogue, every field of which is drawn on a screen, and the
# largest single image is now 260x260 because that is the size it is shown at.
#
# What the limit is really protecting is not disk on an 8 GB box. It is the
# read: Kodi opens a zip by its central directory and then seeks to each
# member, so a big archive on a host that ignores ranged reads is a download
# per seek. That is why Cloudflare had to go. GitHub answers 206, so the
# ceiling can move - but it stays a ceiling, because nothing else notices an
# add-on growing a megabyte at a time.
MAX_ZIP_KB = 700


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
    version = ET.parse(os.path.join(ROOT, "plugin.video.pinky",
                                    "addon.xml")).getroot().get("version")
    path = os.path.join(out, "zips", "plugin.video.pinky",
                        "plugin.video.pinky-%s.zip" % version)
    assert os.path.isfile(path), result.stdout
    return path


def test_the_addon_zip_stays_small(built):
    kilobytes = os.path.getsize(built) / 1024.0
    if kilobytes >= MAX_ZIP_KB:
        import zipfile

        with zipfile.ZipFile(built) as archive:
            biggest = sorted(archive.infolist(),
                             key=lambda entry: -entry.compress_size)[:5]
        where = ", ".join("%s %.0f KB" % (entry.filename.split("/")[-1],
                                          entry.compress_size / 1024.0)
                          for entry in biggest)
        raise AssertionError("the add-on grew to %.0f KB; biggest: %s"
                             % (kilobytes, where))


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
    assert roots == {"plugin.video.pinky"}


def test_the_zip_carries_everything_the_addon_needs(built):
    with zipfile.ZipFile(built) as archive:
        names = set(archive.namelist())
    for required in (
            "plugin.video.pinky/addon.xml",
            "plugin.video.pinky/main.py",
            "plugin.video.pinky/service.py",
            "plugin.video.pinky/subtitles.py",
            "plugin.video.pinky/resources/settings.xml",
            "plugin.video.pinky/resources/data/channels.json",
            "plugin.video.pinky/resources/data/vod_series.json",
            "plugin.video.pinky/resources/skins/default/1080i/pinky-home.xml",
            "plugin.video.pinky/resources/language/resource.language.he_il/strings.po",
    ):
        assert required in names, "missing %s" % required


def test_agent_context_is_tracked_but_never_published(built):
    """Developer-agent context belongs in Git, not on Kodi devices or /repo."""
    context = os.path.join(ROOT, "AGENTS.md")
    assert os.path.isfile(context)
    with zipfile.ZipFile(built) as archive:
        assert not any(os.path.basename(name) == "AGENTS.md"
                       for name in archive.namelist())
    out = os.path.dirname(os.path.dirname(os.path.dirname(built)))
    assert not os.path.exists(os.path.join(out, "AGENTS.md"))


def test_the_repository_index_lists_both_addons(built):
    # The freshly built one, not the committed copy: this is checking that
    # build.py writes a coherent index, and the committed one is checked
    # against the live site by tools/e2e.py instead.
    index = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(built))),
                         "addons.xml")
    text = open(index, encoding="utf-8").read()
    assert 'id="plugin.video.pinky"' in text
    assert 'id="repository.pinky"' in text
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


def test_the_zip_is_a_function_of_the_source_alone(built):
    """Two builds of the same source must be the same bytes.

    A zip records each member's mtime, so CI's 0.0.3 and a local 0.0.3 had
    different checksums while holding the same 123 files - which makes "the
    release and the published zip are the same" impossible to check, and that
    is the one thing worth being able to check about a release. Every member
    carries one fixed timestamp instead.
    """
    with zipfile.ZipFile(built) as archive:
        entries = archive.infolist()
    stamps = {i.date_time for i in entries}
    assert stamps == {(1980, 1, 1, 0, 0, 0)}, \
        "members carry their mtime, so the zip is not reproducible: %s" % (
            sorted(stamps)[:3],)

    # And the same across machines, which took a second fix. With the
    # timestamps pinned, a Windows build and CI's Linux build still differed
    # while every one of their 123 members was identical: ZipInfo takes
    # create_system from the machine it runs on, 0 for Windows and 3 for Unix.
    systems = {i.create_system for i in entries}
    assert systems == {3}, \
        "the zip records the build machine's platform: %s" % sorted(systems)

"""Package the add-ons and refresh the repository index.

Run from the project root:

    python tools/build.py            build zips and the repository index
    python tools/build.py --check    validate without writing anything

The output layout is what a Kodi repository expects:

    repo/addons.xml
    repo/addons.xml.md5
    repo/zips/<addon.id>/<addon.id>-<version>.zip
"""
import hashlib
import io
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Where the repository is written. Overridable because the packaging test used
# to run this as-is, which rebuilt the committed `repo/` on every full test
# run: twelve commits touched it and only two changed a version, every one of
# those was a Cloudflare deployment that published nothing new, and a zip
# could be rebuilt from uncommitted source without anybody noticing.
OUTPUT = os.path.join(ROOT, "repo")
ADDONS = ["plugin.video.katan", "repository.katan"]

# Nothing here belongs in a shipped add-on.
EXCLUDE_DIRS = {"__pycache__", ".git", ".pytest_cache", ".idea", ".vscode"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".log", ".tmp")
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "_fix.py"}


def shown(path):
    """A path to print, relative to the project when that means anything.

    `os.path.relpath` **raises** on Windows when the two paths are on
    different drives, and `--out` is usually a temporary directory: CI checks
    out on `D:` and pytest's tmpdir is on `C:`, so every packaging test errored
    on a line that does nothing but print a filename. That took the Windows
    half of the matrix down with it - the suite failed before the packaging
    check and the offline end-to-end run, so neither had run since it started.
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def addon_info(addon_id):
    path = os.path.join(ROOT, addon_id, "addon.xml")
    tree = ET.parse(path)
    root = tree.getroot()
    return root, root.get("version"), path


def should_include(path, name):
    if name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES):
        return False
    return True


def build_zip(addon_id, version):
    source = os.path.join(ROOT, addon_id)
    target_dir = os.path.join(OUTPUT, "zips", addon_id)
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    target = os.path.join(target_dir, "%s-%s.zip" % (addon_id, version))

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in sorted(files):
                if not should_include(folder, name):
                    continue
                full = os.path.join(folder, name)
                relative = os.path.relpath(full, ROOT)
                archive.write(full, relative.replace(os.sep, "/"))
    return target


def build_index():
    """Merge every addon.xml into the repository index."""
    root = ET.Element("addons")
    for addon_id in ADDONS:
        node, _version, _path = addon_info(addon_id)
        root.append(node)

    text = ET.tostring(root, encoding="unicode")
    text = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + text

    index = os.path.join(OUTPUT, "addons.xml")
    with io.open(index, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    with io.open(index + ".md5", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(digest)
    return index, digest


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>%(title)s</title></head>
<body>
<h1>%(title)s</h1>
%(rows)s
</body></html>
"""

MISSING = """<!doctype html>
<html><head><meta charset="utf-8"><title>Not found</title></head>
<body>
<h1>Not found</h1>
<p>No such file in the Katan repository.</p>
<p><a href="/">back to the top</a></p>
</body></html>
"""


def write_404():
    """Make a missing file answer 404, which it stops doing on its own.

    Cloudflare Pages treats a site with a root `index.html` and no `404.html`
    as a single-page application: every unmatched path is answered with that
    index, status **200**. Harmless for an app, actively dangerous here -
    `updater.check` decides on `status_code >= 400`, and Kodi's own repository
    install would fetch this page believing it was a zip. A mistyped or
    withdrawn version would look present and fail later, somewhere less
    obvious.

    A `404.html` in the output directory takes priority over that fallback and
    is served with a real 404, which is all this needs to be.
    """
    path = os.path.join(OUTPUT, "404.html")
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(MISSING)
    return path


def write_listings():
    """An index.html in every folder, so Kodi's own file browser can walk it.

    This is what turns the published site into a Kodi *source*. Kodi browses
    HTTP by fetching the folder and reading the `<a href>` links out of
    whatever comes back - it has no other way to know what is there - and
    Cloudflare Pages serves no directory listing at all, so asking it for
    /zips/ is a 404 and the whole "add a source, install from zip" route is
    closed.

    That route is the one that matters on a television. The alternative is a
    browser or a file manager or adb on a device driven by a remote control;
    this way the address is typed once and Kodi does the rest.

    Plain anchors on purpose. Kodi is not a browser and reads the markup with
    a regular expression, so anything clever here is a listing it cannot
    read.
    """
    written = []
    for folder, _dirs, _files in os.walk(OUTPUT):
        entries = sorted(os.listdir(folder))
        rows = []
        for name in entries:
            if name in ("index.html", "404.html"):
                continue
            suffix = "/" if os.path.isdir(os.path.join(folder, name)) else ""
            rows.append('<a href="%s%s">%s%s</a><br>' % (name, suffix,
                                                         name, suffix))
        here = os.path.relpath(folder, OUTPUT).replace("\\", "/")
        title = "Katan repository" + ("" if here == "." else " - " + here)
        page = PAGE % {"title": title, "rows": "\n".join(rows)}
        path = os.path.join(folder, "index.html")
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(page)
        written.append(path)
    return written


def copy_assets(addon_id):
    """Kodi shows the icon and changelog from the repository, not the zip."""
    source = os.path.join(ROOT, addon_id)
    target = os.path.join(OUTPUT, "zips", addon_id)
    for name in ("icon.png", "fanart.jpg", "changelog.txt"):
        for candidate in (os.path.join(source, name),
                          os.path.join(source, "resources", "media", name)):
            if os.path.isfile(candidate):
                shutil.copy2(candidate, os.path.join(target, name))
                break


def check():
    problems = []
    for addon_id in ADDONS:
        try:
            node, version, path = addon_info(addon_id)
        except (ET.ParseError, IOError) as error:
            problems.append("%s: %s" % (addon_id, error))
            continue
        if not version:
            problems.append("%s has no version" % addon_id)
        for extension in node.findall("extension"):
            library = extension.get("library")
            if library and not os.path.isfile(os.path.join(ROOT, addon_id, library)):
                problems.append("%s: missing %s" % (addon_id, library))
    return problems


def main():
    global OUTPUT
    if "--out" in sys.argv:
        OUTPUT = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])

    problems = check()
    if problems:
        for problem in problems:
            print("ERROR %s" % problem)
        return 1
    if "--check" in sys.argv:
        print("all add-ons look valid")
        return 0

    if os.path.isdir(OUTPUT):
        shutil.rmtree(OUTPUT)
    os.makedirs(OUTPUT)

    for addon_id in ADDONS:
        _node, version, _path = addon_info(addon_id)
        target = build_zip(addon_id, version)
        copy_assets(addon_id)
        print("built %s (%.0f KB)"
              % (shown(target), os.path.getsize(target) / 1024.0))

    index, digest = build_index()
    print("wrote %s (md5 %s)" % (shown(index), digest))

    write_404()
    listings = write_listings()
    print("wrote %d directory listings and a 404, so Kodi can browse it as a "
          "source and a missing file still says so" % len(listings))
    return 0


if __name__ == "__main__":
    sys.exit(main())

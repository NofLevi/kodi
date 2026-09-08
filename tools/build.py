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
              % (os.path.relpath(target, ROOT), os.path.getsize(target) / 1024.0))

    index, digest = build_index()
    print("wrote %s (md5 %s)" % (os.path.relpath(index, ROOT), digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())

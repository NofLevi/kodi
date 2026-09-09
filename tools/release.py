"""Cut a release: bump the version, write the news, rebuild the repository.

    python tools/release.py            patch bump, build, ready to commit
    python tools/release.py 0.2.0      an explicit version
    python tools/release.py --minor    0.1.4 -> 0.2.0
    python tools/release.py --dry-run  say what would change, write nothing

Work happens on `development`. A release is a **tag**, which is the only thing
that publishes: `.github/workflows/release.yml` runs the suite, cuts the GitHub
release and uploads the built repository to Cloudflare Pages. Pushing code -
to either branch - never reaches a television.

The tag also fixes the changelog. `last_tag()` below asks `git describe`, and
until tags existed it always answered nothing, so the news was silently the
last eight commits rather than what had actually shipped since the last
release.

Why this exists at all: Kodi only offers an update when the version in the
repository index is higher than the one installed. The version sat at 0.1.0
through every change so far, so no device could ever have been told there was
anything new.

The news field is the changelog Kodi shows in the add-on browser, so it is
written from the commit subjects rather than by hand.
"""
import io
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build  # noqa: E402  - reuse its addon list and packaging

NEWS_LINES = 8          # Kodi truncates a long news field in the browser
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def current_version(addon_id):
    _node, version, _path = build.addon_info(addon_id)
    return version


def bump(version, part="patch"):
    match = VERSION.match(version or "")
    if not match:
        raise SystemExit("cannot bump %r: expected major.minor.patch" % version)
    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return "%d.0.0" % (major + 1)
    if part == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def commit_subjects(since_tag=None):
    """Commit subjects since the last release tag, newest first."""
    if since_tag is None:
        since_tag = last_tag()
    span = ["%s..HEAD" % since_tag] if since_tag else ["-%d" % NEWS_LINES]
    try:
        out = subprocess.check_output(
            ["git", "log", "--format=%s"] + span, cwd=ROOT,
            stderr=subprocess.DEVNULL).decode("utf-8", "replace")
    except (subprocess.CalledProcessError, OSError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def last_tag():
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT,
            stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def news_text(version, subjects):
    lines = ["v%s" % version]
    lines.extend("- %s" % subject for subject in subjects[:NEWS_LINES])
    return "\n".join(lines)


def set_version(addon_id, version, news=None):
    """Rewrite addon.xml in place, touching only the version and the news.

    Done textually rather than by re-serialising the tree: ElementTree would
    reorder attributes and drop the comments, and a diff nobody can read is a
    diff nobody reviews.
    """
    path = os.path.join(ROOT, addon_id, "addon.xml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    updated = re.sub(r'(<addon\b[^>]*?\bversion=")[^"]*(")',
                     lambda m: m.group(1) + version + m.group(2), text, count=1)

    if news is not None and "<news>" in updated:
        escaped = news.replace("&", "&amp;").replace("<", "&lt;")
        updated = re.sub(r"<news>.*?</news>",
                         lambda m: "<news>%s</news>" % escaped,
                         updated, count=1, flags=re.S)

    if updated == text:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    return True


def update_readme(repo_version):
    """Point the README's download links at the release just built.

    The links name a file - Cloudflare Pages serves no directory listing, so
    ".../repository.katan/" is a 404 and there is nothing stable to link to.
    That means the version is in the URL, and a version in a URL that nothing
    updates is a broken link one release later.
    """
    path = os.path.join(ROOT, "README.md")
    if not os.path.isfile(path):
        return
    with io.open(path, encoding="utf-8") as handle:
        text = handle.read()
    updated = re.sub(r"repository\.katan-\d+\.\d+\.\d+\.zip",
                     "repository.katan-%s.zip" % repo_version, text)
    if updated == text:
        return
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
    print("README download links -> %s" % repo_version)


def tests_pass():
    print("running the suite")
    result = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                            cwd=ROOT)
    return result.returncode == 0


def main():
    args = [a for a in sys.argv[1:]]
    dry_run = "--dry-run" in args
    skip_tests = "--skip-tests" in args
    part = ("major" if "--major" in args
            else "minor" if "--minor" in args else "patch")
    explicit = next((a for a in args if VERSION.match(a)), None)

    addon_id = build.ADDONS[0]
    old = current_version(addon_id)
    new = explicit or bump(old, part)
    if VERSION.match(new) is None:
        raise SystemExit("bad version %r" % new)

    subjects = commit_subjects()
    news = news_text(new, subjects)

    print("%s -> %s" % (old, new))
    print(news)

    if dry_run:
        print("\ndry run, nothing written")
        return 0

    if not skip_tests and not tests_pass():
        print("suite is red, not releasing")
        return 1

    set_version(addon_id, new, news)
    # The repository add-on carries its own version; bump it too so a change
    # to its URLs actually reaches devices that already have it.
    repo_version = bump(current_version(build.ADDONS[1]), "patch")
    set_version(build.ADDONS[1], repo_version)
    update_readme(repo_version)

    if build.main() != 0:
        return 1

    print("\nreleased %s. Now:" % new)
    print('    git commit -am "Release %s"' % new)
    print("    git push origin development")
    print("    git checkout main && git merge development")
    print('    git tag -a v%s -m "Release %s"' % (new, new))
    print("    git push origin main --follow-tags")
    print("\nThe tag is what publishes - it runs the suite, cuts the GitHub")
    print("release and uploads to Pages. Pushing main alone does nothing,")
    print("and an unannotated tag is not pushed by --follow-tags.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

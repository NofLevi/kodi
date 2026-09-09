# -*- coding: utf-8 -*-
"""Publish the repository by hand, when the tag did not.

**This is the escape hatch, not the route.** Releases publish themselves:
`git push origin main --follow-tags` runs `.github/workflows/release.yml`,
which builds and uploads exactly as this does. Reach for this when that failed
after the release was already created.

Cloudflare is **not connected to the repository**, and that is the whole
design. A Pages project that watches a Git repository records every push -
"No deployment available" - whatever it decides to do about it, and no setting
suppresses those rows. Disconnecting the repository is the only thing that
does, and it turns the project into one that can only be published to by
upload. So a deployment now exists if and only if somebody ran this or tagged
a release.

Cloudflare's own documentation says a Git-connected project cannot be switched
to Direct Upload, which is true of *switching* - the dashboard's Disconnect
button is not that, and after pressing it `wrangler pages deploy` is accepted.
The `pages.dev` hostname survives, which matters more than it sounds: it is
baked into every installed copy of `repository.katan`, so a project that had
to be recreated would strand every device.

Credentials, neither of which goes in the repository:

    CLOUDFLARE_API_TOKEN     or `.cf-token`, an Account -> Pages -> Edit token
    CLOUDFLARE_ACCOUNT_ID    or `.cf-account`

    python tools/deploy.py            build, then upload
    python tools/deploy.py --dry-run  say what would happen, upload nothing

It builds rather than trusting what is on disk: `repo/` is not in the
repository - Cloudflare used to build it and now nothing does unless asked -
so publishing whatever happens to be sitting there is publishing an unknown.
"""
from __future__ import print_function

import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "repo")
PROJECT = "kodi-katan"
TEST_PROJECT = "kodi-katan-dev"


def secret(variable, filename):
    """From the environment, or from a gitignored file beside the project."""
    value = (os.environ.get(variable) or "").strip()
    if value:
        return value, "the environment"
    path = os.path.join(ROOT, filename)
    if os.path.isfile(path):
        value = io.open(path, encoding="utf-8").read().strip()
        if value:
            return value, filename
    return "", ""


def git(*args):
    return subprocess.check_output(["git"] + list(args), cwd=ROOT).decode(
        "utf-8", "replace").strip()


def problems():
    """Everything that would publish something other than a release."""
    found = []
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as error:
        return ["git is not answering: %s" % error]
    if branch != "main":
        found.append("on %s, and a release is cut from main" % branch)

    if git("status", "--porcelain", "--", "plugin.video.katan",
           "repository.katan"):
        found.append("the add-on has uncommitted changes")

    try:
        git("diff", "--quiet", "main", "origin/main")
    except subprocess.CalledProcessError:
        found.append("main and origin/main differ - push first")
    return found


def version():
    import xml.etree.ElementTree as ET
    return ET.parse(os.path.join(ROOT, "plugin.video.katan",
                                 "addon.xml")).getroot().get("version")


def stamped(version):
    """Write a version into both manifests, and put them back afterwards.

    A test build has to be distinguishable from the release it previews, and
    build.py names the zip from addon.xml - so the version has to be on disk
    while it builds. The originals are restored from what was read rather than
    from git, because `git checkout` here would also discard whatever else is
    uncommitted, which is the whole reason you are making a test build.
    """
    import contextlib

    @contextlib.contextmanager
    def swap():
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build
        import release

        was = {}
        for addon in build.ADDONS:
            path = os.path.join(ROOT, addon, "addon.xml")
            was[path] = io.open(path, encoding="utf-8", newline="").read()
        try:
            for addon in build.ADDONS:
                release.set_version(addon, version)
            yield
        finally:
            for path, text in was.items():
                io.open(path, "w", encoding="utf-8", newline="").write(text)
    return swap()


def upload(directory, project, token, account):
    environment = dict(os.environ,
                       CLOUDFLARE_API_TOKEN=token,
                       CLOUDFLARE_ACCOUNT_ID=account)
    # npx rather than a dependency: wrangler is 40 MB of Node and this project
    # has no package.json to put it in.
    return subprocess.call(
        ["npx", "--yes", "wrangler@4", "pages", "deploy", directory,
         "--project-name=" + project, "--branch=main", "--commit-dirty=true"],
        cwd=ROOT, env=environment, shell=(os.name == "nt"))


def main():
    dry = "--dry-run" in sys.argv
    test = "--test" in sys.argv

    token, token_from = secret("CLOUDFLARE_API_TOKEN", ".cf-token")
    account, account_from = secret("CLOUDFLARE_ACCOUNT_ID", ".cf-account")
    if not token or not account:
        print("no Cloudflare credentials. Create a token at\n"
              "  https://dash.cloudflare.com/profile/api-tokens\n"
              "  Custom token -> Account -> Cloudflare Pages -> Edit\n"
              "then put it in CLOUDFLARE_API_TOKEN or .cf-token, and the\n"
              "account id in CLOUDFLARE_ACCOUNT_ID or .cf-account.")
        return 1

    if test:
        # No branch or push checks: a test build is deliberately whatever is
        # in front of you, uncommitted changes included. It cannot reach
        # anybody on the stable channel.
        label = "%s~dev.%s" % (version(), git("rev-parse", "--short", "HEAD"))
        print("building %s from this working tree, for the test channel (%s)"
              % (label, TEST_PROJECT))
        if dry:
            print("dry run: nothing was built and nothing was uploaded")
            return 0
        with stamped(label):
            if subprocess.call([sys.executable,
                                os.path.join(ROOT, "tools", "build.py")],
                               cwd=ROOT) != 0:
                print("the build failed, nothing uploaded")
                return 1
            return upload(OUTPUT, TEST_PROJECT, token, account)

    found = problems()
    for problem in found:
        print("STOP  %s" % problem)
    if found:
        print("\npublishing would put something other than a release on the "
              "site")
        return 1

    print("publishing Katan %s from main (%s), credentials from %s and %s"
          % (version(), git("rev-parse", "--short", "HEAD"),
             token_from, account_from))

    if dry:
        print("dry run: nothing was built and nothing was uploaded")
        return 0

    if subprocess.call([sys.executable,
                        os.path.join(ROOT, "tools", "build.py")],
                       cwd=ROOT) != 0:
        print("the build failed, nothing uploaded")
        return 1

    return upload(OUTPUT, PROJECT, token, account)


if __name__ == "__main__":
    sys.exit(main())

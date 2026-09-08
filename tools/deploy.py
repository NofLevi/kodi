# -*- coding: utf-8 -*-
"""Publish the built repository, by hand, on purpose.

Automatic deployments are paused: pushing code no longer publishes
anything, which is how it should be - `main` moves on a release and the site
moves when somebody decides it should. What that leaves is the question of
how to say so, because Cloudflare's dashboard has no "deploy the newest
commit" button. *Retry* rebuilds the commit that is already live, and
resuming automatic deployments only helps the *next* push.

The answer Cloudflare gives for exactly this is a **deploy hook**: a URL that
triggers a build of one branch when something POSTs to it.

    Settings -> Builds & deployments -> Deploy hooks -> Add deploy hook
    name it `release`, branch `main`

That URL is a credential - anyone holding it can deploy - so it never goes in
the repository. This reads it from `CLOUDFLARE_DEPLOY_HOOK`, or from a
`.deploy-hook` file beside the project, which `.gitignore` covers.

    python tools/deploy.py            check the build, then publish
    python tools/deploy.py --dry-run  say what would happen, publish nothing

The check first is the point. A deploy hook builds whatever is on `main` at
that moment, so publishing is only safe if what is committed is what was
built - and a `repo/` that differs from a fresh build is the one way this
project has actually gone wrong.
"""
from __future__ import print_function

import io
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_FILE = os.path.join(ROOT, ".deploy-hook")


def hook_url():
    value = (os.environ.get("CLOUDFLARE_DEPLOY_HOOK") or "").strip()
    if value:
        return value, "the environment"
    if os.path.isfile(HOOK_FILE):
        value = io.open(HOOK_FILE, encoding="utf-8").read().strip()
        if value:
            return value, ".deploy-hook"
    return "", ""


def git(*args):
    return subprocess.check_output(["git"] + list(args), cwd=ROOT).decode(
        "utf-8", "replace").strip()


def problems():
    """Everything that would make this deployment publish the wrong thing."""
    found = []

    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as error:
        return ["git is not answering: %s" % error]
    if branch != "main":
        found.append("on %s, and the hook builds main" % branch)

    if git("status", "--porcelain", "--", "repo"):
        found.append("repo/ has uncommitted changes")

    try:
        git("diff", "--quiet", "main", "origin/main")
    except subprocess.CalledProcessError:
        found.append("main and origin/main differ - push first")

    # The one that matters: is the committed repo/ what a build produces?
    import shutil
    import tempfile
    staging = tempfile.mkdtemp(prefix="katan-verify-")
    try:
        subprocess.check_output([sys.executable,
                                 os.path.join(ROOT, "tools", "build.py"),
                                 "--out", staging], cwd=ROOT)
        for folder, _dirs, files in os.walk(staging):
            for name in files:
                fresh = os.path.join(folder, name)
                relative = os.path.relpath(fresh, staging)
                committed = os.path.join(ROOT, "repo", relative)
                if not os.path.isfile(committed):
                    found.append("repo/%s was never committed"
                                 % relative.replace(os.sep, "/"))
                elif name.endswith((".xml", ".md5", ".html", ".txt")):
                    # Text only: a zip differs by its timestamps alone.
                    a = io.open(fresh, encoding="utf-8").read()
                    b = io.open(committed, encoding="utf-8").read()
                    if a != b:
                        found.append("repo/%s is not what build.py writes"
                                     % relative.replace(os.sep, "/"))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return found


def main():
    dry = "--dry-run" in sys.argv
    url, where = hook_url()
    if not url:
        print("no deploy hook. Create one in the Cloudflare dashboard:\n"
              "  Settings -> Builds & deployments -> Deploy hooks\n"
              "  name it `release`, branch `main`\n"
              "then put the URL in CLOUDFLARE_DEPLOY_HOOK, or in %s"
              % os.path.relpath(HOOK_FILE, ROOT))
        return 1

    found = problems()
    for problem in found:
        print("STOP  %s" % problem)
    if found:
        print("\npublishing would put something other than the built "
              "release on the site")
        return 1

    version = ""
    try:
        import xml.etree.ElementTree as ET
        version = ET.parse(os.path.join(ROOT, "plugin.video.katan",
                                        "addon.xml")).getroot().get("version")
    except Exception:
        pass
    print("repo/ matches a fresh build; publishing Katan %s from main (%s)"
          % (version, git("rev-parse", "--short", "HEAD")))

    if dry:
        print("dry run: the hook, from %s, was not called" % where)
        return 0

    request = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", "replace")
    print("Cloudflare answered HTTP %s" % response.status)
    try:
        print(json.dumps(json.loads(body), indent=2)[:400])
    except ValueError:
        print(body[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())

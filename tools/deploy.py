# -*- coding: utf-8 -*-
"""Publish `main` by hand, when the tag did not.

**This is the escape hatch, not the route.** Releases publish themselves:
`git push origin main --follow-tags` runs `.github/workflows/release.yml`,
which fires the same hook this does. Reach for this when that failed after the
release was already created, or when there is a fix on `main` that should
reach devices without a new version.

Automatic deployments are paused, so pushing code publishes nothing, and
Cloudflare's dashboard has no "deploy the newest commit" button: *Retry*
rebuilds the commit that is already live, and resuming automatic deployments
only helps the *next* push. The answer Cloudflare gives for exactly this is a
**deploy hook** - a URL that builds one branch when something POSTs to it.

    Settings -> Builds & deployments -> Deploy hooks -> Add deploy hook
    name it `release`, branch `main`

That URL is a credential - anyone holding it can deploy - so it never goes in
the repository. This reads it from `CLOUDFLARE_DEPLOY_HOOK`, or from a
`.deploy-hook` file beside the project, which `.gitignore` covers. The same URL
is the `CLOUDFLARE_DEPLOY_HOOK` repository secret the workflow uses, which is
what lets a release be cut from a machine that has never held the file.

    python tools/deploy.py            publish main
    python tools/deploy.py --dry-run  say what would happen, publish nothing

What is left to check is only *which commit* gets built. Cloudflare now runs
`tools/build.py` itself, on whatever is on `main` at the moment the hook is
called, so the folder it serves is that commit's build by construction - the
comparison against a committed `repo/` that used to live here was guarding a
mistake that can no longer be made.
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
    """Everything that would make this deployment publish the wrong commit.

    The hook builds `origin/main`, so the only questions left are whether that
    is the branch being looked at and whether the local one has been pushed.
    """
    found = []

    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as error:
        return ["git is not answering: %s" % error]
    if branch != "main":
        found.append("on %s, and the hook builds main" % branch)

    try:
        git("diff", "--quiet", "main", "origin/main")
    except subprocess.CalledProcessError:
        found.append("main and origin/main differ - push first")

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
        print("\npublishing would build something other than what you are "
              "looking at")
        return 1

    version = ""
    try:
        import xml.etree.ElementTree as ET
        version = ET.parse(os.path.join(ROOT, "plugin.video.katan",
                                        "addon.xml")).getroot().get("version")
    except Exception:
        pass
    print("publishing Katan %s from main (%s); Cloudflare builds it"
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

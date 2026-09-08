# -*- coding: utf-8 -*-
"""Install Katan onto an Android box over its own FTP server.

Some Android boxes - the Byintek U4 among them - ship a file manager that
offers an FTP server on the local network. That is a better route onto the
device than anything involving a remote control: no address typed on screen,
no browser, no adb, no USB stick.

    python tools/install_ftp.py --host 192.168.1.183 --port 8119 \
        --user pc --password 151800

    python tools/install_ftp.py ... --dry-run    say what would be written

It copies the add-ons straight into Kodi's own add-on folder rather than
leaving a zip to install by hand, and drops the repository zip in Download as
well, so the *Install from zip file* route still exists if Kodi needs to be
told twice.

Credentials are arguments, never files: this is somebody's home network and
nothing here should end up committed. `--password` may also be given through
the KATAN_FTP_PASSWORD environment variable.
"""
from __future__ import print_function

import argparse
import ftplib
import io
import os
import posixpath
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = ["plugin.video.katan", "repository.katan"]

# Where Kodi keeps its add-ons on Android. The `org.xbmc.kodi` package is the
# official build; a fork such as POV lives under its own package and this
# would need pointing at it.
KODI = "/device/Android/data/org.xbmc.kodi/files/.kodi"
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".orig", ".rej", ".log", ".tmp")


def local_files(addon_id):
    """Every file that belongs in the installed add-on, as (local, relative)."""
    base = os.path.join(ROOT, addon_id)
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(folder, name)
            relative = os.path.relpath(full, base).replace(os.sep, "/")
            yield full, relative


def ensure(ftp, path, made):
    """mkdir -p, remembering what already exists so it is asked once."""
    if path in made or path in ("", "/"):
        return
    ensure(ftp, posixpath.dirname(path), made)
    try:
        ftp.mkd(path)
    except ftplib.error_perm:
        pass                      # already there, which is the usual case
    made.add(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=2121)
    parser.add_argument("--user", default="pc")
    parser.add_argument("--password",
                        default=os.environ.get("KATAN_FTP_PASSWORD", ""))
    parser.add_argument("--kodi", default=KODI,
                        help="Kodi's .kodi directory on the device")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    plan = []
    for addon_id in ADDONS:
        for full, relative in local_files(addon_id):
            plan.append((full, "%s/addons/%s/%s"
                         % (args.kodi, addon_id, relative)))
    total = sum(os.path.getsize(full) for full, _ in plan)
    print("%d files, %.0f KB" % (len(plan), total / 1024.0))

    if args.dry_run:
        for _full, remote in plan[:5]:
            print("   ", remote)
        print("    ... and %d more" % (len(plan) - 5))
        return 0

    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port, timeout=30)
    ftp.login(args.user, args.password)
    print("connected:", ftp.getwelcome().strip())

    made = set()
    sent = 0
    try:
        for full, remote in plan:
            ensure(ftp, posixpath.dirname(remote), made)
            with io.open(full, "rb") as handle:
                ftp.storbinary("STOR " + remote, handle)
            sent += 1
            if sent % 25 == 0 or sent == len(plan):
                print("  %d/%d" % (sent, len(plan)))

        # A zip in Download as well, so "Install from zip file" stays open.
        for addon_id in ADDONS:
            import xml.etree.ElementTree as ET
            version = ET.parse(os.path.join(ROOT, addon_id,
                                            "addon.xml")).getroot().get("version")
            zip_path = os.path.join(ROOT, "repo", "zips", addon_id,
                                    "%s-%s.zip" % (addon_id, version))
            if os.path.isfile(zip_path):
                with io.open(zip_path, "rb") as handle:
                    ftp.storbinary("STOR /device/Download/%s"
                                   % os.path.basename(zip_path), handle)
                print("  also left %s in Download"
                      % os.path.basename(zip_path))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()

    print("\ninstalled. Restart Kodi on the device and Katan will be there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

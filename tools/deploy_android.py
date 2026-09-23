"""Push a release to an Android box over adb.

    python tools/deploy_android.py           newest zip to every device
    python tools/deploy_android.py --list    show what adb can see
    python tools/deploy_android.py --settings
                                             copy this machine's Pinky
                                             settings, credentials and all,
                                             to every device

Kodi has no way to be told "install this zip" from outside, so this does the
part that a remote control is bad at - getting the file onto the device - and
leaves the two button presses to you:

    Kodi -> Add-ons -> Install from zip file -> Download -> the file

Only needed once per device, for the repository add-on. After that Kodi
updates itself from the repository and this script has nothing left to do.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIPS = os.path.join(ROOT, "repo", "zips")
REMOTE = "/sdcard/Download"

ADDON_ID = "plugin.video.pinky"
# What this machine's own Kodi has written, which is where the accounts are.
SETTINGS = os.path.join(ROOT, ".kodi-test", "portable_data", "userdata",
                        "addon_data", ADDON_ID, "settings.xml")
REMOTE_SETTINGS = ("/sdcard/Android/data/org.xbmc.kodi/files/.kodi/userdata/"
                   "addon_data/%s/settings.xml" % ADDON_ID)


def adb(*args):
    try:
        out = subprocess.check_output(("adb",) + args,
                                      stderr=subprocess.STDOUT)
        return out.decode("utf-8", "replace")
    except FileNotFoundError:
        raise SystemExit("adb is not on PATH: install platform-tools")
    except subprocess.CalledProcessError as error:
        return error.output.decode("utf-8", "replace")


def devices():
    lines = adb("devices").splitlines()[1:]
    return [line.split()[0] for line in lines
            if line.strip() and line.split()[-1] == "device"]


def newest_zips():
    """The current zip for each add-on, newest version wins."""
    found = {}
    if not os.path.isdir(ZIPS):
        raise SystemExit("no repo/zips: run python tools/build.py first")
    for addon_id in sorted(os.listdir(ZIPS)):
        folder = os.path.join(ZIPS, addon_id)
        zips = [f for f in os.listdir(folder) if f.endswith(".zip")]
        if zips:
            newest = max(zips, key=lambda n: os.path.getmtime(
                os.path.join(folder, n)))
            found[addon_id] = os.path.join(folder, newest)
    return found


def push_settings(targets):
    """Copy this machine's Pinky settings onto every device.

    Accounts are the reason this exists. Twenty-two credentials can be entered
    on a keyboard in a couple of minutes and are miserable on a projector with
    a remote - Ktuvit alone is an email address and a password - so they are
    entered once here and copied. Kodi preserves `addon_data` across updates,
    which `test_upgrade.py` holds, so this survives every later release.

    It is a copy rather than anything in the source on purpose: credentials
    belong to a device, not to a build. One login baked into a published
    add-on is one account shared by everybody who installs it, which is how
    an account gets closed - and it would be in the history for good.

    Kodi must be closed on the box, because it rewrites this file on exit and
    would put back what was there before.
    """
    if not os.path.isfile(SETTINGS):
        raise SystemExit(
            "no settings to copy: run Kodi here and set something first\n  %s"
            % SETTINGS)

    for serial in targets:
        print("%s" % serial)
        adb("-s", serial, "shell", "mkdir", "-p", os.path.dirname(REMOTE_SETTINGS))
        out = adb("-s", serial, "push", SETTINGS, REMOTE_SETTINGS)
        failed = "error" in out.lower() or "no such" in out.lower()
        print("   settings.xml -> %s%s"
              % (REMOTE_SETTINGS, "  FAILED: %s" % out.strip()[:60] if failed else ""))

    print("\nClose Kodi on the box before doing this, and start it after:")
    print("Kodi writes its settings on exit and would put back the old ones.")
    return 0


def main():
    if "--list" in sys.argv:
        print(adb("devices", "-l"))
        return 0

    targets = devices()
    if not targets:
        print("no device. On the box: Settings -> About -> tap Build 7 times,")
        print("then Developer options -> USB or Network debugging.")
        print("Over the network: adb connect <box-ip>:5555")
        return 1

    if "--settings" in sys.argv:
        return push_settings(targets)

    payload = newest_zips()
    for serial in targets:
        print("%s" % serial)
        for addon_id, path in sorted(payload.items()):
            adb("-s", serial, "push", path, REMOTE)
            print("   %s -> %s (%.0f KB)"
                  % (os.path.basename(path), REMOTE,
                     os.path.getsize(path) / 1024.0))

    print("\nOn the box: Kodi -> Add-ons -> Install from zip file -> Download")
    print("Start with repository.pinky; Pinky itself then installs from it,")
    print("and every later release arrives on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

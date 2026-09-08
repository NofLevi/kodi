"""Push a release to an Android box over adb.

    python tools/deploy_android.py           newest zip to every device
    python tools/deploy_android.py --list    show what adb can see

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

    payload = newest_zips()
    for serial in targets:
        print("%s" % serial)
        for addon_id, path in sorted(payload.items()):
            adb("-s", serial, "push", path, REMOTE)
            print("   %s -> %s (%.0f KB)"
                  % (os.path.basename(path), REMOTE,
                     os.path.getsize(path) / 1024.0))

    print("\nOn the box: Kodi -> Add-ons -> Install from zip file -> Download")
    print("Start with repository.katan; Katan itself then installs from it,")
    print("and every later release arrives on its own.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

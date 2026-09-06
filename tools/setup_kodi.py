"""Set up a portable Kodi on Windows for testing.

Portable means everything (profile, add-ons, logs) stays inside one folder and
nothing touches the registry or %APPDATA%. Deleting the folder undoes it.

    python tools/setup_kodi.py            download and install
    python tools/setup_kodi.py --link     only link the add-on into an existing one
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KODI_DIR = os.path.join(ROOT, ".kodi-test")
VERSION = "21.3-Omega"
INSTALLER_URL = ("https://mirrors.kodi.tv/releases/windows/win64/"
                 "kodi-%s-x64.exe" % VERSION)
INSTALLER = os.path.join(KODI_DIR, "kodi-installer.exe")
EXE = os.path.join(KODI_DIR, "kodi.exe")

ADDONS = ["plugin.video.katan", "repository.katan"]


def download():
    if os.path.isfile(INSTALLER) and os.path.getsize(INSTALLER) > 50 * 1024 * 1024:
        print("installer already downloaded")
        return True
    if not os.path.isdir(KODI_DIR):
        os.makedirs(KODI_DIR)

    print("downloading Kodi %s" % VERSION)
    # requests is used rather than urllib because it ships a CA bundle, and
    # urllib has no trust store to fall back on for HTTPS on Windows.
    try:
        import requests
    except ImportError:
        print("this needs the requests package: pip install requests")
        return False

    try:
        with requests.get(INSTALLER_URL, stream=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(INSTALLER, "wb") as handle:
                for block in response.iter_content(chunk_size=1024 * 256):
                    handle.write(block)
                    done += len(block)
                    if total:
                        sys.stdout.write("\r  %5.1f%%  %.0f of %.0f MB"
                                         % (done * 100.0 / total,
                                            done / (1024.0 * 1024.0),
                                            total / (1024.0 * 1024.0)))
                        sys.stdout.flush()
        print()
        return True
    except Exception as error:
        print("download failed: %s" % error)
        return False


def install():
    """NSIS installers accept a silent install into a chosen directory."""
    if os.path.isfile(EXE):
        print("Kodi is already installed at %s" % KODI_DIR)
        return True
    print("installing into %s" % KODI_DIR)
    result = subprocess.run([INSTALLER, "/S", "/D=" + KODI_DIR],
                            capture_output=True)
    if not os.path.isfile(EXE):
        print("silent install did not produce kodi.exe (exit %s)" % result.returncode)
        return False
    return True


def make_portable():
    """A portable marker keeps the profile inside the folder."""
    portable = os.path.join(KODI_DIR, "portable_data")
    for folder in ("addons", "userdata"):
        path = os.path.join(portable, folder)
        if not os.path.isdir(path):
            os.makedirs(path)
    return portable


def link_addons(portable):
    """Link the source tree into the portable add-ons folder.

    A directory junction means editing a file here is live in Kodi, with no
    copy step between a change and testing it.
    """
    target_root = os.path.join(portable, "addons")
    for addon_id in ADDONS:
        source = os.path.join(ROOT, addon_id)
        target = os.path.join(target_root, addon_id)
        if os.path.isdir(target) or os.path.islink(target):
            # A Windows directory junction is not a symlink as far as
            # os.path.islink is concerned, and rmtree refuses to touch one.
            # os.rmdir removes the junction itself and leaves the target alone.
            try:
                os.rmdir(target)
            except OSError:
                try:
                    shutil.rmtree(target)
                except OSError as error:
                    print("could not replace %s: %s" % (target, error))
                    continue
        result = subprocess.run(["cmd", "/c", "mklink", "/J", target, source],
                                capture_output=True, text=True)
        if os.path.isdir(target):
            print("linked %s" % addon_id)
        else:
            shutil.copytree(source, target)
            print("copied %s (junction failed: %s)"
                  % (addon_id, result.stderr.strip()))


def enable_addons(portable):
    """Kodi leaves manually dropped add-ons disabled, so switch them on.

    Editing the add-on database directly is the standard way to do this for a
    development install; there is no command line switch for it.
    """
    import sqlite3

    folder = os.path.join(portable, "userdata", "Database")
    if not os.path.isdir(folder):
        print("no database yet: start Kodi once, then run with --link")
        return False

    databases = [f for f in os.listdir(folder)
                 if f.startswith("Addons") and f.endswith(".db")]
    if not databases:
        print("no add-on database yet: start Kodi once, then run with --link")
        return False

    path = os.path.join(folder, sorted(databases)[-1])
    connection = sqlite3.connect(path)
    try:
        for addon_id in ADDONS:
            connection.execute(
                "INSERT OR REPLACE INTO installed (addonID, enabled, installDate)"
                " VALUES (?, 1, datetime('now'))", (addon_id,))
        connection.commit()
        print("enabled %s in %s" % (", ".join(ADDONS), os.path.basename(path)))
        return True
    finally:
        connection.close()


def write_advanced_settings(portable):
    """Turn on debug logging, so a failure explains itself."""
    path = os.path.join(portable, "userdata", "advancedsettings.xml")
    if os.path.isfile(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "<advancedsettings>\n"
            "    <loglevel hide=\"false\">1</loglevel>\n"
            "    <cache>\n"
            "        <buffermode>1</buffermode>\n"
            "        <memorysize>52428800</memorysize>\n"
            "        <readfactor>5</readfactor>\n"
            "    </cache>\n"
            "</advancedsettings>\n")
    print("wrote advancedsettings.xml with debug logging on")


def main():
    portable = make_portable()

    if "--link" not in sys.argv:
        if not download():
            return 1
        if not install():
            return 1
        make_portable()

    link_addons(portable)
    write_advanced_settings(portable)
    enable_addons(portable)

    print()
    print("run it with:")
    print("    %s -p" % EXE)
    print("log: %s" % os.path.join(portable, "kodi.log"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

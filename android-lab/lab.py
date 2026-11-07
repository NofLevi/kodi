"""A portable Android emulator for testing Pinky on Android.

Portable in the same sense as `tools/setup_kodi.py`: everything - the SDK, the
virtual device, its disk, the logs - stays inside this folder, nothing touches
the registry, %APPDATA% or the PATH, and deleting android-lab/ undoes all of
it. No Android Studio and no Java: the three pieces are plain zips from
Google's own repository, so there is nothing to install.

    python android-lab/lab.py get        download the SDK, make the device
    python android-lab/lab.py accel      is hardware acceleration available
    python android-lab/lab.py driver     fetch Google's hypervisor driver
    python android-lab/lab.py up         boot it
    python android-lab/lab.py clean      wipe Kodi, install it fresh, stage
                                         the zips - the real install journey
                                         (clean 20.2-Nexus for the other Kodi)
    python android-lab/lab.py install    the shortcut: drop Pinky straight in
    python android-lab/lab.py logs       pull kodi.log, kodi.old.log, crashlogs
    python android-lab/lab.py shot       screenshot what is on screen
    python android-lab/lab.py press ...  DPAD_DOWN ENTER BACK, to drive Kodi
    python android-lab/lab.py down       shut it down
    python android-lab/lab.py adb ...    anything else, against this device

What this can and cannot prove is worth being honest about up front. The phone
this stands in for is an arm64 Samsung; no Android emulator on an Intel host
runs arm64 at a usable speed, so this is x86. It will therefore show up
anything in the add-on's Python, its install, its storage access and its
routes, and it will show nothing at all about an arm64 native crash.
"""
import os
import re
import subprocess
import sys
import time
import zipfile

LAB = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(LAB)
SDK = os.path.join(LAB, "sdk")
DOWNLOADS = os.path.join(LAB, "downloads")
LOGS = os.path.join(LAB, "logs")

DEVICE = "pinky-android"
ADDONS = ["plugin.video.pinky", "repository.pinky"]

# Kodi publishes no x86_64 APK for Android - only arm, arm64-v8a and 32-bit
# x86 - so the device has to be able to run a 32-bit x86 app. API 30 is the
# default because Android 11 is where scoped storage began being enforced,
# which is the rule that governs where a download may be written; its image is
# x86_64 and carries 32-bit support alongside. API 29 is the fallback: the
# last image Google built as 32-bit x86 outright, for if that stops being true.
IMAGES = {
    30: ("x86_64", "x86_64-30_r11.zip"),
    29: ("x86", "x86-29_r08-windows.zip"),
}
EMULATOR_ZIP = "emulator-windows_x64-16847572.zip"

# The official Kodi releases, as published by Kodi. These are the same builds
# the Play Store distributes - same version, same source, same signature -
# only the ABI differs, because the Play Store would serve this phone arm64
# and nothing here can run that. Two of them because a dependency floor that
# resolves on one Kodi can fail on another, and that is precisely the kind of
# thing worth catching.
KODI_APKS = {
    "21.3-Omega": "kodi-21.3-Omega-x86.apk",
    "20.2-Nexus": "kodi-20.2-Nexus-x86.apk",
}
DEFAULT_KODI = "21.3-Omega"
APK_BASE = "https://mirrors.kodi.tv/releases/android/x86/"
PACKAGE = "org.xbmc.kodi"
FILES = "/sdcard/Android/data/%s/files" % PACKAGE
KODI_HOME = FILES + "/.kodi"


# -- the pieces ----------------------------------------------------------


def _fetch(url, target):
    """Download with a progress line, skipping what is already here."""
    if os.path.isfile(target) and os.path.getsize(target) > 1024 * 1024:
        print("  have %s" % os.path.basename(target))
        return True
    try:
        import requests
    except ImportError:
        print("this needs the requests package: pip install requests")
        return False
    print("  downloading %s" % os.path.basename(target))
    try:
        with requests.get(url, stream=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(target + ".part", "wb") as handle:
                for block in response.iter_content(chunk_size=1024 * 512):
                    handle.write(block)
                    done += len(block)
                    if total:
                        sys.stdout.write("\r    %5.1f%%  %.0f of %.0f MB"
                                         % (done * 100.0 / total,
                                            done / 1048576.0,
                                            total / 1048576.0))
                        sys.stdout.flush()
    except Exception as error:
        print("\n  failed: %s" % error)
        return False
    print("")
    os.replace(target + ".part", target)
    return True


def _unzip(archive, into, marker):
    """Unpack unless the marker is already there. False on a bad zip."""
    if os.path.exists(marker):
        print("  have %s" % os.path.relpath(marker, LAB))
        return True
    print("  unpacking %s" % os.path.basename(archive))
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(into)
    except zipfile.BadZipFile:
        # A half-finished download that got renamed, or a mirror that served
        # an error page. Removing it means the next run fetches it again
        # rather than failing the same way for ever.
        print("  %s is not a zip; deleting it, run get again"
              % os.path.basename(archive))
        os.remove(archive)
        return False
    return os.path.exists(marker)


def get(api=30):
    """Download the three zips and build the virtual device."""
    if api not in IMAGES:
        print("api must be one of %s" % sorted(IMAGES))
        return False
    abi, image_zip = IMAGES[api]
    for folder in (SDK, DOWNLOADS, LOGS):
        if not os.path.isdir(folder):
            os.makedirs(folder)

    base = "https://dl.google.com/android/repository/"
    pieces = [
        (base + "platform-tools-latest-windows.zip", "platform-tools.zip",
         SDK, os.path.join(SDK, "platform-tools", "adb.exe")),
        (base + EMULATOR_ZIP, EMULATOR_ZIP,
         SDK, os.path.join(SDK, "emulator", "emulator.exe")),
    ]
    # The system image has to land at the exact path the device config names,
    # and the zip's own top folder is only the ABI - so it unpacks into the
    # android-NN/default/ directory rather than into the SDK root.
    image_root = os.path.join(SDK, "system-images", "android-%d" % api,
                              "default")
    pieces.append((base + "sys-img/android/" + image_zip, image_zip,
                   image_root, os.path.join(image_root, abi, "system.img")))

    for url, name, into, marker in pieces:
        archive = os.path.join(DOWNLOADS, name)
        if not _fetch(url, archive):
            return False
        if not os.path.isdir(into):
            os.makedirs(into)
        if not _unzip(archive, into, marker):
            return False

    make_device(api, abi)
    print("")
    accel_check()
    return True


def make_device(api, abi):
    """Write the device by hand, because avdmanager would mean a Java install.

    An AVD is two text files and a folder; the emulator reads them directly.
    """
    home = os.path.join(LAB, "avd")
    folder = os.path.join(home, DEVICE + ".avd")
    if not os.path.isdir(folder):
        os.makedirs(folder)

    with open(os.path.join(home, DEVICE + ".ini"), "w", newline="\n") as handle:
        handle.write("avd.ini.encoding=UTF-8\npath=%s\npath.rel=%s.avd\n"
                     "target=android-%d\n" % (folder, DEVICE, api))

    config = {
        "AvdId": DEVICE,
        "avd.ini.displayname": DEVICE,
        "avd.ini.encoding": "UTF-8",
        "abi.type": abi,
        "hw.cpu.arch": "x86_64" if abi == "x86_64" else "x86",
        "image.sysdir.1": "system-images/android-%d/default/%s/" % (api, abi),
        "tag.id": "default",
        "tag.display": "",
        "PlayStore.enabled": "false",
        # Two cores and 2 GB. The host is a 6-core laptop already running
        # other work, and Kodi's own requirement is far below this.
        "hw.cpu.ncore": "2",
        "hw.ramSize": "2048",
        "vm.heapSize": "256",
        # 720p landscape, not a portrait phone: Kodi's skin is built for a
        # television, and fewer pixels is less for the GPU to do.
        "hw.lcd.width": "1280",
        "hw.lcd.height": "720",
        "hw.lcd.density": "213",
        "hw.initialOrientation": "landscape",
        "hw.keyboard": "yes",
        "hw.dPad": "yes",
        # Silent by choice rather than by accident: nothing here should ever
        # make a sound, so the device has no audio hardware at all.
        "hw.audioInput": "no",
        "hw.audioOutput": "no",
        # No virtual SD card. Kodi's storage is its app-specific folder on
        # emulated primary storage, which exists regardless.
        "hw.sdCard": "no",
        # Room for a couple of films, since offline downloads are the thing
        # most worth testing here.
        "disk.dataPartition.size": "8G",
    }
    with open(os.path.join(folder, "config.ini"), "w", newline="\n") as handle:
        for key in sorted(config):
            handle.write("%s=%s\n" % (key, config[key]))
    print("device %s written (android-%d, %s)" % (DEVICE, api, abi))


# -- running it ----------------------------------------------------------


def _env():
    """Every tool is pointed inside this folder, so nothing leaks out.

    The folders are made here rather than in `get`, because the emulator does
    not create its own home and does not fail usefully without it: it writes
    a lock file there, and if the directory is missing it reports "error: 3"
    once per attempt and retries for ever instead of starting.
    """
    home = os.path.join(LAB, "home")
    avd = os.path.join(LAB, "avd")
    for folder in (home, avd, LOGS):
        if not os.path.isdir(folder):
            os.makedirs(folder)
    env = dict(os.environ)
    env.update(ANDROID_SDK_ROOT=SDK, ANDROID_HOME=SDK,
               ANDROID_AVD_HOME=avd, ANDROID_EMULATOR_HOME=home,
               ANDROID_SDK_HOME=LAB)
    return env


def _adb(*args, **kwargs):
    exe = os.path.join(SDK, "platform-tools", "adb.exe")
    command = [exe, "-s", "emulator-5554"] + list(args)
    try:
        return subprocess.run(command, env=_env(), capture_output=True,
                              text=True, timeout=kwargs.get("timeout", 300))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 1, "", "timed out")


def _shell(line, timeout=300):
    return _adb("shell", line, timeout=timeout).stdout.strip()


def _push(local, remote):
    """Copy a file to the device as the shell user, never as root.

    This is not fussiness. Android 11 serves /sdcard through a FUSE layer,
    and a root adb writes underneath it: the file lands, root can read it
    back, and the app that owns the folder sees something it cannot use. It
    cost an afternoon here - Kodi answered "unable to load settings from
    guisettings.xml" and then "failed to save" the replacement, on a file
    that looked perfectly good from the shell and had the right owner and
    mode. Deleting it and letting Kodi write its own fixed it at once.

    So: root for reading, never for writing.
    """
    _adb("unroot", timeout=60)
    _adb("wait-for-device", timeout=120)
    result = _adb("push", local, remote, timeout=900)
    return "error" not in (result.stdout + result.stderr).lower()


def accel_check():
    """Ask the emulator whether this machine can hardware-accelerate it.

    The single most useful thing to know before waiting on a boot: without a
    hypervisor the emulator interprets x86 instructions one at a time, which
    for Kodi is the difference between half a minute and not finishing.
    """
    exe = os.path.join(SDK, "emulator", "emulator.exe")
    if not os.path.isfile(exe):
        print("run get first")
        return False
    result = subprocess.run([exe, "-accel-check"], env=_env(),
                            capture_output=True, text=True)
    print("acceleration: %s" % ((result.stdout + result.stderr).strip()
                                or "no answer"))
    return result.returncode == 0


def driver():
    """Fetch AEHD, Google's hypervisor driver, and say how to install it.

    The emulator needs something holding VT-x. Windows offers WHPX, but
    turning that on enables the Windows hypervisor for good, and from then on
    VMware Workstation runs on top of it rather than on the metal - slower,
    for every VM on this machine, for ever. AEHD is Google's own driver (the
    replacement for the retired Intel HAXM): 171 KB, no Hyper-V, no reboot,
    and nothing else on the machine changes speed.

    Installing a kernel driver needs administrator rights. The script does
    not try to obtain them for you: it unpacks Google's installer, which asks
    for them itself, and leaves the decision where it belongs.
    """
    url = ("https://dl.google.com/android/repository/aehd-windows_v2.2.zip")
    archive = os.path.join(DOWNLOADS, "aehd-windows_v2.2.zip")
    into = os.path.join(LAB, "driver")
    if not os.path.isdir(DOWNLOADS):
        os.makedirs(DOWNLOADS)
    if not os.path.isfile(archive):
        # 171 KB, which is under the size floor _fetch uses to decide
        # something has already been downloaded, so it is fetched plainly.
        try:
            import requests
            with requests.get(url, timeout=60) as response:
                response.raise_for_status()
                with open(archive, "wb") as handle:
                    handle.write(response.content)
        except Exception as error:
            print("could not download the driver: %s" % error)
            return False
    installer = os.path.join(into, "silent_install.bat")
    if not _unzip(archive, into, installer):
        return False
    print("unpacked to %s\n" % into)
    print("Google's installer asks for administrator rights itself, so run:\n")
    print('    "%s"\n' % installer)
    print("Accept the prompt it raises. No reboot. Then check it took:\n"
          "    python android-lab/lab.py accel\n")
    print("Already installed?  silent_install.bat -v")
    print("Changed your mind?  silent_install.bat -u   (removes it cleanly)")
    return True


def up():
    """Boot the device and wait until Android says it is up."""
    exe = os.path.join(SDK, "emulator", "emulator.exe")
    if not os.path.isfile(exe):
        print("run get first")
        return False
    if _shell("getprop sys.boot_completed", timeout=20) == "1":
        print("already up")
        return True
    print("booting %s" % DEVICE)
    with open(os.path.join(LOGS, "emulator.log"), "wb") as handle:
        subprocess.Popen([exe, "-avd", DEVICE, "-no-boot-anim", "-no-audio",
                          "-no-snapshot", "-gpu", "host", "-accel", "auto"],
                         env=_env(), cwd=SDK, stdout=handle,
                         stderr=subprocess.STDOUT)
    # Polled rather than waited on: `adb wait-for-device` blocks for its whole
    # timeout when the emulator never comes up at all, which turns a startup
    # failure into a long silence instead of a message.
    for waited in range(0, 300, 5):
        if _shell("getprop sys.boot_completed", timeout=20) == "1":
            print("up in about %ds: %s / API %s / abis %s"
                  % (waited, _shell("getprop ro.product.model"),
                     _shell("getprop ro.build.version.sdk"),
                     _shell("getprop ro.product.cpu.abilist")))
            return True
        time.sleep(5)
    print("it did not finish booting. The last distinct thing the emulator "
          "said:")
    for line in _emulator_complaints():
        print("  " + line)
    return False


def _emulator_complaints():
    """The emulator repeats one line thousands of times; show each once."""
    path = os.path.join(LOGS, "emulator.log")
    seen, out = set(), []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.rstrip()
                if line and line not in seen:
                    seen.add(line)
                    out.append(line[:200])
    except OSError:
        return ["no emulator.log"]
    return out[-8:]


def down():
    print(_adb("emu", "kill", timeout=30).stdout.strip() or "stopped")
    return True


# -- Kodi and Pinky ------------------------------------------------------


def _require_device():
    if _shell("getprop sys.boot_completed", timeout=20) != "1":
        print("the device is not up: run up first")
        return False
    # Android 11 closed /sdcard/Android/data to everything but the app that
    # owns it, and that includes the adb shell - a plain `ls` of Kodi's own
    # folder answers "Permission denied", which reads exactly like the folder
    # not existing. Root is why the image is the AOSP one rather than a Play
    # Store one: only AOSP builds allow it.
    _adb("root", timeout=60)
    _adb("wait-for-device", timeout=120)
    abis = _shell("getprop ro.product.cpu.abilist")
    if "x86" not in abis.split(","):
        print("this image cannot run a 32-bit x86 app (abis: %s).\n"
              "Kodi ships no x86_64 APK, so rebuild on API 29:\n"
              "    python android-lab/lab.py get 29" % abis)
        return False
    return True


def _install_kodi(version):
    """Put the chosen official Kodi on the device, replacing any other."""
    name = KODI_APKS[version]
    apk = os.path.join(DOWNLOADS, name)
    if not _fetch(APK_BASE + name, apk):
        return False
    print("  installing Kodi %s" % version)
    result = _adb("install", "-r", "-g", apk, timeout=900)
    if "Success" not in (result.stdout + result.stderr):
        print((result.stdout + result.stderr).strip()[:600])
        return False
    # -g above grants the runtime permissions, but not this one. All-files
    # access is an app operation rather than a runtime permission, so it is
    # granted separately - and without it Kodi does not start: it puts up
    # "Kodi requires access to your device media and files to function...
    # or Kodi will exit", waits on a button, and never writes a profile.
    _shell("appops set %s MANAGE_EXTERNAL_STORAGE allow" % PACKAGE, timeout=60)
    print("  granted all-files access")
    return True


def _first_run():
    """Start Kodi once so it writes its profile, then stop it again."""
    print("  starting Kodi once so it builds its profile")
    _adb("shell", "am", "start", "-n", PACKAGE + "/.Splash", timeout=120)
    for _ in range(60):
        if _shell("ls %s/userdata 2>/dev/null" % KODI_HOME, timeout=30):
            break
        # Slept, because the loop without it asks sixty times in four seconds
        # and then declares Kodi dead before it has finished starting.
        time.sleep(2)
    _adb("shell", "am", "force-stop", PACKAGE, timeout=60)
    if not _shell("ls %s/userdata 2>/dev/null" % KODI_HOME):
        print("Kodi never wrote a profile. Look at what is on its screen "
              "before believing anything else:\n"
              "    python android-lab/lab.py shot")
        return False
    return True


def clean(version=DEFAULT_KODI):
    """The whole journey a real install takes, from nothing.

    Kodi is removed outright and its folder deleted first, so this starts
    from a genuinely clean Android rather than from whatever the last run
    left behind. Then the official Kodi goes on and the zips are put where a
    person's own download would have landed - and it stops there, because
    what happens next is the part worth watching rather than scripting.

    Nothing is written into Kodi's own folder, including the settings: see
    `_push` for the afternoon that taught us why. Unknown sources and debug
    logging are left to Kodi's own screens, which is where a person turns
    them on anyway.
    """
    if version not in KODI_APKS:
        print("kodi must be one of: %s" % ", ".join(sorted(KODI_APKS)))
        return False
    if not _require_device():
        return False

    print("wiping Kodi off the device")
    _adb("uninstall", PACKAGE, timeout=300)
    _shell("rm -rf %s" % FILES, timeout=300)

    if not _install_kodi(version):
        return False
    if not _first_run():
        return False

    # Built from the working tree, so this is exactly what a release would
    # carry - the unreleased commits included.
    print("  building the zips")
    built = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build.py")],
                           cwd=ROOT, capture_output=True, text=True)
    if built.returncode:
        print(built.stdout[-800:] + built.stderr[-800:])
        return False

    zips = []
    for addon_id in ADDONS:
        folder = os.path.join(ROOT, "repo", "zips", addon_id)
        zips += [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                 if n.endswith(".zip")]
    # Download only: it is where a person's own download lands, and it is
    # outside Android/data, so the shell can write it without root.
    for path in zips:
        if not _push(path, "/sdcard/Download/"):
            print("  could not stage %s" % os.path.basename(path))
            return False
    print("  put %s in /sdcard/Download"
          % ", ".join(os.path.basename(p) for p in zips))

    _adb("shell", "am", "start", "-n", PACKAGE + "/.Main", timeout=120)
    print("\nclean Kodi %s is running and the zips are in Downloads. The\n"
          "emulator window is on your screen - install it the way you would\n"
          "on the phone:\n\n"
          "  Settings -> System -> Add-ons -> Unknown sources: on\n"
          "  Add-ons -> Install from zip file -> Download ->\n"
          "      repository.pinky-*.zip\n"
          "  Add-ons -> Install from repository -> Katan -> Video add-ons ->\n"
          "      Pinky\n\n"
          "That second step is the one that matters: it makes Kodi fetch our\n"
          "addons.xml, download the zip and resolve the dependencies itself.\n"
          "I will read what it did:  python android-lab/lab.py logs" % version)
    return True


def install():
    """The developer shortcut: drop the add-on straight in and enable it.

    Not the path a person takes - `clean` is that - but the quick one when
    the question is whether the code runs rather than whether it installs.

    It only works below Android 11. From API 30 the shell cannot write into
    an app's own folder at all, and doing it as root does not count: the
    write lands under the FUSE layer the app reads through, so Kodi sees a
    file it cannot use. There is no version of this shortcut that works on
    API 30, so it says so rather than quietly leaving a broken add-on
    behind.
    """
    if not _require_device():
        return False
    api = _shell("getprop ro.build.version.sdk")
    if api.isdigit() and int(api) >= 30:
        print("the shortcut cannot work on Android %s: nothing but Kodi may\n"
              "write into Kodi's folder. Use the real path instead:\n"
              "    python android-lab/lab.py clean\n"
              "or build a device that still allows it:\n"
              "    python android-lab/lab.py get 29" % api)
        return False
    if PACKAGE not in _shell("pm list packages %s" % PACKAGE):
        if not _install_kodi(DEFAULT_KODI):
            return False
    print("Kodi installed")
    if not _shell("ls %s/userdata 2>/dev/null" % KODI_HOME):
        if not _first_run():
            return False

    _shell("mkdir -p %s/addons" % KODI_HOME)
    for addon_id in ADDONS:
        print("  pushing %s" % addon_id)
        _adb("push", os.path.join(ROOT, addon_id),
             "%s/addons/" % KODI_HOME, timeout=900)
    # Kodi leaves a hand-placed add-on disabled and there is no command line
    # switch for it - the same reason setup_kodi.py edits this database.
    _enable_addons()
    _debug_logging()
    _adb("shell", "am", "force-stop", PACKAGE, timeout=60)
    _adb("shell", "monkey", "-p", PACKAGE, "-c",
         "android.intent.category.LAUNCHER", "1", timeout=120)
    print("Kodi started with Pinky installed. Give it a minute, then:\n"
          "    python android-lab/lab.py logs")
    return True


def _enable_addons():
    import sqlite3

    found = _shell("ls %s/userdata/Database/Addons*.db 2>/dev/null" % KODI_HOME)
    remote = [line.strip() for line in found.splitlines() if line.strip()]
    if not remote:
        print("  no add-on database yet; enable Pinky from Kodi's own screen")
        return False
    remote = sorted(remote)[-1]
    local = os.path.join(LOGS, os.path.basename(remote))
    _adb("pull", remote, local, timeout=300)
    if not os.path.isfile(local):
        print("  could not read %s" % remote)
        return False
    connection = sqlite3.connect(local)
    try:
        for addon_id in ADDONS:
            connection.execute(
                "INSERT OR REPLACE INTO installed (addonID, enabled, installDate)"
                " VALUES (?, 1, datetime('now'))", (addon_id,))
        connection.commit()
    finally:
        connection.close()
    _adb("push", local, remote, timeout=300)
    print("  enabled %s" % ", ".join(ADDONS))
    return True


def _debug_logging():
    """Without this the log holds none of the add-on's own lines."""
    path = os.path.join(LOGS, "advancedsettings.xml")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("<advancedsettings>\n"
                     "    <loglevel hide=\"false\">1</loglevel>\n"
                     "</advancedsettings>\n")
    _adb("push", path, "%s/userdata/advancedsettings.xml" % KODI_HOME,
         timeout=120)
    print("  debug logging on")


def shot():
    """A screenshot off the device, so what is on screen can be looked at."""
    if not os.path.isdir(LOGS):
        os.makedirs(LOGS)
    exe = os.path.join(SDK, "platform-tools", "adb.exe")
    result = subprocess.run([exe, "-s", "emulator-5554", "exec-out",
                             "screencap", "-p"], env=_env(),
                            capture_output=True, timeout=120)
    if not result.stdout.startswith(b"\x89PNG"):
        print("no screenshot: %s" % result.stderr.decode(errors="replace")[:300])
        return False
    path = os.path.join(LOGS, "screen.png")
    with open(path, "wb") as handle:
        handle.write(result.stdout)
    print("%s  (%.0f KB)" % (path, len(result.stdout) / 1024.0))
    return True


def press(*keys):
    """Send remote-control key presses, for driving Kodi's own screens.

    Kodi on Android takes these as a remote would: DPAD_* to move, ENTER to
    choose, BACK to go back. Named keys so a run reads like what a person did.
    """
    if not keys:
        print("press DPAD_DOWN ENTER BACK ...")
        return False
    for key in keys:
        name = key if key.startswith("KEYCODE_") else "KEYCODE_" + key.upper()
        _adb("shell", "input", "keyevent", name, timeout=60)
    print("pressed %s" % " ".join(keys))
    return True


def logs():
    """Pull every log Kodi leaves and print what matters out of them.

    kodi.old.log is the one that counts after a crash: it is the run that
    died. kodi.log is the run that came up afterwards to tell you about it.
    """
    if not os.path.isdir(LOGS):
        os.makedirs(LOGS)
    names = ["kodi.log", "kodi.old.log"]
    listing = _shell("ls %s/temp/ 2>/dev/null" % KODI_HOME)
    names += [n for n in listing.split() if n.startswith("crashlog")]
    pulled = []
    for name in names:
        local = os.path.join(LOGS, name)
        _adb("pull", "%s/temp/%s" % (KODI_HOME, name), local, timeout=300)
        if os.path.isfile(local):
            pulled.append(local)
            print("%s  (%.0f KB)" % (local, os.path.getsize(local) / 1024.0))
    if not pulled:
        print("no Kodi logs on the device yet")
        # A native crash can die before Kodi opens its own log, and then
        # Android's buffer is the only witness there is.
        crash = _shell("logcat -d -b crash -t 200", timeout=120)
        if crash:
            local = os.path.join(LOGS, "logcat-crash.log")
            with open(local, "w", encoding="utf-8", errors="replace") as handle:
                handle.write(crash)
            print("%s  (Android's crash buffer)" % local)
        return False

    interesting = re.compile(r"ERROR|WARNING|pinky|Exception|Traceback",
                             re.IGNORECASE)
    for local in pulled:
        with open(local, encoding="utf-8", errors="replace") as handle:
            hits = [line.rstrip() for line in handle
                    if interesting.search(line)]
        print("\n=== %s: %d lines worth reading ==="
              % (os.path.basename(local), len(hits)))
        for line in hits[-40:]:
            print("  " + line[:200])
    return True


def main():
    actions = {"get": get, "up": up, "down": down, "clean": clean,
               "install": install, "logs": logs, "shot": shot, "press": press,
               "accel": accel_check, "driver": driver}
    args = sys.argv[1:]
    if not args or (args[0] not in actions and args[0] != "adb"):
        print(__doc__)
        return 1
    if args[0] == "adb":
        exe = os.path.join(SDK, "platform-tools", "adb.exe")
        return subprocess.run([exe] + args[1:], env=_env()).returncode
    if args[0] == "get":
        return 0 if get(int(args[1]) if len(args) > 1 else 30) else 1
    if args[0] == "clean":
        return 0 if clean(*args[1:2]) else 1
    if args[0] == "press":
        return 0 if press(*args[1:]) else 1
    return 0 if actions[args[0]]() else 1


if __name__ == "__main__":
    sys.exit(main())

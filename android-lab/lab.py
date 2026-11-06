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
    python android-lab/lab.py install    Kodi + Pinky, with debug logging on
    python android-lab/lab.py logs       pull kodi.log, kodi.old.log, crashlogs
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
KODI_APK = "kodi-21.3-Omega-x86.apk"
KODI_APK_URL = "https://mirrors.kodi.tv/releases/android/x86/" + KODI_APK
PACKAGE = "org.xbmc.kodi"
KODI_HOME = "/sdcard/Android/data/%s/files/.kodi" % PACKAGE


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
    """Every tool is pointed inside this folder, so nothing leaks out."""
    env = dict(os.environ)
    env.update(ANDROID_SDK_ROOT=SDK, ANDROID_HOME=SDK,
               ANDROID_AVD_HOME=os.path.join(LAB, "avd"),
               ANDROID_EMULATOR_HOME=os.path.join(LAB, "home"),
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
    _adb("wait-for-device", timeout=600)
    for _ in range(120):
        if _shell("getprop sys.boot_completed", timeout=30) == "1":
            print("up: %s / API %s / abis %s"
                  % (_shell("getprop ro.product.model"),
                     _shell("getprop ro.build.version.sdk"),
                     _shell("getprop ro.product.cpu.abilist")))
            return True
    print("it did not finish booting; see android-lab/logs/emulator.log")
    return False


def down():
    print(_adb("emu", "kill", timeout=30).stdout.strip() or "stopped")
    return True


# -- Kodi and Pinky ------------------------------------------------------


def install():
    """Kodi, then Pinky, then debug logging, then start it."""
    if _shell("getprop sys.boot_completed", timeout=20) != "1":
        print("the device is not up: run up first")
        return False

    abis = _shell("getprop ro.product.cpu.abilist")
    if "x86" not in abis.split(","):
        print("this image cannot run a 32-bit x86 app (abis: %s).\n"
              "Kodi ships no x86_64 APK, so rebuild on API 29:\n"
              "    python android-lab/lab.py get 29" % abis)
        return False

    if PACKAGE not in _shell("pm list packages %s" % PACKAGE):
        apk = os.path.join(DOWNLOADS, KODI_APK)
        if not _fetch(KODI_APK_URL, apk):
            return False
        print("  installing Kodi")
        # -g grants the permissions up front, so no dialog blocks the boot.
        result = _adb("install", "-r", "-g", apk, timeout=900)
        if "Success" not in (result.stdout + result.stderr):
            print((result.stdout + result.stderr).strip()[:600])
            return False
    print("Kodi installed")

    # Kodi has to run once before its folders exist to push anything into.
    if not _shell("ls %s/userdata 2>/dev/null" % KODI_HOME):
        print("  starting Kodi once so it creates its folders")
        _adb("shell", "monkey", "-p", PACKAGE, "-c",
             "android.intent.category.LAUNCHER", "1", timeout=120)
        for _ in range(60):
            if _shell("ls %s/userdata 2>/dev/null" % KODI_HOME, timeout=30):
                break
        _adb("shell", "am", "force-stop", PACKAGE, timeout=60)

    if not _shell("ls %s/userdata 2>/dev/null" % KODI_HOME):
        print("Kodi never created its profile, so it is dying at startup with\n"
              "no add-on of ours installed. That is worth knowing on its own.\n"
              "Run: python android-lab/lab.py logs")
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
    actions = {"get": get, "up": up, "down": down, "install": install,
               "logs": logs, "accel": accel_check, "driver": driver}
    args = sys.argv[1:]
    if not args or (args[0] not in actions and args[0] != "adb"):
        print(__doc__)
        return 1
    if args[0] == "adb":
        exe = os.path.join(SDK, "platform-tools", "adb.exe")
        return subprocess.run([exe] + args[1:], env=_env()).returncode
    if args[0] == "get" and len(args) > 1:
        return 0 if get(int(args[1])) else 1
    return 0 if actions[args[0]]() else 1


if __name__ == "__main__":
    sys.exit(main())

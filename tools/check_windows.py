"""Open the custom windows in a real Kodi and photograph them.

Every test so far ran the add-on in directory mode, which never loads a single
line of the window XML. A malformed control, a missing include or a bad id
would show as a window that silently refuses to open, and nothing in the unit
suite would notice. This opens them for real and takes a screenshot.
"""
import glob
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from drive_kodi import (EXE, LOG, PORTABLE, enable_webserver, rpc,  # noqa: E402
                        wait_for_kodi)

SHOTS = os.path.join(ROOT, ".kodi-test", "shots")
# Kodi writes screenshots wherever debug.screenshotpath points, which on a
# portable install is beside the profile. Search a few likely places rather
# than assume one.
SCREENSHOT_DIRS = [
    os.path.join(PORTABLE, "screenshots"),
    os.path.join(PORTABLE, "userdata", "screenshots"),
    os.path.join(os.path.expanduser("~"), "Pictures"),
    PORTABLE,
]

WINDOW_ERRORS = ("Unable to load window", "Failed to load", "error loading",
                 "Window Translator", "unable to load XML", "Non-Existent Control")


def set_addon_setting(key, value):
    """Write an add-on setting the way Kodi stores it."""
    path = os.path.join(PORTABLE, "userdata", "addon_data",
                        "plugin.video.katan", "settings.xml")
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))

    entries = {}
    if os.path.isfile(path):
        import xml.etree.ElementTree as ET
        try:
            for node in ET.parse(path).getroot().findall("setting"):
                entries[node.get("id")] = node.text or ""
        except ET.ParseError:
            pass
    entries[key] = value

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write('<settings version="2">\n')
        for name, text in sorted(entries.items()):
            handle.write('    <setting id="%s">%s</setting>\n' % (name, text))
        handle.write("</settings>\n")


def _all_shots():
    found = set()
    for folder in SCREENSHOT_DIRS:
        found.update(glob.glob(os.path.join(folder, "*.png")))
        found.update(glob.glob(os.path.join(folder, "**", "*.png")))
    return found


def screenshot(label):
    """Ask Kodi for a screenshot and keep it under a readable name."""
    before = _all_shots()
    try:
        rpc("Input.ExecuteAction", {"action": "screenshot"}, timeout=20)
    except Exception as error:
        return "", str(error)[:80]
    time.sleep(3)
    new = sorted(_all_shots() - before)
    if not new:
        return "", "no file appeared"
    if not os.path.isdir(SHOTS):
        os.makedirs(SHOTS)
    target = os.path.join(SHOTS, "%s.png" % label)
    shutil.move(new[-1], target)
    return target, ""


def _set_kodi_setting(key, value):
    """Write a Kodi setting into guisettings.xml before Kodi starts."""
    import re

    path = os.path.join(PORTABLE, "userdata", "guisettings.xml")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    replacement = '<setting id="%s">%s</setting>' % (key, value)
    pattern = r'<setting id="%s"[^>]*>.*?</setting>' % re.escape(key)
    selfclosing = r'<setting id="%s"[^>]*/>' % re.escape(key)
    # A Windows path is full of backslashes, and re.sub reads those as escape
    # sequences in the replacement. A function replacement is taken literally.
    literal = lambda match: replacement
    if re.search(pattern, text):
        text = re.sub(pattern, literal, text)
    elif re.search(selfclosing, text):
        text = re.sub(selfclosing, literal, text)
    else:
        text = text.replace("</settings>",
                            "    %s\n</settings>" % replacement)
    with open(path, "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(text)


def log_since(position):
    if not os.path.isfile(LOG):
        return []
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        return handle.readlines()[position:]


def log_length():
    if not os.path.isfile(LOG):
        return 0
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        return len(handle.readlines())


def window_problems(lines):
    found = []
    for line in lines:
        if any(marker.lower() in line.lower() for marker in WINDOW_ERRORS):
            found.append(line.strip()[:150])
    return found


def open_home():
    """Navigate to the plugin, which runs it and opens the custom window.

    Addons.ExecuteAddon on a video plugin opens Kodi's file browser instead of
    running the plugin, which is why this uses ActivateWindow with the path.
    """
    rpc("GUI.ActivateWindow",
        {"window": "videos",
         "parameters": ["plugin://plugin.video.katan/"]}, timeout=30)


def main():
    if not os.path.isfile(EXE):
        print("no portable Kodi: run tools/setup_kodi.py first")
        return 1

    # The whole point is to exercise the windows, so turn them on.
    shots_dir = SCREENSHOT_DIRS[0]
    if not os.path.isdir(shots_dir):
        os.makedirs(shots_dir)
    _set_kodi_setting("debug.screenshotpath", shots_dir + os.sep)

    set_addon_setting("ui.window_home", "true")
    set_addon_setting("ui.window_search", "true")
    set_addon_setting("tmdb.apikey", "")
    enable_webserver()

    if os.path.isfile(LOG):
        os.remove(LOG)

    print("starting Kodi")
    process = subprocess.Popen([EXE, "-p"])
    try:
        if not wait_for_kodi():
            print("Kodi never answered")
            return 1

        mark = log_length()
        print("opening the custom home window")
        open_home()
        time.sleep(10)

        path, error = screenshot("home")
        print("  screenshot: %s" % (path or "failed: %s" % error))

        problems = window_problems(log_since(mark))
        katan = [l.strip() for l in log_since(mark) if "[Katan]" in l]

        print("\nadd-on log while the window was open:")
        for line in katan[-12:]:
            print("  " + line.split("[Katan]", 1)[-1].strip())

        print("\nwindow loading problems: %d" % len(problems))
        for line in problems[:12]:
            print("  " + line)

        # A window that failed to open leaves the plugin dialog behind.
        try:
            current = rpc("GUI.GetProperties", {"properties": ["currentwindow"]})
            window = (current or {}).get("currentwindow", {})
            print("\ncurrent window: %s (id %s)"
                  % (window.get("label"), window.get("id")))
        except Exception as error:
            print("could not read the current window: %s" % str(error)[:80])

        return 1 if problems else 0
    finally:
        try:
            rpc("Application.Quit", timeout=5)
            time.sleep(4)
        except Exception:
            pass
        if process.poll() is None:
            process.kill()
        print("\nKodi stopped")


if __name__ == "__main__":
    sys.exit(main())

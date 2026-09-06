"""Drive the portable Kodi through JSON-RPC and report what the add-on did.

This is the integration test the unit suite cannot be: it starts a real Kodi,
asks it to open real plugin paths, and reads back what the add-on produced.
Files.GetDirectory on a plugin:// path runs the plugin exactly as a user would.

    python tools/drive_kodi.py            run the standard checks
    python tools/drive_kodi.py --keep     leave Kodi running afterwards
"""
import json
import os
import re
import subprocess
import sys
import time

# Channel and programme names are Hebrew, and the Windows console defaults to
# a codepage that cannot represent them. Without this the driver dies on its
# own output rather than on anything the add-on did.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:      # pragma: no cover
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KODI_DIR = os.path.join(ROOT, ".kodi-test")
EXE = os.path.join(KODI_DIR, "kodi.exe")
PORTABLE = os.path.join(KODI_DIR, "portable_data")
SETTINGS = os.path.join(PORTABLE, "userdata", "guisettings.xml")
LOG = os.path.join(PORTABLE, "kodi.log")

PORT = 8080
ENDPOINT = "http://127.0.0.1:%d/jsonrpc" % PORT
PLUGIN = "plugin://plugin.video.katan/"


def enable_webserver():
    """Turn on JSON-RPC over HTTP, without authentication, on loopback only."""
    if not os.path.isfile(SETTINGS):
        print("no guisettings.xml yet: start Kodi once first")
        return False
    with open(SETTINGS, encoding="utf-8") as handle:
        text = handle.read()

    wanted = {
        "services.webserver": "true",
        "services.webserverauthentication": "false",
        "services.webserverport": str(PORT),
        "services.esenabled": "true",
    }
    for key, value in wanted.items():
        pattern = r'<setting id="%s"[^>]*>.*?</setting>' % re.escape(key)
        replacement = '<setting id="%s">%s</setting>' % (key, value)
        if re.search(pattern, text):
            text = re.sub(pattern, replacement, text)
        else:
            selfclosing = r'<setting id="%s"[^>]*/>' % re.escape(key)
            if re.search(selfclosing, text):
                text = re.sub(selfclosing, replacement, text)

    with open(SETTINGS, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("enabled the web server on port %d" % PORT)
    return True


def rpc(method, params=None, timeout=60):
    """One JSON-RPC call."""
    import requests

    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = requests.post(ENDPOINT, json=body, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError("%s: %s" % (method, payload["error"]))
    return payload.get("result")


def wait_for_kodi(seconds=90):
    started = time.time()
    while time.time() - started < seconds:
        try:
            rpc("JSONRPC.Ping", timeout=5)
            return True
        except Exception:
            time.sleep(2)
    return False


def directory(path, label=None):
    """Open a plugin path and summarise what came back."""
    label = label or path
    started = time.time()
    try:
        result = rpc("Files.GetDirectory", {
            "directory": path,
            "media": "video",
            "properties": ["title", "art", "plot"],
        }, timeout=120)
    except Exception as error:
        return {"label": label, "ok": False, "count": 0,
                "ms": int((time.time() - started) * 1000),
                "note": str(error)[:120]}

    files = result.get("files") or []
    return {
        "label": label,
        "ok": True,
        "count": len(files),
        "ms": int((time.time() - started) * 1000),
        "sample": [f.get("label", "") for f in files[:3]],
        "note": "",
    }


CHECKS = [
    (PLUGIN + "?action=home&nowindow=1", "Home"),
    (PLUGIN + "?action=live_tv", "Live TV"),
    (PLUGIN + "?action=channels&kind=radio", "Radio"),
    (PLUGIN + "?action=vod", "VOD broadcasters"),
    (PLUGIN + "?action=vod_module&module=kan", "VOD: Kan"),
    (PLUGIN + "?action=vod_module&module=keshet", "VOD: Keshet"),
    (PLUGIN + "?action=tools", "Tools"),
    (PLUGIN + "?action=row&id=anime_trending", "Row: trending anime"),
    (PLUGIN + "?action=search_query&q=dune", "Search: dune"),
]


def katan_log_lines(since=0):
    if not os.path.isfile(LOG):
        return []
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return [line.rstrip() for line in lines[since:] if "[Katan" in line]


def log_length():
    if not os.path.isfile(LOG):
        return 0
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        return len(handle.readlines())


def python_errors():
    if not os.path.isfile(LOG):
        return []
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    found = []
    for match in re.finditer(r"^.*(Traceback|ModuleNotFoundError|ImportError"
                             r"|CPythonInvoker.*failed).*$", text, re.M):
        found.append(match.group(0).strip()[:160])
    return found


def run_checks():
    rows = []
    for path, label in CHECKS:
        rows.append(directory(path, label))
        print("  %-24s %s" % (label, _summary(rows[-1])))
    return rows


def _summary(row):
    if not row["ok"]:
        return "FAILED  %s" % row["note"]
    sample = ", ".join(s for s in row.get("sample", []) if s)
    return "%3d items  %5d ms  %s" % (row["count"], row["ms"], sample[:70])


def main():
    if not os.path.isfile(EXE):
        print("no portable Kodi: run tools/setup_kodi.py first")
        return 1

    enable_webserver()

    print("starting Kodi")
    process = subprocess.Popen([EXE, "-p"])
    try:
        if not wait_for_kodi():
            print("Kodi did not answer JSON-RPC in time")
            return 1
        print("Kodi is up")

        properties = rpc("Application.GetProperties",
                         {"properties": ["version", "name"]})
        version = properties.get("version", {})
        print("running %s %s.%s\n" % (properties.get("name", "Kodi"),
                                      version.get("major"), version.get("minor")))

        print("opening add-on paths:")
        rows = run_checks()

        print("\nrunning the device report")
        try:
            rpc("Addons.ExecuteAddon",
                {"addonid": "plugin.video.katan",
                 "params": {"action": "diagnostics"}}, timeout=120)
            time.sleep(12)
        except Exception as error:
            print("  could not run it: %s" % str(error)[:120])

        report = [line for line in katan_log_lines() if "[Katan]" in line]
        print("\nadd-on log lines: %d" % len(report))
        for line in report[-45:]:
            print("  " + line.split("[Katan]", 1)[-1].strip())

        errors = python_errors()
        print("\npython errors: %d" % len(errors))
        for line in errors[:10]:
            print("  " + line)

        failed = [r for r in rows if not r["ok"]]
        empty = [r for r in rows if r["ok"] and r["count"] == 0]
        print("\n%d paths opened, %d failed, %d returned nothing"
              % (len(rows), len(failed), len(empty)))
        return 1 if failed or errors else 0
    finally:
        if "--keep" not in sys.argv:
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

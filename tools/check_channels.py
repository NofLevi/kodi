"""Probe every Israeli channel and report which ones actually stream.

The channel list is data, and data goes stale: broadcasters move CDNs, retire
backup feeds and change paths. This resolves each entry with the add-on's own
code and then asks the resulting URL for a few bytes, so the answer is what
works today rather than what worked when the list was written.

    python tools/check_channels.py            television
    python tools/check_channels.py --radio    radio as well
    python tools/check_channels.py --json     machine readable
    python tools/check_channels.py --update   record the result in the data

--update writes a "working" flag into resources/data/channels.json. The add-on
hides channels marked as not working, so the list a user sees is the list that
plays, rather than one where a third of the entries fail.
"""
import json
import os
import sys
from concurrent import futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.katan", "resources", "lib"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:      # pragma: no cover
    pass

import xbmcaddon  # noqa: E402

PROFILE = os.path.join(ROOT, ".kodi-test", "probe-profile")
if not os.path.isdir(PROFILE):
    os.makedirs(PROFILE)
xbmcaddon.reset(PROFILE, os.path.join(ROOT, "plugin.video.katan"))

from katan.vod import channels  # noqa: E402

TIMEOUT = 12
WORKERS = 8

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}


def probe(channel_id, entry):
    """Resolve one channel and see whether the stream answers."""
    import requests

    name = entry.get("name", channel_id)
    try:
        url, headers, adaptive = channels.resolve(channel_id)
    except Exception as error:
        return {"id": channel_id, "name": name, "state": "resolve error",
                "detail": str(error)[:80], "url": ""}

    if not url:
        return {"id": channel_id, "name": name, "state": "no url",
                "detail": "nothing to play", "url": ""}

    request_headers = dict(BROWSER)
    request_headers.update(headers or {})

    try:
        response = requests.get(url, headers=request_headers, timeout=TIMEOUT,
                                stream=True, allow_redirects=True)
        code = response.status_code
        body = b""
        if code < 400:
            body = next(response.iter_content(2048), b"")
        response.close()
    except Exception as error:
        return {"id": channel_id, "name": name, "state": "unreachable",
                "detail": type(error).__name__, "url": url}

    if code >= 400:
        return {"id": channel_id, "name": name, "state": "http %d" % code,
                "detail": "", "url": url}

    kind = _stream_kind(url, body)
    return {"id": channel_id, "name": name, "state": "ok", "detail": kind,
            "url": url}


def _stream_kind(url, body):
    """Confirm the response really is a stream, not an error page."""
    head = body[:200].lstrip()
    if head.startswith(b"#EXTM3U"):
        return "HLS playlist"
    if head.startswith(b"<MPD") or b"<MPD" in body[:400]:
        return "DASH manifest"
    if head.startswith(b"<!DOCTYPE") or head.startswith(b"<html"):
        return "HTML page, not a stream"
    if ".m3u8" in url:
        return "responded, not a playlist"
    return "binary stream"


def update_data(results):
    """Record what worked into the bundled channel list."""
    path = os.path.join(ROOT, "plugin.video.katan", "resources", "data",
                        "channels.json")
    with open(path, encoding="utf-8") as handle:
        table = json.load(handle)

    changed = 0
    for row in results:
        entry = table.get(row["id"])
        if entry is None:
            continue
        working = row["state"] == "ok" and "not a stream" not in row["detail"]
        if entry.get("working") != working:
            entry["working"] = working
            changed += 1

    with open(path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(table, handle, ensure_ascii=False, indent=1, sort_keys=True)
    print("updated %d entries in channels.json" % changed)
    return changed


def main():
    include_radio = "--radio" in sys.argv
    table = channels.load()
    wanted = [(key, value) for key, value in table.items()
              if include_radio or value.get("type") == "tv"]
    wanted.sort(key=lambda kv: (kv[1].get("type", ""), int(kv[1].get("index") or 999)))

    results = []
    with futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs = {pool.submit(probe, key, value): key for key, value in wanted}
        for job in futures.as_completed(jobs):
            results.append(job.result())

    order = {key: position for position, (key, _v) in enumerate(wanted)}
    results.sort(key=lambda r: order.get(r["id"], 999))

    if "--json" in sys.argv:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    working = [r for r in results if r["state"] == "ok"
               and "not a stream" not in r["detail"]]
    print("%-10s %-26s %-12s %s" % ("id", "channel", "state", "detail"))
    print("-" * 78)
    for row in results:
        print("%-10s %-26s %-12s %s"
              % (row["id"], row["name"][:24], row["state"], row["detail"]))

    print()
    print("%d of %d channels streaming" % (len(working), len(results)))
    broken = [r for r in results if r not in working]
    if broken:
        print("not working: %s" % ", ".join(r["id"] for r in broken))

    if "--update" in sys.argv:
        update_data(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())

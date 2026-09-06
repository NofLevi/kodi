"""Device self-check.

Windows testing proves the logic is right. It cannot prove the add-on runs well
on a particular box, because the things most likely to go wrong there are
properties of the device: which codecs the hardware decodes, how much memory
Kodi has left, whether inputstream.adaptive is present, whether the Hebrew font
renders, and how slow the storage is.

This module measures exactly those, on whatever device it runs on, so the
answer is data rather than hope. Run it from Tools on each device.
"""
import os
import platform
import sys
import time

import xbmc

from . import cache, http, kodi, settings

# What a home screen should cost on the weakest target device.
BUDGET_HOME_MS = 2000
BUDGET_CACHE_WRITE_MS = 400
BUDGET_NETWORK_MS = 2500


def collect():
    """Every check, as a list of (section, name, value, verdict) rows.

    verdict is "ok", "warn", "fail" or "" for plain information.
    """
    rows = []
    rows.extend(_platform_rows())
    rows.extend(_python_rows())
    rows.extend(_codec_rows())
    rows.extend(_addon_rows())
    rows.extend(_font_rows())
    rows.extend(_artwork_rows())
    rows.extend(_storage_rows())
    rows.extend(_speed_rows())
    return rows


# --------------------------------------------------------------------------
# platform
# --------------------------------------------------------------------------


def _platform_rows():
    build = xbmc.getInfoLabel("System.BuildVersion")
    rows = [
        ("Platform", "Kodi", build, _kodi_verdict(build)),
        ("Platform", "System", _system_name(), ""),
        ("Platform", "Architecture", platform.machine() or "unknown", ""),
        ("Platform", "Screen", xbmc.getInfoLabel("System.ScreenMode"), ""),
        ("Platform", "Free memory",
         xbmc.getInfoLabel("System.FreeMemory"), _memory_verdict()),
    ]
    return rows


def _system_name():
    for label, condition in (("Android", "System.Platform.Android"),
                             ("Linux", "System.Platform.Linux"),
                             ("Windows", "System.Platform.Windows"),
                             ("macOS", "System.Platform.OSX"),
                             ("webOS", "System.Platform.Webos")):
        if xbmc.getCondVisibility(condition):
            return label
    return sys.platform


def _kodi_verdict(build):
    try:
        major = int((build or "0").split(".")[0])
    except ValueError:
        return "warn"
    if major >= 21:
        return "ok"
    if major == 20:
        return "warn"
    return "fail"


def _memory_verdict():
    free = xbmc.getInfoLabel("System.FreeMemory") or ""
    digits = "".join(c for c in free if c.isdigit())
    if not digits:
        return ""
    megabytes = int(digits)
    if megabytes < 120:
        return "fail"
    if megabytes < 300:
        return "warn"
    return "ok"


def _python_rows():
    version = "%d.%d.%d" % sys.version_info[:3]
    verdict = "ok" if sys.version_info >= (3, 8) else "fail"
    rows = [("Runtime", "Python", version, verdict)]
    # requests is optional. When it is absent the standard-library session
    # takes over, so its absence is information, not a fault.
    rows.append(("Runtime", "HTTP backend", http.backend(), "ok"))
    try:
        import sqlite3
        rows.append(("Runtime", "SQLite", sqlite3.sqlite_version, "ok"))
    except ImportError:
        rows.append(("Runtime", "SQLite", "missing", "fail"))
    # int.bit_count makes subtitle sync noticeably faster.
    fast_popcount = hasattr(int, "bit_count")
    rows.append(("Runtime", "Fast bit count",
                 "yes" if fast_popcount else "no (falls back)",
                 "ok" if fast_popcount else "warn"))
    return rows


# --------------------------------------------------------------------------
# codecs, which is where a weak box actually fails
# --------------------------------------------------------------------------

# Kodi exposes hardware decoding capability through these conditions.
CODEC_CHECKS = [
    ("H.264", "System.HasHWDecoder(h264)", "sources.allow_hevc", None),
    ("HEVC / H.265", "System.HasHWDecoder(hevc)", "sources.allow_hevc", True),
    ("AV1", "System.HasHWDecoder(av1)", "sources.allow_av1", True),
    ("VP9", "System.HasHWDecoder(vp9)", None, None),
]


def _codec_rows():
    """Check hardware decoding, and flag settings the device cannot honour.

    This is the check that matters most. Asking a Mali-G31 to software decode
    4K HEVC is the single most reliable way to make playback stutter, and no
    amount of testing on a PC would reveal it.
    """
    rows = []
    for label, condition, setting_key, wants_true in CODEC_CHECKS:
        supported = bool(xbmc.getCondVisibility(condition))
        verdict = "ok" if supported else "warn"
        note = "hardware" if supported else "software only"

        if setting_key and wants_true and settings.get_bool(setting_key) and not supported:
            verdict = "fail"
            note = "enabled in settings but not decoded in hardware"
        rows.append(("Codecs", label, note, verdict))

    hdr = bool(xbmc.getCondVisibility("System.HDRDisplay"))
    rows.append(("Codecs", "HDR display", "yes" if hdr else "no",
                 "fail" if settings.get_bool("sources.allow_hdr") and not hdr
                 else "ok"))

    ceiling = settings.get("sources.max_resolution")
    screen = xbmc.getInfoLabel("System.ScreenMode") or ""
    rows.append(("Codecs", "Resolution ceiling", ceiling,
                 _resolution_verdict(ceiling, screen)))
    return rows


def _resolution_verdict(ceiling, screen):
    """Downloading 4K for a 720p panel wastes bandwidth and decoding budget."""
    lowered = screen.lower()
    if "720" in lowered and ceiling in ("1080p", "2160p"):
        return "warn"
    if "1080" in lowered and ceiling == "2160p":
        return "warn"
    return "ok"


def _addon_rows():
    rows = []
    # Only inputstream.adaptive is genuinely needed, and only for the live
    # channels that use DASH. Everything else here is a nicety.
    for addon_id, label, required in (
            ("inputstream.adaptive", "InputStream Adaptive", True),
            ("script.module.requests", "Requests (optional speed-up)", False),
            ("service.upnext", "Up Next", False),
            ("plugin.video.youtube", "YouTube", False)):
        present = _addon_present(addon_id)
        if present:
            verdict = "ok"
            note = "installed"
        elif required:
            verdict = "fail"
            note = "missing, needed for DASH live channels"
        else:
            verdict = ""
            note = "not installed (optional)"
        rows.append(("Add-ons", label, note, verdict))
    return rows


def _addon_present(addon_id):
    try:
        import xbmcaddon
        xbmcaddon.Addon(addon_id)
        return True
    except Exception:
        return False


def _font_rows():
    """Hebrew needs a font that has the glyphs and a skin that lays out RTL."""
    language = xbmc.getInfoLabel("System.Language")
    wanted = settings.get("ui.language")
    hebrew_ui = wanted == "he" or "hebrew" in (language or "").lower()
    return [
        ("Language", "Kodi language", language or "unknown", ""),
        ("Language", "Add-on language", wanted, ""),
        ("Language", "Right to left layout",
         "on" if hebrew_ui else "off",
         "warn" if hebrew_ui and not _skin_supports_rtl() else "ok"),
    ]


def _skin_supports_rtl():
    """Estuary handles RTL. A cut-down skin may not."""
    skin = xbmc.getSkinDir() or ""
    return skin in ("skin.estuary", "skin.estouchy") or "estuary" in skin


# --------------------------------------------------------------------------
# storage and speed, measured rather than assumed
# --------------------------------------------------------------------------


def _artwork_rows():
    """Estimate what the visible artwork costs in memory.

    Kodi holds decoded bitmaps, not the compressed downloads, so this is
    usually the largest single allocation the add-on causes. On a device with
    a gigabyte of RAM shared with Android it is worth seeing the number.
    """
    from . import profiles

    width = int((settings.get("ui.poster_size") or "w342").lstrip("w") or 342)
    per_row = settings.get_int("ui.row_items", 20)
    visible_rows = 3                      # roughly what fits on screen at once
    on_screen = 9                         # posters visible across one row

    bitmap = width * (width * 1.5) * 4
    held = bitmap * visible_rows * min(per_row, on_screen + 2)
    megabytes = held / (1024.0 * 1024.0)

    free = profiles._free_megabytes()
    verdict = "ok"
    if free and megabytes > free * 0.35:
        verdict = "fail"
    elif free and megabytes > free * 0.2:
        verdict = "warn"
    elif megabytes > 60:
        verdict = "warn"

    return [
        ("Memory", "Poster width", "%dpx" % width, ""),
        ("Memory", "Items per row", str(per_row), ""),
        ("Memory", "Visible artwork", "about %.0f MB" % megabytes, verdict),
        ("Memory", "Profile", profiles.current(), ""),
    ]


def _storage_rows():
    rows = []
    stats = cache.stats()
    rows.append(("Storage", "Cache entries", str(stats["entries"]), ""))
    rows.append(("Storage", "Cache on disk",
                 "%.1f MB" % (stats["file_bytes"] / (1024.0 * 1024.0)),
                 "warn" if stats["file_bytes"] > settings.get_int("cache.max_mb")
                 * 1024 * 1024 else "ok"))

    subtitle_dir = os.path.join(kodi.profile_path(), "subtitles")
    count = 0
    total = 0
    if os.path.isdir(subtitle_dir):
        for name in os.listdir(subtitle_dir):
            path = os.path.join(subtitle_dir, name)
            try:
                total += os.path.getsize(path)
                count += 1
            except OSError:
                pass
    rows.append(("Storage", "Subtitle files",
                 "%d (%.1f MB)" % (count, total / (1024.0 * 1024.0)), ""))
    return rows


def _speed_rows():
    """Three timings that predict how the add-on will feel on this device."""
    rows = []

    write_ms = _time_cache_write()
    rows.append(("Speed", "Cache write", "%d ms" % write_ms,
                 "ok" if write_ms < BUDGET_CACHE_WRITE_MS else "warn"))

    sync_ms = _time_subtitle_sync()
    rows.append(("Speed", "Subtitle alignment", "%d ms" % sync_ms,
                 "ok" if sync_ms < 3000 else "warn"))

    network_ms = _time_network()
    if network_ms < 0:
        rows.append(("Speed", "Network", "no response", "fail"))
    else:
        rows.append(("Speed", "Network round trip", "%d ms" % network_ms,
                     "ok" if network_ms < BUDGET_NETWORK_MS else "warn"))

    home_ms = _time_home_render()
    rows.append(("Speed", "Home from cache", "%d ms" % home_ms,
                 "ok" if home_ms < BUDGET_HOME_MS else "warn"))
    return rows


def _time_cache_write():
    """Android flash storage is often far slower than a PC disk."""
    payload = {"rows": [{"title": "x" * 80} for _ in range(200)]}
    started = time.time()
    for index in range(5):
        cache.set("diag.write.%d" % index, payload, 60)
    elapsed = (time.time() - started) * 1000 / 5.0
    for index in range(5):
        cache.delete("diag.write.%d" % index)
    return int(elapsed)


def _time_subtitle_sync():
    """The alignment is pure CPU, so this is a clean measure of the processor."""
    from .subs import srt, sync

    cues = []
    moment = 10.0
    for index in range(900):
        cues.append(srt.Cue(index + 1, moment, moment + 2.0, "x"))
        moment += 4.0
    shifted = [cue.shifted(12.0) for cue in cues]

    started = time.time()
    sync.fit(shifted, cues)
    return int((time.time() - started) * 1000)


def _time_network():
    started = time.time()
    response = http.get("https://api.themoviedb.org/3/configuration",
                        params={"api_key": settings.get("tmdb.apikey") or "x"},
                        timeout=(4, 8), retries=0)
    if response is None:
        return -1
    return int((time.time() - started) * 1000)


def _time_home_render():
    """How long the home rows take to come back from the cache."""
    from . import catalog

    started = time.time()
    total = 0
    for row_id in catalog.enabled_row_ids()[:6]:
        total += len(catalog.peek(row_id) or [])
    return int((time.time() - started) * 1000)


# --------------------------------------------------------------------------
# presentation
# --------------------------------------------------------------------------

_MARKS = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "": "      "}


def as_text(rows=None):
    """A plain report, for the log or for pasting into a message."""
    rows = rows if rows is not None else collect()
    lines = ["Katan device report", "=" * 52]
    section = None
    for name, label, value, verdict in rows:
        if name != section:
            section = name
            lines.append("")
            lines.append(name)
        lines.append("  %s %-24s %s" % (_MARKS.get(verdict, ""), label, value))

    failures = [r for r in rows if r[3] == "fail"]
    warnings = [r for r in rows if r[3] == "warn"]
    lines.append("")
    lines.append("%d checks, %d failed, %d warnings"
                 % (len(rows), len(failures), len(warnings)))
    return "\n".join(lines)


def run_and_log():
    """Write the report to the Kodi log and return it."""
    report = as_text()
    for line in report.split("\n"):
        kodi.log(line, kodi.LOG_INFO)
    return report


def summary():
    """One line saying whether this device is in good shape."""
    rows = collect()
    failures = sum(1 for r in rows if r[3] == "fail")
    warnings = sum(1 for r in rows if r[3] == "warn")
    if failures:
        return "fail", "%d checks failed" % failures
    if warnings:
        return "warn", "%d warnings" % warnings
    return "ok", "all checks passed"

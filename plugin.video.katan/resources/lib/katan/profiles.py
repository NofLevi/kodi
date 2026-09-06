"""Device profiles.

Calibration note. The BYINTEK LOVE U4 has 1 GB of RAM and 8 GB of storage,
runs Android 9 on four Cortex-A53 cores with a Mali-G31 MP2, and projects at
1280x720. It runs the Kodi POV IL build well, so the device is not short of
capability and treating it as fragile would make this add-on worse for no
reason. But a gigabyte shared with Android leaves Kodi a few hundred megabytes,
which is why a burst of work can end the process outright.

What actually broke that build on the same hardware was load, not baseline
cost: many scrapers running at once, and a subtitle search that fetches
candidate after candidate. Those are bursts of concurrent work and memory, and
they are what the profiles here control.

That is why a low-memory profile does not switch off the custom home screen.
A Kodi skin is loaded for the whole session; this window exists only while it
is on screen, so it is the cheaper of the two, and a device that runs FENtastic
comfortably will run this.

What the profiles actually change:

    how many providers run at once, and how long they may take
    how many of them are enabled in the first place
    how much artwork is held in memory, which on a 1 GB device is the
      single largest cost: Kodi caches decoded bitmaps, so a w342 poster
      occupies about 700 KB while a w185 one occupies about 205 KB
    how large a file may be, and how much is cached on disk
    whether work is done ahead of time
"""
from . import kodi, settings

# Every profile sets exactly these keys, so switching never leaves a stale
# value behind from the profile before it.
LOW_MEMORY = {
    # The crash case: fewer providers, fewer threads, a shorter deadline.
    # Torrentio alone answers in one request because it crawls server-side,
    # which is the whole point of preferring aggregators on a small device.
    "sources.provider.torrentio": "true",
    "sources.provider.comet": "false",
    "sources.provider.mediafusion": "false",
    "sources.provider.zilean": "false",
    "sources.provider.external": "false",
    "sources.workers": "2",
    "sources.timeout": "10",
    "sources.results": "6",
    "sources.cached_only": "true",
    "sources.prefetch_next": "false",

    # A 720p panel gains nothing from 4K, and decoding it costs everything.
    "sources.max_resolution": "1080p",
    "sources.allow_hdr": "false",
    "sources.allow_av1": "false",
    "sources.max_size_gb": "8",
    "sources.size_preference": "smallest",

    # One subtitle provider at a time, and a small on-disk footprint.
    "subs.provider.subsource": "false",
    "subs.cache_files": "30",
    "subs.ai.chunk": "50",
    "cache.max_mb": "25",

    # The custom screens stay on. See the note at the top of this file.
    "ui.poster_size": "w185",
    "ui.row_items": "12",
    "ui.window_home": "true",
    "ui.window_search": "true",
    "service.warm": "true",
}

BALANCED = {
    "sources.provider.torrentio": "true",
    "sources.provider.comet": "false",
    "sources.provider.mediafusion": "false",
    "sources.provider.zilean": "false",
    "sources.provider.external": "false",
    "sources.workers": "4",
    "sources.timeout": "12",
    "sources.results": "8",
    "sources.cached_only": "true",
    "sources.prefetch_next": "true",

    "sources.max_resolution": "1080p",
    "sources.allow_hdr": "false",
    "sources.allow_av1": "false",
    "sources.max_size_gb": "12",
    "sources.size_preference": "balanced",

    "subs.provider.subsource": "true",
    "subs.cache_files": "60",
    "subs.ai.chunk": "80",
    "cache.max_mb": "50",

    "ui.poster_size": "w342",
    "ui.row_items": "20",
    "ui.window_home": "true",
    "ui.window_search": "true",
    "service.warm": "true",
}

POWERFUL = {
    "sources.provider.torrentio": "true",
    "sources.provider.comet": "true",
    "sources.provider.mediafusion": "true",
    "sources.provider.zilean": "true",
    "sources.provider.external": "true",
    "sources.workers": "6",
    "sources.timeout": "18",
    "sources.results": "15",
    "sources.cached_only": "false",
    "sources.prefetch_next": "true",

    "sources.max_resolution": "2160p",
    "sources.allow_hdr": "true",
    "sources.allow_av1": "true",
    "sources.max_size_gb": "40",
    "sources.size_preference": "balanced",

    "subs.provider.subsource": "true",
    "subs.cache_files": "200",
    "subs.ai.chunk": "120",
    "cache.max_mb": "200",

    "ui.poster_size": "w500",
    "ui.row_items": "20",
    "ui.window_home": "true",
    "ui.window_search": "true",
    "service.warm": "true",
}

PROFILES = {
    "low_memory": LOW_MEMORY,
    "balanced": BALANCED,
    "powerful": POWERFUL,
}

NAMES = {
    "low_memory": 32380,
    "balanced": 32381,
    "powerful": 32382,
}


def apply(name):
    """Apply a profile. Returns how many settings changed."""
    values = PROFILES.get(name)
    if not values:
        return 0
    changed = 0
    for key, value in values.items():
        if settings.get(key) != value:
            settings.set(key, value)
            changed += 1
    settings.set("device.profile", name)
    kodi.log("applied the %s profile (%d settings changed)" % (name, changed),
             kodi.LOG_INFO)
    return changed


def current():
    return settings.get("device.profile") or "balanced"


def recommend():
    """Pick a profile from what the device reports about itself.

    The thresholds assume a device that already runs Kodi with a skin, because
    that is the situation this add-on is installed into. A box comfortably
    running a full build does not need the low profile; it needs its bursts of
    concurrent work bounded, which balanced already does.
    """
    import xbmc

    free = _free_megabytes()
    cores = _core_count()
    screen = (xbmc.getInfoLabel("System.ScreenMode") or "").lower()

    # The reason is shown beside the suggested profile in the chooser, which
    # is otherwise entirely Hebrew, so it is localised rather than literal.
    if free and free < 250:
        return "low_memory", kodi.localize(32405, free)
    if free and free < 450 and cores <= 4:
        return "low_memory", kodi.localize(32406, free, cores)
    if free and free > 1800 and cores >= 6:
        return "powerful", kodi.localize(32406, free, cores)
    if "2160" in screen or "3840" in screen:
        return "powerful", kodi.localize(32407)
    return "balanced", kodi.localize(32408)


def _free_megabytes():
    import xbmc
    label = xbmc.getInfoLabel("System.FreeMemory") or ""
    digits = "".join(c for c in label if c.isdigit())
    return int(digits) if digits else 0


def _core_count():
    try:
        import multiprocessing
        return multiprocessing.cpu_count()
    except Exception:
        return 4


def describe(name):
    """A short summary of what a profile does, for the chooser."""
    values = PROFILES.get(name, {})
    if not values:
        return ""
    providers = sum(1 for key, value in values.items()
                    if key.startswith("sources.provider.") and value == "true")
    return kodi.localize(
        32409,
        values.get("sources.max_resolution", "?"),
        int(values.get("sources.workers") or 0),
        providers,
        values.get("ui.poster_size", "?"),
        values.get("cache.max_mb", "?"))

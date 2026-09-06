"""Typed access to add-on settings.

Kodi's settings API returns strings for everything and raises on unknown ids in
some versions, so every getter here is defensive and falls back to a default.
Defaults live in this module (not only in settings.xml) so unit tests and the
service can run without a populated settings file.
"""
from . import kodi

# --------------------------------------------------------------------------
# defaults - the single source of truth for "what happens out of the box"
# --------------------------------------------------------------------------

DEFAULTS = {
    # accounts
    "tmdb.apikey": "",
    "trakt.client_id": "",
    "trakt.client_secret": "",
    "trakt.access_token": "",
    "trakt.refresh_token": "",
    "trakt.expires": "0",
    "trakt.user": "",
    "mdblist.apikey": "",
    "realdebrid.token": "",
    "realdebrid.refresh": "",
    "realdebrid.client_id": "",
    "realdebrid.client_secret": "",
    "realdebrid.expires": "0",
    "premiumize.client_id": "",
    "premiumize.token": "",
    "premiumize.apikey": "",
    "torbox.apikey": "",
    "alldebrid.apikey": "",
    # sources
    "sources.provider.torrentio": "true",
    "sources.provider.comet": "false",
    "sources.provider.mediafusion": "false",
    "sources.provider.zilean": "false",
    "sources.provider.nyaa": "true",
    "sources.provider.animetosho": "true",
    "sources.provider.external": "false",
    "sources.torrentio.url": "https://torrentio.strem.fun",
    "sources.torrentio.options": "",
    "sources.comet.config": "",
    "sources.comet.url": "https://comet.elfhosted.com",
    "sources.mediafusion.url": "https://mediafusion.elfhosted.com",
    "sources.mediafusion.config": "",
    "sources.zilean.url": "https://zilean.elfhosted.com",
    "sources.timeout": "12",
    "sources.workers": "4",
    "sources.results": "8",
    "sources.cached_only": "true",
    "sources.max_resolution": "1080p",
    "sources.min_resolution": "480p",
    "sources.allow_hevc": "true",
    "sources.allow_av1": "false",
    "sources.allow_hdr": "false",
    "sources.max_size_gb": "12",
    "sources.autoplay": "true",
    "sources.source_memory": "true",
    "sources.seadex": "true",
    # kids mode
    "kids.enabled": "false",
    "kids.age": "older",
    "kids.pin_hash": "",
    "sources.prefetch_next": "true",
    "sources.prefer_hebrew": "true",
    "sources.allow_cam": "false",
    "sources.size_preference": "balanced",
    # subtitles
    "subs.languages": "he,en",
    "subs.auto": "true",
    "subs.embedded_first": "true",
    "subs.hash_match": "true",
    "subs.threshold": "70",
    "subs.provider.wizdom": "true",
    "subs.provider.opensubtitles": "true",
    "subs.provider.subsource": "true",
    # Off by default because it needs an account, and a provider that is on but
    # cannot sign in is a provider that quietly returns nothing.
    "subs.provider.ktuvit": "false",
    "subs.ktuvit.user": "",
    "subs.ktuvit.password": "",
    "subs.opensubtitles.apikey": "",
    "subs.opensubtitles.user": "",
    "subs.opensubtitles.password": "",
    "subs.ai.enabled": "true",
    "subs.ai.engine": "gemini",
    "subs.ai.gemini_key": "",
    "subs.ai.gemini_model": "gemini-2.5-flash",
    "subs.ai.openai_url": "",
    "subs.ai.openai_key": "",
    "subs.ai.openai_model": "",
    "subs.ai.chunk": "80",
    "subs.cache_files": "60",
    # israeli vod
    "vod.channels_url": "",
    "vod.series_url": "",
    "vod.show_radio": "false",
    "vod.show_broken_channels": "false",
    # ui
    "ui.language": "auto",
    "ui.rows": "",
    "ui.region": "IL",
    "ui.hide_watched": "false",
    "ui.show_unaired": "false",
    "ui.upnext": "true",
    "ui.poster_size": "w342",
    "ui.row_items": "20",
    "ui.window_home": "true",
    "ui.window_search": "true",
    "device.profile": "balanced",
    # advanced
    "service.warm": "true",
    "cache.max_mb": "50",
    "cache.meta_hours": "6",
    "log.debug": "false",
}

# Resolution ladder used for comparisons across the code base.
RESOLUTIONS = ["sd", "480p", "720p", "1080p", "2160p"]


def _raw(key):
    try:
        return kodi.addon().getSetting(key)
    except Exception:
        return ""


def get(key, default=None):
    """Return a setting as a string, falling back to the registered default."""
    value = _raw(key)
    if value == "":
        if default is not None:
            return default
        return DEFAULTS.get(key, "")
    return value


def get_bool(key, default=None):
    value = get(key, "" if default is None else ("true" if default else "false"))
    return str(value).lower() in ("true", "1", "yes", "on")


def get_int(key, default=None):
    value = get(key)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        if default is not None:
            return default
        try:
            return int(DEFAULTS.get(key, 0))
        except (TypeError, ValueError):
            return 0


def get_float(key, default=0.0):
    try:
        return float(get(key))
    except (TypeError, ValueError):
        return default


def get_list(key, separator=","):
    """Split a comma separated setting into a list of trimmed, non-empty parts."""
    return [part.strip() for part in get(key).split(separator) if part.strip()]


def set(key, value):  # noqa: A001 - mirrors the Kodi API name
    if isinstance(value, bool):
        value = "true" if value else "false"
    try:
        kodi.addon().setSetting(key, str(value))
    except Exception:
        kodi.log_exception("failed to write setting %s" % key)


def set_many(pairs):
    for key, value in pairs.items():
        set(key, value)


def open_settings():
    kodi.addon().openSettings()


# --------------------------------------------------------------------------
# convenience accessors used all over the add-on
# --------------------------------------------------------------------------


def subtitle_languages():
    """Ordered ISO-639-1 language codes the user wants, e.g. ``['he', 'en']``."""
    langs = get_list("subs.languages")
    return langs or ["he", "en"]


def debug_enabled():
    return get_bool("log.debug")


def enabled_source_providers():
    """Provider ids the user has switched on."""
    prefix = "sources.provider."
    return sorted(
        key[len(prefix):]
        for key in DEFAULTS
        if key.startswith(prefix) and get_bool(key)
    )


def enabled_subtitle_providers():
    prefix = "subs.provider."
    return sorted(
        key[len(prefix):]
        for key in DEFAULTS
        if key.startswith(prefix) and get_bool(key)
    )


def configured_debrid():
    """Debrid services that have credentials, in preference order."""
    services = []
    if get("realdebrid.token"):
        services.append("realdebrid")
    if get("premiumize.token") or get("premiumize.apikey"):
        services.append("premiumize")
    if get("torbox.apikey"):
        services.append("torbox")
    if get("alldebrid.apikey"):
        services.append("alldebrid")
    return services


def resolution_rank(name):
    """Position of a resolution in the ladder; -1 when unknown."""
    try:
        return RESOLUTIONS.index((name or "").lower())
    except ValueError:
        return -1

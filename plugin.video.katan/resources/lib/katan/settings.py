"""Typed access to add-on settings.

Kodi's settings API returns strings for everything and raises on unknown ids in
some versions, so every getter here is defensive and falls back to a default.
Defaults live in this module (not only in settings.xml) so unit tests and the
service can run without a populated settings file.

Every key that `profiles.LOW_MEMORY` names has the same value here, and a test
asserts it. That is the whole of "lightweight by default": the state you get
before touching anything is the lean profile, not a fourth opinion nobody
maintains. The table below had drifted into being `balanced`, which meant an
add-on written for a projector with a gigabyte of RAM shipped w342 posters and
four workers to it.
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
    "sources.provider.torrentsdb": "true",
    "sources.provider.comet": "false",
    "sources.provider.mediafusion": "false",
    "sources.provider.zilean": "false",
    "sources.provider.nyaa": "true",
    "sources.provider.animetosho": "true",
    "sources.provider.external": "false",
    "sources.torrentio.url": "https://torrentio.strem.fun",
    "sources.torrentsdb.url": "https://torrentsdb.com",
    "sources.torrentio.options": "",
    "sources.comet.config": "",
    "sources.comet.url": "https://comet.elfhosted.com",
    "sources.mediafusion.url": "https://mediafusion.elfhosted.com",
    "sources.mediafusion.config": "",
    "sources.zilean.url": "https://zilean.elfhosted.com",
    "sources.timeout": "10",
    "sources.workers": "2",
    "sources.results": "6",
    "sources.cached_only": "true",
    "sources.max_resolution": "1080p",
    # 720p rather than 480p. A 480p release on a 1080p panel looks like a
    # fault rather than a choice, and it is never the thing somebody wanted
    # when they pressed play - it is what is left when everything better has
    # been filtered out, which is a different problem and should look like one.
    "sources.min_resolution": "720p",
    "sources.allow_hevc": "true",
    "sources.allow_av1": "false",
    "sources.allow_hdr": "false",
    "sources.max_size_gb": "8",
    "sources.autoplay": "true",
    "sources.source_memory": "true",
    "sources.seadex": "true",
    # kids mode
    "kids.enabled": "false",
    "kids.age": "older",
    "kids.pin_hash": "",
    "sources.prefetch_next": "false",
    "sources.prefer_hebrew": "true",
    "sources.allow_cam": "false",
    "sources.size_preference": "smallest",
    # subtitles
    "subs.languages": "he,en",
    "subs.auto": "true",
    "subs.embedded_first": "true",
    "subs.hash_match": "true",
    "subs.threshold": "70",
    "subs.provider.wizdom": "true",
    "subs.provider.opensubtitles": "true",
    "subs.provider.opensubtitles_rest": "true",
    "subs.provider.bsplayer": "true",
    "subs.provider.subsource": "false",
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
    "subs.ai.chunk": "50",
    "subs.cache_files": "30",
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
    "ui.poster_size": "w185",
    "ui.row_items": "12",
    # The one switch that trades memory for looks. Off, so the shipped state
    # stays the lean one; profiles.set_rich_visuals is what turns it on and
    # makes it take effect.
    "ui.rich_visuals": "false",
    "ui.window_home": "true",
    "ui.window_search": "true",
    # Go straight into Katan when Kodi starts. Off, because taking over
    # somebody's home screen without being asked is rude; on a box that exists
    # to run this add-on it is the obvious thing to switch on.
    "ui.start_on_boot": "false",
    "update.check_on_start": "false",
    "update.url": "",
    "update.channel": "stable",
    # And once in, stay in: back on the home screen does nothing rather than
    # dropping out to the Kodi interface this add-on replaces. The settings
    # button in the top bar is the way back out.
    # On, because this add-on is the interface on the box it was written for
    # and backing out of its home screen lands on the Kodi interface it
    # replaces - which is nowhere anybody meant to go. Off is still one toggle
    # away, and the top bar's settings button is the door out.
    "ui.stay_in_katan": "true",
    "device.profile": "low_memory",
    # advanced
    "service.warm": "true",
    "cache.max_mb": "25",
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


TRUTHY = ("true", "1", "yes", "on")


def get_bool(key, default=None):
    """A boolean setting, falling back to DEFAULTS when it has never been set.

    This used to pass an empty string down as get()'s default, and get()
    returns any default that is not None *instead of* consulting DEFAULTS. So
    an unset boolean came back as "" and therefore False, whatever DEFAULTS
    said. Every caller that relied on the registered default got the opposite:
    sources.autoplay, cached_only, prefer_hebrew and source_memory all default
    to true and all read as false.

    Kodi itself hides this in normal use, because it writes every declared
    setting into its own settings.xml from the <default> in the XML, so the
    value is rarely genuinely absent. It bites for a setting that is not
    declared, and it made the whole DEFAULTS table meaningless under test.
    """
    value = _raw(key)
    if value == "":
        if default is not None:
            return bool(default)
        value = DEFAULTS.get(key, "")
    return str(value).lower() in TRUTHY


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
    """Whether this add-on writes its own messages where you can see them.

    Read through kodi.verbose(), which remembers the answer for the process
    because log() asks on every line.
    """
    return get_bool("log.debug", False)


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

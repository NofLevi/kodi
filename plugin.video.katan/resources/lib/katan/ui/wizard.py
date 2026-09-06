"""First-run setup.

One screen per account, each skippable, in the order that unlocks the most:
TMDB first because nothing renders without it, then a debrid service because
nothing plays without one, then the optional extras.
"""
from .. import kodi, settings

TMDB_SIGNUP = "https://www.themoviedb.org/settings/api"
GEMINI_SIGNUP = "https://aistudio.google.com/apikey"
TRAKT_APPS = "https://trakt.tv/oauth/applications"


def run(open_home_after=True):
    steps = [
        (kodi.localize(32310), step_tmdb, lambda: bool(settings.get("tmdb.apikey"))),
        (kodi.localize(32311), step_debrid, lambda: bool(settings.configured_debrid())),
        (kodi.localize(32312), step_trakt, lambda: bool(settings.get("trakt.access_token"))),
        (kodi.localize(32313), step_ai, lambda: bool(settings.get("subs.ai.gemini_key"))),
        # Not an account, and the only step that is already answered when the
        # wizard opens. It is here rather than buried in the settings because
        # it is the one decision that changes how the add-on looks, and the
        # shipped answer is the cautious one.
        (kodi.localize(32410), step_visuals, lambda: True),
    ]
    while True:
        labels = []
        for title, _, done in steps:
            labels.append("%s  %s" % ("[OK]" if done() else "[  ]", title))
        labels.append(kodi.localize(32314))     # finish
        choice = kodi.select(labels, kodi.localize(32260))
        if choice < 0 or choice >= len(steps):
            break
        try:
            steps[choice][1]()
        except Exception:
            kodi.log_exception("setup step failed")
            kodi.notify(kodi.localize(32315))
    _finish(open_home_after=open_home_after)


def _finish(open_home_after=True):
    """Warm the rows, then go where the viewer was trying to get to.

    Setup is not the destination. Finishing it and being returned to a Kodi
    file list reads as though nothing happened, so once there is a key the
    Katan window is opened directly.
    """
    from .. import catalog
    catalog.invalidate()

    has_key = bool(settings.get("tmdb.apikey"))
    if has_key:
        catalog.warm(force=True)
    kodi.notify(kodi.localize(32316))

    if open_home_after and has_key and settings.get_bool("ui.window_home", True):
        from .home_window import open_home
        open_home()


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------


def step_tmdb():
    kodi.ok_dialog(kodi.localize(32317, TMDB_SIGNUP), kodi.localize(32310))
    key = kodi.keyboard(settings.get("tmdb.apikey"), kodi.localize(32310))
    if key is None:
        return
    key = key.strip()
    settings.set("tmdb.apikey", key)
    if key and _tmdb_key_works():
        kodi.notify(kodi.localize(32318))
    elif key:
        kodi.notify(kodi.localize(32319))


def _tmdb_key_works():
    from ..meta import tmdb
    from .. import cache
    cache.delete_prefix("tmdb|")
    return bool(tmdb.trending("movie", "day"))


def step_debrid():
    services = [
        ("torbox", "TorBox"),
        ("realdebrid", "Real-Debrid"),
        ("premiumize", "Premiumize"),
        ("alldebrid", "AllDebrid"),
    ]
    choice = kodi.select([name for _, name in services], kodi.localize(32311))
    if choice < 0:
        return
    service = services[choice][0]
    try:
        from ..debrid import registry
    except ImportError:
        kodi.ok_dialog(kodi.localize(32281))
        return
    client = registry.get(service)
    if client is None:
        kodi.notify(kodi.localize(32320))
        return
    if client.authorize():
        kodi.notify(kodi.localize(32321, services[choice][1]))
    else:
        kodi.notify(kodi.localize(32322))


def step_trakt():
    from ..meta import trakt
    if not trakt.configured():
        kodi.ok_dialog(kodi.localize(32323, TRAKT_APPS), kodi.localize(32312))
        client_id = kodi.keyboard(settings.get("trakt.client_id"), "Client ID")
        if not client_id:
            return
        secret = kodi.keyboard(settings.get("trakt.client_secret"), "Client Secret")
        if secret is None:
            return
        settings.set_many({"trakt.client_id": client_id.strip(),
                           "trakt.client_secret": secret.strip()})

    device = trakt.device_code()
    if not device:
        kodi.notify(kodi.localize(32322))
        return

    import xbmcgui
    progress = xbmcgui.DialogProgress()
    progress.create(kodi.localize(32312),
                    kodi.localize(32324, device.get("verification_url", ""),
                                  device.get("user_code", "")))
    total = float(device.get("expires_in") or 600)

    def tick(seconds_left):
        if progress.iscanceled():
            return False
        progress.update(int(100 - (seconds_left / total) * 100))
        return True

    try:
        token = trakt.poll_device_token(device, on_tick=tick)
    finally:
        progress.close()

    if token:
        trakt.sync_state()
        kodi.notify(kodi.localize(32325, settings.get("trakt.user")))
    else:
        kodi.notify(kodi.localize(32322))


def step_visuals():
    """Light or richer artwork, with what each one costs written down.

    The add-on ships light, because it was written for a projector with a
    gigabyte of RAM shared with Android and artwork is the largest thing it
    allocates: Kodi holds decoded bitmaps, so a w342 poster occupies about
    700 KB against 205 KB at w185.

    Nothing is decided for the viewer here. `profiles.recommend()` reads what
    the device says about itself and its opinion is shown, but a box with room
    to spare is told so rather than quietly switched.
    """
    from .. import profiles

    lean = profiles.LOW_MEMORY
    rich = profiles.RICH_VISUALS
    labels = [
        kodi.localize(32411, lean["ui.poster_size"],
                      int(lean["ui.row_items"]),
                      int(round(profiles.artwork_megabytes(
                          lean["ui.poster_size"], int(lean["ui.row_items"]))))),
        kodi.localize(32412, rich["ui.poster_size"],
                      int(rich["ui.row_items"]),
                      int(round(profiles.artwork_megabytes(
                          rich["ui.poster_size"], int(rich["ui.row_items"]))))),
    ]

    _suggested, why = profiles.recommend()
    heading = "%s   -   %s" % (kodi.localize(32410),
                               kodi.localize(32413, why))
    on = settings.get_bool("ui.rich_visuals", False)
    choice = kodi.select(labels, heading, preselect=1 if on else 0)
    if choice < 0:
        return
    profiles.set_rich_visuals(choice == 1)


def step_ai():
    kodi.ok_dialog(kodi.localize(32326, GEMINI_SIGNUP), kodi.localize(32313))
    key = kodi.keyboard(settings.get("subs.ai.gemini_key"), kodi.localize(32313))
    if key is None:
        return
    settings.set("subs.ai.gemini_key", key.strip())
    if not key.strip():
        return
    try:
        from ..subs.ai import gemini
    except ImportError:
        kodi.notify(kodi.localize(32318))
        return
    if gemini.test_key(key.strip()):
        kodi.notify(kodi.localize(32318))
    else:
        kodi.notify(kodi.localize(32319))

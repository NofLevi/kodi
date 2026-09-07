"""The background service: one thread, mostly asleep.

It exists so that opening the add-on is a database read rather than fifteen
HTTP calls. Three jobs, all cheap:

* warm the enabled home rows, staggered so start-up is not a burst
* keep the Trakt mirror fresh, but only when last_activities says it changed
* prune the cache once a day

The loop wakes every second because the player monitor has to react quickly,
but each job has its own interval and does nothing in between.
"""
import time

import xbmc

from . import cache, catalog, kodi, settings

WARM_INTERVAL = 6 * 3600
SYNC_INTERVAL = 15 * 60
PRUNE_INTERVAL = 24 * 3600
STARTUP_DELAY = 20        # let Kodi finish booting before touching the network

# Long enough for the skin to have drawn its home screen, short enough that it
# does not look like the box forgot. Opening sooner races Kodi's own start-up
# and the window can be dismissed by whatever finishes loading after it.
OPEN_DELAY = 8


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        now = time.time()
        # Stagger the first runs so they never collide with each other.
        self.next_warm = now + STARTUP_DELAY
        self.next_sync = now + STARTUP_DELAY + 10
        self.next_prune = now + 300
        self.next_open = now + OPEN_DELAY
        self.opened = False
        self.player = None

    # -- Kodi callbacks ----------------------------------------------------

    def onSettingsChanged(self):
        kodi.refresh_addon()
        # Row selection or language may have changed; drop the warmed copies.
        catalog.invalidate()
        self.next_warm = time.time() + 2

    # -- jobs --------------------------------------------------------------

    def warm_rows(self):
        if not settings.get_bool("service.warm", True):
            return
        try:
            # Not forced. Every row already carries a TTL chosen for how fast
            # it actually changes - three hours for what is trending, a day
            # for a top-rated chart - and forcing threw all of that away: it
            # re-fetched all thirteen rows every six hours whether or not any
            # of them had gone stale, including ones a viewer had refreshed
            # five minutes earlier. On a device with a few hundred megabytes
            # for Kodi, thirteen unnecessary fetches is a burst worth not
            # having. `onSettingsChanged` still invalidates first, so a
            # settings change refills everything as it did before.
            updated = catalog.warm()
            if updated:
                kodi.log("warmed %d home rows" % updated)
            from .search import unified
            unified.invalidate_index()
        except Exception:
            kodi.log_exception("row warming failed")

    def sync_trakt(self):
        try:
            from .meta import trakt
            if not trakt.authorised():
                return
            if trakt.needs_sync():
                trakt.sync_state()
                catalog.invalidate("continue")
                catalog.invalidate("watchlist")
        except Exception:
            kodi.log_exception("Trakt sync failed")

    def open_on_boot(self):
        """Go straight into Katan when Kodi starts, if the viewer asked for it.

        Off by default, and deliberately: taking over somebody's home screen
        without being asked is rude, and Kodi's own Settings -> Interface ->
        Startup deliberately offers only its own windows. But on a box that
        exists to run this add-on - a projector in a living room - stopping at
        Kodi's home screen every time is a wasted step.

        Once per session, never again, so that backing out of Katan leaves you
        in Kodi rather than bouncing straight back in.
        """
        self.opened = True
        if not settings.get_bool("ui.start_on_boot", False):
            return
        if xbmc.getCondVisibility("Player.HasMedia"):
            return          # something is already playing; leave it alone
        kodi.log("opening Katan on start-up", kodi.LOG_INFO)
        kodi.run_builtin(
            "ActivateWindow(Videos,plugin://plugin.video.katan/,return)")

    def prune_cache(self):
        try:
            cache.maybe_prune(force=True)
        except Exception:
            kodi.log_exception("cache prune failed")

    # -- main loop ---------------------------------------------------------

    def run(self):
        kodi.log("service started, version %s" % kodi.addon_version(), kodi.LOG_INFO)
        try:
            from .player import KatanPlayer
            self.player = KatanPlayer()
        except Exception:
            kodi.log_exception("player monitor could not start")

        while not self.abortRequested():
            if self.waitForAbort(1):
                break
            if self.player is not None and self.player.isPlaying():
                self.player.tick()
            now = time.time()
            if not self.opened and now >= self.next_open:
                self.open_on_boot()
            if now >= self.next_warm:
                self.next_warm = now + WARM_INTERVAL
                self.warm_rows()
            if now >= self.next_sync:
                self.next_sync = now + SYNC_INTERVAL
                self.sync_trakt()
            if now >= self.next_prune:
                self.next_prune = now + PRUNE_INTERVAL
                self.prune_cache()

        kodi.log("service stopping", kodi.LOG_INFO)
        self.shutdown()

    def shutdown(self):
        self.player = None
        try:
            from . import http
            http.close_session()
        except Exception:
            pass
        cache.close()


def run():
    Service().run()

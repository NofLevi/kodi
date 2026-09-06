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


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        now = time.time()
        # Stagger the first runs so they never collide with each other.
        self.next_warm = now + STARTUP_DELAY
        self.next_sync = now + STARTUP_DELAY + 10
        self.next_prune = now + 300
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
            updated = catalog.warm(force=True)
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

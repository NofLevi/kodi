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

# Opening on start-up waits for Kodi to be ready rather than for a clock. The
# first version slept eight seconds because eight looked safe, and eight
# seconds of staring at Kodi's home screen is exactly the wasted step this is
# supposed to remove.
#
# What it actually has to wait for is the skin's home window being on screen;
# before that, whatever finishes loading afterwards can dismiss ours. So it
# polls for that and adds a short settle, and only falls back to a deadline if
# the home window never appears at all - a skin that starts somewhere else.
OPEN_SETTLE = 0.75        # after Kodi's home is up, before we take over
OPEN_DEADLINE = 25        # give up waiting for a home screen and just open
OPEN_TICK = 0.2           # how often to look, while we are still looking


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        now = time.time()
        # Stagger the first runs so they never collide with each other.
        self.next_warm = now + STARTUP_DELAY
        self.next_sync = now + STARTUP_DELAY + 10
        self.next_prune = now + 300
        self.open_deadline = now + OPEN_DEADLINE
        self.home_seen_at = 0.0
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

    def open_on_boot(self, now=None):
        """Go straight into Katan when Kodi starts, if the viewer asked for it.

        Off by default, and deliberately: taking over somebody's home screen
        without being asked is rude, and Kodi's own Settings -> Interface ->
        Startup deliberately offers only its own windows. But on a box that
        exists to run this add-on - a projector in a living room - stopping at
        Kodi's home screen every time is a wasted step.

        Returns True once it has decided, so the loop stops asking. Deciding
        includes deciding not to: the setting being off, or something already
        playing, are both final answers.

        Waits for the skin's home window rather than for a clock. A fixed
        eight second sleep worked and felt like the box had forgotten; what
        this actually has to wait for is Kodi being ready to be taken over.
        """
        if not settings.get_bool("ui.start_on_boot", False):
            return True
        if xbmc.getCondVisibility("Player.HasMedia"):
            return True     # something is already playing; leave it alone

        now = time.time() if now is None else now
        if xbmc.getCondVisibility("Window.IsActive(home)"):
            if not self.home_seen_at:
                self.home_seen_at = now
            # A short settle after the home screen appears, because whatever
            # Kodi finishes loading next can dismiss a window opened into the
            # middle of its start-up.
            if now - self.home_seen_at < OPEN_SETTLE:
                return False
        elif now < self.open_deadline:
            return False    # not up yet, and there is still time to wait

        kodi.log("opening Katan on start-up", kodi.LOG_INFO)
        kodi.run_builtin(
            "ActivateWindow(Videos,plugin://plugin.video.katan/,return)")
        return True

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
            # A fifth of a second only while we are still deciding whether to
            # open on start-up, because a one second tick is a second of
            # staring at Kodi's home screen. Back to one second the moment
            # that is settled, which is within the first few seconds of a
            # session and never again.
            if self.waitForAbort(OPEN_TICK if not self.opened else 1):
                break
            if self.player is not None and self.player.isPlaying():
                self.player.tick()
            now = time.time()
            if not self.opened:
                self.opened = self.open_on_boot(now)
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

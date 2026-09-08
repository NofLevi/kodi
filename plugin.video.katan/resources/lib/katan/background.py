"""The background service: one thread, mostly asleep.

It exists so that opening the add-on is a database read rather than fifteen
HTTP calls. Three jobs, all cheap:

* warm the enabled home rows, staggered so start-up is not a burst
* keep the Trakt mirror fresh, but only when last_activities says it changed
* prune the cache once a day
* look for a new release once a week, if the viewer asked it to

The loop wakes every second because the player monitor has to react quickly,
but each job has its own interval and does nothing in between.
"""
import time

import xbmc

from . import cache, catalog, kodi, settings

WARM_INTERVAL = 6 * 3600
SYNC_INTERVAL = 15 * 60
PRUNE_INTERVAL = 24 * 3600
# A projector that phones home on every boot is a projector that is
# slower to open. Once a week is enough to hear about a release.
UPDATE_INTERVAL = 7 * 24 * 3600
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

# How long Kodi's home screen has to be the thing on screen before "stay in
# Katan" treats it as having been left rather than passed through. Kodi shows
# its home for a frame or two between windows, and reopening on that would
# fight every single navigation.
LEFT_SETTLE = 1.5


class Service(xbmc.Monitor):
    def __init__(self):
        super(Service, self).__init__()
        now = time.time()
        # Stagger the first runs so they never collide with each other.
        self.next_warm = now + STARTUP_DELAY
        self.next_sync = now + STARTUP_DELAY + 10
        self.next_prune = now + 300
        self.next_update = now + STARTUP_DELAY + 40
        self.open_deadline = now + OPEN_DEADLINE
        self.home_seen_at = 0.0
        self.left_at = 0.0
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

        # RunPlugin rather than ActivateWindow: the window is not a
        # directory, and routing through the video browser leaves an
        # empty plugin folder in the back stack and a busy dialog over
        # the window while GetDirectory waits for a listing that is never
        # coming.
        kodi.log("opening Katan on start-up", kodi.LOG_INFO)
        kodi.run_builtin(
            "RunPlugin(plugin://plugin.video.katan/)")
        return True

    def check_translation_request(self):
        """Pick up an AI translation the subtitle dialog asked for.

        The dialog cannot do this itself: a plugin invocation is torn down as
        soon as it returns, and a translation takes minutes. So it leaves a
        window property and this picks it up within a second.

        On its own thread, because the loop this runs in also drives Trakt
        scrobbling and the Up Next prompt once a second, and holding it for
        the length of a feature film would stop both. A thread here is safe in
        the way one in a plugin process is not - the service lives as long as
        Kodi does.
        """
        try:
            from .subs import service as subtitles
        except Exception:
            kodi.log_exception("could not load the subtitle service")
            return
        target = subtitles.take_request()
        if not target:
            return
        import threading
        kodi.log("the subtitle dialog asked for a %s translation" % target)
        thread = threading.Thread(target=subtitles.run_translation,
                                  args=(target,))
        thread.daemon = True
        thread.start()

    def check_for_update(self):
        """Tell the viewer a release exists; never install one behind them.

        Silent when there is nothing new, because a weekly background check
        that announces "you are up to date" is a weekly interruption.
        """
        if not settings.get_bool("update.check_on_start", False):
            return
        try:
            from . import updater
            found = updater.check()
        except Exception:
            kodi.log_exception("update check failed")
            return
        if found:
            kodi.notify(kodi.localize(32501, updater.installed_version(),
                                      found[0]))

    def prune_cache(self):
        try:
            cache.maybe_prune(force=True)
        except Exception:
            kodi.log_exception("cache prune failed")

    # -- main loop ---------------------------------------------------------

    def keep_katan_open(self):
        """Bring Katan back if the viewer ends up on Kodi's own home screen.

        "Stay in Katan" only ever held the *home window's* back button, and
        that is not where the doors are. Almost everything outside the four
        custom windows is a plain Kodi directory - the settings dialog, a VOD
        folder, a search result - and from a Kodi directory two presses of
        back land on Kodi's home screen, which is the interface this add-on
        exists to replace. Someone doing that on purpose has a way home; a
        family member who pressed back twice does not, and now does not need
        one.

        Deliberately narrow. It watches for exactly one window, Kodi's home,
        and does nothing while anything is playing or a dialog is up - so
        going into settings, browsing a folder or picking a source is
        untouched. Only landing on the screen that means "you have left"
        brings it back.
        """
        if not settings.get_bool("ui.stay_in_katan"):
            return
        if xbmc.getCondVisibility("Player.HasMedia"):
            return
        if not xbmc.getCondVisibility("Window.IsActive(home)"):
            self.left_at = 0.0
            return
        # A moment's grace, because Kodi's home flickers into view between
        # windows and reopening on that would fight every navigation.
        now = time.time()
        if not self.left_at:
            self.left_at = now
            return
        if now - self.left_at < LEFT_SETTLE:
            return
        self.left_at = 0.0
        kodi.log("back on Kodi's home screen, returning to Katan",
                 kodi.LOG_INFO)
        kodi.run_builtin(
            "RunPlugin(plugin://plugin.video.katan/)")

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
            self.check_translation_request()
            now = time.time()
            if not self.opened:
                self.opened = self.open_on_boot(now)
            else:
                self.keep_katan_open()
            if now >= self.next_warm:
                self.next_warm = now + WARM_INTERVAL
                self.warm_rows()
            if now >= self.next_sync:
                self.next_sync = now + SYNC_INTERVAL
                self.sync_trakt()
            if now >= self.next_prune:
                self.next_prune = now + PRUNE_INTERVAL
                self.prune_cache()
            if now >= self.next_update:
                self.next_update = now + UPDATE_INTERVAL
                self.check_for_update()

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

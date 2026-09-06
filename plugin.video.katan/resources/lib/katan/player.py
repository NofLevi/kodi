"""Playback monitor: Trakt scrobbling, automatic subtitles and Up Next.

The player object lives in the background service, not in the plugin process,
because a plugin process ends the moment it hands a URL back to Kodi.
"""
import json
import time

import xbmc

from . import cache, kodi, settings

PLAYING_KEY = "playing"          # window property holding the current item as JSON


def set_now_playing(meta):
    """Called by play.py just before handing the stream to Kodi."""
    try:
        kodi.set_property(PLAYING_KEY, json.dumps(meta))
    except (TypeError, ValueError):
        kodi.log_exception("could not record the item being played")


def now_playing():
    raw = kodi.get_property(PLAYING_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear_now_playing():
    kodi.clear_property(PLAYING_KEY)


class KatanPlayer(xbmc.Player):
    """Reacts to playback of items this add-on started, and ignores the rest."""

    def __init__(self):
        super(KatanPlayer, self).__init__()
        self.meta = None
        self.total_time = 0.0
        self.scrobbled = False
        self.upnext_sent = False
        self.prefetched = False

    # -- helpers -----------------------------------------------------------

    def _progress(self):
        try:
            if not self.total_time:
                self.total_time = self.getTotalTime() or 0.0
            if self.total_time <= 0:
                return 0.0
            return min(100.0, (self.getTime() / self.total_time) * 100.0)
        except RuntimeError:      # nothing is playing any more
            return 0.0

    def _ids(self):
        return (self.meta or {}).get("ids") or {}

    def _is_episode(self):
        return (self.meta or {}).get("type") == "episode"

    def _scrobble(self, action, progress=None):
        if not self.meta:
            return
        try:
            from .meta import trakt
            trakt.scrobble(
                action,
                "episode" if self._is_episode() else "movie",
                self._ids(),
                self.meta.get("season"),
                self.meta.get("episode"),
                self._progress() if progress is None else progress,
            )
        except Exception:
            kodi.log_exception("scrobble %s failed" % action)

    # -- Kodi callbacks ----------------------------------------------------

    def onAVStarted(self):
        self.meta = now_playing()
        if not self.meta:
            return                # something else is playing; stay out of the way
        self.total_time = 0.0
        self.scrobbled = False
        self.upnext_sent = False
        self.prefetched = False

        self._scrobble("start", 0.0)
        self._apply_subtitles()
        self._send_upnext()

    def onPlayBackPaused(self):
        if self.meta:
            self._scrobble("pause")

    def onPlayBackResumed(self):
        if self.meta:
            self._scrobble("start")

    def onPlayBackSeek(self, seek_time, seek_offset):
        self._maybe_prefetch()

    def onPlayBackStopped(self):
        self._finish()

    def onPlayBackEnded(self):
        self._finish(completed=True)

    def onPlayBackError(self):
        clear_now_playing()
        self.meta = None

    def _finish(self, completed=False):
        if not self.meta:
            return
        progress = 100.0 if completed else self._progress()
        if not self.scrobbled:
            self.scrobbled = True
            self._scrobble("stop", progress)
        self._remember_source(progress)
        clear_now_playing()
        self.meta = None

    def tick(self):
        """Called once a second by the service while something is playing."""
        if not self.meta:
            return
        self._maybe_prefetch()

    # -- features ----------------------------------------------------------

    def _apply_subtitles(self):
        """Hand off to the subtitle pipeline, which picks at most one file."""
        if not settings.get_bool("subs.auto"):
            return
        try:
            from .subs import auto
            auto.on_playback_started(self, self.meta)
        except ImportError:
            pass
        except Exception:
            kodi.log_exception("automatic subtitles failed")

    def _send_upnext(self):
        """Tell the Up Next add-on what plays after this episode."""
        if self.upnext_sent or not self._is_episode():
            return
        if not settings.get_bool("ui.upnext"):
            return
        self.upnext_sent = True
        try:
            from .upnext import notify_upnext
            notify_upnext(self.meta)
        except ImportError:
            pass
        except Exception:
            kodi.log_exception("Up Next handoff failed")

    def _maybe_prefetch(self):
        """At 80% of an episode, look up the next one's best source only.

        One HTTP call, no debrid transfer is created, so the cost is tiny and
        the next episode starts without a visible search.
        """
        if self.prefetched or not self._is_episode():
            return
        if not settings.get_bool("sources.prefetch_next", True):
            return
        if self._progress() < 80:
            return
        self.prefetched = True
        try:
            from . import play
            play.prefetch_next_episode(self.meta)
        except Exception:
            kodi.log_exception("next-episode prefetch failed")

    def _remember_source(self, progress):
        """Record the release group that played well, to bias future picks."""
        if progress < 20 or not settings.get_bool("sources.source_memory"):
            return
        source = (self.meta or {}).get("source") or {}
        group = source.get("group")
        show_id = self._ids().get("tmdb")
        if not group or not show_id:
            return
        cache.set(cache.make_key("srcmem", show_id), {
            "group": group,
            "provider": source.get("provider", ""),
            "quality": source.get("quality", ""),
            "at": int(time.time()),
        }, 90 * 24 * 3600)

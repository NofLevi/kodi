"""Playback monitor: Trakt scrobbling, automatic subtitles and Up Next.

The player object lives in the background service, not in the plugin process,
because a plugin process ends the moment it hands a URL back to Kodi.
"""
import hashlib
import json
import threading
import time

import xbmc

from . import cache, kodi, settings

PLAYING_KEY = "playing"          # window property holding the current item as JSON
PLAYBACK_LOCK = threading.RLock()
HANDOFF_TTL = 60.0


def _stream_id(url):
    if not isinstance(url, str) or not url:
        return ""
    return hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()


def set_now_playing(meta):
    """Publish an opaque, short-lived handoff for the service process."""
    try:
        payload = dict(meta or {})
        payload["_stream_id"] = _stream_id(payload.pop("stream_url", ""))
        payload["_handoff_time"] = time.time()
        with PLAYBACK_LOCK:
            kodi.set_property(PLAYING_KEY, json.dumps(payload))
    except (TypeError, ValueError):
        kodi.log_exception("could not record the item being played")


def now_playing():
    with PLAYBACK_LOCK:
        raw = kodi.get_property(PLAYING_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def clear_now_playing():
    with PLAYBACK_LOCK:
        kodi.clear_property(PLAYING_KEY)


class _PlaybackPlayer(object):
    """Player facade that ignores writes from an obsolete subtitle worker."""

    def __init__(self, owner, generation, meta):
        self.owner = owner
        self.generation = generation
        self.meta = meta

    def current(self):
        with PLAYBACK_LOCK:
            return (self.owner._subtitle_generation == self.generation
                    and self.owner.meta is self.meta)

    def setSubtitleStream(self, index):
        with PLAYBACK_LOCK:
            if (self.owner._subtitle_generation == self.generation
                    and self.owner.meta is self.meta):
                self.owner.setSubtitleStream(index)

    def setSubtitles(self, path):
        with PLAYBACK_LOCK:
            if (self.owner._subtitle_generation == self.generation
                    and self.owner.meta is self.meta):
                self.owner.setSubtitles(path)

    def showSubtitles(self, visible):
        with PLAYBACK_LOCK:
            if (self.owner._subtitle_generation == self.generation
                    and self.owner.meta is self.meta):
                self.owner.showSubtitles(visible)

    def __getattr__(self, name):
        return getattr(self.owner, name)


class KatanPlayer(xbmc.Player):
    """Reacts to playback of items this add-on started, and ignores the rest."""

    def __init__(self):
        super(KatanPlayer, self).__init__()
        self.meta = None
        self.total_time = 0.0
        self.scrobbled = False
        self.upnext_sent = False
        self.prefetched = False
        self._subtitle_generation = 0
        self._playback_generation = 0
        self._subtitle_thread = None
        self._subtitle_pending = None
        self._subtitle_lock = threading.Lock()

    def shutdown(self):
        """Invalidate playback-owned background work before service teardown."""
        with PLAYBACK_LOCK:
            self._subtitle_generation += 1
            self._playback_generation += 1
            self.meta = None
            clear_now_playing()
        with self._subtitle_lock:
            self._subtitle_pending = None
            thread = self._subtitle_thread
        if (thread is not None and thread is not threading.current_thread()
                and getattr(thread, "is_alive", lambda: False)()):
            thread.join(0.5)

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

    def _scrobble(self, action, progress=None, meta=None):
        selected = meta if meta is not None else self.meta
        if not selected:
            return
        try:
            from .meta import trakt
            ids = selected.get("ids") or {}
            item_type = selected.get("type")
            trakt.scrobble(
                action,
                "episode" if item_type == "episode" else "movie",
                ids,
                selected.get("season"),
                selected.get("episode"),
                self._progress() if progress is None else progress,
            )
        except Exception:
            kodi.log_exception("scrobble %s failed" % action)

    # -- Kodi callbacks ----------------------------------------------------

    def onAVStarted(self):
        actual_url = self.getPlayingFile() or ""
        with PLAYBACK_LOCK:
            self._subtitle_generation += 1
            self._playback_generation += 1
            pending = now_playing()
            valid = bool(
                pending
                and pending.get("_stream_id")
                and pending.get("_stream_id") == _stream_id(actual_url)
                and time.time() - float(pending.get("_handoff_time") or 0) <= HANDOFF_TTL
            )
            if not valid:
                clear_now_playing()
                self.meta = None
            else:
                assert pending is not None
                pending.pop("_stream_id", None)
                pending.pop("_handoff_time", None)
                pending["stream_url"] = actual_url
                self.meta = pending
        if not self.meta:
            return                # something else is playing; stay out of the way
        self.total_time = 0.0
        self.scrobbled = False
        self.upnext_sent = False
        self.prefetched = False

        self._scrobble("start")
        self._apply_subtitles()
        self._announce_audio_tracks()
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
        with PLAYBACK_LOCK:
            self._subtitle_generation += 1
            self._playback_generation += 1
            clear_now_playing()
            self.meta = None

    def _finish(self, completed=False):
        with PLAYBACK_LOCK:
            self._subtitle_generation += 1
            meta = self.meta
            generation = self._playback_generation
            if not meta:
                return
            should_scrobble = not self.scrobbled
            if should_scrobble:
                self.scrobbled = True
        progress = 100.0 if completed else self._progress()
        if should_scrobble:
            self._scrobble("stop", progress, meta=meta)
        self._remember_source(progress, meta=meta)
        with PLAYBACK_LOCK:
            if self._playback_generation != generation or self.meta is not meta:
                return
            clear_now_playing()
            self.meta = None

    def tick(self):
        """Called once a second by the service while something is playing."""
        if not self.meta:
            return
        self._maybe_prefetch()

    # -- features ----------------------------------------------------------

    def _apply_subtitles(self):
        """Schedule subtitle search/translation away from Kodi's callback."""
        if not settings.get_bool("subs.auto") or not self.meta:
            return
        generation = self._subtitle_generation
        meta = self.meta
        with self._subtitle_lock:
            # One worker per player. Rapid source changes replace the queued job
            # rather than accumulating threads, provider pools and sockets.
            self._subtitle_pending = (generation, meta)
            active = (self._subtitle_thread is not None
                      and getattr(self._subtitle_thread, "is_alive",
                                  lambda: False)())
            if active:
                return
            thread = threading.Thread(target=self._subtitle_worker,
                                      name="katan-subtitles")
            thread.daemon = True
            self._subtitle_thread = thread
            thread.start()

    def _subtitle_worker(self):
        while True:
            with self._subtitle_lock:
                job = self._subtitle_pending
                self._subtitle_pending = None
                if job is None:
                    self._subtitle_thread = None
                    return
            self._run_subtitle_job(*job)

    def _run_subtitle_job(self, generation, meta):
        proxy = _PlaybackPlayer(self, generation, meta)
        try:
            from .subs import auto
            auto.on_playback_started(proxy, meta,
                                     cancelled=lambda: not proxy.current())
        except ImportError:
            pass
        except Exception:
            kodi.log_exception("automatic subtitles failed")

    def _announce_audio_tracks(self):
        """Say once when a file offers more than one audio language.

        Nothing here chooses a track, and that is deliberate. Kodi already has
        a perfectly good switcher in its own on-screen display; what it cannot
        do is tell somebody there is anything to switch. A dual-audio anime
        release plays whatever Kodi's default resolves to - often Japanese,
        sometimes English - and a viewer with a remote has no reason to go
        looking through an OSD menu for an alternative they do not know exists.

        Only when there are two or more *languages*: a file with a stereo and
        a 5.1 English track has two streams and one choice, and announcing that
        would be noise on every film.
        """
        try:
            from .subs import embedded
            names = embedded.audio_languages()
        except ImportError:
            return
        except Exception:
            kodi.log_exception("could not read the audio tracks")
            return
        if len(names) < 2:
            return
        kodi.log("audio tracks: %s" % ", ".join(names))
        kodi.notify(kodi.localize(32521, ", ".join(names)))

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

    def _remember_source(self, progress, meta=None):
        """Record the release group that played well, to bias future picks."""
        if progress < 20 or not settings.get_bool("sources.source_memory"):
            return
        selected = meta if meta is not None else self.meta
        source = (selected or {}).get("source") or {}
        group = source.get("group")
        show_id = ((selected or {}).get("ids") or {}).get("tmdb")
        if not group or not show_id:
            return
        cache.set(cache.make_key("srcmem", show_id), {
            "group": group,
            "provider": source.get("provider", ""),
            "quality": source.get("quality", ""),
            "at": int(time.time()),
        }, 90 * 24 * 3600)

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
        # What is drawn over the video, and what decides whether it is.
        self._segments = {}
        self._intro_skipped = False
        self._skip_dismissed = False
        self._skip_button = None
        self._next = None
        self._next_checked = False
        self._card_dismissed = False
        self._next_card = None

    def shutdown(self):
        """Invalidate playback-owned background work before service teardown."""
        with PLAYBACK_LOCK:
            self._subtitle_generation += 1
            self._playback_generation += 1
            self.meta = None
            clear_now_playing()
        self._close_skip_button()
        self._close_next_card()
        with self._subtitle_lock:
            self._subtitle_pending = None
            thread = self._subtitle_thread
        if (thread is not None and thread is not threading.current_thread()
                and getattr(thread, "is_alive", lambda: False)()):
            thread.join(0.5)

    # -- helpers -----------------------------------------------------------

    def _position_now(self):
        """Seconds into the file: the player's answer, or the last one heard.

        By the time onPlayBackStopped arrives Kodi has usually stopped, and
        getTime raises then - which made every stop look like it happened at
        0%. The tick's last reading is what a stop knows about where it was.
        """
        try:
            position = float(self.getTime() or 0.0)
        except RuntimeError:      # nothing is playing any more
            return getattr(self, "_position", 0.0)
        self._position = position
        return position

    def _progress(self):
        try:
            if not self.total_time:
                self.total_time = self.getTotalTime() or 0.0
        except RuntimeError:      # nothing is playing any more
            pass
        if self.total_time <= 0:
            return 0.0
        return min(100.0, (self._position_now() / self.total_time) * 100.0)

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
        self._close_skip_button()           # one left over from the last file
        self._close_next_card()
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
        self._segments = {}
        self._intro_skipped = False
        self._skip_dismissed = False
        self._next = None
        self._next_checked = False
        self._card_dismissed = False

        self._scrobble("start")
        self._apply_subtitles()
        self._announce_audio_tracks()
        self._send_upnext()
        self._find_segments()

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
        # A card that was waiting for the end: this is the end.
        url = self._next_on_end()
        self._finish(completed=True)
        if url:
            self.play(url)

    # How many times one playback may fall back after Kodi fails to open a
    # stream, however many candidates the hand-off still carries.
    MAX_RETRIES = 2

    def onPlayBackError(self):
        """Kodi could not play what it was given; try the next source.

        The fall-through to the next source used to run only before the
        hand-off, when a link would not resolve or answer. Once Kodi had the
        URL, a stream its decoder could not open ended on an error screen -
        while the next source in the list would have played.
        """
        with PLAYBACK_LOCK:
            pending = now_playing()
            never_started = self.meta is None
            self._subtitle_generation += 1
            self._playback_generation += 1
            generation = self._playback_generation
            clear_now_playing()
            self.meta = None
            self._forget_position()
        self._close_skip_button()
        self._close_next_card()
        if never_started and self._worth_retrying(pending):
            # Resolving takes seconds and this is Kodi's callback thread, which
            # the service loop also waits on - so a daemon worker, as the
            # subtitle search uses.
            worker = threading.Thread(target=self._retry_next,
                                      args=(pending, generation),
                                      name="katan-retry")
            worker.daemon = True
            worker.start()

    def _worth_retrying(self, pending):
        """A fresh hand-off from our own autoplay, with somewhere left to go.

        Only when Kodi never started the stream. An error after it started is
        a different failure - a stream dying mid-film - with its own
        questions, and a stale hand-off is not ours to act on.
        """
        if not pending or not pending.get("fallbacks"):
            return False
        try:
            age = time.time() - float(pending.get("_handoff_time") or 0)
        except (TypeError, ValueError):
            return False
        if age > HANDOFF_TTL:
            return False
        return int(pending.get("_retry") or 0) < self.MAX_RETRIES

    def _retry_next(self, pending, generation):
        """Open the next fallback and give it to Kodi, or say none worked."""
        from . import play
        from .ui import listing
        try:
            candidate, url, remaining = play.resolve_fallback(pending)
        except Exception:
            kodi.log_exception("falling back to the next source failed")
            candidate, url, remaining = None, "", []
        with PLAYBACK_LOCK:
            if self._playback_generation != generation:
                return          # something else started while we resolved
        if not url:
            kodi.notify(kodi.localize(32284))
            return
        retried = dict(pending)
        retried.pop("_stream_id", None)
        retried.pop("_handoff_time", None)
        retried["source"] = play.source_record(candidate)
        retried["fallbacks"] = remaining
        retried["_retry"] = int(pending.get("_retry") or 0) + 1
        retried["stream_url"] = url
        kodi.notify(kodi.localize(32528))
        set_now_playing(retried)
        listing.resolve(-1, url, retried.get("item"))

    def _finish(self, completed=False):
        self._close_skip_button()
        self._close_next_card()
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
        self._keep_place(meta, progress, completed)
        # The next playback starts from nothing. A reading left over from this
        # one would be taken as where the next one stopped, were it stopped
        # before its first tick - film B saved at film A's position.
        self._forget_position()
        with PLAYBACK_LOCK:
            if self._playback_generation != generation or self.meta is not meta:
                return
            clear_now_playing()
            self.meta = None

    def _forget_position(self):
        self._position = 0.0
        self._bookmarked_at = 0.0

    # How often the place is saved while playing, on top of the save at stop:
    # a projector is as likely to be switched off at the wall as stopped from
    # the menu, and then no stop ever arrives.
    BOOKMARK_EVERY = 120

    def tick(self):
        """Called once a second by the service while something is playing."""
        if not self.meta:
            return
        self._maybe_prefetch()
        progress = self._progress()          # and records where we are
        self._maybe_skip_intro()
        self._offer_skip_button()
        self._offer_next_card(progress)
        now = time.time()
        if now - getattr(self, "_bookmarked_at", 0.0) >= self.BOOKMARK_EVERY:
            self._bookmarked_at = now
            if 1 < progress < 95:
                self._keep_place(self.meta, progress)

    def _keep_place(self, meta, progress, completed=False):
        """Save or clear where this was stopped, for resuming without Trakt.

        The same thresholds Trakt uses: under 1% was a false start and saves
        nothing, 95% or more is the credits and counts as finished.
        """
        if not meta or meta.get("type") not in ("movie", "episode"):
            return
        from . import bookmarks
        from .meta import trakt_state
        key = trakt_state.state_key(meta.get("type"), meta.get("ids"),
                                    meta.get("season"), meta.get("episode"))
        try:
            if completed or progress >= 95:
                bookmarks.clear(key)
            elif progress > 1 and self.total_time > 0:
                bookmarks.save(key, self.total_time * progress / 100.0,
                               self.total_time)
        except Exception:
            kodi.log_exception("could not keep the resume point")

    # -- features ----------------------------------------------------------

    def _find_segments(self):
        """Look up where this episode's intro and credits are, off the
        callback thread - it is a network call."""
        if not settings.get_bool("ui.skip_segments") or not self._is_episode():
            return
        worker = threading.Thread(target=self._segments_job,
                                  args=(self.meta, self._playback_generation),
                                  name="katan-skip")
        worker.daemon = True
        worker.start()

    def _segments_job(self, meta, generation):
        from . import skip
        try:
            found = skip.segments(meta)
        except Exception:
            kodi.log_exception("could not look up the intro and credits")
            return
        with PLAYBACK_LOCK:
            if self._playback_generation == generation and self.meta is meta:
                self._segments = found

    def _intro_now(self):
        """The intro, when playback is inside it with something left to skip.

        Not in its last seconds: a jump that saves two seconds is a stutter.
        """
        found = getattr(self, "_segments", None)
        if not found:
            return None
        from . import skip
        intro = skip.usable(found, self.total_time).get("intro")
        if intro and intro[0] <= getattr(self, "_position", 0.0) < intro[1] - 2:
            return intro
        return None

    def _maybe_skip_intro(self):
        """Jump past the intro, once, when the viewer asked for that.

        Once per playback: somebody who seeks back into the intro wants to
        see it, and being thrown forward again would be a fight.
        """
        if getattr(self, "_intro_skipped", False):
            return
        if not settings.get_bool("ui.auto_skip"):
            return
        intro = self._intro_now()
        if intro and self._skip_to(intro[1]):
            kodi.notify(kodi.localize(32529))

    def _offer_skip_button(self):
        """A Skip intro button for as long as the intro plays.

        Not when skipping is automatic, and not again once it has been used
        or put away - the same once-per-episode rule as skipping itself.
        """
        intro = None
        if not (settings.get_bool("ui.auto_skip")
                or getattr(self, "_intro_skipped", False)
                or getattr(self, "_skip_dismissed", False)):
            intro = self._intro_now()
        button = getattr(self, "_skip_button", None)
        if intro and button is None:
            from .ui import skip_window
            end = intro[1]
            self._skip_button = skip_window.open_button(
                on_skip=lambda: self._skip_to(end),
                on_dismiss=self._dismiss_skip)
        elif not intro and button is not None:
            self._close_skip_button()

    def _skip_to(self, end):
        self._intro_skipped = True
        self._close_skip_button()
        try:
            self.seekTime(end)
        except RuntimeError:
            return False
        return True

    def _dismiss_skip(self):
        self._skip_dismissed = True
        self._close_skip_button()

    def _close_skip_button(self):
        button = getattr(self, "_skip_button", None)
        self._skip_button = None
        if button is not None:
            try:
                button.close()
            except Exception:
                pass

    # How long the card counts down before the next episode starts by itself.
    NEXT_COUNTDOWN = 10
    # Without credits timing the card goes up this close to the end, and the
    # next episode starts only when this one ends by itself: without knowing
    # where the credits are, a countdown could cut off the last scene.
    NEXT_WITHOUT_CREDITS = 20

    def _offer_next_card(self, progress):
        """Katan's next-episode card, when Up Next is not here to draw one.

        Up at the start of the credits with a countdown, or near the end
        with none when the credits are not known. Once put away - or once it
        has started the next episode - it stays away for this one.
        """
        if (progress < 50 or not self._is_episode()
                or getattr(self, "_card_dismissed", False)):
            return
        if not self._next_episode():
            return
        wanted = self._next_card_mode()
        card = getattr(self, "_next_card", None)
        if card is None and wanted:
            from .ui import nextup_window
            self._next_card = nextup_window.open_card(
                self._next,
                self.NEXT_COUNTDOWN if wanted == "countdown" else 0,
                on_play=self._play_next, on_dismiss=self._dismiss_next)
        elif card is not None and not wanted:
            self._close_next_card()         # the viewer went back before it
        elif card is not None and card.autoplay:
            left = self.NEXT_COUNTDOWN - (time.time() - card.opened_at)
            if left <= 0:
                self._play_next()
            else:
                card.set_countdown(int(left + 0.999))

    def _next_card_mode(self):
        """"countdown" in the credits, "wait" near an end with no credits
        known, or None when the card has no business being up."""
        from . import skip
        position = getattr(self, "_position", 0.0)
        credits = skip.usable(getattr(self, "_segments", None),
                              self.total_time).get("credits")
        if credits:
            end = credits[1] or self.total_time
            # After the credits is a scene somebody stayed for.
            return "countdown" if credits[0] <= position < end else None
        if self.total_time and 0 < self.total_time - position <= self.NEXT_WITHOUT_CREDITS:
            return "wait"
        return None

    def _next_episode(self):
        """The episode after this one, looked up once per playback.

        Not with Up Next installed: it draws its own card, and two would
        both start the next episode.
        """
        if not getattr(self, "_next_checked", False):
            self._next_checked = True
            self._next = None
            from . import upnext
            if settings.get_bool("ui.upnext") and not upnext.installed():
                try:
                    self._next = upnext.next_episode(self.meta)
                except Exception:
                    kodi.log_exception("could not find the next episode")
        return getattr(self, "_next", None)

    def _next_url(self):
        nxt = getattr(self, "_next", None)
        if not nxt:
            return ""
        from . import router
        return router.url_for("episode", tmdb=(nxt.get("ids") or {}).get("tmdb"),
                              season=nxt["season"], episode=nxt["episode"])

    def _play_next(self):
        url = self._next_url()
        self._card_dismissed = True
        self._close_next_card()
        if not url:
            return
        # Finished here rather than when Kodi gets round to stopping it: this
        # one counts as watched, and its resume point goes.
        self._finish(completed=True)
        self.play(url)

    def _dismiss_next(self):
        self._card_dismissed = True
        self._close_next_card()

    def _next_on_end(self):
        """The next episode's address, when its card was up as this ended."""
        if getattr(self, "_next_card", None) is None:
            return ""
        if getattr(self, "_card_dismissed", False):
            return ""
        return self._next_url()

    def _close_next_card(self):
        card = getattr(self, "_next_card", None)
        self._next_card = None
        if card is not None:
            try:
                card.close()
            except Exception:
                pass

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
        # No fallback of its own. It said True while settings.xml and
        # settings.DEFAULTS both say false, and the difference only shows when
        # the value is unset - which is exactly when a default is consulted.
        if not settings.get_bool("sources.prefetch_next"):
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

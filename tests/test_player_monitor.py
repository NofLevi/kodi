"""Playback-bound subtitle work must never block or leak into the next film."""


def test_old_finish_cannot_clear_a_new_playback(monkeypatch):
    import threading
    from katan import player
    old = {"type": "movie", "title": "old", "ids": {"tmdb": 1}}
    new_url = "https://cdn.invalid/new"
    new = {"type": "movie", "title": "new", "ids": {"tmdb": 2},
           "stream_url": new_url}
    monitor = player.KatanPlayer()
    monitor.meta = old
    entered, release = threading.Event(), threading.Event()

    def scrobble(action, progress=None, **_kwargs):
        if action == "stop":
            entered.set()
            release.wait(2)

    monkeypatch.setattr(monitor, "_scrobble", scrobble)
    monkeypatch.setattr(monitor, "_progress", lambda: 50.0)
    monkeypatch.setattr(monitor, "_remember_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor, "getPlayingFile", lambda: new_url)
    monkeypatch.setattr(monitor, "_apply_subtitles", lambda: None)
    monkeypatch.setattr(monitor, "_announce_audio_tracks", lambda: None)
    monkeypatch.setattr(monitor, "_send_upnext", lambda: None)
    worker = threading.Thread(target=monitor.onPlayBackStopped)
    worker.start()
    assert entered.wait(1)
    player.set_now_playing(new)
    monitor.onAVStarted()
    release.set()
    worker.join(1)
    assert monitor.meta is not None and monitor.meta.get("title") == "new"
    assert player.now_playing() is not None


def test_resumed_playback_scrobbles_measured_progress(monkeypatch):
    from katan import player
    from katan.meta import trakt
    url = "https://cdn.invalid/video"
    player.set_now_playing({"type": "movie", "ids": {"tmdb": 1},
                            "stream_url": url})
    monitor = player.KatanPlayer()
    monkeypatch.setattr(monitor, "getPlayingFile", lambda: url)
    monkeypatch.setattr(monitor, "getTime", lambda: 3600.0)
    monkeypatch.setattr(monitor, "getTotalTime", lambda: 7200.0)
    seen = []
    monkeypatch.setattr(trakt, "scrobble",
                        lambda action, kind, ids, season, episode, progress:
                        seen.append((action, progress)))
    monkeypatch.setattr(monitor, "_apply_subtitles", lambda: None)
    monkeypatch.setattr(monitor, "_announce_audio_tracks", lambda: None)
    monkeypatch.setattr(monitor, "_send_upnext", lambda: None)
    monitor.onAVStarted()
    assert seen == [("start", 50.0)]


def test_unrelated_playback_cannot_consume_a_stale_katan_handoff(monkeypatch):
    from katan import player
    stale = {"type": "movie", "title": "never-started", "ids": {"tmdb": 1},
             "stream_url": "https://cdn.invalid/private/SYNTH_PATH?sig=SYNTH_SIG"}
    player.set_now_playing(stale)
    assert "SYNTH_PATH" not in player.kodi.get_property(player.PLAYING_KEY)
    monitor = player.KatanPlayer()
    monkeypatch.setattr(monitor, "getPlayingFile",
                        lambda: "https://other.invalid/video")
    monkeypatch.setattr(monitor, "_scrobble", lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor, "_apply_subtitles", lambda: None)
    monkeypatch.setattr(monitor, "_announce_audio_tracks", lambda: None)
    monkeypatch.setattr(monitor, "_send_upnext", lambda: None)
    monitor.onAVStarted()
    assert monitor.meta is None
    assert player.now_playing() is None


def test_automatic_subtitles_are_scheduled_off_the_kodi_callback(
        monkeypatch, settings_module):
    import threading
    from katan import player
    from katan.subs import auto

    settings_module.set("subs.auto", "true")
    monitor = player.KatanPlayer()
    monitor.meta = {"type": "movie", "ids": {"tmdb": 1}}
    called = []
    queued = []

    monkeypatch.setattr(auto, "on_playback_started",
                        lambda *args, **kwargs: called.append((args, kwargs)))

    class Thread(object):
        daemon = False

        def __init__(self, target=None, **kwargs):
            self.target = target

        def start(self):
            queued.append(self.target)

    monkeypatch.setattr(threading, "Thread", Thread)
    monitor._apply_subtitles()
    assert called == [], "onAVStarted would still block on subtitle translation"
    assert len(queued) == 1
    queued[0]()
    assert len(called) == 1


def test_rapid_playback_changes_keep_one_subtitle_worker(
        monkeypatch, settings_module):
    import threading
    from katan import player
    from katan.subs import auto

    settings_module.set("subs.auto", "true")
    monitor = player.KatanPlayer()
    queued = []
    called = []

    class Thread(object):
        daemon = False

        def __init__(self, target=None, **kwargs):
            self.target = target
            self.started = False

        def start(self):
            self.started = True
            queued.append(self)

        def is_alive(self):
            return self.started

    monkeypatch.setattr(threading, "Thread", Thread)
    monkeypatch.setattr(auto, "on_playback_started",
                        lambda _p, meta, **kw: called.append(meta["id"]))
    monitor.meta = {"id": 1}
    monitor._subtitle_generation = 1
    monitor._apply_subtitles()
    monitor.meta = {"id": 2}
    monitor._subtitle_generation = 2
    monitor._apply_subtitles()

    assert len(queued) == 1
    queued[0].target()
    assert called == [2]


def test_old_subtitle_worker_cannot_select_embedded_track_after_enumeration(
        monkeypatch, settings_module):
    from katan import player
    from katan.subs import auto, embedded

    monitor = player.KatanPlayer()
    meta = {"type": "movie", "ids": {"tmdb": 1}}
    monitor.meta = meta
    monitor._subtitle_generation = 1
    selected = []
    monkeypatch.setattr(monitor, "setSubtitleStream",
                        lambda index: selected.append(index))
    proxy = player._PlaybackPlayer(monitor, 1, meta)

    def candidates(languages):
        monitor._subtitle_generation = 2
        return [{"stream_index": 4, "partial": False}]

    monkeypatch.setattr(embedded, "candidates", candidates)
    assert auto.use_embedded(proxy, "he",
                             cancelled=lambda: not proxy.current()) is False
    assert selected == []


def test_playback_invalidation_waits_for_atomic_subtitle_write(monkeypatch):
    import threading
    from katan import player

    monitor = player.KatanPlayer()
    meta = {"type": "movie", "ids": {"tmdb": 1}}
    monitor.meta = meta
    monitor._subtitle_generation = 1
    entered = threading.Event()
    release = threading.Event()
    invalidation_started = threading.Event()
    invalidation_done = threading.Event()
    shown = []

    def blocked_write(path):
        entered.set()
        release.wait(2.0)
        shown.append(path)

    monkeypatch.setattr(monitor, "setSubtitles", blocked_write)
    proxy = player._PlaybackPlayer(monitor, 1, meta)
    writer = threading.Thread(target=lambda: proxy.setSubtitles("owned.srt"))

    def invalidate():
        invalidation_started.set()
        monitor.onPlayBackError()
        invalidation_done.set()

    invalidator = threading.Thread(target=invalidate)
    writer.start()
    assert entered.wait(1.0)
    invalidator.start()
    assert invalidation_started.wait(1.0)
    assert not invalidation_done.wait(0.05), \
        "playback changed in the middle of an owned subtitle mutation"
    release.set()
    writer.join(1.0)
    invalidator.join(1.0)

    assert shown == ["owned.srt"]
    assert invalidation_done.is_set()
    assert monitor.meta is None


def test_old_subtitle_worker_cannot_touch_a_new_playback(
        monkeypatch, settings_module):
    from katan import player
    from katan.subs import auto

    settings_module.set("subs.auto", "true")
    monitor = player.KatanPlayer()
    old_meta = {"type": "movie", "ids": {"tmdb": 1}}
    monitor.meta = old_meta
    monitor._subtitle_generation = 1
    shown = []
    monkeypatch.setattr(monitor, "setSubtitles", lambda path: shown.append(path))

    def stale_attempt(proxy, meta, cancelled=None):
        monitor.meta = {"type": "movie", "ids": {"tmdb": 2}}
        monitor._subtitle_generation += 1
        assert cancelled()
        proxy.setSubtitles("old-film.srt")

    monkeypatch.setattr(auto, "on_playback_started", stale_attempt)
    monitor._run_subtitle_job(1, old_meta)
    assert shown == []

"""From "the user pressed OK" to "Kodi has a URL".

This module decides what every viewer actually sees and had no tests at all.
The autoplay decision in particular is the difference between a film starting
by itself and a list of eight releases appearing.
"""
import pytest

from pinky import kodi, play
from pinky.debrid import registry
from pinky.meta import tmdb


def source(title, info_hash, cached_by="torbox", **extra):
    entry = {"title": title, "hash": info_hash, "provider": "torrentio",
             "quality": "1080p", "size": 8 * 1024 ** 3, "seeders": 40,
             "cached": bool(cached_by), "cached_by": cached_by,
             "group": "GRP", "extra": {"meta": {"type": "movie"}}}
    entry.update(extra)
    return entry


SOURCES = [source("Best 1080p", "a" * 40), source("Worse 720p", "b" * 40)]


@pytest.fixture
def film(monkeypatch, settings_module):
    """A configured add-on with a film TMDB knows about."""
    settings_module.set("torbox.apikey", "a-key")
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": 278, "imdb": "tt0111161"},
        "title": "The Shawshank Redemption", "year": 1994,
        "art": {}, "original_title": "The Shawshank Redemption"})
    # Nothing plays without the picker any more, so a test that is about
    # what happens after the choice stands in for the viewer taking the top
    # row. Tests about the picker itself patch over this.
    import pinky.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source",
                        lambda sources, meta: sources[0] if sources else None)
    return settings_module


# --------------------------------------------------------------------------
# build_meta
# --------------------------------------------------------------------------


def test_a_film_gets_its_title_and_ids(film):
    meta = play.build_meta({"type": "movie", "tmdb": "278"})
    assert meta["title"] == "The Shawshank Redemption"
    assert meta["ids"]["imdb"] == "tt0111161"
    assert meta["year"] == 1994


def test_a_series_keeps_its_own_language_on_every_episode(monkeypatch,
                                                          settings_module):
    """meta["item"] is the episode, and TMDB episodes carry no language - so
    reading it from there left every series blank: a Turkish drama's Turkish
    releases were ranked as foreign, and its language was never searched to
    translate a subtitle from. Measured: Hikaru no Go and Game of Thrones
    both reached the source ranking as ''."""
    monkeypatch.setattr(tmdb, "show", lambda tmdb_id: {
        "ids": {"tmdb": 1}, "title": "Magnificent Century", "year": 2011,
        "art": {}, "original_title": "Muhtesem Yuzyil",
        "original_language": "tr"})
    monkeypatch.setattr(tmdb, "episodes", lambda tmdb_id, season: [
        {"episode": 3, "title": "Episode 3", "art": {}, "original_language": ""}])
    meta = play.build_meta({"type": "episode", "tmdb": 1, "season": 1,
                            "episode": 3})
    assert meta["original_language"] == "tr"


def test_a_film_carries_its_language_where_it_is_read(monkeypatch, settings_module):
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": 496243}, "title": "Parasite", "year": 2019,
        "art": {}, "original_title": "Gisaengchung",
        "original_language": "ko"})
    assert play.build_meta({"type": "movie", "tmdb": 496243})[
        "original_language"] == "ko"


def test_an_imdb_id_from_the_route_fills_a_gap(monkeypatch, settings_module):
    """The route carries one; TMDB does not always."""
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": 1}, "title": "A Film", "year": 2020, "art": {}})
    meta = play.build_meta({"type": "movie", "tmdb": "1", "imdb": "tt999"})
    assert meta["ids"]["imdb"] == "tt999"


def test_no_tmdb_answer_means_no_title(monkeypatch, settings_module):
    """This is the gate that stops playback without a TMDB key."""
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: None)
    assert play.build_meta({"type": "movie", "tmdb": "278"})["title"] == ""


# --------------------------------------------------------------------------
# choosing a source
# --------------------------------------------------------------------------


def test_the_picker_is_always_opened(film, monkeypatch):
    """There is no autoplay any more, and no setting that brings it back.

    The ranking cannot see what the picker shows: its three lists compare
    the best Hebrew subtitle against the best one AI can translate from,
    and for most anime the first list is empty - which is a question about
    subtitles that the source sort never asked.
    """
    import pinky.ui.sources_window as window
    asked = []
    monkeypatch.setattr(window, "pick_source",
                        lambda sources, meta: asked.append(sources) or sources[0])
    assert play._choose(SOURCES, {}, force_picker=False) is SOURCES[0]
    assert asked, "nothing may start without the picker"


def test_no_setting_can_turn_the_picker_off():
    from pinky import settings
    assert "sources.autoplay" not in settings.DEFAULTS


def test_the_context_menu_forces_the_picker(film, monkeypatch, settings_module):
    """"Choose a source" must never quietly autoplay instead."""
    asked = []
    import pinky.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source",
                        lambda sources, meta: asked.append(sources) or sources[1])

    chosen = play._choose(SOURCES, {}, force_picker=True)
    assert asked, "the picker should have been opened"
    assert chosen is SOURCES[1]


def test_the_picker_choice_is_what_plays(film, monkeypatch, settings_module):
    import pinky.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source", lambda sources, meta: sources[1])
    assert play._choose(SOURCES, {}, force_picker=False) is SOURCES[1]


def test_a_cancelled_picker_plays_nothing(film, monkeypatch, settings_module):
    import pinky.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source", lambda sources, meta: None)
    assert play._choose(SOURCES, {}, force_picker=False) is None


def test_custom_window_play_carries_the_resume_list_item(monkeypatch):
    """Plugin URLs launched outside a directory must retain StartOffset."""
    import xbmc
    from pinky.ui import listing

    marker = object()
    meta = {"type": "movie", "title": "A Film", "ids": {}, "art": {}}
    del xbmc.Player.PLAYED[:]
    monkeypatch.setattr(listing, "make_list_item", lambda item: marker)

    kodi.play_media("plugin://plugin.video.pinky/play", meta)

    assert xbmc.Player.PLAYED == [
        (("plugin://plugin.video.pinky/play", marker), {})]


# --------------------------------------------------------------------------
# resolving to a stream
# --------------------------------------------------------------------------


class FakeClient(object):
    name = "torbox"
    label = "TorBox"

    def __init__(self, url="https://cdn/x.mkv", configured=True, boom=False):
        self.url = url
        self._configured = configured
        self.boom = boom
        self.calls = []

    def configured(self):
        return self._configured

    def resolve(self, source):
        self.calls.append(source)
        if self.boom:
            raise RuntimeError("service exploded")
        return self.url


def test_the_service_that_cached_it_is_the_one_asked(monkeypatch, film):
    client = FakeClient()
    monkeypatch.setattr(registry, "get",
                        lambda name: client if name == "torbox" else None)
    assert play._resolve(SOURCES[0]) == "https://cdn/x.mkv"
    assert client.calls


def test_a_service_no_longer_signed_in_is_not_used(monkeypatch, film):
    """A source cached by an account since removed must not go to it.

    _resolve used to pick the service by name without checking, so it handed
    the source to a client with no key.
    """
    gone = FakeClient(configured=False)
    fallback = FakeClient(url="https://cdn/fallback.mkv")
    monkeypatch.setattr(registry, "get", lambda name: gone)
    monkeypatch.setattr(registry, "preferred", lambda: fallback)

    assert play._resolve(SOURCES[0]) == "https://cdn/fallback.mkv"
    assert not gone.calls, "the unconfigured service must not be asked"


def test_no_debrid_at_all_resolves_to_nothing(monkeypatch, settings_module):
    monkeypatch.setattr(registry, "resolver_for", lambda source: None)
    assert play._resolve(SOURCES[0]) == ""


def test_a_service_that_raises_is_not_fatal(monkeypatch, film):
    monkeypatch.setattr(registry, "resolver_for",
                        lambda source: FakeClient(boom=True))
    assert play._resolve(SOURCES[0]) == ""


# --------------------------------------------------------------------------
# whether a download may be started
# --------------------------------------------------------------------------


def test_cached_only_forbids_starting_a_download(film, settings_module,
                                                 monkeypatch):
    """The default, and what the low-memory profile enforces."""
    settings_module.set("sources.cached_only", "true")
    monkeypatch.setattr(registry, "resolver_for", lambda source: FakeClient())
    entry = source("Best 1080p", "a" * 40)
    play._resolve(entry)
    assert entry["extra"]["allow_uncached"] is False


def test_turning_cached_only_off_lets_a_download_start(film, settings_module,
                                                       monkeypatch):
    """Otherwise the setting is a trap.

    Three debrid clients read allow_uncached and nothing ever set it, so it
    was always false: the picker listed uncached sources and the client then
    refused to open them, and pressing play did nothing at all.
    """
    settings_module.set("sources.cached_only", "false")
    monkeypatch.setattr(registry, "resolver_for", lambda source: FakeClient())
    entry = source("Best 1080p", "a" * 40)
    play._resolve(entry)
    assert entry["extra"]["allow_uncached"] is True


def test_the_answer_is_decided_at_playback_not_at_search(film, settings_module,
                                                         monkeypatch):
    """The setting can change between finding a source and playing it."""
    monkeypatch.setattr(registry, "resolver_for", lambda source: FakeClient())
    entry = source("Best 1080p", "a" * 40)
    entry["extra"]["allow_uncached"] = True          # as a stale search left it

    settings_module.set("sources.cached_only", "true")
    play._resolve(entry)
    assert entry["extra"]["allow_uncached"] is False


# --------------------------------------------------------------------------
# the bail-outs, each of which must say something
# --------------------------------------------------------------------------


@pytest.fixture
def spoken(monkeypatch):
    said = []
    monkeypatch.setattr(kodi, "notify", lambda *a, **kw: said.append(a))
    monkeypatch.setattr(kodi, "ok_dialog", lambda *a, **kw: said.append(a))
    return said


def test_a_title_tmdb_does_not_know_says_so(monkeypatch, spoken, settings_module):
    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: None)
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken, "the viewer should be told, not left with a dead button"


def test_no_debrid_account_says_so(film, spoken, settings_module):
    settings_module.set("torbox.apikey", "")
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken


def test_no_sources_found_says_so(film, spoken, monkeypatch):
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: [])
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken


def test_a_source_that_will_not_resolve_says_so(film, spoken, monkeypatch):
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: list(SOURCES))
    monkeypatch.setattr(play, "_resolve", lambda source: "")
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken


# --------------------------------------------------------------------------
# prefetch
# --------------------------------------------------------------------------


def _season_of(count):
    return [{"episode": number} for number in range(1, count + 1)]


def test_the_next_episode_is_warmed(monkeypatch, film):
    asked = []
    from pinky.meta import tmdb
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find",
                        lambda meta, **kw: asked.append(meta) or [])
    monkeypatch.setattr(tmdb, "episodes", lambda tmdb_id, season: _season_of(10))
    play.prefetch_next_episode({"type": "episode", "season": 1, "episode": 3,
                                "ids": {"tmdb": 1396}})
    assert asked and (asked[0]["season"], asked[0]["episode"]) == (1, 4)


def test_the_last_episode_of_a_season_warms_the_next_seasons_first(
        monkeypatch, film):
    """It added one to the episode number, so S1E7 of a seven-episode season
    prefetched S1E8, which does not exist, and S2E1 started cold."""
    asked = []
    from pinky.meta import tmdb
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find",
                        lambda meta, **kw: asked.append(meta) or [])
    seasons = {1: _season_of(7), 2: _season_of(7)}
    monkeypatch.setattr(tmdb, "episodes",
                        lambda tmdb_id, season: seasons.get(season, []))
    play.prefetch_next_episode({
        "type": "episode", "season": 1, "episode": 7, "ids": {"tmdb": 1396},
        "absolute": 7, "season_name": "The First Arc", "season_episodes": 7,
        "episode_title": "Finale"})

    assert asked, "the next season's first episode was never looked for"
    nxt = asked[0]
    assert (nxt["season"], nxt["episode"]) == (2, 1)
    assert nxt["absolute"] == 8, \
        "the playing episode's absolute number would search for itself again"
    assert "season_name" not in nxt and "season_episodes" not in nxt
    assert "episode_title" not in nxt


def test_a_final_episode_prefetches_nothing(monkeypatch, film):
    asked = []
    from pinky.meta import tmdb
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find",
                        lambda meta, **kw: asked.append(meta) or [])
    monkeypatch.setattr(tmdb, "episodes",
                        lambda tmdb_id, season: _season_of(5) if season == 1 else [])
    play.prefetch_next_episode({"type": "episode", "season": 1, "episode": 5,
                                "ids": {"tmdb": 1396}})
    assert not asked


def test_prefetch_follows_the_setting_when_nothing_was_set(monkeypatch, film,
                                                           settings_module):
    """The player's own fallback said on; the setting says off by default."""
    from pinky import player as player_module

    warmed = []
    monkeypatch.setattr(play, "prefetch_next_episode",
                        lambda meta: warmed.append(meta))
    monitor = player_module.PinkyPlayer()
    monitor.meta = {"type": "episode", "season": 1, "episode": 3}
    monkeypatch.setattr(monitor, "_progress", lambda: 90.0)

    monitor._maybe_prefetch()
    assert not warmed, "prefetched with the setting never switched on"

    settings_module.set("sources.prefetch_next", "true")
    monitor.prefetched = False
    monitor._maybe_prefetch()
    assert warmed


def test_a_film_has_no_next_episode(monkeypatch, film):
    asked = []
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find",
                        lambda meta, **kw: asked.append(meta) or [])
    play.prefetch_next_episode({"type": "movie"})
    assert not asked


# --------------------------------------------------------------------------
# falling through to the next source
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def links_open(monkeypatch):
    """Every resolved link opens, unless a test says otherwise.

    Playback now checks that a link actually gives up a byte before it is
    handed to Kodi, and the suite has no network, so without this every
    fall-through test would see every link as dead.

    Yields the real function, for the tests that are about the check itself
    rather than about what happens around it.
    """
    original = play._reachable
    monkeypatch.setattr(play, "_reachable", lambda url, honour_memory=True: url)
    return original


def test_a_source_that_will_not_resolve_falls_through_to_the_next(film,
                                                                  monkeypatch):
    """A source can be flagged cached by the indexer and not be on the debrid
    service at all. Giving up there told the viewer "could not play" while the
    second source would have played immediately."""
    tried = []

    def fake_resolve(source):
        tried.append(source["title"])
        return "https://cdn/ok.mkv" if source["title"] == "Worse 720p" else ""

    monkeypatch.setattr(play, "_resolve", fake_resolve)
    chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=False)

    assert url == "https://cdn/ok.mkv"
    assert chosen["title"] == "Worse 720p"
    assert tried == ["Best 1080p", "Worse 720p"]


def test_the_first_source_is_used_when_it_works(film, monkeypatch):
    tried = []
    monkeypatch.setattr(play, "_resolve",
                        lambda s: tried.append(s["title"]) or "https://cdn/a")
    chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=False)
    assert url == "https://cdn/a"
    assert tried == ["Best 1080p"], "no reason to try any others"


def test_a_link_that_will_not_open_falls_through_to_the_next(film,
                                                             monkeypatch):
    """A debrid service can hand back a link its own CDN will not serve.

    Not theoretical: TorBox returned good-looking links to two different
    store hosts on one evening, both of which accepted a TCP connection and
    then never answered. Kodi's only symptom was a black screen, because as
    far as the add-on was concerned it had succeeded.
    """
    monkeypatch.setattr(play, "_resolve",
                        lambda s: "https://dead/%s" % s["title"][:4]
                        if s["title"] == "Best 1080p" else "https://cdn/ok")
    monkeypatch.setattr(play, "_reachable",
                        lambda url, honour_memory=True: "" if url.startswith("https://dead/") else url)

    chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=False)
    assert url == "https://cdn/ok"
    assert chosen["title"] != "Best 1080p", "the dead link must not be used"


def test_a_link_the_viewer_picked_is_handed_over_without_a_probe(film,
                                                                 monkeypatch):
    """They asked for that release, and there is nothing to fall through to,
    so spending a round trip to confirm what cannot be acted on is waste."""
    probed = []
    monkeypatch.setattr(play, "_resolve", lambda s: "https://cdn/a")
    monkeypatch.setattr(play, "_reachable",
                        lambda url, honour_memory=True: probed.append(url) or url)

    _chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=True)
    assert url == "https://cdn/a"
    assert probed == []


def test_accepting_uncached_sources_is_playback_scoped(monkeypatch):
    from pinky import play
    waiting = [{"title": "uncached", "extra": {}}]
    monkeypatch.setattr("pinky.sources.aggregator.uncached", lambda meta: waiting)
    monkeypatch.setattr(play.kodi, "yes_no", lambda *args, **kwargs: True)
    writes = []
    monkeypatch.setattr(play.settings, "set", lambda *args: writes.append(args))
    assert play._offer_uncached({}) == waiting
    assert writes == []
    assert waiting[0]["extra"]["playback_allow_uncached"] is True


def test_trakt_percentage_resume_is_converted_to_duration_seconds(monkeypatch):
    from pinky.meta import trakt_state
    monkeypatch.setattr(trakt_state, "enabled", lambda: True)
    key = trakt_state.state_key("movie", {"tmdb": 7})
    monkeypatch.setattr(trakt_state, "_watched_map", lambda: {})
    monkeypatch.setattr(trakt_state, "_playback_map",
                        lambda: {key: {"progress": 50.0}})
    item = {"type": "movie", "ids": {"tmdb": 7}, "duration": 7200,
            "resume": {}}
    trakt_state.annotate([item])
    assert item["resume"] == {"position": 3600.0, "total": 7200.0}


def test_a_part_watched_item_resumes_without_being_asked(film, monkeypatch):
    """A numeric StartOffset chooses resume before Kodi can show its chooser."""
    import xbmcplugin
    from pinky.ui import listing

    xbmcplugin.reset()
    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {},
                     "resume": {"position": 2040.0, "total": 7200.0}})

    item = xbmcplugin.RESOLVED[-1][2]
    assert item.getProperty("StartOffset") == "2040.0"
    assert not item.getProperty("ResumeTime")
    assert not item.getProperty("TotalTime")


def test_original_playable_item_carries_start_offset_before_plugin_runs(film):
    from pinky.ui import listing

    item = listing.make_list_item(
        {"type": "movie", "title": "A Film", "ids": {}, "art": {},
         "resume": {"position": 2040.0, "total": 7200.0}})
    assert item.getProperty("StartOffset") == "2040.0"
    assert float(item.getProperty("pinky.percentplayed")) == pytest.approx(
        2040.0 / 7200.0 * 100.0)
    assert "resumepoint" not in item.getVideoInfoTag().data


def test_something_never_started_has_no_resume_properties(film):
    import xbmcplugin
    from pinky.ui import listing

    xbmcplugin.reset()
    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {}})
    item = xbmcplugin.RESOLVED[-1][2]
    assert not item.getProperty("ResumeTime")


def test_the_resume_point_is_fetched_before_playback(film, monkeypatch):
    """The rows carry it - that is the progress bar under a poster - but the
    item playback builds comes from TMDB and knows nothing about it, so
    resuming worked from a plain listing and not from the Pinky window."""
    from pinky.meta import trakt_state

    asked = []
    monkeypatch.setattr(trakt_state, "annotate",
                        lambda items: asked.append(items) or items)
    monkeypatch.setattr(play, "_resolve", lambda s: "https://cdn/a.mkv")
    monkeypatch.setattr(play, "build_meta", lambda request: {
        "type": "movie", "title": "A Film", "ids": {"imdb": "tt1"},
        "item": {"type": "movie", "title": "A Film", "ids": {}, "art": {}}})
    from pinky.sources import aggregator
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: list(SOURCES))

    play.play(1, {"type": "movie", "tmdb": "1"})
    assert asked, "nothing asked the Trakt mirror where the viewer got to"


class _Answered(object):
    """A response object with only what the reachability check touches."""

    def __init__(self, status_code=206):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


def test_a_link_that_answers_at_all_is_good_enough(links_open, monkeypatch):
    """Only a connection failure counts against a link. Some CDNs answer a
    range request with 403 and the whole file with 200, and refusing those
    would be worse than the problem this solves."""
    from pinky import http
    monkeypatch.setattr(http, "get", lambda url, **kwargs: _Answered(403))
    assert links_open("https://cdn/whatever")


def test_a_link_that_never_answers_is_refused(links_open, monkeypatch):
    from pinky import http
    monkeypatch.setattr(http, "get", lambda url, **kwargs: None)
    assert links_open("https://cdn/whatever") == ""


def test_the_probe_asks_for_one_byte_and_does_not_retry(links_open,
                                                        monkeypatch):
    """A dead host must cost one timeout, not three."""
    seen = {}
    answered = _Answered()

    from pinky import http

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return answered

    monkeypatch.setattr(http, "get", fake_get)
    assert links_open("https://cdn/whatever")
    assert seen["headers"]["Range"] == "bytes=0-0"
    assert seen["retries"] == 0
    assert seen["timeout"] == play.REACHABLE_TIMEOUT
    assert answered.closed, "the probe must not leave the connection open"


def test_a_host_that_would_not_answer_is_not_asked_twice(links_open,
                                                         monkeypatch):
    """A debrid service hands out links round-robin across its nodes, so the
    same dead node comes back for source after source. One evening's log had
    store-028, store-045 and store-028 again, each costing a full timeout."""
    from pinky import http

    attempts = []
    monkeypatch.setattr(http, "get",
                        lambda url, **kwargs: attempts.append(url) or None)

    assert links_open("https://store-028.example/dld/one") == ""
    assert links_open("https://store-028.example/dld/two") == ""
    assert len(attempts) == 1, "the second link on a dead host is free"

    # A different node is still asked.
    assert links_open("https://store-029.example/dld/three") == ""
    assert len(attempts) == 2


def test_dead_host_cache_and_logs_never_store_signed_url_secrets(
        links_open, monkeypatch):
    from pinky import cache, http
    secret_url = "https://user:password@cdn.example/x?token=TOPSECRET"
    stored = {}
    logs = []

    monkeypatch.setattr(cache, "get", lambda key: stored.get(key))
    monkeypatch.setattr(cache, "set",
                        lambda key, value, ttl: stored.__setitem__(key, value))
    monkeypatch.setattr(cache, "delete", lambda key: stored.pop(key, None))
    monkeypatch.setattr(http, "get", lambda url, **kwargs: None)
    monkeypatch.setattr(kodi, "log", lambda message, *args: logs.append(message))

    assert links_open(secret_url) == ""
    assert links_open(secret_url) == ""
    key = play._dead_host_key(secret_url)
    assert key == "debrid|deadhost|cdn.example"
    exposed = " ".join(list(stored) + logs)
    assert "TOPSECRET" not in exposed
    assert "password" not in exposed


def test_a_host_is_only_written_off_for_a_few_minutes(links_open, monkeypatch):
    """Being wrong here costs a playback, so the memory is deliberately
    short: a node coming back is normal, and once it is written off it is
    not asked again until the note expires."""
    from pinky import cache, http

    monkeypatch.setattr(http, "get", lambda url, **kwargs: None)
    assert links_open("https://store-030.example/dld/one") == ""

    key = play._dead_host_key("https://store-030.example/dld/one")
    assert cache.get(key), "the node should be remembered as unreachable"
    assert play.DEAD_HOST_TTL <= 600, \
        "a node written off for longer than this is a node nobody retries"

    # Once the note has gone, the node gets another chance.
    cache.delete(key)
    monkeypatch.setattr(http, "get", lambda url, **kwargs: _Answered())
    assert links_open("https://store-030.example/dld/two")


def test_the_key_is_the_host_not_the_link(links_open):
    """Links differ every time; the node is what fails."""
    first = play._dead_host_key("https://store-031.example/dld/aaaa?token=1")
    second = play._dead_host_key("https://store-031.example/dld/bbbb?token=2")
    other = play._dead_host_key("https://store-032.example/dld/aaaa?token=1")
    assert first == second and first != other
    assert play._dead_host_key("not a url at all") == ""


def test_a_source_the_viewer_picked_is_not_quietly_swapped(film, monkeypatch):
    """They asked for that release. Playing a different one would be worse
    than saying it could not be played."""
    tried = []
    monkeypatch.setattr(play, "_resolve",
                        lambda s: tried.append(s["title"]) or "")
    chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=True)
    assert url == ""
    assert tried == ["Best 1080p"]


def test_it_gives_up_rather_than_working_through_every_source(film,
                                                              monkeypatch,
                                                              settings_module):
    """It must stop, but where it stops depends on what an attempt costs.

    With uncached downloads allowed an attempt can spend one of TorBox's sixty
    an hour, so the limit is small. With "cached only" on - the default - no
    attempt can start a download, and being stingy only loses playbacks: The
    Matrix was refused by three sources in a row, each for an honest reason,
    with three more in the list that were never tried.
    """
    many = [dict(SOURCES[0], title="source %d" % n) for n in range(20)]

    def run():
        tried = []
        monkeypatch.setattr(play, "_resolve",
                            lambda s: tried.append(s["title"]) or "")
        play._resolve_any(many[0], many, force_picker=False)
        return tried

    settings_module.set("sources.cached_only", "true")
    assert len(run()) == play.RESOLVE_ATTEMPTS_CACHED

    settings_module.set("sources.cached_only", "false")
    assert len(run()) == play.RESOLVE_ATTEMPTS

    assert play.RESOLVE_ATTEMPTS < play.RESOLVE_ATTEMPTS_CACHED, \
        "an attempt that can start a download is the expensive one"


def test_a_source_no_service_can_open_says_so(film, monkeypatch, caplog):
    """Silence here reads as "the button did nothing".

    An episode reached the end of playback with eighty-one sources behind it
    and left no trace at all - no attempt, no message, nothing in the log.
    """
    from pinky.debrid import registry

    monkeypatch.setattr(registry, "resolver_for", lambda source: None)
    logged = []
    monkeypatch.setattr(play.kodi, "log",
                        lambda message, *a, **k: logged.append(message))

    assert play._resolve(dict(SOURCES[0])) == ""
    assert any("no configured debrid service" in line for line in logged), \
        "a source nothing can open must say so: %s" % logged


# --------------------------------------------------------------------------
# there are two ways Kodi expects to be handed a stream
# --------------------------------------------------------------------------


def test_a_context_menu_run_starts_playback_itself(film):
    """"Choose a source" found sources, resolved one, and played nothing.

    A context-menu entry is `RunPlugin`, which runs the plugin as a script:
    the handle is -1 and nothing is waiting for `setResolvedUrl`, so the
    resolved URL was handed to nobody. Pressing play on the same title
    worked, because that path passes a real handle.
    """
    import xbmc
    import xbmcplugin
    from pinky.ui import listing

    del xbmc.Player.PLAYED[:]
    xbmcplugin.reset()

    listing.resolve(-1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {}})

    assert not xbmcplugin.RESOLVED, "there was no handle to resolve to"
    assert xbmc.Player.PLAYED, "so it has to start playback itself"
    assert xbmc.Player.PLAYED[0][0][0] == "https://cdn/a.mkv"


def test_a_real_handle_is_still_resolved_to(film):
    """The normal path must not change: Kodi is waiting for this one."""
    import xbmc
    import xbmcplugin
    from pinky.ui import listing

    del xbmc.Player.PLAYED[:]
    xbmcplugin.reset()

    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {}})

    assert xbmcplugin.RESOLVED, "Kodi is waiting for a resolved URL"
    assert not xbmc.Player.PLAYED, "and must not be played to twice"


def test_a_failure_with_no_handle_is_not_reported_to_nobody(film):
    import xbmcplugin
    from pinky.ui import listing

    xbmcplugin.reset()
    listing.resolve_failed(-1)
    assert not xbmcplugin.RESOLVED


# --------------------------------------------------------------------------
# the broadcaster's own copy
# --------------------------------------------------------------------------


def test_an_israeli_title_falls_back_to_the_broadcaster():
    """Israeli television is not on the trackers, and never will be.

    The private Israeli trackers are account-gated and Sdarot was dissolved in
    2023, so "no sources" is the ordinary answer for a Keshet programme rather
    than a failure. The broadcaster streams it, the VOD catalogue has it, and
    the two halves of this add-on held both and never joined them.
    """
    found = play._vod_alternative({"original_title": u"רמזור",
                                   "original_language": "he"})
    assert "action=vod_show" in found["url"]
    assert "keshet" in found["url"]
    assert found["module"] == "keshet"
    assert found["ref"], "the walk down to the episode starts from this"


def test_a_foreign_title_is_never_looked_up_in_the_israeli_catalogue():
    assert play._vod_alternative({"original_title": "Fight Club",
                                  "original_language": "en"}) is None


def test_the_broadcaster_match_is_exact_and_never_a_substring():
    """A prefix of a real programme name must not open that programme.

    Landing inside the wrong series is worse than the honest "nothing found"
    this replaces, and the catalogue carries no id to check the guess against.
    """
    assert play._vod_alternative({"original_title": u"רמז",
                                  "original_language": "he"}) is None


def _mako_entries(module, ref, mode=""):
    """Mako as it really answers: numbers in the Hebrew title and nowhere else."""
    if ref.endswith("ramzor"):
        return [{"title": u"עונה %d" % n, "ids": {"vod": "ramzor-s%d" % n},
                 "extra": {"url": "plugin://x/?action=vod_show&ref=ramzor-s%d" % n}}
                for n in (1, 2, 3, 4)]
    season = ref[-1]
    return [{"title": u"פרק %d 07.04.08 כותרת" % n,
             "ids": {"vod": "vod-%s-%d" % (season, n)},
             "extra": {"url": "plugin://x/?action=play_vod&ref=vod-%s-%d" % (season, n)}}
            for n in range(1, 14)]


@pytest.fixture
def broadcaster(monkeypatch):
    from pinky.vod import extractors
    monkeypatch.setattr(extractors, "episodes", _mako_entries)
    return {"title": u"רמזור", "studio": "Keshet", "module": "keshet",
            "ref": "https://www.mako.co.il/mako-vod-keshet/ramzor", "mode": "2",
            "url": "plugin://x/?action=vod_show"}


def test_the_walk_reaches_the_episode_that_was_pressed(broadcaster, no_network):
    """Landing on the programme is not the feature; landing on the episode is.

    The first version of this opened a folder of four seasons after a press
    that meant "play episode three", which is the add-on looking like it lost
    its place rather than like it found something.
    """
    url = play._walk_to_episode({"season": 2, "episode": 5}, broadcaster)
    assert "action=play_vod" in url
    assert "ref=vod-2-5" in url


def test_a_season_the_broadcaster_does_not_have_falls_through(broadcaster, no_network):
    assert play._walk_to_episode({"season": 9, "episode": 1}, broadcaster) == ""


def test_the_date_in_a_mako_title_is_not_an_episode_number(broadcaster, no_network):
    """Every title carries "07.04.08", and 7 is not episode seven.

    The number is read from the word that names it, not from the first digits
    on the line, because playing the wrong episode is the one failure nothing
    downstream can catch - the file is exactly what it says it is.
    """
    url = play._walk_to_episode({"season": 1, "episode": 7}, broadcaster)
    assert "ref=vod-1-7" in url


def test_an_ambiguous_number_is_refused_rather_than_guessed(no_network, monkeypatch):
    from pinky.vod import extractors
    twice = [{"title": u"פרק 1 א", "ids": {"vod": "a"},
              "extra": {"url": "plugin://x/?action=play_vod&ref=a"}},
             {"title": u"פרק 1 ב", "ids": {"vod": "b"},
              "extra": {"url": "plugin://x/?action=play_vod&ref=b"}}]
    monkeypatch.setattr(extractors, "episodes", lambda *a, **k: twice)
    assert play._walk_to_episode({"season": 0, "episode": 1},
                                 {"title": "x", "studio": "s", "module": "keshet",
                                  "ref": "r", "mode": "2"}) == ""


# --------------------------------------------------------------------------
# leaving the video, and two presses racing
# --------------------------------------------------------------------------


FULLSCREEN = "Window.IsActive(fullscreenvideo)"


def _watching(meta_type="movie"):
    import xbmc
    from pinky import player as player_module

    p = player_module.PinkyPlayer()
    p.meta = {"type": meta_type, "title": "x", "ids": {}}
    p.paused = 0
    p.pause = lambda: setattr(p, "paused", p.paused + 1)
    xbmc.CONDITIONS.add(FULLSCREEN)
    return p


def test_playback_survives_while_the_video_is_on_screen():
    p = _watching()
    for _ in range(5):
        assert p.stop_if_left_behind() is False
    assert not p.paused


def test_leaving_the_video_pauses_it():
    """Escape means paused, not playing behind the menu.

    Kodi's own answer is to keep the file running, which is right for a
    library and wrong for a debrid link on a gigabyte of RAM. Paused rather
    than stopped, because going back in would otherwise be a source search
    and a resolve all over again.
    """
    import xbmc
    p = _watching()
    xbmc.CONDITIONS.discard(FULLSCREEN)
    assert p.stop_if_left_behind() is False, "one tick out is not enough"
    assert p.stop_if_left_behind() is True
    assert p.paused == 1


def test_it_pauses_once_and_does_not_toggle_back():
    """pause() is a toggle, so a second one is the film playing again."""
    import xbmc
    p = _watching()
    xbmc.CONDITIONS.discard(FULLSCREEN)
    for _ in range(10):
        p.stop_if_left_behind()
    assert p.paused == 1


def test_a_film_the_viewer_paused_is_left_alone():
    import xbmc
    p = _watching()
    xbmc.CONDITIONS.discard(FULLSCREEN)
    xbmc.CONDITIONS.add("Player.Paused")
    for _ in range(5):
        p.stop_if_left_behind()
    assert p.paused == 0


def test_a_glimpse_away_from_the_video_does_not_stop_it():
    """The window is not always up on the tick after onAVStarted."""
    import xbmc
    p = _watching()
    xbmc.CONDITIONS.discard(FULLSCREEN)
    p.stop_if_left_behind()
    xbmc.CONDITIONS.add(FULLSCREEN)
    p.stop_if_left_behind()
    xbmc.CONDITIONS.discard(FULLSCREEN)
    assert p.stop_if_left_behind() is False
    assert not p.paused


def test_a_live_channel_is_meant_to_play_behind_the_menus():
    """A television plays the channel while you browse; that is the point."""
    import xbmc
    p = _watching("channel")
    xbmc.CONDITIONS.discard(FULLSCREEN)
    for _ in range(5):
        assert p.stop_if_left_behind() is False
    assert not p.paused


def test_the_newest_press_cancels_the_one_still_resolving():
    """Two searches in flight, and the loser must not start its film.

    A source search is seconds of network. Without this the cancelled title
    finishes second, hands Kodi its URL, and the film that starts is the one
    the viewer backed out of.
    """
    first = play._take_ticket()
    assert play._still_wanted(first)
    second = play._take_ticket()
    assert play._still_wanted(second)
    assert not play._still_wanted(first)


def test_a_dead_host_note_never_vetoes_the_whole_playback(links_open, monkeypatch):
    """Measured on Hikaru no Go 2x03: every source went through one host.

    All ten resolved through torrentio.strem.fun, one stale note against that
    host skipped every one of them without opening anything, and the viewer
    got "that source would not open" with nothing tried at all. The memory is
    there to make the fallbacks cheap, not to end a playback before it starts.
    """
    from pinky import cache

    reachable = links_open
    cache.set(play._dead_host_key("https://onehost/x"), True, 300)
    from pinky import http
    opened = []
    monkeypatch.setattr(http, "get",
                        lambda url, **kw: opened.append(url) or None)

    assert reachable("https://onehost/x") == "", "a fallback is skipped free"
    assert opened == [], "and costs no request"

    assert reachable("https://onehost/x", honour_memory=False) == ""
    assert opened == ["https://onehost/x"],         "but the first attempt of a playback is actually made"


def test_the_first_attempt_ignores_the_memory(links_open, monkeypatch):
    """`_resolve_any` must open the chosen source even against a black mark."""
    from pinky import cache

    asked = []
    monkeypatch.setattr(play, "_resolve", lambda s: "https://onehost/a")
    monkeypatch.setattr(play, "_reachable",
                        lambda url, honour_memory=True:
                        asked.append(honour_memory) or url)
    cache.set(play._dead_host_key("https://onehost/a"), True, 300)

    play._resolve_any(SOURCES[0], SOURCES, force_picker=False)
    assert asked and asked[0] is False,         "the first source is tried for real, memory or no memory"


def test_the_playing_source_is_never_its_own_fallback():
    """The picker hands back a copy - the three subtitle lists mark each
    release with how it gets Hebrew, so what comes back is
    `dict(source, subs_mode=...)` and never the dict that went in. `entry is
    chosen` was therefore always false, so the fallback list began at index
    zero and included the source now playing: one that would not open was
    retried as its own replacement."""
    sources = [
        {"title": "A", "hash": "a" * 40, "cached": True, "cached_by": "torbox"},
        {"title": "B", "hash": "b" * 40, "cached": True, "cached_by": "torbox"},
        {"title": "C", "hash": "c" * 40, "cached": True, "cached_by": "torbox"},
    ]
    chosen = dict(sources[0], subs_mode="llm", subs_fit=70)

    found = play._fallbacks_after(chosen, sources)
    assert [f["title"] for f in found] == ["B", "C"]


# --------------------------------------------------------------------------
# a series is known by every name it is released under
# --------------------------------------------------------------------------


def _show(monkeypatch, anime=False, original_language="ko"):
    monkeypatch.setattr(tmdb, "show", lambda tmdb_id: {
        "ids": {"tmdb": tmdb_id, "imdb": "tt10919420"},
        "title": u"משחק הדיונון", "original_title": u"오징어 게임",
        "original_language": original_language, "year": 2021, "art": {},
        "extra": {"anime": anime}})
    monkeypatch.setattr(tmdb, "episodes", lambda tmdb_id, season: [])


def test_a_series_carries_its_english_and_translated_names(monkeypatch):
    """On a Hebrew interface `title` is Hebrew and a Korean drama's
    `original_title` is Korean, and no release carries either."""
    _show(monkeypatch)
    monkeypatch.setattr(tmdb, "translations", lambda kind, tmdb_id: [
        ("en", "Squid Game"), ("es", "El juego del calamar")])
    meta = play.build_meta({"type": "episode", "tmdb": "93405",
                            "season": 1, "episode": 1})
    assert meta["english_title"] == "Squid Game"
    assert "El juego del calamar" in meta["translated_titles"]


def test_an_english_original_needs_no_english_translation(monkeypatch):
    """TMDB leaves the English translation blank when it is the original."""
    _show(monkeypatch, original_language="en")
    monkeypatch.setattr(tmdb, "translations", lambda kind, tmdb_id: [
        ("es", "Juego de tronos")])
    meta = play.build_meta({"type": "episode", "tmdb": "1399",
                            "season": 1, "episode": 1})
    assert meta["english_title"] == meta["original_title"]


def test_anime_and_films_are_not_asked_for_translations(monkeypatch):
    """Anime has its own naming and films are checked differently - neither
    reads these, so neither pays for them."""
    def explode(*args, **kwargs):
        raise AssertionError("asked TMDB for translations")

    monkeypatch.setattr(tmdb, "translations", explode)
    monkeypatch.setattr(tmdb, "english_title", lambda kind, tmdb_id: "X")
    monkeypatch.setattr(tmdb, "anime_titles", lambda kind, tmdb_id: [])
    monkeypatch.setattr(tmdb, "absolute_episode", lambda *a: 1)
    monkeypatch.setattr(tmdb, "seasons", lambda tmdb_id: [])
    _show(monkeypatch, anime=True)
    assert "translated_titles" not in play.build_meta(
        {"type": "episode", "tmdb": "1", "season": 1, "episode": 1})

    monkeypatch.setattr(tmdb, "movie", lambda tmdb_id: {
        "ids": {"tmdb": tmdb_id}, "title": "The Matrix", "year": 1999,
        "art": {}, "extra": {"anime": False}})
    assert "translated_titles" not in play.build_meta(
        {"type": "movie", "tmdb": "603"})


def test_translations_reads_both_shapes_and_skips_blank_names(monkeypatch):
    monkeypatch.setattr(tmdb, "_call", lambda path, ttl=None, **kw: {
        "translations": [
            {"iso_639_1": "en", "data": {"name": ""}},
            {"iso_639_1": "es", "data": {"name": "Juego de tronos"}},
            {"iso_639_1": "it", "data": {"title": "Il Trono di Spade"}},
        ]})
    assert tmdb.translations("show", "1399") == [
        ("es", "Juego de tronos"), ("it", "Il Trono di Spade")]


def test_a_link_that_lands_on_a_providers_own_clip_is_not_the_film(links_open,
                                                                   monkeypatch):
    """Hikaru no Go 1x03 played Torrentio's "Torrent is being downloaded to
    debrid..." with an AI translation running over it: the link answered,
    and what it answered was a green screen."""
    from pinky import http
    reachable = links_open
    asked = "https://torrentio.strem.fun/resolve/torbox/k/abc/Hikaru.EP03.mkv"

    class Landed(object):
        def __init__(self, url):
            self.url, self.status_code = url, 200

        def close(self):
            pass

    monkeypatch.setattr(http, "get", lambda url, **kw: Landed(
        "https://torrentio.strem.fun/videos/downloading_v3.mp4"))
    assert reachable(asked, honour_memory=False) == ""
    monkeypatch.setattr(http, "get", lambda url, **kw: Landed(
        "https://store-039.wnam.tb-cdn.io/dld/0c5e91c1"))
    assert reachable(asked, honour_memory=False), "a redirect to the file is fine"


class _Hop(object):
    def __init__(self, status, url, location=""):
        self.status_code, self.url = status, url
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def close(self):
        self.closed = True


def test_kodi_is_given_where_the_link_leads_and_the_same_file_is_not_asked_twice(
        links_open, monkeypatch):
    """Kodi opened Torrentio's link and redirected again for every request it
    sent; the address it leads to is handed over instead. And coming back to
    the same file minutes later does not pay TorBox's first byte again."""
    from pinky import cache, http
    reachable = links_open
    asked = []
    torrentio = "https://torrentio.strem.fun/resolve/torbox/k/abc/Top.Gun.mkv"
    cdn = "https://store-013.wnam.tb-cdn.io/dld/c14c26d6?token=k"

    def get(url, **kwargs):
        asked.append((url, kwargs.get("allow_redirects", True)))
        if url == torrentio:
            return _Hop(302, url, cdn)
        return _Hop(206, url)

    monkeypatch.setattr(http, "get", get)
    assert reachable(torrentio, honour_memory=False) == cdn
    assert asked == [(torrentio, False), (cdn, True)], "one hop, not followed, then the file"
    del asked[:]
    assert reachable(torrentio, honour_memory=False) == cdn
    assert asked == [(torrentio, False)], "the same file, checked a moment ago"
    assert "token" not in play._answered_key(cdn) and cdn not in play._answered_key(cdn),         "the token is not written into the cache"


def test_kodi_is_told_the_file_type_so_it_does_not_ask_first():
    """Kodi asked TorBox's server what the file was before opening it: 1.4 s
    of first byte, then the request that actually opened the film."""
    assert play._mime_of({"file_name": "Top.Gun.Maverick.2022.1080p.WEB-DL.mkv"}) \
        == "video/x-matroska"
    assert play._mime_of({"title": "Fight.Club.1999.mp4"}) == "video/mp4"
    assert play._mime_of({"title": "Something without an extension"}) == ""


def test_a_release_that_will_not_open_is_replaced_aloud_and_keeps_the_route(
        film, monkeypatch):
    """Hikaru no Go: the chosen release failed in silence for five seconds,
    another played, and without the AI route it searched for Hebrew first."""
    from pinky import kodi
    said = []
    monkeypatch.setattr(kodi, "notify", lambda message, *a, **k: said.append(message))
    monkeypatch.setattr(play, "_resolve",
                        lambda source: "" if source["title"] == "Best 1080p" else "https://cdn/ok.mkv")
    chosen = dict(SOURCES[0], subs_mode="llm", subs_from="en")
    playing, url = play._resolve_any(chosen, [chosen] + SOURCES[1:], force_picker=False)
    assert url == "https://cdn/ok.mkv" and playing["title"] == "Worse 720p"
    assert playing["subs_mode"] == "llm", "translated, as the chosen row would have been"
    assert said and "Worse 720p" in said[-1]


def _release(title, cached=True):
    return {"title": title, "cached": cached, "hash": title[:8]}


def test_the_next_episode_carries_on_in_the_same_release_and_route():
    """Fansub and scene releases name every episode alike but for its number;
    watched with AI subtitles, the next one is translated too."""
    sources = [_release("[KORSARS]_Hikaru.no.Go.[S01E04].BDRip.1080p_[ForceMedia].mkv"),
               _release("Hikaru.No.Go.TV.EP04.BluRay.1080p.AC3.x264-CHD.mkv"),
               _release("[nielsen145]Hikaru.No.Go.TV.EP04.BluRay.1080p.AC3.x264[2021F4A9].mkv")]
    chosen = play._follow({"follow": "Hikaru.No.Go.TV.EP03.BluRay.1080p.AC3.x264-CHD.mkv",
                           "route": "llm"}, sources)
    assert chosen["title"] == "Hikaru.No.Go.TV.EP04.BluRay.1080p.AC3.x264-CHD.mkv"
    assert chosen["subs_mode"] == "llm"


def test_nothing_alike_enough_opens_the_picker():
    followed = {"follow": "Hikaru.No.Go.TV.EP03.BluRay.1080p.AC3.x264-CHD.mkv", "route": "llm"}
    assert play._follow(followed, [_release("Hikaru.No.Go.TV.EP04.BluRay.720p.AC3.x264-CHD.mkv")]) is None, \
        "another resolution is another release"
    assert play._follow(followed, [_release("Hikaru.No.Go.TV.EP04.BluRay.1080p.AC3.x265-CHD.mkv")]) is None, \
        "another codec too"
    assert play._follow(followed, [_release("Hikaru.No.Go.TV.EP04.BluRay.1080p.AC3.x264-CHD.mkv",
                                            cached=False)]) is None, "only what plays at once"
    assert play._follow({"follow": ""}, [_release("anything")]) is None, "and only when asked"


def test_the_next_episode_link_names_what_was_playing(monkeypatch):
    from pinky import player as player_module
    p = player_module.PinkyPlayer()
    p.meta = {"source": {"file_name": "Hikaru.No.Go.TV.EP03.BluRay.1080p.AC3.x264-CHD.mkv",
                         "subs_mode": "llm"}}
    p._next = {"ids": {"tmdb": 30982}, "season": 1, "episode": 4}
    url = p._next_url()
    assert "follow=" in url and "route=llm" in url and "episode=4" in url

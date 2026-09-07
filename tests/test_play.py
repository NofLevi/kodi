"""From "the user pressed OK" to "Kodi has a URL".

This module decides what every viewer actually sees and had no tests at all.
The autoplay decision in particular is the difference between a film starting
by itself and a list of eight releases appearing.
"""
import pytest

from katan import kodi, play, settings
from katan.debrid import registry
from katan.meta import tmdb


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
    return settings_module


# --------------------------------------------------------------------------
# build_meta
# --------------------------------------------------------------------------


def test_a_film_gets_its_title_and_ids(film):
    meta = play.build_meta({"type": "movie", "tmdb": "278"})
    assert meta["title"] == "The Shawshank Redemption"
    assert meta["ids"]["imdb"] == "tt0111161"
    assert meta["year"] == 1994


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
# the autoplay decision
# --------------------------------------------------------------------------


def test_autoplay_takes_the_best_source_without_asking(film, settings_module):
    settings_module.set("sources.autoplay", "true")
    assert play._choose(SOURCES, {}, force_picker=False) is SOURCES[0]


def test_the_context_menu_forces_the_picker(film, monkeypatch, settings_module):
    """"Choose a source" must never quietly autoplay instead."""
    settings_module.set("sources.autoplay", "true")
    asked = []
    import katan.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source",
                        lambda sources, meta: asked.append(sources) or sources[1])

    chosen = play._choose(SOURCES, {}, force_picker=True)
    assert asked, "the picker should have been opened"
    assert chosen is SOURCES[1]


def test_autoplay_off_opens_the_picker(film, monkeypatch, settings_module):
    settings_module.set("sources.autoplay", "false")
    import katan.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source", lambda sources, meta: sources[1])
    assert play._choose(SOURCES, {}, force_picker=False) is SOURCES[1]


def test_a_cancelled_picker_plays_nothing(film, monkeypatch, settings_module):
    settings_module.set("sources.autoplay", "false")
    import katan.ui.sources_window as window
    monkeypatch.setattr(window, "pick_source", lambda sources, meta: None)
    assert play._choose(SOURCES, {}, force_picker=False) is None


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
    from katan.sources import aggregator
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: [])
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken


def test_a_source_that_will_not_resolve_says_so(film, spoken, monkeypatch):
    from katan.sources import aggregator
    monkeypatch.setattr(aggregator, "find", lambda meta, **kw: list(SOURCES))
    monkeypatch.setattr(play, "_resolve", lambda source: "")
    play.play(-1, {"type": "movie", "tmdb": "278"})
    assert spoken


# --------------------------------------------------------------------------
# prefetch
# --------------------------------------------------------------------------


def test_the_next_episode_is_warmed(monkeypatch, film):
    asked = []
    from katan.sources import aggregator
    monkeypatch.setattr(aggregator, "find",
                        lambda meta, **kw: asked.append(meta) or [])
    play.prefetch_next_episode({"type": "episode", "season": 1, "episode": 3})
    assert asked and asked[0]["episode"] == 4


def test_a_film_has_no_next_episode(monkeypatch, film):
    asked = []
    from katan.sources import aggregator
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
    monkeypatch.setattr(play, "_reachable", lambda url: True)
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
                        lambda url: not url.startswith("https://dead/"))

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
                        lambda url: probed.append(url) or True)

    _chosen, url = play._resolve_any(SOURCES[0], SOURCES, force_picker=True)
    assert url == "https://cdn/a"
    assert probed == []


def test_a_part_watched_item_resumes_without_being_asked(film, monkeypatch):
    """Kodi asks "resume or start again?" when it is given a resume point and
    nothing else. Nobody wants that question on the way into something they
    were already watching, and ResumeTime is what answers it in advance."""
    import xbmcplugin
    from katan.ui import listing

    xbmcplugin.reset()
    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {},
                     "resume": {"position": 2040.0, "total": 7200.0}})

    item = xbmcplugin.RESOLVED[-1][2]
    assert item.getProperty("ResumeTime") == "2040.0"
    assert item.getProperty("TotalTime") == "7200.0"


def test_something_never_started_has_no_resume_properties(film):
    import xbmcplugin
    from katan.ui import listing

    xbmcplugin.reset()
    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {}})
    item = xbmcplugin.RESOLVED[-1][2]
    assert not item.getProperty("ResumeTime")


def test_the_resume_point_is_fetched_before_playback(film, monkeypatch):
    """The rows carry it - that is the progress bar under a poster - but the
    item playback builds comes from TMDB and knows nothing about it, so
    resuming worked from a plain listing and not from the Katan window."""
    from katan.meta import trakt_state

    asked = []
    monkeypatch.setattr(trakt_state, "annotate",
                        lambda items: asked.append(items) or items)
    monkeypatch.setattr(play, "_resolve", lambda s: "https://cdn/a.mkv")
    monkeypatch.setattr(play, "build_meta", lambda request: {
        "type": "movie", "title": "A Film", "ids": {"imdb": "tt1"},
        "item": {"type": "movie", "title": "A Film", "ids": {}, "art": {}}})
    from katan.sources import aggregator
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
    from katan import http
    monkeypatch.setattr(http, "get", lambda url, **kwargs: _Answered(403))
    assert links_open("https://cdn/whatever") is True


def test_a_link_that_never_answers_is_refused(links_open, monkeypatch):
    from katan import http
    monkeypatch.setattr(http, "get", lambda url, **kwargs: None)
    assert links_open("https://cdn/whatever") is False


def test_the_probe_asks_for_one_byte_and_does_not_retry(links_open,
                                                        monkeypatch):
    """A dead host must cost one timeout, not three."""
    seen = {}
    answered = _Answered()

    from katan import http

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return answered

    monkeypatch.setattr(http, "get", fake_get)
    assert links_open("https://cdn/whatever") is True
    assert seen["headers"]["Range"] == "bytes=0-0"
    assert seen["retries"] == 0
    assert seen["timeout"] == play.REACHABLE_TIMEOUT
    assert answered.closed, "the probe must not leave the connection open"


def test_a_host_that_would_not_answer_is_not_asked_twice(links_open,
                                                         monkeypatch):
    """A debrid service hands out links round-robin across its nodes, so the
    same dead node comes back for source after source. One evening's log had
    store-028, store-045 and store-028 again, each costing a full timeout."""
    from katan import http

    attempts = []
    monkeypatch.setattr(http, "get",
                        lambda url, **kwargs: attempts.append(url) or None)

    assert links_open("https://store-028.example/dld/one") is False
    assert links_open("https://store-028.example/dld/two") is False
    assert len(attempts) == 1, "the second link on a dead host is free"

    # A different node is still asked.
    assert links_open("https://store-029.example/dld/three") is False
    assert len(attempts) == 2


def test_a_host_is_only_written_off_for_a_few_minutes(links_open, monkeypatch):
    """Being wrong here costs a playback, so the memory is deliberately
    short: a node coming back is normal, and once it is written off it is
    not asked again until the note expires."""
    from katan import cache, http

    monkeypatch.setattr(http, "get", lambda url, **kwargs: None)
    assert links_open("https://store-030.example/dld/one") is False

    key = play._dead_host_key("https://store-030.example/dld/one")
    assert cache.get(key), "the node should be remembered as unreachable"
    assert play.DEAD_HOST_TTL <= 600, \
        "a node written off for longer than this is a node nobody retries"

    # Once the note has gone, the node gets another chance.
    cache.delete(key)
    monkeypatch.setattr(http, "get", lambda url, **kwargs: _Answered())
    assert links_open("https://store-030.example/dld/two") is True


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
    from katan.debrid import registry

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
    from katan.ui import listing

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
    from katan.ui import listing

    del xbmc.Player.PLAYED[:]
    xbmcplugin.reset()

    listing.resolve(1, "https://cdn/a.mkv",
                    {"type": "movie", "title": "A Film", "ids": {}, "art": {}})

    assert xbmcplugin.RESOLVED, "Kodi is waiting for a resolved URL"
    assert not xbmc.Player.PLAYED, "and must not be played to twice"


def test_a_failure_with_no_handle_is_not_reported_to_nobody(film):
    import xbmcplugin
    from katan.ui import listing

    xbmcplugin.reset()
    listing.resolve_failed(-1)
    assert not xbmcplugin.RESOLVED

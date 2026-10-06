"""Finding sources: run the providers, learn what is cached, rank, return few.

The shape of this module is the answer to "why did the other add-on crash my
box". It does four things in a fixed order and refuses to do more:

1. Run only the providers that are enabled and relevant, on the shared worker
   pool, under one wall-clock deadline. A slow provider is abandoned, not
   waited for.
2. Merge duplicates by infohash, so three providers reporting the same torrent
   cost one cache check rather than three.
3. Ask each debrid service once, in batches, which hashes it can play now.
4. Filter and rank, then hand back a short list.

Results are cached, so pressing back and playing again is instant, and so the
next-episode prefetch costs nothing when the user gets there.
"""
import time

from .. import cache, http, kodi, settings
from . import model, scoring

TTL_RESULTS = 20 * 60          # a source list stays useful for a short while
TTL_EMPTY = 5 * 60             # remember failures briefly, but not for long

# The second ask, when the first came back with nothing and a provider was
# still working. Longer than the ordinary deadline because there is nothing
# queued behind it - the whole point of the ten second rule is that somebody
# is waiting on the *other* providers, and by here there are none.
_PATIENT_DEADLINE = 25

# Providers that only make sense for anime, and are skipped otherwise.
ANIME_PROVIDERS = ("nyaa", "animetosho")


def _provider_modules():
    """Import providers lazily, so an unused one costs nothing."""
    from .providers import (animetosho, comet, external, mediafusion, nyaa,
                            torrentio, torrentsdb, zilean)
    return {
        "torrentio": torrentio,
        "torrentsdb": torrentsdb,
        "comet": comet,
        "mediafusion": mediafusion,
        "zilean": zilean,
        "nyaa": nyaa,
        "animetosho": animetosho,
        "external": external,
    }


def _is_anime(meta):
    extra = meta.get("extra") or {}
    if extra.get("anime"):
        return True
    ids = meta.get("ids") or {}
    if ids.get("anilist") or ids.get("kitsu"):
        return True
    item = meta.get("item") or {}
    return bool((item.get("extra") or {}).get("anime"))


def _enabled_providers(meta):
    modules = _provider_modules()
    anime = _is_anime(meta)
    chosen = []
    for name in settings.enabled_source_providers():
        module = modules.get(name)
        if module is None:
            continue
        if name in ANIME_PROVIDERS and not anime:
            continue
        chosen.append((name, module))
    return chosen


def cache_key(meta):
    ids = meta.get("ids") or {}
    identity = ids.get("imdb") or ids.get("tmdb")
    if not identity:
        identity = "%s|%s" % (meta.get("title") or "", meta.get("year") or "")
    return cache.make_key("sources", meta.get("type"), identity,
                          meta.get("season"), meta.get("episode"))


def find(meta, prefetch=False, force=False):
    """Return a ranked, short list of sources for a movie or episode."""
    return _top(_ranked(meta, prefetch=prefetch, force=force))


def _ranked(meta, prefetch=False, force=False):
    """Everything that survived the filters, best first.

    What is cached is the whole ranked list, not the handful the picker
    shows. Caching only the short list made "show all" a lie: it read back
    the same cache entry, so the toggle re-ranked eight sources and returned
    the same eight. Ranking a cached list again is a sort of a few hundred
    dictionaries, which costs nothing next to the search it replaces.
    """
    key = cache_key(meta)
    if not force:
        hit = cache.volatile_get(key)
        if hit is not None:
            # Source rows cached by an older add-on version predate subtitle
            # outlook fields. Without upgrading them, the picker draws an empty
            # subtitle line for up to twenty minutes after an update even though
            # fresh searches show a percentage. Annotate and re-rank once, then
            # persist the upgraded shape so subsequent opens remain free.
            _scene_episode(meta, hit)
            if hit and any("subs_kind" not in source for source in hit):
                migrated = _apply_subtitles(hit, meta)
                if migrated and all("subs_kind" in source for source in hit):
                    hit, _rejected = scoring.rank_all(
                        hit, meta, _runtime_hours(meta))
                    cache.volatile_set(key, hit, TTL_RESULTS)
            return hit if prefetch else _recheck_cached(hit, meta)

    providers = _enabled_providers(meta)
    if not providers:
        kodi.log("no source providers are enabled")
        return []

    # An anime episode whose arc TMDB has named is asked for under both of the
    # addresses the world files it under, and **in the same round**. Not as a
    # fallback: making it conditional on the first search finding nothing
    # meant one bad name-match suppressed it entirely, and that is not
    # hypothetical - Bleach 2x47 came back with a single wrongly matched
    # result from a name index, which was enough to stop the address that had
    # the episode from ever being tried.
    #
    # Together rather than one after the other, because two rounds is two
    # deadlines and the viewer waits through both: measured on Bleach 2x46 at
    # 3598 ms against 904 ms for a series needing only one address.
    dropped = []
    raw = _run_providers(providers, meta, quiet=prefetch,
                         also=_anime_address(meta, "arc"), dropped=dropped)

    # The same holds when only a name index answered and a provider that asks
    # by id was cut off. Black Lagoon 1x20: Nyaa came back in a second with
    # two files that were never going to survive the filters, Torrentio was
    # still working at ten seconds, and "not raw" was false - so the provider
    # that answers for this exact episode was never asked again, and the
    # viewer was told there was nothing where a calmer run found twenty-one.
    by_id_dropped = [name for name in dropped if name not in ANIME_PROVIDERS]
    if raw and by_id_dropped and all(
            source.get("provider") in ANIME_PROVIDERS for source in raw):
        kodi.log("only a name index answered and %s was still working, so "
                 "asking again" % ", ".join(by_id_dropped))
        retry = [(name, module) for name, module in providers
                 if name in set(by_id_dropped)]
        raw = raw + _run_providers(retry, meta, quiet=prefetch,
                                   also=_anime_address(meta, "arc"),
                                   deadline=_PATIENT_DEADLINE)

    if not raw and dropped:
        # Nothing came back and somebody was still working: that is "we
        # stopped waiting", not "there is nothing". Measured on Mortal Kombat
        # II - TorrentsDB was cooling down after a 429 and Comet and
        # MediaFusion refused for want of configuration, so Torrentio was the
        # only provider that could answer and it was cut off at ten seconds.
        # The viewer was told "no playable sources found" for a film with
        # plenty.
        #
        # Asked once more, and only in this case: there is nothing else in
        # flight to wait behind, so the second deadline costs a viewer who was
        # about to be told no.
        kodi.log("nothing came back and %s was still working, so asking again"
                 % ", ".join(dropped))
        retry = [(name, module) for name, module in providers
                 if name in set(dropped)]
        if retry:
            raw = _run_providers(retry, meta, quiet=prefetch,
                                 also=_anime_address(meta, "arc"),
                                 deadline=_PATIENT_DEADLINE)

    if not raw:
        # The plain shape, where TMDB's address usually works and paying for a
        # second question every time buys nothing - so it is only asked when
        # the first one came back with nothing at all.
        season = _anime_address(meta, "season")
        if season:
            raw = _run_providers(_by_id(providers), season, quiet=prefetch)

    if not raw:
        if dropped:
            # Do not remember a deadline as an answer. Five minutes of "no
            # sources" for a film that has them is the same failure twice.
            kodi.log("no sources, but %s never answered - not remembering it"
                     % ", ".join(dropped))
            return []
        cache.volatile_set(key, [], TTL_EMPTY)
        return []

    merged = model.dedupe(raw)
    _apply_meta(merged, meta)
    _check_debrid_cache(merged)
    _scene_episode(meta, merged)

    # Rated after the filters, not before: a subtitle's fit is only read by
    # the score and the order, and rating all 279 of Top Gun's releases to
    # keep 89 was most of the picker's computation.
    kept, rejected = scoring.rank_all(
        merged, meta, _runtime_hours(meta),
        annotate=lambda survivors: _apply_subtitles(survivors, meta))
    kept = _drop_unplayable(kept, rejected)
    # Both numbers, because they are different things and the log is read by
    # somebody wondering why the picker shows six rows. "66 after ranking"
    # next to a six-row picker reads as a contradiction; "66 kept, showing 6"
    # is the actual arrangement.
    kodi.log("sources: %d found, %d kept, showing %d%s"
             % (len(merged), len(kept), len(_top(kept)),
                _rejection_summary(rejected)))
    _remember_filtering(meta, len(merged), rejected)

    # Everything that was found, before the filters had their say. It is kept
    # so that "nothing survived" can be answered with something better than
    # "nothing found" - see `uncached`, below - without searching again.
    cache.volatile_set(unfiltered_key(meta), merged, TTL_RESULTS if merged else TTL_EMPTY)
    cache.volatile_set(key, kept, TTL_RESULTS if kept else TTL_EMPTY)
    return kept


def _scene_episode(meta, sources):
    """The numbering the releases themselves use, when it is not TMDB's.

    TMDB folds Jujutsu Kaisen's second season into its first, so the episode
    the viewer pressed is "1x41" - and every release and every subtitle calls
    it "S2 - 17". The sources were found anyway, at the Kitsu address, but the
    subtitle search asked for 1x41 and absolute 41 and got nothing: Re:Zero,
    Apothecary Diaries and JJK all drew an empty AI list beside dozens of
    cached sources, while OpenSubtitles had five English files as S02E17.

    Read off the releases rather than worked out from Kitsu's cour titles,
    because the releases are what the subtitles were typed against. Only the
    providers that ask by id vote - they answered for this exact episode,
    where a name index returns anything sharing a word - and only a clear
    majority counts. Anime only, and set on `meta` as `scene`.
    """
    if meta.get("type") != "episode" or not (meta.get("extra") or {}).get("anime"):
        return
    from ..utils import release
    own = {(int(meta.get("season") or 0), int(meta.get("episode") or 0)),
           (1, int(meta.get("absolute") or meta.get("episode") or 0))}
    votes = {}
    for source in sources or []:
        named = source.get("providers") or [source.get("provider")]
        if all(name in ANIME_PROVIDERS for name in named):
            continue
        parsed = release.parse(source.get("title") or "")
        pair = (parsed["season"] or 0, parsed["episode"] or parsed["absolute"] or 0)
        if pair[0] and pair[1] and pair not in own:
            votes[pair] = votes.get(pair, 0) + 1
    pair = max(votes, key=votes.get) if votes else None
    if pair and votes[pair] >= 2 and votes[pair] * 2 > sum(votes.values()):
        meta["scene"] = list(pair)
    elif _only_a_break(meta):
        # Every release of Snow White with the Red Hair 1x21 calls it "21" or
        # S01E21, and OpenSubtitles files its English as S02E09 - which is
        # what TMDB's own air dates say too: the ninth episode after a break.
        numbering = _numbering_after_break(meta)
        if numbering:
            meta["scene"] = numbering


def _only_a_break(meta):
    """Folded by a break alone: no season name, not long, not absolute."""
    return (not meta.get("season_name") and not _numbered_absolutely(meta)
            and int(meta.get("season_episodes") or 0) <= FOLDED_SEASON
            and _folded(meta))


def _numbering_after_break(meta):
    """[season, episode] counted from the last break before this episode."""
    import datetime
    aired = (meta.get("item") or {}).get("premiered") or ""
    try:
        dates = sorted(datetime.date(*map(int, day[:10].split("-")))
                       for day in _season_dates(meta) if day and day <= aired)
    except ValueError:
        return None
    runs, start = 1, 0
    for index in range(1, len(dates)):
        if (dates[index] - dates[index - 1]).days > BREAK_DAYS:
            runs, start = runs + 1, index
    if runs == 1:
        return None
    return [int(meta.get("season") or 1) + runs - 1, len(dates) - start]


PLAYABLE_CHECK = 20


def _drop_unplayable(kept, rejected):
    """Take out the cached rows the service holds nothing playable in.

    Only the top PLAYABLE_CHECK cached rows are asked about - they are what
    the picker draws - and only of a service that can say (TorBox, whose
    `playable` explains what "cached" was hiding). A row it cannot vouch for
    either way stays.
    """
    from ..debrid import registry
    asked = {}
    for source in [s for s in kept if s.get("cached") and s.get("hash")][:PLAYABLE_CHECK]:
        service = registry.get(source.get("cached_by") or "")
        if service is not None and hasattr(service, "playable"):
            asked.setdefault(service, []).append(source["hash"])
    dead = set()
    for service, hashes in asked.items():
        try:
            answers = service.playable(hashes)
        except Exception:
            kodi.log_exception("could not ask %s what it can play" % service.name)
            continue
        dead |= set(info_hash for info_hash, ok in answers.items() if ok is False)
    if not dead:
        return kept
    reason = "held by the debrid service with nothing to play"
    rejected[reason] = rejected.get(reason, 0) + len(dead)
    kodi.log("sources: %d cached rows have nothing playable in them" % len(dead))
    return [s for s in kept if s.get("hash") not in dead]


def unfiltered_key(meta):
    return cache_key(meta) + "|raw"


def uncached(meta):
    """What there would be if "cached only" were off, ranked as usual.

    For a film that has been out a while this is empty or close to it -
    everything worth having is in somebody's debrid cache. For an episode
    that aired last week it is the whole answer: forty-four copies of a
    Bleach episode existed, every one of them found, and not one of them was
    on the account yet. Saying "no sources" to that is untrue and unhelpful.
    """
    found = cache.volatile_get(unfiltered_key(meta))
    if not found:
        return []
    prefs = scoring.Preferences()
    prefs.cached_only = False
    kept = [source for source in found
            if not scoring.rejection_reason(source, prefs, _runtime_hours(meta))]
    # The cached-only filter kept these from being rated the first time.
    _apply_subtitles(kept, meta)
    for source in kept:
        source["score"] = scoring.score(source, prefs, _runtime_hours(meta))
    kept.sort(key=lambda s: scoring.sort_key(s, prefs))
    return kept


def filter_key(meta):
    return cache_key(meta) + "|why"


def _remember_filtering(meta, found, rejected):
    """Keep why sources were dropped, for the picker to show.

    Until now this only went to the log, and only when the search actually
    ran. So a viewer looking at eight results for a film with sixty-four
    releases had no way at all to know that forty of them were camera
    recordings - the picker simply looked broken, and was reported as such.
    """
    cache.volatile_set(filter_key(meta),
              {"found": found,
               "reasons": sorted((rejected or {}).items(),
                                 key=lambda kv: -kv[1])},
              TTL_RESULTS)


def filter_report(meta):
    """How many were found and why most of them are not on the screen.

    Returns None when nothing is remembered, which is not the same as
    "nothing was dropped" and must not be shown as though it were.
    """
    return cache.volatile_get(filter_key(meta))


def _top(sources):
    """The handful the picker shows, which is the point of ranking at all."""
    limit = settings.get_int("sources.results")
    return sources[:limit] if limit else sources


def _by_id(providers):
    """The providers that ask by id.

    The two that ask by name have already been given their best question - the
    show's name and the absolute number - and asking them again with a
    cour-relative number is asking something they cannot answer correctly.
    """
    return [(name, module) for name, module in providers
            if not getattr(module, "BY_NAME", False)]


def _run_providers(providers, meta, quiet=False, also=None, dropped=None,
                   deadline=None):
    """Fan out under the shared cap, showing progress unless prefetching.

    `also` is a second description of the same episode - the anime address -
    and its providers join the same round rather than forming another one.
    Under one worker cap and one deadline, which is the point: a second round
    is a second wait, and the rule this add-on rests on is that a search has a
    wall clock.
    """
    workers = max(1, settings.get_int("sources.workers"))
    if deadline is None:
        deadline = max(4, settings.get_int("sources.timeout"))

    progress = None
    if not quiet:
        import xbmcgui
        progress = xbmcgui.DialogProgressBG()
        progress.create("Pinky", kodi.localize(32331))

    found = []
    started = time.time()

    def on_result(name, sources):
        found.extend(sources)
        if progress is not None:
            elapsed = time.time() - started
            progress.update(int(min(99, (elapsed / deadline) * 100)),
                            message=kodi.localize(32332, len(found)))

    if also:
        # A named arc replaces the address rather than adding to it, for the
        # providers that ask by id. TMDB's address is not merely sometimes
        # empty for those - it is the wrong address, and measured on both
        # Bleach 2x46 and 2x47 it contributed nothing at all. Asking anyway
        # would be half again as many requests through a four-worker cap,
        # which the viewer waits through; the whole reason this add-on caps
        # concurrency is that the wait is the cost.
        #
        # The two that ask by name still get the original, because the show's
        # name and the absolute number are what they can answer.
        tasks = []
        for name, module in providers:
            asks_by_name = getattr(module, "BY_NAME", False)
            tasks.append((name, _guarded(module, meta if asks_by_name else also)))
            if also.get("alongside") and not asks_by_name:
                tasks.append((name, _guarded(module, meta)))
    else:
        tasks = [(name, _guarded(module, meta)) for name, module in providers]

    try:
        http.run_parallel(tasks, workers=workers, deadline=deadline,
                          on_result=on_result, dropped=dropped)
    finally:
        if progress is not None:
            progress.close()
    return found


def _guarded(module, meta):
    def call():
        return module.search(meta) or []
    return call


def _apply_subtitles(sources, meta):
    """Note what each source's Hebrew subtitles are likely to be.

    Ranking needs this, not only the picker: a release with a subtitle
    written for it is a better answer than a slightly larger one without,
    and autoplay should be making that choice too rather than leaving it to
    whoever happens to open the picker.

    The lookup only needs the title, so its one network call has already been
    warmed by the time the providers finish - and it is cached per title, so
    the second source search for the same film pays nothing at all. A failure
    leaves the sources unannotated, which ranks them as before.
    """
    try:
        from ..subs import outlook
        outlook.annotate(meta, sources)
        return True
    except Exception:
        kodi.log_exception("could not work out the subtitle outlook")
        return False


def _apply_meta(sources, meta):
    """Attach the playback context each debrid client needs for file picking."""
    context = {
        "type": meta.get("type"),
        "season": meta.get("season"),
        "episode": meta.get("episode"),
        # A fansub batch names its files absolutely, so picking the right one
        # out of a pack needs the absolute number as well as the pair.
        "absolute": meta.get("absolute"),
        "title": meta.get("title"),
        # How long it runs, so the file picker can tell a two-minute
        # episode from a decoy of the same size.
        "item": {"duration": (meta.get("item") or {}).get("duration") or 0},
    }
    for source in sources:
        source.setdefault("extra", {})
        source["extra"]["meta"] = context


def _check_debrid_cache(sources, recheck=False):
    """One batched question per service, never one request per source.

    Returns False when no service could answer, so the caller can tell "no
    service holds this" apart from "nobody was asked". Normally only the
    sources not already flagged are asked about; `recheck` asks about all of
    them and clears a flag that is no longer true.
    """
    from ..debrid import registry

    ask = [s["hash"] for s in sources
           if s.get("hash") and (recheck or not s.get("cached"))]
    if not ask:
        return True
    try:
        answers = registry.cached_map(ask)
    except Exception:
        kodi.log_exception("debrid cache lookup failed")
        return False
    known = getattr(answers, "known", set(ask))
    for source in sources:
        service = answers.get(source.get("hash"))
        if service:
            source["cached"] = True
            source["cached_by"] = service
        elif recheck and source.get("hash") in known:
            source.pop("cached", None)
            source.pop("cached_by", None)
    return True


def _recheck_cached(sources, meta):
    """Re-confirm cache flags on a cached result list, and re-rank if they moved.

    The list itself stays valid for a while, but whether a service still holds
    a torrent can change, and playing a source that is no longer cached is the
    most annoying possible failure.

    This used to ask only about the sources already marked *un*cached, so a
    flag could go up and never come down - which is precisely the failure the
    paragraph above says it prevents. It now asks about all of them.

    Re-ranking afterwards is not decoration. Being cached is worth more than
    every other signal put together, and with `cached_only` on - the default -
    it decides whether a source is shown at all, so a flag that changed and an
    order that did not would put a source that can no longer play at the top.
    """
    if not sources:
        return sources
    before = [bool(s.get("cached")) for s in sources]
    if not _check_debrid_cache(sources, recheck=True):
        return sources
    if [bool(s.get("cached")) for s in sources] == before:
        return sources
    # Deliberately not written back to the cache. Re-ranking drops whatever
    # is no longer playable, and with `cached_only` on that is most of the
    # list; storing the shorter version would mean a source that became
    # cached again could not come back until the whole entry expired. The
    # stored list stays the full one and this correction is redone each time,
    # which the debrid client's own memo makes cheap.
    kept, _ = scoring.rank_all(sources, meta, _runtime_hours(meta))
    return kept


def _runtime_hours(meta):
    """Used for size sanity checks, so an episode is not judged like a film."""
    item = meta.get("item") or {}
    duration = item.get("duration") or 0
    if duration:
        # TMDB's own length, trusted. A fifteen-minute floor judged Chiikawa's
        # two-minute episodes as quarter-hour ones, and rejected all eight
        # copies of 1x55 - every one cached - as "far too small".
        return max(1 / 60.0, duration / 3600.0)
    return 0.75 if meta.get("type") == "episode" else 2.0


def _rejection_summary(rejected):
    if not rejected:
        return ""
    parts = ["%d %s" % (count, reason)
             for reason, count in sorted(rejected.items(),
                                         key=lambda kv: -kv[1])[:3]]
    return " (dropped: %s)" % ", ".join(parts)


def all_sources(meta):
    """Everything that passed the filters, for the "show all" action.

    Deliberately without the debrid re-check. This is called the moment the
    picker opens, which is seconds after the search that set those flags, and
    re-asking the service about every source is not free: on a film with a
    hundred sources it held the picker on a spinner for twenty-six seconds
    while it confirmed what it had just been told.

    The re-check exists so a flag can come *down* between a cached search
    result and playback, and it still runs where that matters - on the way
    into `find`, which is what playback uses. Being a few seconds stale in a
    list somebody is reading is not the same risk as being stale in the
    source about to be opened.
    """
    return _ranked(meta, prefetch=True)


def invalidate(meta=None):
    if meta:
        cache.volatile_delete(cache_key(meta))
        cache.volatile_delete(unfiltered_key(meta))
        cache.volatile_delete(filter_key(meta))
    else:
        cache.volatile_delete_prefix("sources|")


# A cour is at most about twenty-six episodes. A TMDB season longer than that
# has folded several of them together - Re:Zero's "Season 1" is 85 - and its
# numbering matches no index's.
FOLDED_SEASON = 26


BREAK_DAYS = 90


def _folded(meta):
    """Has TMDB put more than one broadcast run into this season?

    Longer than a cour says so. So does a break: Hell's Paradise season one
    is 25 episodes - thirteen in 2023, twelve in 2026 - which is under the
    length rule, so "1x22" was asked only at TMDB's address, found four
    files from a name index, and the Kitsu address that has it as episode 9
    of the second season was never tried because something had come back.
    Only a break *before* this episode counts: episodes before it are where
    TMDB's own address is right.
    """
    if int(meta.get("season_episodes") or 0) > FOLDED_SEASON:
        return True
    import datetime
    aired = (meta.get("item") or {}).get("premiered") or ""
    if not aired:
        return False
    try:
        dates = sorted(datetime.date(*map(int, day[:10].split("-")))
                       for day in _season_dates(meta) if day and day <= aired)
    except ValueError:
        return False
    return any((later - earlier).days > BREAK_DAYS
               for earlier, later in zip(dates, dates[1:]))


def _names(meta, title):
    """The names to look an anime up under on Kitsu, likeliest first.

    Kitsu's search cannot find "Re:ZERO -Starting Life in Another World-" -
    it answers with an unrelated show - and finds the romaji name at once,
    so the anime engine's other names are asked too.
    """
    names = [title] + list(meta.get("aliases") or [])[:2]
    return [name for i, name in enumerate(names)
            if name and name not in names[:i]]


def _first(names, lookup):
    for name in names:
        found = lookup(name)
        if found:
            return found
    return None


def _season_dates(meta):
    """When each episode of this TMDB season aired - a cached lookup."""
    from ..meta import tmdb
    show = (meta.get("ids") or {}).get("tmdb")
    if not show:
        return []
    try:
        return [episode.get("premiered") or ""
                for episode in tmdb.episodes(show, meta.get("season")) or []]
    except Exception:
        return []


def _numbered_absolutely(meta):
    """Does TMDB already count this season's episodes from the first?

    Naruto Shippuden's season 3 is episodes 54 to 71, so "3x55" is episode
    55 of the series - `tmdb.absolute_episode` saw that and left the number
    alone, which is what `absolute == episode` past season one says.
    """
    season = int(meta.get("season") or 0)
    return season > 1 and int(meta.get("absolute") or 0) == int(
        meta.get("episode") or -1)


def _anime_address(meta, mode="arc"):
    """The same episode, addressed the way an anime index files it.

    Every provider keyed on an IMDb id asks for `imdb:season:episode`, and for
    anime that address is very often empty even when the episode is widely
    available - TMDB counts a whole multi-year arc as one season, and the
    trackers count each cour from one. Bleach 2x46 measured against Torrentio:
    nothing for `tt0434665:2:46`, nine sources for `kitsu:49444:6`.

    Two shapes, and they are asked at different times because they are worth
    different amounts:

    * `"arc"` - TMDB gave the season a name, which means it folded several
      broadcast runs into one and its address is *systematically* wrong. Asked
      alongside the ordinary one, in the same round.
    * `"season"` - the plain shape, one TMDB season to one broadcast run.
      TMDB's address usually works here, and measured on KonoSuba S03E05 both
      return the same 39 sources, so this is only asked when the first
      question came back with nothing at all.

    Returns a copy of `meta` carrying a kitsu id, or None.
    """
    if meta.get("type") != "episode" or (meta.get("ids") or {}).get("kitsu"):
        return None
    # Anime only. Everything below costs a Kitsu lookup, and that is not worth
    # spending on a show whose numbering nobody disagrees about.
    if not (meta.get("extra") or {}).get("anime"):
        return None

    title = meta.get("search_title") or meta.get("title") or ""
    try:
        from ..meta import kitsu
        if not kitsu.available():
            return None
        if mode == "arc":
            air_date = (meta.get("item") or {}).get("premiered") or ""
            found = None
            names = _names(meta, title)
            if _numbered_absolutely(meta):
                found = _first(names, lambda name: kitsu.series_address(
                    name, meta.get("year"), meta.get("absolute")))
            if not found and meta.get("season_name"):
                found = kitsu.episode_address(
                    title, meta.get("episode"), meta.get("season_name"),
                    meta.get("season_episodes") or 0,
                    already_absolute=_numbered_absolutely(meta),
                    air_date=air_date)
            if not found and _folded(meta):
                found = (_first(names, lambda name: kitsu.series_address(
                    name, meta.get("year"), meta.get("absolute")))
                    or _first(names, lambda name: kitsu.air_date_address(
                        name, air_date, _season_dates(meta))))
            if not found:
                return None
        else:
            found = kitsu.season_address(
                [title, meta.get("original_title") or ""],
                meta.get("season"), meta.get("episode"),
                meta.get("season_counts") or [])
            # Asked only once the first search found nothing, so a season
            # that works at TMDB's address is never moved off it. My Hero
            # Academia: Vigilantes is one TMDB season of two cours - "1x24"
            # is released as "2x11" - short enough not to look folded.
            if not found:
                air_date = (meta.get("item") or {}).get("premiered") or ""
                found = _first(_names(meta, title),
                               lambda name: kitsu.air_date_address(
                                   name, air_date, _season_dates(meta)))
    except Exception:
        kodi.log_exception("could not look up an anime address")
        return None
    if not found:
        return None

    kitsu_id, episode = found
    kodi.log("%s S%02dE%02d is also kitsu:%s:%s"
             % (meta.get("title", ""), int(meta.get("season") or 0),
                int(meta.get("episode") or 0), kitsu_id, episode))
    address = dict(meta)
    address["ids"] = dict(meta.get("ids") or {}, kitsu=kitsu_id)
    # The kitsu address carries its own numbering, and stream_id prefers an
    # IMDb id when it sees one - so the IMDb id has to go, or this asks the
    # identical question a second time.
    address["ids"].pop("imdb", None)
    address["episode"] = episode
    # A season that is folded only because it has a break in it - no name,
    # not long, not counted from the first - is asked at both addresses,
    # because which one the trackers use varies by show: Hell's Paradise
    # 1x22 has nothing at TMDB's and thirty-six at Kitsu's, Snow White with
    # the Red Hair 1x21 has ten at TMDB's and five at Kitsu's.
    address["alongside"] = (
        mode == "arc" and not meta.get("season_name")
        and not _numbered_absolutely(meta)
        and int(meta.get("season_episodes") or 0) <= FOLDED_SEASON)
    return address

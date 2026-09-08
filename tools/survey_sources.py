# -*- coding: utf-8 -*-
"""Ask a thousand titles what they can actually offer, and find the gaps.

The unit suite proves the pipeline is correct on the cases somebody thought
of. This asks a different question: across a wide, deliberately awkward slice
of the catalogue, **how many sources does each title have and how good is the
best Hebrew subtitle for it** - and which titles come back with nothing.

The sample is stratified rather than random, and that is the whole point. A
thousand titles drawn from "trending" would be a thousand recent English
blockbusters, every one of which works, and would prove nothing. The strata
below are chosen because each one is a way the pipeline has broken before or
could plausibly break: an anime episode numbered absolutely, an episode that
aired three days ago, a film nobody has seeded since 2009, a season-zero
special, an Israeli title whose only subtitle is the audio.

Usage:

    python tools/survey_sources.py --count 1000
    python tools/survey_sources.py --report

Results are appended to `survey.jsonl` one line at a time, so the run can be
stopped and resumed and a crash costs nothing. `--report` reads that file and
says what it found; it needs no network and can be run while a survey is
still going.

Two deliberate choices about what this does *not* do:

* **The debrid services are not asked**, unless `--debrid` says so. Asking
  costs one batched request per title, and a thousand of those against an
  account with a 300-a-minute limit is a poor way to treat somebody's
  subscription for a number - "how many sources exist" - that does not depend
  on the answer. With it off, `kept` means "passed the quality filters".
* **Its own cache directory**, so a survey never evicts the rows and sources
  a real Kodi has warmed, and so a second run is fast rather than free.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "plugin.video.katan")
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ADDON, "resources", "lib"))

OUT = os.path.join(ROOT, "survey.jsonl")
PROFILE = os.path.join(ROOT, ".kodi-test", "portable_data", "userdata",
                       "addon_data", "plugin.video.katan", "settings.xml")
WORK = os.path.join(ROOT, ".survey")

_print_lock = threading.Lock()
_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def say(text):
    with _print_lock:
        print(text, file=_out)
        _out.flush()


# --------------------------------------------------------------------------
# setting the add-on up outside Kodi
# --------------------------------------------------------------------------


def boot(use_debrid=False):
    """Load the add-on with the test profile's keys and its own cache."""
    import xml.etree.ElementTree as ET

    import xbmcaddon  # noqa: F401  - registers the stub
    from katan import kodi, settings

    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    kodi.profile_path = lambda: WORK

    if not os.path.isfile(PROFILE):
        raise SystemExit(
            "no test profile at %s - the survey needs a TMDB key" % PROFILE)
    for node in ET.parse(PROFILE).getroot().findall("setting"):
        settings.set(node.get("id"), node.text or "")

    # Everything the providers have, not the handful the picker shows, and
    # judged on quality alone unless the debrid account is being asked.
    settings.set("sources.results", "0")
    settings.set("cache.max_mb", "400")
    if not use_debrid:
        settings.set("cached_only", "false")
        settings.set("sources.cached_only", "false")

    from katan.meta import tmdb
    if not tmdb.has_key():
        raise SystemExit("the test profile has no TMDB key")
    return settings


# --------------------------------------------------------------------------
# the sample
#
# Each stratum is a way this has broken before or could plausibly break. The
# weights are how many of a thousand go to each, and they are not equal on
# purpose: the awkward corners are worth more attention than the mainstream,
# which is the part already known to work.
# --------------------------------------------------------------------------

GENRE_ANIMATION = 16

STRATA = [
    # (name, share of the sample, builder)
    ("trending films", 0.06, lambda n: _films_from("trending", n)),
    ("popular films", 0.05, lambda n: _films_from("popular", n)),
    ("in cinemas now", 0.05, lambda n: _films_from("now_playing", n)),
    ("not yet released", 0.03, lambda n: _films_from("upcoming", n)),
    ("top rated films", 0.05, lambda n: _films_from("top_rated", n)),
    ("films of the 1970s", 0.04, lambda n: _films_by_decade(1970, n)),
    ("films of the 1980s", 0.04, lambda n: _films_by_decade(1980, n)),
    ("films of the 1990s", 0.04, lambda n: _films_by_decade(1990, n)),
    ("films of the 2000s", 0.04, lambda n: _films_by_decade(2000, n)),
    ("the long tail", 0.07, lambda n: _long_tail_films(n)),
    ("israeli films", 0.05, lambda n: _israeli_films(n)),
    ("anime films", 0.04, lambda n: _anime_films(n)),

    ("first episodes", 0.08, lambda n: _episodes_from("popular", n, "first")),
    ("mid-season episodes", 0.06,
     lambda n: _episodes_from("top_rated", n, "middle")),
    ("the latest episode", 0.09, lambda n: _episodes_from("on_the_air", n,
                                                          "latest")),
    ("episodes airing today", 0.05,
     lambda n: _episodes_from("airing_today", n, "latest")),
    ("specials, season zero", 0.04, lambda n: _episodes_from("popular", n,
                                                             "special")),
    ("anime episodes", 0.07, lambda n: _anime_episodes(n)),
    ("israeli episodes", 0.05, lambda n: _israeli_episodes(n)),
]


# A second sample, all episodes, for the question "does the address problem
# happen anywhere else". Anime is weighted heavily because that is where TMDB
# and the trackers disagree about numbering, and because it is what gets
# watched here. Foreign-language shows are next, because they are the other
# place a name-based lookup has nothing to work with. The rest is drawn from
# ordinary listings so the two awkward groups have something to be compared
# against rather than being measured on their own.
EPISODE_MIX = [
    ("anime episodes", 0.30, lambda n: _anime_episodes(n)),
    ("foreign-language episodes", 0.20, lambda n: _foreign_episodes(n)),
    ("first episodes", 0.13, lambda n: _episodes_from("popular", n, "first")),
    ("mid-season episodes", 0.12,
     lambda n: _episodes_from("top_rated", n, "middle")),
    ("the latest episode", 0.13,
     lambda n: _episodes_from("on_the_air", n, "latest")),
    ("episodes airing today", 0.07,
     lambda n: _episodes_from("airing_today", n, "latest")),
    ("specials, season zero", 0.05,
     lambda n: _episodes_from("popular", n, "special")),
]

# Turkish, Spanish, Korean, Hindi, Japanese live action, French, Portuguese,
# German, Italian, Polish. Which ones hardly matters, as long as none of them
# is English and the titles are not all in one script.
FOREIGN_LANGUAGES = ("tr", "es", "ko", "hi", "fr", "pt", "de", "it", "pl", "th")


def _foreign_episodes(wanted):
    """One episode each from shows made outside the English-speaking world."""
    from katan.meta import tmdb

    picked = []
    per_language = max(1, (wanted // len(FOREIGN_LANGUAGES)) + 1)
    for language in FOREIGN_LANGUAGES:
        if len(picked) >= wanted:
            break

        def fetch(page, language=language):
            return tmdb.discover("tv", page=page,
                                 with_original_language=language,
                                 sort_by="popularity.desc",
                                 **{"vote_count.gte": "10"})

        for row in _pages(fetch, per_language * 3):
            if len(picked) >= wanted:
                break
            # A mix of positions, because "the newest episode" and "the first
            # episode" fail for different reasons and a survey of only one of
            # them answers half the question.
            which = ("first", "middle", "latest")[len(picked) % 3]
            entry = _pick_episode(row, which)
            if entry:
                entry["note"] = "foreign:%s" % language
                picked.append(entry)
    return picked


def _pages(fetch, wanted, per_page=20):
    """Walk pages of a TMDB listing until there are enough rows."""
    rows = []
    page = 1
    while len(rows) < wanted and page <= 25:
        try:
            batch = fetch(page) or []
        except Exception:
            break
        if not batch:
            break
        rows.extend(batch)
        page += 1
    return rows[:wanted]


def _films_from(listing, wanted):
    from katan.meta import tmdb
    calls = {
        "trending": lambda page: tmdb.trending("movie", "week", page),
        "popular": lambda page: tmdb.popular("movie", page),
        "now_playing": tmdb.now_playing,
        "upcoming": tmdb.upcoming,
        "top_rated": lambda page: tmdb.top_rated("movie", page),
    }
    return [_film(row) for row in _pages(calls[listing], wanted)]


def _films_by_decade(first_year, wanted):
    from katan.meta import tmdb

    def fetch(page):
        return tmdb.discover(
            "movie", page=page,
            **{"primary_release_date.gte": "%d-01-01" % first_year,
               "primary_release_date.lte": "%d-12-31" % (first_year + 9),
               "sort_by": "popularity.desc", "vote_count.gte": "50"})
    return [_film(row) for row in _pages(fetch, wanted)]


def _long_tail_films(wanted):
    """Deep pages of the popularity ranking: films nobody is seeding.

    This is where "no sources at all" should live if it lives anywhere, and
    it is the honest test of whether the add-on degrades or simply fails.
    """
    from katan.meta import tmdb

    def fetch(page):
        return tmdb.discover("movie", page=200 + page,
                             sort_by="popularity.desc",
                             **{"vote_count.gte": "20"})
    return [_film(row) for row in _pages(fetch, wanted)]


def _israeli_films(wanted):
    from katan.meta import tmdb
    return [_film(row) for row in
            _pages(lambda page: tmdb.by_original_language("he", "movie", page),
                   wanted)]


def _anime_films(wanted):
    from katan.meta import tmdb

    def fetch(page):
        return tmdb.discover("movie", page=page,
                             with_genres=str(GENRE_ANIMATION),
                             with_original_language="ja",
                             sort_by="popularity.desc")
    return [_film(row) for row in _pages(fetch, wanted)]


def _film(row):
    ids = row.get("ids") or {}
    return {"kind": "movie", "tmdb": str(ids.get("tmdb") or ""),
            "title": row.get("title", ""), "year": row.get("year", 0)}


def _shows_from(listing, wanted):
    from katan.meta import tmdb
    calls = {
        "popular": lambda page: tmdb.popular("tv", page),
        "top_rated": lambda page: tmdb.top_rated("tv", page),
        "on_the_air": tmdb.on_the_air,
        "airing_today": tmdb.airing_today,
    }
    return _pages(calls[listing], wanted)


def _episodes_from(listing, wanted, which):
    """One episode per show, chosen for the shape being tested."""
    shows = _shows_from(listing, wanted * 2)
    picked = []
    for row in shows:
        if len(picked) >= wanted:
            break
        entry = _pick_episode(row, which)
        if entry:
            picked.append(entry)
    return picked


def _anime_episodes(wanted):
    from katan.meta import tmdb

    def fetch(page):
        return tmdb.discover("tv", page=page,
                             with_genres=str(GENRE_ANIMATION),
                             with_original_language="ja",
                             sort_by="popularity.desc")
    shows = _pages(fetch, wanted * 2)
    picked = []
    for row in shows:
        if len(picked) >= wanted:
            break
        entry = _pick_episode(row, "latest")
        if entry:
            entry["note"] = "anime"
            picked.append(entry)
    return picked


def _israeli_episodes(wanted):
    from katan.meta import tmdb
    shows = _pages(lambda page: tmdb.by_original_language("he", "tv", page),
                   wanted * 2)
    picked = []
    for row in shows:
        if len(picked) >= wanted:
            break
        entry = _pick_episode(row, "first")
        if entry:
            picked.append(entry)
    return picked


def _pick_episode(show_row, which):
    """Choose one episode of a show: the first, a middle one, the newest, or
    a special.

    "latest" is the case that has actually bitten - an episode that aired days
    ago, which the aggregators may not have indexed yet and for which no
    subtitle can exist. "special" is season zero, which most code forgets.
    """
    from katan.meta import tmdb

    ids = show_row.get("ids") or {}
    tmdb_id = str(ids.get("tmdb") or "")
    if not tmdb_id:
        return None
    try:
        seasons = tmdb.seasons(tmdb_id) or []
    except Exception:
        return None
    if not seasons:
        return None

    # TMDB lists a season the moment it is announced, with no air date and no
    # episodes in it. Sampling from one of those and calling the result a miss
    # is measuring nothing: KONOSUBA season 4 is exactly that, and its
    # "S04E01" found no sources because there is no episode.
    seasons = [s for s in seasons
               if (s.get("extra") or {}).get("episode_count")
               and s.get("premiered")] or seasons

    if which == "special":
        # tmdb.seasons hides season zero unless it is all there is, so ask the
        # show for it directly.
        season, episode = 0, 1
        try:
            if not tmdb.episodes(tmdb_id, 0):
                return None
        except Exception:
            return None
    elif which == "first":
        season, episode = seasons[0]["season"], 1
    elif which == "middle":
        chosen = seasons[len(seasons) // 2]
        count = int((chosen.get("extra") or {}).get("episode_count") or 1)
        season, episode = chosen["season"], max(1, count // 2)
    else:                                   # latest
        chosen = seasons[-1]
        count = int((chosen.get("extra") or {}).get("episode_count") or 1)
        season, episode = chosen["season"], max(1, count)

    return {"kind": "episode", "tmdb": tmdb_id,
            "title": show_row.get("title", ""),
            "year": show_row.get("year", 0),
            "season": int(season), "episode": int(episode)}


def build_sample(count, seed=20260907):
    """The stratified sample, shuffled so a partial run is still balanced."""
    random.seed(seed)
    sample = []
    for name, share, builder in STRATA:
        wanted = max(1, int(round(count * share)))
        try:
            rows = builder(wanted) or []
        except Exception as error:
            say("  %-22s failed: %s" % (name, str(error)[:70]))
            rows = []
        for row in rows:
            row["stratum"] = name
        say("  %-22s %d" % (name, len(rows)))
        sample.extend(rows)

    seen = set()
    unique = []
    for row in sample:
        key = (row["kind"], row["tmdb"], row.get("season"), row.get("episode"))
        if key in seen or not row["tmdb"]:
            continue
        seen.add(key)
        unique.append(row)
    random.shuffle(unique)
    return unique


# --------------------------------------------------------------------------
# asking one title what it has
# --------------------------------------------------------------------------


def _has_aired(air_date):
    """True when the date is in the past, and True when there is no date.

    No date is not evidence of the future - plenty of real episodes carry
    none - so the benefit of the doubt goes to "this exists".
    """
    if not air_date:
        return True
    try:
        return air_date <= time.strftime("%Y-%m-%d")
    except Exception:
        return True


def _is_latin(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    latin = sum(1 for c in letters if ord(c) < 0x250)
    return latin * 2 >= len(letters)


def examine(entry):
    """Everything one title can tell us, as a flat record."""
    from katan import play
    from katan.sources import aggregator, model, scoring
    from katan.subs import outlook

    started = time.time()
    record = dict(entry)
    record["error"] = ""

    try:
        meta = play.build_meta({
            "type": entry["kind"], "tmdb": entry["tmdb"],
            "season": entry.get("season", 0),
            "episode": entry.get("episode", 0),
        })
    except Exception as error:
        record["error"] = "meta: %s" % str(error)[:90]
        return record

    record["resolved_title"] = meta.get("title", "")
    record["original_title"] = meta.get("original_title", "")
    record["imdb"] = (meta.get("ids") or {}).get("imdb", "")
    record["anime"] = bool((meta.get("extra") or {}).get("anime"))

    # An episode that has not gone out yet has no sources for a good reason,
    # and counting it as a miss would bury the real ones. The pilot found Ted
    # Lasso s04e10 and Reacher s04e08 in that state - TMDB lists a whole
    # ordered season the moment it is announced.
    item = meta.get("item") or {}
    record["air_date"] = item.get("premiered") or ""
    record["aired"] = _has_aired(record["air_date"])

    # A title written in a script no release is ever named in. Providers are
    # asked by IMDb id where they can be, so this only bites the ones that
    # fall back to a name - and it is worth being able to see that it did.
    record["latin_title"] = _is_latin(meta.get("title", ""))
    if not meta.get("title"):
        record["error"] = "TMDB gave no title"
        record["ms"] = int((time.time() - started) * 1000)
        return record

    try:
        providers = aggregator._enabled_providers(meta)
        record["providers"] = [name for name, _module in providers]
        raw = aggregator._run_providers(providers, meta, quiet=True)
        merged = model.dedupe(raw)
        record["found_raw"] = len(raw)
        record["found"] = len(merged)
        per = {}
        for source in raw:
            per[source.get("provider")] = per.get(source.get("provider"), 0) + 1
        record["per_provider"] = per

        # The whole point of this run. `raw` above is what the address TMDB
        # gives us returns; this is what the address the anime indexes file it
        # under returns. Recording them apart is the only way to say whether
        # the second one is earning its request, and on what.
        record["found_tmdb_address"] = len(merged)
        record["kitsu_address"] = ""
        record["found_kitsu_address"] = 0
        also = aggregator._anime_address(meta)
        if also:
            record["kitsu_address"] = "kitsu:%s:%s" % (
                (also.get("ids") or {}).get("kitsu"), also.get("episode"))
            by_id = [(n, m) for n, m in providers
                     if not getattr(m, "BY_NAME", False)]
            extra = aggregator._run_providers(by_id, also, quiet=True)
            record["found_kitsu_address"] = len(model.dedupe(extra))
            merged = model.dedupe(raw + extra)
            record["found"] = len(merged)
    except Exception as error:
        record["error"] = "sources: %s" % str(error)[:90]
        record["ms"] = int((time.time() - started) * 1000)
        return record

    try:
        subs = outlook.candidates(meta)
        record["subtitle_candidates"] = len(subs)
        outlook.annotate(meta, merged, subs)
        best = 0
        embedded = 0
        for source in merged:
            if source.get("subs_kind") == outlook.EMBEDDED:
                embedded += 1
            best = max(best, int(source.get("subs_score") or 0))
        record["best_subtitle"] = best
        record["claims_embedded_hebrew"] = embedded
    except Exception as error:
        record["error"] = "subtitles: %s" % str(error)[:90]
        record["subtitle_candidates"] = -1
        record["best_subtitle"] = -1

    try:
        kept, rejected = scoring.rank_all(merged, meta,
                                          aggregator._runtime_hours(meta))
        record["kept"] = len(kept)
        record["rejected"] = rejected
        record["best_quality"] = kept[0].get("quality") if kept else ""
        record["best_subtitle_kept"] = max(
            [int(s.get("subs_score") or 0) for s in kept] or [0])
    except Exception as error:
        record["error"] = "ranking: %s" % str(error)[:90]

    record["ms"] = int((time.time() - started) * 1000)
    return record


# --------------------------------------------------------------------------
# running it
# --------------------------------------------------------------------------


def already_done(path):
    done = set()
    if not os.path.isfile(path):
        return done
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            done.add((row.get("kind"), row.get("tmdb"),
                      row.get("season"), row.get("episode")))
    return done


def run(count, workers, use_debrid, path):
    boot(use_debrid)
    say("building the sample")
    sample = build_sample(count)
    say("%d titles in the sample" % len(sample))

    done = already_done(path)
    todo = [row for row in sample
            if (row["kind"], row["tmdb"], row.get("season"),
                row.get("episode")) not in done]
    say("%d already surveyed, %d to go" % (len(sample) - len(todo), len(todo)))
    if not todo:
        return

    write_lock = threading.Lock()
    counter = {"n": 0}
    handle = io.open(path, "a", encoding="utf-8", newline="")

    def worker(queue):
        while True:
            try:
                entry = queue.pop()
            except IndexError:
                return
            try:
                record = examine(entry)
            except Exception as error:
                record = dict(entry, error="crashed: %s" % str(error)[:90])
            with write_lock:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                counter["n"] += 1
                n = counter["n"]
            if n % 10 == 0 or n < 5:
                say("  %4d/%d  %-8s %-34s found %-4s subs %-4s %s"
                    % (n, len(todo), record.get("kind", ""),
                       (record.get("resolved_title") or record.get("title")
                        or "")[:34],
                       record.get("found", "-"),
                       record.get("best_subtitle", "-"),
                       record.get("error", "")[:40]))

    queue = list(todo)
    queue.reverse()
    threads = [threading.Thread(target=worker, args=(queue,))
               for _ in range(max(1, workers))]
    for thread in threads:
        thread.daemon = True
        thread.start()
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(0.5)
    except KeyboardInterrupt:
        say("stopping - what is written so far is kept")
    finally:
        handle.close()


# --------------------------------------------------------------------------
# what it found
# --------------------------------------------------------------------------


def load(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def percentile(values, fraction):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def report(path, examples=12):
    rows = load(path)
    if not rows:
        say("nothing surveyed yet (%s)" % path)
        return
    say("=" * 78)
    say("%d titles surveyed" % len(rows))

    ok = [r for r in rows if not r.get("error")]
    broken = [r for r in rows if r.get("error")]
    say("%d completed, %d errored" % (len(ok), len(broken)))

    def block(title, subset):
        if not subset:
            return
        found = [r.get("found", 0) for r in subset]
        subs = [r.get("best_subtitle", 0) for r in subset
                if r.get("best_subtitle", -1) >= 0]
        nothing = [r for r in subset if r.get("found", 0) == 0]
        no_subs = [r for r in subset if r.get("best_subtitle", 0) <= 0]
        say("")
        say("%-26s %4d titles" % (title, len(subset)))
        say("   sources   median %-4d  p10 %-4d  p90 %-5d  none at all %d (%d%%)"
            % (percentile(found, 0.5), percentile(found, 0.1),
               percentile(found, 0.9), len(nothing),
               100 * len(nothing) // max(1, len(subset))))
        if subs:
            say("   subtitles median %-4d  p10 %-4d  p90 %-5d  none at all %d (%d%%)"
                % (percentile(subs, 0.5), percentile(subs, 0.1),
                   percentile(subs, 0.9), len(no_subs),
                   100 * len(no_subs) // max(1, len(subset))))

    block("everything", ok)
    for kind in ("movie", "episode"):
        block("all %ss" % kind, [r for r in ok if r.get("kind") == kind])

    say("")
    say("-" * 78)
    say("by stratum, worst first")
    strata = {}
    for row in ok:
        strata.setdefault(row.get("stratum", "?"), []).append(row)
    scored = []
    for name, subset in strata.items():
        nothing = sum(1 for r in subset if r.get("found", 0) == 0)
        no_subs = sum(1 for r in subset if r.get("best_subtitle", 0) <= 0)
        scored.append((nothing / float(len(subset)), name, subset, nothing,
                       no_subs))
    for _rate, name, subset, nothing, no_subs in sorted(scored, reverse=True):
        found = [r.get("found", 0) for r in subset]
        subs = [r.get("best_subtitle", 0) for r in subset]
        say("  %-24s %3d titles  sources med %-4d  no sources %2d  "
            "subs med %-4d  no subs %2d"
            % (name, len(subset), percentile(found, 0.5), nothing,
               percentile(subs, 0.5), no_subs))

    _edge_cases(ok, broken, examples)


def _edge_cases(ok, broken, examples):
    say("")
    say("=" * 78)
    say("EDGE CASES")

    def show(title, subset, extra=None):
        if not subset:
            say("")
            say("%s: none" % title)
            return
        say("")
        say("%s: %d" % (title, len(subset)))
        for row in subset[:examples]:
            line = "   %-8s %-38s" % (
                row.get("kind", ""),
                ((row.get("resolved_title") or row.get("title") or "")[:36]))
            if row.get("kind") == "episode":
                line += " s%02de%02d" % (row.get("season") or 0,
                                         row.get("episode") or 0)
            else:
                line += "         "
            line += "  %-22s" % (row.get("stratum", "")[:22])
            if extra:
                line += "  " + extra(row)
            say(line)

    missing = [r for r in ok if r.get("found", 0) == 0]
    unaired = [r for r in missing if not r.get("aired", True)]
    genuinely = [r for r in missing if r.get("aired", True)]

    show("nothing found, and it has not gone out yet", unaired,
         lambda r: "airs %s" % (r.get("air_date") or "?"))

    show("nothing found at all", genuinely,
         lambda r: "%sproviders=%s"
         % ("" if r.get("latin_title", True) else "NON-LATIN TITLE  ",
            ",".join(r.get("providers") or [])))

    show("anime numbered past its season",
         [r for r in ok if r.get("kind") == "episode"
          and (r.get("anime") or r.get("stratum") == "anime episodes")
          and (r.get("episode") or 0) > 100],
         lambda r: "asked for s%02de%02d, found %d"
         % (r.get("season") or 0, r.get("episode") or 0, r.get("found", 0)))

    show("sources but not one subtitle",
         [r for r in ok if r.get("found", 0) > 0
          and r.get("best_subtitle", 0) <= 0],
         lambda r: "%d sources, %d subtitle candidates"
         % (r.get("found", 0), r.get("subtitle_candidates", 0)))

    show("subtitles exist but none fits well",
         [r for r in ok if r.get("subtitle_candidates", 0) > 0
          and 0 < r.get("best_subtitle", 0) < 55],
         lambda r: "best %d%% from %d candidates"
         % (r.get("best_subtitle", 0), r.get("subtitle_candidates", 0)))

    show("everything was filtered out",
         [r for r in ok if r.get("found", 0) > 0 and r.get("kept", 0) == 0],
         lambda r: "%d found, dropped: %s"
         % (r.get("found", 0),
            ", ".join("%d %s" % (v, k) for k, v in
                      sorted((r.get("rejected") or {}).items(),
                             key=lambda kv: -kv[1])[:2])))

    show("only one or two sources",
         [r for r in ok if 0 < r.get("found", 0) <= 2],
         lambda r: "%d found" % r.get("found", 0))

    show("slower than fifteen seconds",
         sorted([r for r in ok if r.get("ms", 0) > 15000],
                key=lambda r: -r.get("ms", 0)),
         lambda r: "%.1f s" % (r.get("ms", 0) / 1000.0))

    show("errored", broken, lambda r: r.get("error", "")[:46])

    say("")
    say("-" * 78)
    say("provider contribution, over every title that found anything")
    totals = {}
    titles = {}
    for row in ok:
        for name, count in (row.get("per_provider") or {}).items():
            totals[name] = totals.get(name, 0) + count
            titles[name] = titles.get(name, 0) + 1
    for name in sorted(totals, key=lambda n: -totals[n]):
        say("  %-14s %7d results over %4d titles" % (name, totals[name],
                                                     titles[name]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000,
                        help="how many titles to sample (default 1000)")
    parser.add_argument("--workers", type=int, default=4,
                        help="titles examined at once (default 4)")
    parser.add_argument("--debrid", action="store_true",
                        help="also ask the debrid services what is cached")
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--report", action="store_true",
                        help="summarise what has been written so far")
    parser.add_argument("--examples", type=int, default=12)
    parser.add_argument("--episodes", action="store_true",
                        help="sample episodes only, 30%% anime and 20%% "
                             "foreign-language, rest from ordinary listings")
    args = parser.parse_args()

    if args.report:
        report(args.out, args.examples)
        return
    if args.episodes:
        global STRATA
        STRATA = EPISODE_MIX
    started = time.time()
    run(args.count, args.workers, args.debrid, args.out)
    say("done in %.1f minutes" % ((time.time() - started) / 60.0))
    report(args.out, args.examples)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Anime, films and series, measured the way a viewer meets them: real search.

    python tools/anime_survey.py --kind film --sample 300     the same for films
    python tools/anime_survey.py --kind series --sample 300   and for series

    python tools/anime_survey.py --sample 300     choose the episodes (saved)
    python tools/anime_survey.py --run            search each one, resumable
    python tools/anime_survey.py --run --retry    search again the ones a rate
                                                  limit or a deadline spoiled
    python tools/anime_survey.py --report         what it found, and the worst

The sample is a quarter each popular, old (aired before 2007), new (2022 on)
and obscure (deep in TMDB's popularity order), three random aired episodes
from random seasons of each show - because season one episode one of a hit
is the case that already works.

Each episode goes through the path the picker takes: `play.build_meta`,
`aggregator.find` and `all_sources`, the filter report, and
`outlook.split_rows` for the three subtitle lists. It runs with the settings
of the Kodi profile in `.kodi-test/` - the same worker count, deadline and
keys the device has - and records every line the add-on logs while it
searches, so a zero can be told apart from a rate limit or a deadline.
"""
import argparse
import io
import json
import os
import random
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".survey", "anime")
SAMPLE = os.path.join(WORK, "sample.json")
RESULTS = os.path.join(WORK, "results.jsonl")
PROFILE = os.path.join(ROOT, ".kodi-test", "portable_data", "userdata",
                       "addon_data", "plugin.video.pinky", "settings.xml")
sys.path.insert(0, os.path.join(ROOT, "plugin.video.pinky/resources/lib"))
sys.path.insert(0, os.path.join(ROOT, "tests/stubs"))

LOG = []
INSIDE = False
KIND = "anime"


def _use(kind):
    """Point the sample and the results at one kind's own folder."""
    global KIND, WORK, SAMPLE, RESULTS
    KIND = kind
    WORK = os.path.join(ROOT, ".survey", kind)
    SAMPLE = os.path.join(WORK, "sample.json")
    RESULTS = os.path.join(WORK, "results.jsonl")


def boot():
    import xbmcaddon
    profile = os.path.join(tempfile.gettempdir(), "pinky-anime-survey")
    os.makedirs(profile, exist_ok=True)
    xbmcaddon.reset(profile, os.path.join(ROOT, "plugin.video.pinky"))
    from pinky import kodi, settings
    if os.path.isfile(PROFILE):
        for node in ET.parse(PROFILE).getroot().findall("setting"):
            settings.set(node.get("id"), node.text or "")
    original = kodi.log

    def capture(message, *args, **kwargs):
        LOG.append(str(message))
        return original(message, *args, **kwargs)

    kodi.log = capture
    return settings


# --------------------------------------------------------------------------
# the sample
# --------------------------------------------------------------------------

BUCKETS = {
    "popular": [({"sort_by": "popularity.desc"}, [1, 2, 3])],
    "old": [({"sort_by": "vote_count.desc", "first_air_date.lte": "2006-12-31"},
             [1, 2, 3])],
    "new": [({"sort_by": "popularity.desc", "first_air_date.gte": "2022-01-01"},
             [1, 2, 3])],
    "obscure": [({"sort_by": "popularity.desc", "vote_count.gte": "3"},
                 [18, 22, 26, 30])],
}
SHOWS_PER_BUCKET = 25
EPISODES_PER_SHOW = 3


def _aired(date):
    return bool(date) and date <= time.strftime("%Y-%m-%d")


def _weeks_ago(days):
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))


# Films: one each. The popular and new ones stop 45 days back, because a film
# still in cinemas exists only as a camera recording, which this refuses by
# design - that would measure a setting, not the search.
FILM_BUCKETS = {
    "popular": [({"sort_by": "popularity.desc",
                  "primary_release_date.lte": _weeks_ago(45)}, [1, 2, 3, 4])],
    "old": [({"sort_by": "vote_count.desc",
              "primary_release_date.lte": "2006-12-31"}, [1, 2, 3, 4, 5, 6])],
    "new": [({"sort_by": "popularity.desc", "primary_release_date.gte": "2022-01-01",
              "primary_release_date.lte": _weeks_ago(45)}, [5, 6, 7, 8, 9, 10])],
    "obscure": [({"sort_by": "popularity.desc", "vote_count.gte": "20"},
                 [40, 50, 60, 70])],
}


def build_film_sample(count, seed=20260929):
    from pinky.meta import tmdb
    rng = random.Random(seed)
    wanted = max(1, count // len(FILM_BUCKETS))
    sample, seen = [], set()
    for bucket, queries in FILM_BUCKETS.items():
        rows = []
        for filters, pages in queries:
            for page in pages:
                rows += tmdb.discover("movie", page=page, **filters)
        rng.shuffle(rows)
        taken = 0
        for row in rows:
            tmdb_id = str((row.get("ids") or {}).get("tmdb") or "")
            if not tmdb_id or tmdb_id in seen or taken >= wanted:
                continue
            seen.add(tmdb_id)
            taken += 1
            sample.append({"id": tmdb_id, "bucket": bucket, "tmdb": tmdb_id,
                           "title": row.get("title", ""), "year": row.get("year", 0),
                           "season": 0, "episode": 0})
    os.makedirs(WORK, exist_ok=True)
    with io.open(SAMPLE, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=0)
    print("%d films -> %s" % (len(sample), SAMPLE))


def build_sample(count, seed=20260928):
    from pinky.meta import tmdb
    if KIND == "film":
        return build_film_sample(count)
    # Series are the anime sample's shape with the anime taken out.
    scope = ({"with_genres": "16", "with_original_language": "ja"}
             if KIND == "anime" else {"without_genres": "16"})
    rng = random.Random(seed)
    per_show = EPISODES_PER_SHOW
    shows_wanted = max(1, count // per_show // len(BUCKETS))
    sample, seen = [], set()
    for bucket, queries in BUCKETS.items():
        rows = []
        for filters, pages in queries:
            for page in pages:
                rows += tmdb.discover("tv", page=page, **dict(scope, **filters))
        rng.shuffle(rows)
        taken = 0
        for row in rows:
            tmdb_id = str((row.get("ids") or {}).get("tmdb") or "")
            if not tmdb_id or tmdb_id in seen or taken >= shows_wanted:
                continue
            seasons = [s for s in (tmdb.seasons(tmdb_id) or [])
                       if int(s.get("season") or 0) >= 1
                       and (s.get("extra") or {}).get("episode_count")]
            aired = []
            for season in seasons:
                for episode in tmdb.episodes(tmdb_id, season["season"]) or []:
                    if _aired(episode.get("premiered") or ""):
                        aired.append((int(season["season"]),
                                      int(episode.get("episode") or 0)))
            aired = [pair for pair in aired if pair[1] > 0]
            if not aired:
                continue
            seen.add(tmdb_id)
            taken += 1
            for season, episode in rng.sample(aired, min(per_show, len(aired))):
                sample.append({"id": "%s-%d-%d" % (tmdb_id, season, episode),
                               "bucket": bucket, "tmdb": tmdb_id,
                               "title": row.get("title", ""),
                               "year": row.get("year", 0),
                               "season": season, "episode": episode})
            print("%-8s %-40s %d episodes" % (bucket, row.get("title", "")[:40],
                                              len(aired)))
    os.makedirs(WORK, exist_ok=True)
    with io.open(SAMPLE, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=0)
    print("%d episodes from %d shows -> %s" % (len(sample), len(seen), SAMPLE))


# --------------------------------------------------------------------------
# one episode
# --------------------------------------------------------------------------

def examine(entry):
    from pinky import play
    from pinky.sources import aggregator
    from pinky.subs import outlook

    del LOG[:]
    record = dict(entry)
    started = time.time()
    try:
        if entry.get("season"):
            meta = play.build_meta({"type": "episode", "tmdb": entry["tmdb"],
                                    "season": entry["season"],
                                    "episode": entry["episode"]})
        else:
            meta = play.build_meta({"type": "movie", "tmdb": entry["tmdb"]})
        record["absolute"] = meta.get("absolute")
        record["imdb"] = (meta.get("ids") or {}).get("imdb", "")
        record["anime_flag"] = bool((meta.get("extra") or {}).get("anime"))
        shown = aggregator.find(meta)
        full = aggregator.all_sources(meta)
        report = aggregator.filter_report(meta) or {}
        record["found"] = int(report.get("found") or 0)
        record["kept"] = len(full)
        record["shown"] = len(shown)
        record["cached"] = sum(1 for s in full if s.get("cached"))
        record["rejected"] = report.get("reasons") or []
        per = {}
        for source in full:
            per[source.get("provider")] = per.get(source.get("provider"), 0) + 1
        record["per_provider"] = per
        record["example"] = [s.get("title") for s in full[:3]]
        from pinky.sources import scoring
        raw = aggregator.cache.volatile_get(aggregator.unfiltered_key(meta)) or []
        record["other_series"] = [s.get("title") for s in raw
                                  if scoring._a_different_series(s, meta)][:8]

        if INSIDE:
            # What the picker learns in the background: the tracks inside
            # the first two cached files. Remembered the same way, so the
            # lists below count them exactly as a second open would.
            from pinky.sources import bundled
            record["inside"] = []
            for source in [s for s in full if s.get("cached")][:2]:
                languages = bundled._look_inside(dict(source))
                bundled._remember_inside_for(source)(languages)
                record["inside"].append(languages)
        native, llm, english = outlook.split_rows(meta, full)
        record["rows"] = [len(native), len(llm), len(english)]
        record["best_fit"] = [max([r.get("subs_fit") or 0 for r in rows] or [0])
                              for rows in (native, llm, english)]
        he = outlook.candidates(meta)
        ai = outlook.translation_candidates(meta)
        en = outlook.english_candidates(meta, ai)
        record["candidates"] = [len(he), len(ai), len(en)]
    except Exception as error:
        record["error"] = "%s: %s" % (type(error).__name__, str(error)[:120])
    record["ms"] = int((time.time() - started) * 1000)
    record["log"] = [line for line in LOG
                     if any(word in line for word in
                            ("deadline", "rate limit", "429", "kitsu",
                             "no sources", "cannot", "refus", "failed"))][:12]
    record["rate_limited"] = any("rate limit" in line for line in LOG)
    record["deadline"] = any("deadline hit" in line for line in LOG)
    return record


def _done(path):
    done = {}
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                done[row["id"]] = row
    return done


def run(limit=0, retry=False, pause=6.0):
    with io.open(SAMPLE, encoding="utf-8") as handle:
        sample = json.load(handle)
    done = _done(RESULTS)
    todo = []
    for entry in sample:
        old = done.get(entry["id"])
        if old is None or (retry and (old.get("rate_limited") or old.get("deadline")
                                      or old.get("error"))):
            todo.append(entry)
    if limit:
        todo = todo[:limit]
    print("%d to search" % len(todo))
    for index, entry in enumerate(todo, 1):
        record = examine(entry)
        with io.open(RESULTS, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print("%3d/%d %-7s %-32s %2dx%-4d found %3s kept %3s cached %3s rows %s %s%s"
              % (index, len(todo), entry["bucket"], entry["title"][:32],
                 entry["season"], entry["episode"], record.get("found", "-"),
                 record.get("kept", "-"), record.get("cached", "-"),
                 record.get("rows", "-"),
                 " RATE" if record.get("rate_limited") else "",
                 " DEADLINE" if record.get("deadline") else ""))
        sys.stdout.flush()
        time.sleep(pause)


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

def report(worst=25):
    rows = list(_done(RESULTS).values())
    if not rows:
        print("nothing surveyed yet")
        return
    ok = [r for r in rows if not r.get("error")]

    def pct(part, whole):
        return "%5.1f%%" % (100.0 * part / whole) if whole else "   -  "

    def median(values):
        values = sorted(values)
        return values[len(values) // 2] if values else 0

    print("%d episodes surveyed, %d errors, %d rate-limited, %d hit the deadline"
          % (len(rows), len(rows) - len(ok),
             sum(1 for r in rows if r.get("rate_limited")),
             sum(1 for r in rows if r.get("deadline"))))
    print()
    probed = [r for r in ok if r.get("inside") is not None]
    if probed:
        english_inside = sum(1 for r in probed if any(
            langs and "en" in langs for langs in r["inside"]))
        print("tracks inside the file: %d episodes probed, English inside %s"
              % (len(probed), pct(english_inside, len(probed))))
    print("%-8s %4s  %8s %8s %8s  %8s  %8s %8s %8s  %s"
          % ("bucket", "n", ">=1 kept", ">=5 kept", "cached", "median",
             "Hebrew", "AI", "English", "no subtitle row"))
    for bucket in ("popular", "old", "new", "obscure", "ALL"):
        part = [r for r in ok if bucket == "ALL" or r["bucket"] == bucket]
        n = len(part)
        if not n:
            continue
        print("%-8s %4d  %8s %8s %8s  %8d  %8s %8s %8s  %s" % (
            bucket, n,
            pct(sum(1 for r in part if r.get("kept")), n),
            pct(sum(1 for r in part if (r.get("kept") or 0) >= 5), n),
            pct(sum(1 for r in part if r.get("cached")), n),
            median([r.get("kept") or 0 for r in part]),
            pct(sum(1 for r in part if (r.get("rows") or [0])[0]), n),
            pct(sum(1 for r in part if (r.get("rows") or [0, 0])[1]), n),
            pct(sum(1 for r in part if (r.get("rows") or [0, 0, 0])[2]), n),
            pct(sum(1 for r in part if r.get("kept") and not any(r.get("rows") or [])), n)))
    print()
    per = {}
    for r in ok:
        for name, count in (r.get("per_provider") or {}).items():
            per.setdefault(name, [0, 0])
            per[name][0] += 1
            per[name][1] += count
    print("providers: " + ", ".join("%s on %d eps (%d)" % (k, v[0], v[1])
                                    for k, v in sorted(per.items(), key=lambda kv: -kv[1][1])))
    reasons = {}
    for r in ok:
        for reason, count in r.get("rejected") or []:
            reasons[reason] = reasons.get(reason, 0) + count
    print("rejected: " + ", ".join("%s %d" % kv for kv in
                                   sorted(reasons.items(), key=lambda kv: -kv[1])))
    print()
    print("worst: nothing kept")
    for r in [r for r in ok if not r.get("kept")][:worst]:
        print("  %-7s %-34s %2dx%-4d abs=%-4s found=%-3s %s | %s" % (
            r["bucket"], r["title"][:34], r["season"], r["episode"],
            r.get("absolute"), r.get("found"), r.get("rejected"),
            " / ".join(r.get("log") or [])[:160]))
    print()
    print("rejected as another series (to check they really are):")
    for r in ok:
        for name in (r.get("other_series") or [])[:2]:
            print("  %-28s %2dx%-4d %s" % (r["title"][:28], r["season"], r["episode"], name[:90]))
    print()
    print("worst: sources but no subtitle row at all")
    for r in [r for r in ok if r.get("kept") and not any(r.get("rows") or [])][:worst]:
        print("  %-7s %-34s %2dx%-4d kept=%-3s cand=%s  %s" % (
            r["bucket"], r["title"][:34], r["season"], r["episode"],
            r.get("kept"), r.get("candidates"), (r.get("example") or [""])[0][:60]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--inside", action="store_true",
                        help="also read the tracks inside two cached files")
    parser.add_argument("--kind", default="anime", choices=("anime", "film", "series"))
    parser.add_argument("--results", default="",
                        help="write to this results file instead")
    args = parser.parse_args()
    global INSIDE, RESULTS
    _use(args.kind)
    INSIDE = args.inside
    if args.results:
        RESULTS = os.path.join(WORK, args.results)
    if args.report:
        report()
        return
    boot()
    if args.sample:
        build_sample(args.sample)
    if args.run:
        run(limit=args.limit, retry=args.retry)


if __name__ == "__main__":
    main()

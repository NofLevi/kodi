# -*- coding: utf-8 -*-
"""How many Hebrew dubs the picker can find, measured the same way twice.

    python tools/dub_gate.py --harvest        ask the providers, cache it
    python tools/dub_gate.py                  score the cache, print the gate
    python tools/dub_gate.py --review         the rows the net flags and the
                                              detector does not - where the
                                              next pattern comes from
    python tools/dub_gate.py --tokens         Hebrew-ish words in the corpus,
                                              by how often they appear

The point is the second run. The `heb_dub` field in `release.parse` is a pile
of spellings read off real release names, and a pile of spellings is exactly
the kind of thing that looks finished and is not. The first version missed
six of every nine dubs, because a word-bounded "dub" cannot match "HebDub",
one glued word. The second missed a plain "heb.dub", because the quantifier
in "dubbed?" makes the final "d" optional and nothing else - so the pattern
wanted "dubbe". Neither was visible by reading the pattern again. Both were
visible the moment the same corpus was scored twice.

So the corpus is **cached to disk and never re-fetched** unless asked. A
pattern change is then free to measure, which is the only way a gate ever
gets run twice - the same reasoning `tools/subtitle_gate.py` gives for
caching its own candidates.

The net is deliberately far wider than the detector: anything with Hebrew
script in it, Torrentio's Israeli flag, or any of the Latin tokens an
Israeli release tends to carry. Its false positives are the whole point - a
row the net flags and the detector misses is either a pattern to add or a
decision to write down, and --review is where they are read.
"""
import argparse
import collections
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.pinky", "resources", "lib"))
# The stubs, because `pinky.meta.tmdb` reaches `pinky.kodi` and that imports
# xbmc. Same two lines as `tools/subtitle_gate.py`, same reason.
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))

from pinky.meta.tmdb import BUNDLED_KEY as KEY          # noqa: E402

CACHE = os.path.join(ROOT, ".dub-gate", "corpus.jsonl")
TMDB = "https://api.themoviedb.org/3"
# Both of the providers that ship switched on, because they do not return the
# same rows: TorrentsDB had the only Hebrew Shrek under a Hebrew title,
# "שרק - Shrek DVDRip HebDub avi", which Torrentio did not list
# at all. A corpus from one provider is a corpus of one provider's parser.
PROVIDERS = [
    ("torrentio",
     "https://torrentio.strem.fun/sort=qualitysize/stream/movie/%s.json"),
    ("torrentsdb", "https://torrentsdb.com/stream/movie/%s.json"),
]
# A series episode is addressed imdb:season:episode, and season one episode
# one is the one that always exists. Dubbed children's *series* are far more
# of what a household actually watches than dubbed films are - a child gets
# through a hundred episodes of a cartoon and one Aladdin - and nothing here
# had ever measured one.
EPISODE_PROVIDERS = [
    ("torrentio",
     "https://torrentio.strem.fun/sort=qualitysize/stream/series/%s:1:1.json"),
    ("torrentsdb", "https://torrentsdb.com/stream/series/%s:1:1.json"),
]

IL_FLAG = u"\U0001F1EE\U0001F1F1"
HEBREW_SCRIPT = re.compile(u"[֐-׿]")
# A flag emoji is two regional indicators, so a plain substring search for
# Israel's I+L matches **across two adjacent flags**: Finland then Lithuania
# is F,I,L,T, and the I,L in the middle of it read as Israel. Torrentio lists
# a dozen flags on a multi-language release, so this flagged most of the
# Italian and Russian remuxes in the corpus and buried the real rows. Runs of
# indicators are split into pairs before any of them is compared.
_INDICATORS = re.compile(u"[\U0001F1E6-\U0001F1FF]+")


def has_il_flag(text):
    """Torrentio's Israeli flag, as a flag rather than as two characters."""
    for run in _INDICATORS.findall(text or u""):
        for at in range(0, len(run) - 1, 2):
            if run[at:at + 2] == IL_FLAG:
                return True
    return False

# Wider than the detector on purpose. See the module docstring.
NET = re.compile(
    r"(\bheb\b|hebrew|hebsub|hebdub|israel|isra|ivrit|zion|moridim|horadot"
    r"|hebits|dossinet|toonhebdub|\bdub\b|dubb|dubbed|dual|multi)", re.I)

# Only the words that could mean Hebrew, for --review and --tokens. "MULTI"
# is most of the net and says nothing on its own.
# "zion" was in here for the ZionHD group and matched inside *animazione* -
# 118 Italian rows in one corpus, every one of them noise, burying the few
# that matter. Anchored, and the group spelled out.
SMELLS_HEBREW = re.compile(
    r"(\bheb\b|hebrew|israel|\bisra\b|ivrit|\bzion\b|zionhd|moridim|horadot"
    r"|hebits|dossinet|toonhebdub)", re.I)

# Where the dubs are, which is a measurement rather than a hunch: adult
# drama and action came out at 0% over twenty titles.
STRATA = [
    ("animation", {"with_genres": "16"}),
    ("family", {"with_genres": "10751"}),
    ("disney", {"with_companies": "2"}),
    ("pixar", {"with_companies": "3"}),
    ("dreamworks", {"with_companies": "521"}),
    ("illumination", {"with_companies": "6704"}),
    ("sony-animation", {"with_companies": "2251"}),
    ("ghibli", {"with_companies": "10342"}),
    # The dubs are old, and "popular" is not. Sorting the animation strata by
    # popularity returned the last ten years twice over, while every scene
    # HebDub in the corpus is a 1990s or 2000s Disney film - so one stratum
    # asks for those directly. It is the single richest one.
    ("animation-1989-2010",
     {"with_genres": "16", "primary_release_date.gte": "1989-01-01",
      "primary_release_date.lte": "2010-12-31"}),
    # The control. Twenty titles of adult drama and action came out at 0%,
    # which is what says the whole reading is real rather than noise, so it
    # stays in the corpus rather than being remembered as a one-off.
    ("control-adult", {"with_genres": "18,28",
                       "primary_release_date.gte": "2015-01-01"}),
]
# Television, asked at season one episode one. Separate because `/discover/tv`
# and `/tv/<id>/external_ids` are different endpoints from the film ones.
TV_STRATA = [
    ("tv-animation", {"with_genres": "16"}),
    ("tv-kids", {"with_genres": "10762"}),
]
PAGES = 8


def get(url, timeout=45):
    request = urllib.request.Request(url, headers={"User-Agent": "Pinky/dub-gate"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8", "replace"))


def tmdb(path, **params):
    params["api_key"] = KEY
    return get("%s%s?%s" % (TMDB, path, urllib.parse.urlencode(params)))


def wanted_titles():
    """(tmdb id, name, stratum, kind) for every title, deduplicated."""
    seen, out = set(), []
    for strata, path, title_key, kind in (
            (STRATA, "/discover/movie", "title", "movie"),
            (TV_STRATA, "/discover/tv", "name", "series")):
        for stratum, params in strata:
            for page in range(1, PAGES + 1):
                try:
                    data = tmdb(path, sort_by="popularity.desc",
                                page=page, **params)
                except Exception as exc:
                    print("  %s page %d: %s" % (stratum, page, exc))
                    continue
                for row in data.get("results") or []:
                    key = (kind, row["id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((row["id"], row.get(title_key) or "",
                                stratum, kind))
                time.sleep(0.12)
    return out


def _already_cached():
    done = set()
    if os.path.isfile(CACHE):
        with io.open(CACHE, encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["imdb"])
                except Exception:
                    pass
    return done


def harvest():
    """Ask the providers once and keep every name worth looking at again."""
    done = _already_cached()
    titles = wanted_titles()
    print("%d titles across %d strata, %d already cached"
          % (len(titles), len(STRATA) + len(TV_STRATA), len(done)))
    folder = os.path.dirname(CACHE)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    kept = 0
    with io.open(CACHE, "a", encoding="utf-8") as handle:
        for index, (tmdb_id, name, stratum, kind) in enumerate(titles, 1):
            path = ("/tv/%s/external_ids" if kind == "series"
                    else "/movie/%s/external_ids")
            try:
                imdb = (tmdb(path % tmdb_id) or {}).get("imdb_id") or ""
            except Exception:
                continue
            if not imdb or imdb in done:
                continue
            streams, failed = [], False
            for provider, template in (EPISODE_PROVIDERS if kind == "series"
                                       else PROVIDERS):
                try:
                    answer = get(template % imdb).get("streams") or []
                except Exception as exc:
                    print("  %-34s %s: %s" % (name[:34], provider, exc))
                    failed = True
                    continue
                for stream in answer:
                    streams.append((provider, stream))
                time.sleep(0.25)
            if failed and not streams:
                continue
            # Only the rows the net flags are kept. The rest can never be a
            # Hebrew dub, and 300 names a title over 300 titles is tens of
            # megabytes to answer a question about a few hundred rows.
            rows = []
            for provider, stream in streams:
                flat = ("%s %s" % (stream.get("title") or "",
                                   stream.get("name") or "")).replace("\n", " ")
                if (NET.search(flat) or HEBREW_SCRIPT.search(flat)
                        or IL_FLAG in flat):
                    seeders = re.search(u"\U0001F464\\s*(\\d+)", flat)
                    rows.append({
                        "provider": provider,
                        "release": (stream.get("title") or "").split("\n")[0],
                        # Not truncated. Torrentio puts its flag list last
                        # and Israel last inside it, so a cap cuts exactly
                        # the flag this gate counts - the first corpus hit
                        # 300 on 102 rows and undercounted every one.
                        "flat": flat,
                        # The raw title, newlines and all. Torrentio's audio
                        # languages are a *line* of it, so flattening the
                        # newlines threw away the one thing that says those
                        # flags are audio tracks rather than subtitles.
                        "title": stream.get("title") or "",
                        "stream_name": stream.get("name") or "",
                        "seeders": int(seeders.group(1)) if seeders else 0,
                    })
            handle.write(json.dumps({
                "imdb": imdb, "name": name, "stratum": stratum, "kind": kind,
                "streams": len(streams), "rows": rows},
                ensure_ascii=False) + "\n")
            handle.flush()
            kept += 1
            done.add(imdb)
            print("  %4d/%d  %-34s %4d streams, %3d flagged"
                  % (index, len(titles), name[:34], len(streams), len(rows)))
    print("harvested %d new titles into %s" % (kept, CACHE))
    return 0


def corpus():
    if not os.path.isfile(CACHE):
        raise SystemExit("no corpus yet - run with --harvest first")
    with io.open(CACHE, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def score(records):
    """What this working tree says about every flagged row.

    Through the add-on's own code rather than a copy of its rules: the
    stream goes to `stremio._parse_stream`, which is what builds a source on
    a device, and the answer comes from `outlook._hebrew_dub_fit`. A gate
    that reimplements the thing it measures can agree with itself while
    disagreeing with the add-on, which is the one failure that would make
    every number here worthless.
    """
    from pinky.sources.providers import stremio
    from pinky.subs import outlook
    found = collections.defaultdict(list)
    for record in records:
        # Two providers return the same torrent, and the add-on merges those
        # by infohash before anything scores them - so counting both here
        # would inflate every number in the table. Keyed on the release name,
        # keeping whichever copy says more.
        best = {}
        for row in record["rows"]:
            source = stremio._parse_stream(
                {"title": row.get("title") or row["release"],
                 "name": row.get("stream_name") or "",
                 "infoHash": "0" * 40}, row.get("provider") or "torrentio")
            if source is None:
                continue
            source["seeders"] = row["seeders"]
            fit = outlook._hebrew_dub_fit(source)
            if not fit:
                continue
            key = (row["release"] or "").strip().lower()
            if key not in best or (fit, row["seeders"]) > (
                    best[key][0], best[key][1]["seeders"]):
                best[key] = (fit, row)
        if best:
            found[record["imdb"]] = list(best.values())
    return found


def _after_the_filters(records, found):
    """Which of the dubs found would actually reach the picker.

    `scoring.rejection_reason` on the shipped defaults, which is what every
    source passes through before a window ever draws it. A Hebrew dub is
    exempt from the resolution floor and the size ceiling - preferences,
    where there is no better copy that is also in Hebrew - and refused by
    the capability checks exactly like anything else, because a release the
    projector cannot decode is not a Hebrew dub, it is a stutter.
    """
    from pinky.sources import scoring
    from pinky.sources.providers import stremio

    prefs = scoring.Preferences()
    prefs.original_language = "en"
    refused = collections.Counter()
    reaching, titles, if_cached = 0, set(), set()
    for record in records:
        for _fit, row in found.get(record["imdb"], []):
            source = stremio._parse_stream(
                {"title": row.get("title") or row["release"],
                 "name": row.get("stream_name") or "",
                 "infoHash": "0" * 40}, row.get("provider") or "torrentio")
            if source is None:
                continue
            source["seeders"] = row["seeders"]
            why = scoring.rejection_reason(source, prefs)
            if not why:
                reaching += 1
                titles.add(record["imdb"])
                if_cached.add(record["imdb"])
                continue
            refused[why] += 1
            # **This gate has no debrid account, so every row looks
            # uncached** - and a dead swarm only matters to a torrent
            # somebody still has to fetch. A dub TorBox already holds plays
            # from TorBox whatever the swarm is doing, and `dub_rows` exempts
            # a cached source for exactly that reason. So the number above is
            # a floor for a real box rather than the answer.
            source["cached"] = True
            if not scoring.rejection_reason(source, prefs):
                if_cached.add(record["imdb"])
    return refused, reaching, titles, if_cached


def report(records, found):
    titles = len(records)
    if not titles:
        raise SystemExit("the corpus is empty")
    with_dub = sum(1 for r in records if found.get(r["imdb"]))
    live = sum(1 for r in records
               if any(row["seeders"] > 0
                      for _fit, row in found.get(r["imdb"], [])))
    total = sum(len(v) for v in found.values())
    tiers = collections.Counter(fit for v in found.values() for fit, _row in v)
    print("titles in corpus        : %d" % titles)
    print("streams behind them     : %d" % sum(r["streams"] for r in records))
    print("flagged rows scored     : %d" % sum(len(r["rows"]) for r in records))
    print("titles with a dub       : %d (%.0f%%)"
          % (with_dub, 100.0 * with_dub / titles))
    print("titles with a live dub  : %d (%.0f%%)"
          % (live, 100.0 * live / titles))
    print("dubbed rows found       : %d" % total)
    for fit, label in ((100, "the name says it"),
                       (90, "the provider names a Hebrew audio track"),
                       (70, "probably")):
        print("  %3d  %-40s %d" % (fit, label, tiers.get(fit, 0)))
    print("most on any one title   : %d"
          % max([len(v) for v in found.values()] or [0]))

    # **The number that actually matters**, and the one this gate did not
    # have at first: a dub the detector finds and the add-on's own filters
    # then throw away never reaches the picker, so finding it is worth
    # nothing. On the first run 64 of 91 were thrown away here.
    refused, reaching, reaching_titles, if_cached = _after_the_filters(
        records, found)
    print("\nwhat survives our own filters, on the shipped defaults:")
    print("  reaches the picker    : %d rows across %d titles (%.0f%%)"
          % (reaching, len(reaching_titles),
             100.0 * len(reaching_titles) / titles))
    for reason, count in refused.most_common():
        print("  refused: %-30s %d" % (reason, count))
    print("  on a box with TorBox  : %d titles (%.0f%%), because a dub TorBox"
          % (len(if_cached), 100.0 * len(if_cached) / titles))
    print("                          already holds needs no swarm")

    by = collections.Counter(r["stratum"] for r in records if found.get(r["imdb"]))
    seen = collections.Counter(r["stratum"] for r in records)
    print("\nby stratum:")
    for stratum, _params in STRATA + TV_STRATA:
        if seen[stratum]:
            print("  %-14s %3d titles  %3d with a dub (%3.0f%%)"
                  % (stratum, seen[stratum], by[stratum],
                     100.0 * by[stratum] / seen[stratum]))


def review(records, found):
    print("\n--- flagged by the net, not read as a dub ---")
    shown = 0
    for record in records:
        taken = set(id(row) for _fit, row in found.get(record["imdb"], []))
        for row in record["rows"]:
            if id(row) in taken:
                continue
            text = row["flat"]
            if not (HEBREW_SCRIPT.search(text) or has_il_flag(text)
                    or SMELLS_HEBREW.search(text)):
                continue
            shown += 1
            if shown <= 100:
                print("  %-16s %s" % (record["name"][:16], text[:124]))
    print("  ... %d rows in all" % shown)


def tokens(records, found):
    """Every Hebrew-ish word in the corpus, by how often it appears.

    Pattern mining rather than pattern guessing: a spelling nobody here has
    thought of shows up as a word that keeps appearing next to the ones we
    already know.
    """
    counts = collections.Counter()
    for record in records:
        for row in record["rows"]:
            text = row["flat"]
            if not (HEBREW_SCRIPT.search(text) or has_il_flag(text)
                    or SMELLS_HEBREW.search(text)):
                continue
            for word in re.findall(u"[A-Za-z֐-׿]{2,}", text):
                counts[word.lower()] += 1
    print("\n--- words in Hebrew-looking rows, most common first ---")
    for word, count in counts.most_common(90):
        print("  %5d  %s" % (count, word))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--tokens", action="store_true")
    args = parser.parse_args()
    if args.harvest:
        return harvest()
    records = corpus()
    found = score(records)
    report(records, found)
    if args.review:
        review(records, found)
    if args.tokens:
        tokens(records, found)
    return 0


if __name__ == "__main__":
    sys.exit(main())

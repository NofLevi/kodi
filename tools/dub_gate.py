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
TORRENTIO = "https://torrentio.strem.fun/sort=qualitysize/stream/movie/%s.json"

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
SMELLS_HEBREW = re.compile(
    r"(\bheb\b|hebrew|israel|isra\b|ivrit|zion|moridim|horadot|hebits"
    r"|dossinet|toonhebdub)", re.I)

# Where the dubs are, which is a measurement rather than a hunch: adult
# drama and action came out at 0% over twenty titles.
STRATA = [
    ("animation", {"with_genres": "16"}),
    ("family", {"with_genres": "10751"}),
    ("disney", {"with_companies": "2"}),
    ("pixar", {"with_companies": "3"}),
    ("dreamworks", {"with_companies": "521"}),
    ("illumination", {"with_companies": "6704"}),
]
PAGES = 4


def get(url, timeout=45):
    request = urllib.request.Request(url, headers={"User-Agent": "Pinky/dub-gate"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8", "replace"))


def tmdb(path, **params):
    params["api_key"] = KEY
    return get("%s%s?%s" % (TMDB, path, urllib.parse.urlencode(params)))


def wanted_titles():
    """(tmdb id, name, stratum) for every title in the strata, deduplicated."""
    seen, out = set(), []
    for stratum, params in STRATA:
        for page in range(1, PAGES + 1):
            try:
                data = tmdb("/discover/movie", sort_by="popularity.desc",
                            page=page, **params)
            except Exception as exc:
                print("  %s page %d: %s" % (stratum, page, exc))
                continue
            for row in data.get("results") or []:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                out.append((row["id"], row.get("title") or "", stratum))
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
          % (len(titles), len(STRATA), len(done)))
    folder = os.path.dirname(CACHE)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    kept = 0
    with io.open(CACHE, "a", encoding="utf-8") as handle:
        for index, (tmdb_id, name, stratum) in enumerate(titles, 1):
            try:
                imdb = (tmdb("/movie/%s/external_ids" % tmdb_id)
                        or {}).get("imdb_id") or ""
            except Exception:
                continue
            if not imdb or imdb in done:
                continue
            try:
                streams = get(TORRENTIO % imdb).get("streams") or []
            except Exception as exc:
                print("  %-34s %s" % (name[:34], exc))
                continue
            # Only the rows the net flags are kept. The rest can never be a
            # Hebrew dub, and 300 names a title over 300 titles is tens of
            # megabytes to answer a question about a few hundred rows.
            rows = []
            for stream in streams:
                flat = ("%s %s" % (stream.get("title") or "",
                                   stream.get("name") or "")).replace("\n", " ")
                if (NET.search(flat) or HEBREW_SCRIPT.search(flat)
                        or IL_FLAG in flat):
                    seeders = re.search(u"\U0001F464\\s*(\\d+)", flat)
                    rows.append({
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
                "imdb": imdb, "name": name, "stratum": stratum,
                "streams": len(streams), "rows": rows},
                ensure_ascii=False) + "\n")
            handle.flush()
            kept += 1
            done.add(imdb)
            print("  %4d/%d  %-34s %4d streams, %3d flagged"
                  % (index, len(titles), name[:34], len(streams), len(rows)))
            time.sleep(0.3)
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
        for row in record["rows"]:
            source = stremio._parse_stream(
                {"title": row.get("title") or row["release"],
                 "name": row.get("stream_name") or "",
                 "infoHash": "0" * 40}, "torrentio")
            if source is None:
                continue
            source["seeders"] = row["seeders"]
            fit = outlook._hebrew_dub_fit(source)
            if fit:
                found[record["imdb"]].append((fit, row))
    return found


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
    by = collections.Counter(r["stratum"] for r in records if found.get(r["imdb"]))
    seen = collections.Counter(r["stratum"] for r in records)
    print("\nby stratum:")
    for stratum, _params in STRATA:
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

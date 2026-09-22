"""What the picker's three lists hold, over a thousand mostly-rare titles.

    python tools/survey_split.py --count 1000      run, resumable
    python tools/survey_split.py --report          summarise what was run

For every title: the release list a real search returns, and the picker's
first page built from it - up to ten NATIVE Hebrew rows, ten LLM rows and five
ENGLISH rows - with the best fit in each and the language each LLM row would
translate from. The questions it answers are the ones the page was built to
settle: how often is there no Hebrew subtitle at all, how often does the AI
list step in when there is not, and how often does a release's best
translatable subtitle fit it better than its best Hebrew one.

The sample leans rare on purpose, roughly four titles in five: deep pages of
the popularity ranking, old films, anime, foreign-language series, specials.
A thousand trending titles would be a thousand English blockbusters with a
Hebrew subtitle each, and would measure nothing.

It does not touch the debrid services. Which subtitles exist, and how well
they fit a release, is decided by the subtitle catalogues and the release
names; what TorBox has cached changes which releases are listed, not what
their subtitles are, and a thousand cache checks is a poor way to treat
somebody's account. No LLM is called either - only the catalogues it would
translate from are asked.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import survey_sources as base  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "split.jsonl")

# (name, share, builder). Popular first for readability; four in five are not.
STRATA = [
    ("trending films", 0.05, lambda n: base._films_from("trending", n)),
    ("popular films", 0.04, lambda n: base._films_from("popular", n)),
    ("top rated films", 0.03, lambda n: base._films_from("top_rated", n)),
    ("first episodes", 0.05, lambda n: base._episodes_from("popular", n, "first")),
    ("the latest episode", 0.03,
     lambda n: base._episodes_from("on_the_air", n, "latest")),

    ("the long tail", 0.18, lambda n: base._long_tail_films(n)),
    ("films of the 1970s", 0.05, lambda n: base._films_by_decade(1970, n)),
    ("films of the 1980s", 0.05, lambda n: base._films_by_decade(1980, n)),
    ("films of the 1990s", 0.04, lambda n: base._films_by_decade(1990, n)),
    ("films of the 2000s", 0.04, lambda n: base._films_by_decade(2000, n)),
    ("anime films", 0.05, lambda n: base._anime_films(n)),
    ("anime episodes", 0.12, lambda n: base._anime_episodes(n)),
    ("foreign-language episodes", 0.15, lambda n: base._foreign_episodes(n)),
    ("mid-season episodes", 0.05,
     lambda n: base._episodes_from("top_rated", n, "middle")),
    ("specials, season zero", 0.03,
     lambda n: base._episodes_from("popular", n, "special")),
    ("israeli films", 0.02, lambda n: base._israeli_films(n)),
    ("israeli episodes", 0.02, lambda n: base._israeli_episodes(n)),
]
RARE = {name for name, _share, _builder in STRATA[5:]}


def build_sample(count, seed=20260922):
    random.seed(seed)
    sample = []
    for name, share, builder in STRATA:
        wanted = max(1, int(round(count * share)))
        try:
            rows = builder(wanted) or []
        except Exception as error:
            base.say("  %-26s failed: %s" % (name, str(error)[:70]))
            rows = []
        for row in rows[:wanted]:
            row["stratum"] = name
        base.say("  %-26s %d" % (name, min(len(rows), wanted)))
        sample.extend(rows[:wanted])
    seen, unique = set(), []
    for row in sample:
        key = _key(row)
        if key in seen or not row.get("tmdb"):
            continue
        seen.add(key)
        unique.append(row)
    random.shuffle(unique)
    return unique


def _key(row):
    return (row["kind"], str(row["tmdb"]), row.get("season") or 0,
            row.get("episode") or 0)


def examine(entry):
    from katan import play
    from katan.sources import aggregator
    from katan.subs import outlook

    started = time.time()
    record = {"kind": entry["kind"], "tmdb": entry["tmdb"],
              "season": entry.get("season", 0), "episode": entry.get("episode", 0),
              "stratum": entry.get("stratum", ""), "error": ""}
    try:
        request = {"type": "movie" if entry["kind"] == "movie" else "episode",
                   "tmdb": entry["tmdb"], "season": entry.get("season", 0),
                   "episode": entry.get("episode", 0)}
        meta = play.build_meta(request)
        record["title"] = meta.get("title") or ""
        record["language"] = meta.get("original_language") or ""
        full = aggregator.all_sources(meta) or aggregator.find(meta) or []
        record["sources"] = len(full)
        native, llm, english = outlook.split_rows(meta, full) if full else ([], [], [])
        for name, rows in (("native", native), ("llm", llm), ("english", english)):
            record[name] = len(rows)
            record[name + "_best"] = rows[0]["subs_fit"] if rows else 0
        record["llm_from"] = sorted({row["subs_from"] for row in llm})
        # Per release, not per title: did this release's best translatable
        # subtitle fit it better than its best Hebrew one?
        hebrew_by_hash = {row["hash"]: row["subs_fit"] for row in native}
        record["llm_beats_native"] = sum(
            1 for row in llm if row["subs_fit"] > hebrew_by_hash.get(row["hash"], 0))
    except Exception as error:
        record["error"] = "%s: %s" % (type(error).__name__, str(error)[:80])
    record["seconds"] = round(time.time() - started, 1)
    return record


def run(count, path):
    base.boot(use_debrid=False)
    from katan.subs.ai import translator
    base.say("translation engine configured: %s (none is called)" % translator.available())
    sample = build_sample(count)
    done = set()
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8"):
            try:
                done.add(_key(json.loads(line)))
            except ValueError:
                continue
    todo = [entry for entry in sample if _key(entry) not in done]
    base.say("%d in the sample, %d already done, %d to go" % (len(sample), len(done), len(todo)))
    with io.open(path, "a", encoding="utf-8") as handle:
        for number, entry in enumerate(todo, 1):
            record = examine(entry)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            base.say("%4d/%d  %-26s %-28s HE %2d  LLM %2d  EN %d  %4.1fs %s"
                     % (number, len(todo), record["stratum"][:26],
                        (record.get("title") or "?")[:28], record.get("native", 0),
                        record.get("llm", 0), record.get("english", 0),
                        record["seconds"], record["error"][:40]))


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def _pc(part, whole):
    return "%3d%%" % (100 * part // whole) if whole else "   -"


def report(path):
    rows = [json.loads(line) for line in io.open(path, encoding="utf-8") if line.strip()]
    ok = [r for r in rows if not r["error"]]
    hebrew = [r for r in ok if r.get("language") == "he"]
    # A title made in Hebrew is not split at all - it needs no subtitle - so
    # counting it as "nothing found" would be a false alarm in every run.
    with_sources = [r for r in ok if r.get("sources") and r.get("language") != "he"]
    print("%d titles surveyed, %d errors, %d with at least one source, "
          "%d made in Hebrew (not split, and not counted below)"
          % (len(rows), len(rows) - len(ok), len(with_sources), len(hebrew)))

    def block(label, group):
        n = len(group)
        he = [r for r in group if r["native"]]
        llm = [r for r in group if r["llm"]]
        en = [r for r in group if r["english"]]
        none = [r for r in group if not (r["native"] or r["llm"] or r["english"])]
        rescued = [r for r in group if not r["native"] and (r["llm"] or r["english"])]
        he_fits = [r for r in group if r["native_best"] >= 70]
        llm_fits = [r for r in group if r["llm_best"] >= 70]
        print("  %-26s %4d | HE %s  LLM %s  EN %s | nothing %s | no HE but LLM/EN %s"
              " | fits>=70: HE %s LLM %s"
              % (label[:26], n, _pc(len(he), n), _pc(len(llm), n), _pc(len(en), n),
                 _pc(len(none), n), _pc(len(rescued), n),
                 _pc(len(he_fits), n), _pc(len(llm_fits), n)))

    print("\nOf titles with sources:")
    block("ALL", with_sources)
    block("popular", [r for r in with_sources if r["stratum"] not in RARE])
    block("rare", [r for r in with_sources if r["stratum"] in RARE])
    print("\nBy stratum:")
    for name, _share, _builder in STRATA:
        group = [r for r in with_sources if r["stratum"] == name]
        if group:
            block(name, group)

    print("\nMedian best fit, where the list is not empty:")
    for name in ("native", "llm", "english"):
        fits = [r[name + "_best"] for r in with_sources if r[name]]
        print("  %-8s %3d%%  over %d titles" % (name, _median(fits), len(fits)))

    languages = {}
    for r in with_sources:
        for code in r.get("llm_from") or []:
            languages[code] = languages.get(code, 0) + 1
    print("\nLLM rows translate from:",
          ", ".join("%s %d" % kv for kv in sorted(languages.items(), key=lambda kv: -kv[1])))
    beats = [r for r in with_sources if r.get("llm_beats_native")]
    print("Titles where some release fits its AI source better than its Hebrew: %d of %d (%s)"
          % (len(beats), len(with_sources), _pc(len(beats), len(with_sources)).strip()))
    print("Median seconds per title: %.1f" % _median([r["seconds"] for r in rows]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        report(args.out)
    else:
        run(args.count, args.out)


if __name__ == "__main__":
    main()

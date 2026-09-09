# -*- coding: utf-8 -*-
"""Does the subtitle we choose actually fit the video?

`survey_sources.py` answers a different question and has been mistaken for
this one. It records `best_subtitle`, which is the **matcher's name score** -
how much a candidate's filename resembles the release we would play. It never
downloads a subtitle, never parses one, never calls `sync`. So when it reported
that 23% of episodes got a subtitle, what it measured was that 23% had a
candidate whose *name* looked right. Whether that file is in the language it
claims, whether it decodes, whether it covers the whole episode and whether it
is in time were all unmeasured.

    python tools/survey_subtitles.py --count 40 --debrid    a pilot first
    python tools/survey_subtitles.py --count 300 --debrid
    python tools/survey_subtitles.py --count 1000           no account needed
    python tools/survey_subtitles.py --report

**The ground truth is a hash.** A subtitle matched by file hash belongs to
*this* file by construction - not to something with the same name - so it is
the reference everything else can be measured against. `sync.fit` correlates
speech activity between two subtitle timelines and returns an offset, a
framerate scale and a chance-corrected confidence, and that confidence is the
number this whole tool exists to collect.

Getting a hash needs a real stream, which needs a debrid account, which is why
`--debrid` is where the interesting half lives. Without it the survey still
answers the cheap questions - does a subtitle exist, is it the language it
claims, does it decode, does it cover the episode - and those alone found
mislabelled and truncated files.

The headline the report prints is a **calibration table**: matcher score
against measured fit. `subs.threshold` is 70 and nobody has ever checked
whether 70 means "fits".
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ROOT, "plugin.video.katan", "resources", "lib"))

# The sampler, the resume file format and the isolated cache are all worth
# having once. This tool asks a different question of the same nineteen strata.
import survey_sources as base  # noqa: E402

OUT = os.path.join(ROOT, "subtitles.jsonl")

_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_lock = threading.Lock()


def say(text=""):
    _out.write(text + "\n")
    _out.flush()


# --------------------------------------------------------------------------
# one title
# --------------------------------------------------------------------------


def examine(entry, use_debrid):
    """Everything one title can tell us about its subtitles."""
    from katan import play, settings
    from katan.sources import aggregator, scoring
    from katan.subs import auto, hasher, matcher, srt, sync

    started = time.time()
    record = dict(entry)
    record.update(error="", candidates=0, per_provider={}, has_hash_match=False,
                  chosen_provider="", chosen_score=-1, accepted=False,
                  decoded=False, cues=0, last_cue=0.0, runtime=0,
                  language_claimed="", language_detected="",
                  fit_confidence=-1.0, fit_offset=0.0, fit_scale=1.0,
                  fit_after=-1.0, segments=0,
                  failed_bytes="")

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
    record["runtime"] = int(meta.get("duration") or 0)

    # What a viewer would actually be given, because the name score is measured
    # against that release and no other.
    try:
        from katan.sources import model

        providers = aggregator._enabled_providers(meta)
        raw = aggregator._run_providers(providers, meta, quiet=True)
        merged = model.dedupe(raw)

        # Anime files under a different address, and the survey is 30% anime
        # when run with --episodes. Asking only TMDB's address there would
        # measure subtitles for episodes we could not have played anyway.
        also = aggregator._anime_address(meta)
        if also:
            by_id = [(n, m) for n, m in providers
                     if not getattr(m, "BY_NAME", False)]
            merged = model.dedupe(raw + aggregator._run_providers(
                by_id, also, quiet=True))

        # (kept, rejected), and kept is what a viewer would be offered.
        sources, _rejected = scoring.rank_all(
            merged, meta, aggregator._runtime_hours(meta))
    except Exception as error:
        record["error"] = "sources: %s" % str(error)[:90]
        record["ms"] = int((time.time() - started) * 1000)
        return record

    record["sources"] = len(sources)
    if not sources:
        record["ms"] = int((time.time() - started) * 1000)
        return record

    top = sources[0]
    record["release"] = top.get("title", "")[:120]
    meta["source"] = {"release": top.get("title", ""),
                      "file_name": top.get("file_name", "")}

    # The hash, which is the only thing that makes the rest of this a
    # measurement rather than an opinion.
    video_hash = ""
    if use_debrid:
        try:
            url = _stream_url(top, sources)
            if url:
                video_hash, size = hasher.hash_stream(url)
                meta["stream_size"] = size
                record["hashed"] = bool(video_hash)
        except Exception as error:
            record["error"] = "hash: %s" % str(error)[:90]

    languages = settings.get_list("subs.languages") or ["he", "en"]
    try:
        candidates = auto.search_candidates(meta, languages, video_hash)
    except Exception as error:
        record["error"] = "search: %s" % str(error)[:90]
        return record

    record["candidates"] = len(candidates)
    per_provider = {}
    for candidate in candidates:
        name = candidate.get("provider", "?")
        per_provider[name] = per_provider.get(name, 0) + 1
    record["per_provider"] = per_provider

    target = matcher.target_from(meta)
    ranked = matcher.rank(candidates, target, video_hash, languages)
    threshold = settings.get_int("subs.threshold") or 70
    winner = None
    for candidate in ranked:
        if candidate.get("language") == languages[0]:
            winner = candidate
            break
    winner = winner or (ranked[0] if ranked else None)
    if not winner:
        record["ms"] = int((time.time() - started) * 1000)
        return record

    record["chosen_provider"] = winner.get("provider", "")
    record["chosen_score"] = int(winner.get("score") or 0)
    record["language_claimed"] = winner.get("language", "")
    record["accepted"] = record["chosen_score"] >= threshold

    reference = _hash_matched(ranked, video_hash)
    record["has_hash_match"] = bool(reference)

    chosen_cues = _cues(winner)
    if chosen_cues is None:
        record["error"] = record["error"] or "the chosen subtitle did not parse"
        # 16 of 116 downloads failed here on the first run and every one was a
        # mystery, because nothing recorded *what* arrived. A parse failure and
        # a download that returned an error page look identical from up here.
        record["failed_bytes"] = _peek(winner)
        record["ms"] = int((time.time() - started) * 1000)
        return record

    record["decoded"] = True
    record["cues"] = len(chosen_cues)
    record["last_cue"] = round(chosen_cues[-1].end, 1) if chosen_cues else 0.0
    record["language_detected"] = srt.detect_script(chosen_cues)

    # The measurement. Only against a hash match, and only against a different
    # file - correlating a subtitle with itself says nothing.
    if reference is not None and reference is not winner:
        reference_cues = _cues(reference)
        if reference_cues:
            try:
                offset, scale, confidence = sync.fit(reference_cues, chosen_cues)
                record["fit_confidence"] = round(confidence, 3)
                record["fit_offset"] = round(offset, 2)
                record["fit_scale"] = scale
                # And what re-timing would actually leave the viewer with,
                # splits included. A raw fit says how wrong the download was;
                # this says how wrong it still is after the add-on has done
                # everything it can, which is the number that matters.
                fixed, result = sync.synchronise(chosen_cues, reference_cues)
                record["segments"] = result["segments"]
                record["fit_after"] = round(
                    sync.estimate_quality(fixed, reference_cues), 3)
            except Exception as error:
                record["error"] = "sync: %s" % str(error)[:90]

    record["ms"] = int((time.time() - started) * 1000)
    return record


def _stream_url(top, sources):
    """A playable URL for the best cached source, or "".

    Only cached ones: a survey must not queue hundreds of downloads onto
    somebody's account, and an uncached source would not answer in time
    anyway.
    """
    from katan import play

    for source in [top] + list(sources[1:6]):
        if not source.get("cached"):
            continue
        try:
            url = play._resolve(source)
        except Exception:
            continue
        if url:
            return url
    return ""


def _hash_matched(candidates, video_hash):
    """The candidate the service says matched this file, if any."""
    for candidate in candidates:
        if candidate.get("hash_match"):
            return candidate
        if video_hash and candidate.get("moviehash") == video_hash:
            return candidate
    return None


def _peek(candidate):
    """The first bytes of a download that would not parse, for diagnosis."""
    from katan.subs import auto
    try:
        module = auto._modules().get(candidate.get("provider"))
        data = module.download(candidate) if module else b""
    except Exception as error:
        return "download raised: %s" % str(error)[:60]
    if not data:
        return "download returned nothing"
    return "%d bytes %r" % (len(data), data[:60])


def _cues(candidate):
    """Download and parse one candidate. None when it will not open."""
    from katan.subs import auto

    try:
        return auto.download_candidate(candidate) or None
    except Exception:
        return None


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------


def run(count, workers, use_debrid, path):
    sample = base.build_sample(count)
    done = base.already_done(path)
    todo = [e for e in sample
            if (e["kind"], e["tmdb"], e.get("season", 0),
                e.get("episode", 0)) not in done]

    say("%d titles, %d already done, %d to go%s"
        % (len(sample), len(sample) - len(todo), len(todo),
           " (with debrid)" if use_debrid else ""))
    say("")

    if not todo:
        return 0

    # Plain threads over a shared queue, the shape survey_sources uses.
    # `http.run_parallel` carries a wall-clock deadline for the whole batch,
    # which is exactly right for a search a viewer is waiting on and exactly
    # wrong for a survey that runs for an hour.
    handle = io.open(path, "a", encoding="utf-8", newline="")
    counter = {"n": 0}

    def worker(queue):
        while True:
            try:
                entry = queue.pop()
            except IndexError:
                return
            try:
                record = examine(entry, use_debrid)
            except Exception as error:
                record = dict(entry, error="crashed: %s" % str(error)[:90])
            with _lock:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                counter["n"] += 1
                n = counter["n"]
            if n % 5 == 0 or n < 5:
                say("  %4d/%-4d %-28s cand %-3s score %-4s fit %-6s %s"
                    % (n, len(todo),
                       (record.get("resolved_title") or "?")[:28],
                       record.get("candidates", "-"),
                       record.get("chosen_score", "-"),
                       record.get("fit_confidence", "-"),
                       (record.get("error") or "")[:32]))

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
        say("\nstopping - what is written so far is kept")
    finally:
        handle.close()
    return 0


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def _median(values):
    values = sorted(values)
    return values[len(values) // 2] if values else 0


def report(path, examples=10):
    if not os.path.isfile(path):
        raise SystemExit("nothing at %s yet" % os.path.relpath(path, ROOT))
    rows = [json.loads(line) for line in io.open(path, encoding="utf-8")
            if line.strip()]
    say("%d titles surveyed" % len(rows))

    with_sources = [r for r in rows if r.get("sources", 0) > 0]
    with_candidates = [r for r in with_sources if r.get("candidates", 0) > 0]
    accepted = [r for r in with_candidates if r.get("accepted")]
    say("  had sources          %4d" % len(with_sources))
    say("  had any candidate    %4d (%d%%)"
        % (len(with_candidates), _pc(with_candidates, with_sources)))
    say("  cleared the threshold%4d (%d%%)"
        % (len(accepted), _pc(accepted, with_sources)))

    # -- the point of the whole exercise -----------------------------------
    measured = [r for r in rows if r.get("fit_confidence", -1) >= 0]
    say("")
    say("CALIBRATION - does the name score predict the fit?")
    if not measured:
        say("  nothing measured. That needs --debrid: the fit is measured")
        say("  against a hash-matched subtitle, and a hash needs a real stream.")
    else:
        say("  %d titles had a hash-matched reference to measure against" %
            len(measured))
        say("")
        say("  name score      titles   median fit   below 0.45   after re-timing")
        for low, high, label in ((100, 101, "100 exact"),
                                 (90, 100, " 90-99"),
                                 (70, 90, " 70-89"),
                                 (55, 70, " 55-69"),
                                 (0, 55, " under 55")):
            block = [r for r in measured
                     if low <= r.get("chosen_score", -1) < high]
            if not block:
                continue
            fits = [r["fit_confidence"] for r in block]
            bad = [f for f in fits if f < 0.45]
            after = [r["fit_after"] for r in block if r.get("fit_after", -1) >= 0]
            say("  %-14s  %4d     %6.2f       %d (%d%%)      %s"
                % (label, len(block), _median(fits), len(bad),
                   100 * len(bad) // len(block),
                   "%6.2f" % _median(after) if after else "     -"))
        say("")
        say("  MIN_CONFIDENCE is 0.45 and subs.threshold is 70. A row at or")
        say("  above 70 whose median fit is under 0.45 means the threshold is")
        say("  letting through subtitles that do not fit. The last column is")
        say("  what the viewer actually gets, after sync has re-timed it.")

        split = [r for r in measured if r.get("segments", 0) > 1]
        say("")
        say("  SPLITS - files cut differently from their subtitle")
        say("  needed more than one offset  %4d of %4d (%d%%)"
            % (len(split), len(measured), _pc(split, measured)))
        say("  a single global offset cannot fix those, which is why alass")
        say("  exists. Near zero here means fit_segments is not earning its")
        say("  place and should be deleted.")

    # -- did sync engage at all --------------------------------------------
    hashed = [r for r in rows if r.get("has_hash_match")]
    say("")
    say("REACH - can the sync engine run?")
    say("  had a hash-matched candidate  %4d of %4d (%d%%)"
        % (len(hashed), len(with_candidates), _pc(hashed, with_candidates)))
    say("  without one, sync never runs and the name score is the only")
    say("  judgement - which was true of every playback before BSPlayer.")

    # -- honesty and integrity ---------------------------------------------
    decoded = [r for r in with_candidates if r.get("decoded")]
    lying = [r for r in decoded
             if r.get("language_detected")
             and r.get("language_claimed")
             and r["language_detected"] != r["language_claimed"]]
    short = [r for r in decoded
             if r.get("runtime") and r.get("last_cue")
             and r["last_cue"] / float(r["runtime"]) < 0.6]
    say("")
    say("HONESTY AND INTEGRITY")
    say("  downloaded and parsed         %4d" % len(decoded))
    say("  failed to parse               %4d"
        % (len(with_candidates) - len(decoded)))
    say("  language is not what it says  %4d (%d%%)"
        % (len(lying), _pc(lying, decoded)))
    say("  covers under 60%% of runtime   %4d (%d%%)"
        % (len(short), _pc(short, decoded)))
    for row in [r for r in with_candidates if r.get("failed_bytes")][:6]:
        say("    %-28s %s" % (row["title"][:28], row["failed_bytes"][:70]))

    # -- per provider -------------------------------------------------------
    say("")
    say("BY PROVIDER")
    providers = {}
    for row in rows:
        for name, n in (row.get("per_provider") or {}).items():
            providers.setdefault(name, [0, []])[0] += n
    for row in measured:
        name = row.get("chosen_provider")
        if name in providers:
            providers[name][1].append(row["fit_confidence"])
    for name, (found, fits) in sorted(providers.items(), key=lambda kv: -kv[1][0]):
        say("  %-20s %5d candidates   median fit %s"
            % (name, found, ("%.2f" % _median(fits)) if fits else "-"))

    _examples(rows, examples)
    return 0


def _pc(part, whole):
    return 100 * len(part) // max(1, len(whole))


def _examples(rows, limit):
    def show(heading, matching, describe):
        if not matching:
            return
        say("")
        say("%s (%d)" % (heading, len(matching)))
        for row in matching[:limit]:
            say("  %-34s %s" % ((row.get("resolved_title") or "?")[:34],
                                describe(row)))

    show("cleared the threshold and does not fit",
         [r for r in rows if r.get("accepted")
          and 0 <= r.get("fit_confidence", -1) < 0.45],
         lambda r: "score %s, fit %.2f, offset %ss"
         % (r.get("chosen_score"), r.get("fit_confidence"),
            r.get("fit_offset")))
    show("labelled one language, written in another",
         [r for r in rows if r.get("language_detected")
          and r.get("language_claimed")
          and r["language_detected"] != r["language_claimed"]],
         lambda r: "says %s, looks %s (%s)"
         % (r.get("language_claimed"), r.get("language_detected"),
            r.get("chosen_provider")))
    show("stops less than 60% of the way in",
         [r for r in rows if r.get("runtime") and r.get("last_cue")
          and r["last_cue"] / float(r["runtime"]) < 0.6],
         lambda r: "last cue %ds of %ds" % (r["last_cue"], r["runtime"]))
    show("sources but not one subtitle",
         [r for r in rows if r.get("sources", 0) > 0
          and r.get("candidates", 0) == 0],
         lambda r: "%d sources" % r.get("sources", 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--debrid", action="store_true",
                        help="resolve a real stream and hash it, which is what "
                             "makes the fit measurable")
    parser.add_argument("--episodes", action="store_true",
                        help="episodes only, the mix survey_sources uses")
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()

    if args.report:
        return report(args.out, args.examples)

    if args.episodes:
        base.STRATA = base.EPISODE_MIX
    base.boot(use_debrid=args.debrid)
    return run(args.count, args.workers, args.debrid, args.out)


if __name__ == "__main__":
    sys.exit(main())

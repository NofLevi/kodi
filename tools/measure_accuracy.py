# -*- coding: utf-8 -*-
"""How often is the subtitle the add-on picks by itself the right one?

`survey_subtitles.py` answers this only for the titles where OpenSubtitles
holds a file matched by hash to the exact video - measured, about 3% of them.
Twelve titles cannot answer "how good is it", so this adds a second test that
works on any title with two independent candidates:

    ground truth  a hash-matched subtitle belongs to this file by
                  construction, so the chosen one can be correlated against
                  it. Needs a real stream, so --debrid.
    corroboration the best candidate from a *different provider* than the one
                  chosen. Two uploaders who independently land on the same
                  timeline are describing the real one; a disagreement of
                  seconds means one of them is wrong. No stream needed.

Both are reported, and where a title has both, the corroboration verdict is
compared with the hash verdict - which is the only honest way to say what the
wide number is worth.

Judged per title, as a viewer would experience it:

    found         a subtitle was chosen, parsed, and covers the runtime
    language      the script in the file is the language it claims
    episode       the release name does not name a different episode
    timing        fit against ground truth, or against corroboration

    python tools/measure_accuracy.py --count 250 --debrid
    python tools/measure_accuracy.py --report
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

import survey_subtitles as survey  # noqa: E402
import survey_sources as base  # noqa: E402

# Captured before the override below, so the survey's own measurement still
# runs inside ours rather than recursing into it.
_probe = survey.examine

OUT = os.path.join(ROOT, ".survey-accuracy.jsonl")

# A subtitle that agrees with an independent one to within this much is
# corroborated; the add-on's own sync would not move it further either.
AGREE_SECONDS = 1.0
# Below this the correlation is not evidence of anything - see sync.MIN_CONFIDENCE.
AGREE_SCORE = 0.45

# One search per title, not three. This tool asks the providers the same
# question three times - for the probe, inside the real pipeline, and for
# corroboration - and four workers doing that got OpenSubtitles to start
# answering nothing: Scarface went from 40 candidates to "no candidates" in
# the same second. A viewer never does this; the measurement must not either.
_searches = {}
_search_lock = threading.Lock()
_real_search = None


def _cached_search(meta, languages, video_hash="", **kwargs):
    ids = meta.get("ids") or {}
    key = (ids.get("tmdb"), ids.get("imdb"), meta.get("season"),
           meta.get("episode"), tuple(languages), bool(video_hash))
    with _search_lock:
        held = _searches.get(key)
    if held is None:
        held = _real_search(meta, languages, video_hash, **kwargs) or []
        with _search_lock:
            _searches[key] = held
    # Copies: the matcher writes its score onto every candidate it ranks.
    return [dict(candidate) for candidate in held]


def examine(entry, use_debrid):
    """One title, run through the real pipeline and then judged."""
    from katan import play, settings
    from katan.subs import auto, matcher, srt, sync
    from katan.utils import release as release_parser

    started = time.time()
    record = dict(entry)
    record.update(error="", sources=0, candidates=0, found=False, reason="",
                  chosen_provider="", chosen_score=-1, chosen_language="",
                  language_ok=None, episode_ok=None, covers=None,
                  truth=-1.0, truth_after=-1.0, agree=-1.0, agree_offset=0.0,
                  corroborated=None, providers=0)

    # Everything up to "what would the viewer be given" is the survey's, so
    # the two tools measure the same pipeline rather than two versions of it.
    probe = _probe(entry, use_debrid)
    for key in ("resolved_title", "runtime", "sources", "release", "candidates",
                "chosen_provider", "chosen_score", "has_hash_match", "hashed",
                "language_claimed", "language_detected", "cues", "last_cue",
                "failed_bytes", "per_provider"):
        if key in probe:
            record[key] = probe[key]
    record["error"] = probe.get("error", "")
    record["truth"] = probe.get("fit_confidence", -1.0)
    record["truth_after"] = probe.get("fit_after", -1.0)
    record["chosen_language"] = probe.get("language_claimed", "")
    record["providers"] = len(probe.get("per_provider") or {})
    if not probe.get("candidates"):
        record["ms"] = int((time.time() - started) * 1000)
        return record

    # What the automatic path actually ends up applying, which is not always
    # the top-ranked candidate: it verifies, re-times, and falls back.
    try:
        meta = play.build_meta({"type": entry["kind"], "tmdb": entry["tmdb"],
                                "season": entry.get("season", 0),
                                "episode": entry.get("episode", 0)})
        meta["source"] = {"release": probe.get("release", "")}
        languages = settings.get_list("subs.languages") or ["he", "en"]
        path, report = auto.find_and_prepare(meta, languages)
    except Exception as error:
        record["error"] = "pipeline: %s" % str(error)[:90]
        record["ms"] = int((time.time() - started) * 1000)
        return record

    record["reason"] = report.get("reason", "")
    record["translated"] = bool(report.get("translated"))
    record["synchronised"] = bool(report.get("synchronised"))
    if not path:
        record["ms"] = int((time.time() - started) * 1000)
        return record

    cues = srt.read(path)
    record["found"] = bool(cues)
    if not cues:
        record["ms"] = int((time.time() - started) * 1000)
        return record

    applied = os.path.basename(path).split(".")[-2]
    record["chosen_language"] = applied
    record["language_ok"] = srt.detect_script(cues) in ("", applied)
    runtime = int(meta.get("duration") or record.get("runtime") or 0)
    record["covers"] = bool(auto.covers_runtime(cues, runtime)) if runtime else None

    # Corroboration: the best candidate that is not from the provider we used.
    try:
        found = auto.search_candidates(meta, languages)
        target = matcher.target_from(meta)
        ranked = matcher.rank(found, target, "", languages)
        chosen_provider = record.get("chosen_provider")

        # The subtitle's own name is the only thing that can say "this is
        # another episode", and saying nothing is not saying the wrong thing -
        # which is why this asks the matcher's contradiction rule rather than
        # its match rule. A season pack names no episode and is not wrong.
        for candidate in ranked:
            if candidate.get("language") == record["chosen_language"]:
                record["chosen_release"] = (candidate.get("release") or "")[:120]
                if entry["kind"] == "episode":
                    record["episode_ok"] = not matcher._contradicts_episode(
                        release_parser.parse(candidate.get("release") or ""), target)
                break

        for candidate in ranked:
            if candidate.get("provider") == chosen_provider:
                continue
            other = auto.download_candidate(candidate)
            if not other:
                continue
            offset, _scale, score = sync.fit(other, cues)
            record["agree"] = round(score, 3)
            record["agree_offset"] = round(offset, 2)
            record["agree_provider"] = candidate.get("provider", "")
            record["corroborated"] = bool(
                score >= AGREE_SCORE and abs(offset) <= AGREE_SECONDS)
            break
    except Exception as error:
        record["error"] = record["error"] or "corroborate: %s" % str(error)[:80]

    record["ms"] = int((time.time() - started) * 1000)
    return record


def _pc(part, whole):
    return "%3d%%" % (100 * part // whole) if whole else "   -"


def report(path):
    rows = [json.loads(line) for line in io.open(path, encoding="utf-8") if line.strip()]
    ok = [r for r in rows if not r.get("error") or r.get("found")]
    with_sources = [r for r in ok if r.get("sources")]
    found = [r for r in with_sources if r.get("found")]
    print("%d titles, %d had sources, %d ended with a subtitle applied (%s)"
          % (len(rows), len(with_sources), len(found), _pc(len(found), len(with_sources))))

    print("\nOF THE SUBTITLES IT APPLIED")
    lang = [r for r in found if r.get("language_ok") is False]
    covers = [r for r in found if r.get("covers") is False]
    episodes = [r for r in found if r.get("episode_ok") is not None]
    wrong_ep = [r for r in episodes if r["episode_ok"] is False]
    print("  in the language it claims        %s (%d wrong)"
          % (_pc(len(found) - len(lang), len(found)), len(lang)))
    print("  covers the whole runtime         %s (%d short)"
          % (_pc(len(found) - len(covers), len(found)), len(covers)))
    print("  right episode, by release name   %s of %d episodes (%d wrong)"
          % (_pc(len(episodes) - len(wrong_ep), len(episodes)), len(episodes), len(wrong_ep)))

    truth = [r for r in found if r.get("truth_after", -1) >= 0]
    agree = [r for r in found if r.get("corroborated") is not None]
    print("\nTIMING")
    if truth:
        good = [r for r in truth if r["truth_after"] >= 0.8]
        poor = [r for r in truth if r["truth_after"] < 0.45]
        print("  against ground truth (hash)      %s fit well, %d poorly, of %d"
              % (_pc(len(good), len(truth)), len(poor), len(truth)))
    if agree:
        yes = [r for r in agree if r["corroborated"]]
        print("  corroborated by another uploader %s of %d"
              % (_pc(len(yes), len(agree)), len(agree)))
        both = [r for r in agree if r.get("truth_after", -1) >= 0]
        if both:
            same = [r for r in both if r["corroborated"] == (r["truth_after"] >= 0.8)]
            print("  the two agree with each other    %s of %d titles that have both"
                  % (_pc(len(same), len(both)), len(both)))

    print("\nHOW IT GOT THERE")
    reasons = {}
    for r in found:
        reasons[r.get("reason") or "?"] = reasons.get(r.get("reason") or "?", 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("  %-42s %4d" % (reason[:42], n))

    print("\nWHERE IT FAILED")
    empty = [r for r in with_sources if not r.get("found")]
    for r in empty[:12]:
        print("  %-34s %s" % ((r.get("resolved_title") or r.get("title") or "?")[:34],
                              (r.get("reason") or r.get("error") or "no candidates")[:60]))
    print("  ... %d more" % max(0, len(empty) - 12) if len(empty) > 12 else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--debrid", action="store_true")
    parser.add_argument("--episodes", action="store_true")
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        return report(args.out)
    if args.episodes:
        base.STRATA = base.EPISODE_MIX
    base.boot(use_debrid=args.debrid)
    from katan.subs import auto
    global _real_search
    _real_search = auto.search_candidates
    auto.search_candidates = _cached_search
    survey.examine = examine          # what survey.run calls per title
    survey.OUT = args.out
    return survey.run(args.count, args.workers, args.debrid, args.out)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Does every Israeli broadcaster still work, end to end?

    python tools/check_vod.py                 a sample of every broadcaster
    python tools/check_vod.py --module kan    just one
    python tools/check_vod.py --count 12      how many programmes each
    python tools/check_vod.py --json out.json

`tools/check_channels.py` does this for the live channels. Nothing did it for
the on-demand catalogue, which is the half with the moving parts: seven
broadcasters, seven different content management systems, each scraped from
pages that were never meant for a media player and change without notice.

What it asks of a programme, in the order a viewer meets it:

    listed       the programme yields episodes at all
    named        every episode has a title
    clean        no raw HTML entities in that title - Kan writes an
                 apostrophe as `&#x27;`, and a title is where that shows
    distinct     the titles are not all the same string, which is what a
                 card layout being misread looks like
    not glued    no title is the episode name with the synopsis run onto the
                 end of it, which is how every episode of קופה ראשית came to
                 be called "פרק 2 - הג'ל של ראמזי יום העצמאות בפתח ואביחי..."
    numbered     a season or an episode number was read off the title where
                 the broadcaster states one
    plays        the first episode resolves to a URL, and that URL answers

The last one is the point. Everything above it can pass on a page that has
been redesigned into something unplayable, and "it listed the episodes and
then nothing happened" is the failure this is built to catch.
"""
import argparse
import collections
import json
import os
import random
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "plugin.video.pinky")
WORK = os.path.join(ROOT, ".vod-check")
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "stubs"))
sys.path.insert(0, os.path.join(ADDON, "resources", "lib"))

# A title this long is the name with the synopsis glued to it. Real Hebrew
# episode names run to about forty characters; the glued ones hit the 120
# limit `plain_text` truncates at.
GLUED = 95
ENTITIES = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|quot|apos|nbsp|lt|gt);")
# A handoff rather than a URL we fetch ourselves: Kan publishes a great deal
# through YouTube and the YouTube add-on plays it.
HANDOFF = ("plugin://", "youtube")


def boot():
    import xbmcaddon  # noqa: F401  - registers the stub
    from pinky import kodi
    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    kodi.profile_path = lambda: WORK
    # Without this the bundled catalogue is invisible and every check passes
    # for the wrong reason - the same trap `tools/e2e.py` records.
    kodi.addon_path = lambda: ADDON
    return kodi


def fetch_head(url, referer=""):
    """The first bytes of a stream, and what they look like.

    **Through the add-on's own HTTP layer**, not a bare urllib call. The
    first version used urllib and reported "the stream would not open
    (CERTIFICATE_VERIFY_FAILED)" for two Sport 1 programmes - which is a
    fact about this Python's certificate store, not about the broadcaster,
    and the add-on fetches them perfectly well. A checker that fails where
    the thing it checks succeeds is worse than no checker.
    """
    from pinky import http

    headers = {"Range": "bytes=0-2047"}
    if referer:
        headers["Referer"] = referer
    answer = http.get(url, headers=headers, timeout=(6, 20))
    if answer is None:
        raise IOError("no response")
    body = answer.content[:2048] if hasattr(answer, "content") else b""
    return (getattr(answer, "status_code", 0),
            (answer.headers or {}).get("content-type") or "", body)


def looks_playable(body, content_type):
    """Is this a manifest or media, rather than a web page or an error?"""
    head = body[:400].lstrip()
    if head.startswith(b"#EXTM3U"):
        return "hls"
    if b"<MPD" in body[:800]:
        return "dash"
    if b"ftyp" in body[:64] or head.startswith(b"\x00\x00\x00"):
        return "mp4"
    if head.startswith(b"ID3") or b"audio/" in content_type.encode():
        return "audio"
    if b"video/" in content_type.encode() or b"mpegurl" in content_type.encode():
        return "media"
    if head[:1] == b"<":
        return ""          # a web page where a stream should be
    return ""


def check_programme(module, entry, kodi):
    """Every question above, for one programme."""
    from pinky.vod import extractors

    ref = entry.get("u") or ""
    name = entry.get("n") or "?"
    out = {"module": module, "programme": name, "ref": ref, "problems": [],
           "episodes": 0, "numbered": 0, "played": "", "kind": ""}

    try:
        episodes = extractors.episodes(module, ref)
    except Exception as exc:
        out["problems"].append("listing raised %s" % type(exc).__name__)
        return out
    out["episodes"] = len(episodes)
    if not episodes:
        # **A catalogue entry is not always a programme.** Sport 5 files
        # 1,092 of them and many are a single clip - "5 באוויר: 4.9 - רז
        # זהבי ונדב..." is one broadcast, not a series - and a radio
        # programme is often one page with the audio on it. There is nothing
        # beneath those to list, so the question becomes whether the entry
        # itself plays, which is what a viewer pressing it would find out.
        out["single"] = True
        out = _check_stream(module, ref, ref, out)
        if out["kind"] and not out["problems"]:
            # It is a single video and it plays. Not a fault.
            return out
        # It is neither a programme nor a playable thing. Report that once,
        # as the viewer meets it, rather than also reporting whatever the
        # reference answered when fetched as if it were a stream - Channel
        # 14's catalogue refs are API endpoints, and fetching one as a video
        # reported "the stream answered 500" for a programme whose real
        # problem is that the broadcaster listed no episodes for it.
        out["problems"] = ["no episodes listed, and the entry does not play"]
        return out

    titles = [(e.get("title") or "").strip() for e in episodes]
    if not all(titles):
        out["problems"].append("%d of %d episodes have no title"
                               % (sum(1 for t in titles if not t), len(titles)))
    bad = [t for t in titles if ENTITIES.search(t)]
    if bad:
        out["problems"].append("raw HTML entity in %d titles (%r)"
                               % (len(bad), bad[0][:60]))
    # **Most of them, not one of them.** A card layout being misread glues
    # every title to its synopsis; a single long one is a real headline, and
    # Channel 14 files talkback items whose names genuinely run past ninety
    # characters. Flagging those was the checker crying wolf on the one
    # programme of its six that worked.
    glued = [t for t in titles if len(t) > GLUED]
    if len(glued) * 2 > len(titles) and len(glued) > 1:
        out["problems"].append("%d of %d titles look glued to their synopsis (%r)"
                               % (len(glued), len(titles), glued[0][:70]))
    if len(titles) > 2 and len(set(titles)) == 1:
        out["problems"].append("every episode has the same title (%r)"
                               % titles[0][:50])

    out["numbered"] = sum(1 for e in episodes
                          if (e.get("season") or 0) or (e.get("episode") or 0))

    # The one that matters. The first episode with a ref of its own, because
    # Kan's programme pages open with a "watch" link that is not an episode.
    playable = [e for e in episodes
                if ((e.get("extra") or {}).get("ref") or (e.get("ids") or {}).get("vod"))]
    if not playable:
        out["problems"].append("no episode carries a reference to play")
        return out
    target = playable[0]
    episode_ref = ((target.get("extra") or {}).get("ref")
                   or (target.get("ids") or {}).get("vod"))
    return _check_stream(module, episode_ref, ref, out)


def _check_stream(module, episode_ref, referer, out):
    """Resolve one reference and prove the bytes behind it are playable."""
    from pinky.vod import extractors

    try:
        url, _adaptive = extractors.stream(module, episode_ref)
    except Exception as exc:
        out["problems"].append("resolving raised %s" % type(exc).__name__)
        return out
    if not url:
        out["problems"].append(
            "resolved to nothing" if out.get("single")
            else "the first episode resolved to nothing")
        return out
    out["played"] = url[:120]
    if any(mark in url for mark in HANDOFF):
        # Kan publishes a great deal through YouTube and the YouTube add-on
        # plays it, so there is no URL of ours to fetch.
        out["kind"] = "handoff"
        return out
    try:
        status, content_type, body = fetch_head(url, referer=referer)
    except Exception as exc:
        out["problems"].append("the stream would not open (%s)"
                               % str(exc)[:60])
        return out
    kind = looks_playable(body, content_type)
    out["kind"] = kind or "not a stream"
    if status not in (200, 206):
        out["problems"].append("the stream answered %s" % status)
    elif not kind:
        out["problems"].append("the stream is not a manifest or media (%s)"
                               % (content_type or "no content type"))
    return out


def sample(entries, count, seed):
    """A stable sample, so two runs are comparable."""
    picked = list(entries)
    random.Random(seed).shuffle(picked)
    return picked[:count]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", action="append", default=None)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--category", default="")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    kodi = boot()
    from pinky.vod import extractors, library

    entries = library._read_seed()
    by_module = collections.defaultdict(list)
    for entry in entries:
        by_module[entry.get("m") or "?"].append(entry)

    wanted = args.module or extractors.supported()
    results, failures = [], 0
    for module in wanted:
        pool = by_module.get(module) or []
        if not pool:
            print("%-8s no catalogue entries at all" % module)
            failures += 1
            continue
        # **Stratified by the broadcaster's own category**, because one
        # module is not one kind of thing: 266 of Kan's 826 entries are
        # podcasts and another 205 are radio stations, so a random sample of
        # "kan" is mostly radio and says nothing about whether כאן 11 plays.
        # Sampling each category separately also makes the failure legible -
        # "the podcasts resolve to nothing" is a different bug from "Kan is
        # broken", and the first run here reported the second when it had
        # found the first.
        groups = collections.defaultdict(list)
        for entry in pool:
            groups[(entry.get("c") or "").strip() or "(no category)"].append(entry)
        print("\n=== %s  (%d programmes, %d categories) ==="
              % (module, len(pool), len(groups)))
        for category, members in sorted(groups.items(),
                                        key=lambda kv: -len(kv[1])):
            if args.category and args.category not in category:
                continue
            # Proportional across a module's categories, but never fewer than
            # two: a category sampled once tells you a single page still
            # parses, not that the category works. A named category gets the
            # full count, because then it is the thing being asked about.
            if args.category:
                share = args.count
            else:
                share = min(args.count, max(2, int(round(
                    args.count * len(members) / float(len(pool))))))
            share = min(share, len(members))
            print("  -- %s (%d) --" % (category, len(members)))
            for entry in sample(members, share, args.seed):
                result = check_programme(module, entry, kodi)
                result["category"] = category
                results.append(result)
                mark = "ok  " if not result["problems"] else "FAIL"
                print("    %s %-28s %3d eps, %3d numbered, %-9s"
                      % (mark, (result["programme"] or "?")[:28],
                         result["episodes"], result["numbered"],
                         result["kind"] or "-"))
                for problem in result["problems"]:
                    print("          - %s" % problem)
                if result["problems"]:
                    failures += 1
                time.sleep(0.2)

    print("\n%d programmes checked, %d with problems" % (len(results), failures))
    print("\nby broadcaster and category:")
    rows = collections.defaultdict(lambda: [0, 0])
    for result in results:
        key = (result["module"], result.get("category") or "-")
        rows[key][0] += 1
        if not result["problems"]:
            rows[key][1] += 1
    for (module, category), (seen, ok) in sorted(rows.items()):
        flag = "" if ok == seen else "   <-- "
        print("  %-8s %-22s %2d/%2d working%s"
              % (module, category[:22], ok, seen, flag))

    worst = collections.Counter()
    for result in results:
        for problem in result["problems"]:
            worst[re.sub(r"\d+", "N", problem.split(" (")[0])] += 1
    for problem, count in worst.most_common():
        print("  %-52s %d" % (problem, count))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=1)
        print("wrote %s" % args.json)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

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
        # **"No response" is where a checker stops being useful.** Sport 1's
        # on-demand CDN - Walla's, not ours - has an expired certificate, so
        # every programme on it reads as broken here while the fault is
        # somebody else's and unfixable from this side. `http.get` swallows
        # the reason, so it is asked for again plainly and reported.
        raise IOError(_why_it_would_not_open(url))
    body = answer.content[:2048] if hasattr(answer, "content") else b""
    return (getattr(answer, "status_code", 0),
            (answer.headers or {}).get("content-type") or "", body)


def _why_it_would_not_open(url):
    """The reason behind a refusal, in the words the library uses."""
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=15).read(16)
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or exc)
        return "%s: %s" % (type(exc).__name__, reason[:120])
    return "no response"


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
    # **A season is not an episode.** Mako lists its seasons as pages of their
    # own - "עונה 1", "עונה 2" at `...book_a_table-s1` - and a viewer descends
    # into one before reaching a video. Calling `stream` on a season folder
    # asks the player for a directory and gets nothing, which this reported as
    # a broken programme when it is how Mako is shaped. So where the first
    # entry resolves to nothing, descend once and try its first child, which
    # is what pressing it actually does.
    # `dict(out)` is a *shallow* copy, so the copy's "problems" is the very
    # same list - the failed first attempt's message leaked into the
    # successful retry, and two working Mako programmes were reported as
    # "the first episode resolved to nothing" while the same row said `hls`.
    # A row that contradicts itself is the tell.
    result = _check_stream(module, episode_ref, ref,
                           dict(out, problems=list(out["problems"])))
    if not result["problems"]:
        return result
    try:
        children = extractors.episodes(module, episode_ref)
    except Exception:
        children = []
    child_refs = [((c.get("extra") or {}).get("ref")
                   or (c.get("ids") or {}).get("vod")) for c in children]
    child_refs = [c for c in child_refs if c and c != episode_ref]
    if not child_refs:
        return result
    out["descended"] = True
    out["episodes"] = len(children)
    return _check_stream(module, child_refs[0], ref, out)


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
                               % str(exc)[:130])
        return out
    kind = looks_playable(body, content_type)
    out["kind"] = kind or "not a stream"
    if status not in (200, 206):
        out["problems"].append("the stream answered %s" % status)
    elif not kind:
        out["problems"].append("the stream is not a manifest or media (%s)"
                               % (content_type or "no content type"))
    return out


# ---------------------------------------------------------------------------
# The walk: a specific season and episode, through the code a press runs.
#
# Everything above checks the *extractors*. A viewer never calls one. They
# search a title, TMDB answers with a season and an episode, and `play.py`
# walks the broadcaster's pages looking for that episode - and that walk is
# where the bug was. This checker reported kan 24/24 the same week a press on
# season 4 of a Kan programme played season 6's episode, because it only ever
# resolved the *first* entry of a list and never asked for a particular one.
#
# So this asks the way a press asks: `play._broadcaster_stream(meta)`, which
# includes the catalogue lookup by name, with a season and an episode on the
# meta - and then checks that the episode which came back is the episode that
# was wanted, rather than merely that something came back.
# ---------------------------------------------------------------------------

# How many (season, episode) pairs to walk per programme. Three covers more
# than one season without turning a sample of eight programmes into a hundred
# page fetches.
WALK_TARGETS = 3
# Seasons to look for that the programme page does not link to. This is the
# case that broke: a Kan programme page carries episodes from seasons 1, 5
# and 6 while season 4 lives only at its own address.
PROBE_SEASONS = 4
# How far to look past the sample for a programme the walk can use.
WALK_SEARCH = 8


def _pairs(entries, force_season=0):
    """{(season, episode): (ref, entry)} for every entry stating an episode.

    `numbering.number` has already filled these in from the broadcaster's own
    titles, so this reads what the viewer sees on the screen rather than
    parsing the title a second time and possibly differently.
    """
    found = {}
    for entry in entries or []:
        episode = int(entry.get("episode") or 0)
        if not episode:
            continue
        ref = ((entry.get("extra") or {}).get("ref")
               or (entry.get("ids") or {}).get("vod") or "")
        if not ref:
            continue
        season = force_season or int(entry.get("season") or 0)
        found.setdefault((season, episode), []).append((ref, entry))
    # Two entries claiming the same number is a page this code has not
    # understood - `play._numbered` refuses such a pair outright, so asking
    # for it would be testing the refusal rather than testing the walk.
    return dict((key, rows[0]) for key, rows in found.items() if len(rows) == 1)


def discover_targets(module, ref, entries):
    """Season and episode pairs this programme really has, and where each is.

    Two sources, because the programme page is not the whole programme: what
    the page itself lists, and - for a broadcaster that gives a season an
    address of its own - the seasons that page never links to.
    """
    from pinky.vod import extractors

    targets = _pairs(entries)
    seen_seasons = set(season for season, _ in targets)

    handler = extractors.module_for(module)
    season_ref = getattr(handler, "season_ref", None)
    if season_ref is not None:
        for season in range(1, PROBE_SEASONS + 1):
            if season in seen_seasons:
                continue
            try:
                address = season_ref(ref, season)
            except Exception:
                continue
            if not address or address == ref:
                continue
            try:
                found = extractors.episodes(module, address) or []
            except Exception:
                continue
            # A season page that answers with the programme page again is a
            # redirect rather than a season, and its entries say so by
            # naming other seasons.
            found = [e for e in found
                     if int(e.get("season") or 0) in (0, season)]
            for key, value in _pairs(found, force_season=season).items():
                targets.setdefault(key, value)
            time.sleep(0.2)

    # **What TMDB asks for when the broadcaster numbers no season.** A
    # single-season programme states "episode 5" and nothing else, so its
    # entries carry season 0 - but TMDB has no season 0, and the press that
    # reaches `play.py` says season 1. If that is not walkable the programme
    # is unreachable from a search however well it lists.
    if targets and all(season == 0 for season, _ in targets):
        for (_, episode), value in list(targets.items()):
            targets.setdefault((1, episode), value)
    return targets


def _chosen(targets):
    """At most WALK_TARGETS pairs, spread over as many seasons as there are.

    Round-robin across seasons rather than the first three of a sorted list:
    three episodes of season six say nothing about season four, which is
    exactly the season that was broken.
    """
    by_season = collections.defaultdict(list)
    for key in sorted(targets):
        by_season[key[0]].append(key)
    picked, depth = [], 0
    while len(picked) < WALK_TARGETS and depth < 50:
        added = False
        for season in sorted(by_season):
            if depth < len(by_season[season]) and len(picked) < WALK_TARGETS:
                picked.append(by_season[season][depth])
                added = True
        if not added:
            break
        depth += 1
    return picked


def _ref_of(url):
    import urllib.parse
    query = urllib.parse.urlparse(url).query
    return (urllib.parse.parse_qs(query).get("ref") or [""])[0]


def _describe(entry):
    season = int(entry.get("season") or 0)
    episode = int(entry.get("episode") or 0)
    name = (entry.get("title") or "?")[:34]
    if season or episode:
        return "%dx%02d %s" % (season, episode, name)
    return name


def walk_programme(module, entry, kodi):
    """Ask for a particular episode the way a press does, and check it came."""
    from pinky import play
    from pinky.vod import extractors

    ref = entry.get("u") or ""
    name = entry.get("n") or "?"
    out = {"module": module, "programme": name, "ref": ref, "problems": [],
           "episodes": 0, "numbered": 0, "played": "", "kind": "",
           "walked": 0, "targets": []}
    proved = False

    try:
        entries = extractors.episodes(module, ref)
    except Exception as exc:
        out["problems"].append("listing raised %s" % type(exc).__name__)
        return out
    out["episodes"] = len(entries)
    out["numbered"] = sum(1 for e in entries if int(e.get("episode") or 0))
    if not entries:
        out["kind"] = "not a series"
        return out

    targets = discover_targets(module, ref, entries)
    if not targets:
        # Nothing numbered: a clip, a radio broadcast, a single film. There
        # is no "episode three" to ask for, so there is nothing here to
        # check - a fact about the programme, not a failure of the walk.
        out["kind"] = "not numbered"
        return out

    for season, episode in _chosen(targets):
        expected_ref, expected = targets[(season, episode)]
        meta = {"original_title": name, "original_language": "he",
                "title": name, "season": season, "episode": episode}
        try:
            url = play._broadcaster_stream(meta)
        except Exception as exc:
            out["problems"].append("%dx%02d: the walk raised %s"
                                   % (season, episode, type(exc).__name__))
            continue
        out["walked"] += 1
        if not url:
            out["problems"].append(
                "%dx%02d: the walk found nothing, though it is listed as %s"
                % (season, episode, _describe(expected)))
            continue
        got = _ref_of(url)
        if got != expected_ref and len(play._vod_alternatives(meta)) > 1:
            # **Two catalogue programmes of exactly this name, and both have
            # an episode of this number.** A Kan podcast and a Kan *kids*
            # podcast are both called סוכן חלל. TMDB gives a name and the
            # catalogue carries no id, so there is nothing left to tell them
            # apart - `play._broadcaster_stream` tries each and takes the
            # first that has the episode, which is the best anything can do.
            # Reporting it as a broken walk would be reporting the catalogue.
            out.setdefault("namesakes", []).append("%dx%02d" % (season, episode))
            continue
        if got != expected_ref:
            # Name what it *did* bring back, because "some other episode" and
            # "another season's episode of the same number" are different
            # bugs and the log line for both is identical.
            actual = ""
            for candidate in entries:
                candidate_ref = ((candidate.get("extra") or {}).get("ref")
                                 or (candidate.get("ids") or {}).get("vod"))
                if candidate_ref == got:
                    actual = _describe(candidate)
                    break
            out["problems"].append(
                "%dx%02d: the walk played %s instead"
                % (season, episode, actual or got[:70]))
            continue
        out["targets"].append("%dx%02d" % (season, episode))
        if not proved:
            proved = True
            # One of them is proved all the way to bytes. Doing it for every
            # target would treble the run to answer a question the breadth
            # check above already answers.
            _check_stream(module, got, ref, out)
    if not out["walked"]:
        out["kind"] = out["kind"] or "not walkable"
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
    parser.add_argument("--programme", default="",
                        help="only programmes whose name contains this")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--walk", action="store_true",
                        help="ask for a particular season and episode, "
                             "through play.py, the way a press does")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    kodi = boot()
    from pinky.vod import extractors, library

    # `load()` and not `_read_seed()`: the catalogue is unescaped on the way
    # out, so the raw seed holds "הצ&#x27;ייסר מציג" where every other part
    # of the add-on - the search box, the rows, play.py's name join - sees
    # the apostrophe. Checking against the raw file reported a working
    # programme as unreachable.
    entries = [dict(e) for e in library.load()]
    by_module = collections.defaultdict(list)
    for entry in entries:
        by_module[entry.get("m") or "?"].append(entry)

    wanted = args.module or extractors.supported()
    results, failures, skipped = [], 0, 0
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
            chosen = sample(members, share, args.seed)
            if args.walk:
                # **The walk needs a programme with episodes in it.** Most of
                # a broadcaster's catalogue has no episode numbers at all -
                # 266 of Kan's 827 entries are podcasts and another 205 are
                # radio stations, and a radio broadcast has no "episode
                # three" to ask for. A plain sample spent two thirds of its
                # requests proving that radio is radio, so this draws from a
                # wider pool and keeps going until it has `share` programmes
                # it could actually walk.
                chosen = sample(members, min(len(members), share * WALK_SEARCH),
                                args.seed)
            if args.programme:
                chosen = [e for e in members
                          if args.programme in (e.get("n") or "")]
                if not chosen:
                    continue
            walked = 0
            for entry in chosen:
                step = walk_programme if args.walk else check_programme
                result = step(module, entry, kodi)
                result["category"] = category
                if (args.walk and not args.programme and not result["problems"]
                        and not result["walked"]):
                    # Nothing to walk and nothing wrong with it. Counted,
                    # not printed: a column of "not numbered" buries the
                    # three lines that are the point of the run.
                    skipped += 1
                    time.sleep(0.2)
                    continue
                results.append(result)
                mark = "ok  " if not result["problems"] else "FAIL"
                print("    %s %-28s %3d eps, %3d numbered, %-9s %s"
                      % (mark, (result["programme"] or "?")[:28],
                         result["episodes"], result["numbered"],
                         result["kind"] or "-",
                         " ".join(result.get("targets") or [])
                         + ("  [%s: another programme of the same name]"
                            % " ".join(result["namesakes"])
                            if result.get("namesakes") else "")))
                for problem in result["problems"]:
                    print("          - %s" % problem)
                if result["problems"]:
                    failures += 1
                walked += 1
                if args.walk and not args.programme and walked >= share:
                    break
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

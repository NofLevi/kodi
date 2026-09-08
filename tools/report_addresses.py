# -*- coding: utf-8 -*-
"""What the two addresses each found, across a survey of episodes.

`survey_sources.py --episodes` records two numbers per episode: how many
sources the address TMDB gives us returns, and how many the address the anime
indexes file it under returns. This reads that file and answers the only
questions that matter about the second one:

* how often the first address finds nothing and the second finds something -
  which is the bug that started this, counted rather than assumed;
* how often the second address is tried and finds nothing anyway, which is
  the cost of having it;
* whether either of those is peculiar to anime or happens elsewhere too.

    python tools/report_addresses.py [survey.jsonl]
"""
from __future__ import print_function

import io
import json
import os
import sys


def load(path):
    rows = []
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def percent(part, whole):
    return "%5.1f%%" % (100.0 * part / whole) if whole else "    -"


def group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(row.get(key) or "?", []).append(row)
    return out


def summarise(name, rows):
    """One line of the table, plus the address columns."""
    total = len(rows)
    if not total:
        return
    # An episode that has not gone out yet has no sources for a good reason.
    aired = [r for r in rows if r.get("aired", True)]
    n = len(aired) or 1

    nothing = [r for r in aired if not r.get("found")]
    tried = [r for r in aired if r.get("kitsu_address")]
    rescued = [r for r in tried
               if not r.get("found_tmdb_address") and r.get("found_kitsu_address")]
    helped = [r for r in tried if r.get("found_kitsu_address")]
    wasted = [r for r in tried if not r.get("found_kitsu_address")]

    print("%-28s %4d aired  nothing %-7s | second address: tried %-6s "
          "found %-7s rescued %-7s wasted %s"
          % (name[:28], len(aired), percent(len(nothing), n),
             percent(len(tried), n), percent(len(helped), n),
             percent(len(rescued), n), percent(len(wasted), n)))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "survey.jsonl")
    rows = [r for r in load(path) if not r.get("error")]
    if not rows:
        print("nothing to report in %s" % path)
        return 1

    print("%d episodes surveyed\n" % len(rows))
    summarise("everything", rows)
    print()
    for name, group_rows in sorted(group(rows, "stratum").items()):
        summarise(name, group_rows)

    print()
    anime = [r for r in rows if r.get("anime")]
    print("anime by TMDB's own genre: %d" % len(anime))
    summarise("  anime", anime)
    summarise("  everything else", [r for r in rows if not r.get("anime")])

    rescued = [r for r in rows
               if r.get("kitsu_address") and not r.get("found_tmdb_address")
               and r.get("found_kitsu_address")]
    print("\nepisodes that would find nothing without the second address: %d"
          % len(rescued))
    for row in sorted(rescued, key=lambda r: -r.get("found_kitsu_address", 0))[:25]:
        print("   %-38s S%02dE%02d  %-18s %3d sources"
              % ((row.get("resolved_title") or "")[:38],
                 int(row.get("season") or 0), int(row.get("episode") or 0),
                 row.get("kitsu_address", ""), row.get("found_kitsu_address", 0)))

    still = [r for r in rows if r.get("aired", True) and not r.get("found")
             and r.get("anime")]
    print("\nanime episodes still finding nothing: %d" % len(still))
    for row in still[:25]:
        print("   %-38s S%02dE%02d  %s"
              % ((row.get("resolved_title") or "")[:38],
                 int(row.get("season") or 0), int(row.get("episode") or 0),
                 row.get("kitsu_address") or "no second address"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

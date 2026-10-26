# -*- coding: utf-8 -*-
"""Read season and episode numbers out of a broadcaster's own titles.

Only Reshet ever filled these in. Kan set a season and no episode; Mako,
Now14, Sport1, Sport5 and the radio set neither - so most of an Israeli VOD
programme listed as a column of bare names with nothing to say which episode
was which, or which came first.

The broadcasters do number their episodes; they write it in the title rather
than in a field. "עונה 2 פרק 4" and "פרק 12" are the two shapes, and they are
the same ones Reshet's own parser has used since it was written - this is
that parser, moved somewhere every provider passes through.

Applied in `extractors.episodes`, which is the single choke point for every
provider's episode list, so a provider added tomorrow is numbered without
being asked to do anything.
"""
import re

SEASON_EPISODE = re.compile(u"עונה\\s*(\\d+)\\s*,?\\s*פרק\\s*(\\d+)")
EPISODE_ONLY = re.compile(u"פרק\\s*(\\d+)")

# A trailing "- 12" or "(12)" is a number often enough to be worth reading,
# but only when the title says nothing else: a date, a score or a year must
# not become an episode number, so four digits are refused.
TRAILING_NUMBER = re.compile(r"(?:^|[\s\-–(\[])(\d{1,3})\s*[)\]]?\s*$")


def season_episode(text):
    """(season, episode) from a title, or (0, 0) when it says nothing."""
    text = text or ""
    match = SEASON_EPISODE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = EPISODE_ONLY.search(text)
    if match:
        return 0, int(match.group(1))
    match = TRAILING_NUMBER.search(text)
    if match:
        return 0, int(match.group(1))
    return 0, 0


def number(entries):
    """Fill in what the broadcaster wrote in the title. Modifies in place.

    Never overrules a provider that filled the fields itself: Reshet reads
    them from its API, which is better than any title parse, and a season
    Kan took from the URL is better than one guessed from the name.
    """
    for entry in entries or []:
        if entry.get("type") != "vod":
            continue
        if entry.get("episode"):
            continue
        season, episode = season_episode(entry.get("title"))
        if not episode:
            continue
        entry["episode"] = episode
        if season and not entry.get("season"):
            entry["season"] = season
    return entries

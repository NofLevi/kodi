"""TorrentsDB.

A second server-side aggregator, and the reason it is here is that until now
there was effectively one: Comet and MediaFusion both refuse without a
configuration blob from their own web sites, Zilean's public instance answers
404 on every path including its own healthcheck, and the two anime providers
only run for anime. Everything else rested on Torrentio alone.

Measured against Torrentio on the same titles: 90 results to Torrentio's 63
for a film, 50 for an episode, every one carrying an infohash. It needs no
configuration and no account, so unlike Torrentio it is asked anonymously -
this add-on does its own debrid cache checking anyway, and a key not sent is
a key that cannot leak.

It speaks the same Stremio protocol as the others, so the shared adapter does
all the work.
"""
from ... import settings
from . import stremio

NAME = "torrentsdb"
BASE = "https://torrentsdb.com"


def search(meta):
    return stremio.fetch(settings.get("sources.torrentsdb.url", BASE),
                         "", meta, NAME)

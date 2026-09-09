# -*- coding: utf-8 -*-
"""The ad-free way to play a Kaltura entry.

Two Israeli broadcasters put their catalogue on Kaltura, and both have a
second, ad-inserting layer in front of it. Reshet's OTT endpoint answers
`asset/getPlaybackContext` with a URL on `hub13.g-mana.live`, which is
server-side ad insertion: the adverts are spliced into the same HLS timeline as
the programme, as discontinuities. There is no ad *period* to skip and no
marker Kodi acts on - measured on one episode of `החברים של נאור`, the player
showed **fifteen chapters** and started on an advert.

What a browser on 13tv.co.il actually plays is not that. It asks plain Kaltura
- `cdnapisec.kaltura.com`, no OTT layer - for the same entry, and gets the CDN
manifest with nothing inserted. Measured on the same episode, the same six
flavour ids: **zero** `EXT-X-DISCONTINUITY`, zero `CUE-OUT`, zero `SCTE35`, and
both HLS and DASH offered without DRM.

So this is not ad *blocking*, which would mean parsing manifests and guessing.
It is asking the question the other way round, and the adverts were never in
the answer. The approach is taken from the Idan Plus add-on, which is what the
Kodi POV IL build uses for Israeli content.

The one thing worth knowing before touching it: the entry id is not the OTT
asset id. It comes back on the OTT playback source as `externalId`, shaped
`1_1p23hf83_1_fop805w6` - the entry, then the flavour - so no extra request is
needed to find it.
"""
import json

from .. import cache, http, kodi

API = "https://cdnapisec.kaltura.com/api_v3/service/multirequest"

# A resolved manifest is good for a while and the round trip is three chained
# calls, so it is worth not making twice.
TTL = 30 * 60


def entry_id(external_id):
    """The Kaltura entry out of an OTT source's externalId.

    `1_1p23hf83_1_fop805w6,1_1p23hf83_1_s6xebfi3` is one entry and several
    flavours, so the first two underscore-separated parts are the answer and
    everything after is which rendition. Returns "" for anything else rather
    than guessing, because a wrong entry id plays somebody else's programme.
    """
    first = (external_id or "").split(",")[0].strip()
    parts = first.split("_")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1]:
        return ""
    return "%s_%s" % (parts[0], parts[1])


def _request(entry, partner, referer):
    """Widget session, resolve the entry, then ask how to play it.

    One multirequest rather than three round trips: Kaltura substitutes
    `{1:result:ks}` and `{2:result:objects:0:id}` from the earlier answers, so
    the session and the redirect lookup cost nothing extra.

    `redirectFromEntryId` rather than `entryId` on the lookup, because an entry
    can be replaced by another and the old id still published.
    """
    payload = {
        "1": {"service": "session", "action": "startWidgetSession",
              "widgetId": "_%s" % partner},
        "2": {"service": "baseEntry", "action": "list", "ks": "{1:result:ks}",
              "filter": {"redirectFromEntryId": entry},
              "responseProfile": {"type": 1,
                                  "fields": "id,name,duration,mediaType"}},
        "3": {"service": "baseEntry", "action": "getPlaybackContext",
              "entryId": "{2:result:objects:0:id}", "ks": "{1:result:ks}",
              "contextDataParams": {"objectType": "KalturaContextDataParams",
                                    "flavorTags": "all"}},
        "apiVersion": "3.3.0", "format": 1, "ks": "",
        "clientTag": "html5:v0.56.1", "partnerId": partner,
    }
    return http.post_json(
        API, default=None, data=json.dumps(payload),
        headers={"Content-Type": "application/json", "Referer": referer},
        timeout=(5, 15))


def playback_url(entry, partner, referer, prefer_dash=False):
    """An ad-free manifest for one entry, or "" if this route cannot answer.

    Returns (url, adaptive). Never raises: every caller has a working
    ad-carrying URL already and must be able to fall back to it.
    """
    entry = (entry or "").strip()
    if not entry:
        return "", False

    key = "kaltura:%s:%s:%s" % (partner, entry, "dash" if prefer_dash else "hls")
    cached = cache.get(key)
    if cached:
        return cached, True

    try:
        answer = _request(entry, partner, referer)
    except Exception:
        kodi.log_exception("kaltura: asking for %s failed" % entry)
        return "", False
    if not isinstance(answer, list) or len(answer) < 3:
        kodi.log("kaltura: unexpected answer shape for %s" % entry)
        return "", False

    context = answer[2] or {}
    # An error comes back in the same slot as a result, which is why this
    # cannot simply read `sources` and hope.
    if isinstance(context, dict) and context.get("objectType") == "KalturaAPIException":
        kodi.log("kaltura: %s for %s"
                 % (context.get("message", "refused"), entry))
        return "", False

    sources = [s for s in (context.get("sources") or []) if s.get("url")]
    if not sources:
        kodi.log("kaltura: no playable source for %s" % entry)
        return "", False

    # Clear before encrypted, in that order of preference, and only then
    # anything at all. Reshet offers FairPlay on the same entry for Apple
    # clients and Kodi cannot open it.
    wanted = ["mpegdash", "applehttp"] if prefer_dash else ["applehttp",
                                                            "mpegdash"]
    for fmt in wanted:
        for source in sources:
            if source.get("format") == fmt and not source.get("drm"):
                cache.set(key, source["url"], TTL)
                return source["url"], True

    kodi.log("kaltura: only DRM sources for %s" % entry)
    return "", False

"""Tickets for the live streams a broadcaster puts behind an Akamai token.

Some Israeli live streams are free to watch — the broadcaster's own site plays
them with no login, no subscription and no account — but the CDN in front of
them refuses any request that does not carry a ticket minted by the
broadcaster's entitlement service. Without one the answer is a bare 403, which
is why a third of the television list was hidden.

Mako, which carries Keshet 12 and its sister channels, mints a ticket from a
single GET. Two properties of what it returns are what make this cheap enough
to belong in this add-on at all:

* The ticket is granted for ``acl=/*`` rather than for one path, so a single
  ticket signs every Keshet channel. One request covers the broadcaster, not
  one request per channel.
* Akamai rewrites the variant URLs inside the master manifest with a much
  longer lived path token, so only the very first request needs signing. Child
  manifests and media segments carry their own authorisation and are fetched
  unsigned, which was measured rather than assumed.

Together those mean a ticket costs one request per ten minutes across a whole
broadcaster, and nothing at all during playback: a stream keeps playing for
hours after the ticket that started it has expired. That is the difference
between a feature that fits on a one gigabyte box and a token refresh loop
running behind every live channel.

When minting fails the unsigned URL is returned rather than an empty one. Kodi
then reports a playback error, which is the honest outcome; silently returning
nothing would look like a channel that does not exist.
"""
from .. import cache, http, kodi

MAKO_ENTITLEMENTS = ("https://mass.mako.co.il/ClicksStatistics/"
                     "entitlementsServicesV2.jsp")
MAKO_REFERER = "https://www.mako.co.il/"

# The service grants fifteen minutes. Ten is cached so a ticket handed to the
# player still has several minutes of validity left, which matters only for the
# one request that actually needs it.
TICKET_TTL = 600

# These sites answer a default urllib user agent with a 403, so the request has
# to look like the browser the stream is meant for.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def sign(url, provider, path=""):
    """Append a broadcaster ticket to a stream URL.

    Returns the URL unchanged when the provider is unknown or the service does
    not answer, so a failure degrades to the 403 it would have been anyway
    rather than to a channel that silently vanishes.
    """
    minter = _MINTERS.get(provider)
    if not url or minter is None:
        if url and provider:
            kodi.log("no ticket minter for %s" % provider)
        return url
    ticket = minter(path or _path_of(url))
    if not ticket:
        return url
    return url + ("&" if "?" in url else "?") + ticket


def mako_ticket(path, refresh=False):
    """A Mako entitlement ticket, cached because one covers every channel."""
    # The key deliberately carries no path: the grant is acl=/*, so caching per
    # path would mint a dozen interchangeable tickets while browsing the list.
    key = cache.make_key("vod", "ticket", "mako")
    if not refresh:
        cached = cache.get(key)
        if cached:
            return cached

    ticket = _mint_mako(path)
    if ticket:
        cache.set(key, ticket, TICKET_TTL)
    return ticket


def _mint_mako(path):
    payload = http.get_json(
        MAKO_ENTITLEMENTS,
        params={"et": "gt", "lp": path, "rv": "AKAMAI"},
        headers={"User-Agent": BROWSER_UA,
                 "Referer": MAKO_REFERER,
                 "Accept": "application/json, text/plain, */*"},
        timeout=(4, 8),
        default=None)

    if not isinstance(payload, dict):
        kodi.log("the mako entitlement service did not answer")
        return ""
    if payload.get("status") != "Success":
        kodi.log("the mako entitlement service refused: %s"
                 % payload.get("status"))
        return ""

    for entry in payload.get("tickets") or []:
        ticket = (entry or {}).get("ticket")
        if ticket:
            return ticket

    kodi.log("the mako entitlement service returned no ticket")
    return ""


def _path_of(url):
    """The path and query of a URL, which is what the service signs."""
    if not url.startswith("http"):
        return url
    rest = url.split("/", 3)
    return "/" + rest[3] if len(rest) > 3 else "/"


_MINTERS = {"mako": mako_ticket}


def providers():
    """Which broadcasters this module can sign for, for the device report."""
    return sorted(_MINTERS)

"""Tickets for the live streams a broadcaster puts behind an Akamai token.

Some Israeli live streams are free to watch — the broadcaster's own site plays
them with no login, no subscription and no account — but the CDN in front of
them refuses any request that does not carry a ticket minted by the
broadcaster's entitlement service. Without one the answer is a bare 403, which
is why a third of the television list was hidden.

Mako, which carries Keshet 12 and its sister channels, mints a ticket from a
single GET. It serves from two CDNs and signs each differently, which the same
endpoint handles through its ``rv`` parameter:

* **Live**, on Akamai, is signed with ``rv=AKAMAI`` and answers an ``hdnea``
  token granted for ``acl=/*``. One ticket therefore signs every Keshet
  channel: one request covers the broadcaster, not one per channel. Akamai
  then rewrites the variant URLs inside the master manifest with a much longer
  lived path token, so only the very first request needs signing at all. Child
  manifests and media segments carry their own authorisation and were fetched
  unsigned to confirm it.
* **On demand**, on CloudFront, is signed with ``rv=AWS`` and answers a
  ``token`` JWT with the stream's path inside it. That one is good for exactly
  the path it was minted for, so unlike the live ticket it cannot be shared and
  is cached per path.

Asking for the wrong vendor is not a soft failure: CloudFront answers an
Akamai token with "Missing token query parameter" and Akamai answers nothing
useful without one, which is why the vendor is chosen from the host rather than
configured.

Together this means a live ticket costs one request per ten minutes across a
whole broadcaster and nothing during playback - a stream keeps playing for
hours after the ticket that started it expired - and a VOD ticket costs one
request per episode opened. That is the difference between a feature that fits
on a one gigabyte box and a token refresh loop behind every stream.

When minting fails the unsigned URL is returned rather than an empty one. Kodi
then reports a playback error, which is the honest outcome; silently returning
nothing would look like a channel that does not exist.
"""
from .. import cache, http, kodi

MAKO_ENTITLEMENTS = ("https://mass.mako.co.il/ClicksStatistics/"
                     "entitlementsServicesV2.jsp")
MAKO_REFERER = "https://www.mako.co.il/"

# Which CDN is being asked. The service mints a different ticket for each.
VENDOR_AKAMAI = "AKAMAI"
VENDOR_AWS = "AWS"

# Hosts that CloudFront serves, and so want the AWS ticket rather than the
# Akamai one. Matching on the host is deliberate: the two are not
# interchangeable and the failure is a bare 403 with no explanation.
AWS_HOSTS = ("cloudfront.net", "amazonaws.com")

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

    ticket = minter(path or _path_of(url), vendor_for(url))
    if not ticket:
        return url

    # A LEVEL3 ticket arrives as a whole query string; the others are one
    # parameter. Both shapes are handled so the caller never has to know.
    if ticket.startswith("?"):
        return url.split("?")[0] + ticket
    return url + ("&" if "?" in url else "?") + ticket


def vendor_for(url):
    """Which CDN signs this host.

    Chosen from the host rather than configured, because the two tickets are
    not interchangeable: CloudFront answers an Akamai token with "Missing token
    query parameter" and Akamai will not take an AWS one.
    """
    host = _host_of(url)
    return VENDOR_AWS if any(h in host for h in AWS_HOSTS) else VENDOR_AKAMAI


def mako_ticket(path, vendor=VENDOR_AKAMAI, refresh=False):
    """A Mako entitlement ticket for one CDN."""
    # The Akamai grant is acl=/*, so its key carries no path: caching per path
    # would mint a dozen interchangeable tickets while browsing the channel
    # list. The AWS ticket is a JWT with the path inside it and is good for
    # nothing else, so that one has to be keyed by path.
    scope = path if vendor == VENDOR_AWS else ""
    key = cache.make_key("vod", "ticket", "mako", vendor, scope)
    if not refresh:
        cached = cache.volatile_get(key)
        if cached:
            return cached

    ticket = _mint_mako(path, vendor)
    if ticket:
        cache.volatile_set(key, ticket, TICKET_TTL)
    return ticket


def _mint_mako(path, vendor=VENDOR_AKAMAI):
    payload = http.get_json(
        MAKO_ENTITLEMENTS,
        params={"et": "gt", "lp": path, "rv": vendor},
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

    # An empty ticket list is a real answer, not a failure: the service returns
    # one for a vendor it does not sign that path for.
    kodi.log("the mako entitlement service issued no %s ticket" % vendor)
    return ""


def _path_of(url):
    """The path and query of a URL, which is what the service signs."""
    if not url.startswith("http"):
        return url
    rest = url.split("/", 3)
    return "/" + rest[3] if len(rest) > 3 else "/"


def _host_of(url):
    try:
        return url.split("/")[2].lower()
    except IndexError:
        return ""


_MINTERS = {"mako": mako_ticket}


def providers():
    """Which broadcasters this module can sign for, for the device report."""
    return sorted(_MINTERS)

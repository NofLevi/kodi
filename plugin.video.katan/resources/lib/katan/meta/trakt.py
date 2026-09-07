"""Trakt API v2 client.

Covers the three things the add-on needs: device authentication, the public
charts used by home rows, and the personal sync data behind Continue Watching
and watched ticks.

Rate limits are 1000 GET per 5 minutes and 1 POST per second, so writes are
serialised through a small gate and reads lean on the cache.
"""
import threading
import time

from .. import cache, http, kodi, settings
from . import items

API_BASE = "https://api.trakt.tv"
TTL_CHART = 3 * 3600
TTL_SYNC = 900

_post_lock = threading.Lock()
_last_post = [0.0]


def client_id():
    return settings.get("trakt.client_id", "").strip()


def client_secret():
    return settings.get("trakt.client_secret", "").strip()


def configured():
    """A client id alone is enough for the public charts."""
    return bool(client_id())


def authorised():
    return bool(settings.get("trakt.access_token"))


def _headers(auth=False):
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id(),
    }
    if auth:
        token = valid_token()
        if token:
            headers["Authorization"] = "Bearer %s" % token
    return headers


def _get(path, auth=False, ttl=0, **params):
    """GET a Trakt endpoint, optionally through the cache."""
    if not configured():
        return None
    url = "%s%s" % (API_BASE, path)

    def fetch():
        return http.get_json(url, params=params or None,
                             headers=_headers(auth), default=None)

    if ttl > 0 and not auth:
        return cache.cached(cache.make_key("trakt", path, sorted(params.items())),
                            fetch, ttl)
    return fetch()


def _post(path, payload=None, auth=True):
    """POST with the documented one-per-second pacing."""
    if not configured():
        return None
    with _post_lock:
        wait = 1.0 - (time.time() - _last_post[0])
        if wait > 0:
            time.sleep(wait)
        _last_post[0] = time.time()
    return http.post("%s%s" % (API_BASE, path), json=payload or {},
                     headers=_headers(auth))


# --------------------------------------------------------------------------
# device authentication
# --------------------------------------------------------------------------


def device_code():
    """Start the device flow. Returns the payload with user_code and URL."""
    response = http.post("%s/oauth/device/code" % API_BASE,
                         json={"client_id": client_id()},
                         headers={"Content-Type": "application/json"})
    if response is None or response.status_code != 200:
        return None
    return response.json()


def exchange_device_token(device):
    """One attempt at swapping the device code for a token.

    Three answers, not two, and the third one matters. The token means the
    viewer has finished on their phone; False means they have not yet; None
    means this attempt is over for good - expired, denied, or already used -
    and the screen should say so rather than sit there for ten minutes
    waiting for something that is never going to arrive.

    One attempt rather than a loop because the sign-in window owns the
    waiting: it has a code on it that the viewer is looking at, and a
    function that blocks for ten minutes cannot draw one.
    """
    if kodi.abort_requested():
        return None
    response = http.post("%s/oauth/device/token" % API_BASE, json={
        "code": device.get("device_code"),
        "client_id": client_id(),
        "client_secret": client_secret(),
    }, headers={"Content-Type": "application/json"})
    if response is None:
        return False                      # a blip, not an answer
    if response.status_code == 200:
        return _store_token(response.json())
    if response.status_code in (400, 429):
        # 400 is "still pending"; 429 is "slow down", and the window's own
        # interval is what paces this, so both mean keep waiting.
        return False
    # 404 invalid, 409 already used, 410 expired, 418 denied
    kodi.log("device auth stopped with HTTP %s" % response.status_code)
    return None


def _store_token(payload):
    if not payload or not payload.get("access_token"):
        return None
    expires = int(payload.get("created_at") or time.time()) + int(payload.get("expires_in") or 0)
    settings.set_many({
        "trakt.access_token": payload["access_token"],
        "trakt.refresh_token": payload.get("refresh_token", ""),
        "trakt.expires": str(expires),
    })
    profile = _get("/users/me", auth=True)
    if profile:
        settings.set("trakt.user", profile.get("username", ""))
    return payload["access_token"]


def valid_token():
    """Return a live access token, refreshing it when it is close to expiry."""
    token = settings.get("trakt.access_token")
    if not token:
        return ""
    expires = settings.get_int("trakt.expires")
    if expires and expires - time.time() > 86400:
        return token
    return refresh_token() or token


def refresh_token():
    refresh = settings.get("trakt.refresh_token")
    if not refresh:
        return ""
    response = http.post("%s/oauth/token" % API_BASE, json={
        "refresh_token": refresh,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "grant_type": "refresh_token",
    }, headers={"Content-Type": "application/json"})
    if response is None or response.status_code != 200:
        kodi.log("Trakt token refresh failed")
        return ""
    return _store_token(response.json()) or ""


def revoke():
    token = settings.get("trakt.access_token")
    if token:
        http.post("%s/oauth/revoke" % API_BASE, json={
            "token": token,
            "client_id": client_id(),
            "client_secret": client_secret(),
        }, headers={"Content-Type": "application/json"})
    settings.set_many({
        "trakt.access_token": "", "trakt.refresh_token": "",
        "trakt.expires": "0", "trakt.user": "",
    })


# --------------------------------------------------------------------------
# converting Trakt payloads into items
# --------------------------------------------------------------------------


def _from_trakt(node, item_type):
    """Convert a movie or show node. Trakt gives ids and little art, so the
    poster is filled in later from TMDB when the row is rendered."""
    if not node:
        return None
    ids = node.get("ids") or {}
    return items.new_item(
        "movie" if item_type == "movie" else "show",
        ids={
            "trakt": ids.get("trakt") or 0,
            "imdb": ids.get("imdb") or "",
            "tmdb": ids.get("tmdb") or 0,
            "tvdb": ids.get("tvdb") or 0,
            "slug": ids.get("slug") or "",
        },
        title=node.get("title") or "",
        year=int(node.get("year") or 0),
        plot=node.get("overview") or "",
        rating=float(node.get("rating") or 0.0),
        votes=int(node.get("votes") or 0),
        genres=node.get("genres") or [],
        duration=int(node.get("runtime") or 0) * 60,
        mpaa=node.get("certification") or "",
    )


def _unwrap(rows, item_type):
    """Charts wrap the media node in an envelope; personal lists sometimes do not."""
    out = []
    key = "movie" if item_type == "movie" else "show"
    for row in rows or []:
        node = row.get(key) if isinstance(row, dict) and key in row else row
        item = _from_trakt(node, item_type)
        if item:
            out.append(item)
    return out


def _fill_art(entries):
    """Trakt has no artwork, so borrow it from TMDB where we have an id.

    Only rows that will actually be shown get this treatment, and each lookup
    is individually cached, so a warmed row costs nothing.
    """
    from . import tmdb
    if not tmdb.has_key():
        return entries
    for item in entries:
        if item.get("art", {}).get("poster"):
            continue
        tmdb_id = (item.get("ids") or {}).get("tmdb")
        if not tmdb_id:
            continue
        detail = tmdb.movie(tmdb_id) if item["type"] == "movie" else tmdb.show(tmdb_id)
        if detail:
            item["art"] = detail.get("art", {})
            item["plot"] = item.get("plot") or detail.get("plot", "")
            item["genres"] = item.get("genres") or detail.get("genres", [])
    return entries


# --------------------------------------------------------------------------
# public charts
# --------------------------------------------------------------------------

_CHART_PATHS = {
    "trending": "/%s/trending",
    "popular": "/%s/popular",
    "anticipated": "/%s/anticipated",
    "boxoffice": "/%s/boxoffice",
    "watched": "/%s/watched/weekly",
    "collected": "/%s/collected/weekly",
}


def chart(media_type, name, limit=20):
    """media_type is "movies" or "shows"; name is a key of _CHART_PATHS."""
    path = _CHART_PATHS.get(name)
    if not path or not configured():
        return []
    rows = _get(path % media_type, ttl=TTL_CHART, limit=limit, extended="full")
    item_type = "movie" if media_type == "movies" else "show"
    return _fill_art(_unwrap(rows, item_type)[:limit])


# --------------------------------------------------------------------------
# personal sync
# --------------------------------------------------------------------------


def watchlist(limit=20):
    if not authorised():
        return []
    rows = _get("/users/me/watchlist/movies", auth=True, extended="full") or []
    entries = _unwrap(rows, "movie")
    rows = _get("/users/me/watchlist/shows", auth=True, extended="full") or []
    entries.extend(_unwrap(rows, "show"))
    return _fill_art(entries[:limit])


def continue_watching(limit=20):
    """Paused playback plus the next unwatched episode of shows in progress."""
    if not authorised():
        return []
    entries = []
    for row in _get("/sync/playback", auth=True, limit=50) or []:
        progress = float(row.get("progress") or 0)
        if progress <= 1 or progress >= 95:
            continue
        if row.get("type") == "movie":
            item = _from_trakt(row.get("movie"), "movie")
        elif row.get("type") == "episode":
            item = _episode_item(row.get("episode"), row.get("show"))
        else:
            continue
        if not item:
            continue
        item["resume"] = {"position": progress, "total": 100}
        item["extra"]["paused_at"] = row.get("paused_at") or ""
        entries.append(item)
    return _fill_art(entries[:limit])


def _episode_item(episode, show):
    if not episode:
        return None
    show_item = _from_trakt(show, "show") or {}
    ids = episode.get("ids") or {}
    return items.new_item(
        "episode",
        ids={"trakt": ids.get("trakt") or 0, "tmdb": ids.get("tmdb") or 0,
             "imdb": ids.get("imdb") or ""},
        title=episode.get("title") or "",
        season=int(episode.get("season") or 0),
        episode=int(episode.get("number") or 0),
        show_title=show_item.get("title", ""),
        plot=episode.get("overview") or "",
        extra={"show_ids": show_item.get("ids", {}),
               "tmdb_show": (show_item.get("ids") or {}).get("tmdb")},
    )


def last_finished():
    """The most recently completed title, used to seed recommendations."""
    if not authorised():
        return None
    rows = _get("/sync/history", auth=True, limit=1, extended="full") or []
    for row in rows:
        if row.get("type") == "movie":
            return _from_trakt(row.get("movie"), "movie")
        if row.get("type") == "episode":
            return _from_trakt(row.get("show"), "show")
    return None


# --------------------------------------------------------------------------
# writes: watchlist, watched state, scrobbling
# --------------------------------------------------------------------------


def _media_payload(item_type, ids, season=None, episode=None):
    """Build the {movies|shows|episodes: [...]} body Trakt expects."""
    id_block = {}
    if ids.get("trakt"):
        id_block["trakt"] = int(ids["trakt"])
    if ids.get("imdb"):
        id_block["imdb"] = ids["imdb"]
    if ids.get("tmdb"):
        id_block["tmdb"] = int(ids["tmdb"])
    if not id_block:
        return None
    if item_type == "movie":
        return {"movies": [{"ids": id_block}]}
    if item_type == "show" and season is None:
        return {"shows": [{"ids": id_block}]}
    return {"shows": [{
        "ids": id_block,
        "seasons": [{"number": int(season or 0),
                     "episodes": [{"number": int(episode or 0)}]}],
    }]}


def add_to_watchlist(item_type, tmdb_id):
    if not authorised():
        kodi.notify(kodi.localize(32272))
        return False
    payload = _media_payload(item_type, {"tmdb": tmdb_id})
    if not payload:
        return False
    response = _post("/sync/watchlist", payload)
    return bool(response is not None and response.status_code in (200, 201))


def set_watched(item_type, ids, season=None, episode=None, watched=True):
    if not authorised():
        return False
    payload = _media_payload(item_type, ids, season, episode)
    if not payload:
        return False
    path = "/sync/history" if watched else "/sync/history/remove"
    response = _post(path, payload)
    ok = bool(response is not None and response.status_code in (200, 201))
    if ok:
        invalidate_sync()
    return ok


def scrobble(action, item_type, ids, season=None, episode=None, progress=0.0):
    """action is start, pause or stop. Trakt marks watched at stop above 80%."""
    if not authorised():
        return False
    id_block = {}
    for key in ("trakt", "imdb", "tmdb"):
        if ids.get(key):
            id_block[key] = ids[key]
    if not id_block:
        return False
    if item_type == "movie":
        payload = {"movie": {"ids": id_block}, "progress": round(float(progress), 2)}
    else:
        payload = {
            "show": {"ids": id_block},
            "episode": {"season": int(season or 0), "number": int(episode or 0)},
            "progress": round(float(progress), 2),
        }
    response = _post("/scrobble/%s" % action, payload)
    ok = bool(response is not None and response.status_code in (200, 201, 409))
    if ok and action == "stop":
        invalidate_sync()
    return ok


# --------------------------------------------------------------------------
# the local mirror the UI reads
# --------------------------------------------------------------------------

ACTIVITY_KEY = "trakt|last_activities"


def last_activities():
    return _get("/sync/last_activities", auth=True)


def needs_sync():
    """True when Trakt reports activity newer than our snapshot.

    This is the whole point of last_activities: one small request tells us
    whether the expensive watched/playback pulls are worth doing at all.
    """
    if not authorised():
        return False
    current = last_activities()
    if not current:
        return False
    previous = cache.get(ACTIVITY_KEY)
    if previous == current:
        return False
    cache.set(ACTIVITY_KEY, current, 30 * 24 * 3600)
    return True


def sync_state():
    """Refresh the watched and playback snapshot used for ticks and resume."""
    if not authorised():
        return False
    from . import trakt_state

    watched = {}
    for row in _get("/sync/watched/movies", auth=True) or []:
        ids = (row.get("movie") or {}).get("ids") or {}
        key = trakt_state.state_key("movie", {"tmdb": ids.get("tmdb"),
                                              "imdb": ids.get("imdb")})
        watched[key] = int(row.get("plays") or 1)

    for row in _get("/sync/watched/shows", auth=True) or []:
        show_ids = (row.get("show") or {}).get("ids") or {}
        base = {"tmdb": show_ids.get("tmdb"), "imdb": show_ids.get("imdb")}
        watched[trakt_state.state_key("show", base)] = 1
        for season in row.get("seasons") or []:
            number = season.get("number")
            for episode in season.get("episodes") or []:
                key = trakt_state.state_key("episode", base, number, episode.get("number"))
                watched[key] = int(episode.get("plays") or 1)

    playback = {}
    for row in _get("/sync/playback", auth=True, limit=100) or []:
        progress = float(row.get("progress") or 0)
        if progress <= 1 or progress >= 95:
            continue
        if row.get("type") == "movie":
            ids = (row.get("movie") or {}).get("ids") or {}
            key = trakt_state.state_key("movie", {"tmdb": ids.get("tmdb"),
                                                  "imdb": ids.get("imdb")})
        else:
            show_ids = (row.get("show") or {}).get("ids") or {}
            episode = row.get("episode") or {}
            key = trakt_state.state_key(
                "episode",
                {"tmdb": show_ids.get("tmdb"), "imdb": show_ids.get("imdb")},
                episode.get("season"), episode.get("number"))
        playback[key] = {"position": progress, "total": 100}

    trakt_state.store(watched=watched, playback=playback)
    kodi.log("Trakt sync: %d watched keys, %d in progress" % (len(watched), len(playback)))
    return True


def invalidate_sync():
    cache.delete(ACTIVITY_KEY)
    from . import trakt_state
    trakt_state.invalidate()

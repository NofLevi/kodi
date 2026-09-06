"""MDBList, which is where people keep the lists they actually curate.

The setting for an MDBList key has existed since the first release with no code
behind it, which is exactly the phantom setting this project says it does not
ship. This is the code.

MDBList answers with external ids and almost no presentation: a title, a year,
an IMDb id and a media type, but no artwork and no plot. A row built straight
from that would be a wall of grey placeholders, so each entry is resolved
through TMDB, which the add-on is already talking to and already caching.

That resolution is the only expensive part, and it is bounded twice over. It
goes through http.run_parallel, so at most four lookups are in flight and the
whole batch has a wall-clock deadline; and every lookup is cached by TMDB's own
details TTL, so a list opened twice costs nothing the second time. A lookup
that does not finish inside the deadline is dropped rather than waited for,
which means a slow list renders short instead of hanging the screen.

Nothing here runs unless the user has entered a key.
"""
from .. import cache, http, kodi, settings

API = "https://api.mdblist.com"

LISTS_TTL = 6 * 3600
ITEMS_TTL = 3 * 3600

# One page is plenty for a home row and keeps the enrichment batch bounded.
MAX_ITEMS = 40
WORKERS = 4
DEADLINE = 12.0


def api_key():
    return settings.get("mdblist.apikey", "").strip()


def has_key():
    return bool(api_key())


def _call(path, ttl, **params):
    key = api_key()
    if not key:
        return None

    query = dict(params)
    query["apikey"] = key

    # The key is deliberately kept out of the cache key. It is a credential,
    # the cache is a file on disk, and one device has one key anyway.
    cache_key = cache.make_key("mdblist", path,
                               *["%s=%s" % kv for kv in sorted(params.items())])
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = http.get_json(API + path, params=query, timeout=(5, 12),
                            default=None)
    if payload is None:
        kodi.log("mdblist did not answer for %s" % path)
        return None
    if isinstance(payload, dict) and payload.get("error"):
        kodi.log("mdblist refused %s: %s" % (path, payload.get("error")))
        return None

    cache.set(cache_key, payload, ttl)
    return payload


def my_lists():
    """The lists this user has made, as (id, name, count) triples."""
    payload = _call("/lists/user", LISTS_TTL)
    return _as_lists(payload)


def top_lists():
    """The lists MDBList itself features."""
    payload = _call("/lists/top", LISTS_TTL)
    return _as_lists(payload)


def _as_lists(payload):
    rows = payload if isinstance(payload, list) else (payload or {}).get("lists")
    if not isinstance(rows, list):
        return []

    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        list_id = row.get("id")
        name = row.get("name") or row.get("slug") or ""
        if list_id is None or not name:
            continue
        found.append({
            "id": str(list_id),
            "name": name,
            "count": int(row.get("items") or row.get("item_count") or 0),
            "description": row.get("description") or "",
        })
    return found


def list_items(list_id, limit=MAX_ITEMS):
    """The titles in one list, resolved through TMDB so they have artwork."""
    if not list_id:
        return []

    payload = _call("/lists/%s/items" % list_id, ITEMS_TTL, limit=limit)
    rows = _as_items(payload)[:limit]
    if not rows:
        return []
    return _enrich(rows)


def _as_items(payload):
    """MDBList returns either a bare list or one split by media type."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if not isinstance(payload, dict):
        return []

    found = []
    for key in ("movies", "shows", "items"):
        rows = payload.get(key)
        if isinstance(rows, list):
            found.extend(row for row in rows if isinstance(row, dict))
    return found


def _enrich(rows):
    """Turn MDBList rows into full items, four lookups at a time."""
    from . import tmdb

    wanted = []
    for row in rows:
        imdb_id = str(row.get("imdb_id") or row.get("imdbid") or "").strip()
        if imdb_id.startswith("tt"):
            wanted.append((imdb_id, row))

    if not wanted:
        return []

    def lookup(imdb_id):
        return lambda: tmdb.find_by_imdb(imdb_id)

    results = http.run_parallel(
        [(imdb_id, lookup(imdb_id)) for imdb_id, _row in wanted],
        workers=WORKERS, deadline=DEADLINE)

    # Preserve the order the curator chose rather than the order they finished.
    found = []
    for imdb_id, row in wanted:
        item = results.get(imdb_id)
        if not item:
            continue
        rank = row.get("rank")
        if rank is not None:
            item["extra"]["mdblist_rank"] = rank
        found.append(item)

    missing = len(wanted) - len(found)
    if missing:
        kodi.log("mdblist: %d of %d titles did not resolve in time"
                 % (missing, len(wanted)))
    return found

"""A single small SQLite key/value cache with TTLs and a hard size cap.

Design notes
------------
* One database file for every kind of cached payload (metadata rows, source
  results, provider responses). One file means one set of file handles, which
  matters on low-end Android boxes.
* Values are JSON, zlib-compressed above a threshold. Compression typically
  turns a 60 KB TMDB page into a few KB, which is the difference between a
  cache that fits the budget and one that does not.
* Eviction is least-recently-used and runs only when the database grows past
  the configured cap, so the common path never pays for housekeeping. Access
  stamps are sub-second floats on purpose: at whole-second resolution every
  entry written during one browsing session ties, and eviction silently
  degrades into insertion order.
* Connections are per-thread because sqlite3 objects may not be shared across
  threads.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
import zlib

from . import kodi, settings

_COMPRESS_OVER = 2048      # bytes; below this, compression costs more than it saves
_PRUNE_EVERY = 300         # seconds between size checks

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key      TEXT PRIMARY KEY,
    value    BLOB    NOT NULL,
    packed   INTEGER NOT NULL DEFAULT 0,
    expires  INTEGER NOT NULL,
    accessed REAL    NOT NULL,
    bytes    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS kv_expires  ON kv (expires);
CREATE INDEX IF NOT EXISTS kv_accessed ON kv (accessed);
"""

_local = threading.local()
_last_prune = [0.0]
_prune_lock = threading.Lock()


def db_path():
    return os.path.join(kodi.profile_path(), "cache.db")


def _connect():
    """Return the connection for this thread, creating the schema once."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None)
    # WAL keeps readers from blocking the writer, which matters because the
    # service thread writes while the UI thread reads.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    _local.conn = conn
    return conn


def close():
    """Close the connection for this thread, used by the service on shutdown."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None


def make_key(*parts):
    """Build a stable cache key from arbitrary parts.

    Long or unicode-heavy keys are hashed so the primary key stays compact.
    """
    raw = "|".join("" if p is None else str(p) for p in parts)
    try:
        compact = len(raw) <= 120 and raw.isascii()
    except AttributeError:  # pragma: no cover - Python < 3.7
        compact = False
    if compact:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return "%s#%s" % (raw[:60].encode("ascii", "ignore").decode("ascii"), digest)


def _pack(value):
    blob = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(blob) > _COMPRESS_OVER:
        return zlib.compress(blob, 1), 1
    return blob, 0


def _unpack(blob, packed):
    if packed:
        blob = zlib.decompress(blob)
    return json.loads(blob.decode("utf-8"))


def get(key, default=None):
    """Return a cached value, or default when missing or expired."""
    now = int(time.time())
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT value, packed, expires FROM kv WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        if row[2] <= now:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            return default
        conn.execute("UPDATE kv SET accessed = ? WHERE key = ?",
                     (time.time(), key))
        return _unpack(row[0], row[1])
    except (sqlite3.Error, zlib.error, ValueError):
        kodi.log_exception("cache get failed for %s" % key)
        return default


def set(key, value, ttl_seconds):  # noqa: A001 - mirrors dict-like naming
    """Store a JSON-serialisable value for ttl_seconds."""
    if ttl_seconds <= 0:
        return
    now = time.time()
    try:
        blob, packed = _pack(value)
        _connect().execute(
            "REPLACE INTO kv (key, value, packed, expires, accessed, bytes)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, sqlite3.Binary(blob), packed,
             int(now) + int(ttl_seconds), now, len(blob)),
        )
    except (sqlite3.Error, TypeError, ValueError):
        kodi.log_exception("cache set failed for %s" % key)
        return
    maybe_prune()


def delete(key):
    try:
        _connect().execute("DELETE FROM kv WHERE key = ?", (key,))
    except sqlite3.Error:
        pass


def delete_prefix(prefix):
    """Drop every entry whose key starts with prefix."""
    try:
        _connect().execute("DELETE FROM kv WHERE key LIKE ?", (prefix + "%",))
    except sqlite3.Error:
        pass


def clear():
    try:
        conn = _connect()
        conn.execute("DELETE FROM kv")
        conn.execute("VACUUM")
    except sqlite3.Error:
        kodi.log_exception("cache clear failed")


def cached(key, producer, ttl_seconds):
    """Return the cached value for key, or compute, store and return it.

    producer is only called on a miss. A producer that returns nothing is not
    cached, so one failed network call does not poison the cache for hours.
    """
    hit = get(key)
    if hit is not None:
        return hit
    value = producer()
    if value:
        set(key, value, ttl_seconds)
    return value


def size_bytes():
    """Approximate on-disk size, including the write-ahead log."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(db_path() + suffix)
        except OSError:
            pass
    return total


def stats():
    try:
        conn = _connect()
        counts = conn.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM kv").fetchone()
        expired = conn.execute(
            "SELECT COUNT(*) FROM kv WHERE expires <= ?", (int(time.time()),)
        ).fetchone()[0]
    except sqlite3.Error:
        return {"entries": 0, "payload_bytes": 0, "expired": 0, "file_bytes": 0}
    return {
        "entries": counts[0],
        "payload_bytes": counts[1],
        "expired": expired,
        "file_bytes": size_bytes(),
    }


def maybe_prune(force=False):
    """Enforce the size cap, at most once every _PRUNE_EVERY seconds."""
    now = time.time()
    if not force and now - _last_prune[0] < _PRUNE_EVERY:
        return
    if not _prune_lock.acquire(False):
        return
    try:
        _last_prune[0] = now
        prune()
    finally:
        _prune_lock.release()


def prune():
    """Drop expired rows, then evict least-recently-used rows over the cap."""
    cap_bytes = max(5, settings.get_int("cache.max_mb", 50)) * 1024 * 1024
    try:
        conn = _connect()
        conn.execute("DELETE FROM kv WHERE expires <= ?", (int(time.time()),))
        if size_bytes() <= cap_bytes:
            return
        # Evict in LRU order until the stored payload is under three quarters
        # of the cap, leaving headroom so we do not prune on every write.
        target = int(cap_bytes * 0.75)
        rows = conn.execute("SELECT key, bytes FROM kv ORDER BY accessed ASC").fetchall()
        total = sum(r[1] for r in rows)
        doomed = []
        for key, nbytes in rows:
            if total <= target:
                break
            doomed.append(key)
            total -= nbytes
        if doomed:
            conn.executemany("DELETE FROM kv WHERE key = ?", [(k,) for k in doomed])
            kodi.log("cache pruned %d entries" % len(doomed))
        conn.execute("VACUUM")
    except sqlite3.Error:
        kodi.log_exception("cache prune failed")

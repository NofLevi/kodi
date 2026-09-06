# Night log — 7 September 2026

Read this first. Newest entries at the bottom. Anything you should look at
personally is marked **NEEDS YOU**.

---

## NEEDS YOU (as of the last entry)

1. **Trakt client id and secret.** Not provided. Watchlist, continue-watching
   and scrobbling stay unverified. trakt.tv/oauth/applications, redirect URI
   `urn:ietf:wg:oauth:2.0:oob`.
2. **The repository is private**, so Kodi's auto-update 404s. Your call, no
   code change needed.

TorBox and TMDB are both connected and working. Films and episodes play.

---

## 00:50 — TorBox connected, and two facts we had been guessing at

The key is stored only in
`.kodi-test/portable_data/userdata/addon_data/plugin.video.katan/settings.xml`,
which git ignores twice over. Verified it appears in no tracked file.

`debrid/torbox.py` had never executed once. It has now:

- **Auth works.** `GET /user/me` returns plan 1, premium until 2026-10-23.
  Not a free account, so it can actually stream.
- **`checkcached` returns a LIST of `{name, size, hash}` objects**, and only
  for hashes it actually has — a miss is an absence, not a `false`. This was
  genuinely unknown; the client handles both shapes and the list-of-dicts
  branch is the one that fires. Had it been wrong, every source would have
  read as uncached and, with `cached_only` on by default, nothing would ever
  have played.
- **43 torrents are already on the account.** `_existing()` re-fetches that
  whole list on every single resolve, which will only get slower. Noted for
  the polish pass.

## 00:55 — Fixed a bug I introduced earlier today

The home window was drawing its two row headings above **completely empty
rows**, with no hero and no artwork. The main screen of the add-on.

I caused it. Adding `prepare()` — the fix for the focus bug — sets
`self.rows` before the window is shown, and `onInit`'s "have I already run"
guard was reading exactly that attribute. So `onInit` returned immediately and
never filled anything.

The suite could not catch it: every home test calls `onInit()` directly and
none of them call `prepare()` first, so none ran the order the add-on actually
uses. There is now a test that does, plus one asserting the second `onInit`
(which Kodi fires when a dialog closes) stays a no-op.

Found by an audit agent reading the code, then reproduced in a real Kodi from
a screenshot taken after the change. Confirmed fixed the same way.

## 01:20 — A film played. This had never happened before.

The whole point of the add-on, and until tonight not one film or episode had
ever played. Through the real route — `action=movie`, the URL the details
screen builds — not a harness:

| Title | Sources found | Result |
|---|---|---|
| The Shawshank Redemption | 166 → 8 ranked | plays, 2h22m |
| The Dark Knight | 120 → 8 | plays, 2h32m |
| Inception | 147 → 8 | plays, 2h28m |
| Breaking Bad S01E01 | 131 → 8 | plays, 58m |
| Game of Thrones S01E01 | 196 → 8 | plays, 1h01m |

**5 of 5.** Torrentio returns the sources, TorBox reports which are cached,
`scoring` ranks them, `pick_file` picks the file and TorBox hands back a CDN
link Kodi plays. The subtitle pipeline runs on top of it — the log shows a
file hash computed in about a second and a Hebrew subtitle search starting.

The season pack case is the one that could have played the wrong thing, and it
did not. Game of Thrones resolved to a **73-file S01–S08 pack**, and
`pick_file` picked `S01E01.Winter.Is.Coming…mkv` and rejected all 72 others.

**A correction I owe you:** I first reported GoT as playing a 1m37s file and
called it a wrong-file bug. It was not. My own test script printed the
`minutes` field of the duration and dropped `hours`, so `1h01m37s` printed as
`1m37s`. The add-on was right and my instrument was wrong. Re-measured over
75 seconds: 61 minutes, exactly right.

Two things that were genuinely unknown about TorBox are now known and will be
written into tests rather than left as guesses:

- `checkcached` answers with a **list of `{name, size, hash}` objects**, and
  omits misses rather than returning false for them.
- `requestdl` answers with a **bare URL string**, not an object.

## 01:55 — DEFAULTS was being ignored for every boolean setting

Writing the first tests for `play.py` turned up something worth more than the
tests. `settings.get_bool("sources.autoplay")` returned **False**, though the
setting ships as `"true"`.

`get_bool` handed `get()` an empty string as its fallback, and `get()` returns
any fallback that is not None *instead of* consulting `DEFAULTS`. So an unset
boolean came back as `""`, and therefore False, whatever the table said. That
covered seventeen settings that ship as true, including `sources.autoplay`,
`sources.cached_only`, `sources.prefer_hebrew` and `sources.source_memory`.

Real Kodi mostly hides this, which is why it survived: Kodi writes every
declared setting into its own settings.xml from the `<default>` in the XML, so
the value is rarely genuinely absent. It bit exactly where a setting was *not*
declared — and until earlier tonight twenty-three settings were not.

Fixing it broke ten existing tests, which is the interesting part. They were
passing only because `cached_only` was wrongly false, so nothing was ever
filtered; they had been ranking sources that production would have discarded
before ranking began. They now state their precondition instead of inheriting
a bug, and there is a test asserting **every** declared boolean reads back as
declared, so a new one cannot slip through.

Also in this batch, both from the code audit:

- `play._resolve` had its own copy of `registry.resolver_for`, and the copy had
  drifted: it never checked the named service was still configured, so a source
  cached by an account you had since removed went to a client with no key. It
  now calls the real one.
- `registry.forget_cache_status()` existed and was never called, while a
  negative cache answer is remembered for an hour — so a torrent that became
  cached still read as uncached. Tools → Clear cache now clears it, which is
  the one moment you have asked for exactly that.

`torbox.py`, `play.py` and the settings defaults now have tests where they had
none. 602 tests, all green.

## 02:40 — The source picker was broken for everyone who opened it

With TMDB connected the home screen finally has real rows, and the details
window and source picker could be seen with something in them for the first
time. The home screen looks the part: hero with a backdrop, Hebrew row
headings, real artwork.

The picker did not. It showed a title, no status, a blank button and an empty
list. The traceback names the line:

    bits.append("%s %s" % (kodi.localize(32330), service.upper()).strip())

`.strip()` binds to the **tuple**, not to the formatted string, so `_badge`
raised on every cached source. Cached sources rank first, so the first row
took out the entire list, inside `onInit`, where an exception is not a stack
trace anyone sees — it just silently abandons the rest of the method. That is
why the title was set (it happens on the line before) and nothing after it was.

It survived because `sources.autoplay` ships on, so the picker only opens if
you deliberately ask to choose a source.

Fixed, along with four things from the audit: the window now sets its
properties before it is shown (the toggle button's entire label is a property,
so it painted blank), `_render` is wrapped so one bad row cannot abandon the
rest, the debrid service is named the way it names itself — `TorBox`, not
`TORBOX` — and an unknown quality is no longer labelled `SD`, which was a claim
rather than a fallback. The window has tests now; it was the only one without
any.

**A second correction.** Between fixes I reported the picker rendering only
one row, then none. Neither was true. Kodi renders lazily when idle — those
screenshots show FPS 2 and 4 — so the capture returned a stale frame and a
keypress forced the redraw. All eight rows were always there. I have stopped
trusting a screenshot taken while nothing is moving.

615 tests.

# Continuation report — 10 September 2026

This report is the handoff for continuing Katan from another development host.
Branch: `development`. No release was created and no version tag was added.
Read `CLAUDE.md` first, then this file. `NIGHT-LOG.md` and `SUBTITLE-LOG.md`
contain the earlier real-device and subtitle work.

## Current verification state

The latest broad suite before this handoff passed **1,670 tests** while excluding
only the assertion that `AGENTS.md` was tracked. The protected `AGENTS.md`
write was blocked by the host approval gate during handoff, so this report is
the committed cross-host context. Recreate `AGENTS.md` from it on the next host
if desired, then rerun the complete unfiltered suite.

Latest focused results from this batch:

- HTTP, URL-session, and audit redirect tests: **39 passed** after adding the
  real `Trakt-Api-Key` header to both redirect-sensitive sets.
- HTTP compression and failure-path tests: **49 passed**.
- Release parser suite: **69 passed**.
- Permanent cross-component audit regressions: **13 passed**.
- Temporary Trakt/watch-state audit reproductions: **9 passed**.
- Active playback/source/HTTP/Pastebox focused run: **90 passed**.
- Ruff `E9,F`: passed.
- `compileall`: passed.
- `git diff --check`: passed.
- Active ZIP install smoke: `ACTIVE_INSTALL_PASS files=128`.
- Existing package inspection: two ZIPs opened, zero `AGENTS.md` leaks; updater
  accepted the inspected add-on ZIP at 520,759 bytes.

These are historical results for the committed batch, not a substitute for a
fresh run after pulling it.

## Active Linux Kodi run performed at handoff

Kodi and Xvfb were installed on the Ubuntu 24.04 development host:

- Kodi: **20.5 Nexus** from Ubuntu packages.
- InputStream Adaptive: **20.3.18**.
- Display: Xvfb, 1920x1080x24.
- Renderer: Mesa llvmpipe, OpenGL 4.5 software rendering.
- Profile: isolated under `/tmp/kodi-e2e/home/.kodi` as `hermesuser`.

Observed successfully in the real Kodi process:

- Kodi initialized its GUI, databases, JSON-RPC, and TCP server on port 9090.
- Kodi discovered Katan 0.0.1 and loaded all 400 English strings.
- Katan was enabled through `Addons.SetAddonEnabled`.
- Kodi started the real `service.py` Python interpreter.
- The log emitted `[Katan] service started, version 0.0.1`.
- Kodi invoked the real `main.py` plugin source and activated Katan window
  13000.
- JSON-RPC confirmed the add-on was enabled with its required
  `inputstream.adaptive` dependency.

The process was stopped when requested. It was killed rather than exited via
`Application.Quit`, so graceful service shutdown is not proven by this run.

### Finding to investigate on the next host

A modal `Yes / No dialog` was active, with current control `No`. While that
modal remained open, the service repeatedly logged:

```text
[Katan] back on Kodi's home screen, returning to Katan
```

and repeatedly invoked `plugin://plugin.video.katan/`; the final process output
showed invoker number 135. Determine what owns the modal dialog and make the
auto-return logic ignore modal dialogs or an already-running Katan window.
This may be specific to a fresh Kodi 20/Xvfb profile, but it is a real runtime
observation and should not be dismissed without reproducing on Kodi 21.

Expected unrelated host warnings were missing audio sink, VDPAU unavailable,
UPower/ConsoleKit unavailable, and optical-media authorization. They do not
show a Katan Python crash. For playback testing, provide a PulseAudio/PipeWire
null sink or disable audio output so the sink retry loop does not flood logs.

## Implemented in the current batch

### Playback and lifecycle

- Bound pending playback metadata to a SHA-256 stream identity and a 60-second
  handoff TTL, preventing unrelated playback from consuming stale Katan state.
- Added playback generations so delayed stop/finalization work cannot clear a
  newer playback.
- Made start scrobbles use measured Kodi progress, preserving resumed starts.
- Snapshot metadata for delayed scrobble/source persistence.
- Added explicit player/background shutdown paths and closed shared HTTP/cache
  resources.
- Allowed valid direct HTTP/HTTPS sources to bypass debrid resolution.
- Fixed custom-window resume playback to pass populated `ListItem` objects.

### Trakt and state synchronization

- Commit activity checkpoints only after all required pulls and mirror writes
  succeed.
- Preserve freshly updated watched mirrors after `set_watched()`.
- Request watched shows with `extended=progress`.
- Added bounded pagination: 100 rows per page, maximum 50 pages, repeated-page
  detection.
- Treat only returned episode rows as watched; show presence no longer marks an
  entire show watched.
- Invalidate continue/watchlist/recommendation/trending rows only after a
  successful synchronization.
- Clear synchronization state and related caches on sign-out.
- Corrected percentage resume conversion.

### Sources and debrid

- Track positive cache ownership separately from definitive negative answers;
  provider failures no longer erase previously known cache state.
- Strengthened cache identity with title/year when IMDb/TMDb IDs are absent.
- Preserve richer cached ownership when duplicate source rows merge.
- Accept both `biggest` and `largest` size preference values.
- Skip malformed AnimeTosho/Zilean rows.
- Isolate malformed torrent files and prefer exact filename, file ID, and file
  index hints before heuristic selection.
- Moved raw, ranked, and resolved private source values to volatile memory and
  made prefix invalidation clear all variants.

### Subtitles

- Added one operation-wide three-download coordinator shared by exact-hash,
  consensus, language fallback, translation, and final synchronization work.
- Added independent cross-language timing consensus and provenance checks.
- Kept content confidence separate from timing confidence.
- Hardened exact-hash identity, wrong-episode rejection, multilingual release
  parsing, archive extraction, legacy decoding, decompression, cancellation,
  stale generations, and translation echo handling.
- Bounded decoded subtitle data to 8 MiB, cues to 20,000, gzip members to 8,
  and gzip reads to 64 KiB chunks.
- Tightened timing-scale agreement so PAL/NTSC conversions are not mislabeled
  as identical timelines.
- Account-bound Ktuvit sessions use a non-reversible email fingerprint.
- Remote OpenSubtitles hashing now requires known exact size, valid 206 ranges,
  matching `Content-Range`, identity encoding, and bounded 64 KiB reads.

### HTTP, security, and storage

- Both Requests and stdlib redirects strip authorization, proxy authorization,
  cookies, generic API keys, and `Trakt-Api-Key` across origins.
- HTTPS-to-HTTP redirects fail closed.
- Retried responses close before sleeping.
- Added an 8 MiB decoded response ceiling with bounded Requests and stdlib
  materialization.
- Added bounded stdlib gzip, x-gzip, zlib, and raw-deflate handling; malformed,
  trailing, and oversized payloads fail closed.
- Replaced per-call provider pools with one process-wide four-worker pool.
- Made URL-containing cache keys opaque.
- Signed Kaltura URLs, entitlement tickets, and source payloads are volatile
  and covered by SQLite-absence regressions.
- Profile directories use mode 0700 and cache SQLite uses 0600 where supported.
- Logs omit exception values and private URL components.
- QR images are deleted immediately after authentication windows close.
- LAN Pastebox uses a random single-use exact capability path, body bound,
  expiry, cancellation, and completion checks. It remains plain LAN HTTP, not
  TLS.

### Updater and parsing

- Added 32 MiB ZIP transport, 1,024-member, 96 MiB expanded, and per-member
  bounds.
- Require canonical paths and required `addon.xml` plus `main.py` before and
  during installation.
- Validate add-on identity and version before replacement.
- Bound versions to three 1–9 digit numeric components and 29 characters.
- Fixed release parsing so DD5.1, 2.0, 5.1, and 7.1 are not interpreted as
  absolute episode numbers.

### UI hardening

- Home context actions now use stable item identity rather than duplicate row
  indices.
- Stale search/home generations are ignored.
- Cached subtitle sources display their known accuracy.

## Known gaps and next work

1. Recreate and track a concise source-only `AGENTS.md`; the host blocked that
   protected write during this handoff. Keep it out of packages and `repo/`.
2. Reproduce and fix the Kodi modal/auto-return invocation loop described
   above; add a regression for modal dialogs and window lifecycle.
3. Run the full unfiltered test suite after pull, then fresh Ruff, compileall,
   diff checks, package build, ZIP-member checks, and updater validation.
4. Run under **Kodi 21**. Ubuntu 24.04 packaged Kodi 20.5, so use the existing
   Windows `python tools/setup_kodi.py` flow or a Kodi 21 image/build.
5. Exercise clean `Application.Quit` and verify `[Katan] service stopped`,
   player shutdown, pool/session closure, and no surviving worker threads.
6. UI workers in Search/Home/Auth still need a lifecycle-safe pattern: avoid
   touching Kodi controls from worker threads, ignore work after close, and
   move remote cache misses out of synchronous UI callbacks.
7. Update authenticity still needs a signed manifest and pinned public key.
8. Pastebox capability secrecy reduces exposure but does not provide encrypted
   LAN transport.
9. Validate real provider entitlement values stay volatile in an actual flow,
   not only synthetic SQLite regressions.
10. Physical Kodi 21 ARM validation is still required for decoder startup,
   auto-resume without a chooser, InputStream Adaptive, subtitle application,
   cancellation, memory behavior, and real TorBox range requests.
11. Missing product work includes local-resume fallback, retrying the next
    source after decoder/start failure, Trakt watchlist toggle, kids PIN
    lifecycle, device capability filtering, and stronger subtitle runtime
    completeness checks.

## Safety and release state

- No credentials, signed URLs, cookies, entitlement tickets, or private request
  data belong in this report or any tracked file.
- A future `AGENTS.md` must be source-only and stay outside generated ZIPs and
  `repo/`.
- Pushing `development` does not publish. Do not create or push a version tag
  until the user explicitly requests a release.

# Katan

A lightweight Netflix-style front end for **Kodi 21 (Omega)**, in Hebrew and
English. Discovery from TMDB and Trakt, playback through Real-Debrid, TorBox,
Premiumize or AllDebrid, Israeli live television and on-demand, anime, and
automatic Hebrew subtitles with an AI translation fallback.

It is written for weak hardware — a 1 GB Android projector — so concurrency is
bounded, caches are capped, and there is no skin fork.

---

## Install it once, then it updates itself

The same install works on **every** platform, because the add-on is pure
Python with `<platform>all</platform>` — no `.so`, no `.dll`, nothing compiled.
Only the way you get the file onto the box differs.

**Install the repository, not the add-on.** Installing `plugin.video.katan`
directly works, but nothing will ever tell you there is a new version. The
repository is 2 KB and is the whole difference between updating and
reinstalling.

| | |
|---|---|
| Current release | **Katan 0.0.1** - [releases page](https://github.com/NofLevi/kodi/releases/latest) |
| Repository zip | <https://github.com/NofLevi/kodi/releases/latest/download/repository.katan-0.0.1.zip> |
| Add as a Kodi source | `https://noflevi.github.io/kodi/` |
| Requires | Kodi 19 or newer; developed and tested on Kodi 21 |
| Installs with it | `inputstream.adaptive`, from Kodi's own repository — the DASH live channels need it |

Two add-ons, both at 0.0.1: **Katan** is what you watch with, and **Katan
Repository** is the two-kilobyte pointer that tells Kodi where to look for new
versions of it. Install the repository and the other arrives on its own.

### Step 0, on every platform: allow unknown sources

Kodi refuses to install anything from a zip until you do this, and the error it
gives instead does not say so.

> **Settings → System → Add-ons → Unknown sources → On**

---

## Windows

1. Download the [repository zip](https://github.com/NofLevi/kodi/releases/latest/download/repository.katan-0.0.1.zip).
2. Kodi → **Add-ons** → the box icon (top left) → **Install from zip file**.
3. Point it at the file you downloaded, usually `C:\Users\<you>\Downloads`.
4. **Install from repository → Katan Repository → Video add-ons → Katan → Install.**

Kodi's own file browser can also fetch it for you: **Install from zip file →
Add network location…** is fiddlier than downloading first, so download first.

## Android — Mi Box, Fire TV, phones, tablets

The awkward part is getting a file onto a device you drive with a remote.
Three ways, easiest first.

**With Kodi alone — no browser, no file manager, no cable.** This is the
easiest route on a television and the one to use:

> **Settings → File manager → Add source → &lt;None&gt; →**
> type `https://noflevi.github.io/kodi/` **→ name it `Katan` → OK**

then

> **Add-ons → Install from zip file → Katan → zips → repository.katan →**
> the `.zip`

One address typed once, and Kodi fetches everything itself. This works
because the published site carries a plain listing in every folder — Kodi
browses HTTP by reading the links out of the page, and without one it cannot
see what is there.

**With a browser on the device.** Install a file manager that can download
(*Downloader* by AFTVnews is the usual one on Fire TV), fetch the repository
zip URL above, then follow the Windows steps 2–4.

**With adb, from this repository.** Nothing to type on the box:

```
python tools/deploy_android.py --list     # what adb can see
python tools/deploy_android.py            # push the newest zips
```

That copies the zips to `/sdcard/Download` and leaves you the two presses Kodi
gives no way to automate:

> **Add-ons → Install from zip file → Download → the file**

**With a USB stick.** Copy the zip on, plug it in, browse to it in step 3.

### Nothing extra on Android

The Israeli live channels are DASH streams and need **InputStream Adaptive**.
It is a binary add-on, so it cannot ship inside Katan's zip - but it does not
have to. Kodi installs it for you, because Katan declares it as a requirement
and Kodi resolves requirements from its own repository before installing
anything that has them.

It used to be marked optional, which told Kodi not to bother, and left every
box with a live TV section that silently played nothing until somebody went
and found the add-on by hand.

## Linux, macOS, LibreELEC, Raspberry Pi

Identical to Windows — download the zip, then **Install from zip file**. The
only difference is where your browser puts it:

| | |
|---|---|
| Linux / Raspberry Pi | `~/Downloads` |
| macOS | `~/Downloads` |
| LibreELEC / CoreELEC | no browser — use `wget` over SSH, or a USB stick |

On LibreELEC, over SSH:

```
cd /storage/downloads
wget https://github.com/NofLevi/kodi/releases/latest/download/repository.katan-0.0.1.zip
```

then install it from that path in step 3.

---

## After installing

**Nothing else is required to start watching.** The Israeli live channels, the
on-demand catalogue and anime all work with no account and no key — a TMDB key
ships with the add-on so films and series work out of the box too.

What you may want to add, in **Tools → Setup wizard**:

| | What it unlocks | Cost |
|---|---|---|
| A debrid service | Films and series play at all | Paid: TorBox, Real-Debrid, Premiumize or AllDebrid |
| Trakt | Continue watching, watchlist, scrobbling | Free |
| OpenSubtitles | The only provider that matches a subtitle to the file by hash | Free |
| Gemini | AI subtitle translation when no Hebrew subtitle exists | Free tier |

**TorBox, Real-Debrid and AllDebrid sign in by scanning a code with a phone**
— nothing long to type on a remote.

Trakt and Premiumize cannot yet. Both have the same flow, but both require an
application registered with them first and neither publishes a client id the
way Real-Debrid does, so the add-on would have to ask you for a client id and
secret on a remote control. Until those are registered, the raw key fields at
expert level are the only way in for those two.

### Updating

Kodi checks the repository on its own, and Katan checks too — it reads
[the latest release](https://github.com/NofLevi/kodi/releases/latest) directly,
so it can offer an update even when it was installed from a zip and there is no
repository to ask. To check now:

> **Tools → Check for updates**

Your keys survive an update. Kodi preserves `userdata/addon_data`, and nothing
in the add-on writes inside its own folder — there is a test that reads the
source to keep it that way.

---

## Building it yourself

```
python -m pytest tests        # 1292 tests, no Kodi install needed
python tools/build.py         # writes repo/zips and the repository index
python tools/release.py       # version bump, news, build
```

`tools/setup_kodi.py` fetches a portable Kodi 21 into `.kodi-test/` and links
the add-on into it, so you can run the real thing on Windows without touching
your own Kodi.

See [CLAUDE.md](CLAUDE.md) for how the pieces fit and why they are the way
they are.

## Attribution

The Israeli channel list and VOD catalogue were derived from the
[Idan Plus](https://github.com/Fishenzon/repo) add-on by Fishenzon.

Licensed GPL-3.0-or-later.

# Katan features — which go into the Stremio add-on?

Mark what you want. Legend:

- ✅ **We can build it** in our add-on
- 🟦 **Stremio already does it** — nothing to build
- 🟨 **Another Stremio add-on already does it** (Torrentio, AIOStreams…)
- ❌ **Not possible** — Stremio add-ons can't change its screens or player

## Israeli content

| # | Feature | Stremio |
|---|---|---|
| 1 | Israeli VOD library — Kan, Keshet 12, Reshet 13, sport5 and more (~2,800 programmes), with seasons and episodes | ✅ (runs on your PC — Israeli connection needed) |
| 2 | Search inside the Israeli VOD | ✅ |
| 3 | Israeli live TV channels (84) | ✅ most; some may not play on Android TV |
| 4 | Mako/Keshet episodes without the ad breaks / signed links handled | ✅ |

## Subtitles

| # | Feature | Stremio |
|---|---|---|
| 5 | Automatic Hebrew subtitles from Wizdom, OpenSubtitles (two APIs), BSPlayer, Ktuvit, SubSource | ✅ |
| 6 | Match by file fingerprint (hash) — the subtitle made for this exact file | ✅ (Stremio sends the hash) |
| 7 | Smart choice by release name / group, consensus between uploads | ✅ |
| 8 | Re-timing a subtitle that is early/late or at the wrong speed, including files cut differently | ✅ |
| 9 | Reject subtitles for the wrong episode or that stop halfway | ✅ |
| 10 | AI translation to Hebrew (Gemini / OpenAI) when no Hebrew exists | ✅ |
| 11 | Hebrew for anime: English from AnimeTosho / Kitsunekko, translated with AI | ✅ |
| 12 | Translate the next episode ahead so it's ready | ✅ |
| 13 | Several subtitle choices offered, best first | ✅ |
| 14 | Prefer a Hebrew track already inside the file | 🟦 Stremio picks embedded tracks itself |

## Sources and playback

| # | Feature | Stremio |
|---|---|---|
| 15 | Debrid — TorBox, Real-Debrid, Premiumize, AllDebrid | 🟨 Torrentio / AIOStreams |
| 16 | Torrent providers, including anime (Nyaa, SeaDex…) | 🟨 Torrentio / AIOStreams / AniScraper |
| 17 | Our own source ranking: quality, size, HEVC/AV1/HDR/Dolby Vision filters, prefer Hebrew releases | ✅ (as our own stream add-on) or 🟨 AIOStreams filters |
| 18 | Remember which release group played well and prefer it | ✅ (only if we make our own stream add-on) |
| 19 | Anime episode numbering fixes (absolute numbers, Kitsu seasons) | ✅ (own stream add-on) / 🟨 partly in Torrentio |
| 20 | Reject a different show with the same name (e.g. the 2020 live-action Hikaru no Go) | ✅ (own stream add-on) |
| 21 | Autoplay the best source | 🟦 Stremio "auto-play" option |
| 22 | Try the next source automatically when one fails | ❌ |
| 23 | Pre-load the next episode's source | ❌ |

## Catalogues and metadata

| # | Feature | Stremio |
|---|---|---|
| 24 | Films and series catalogues (trending, popular, top rated…) | 🟦 Cinemeta |
| 25 | Hebrew titles, plots and posters | ✅ |
| 26 | Anime catalogues (Kitsu / AniList) | 🟨 Anime Kitsu add-on |
| 27 | MDBList lists | 🟨 existing add-ons |
| 28 | Search, including voice | 🟦 |

## Tracking

| # | Feature | Stremio |
|---|---|---|
| 29 | Trakt sync, watchlist, watched marks | 🟦 built-in Trakt |
| 30 | Resume where you stopped | 🟦 |
| 31 | "Continue watching" / next episode | 🟦 |

## Player and screens

| # | Feature | Stremio |
|---|---|---|
| 32 | Skip intro button / automatic intro skip | ❌ |
| 33 | Our own next-episode card with countdown | ❌ (Stremio has its own "next episode") |
| 34 | Announce when a file has several audio languages | ❌ |
| 35 | Our own home screen, details and search windows | ❌ |
| 36 | Kids mode / keep the box locked in the app | ❌ |
| 37 | Start on boot | ❌ (Android setting, not ours) |
| 38 | Lightweight mode for weak boxes | ❌ |

## Setup

| # | Feature | Stremio |
|---|---|---|
| 39 | Settings page (keys, languages, filters) | ✅ Stremio's "configure" page |
| 40 | QR-code sign-in for Trakt / debrid | ✅ on the configure page |
| 41 | Self-updating | 🟦 add-ons update on the server side |

---

Reply with the numbers you want (e.g. "1, 2, 5–13, 25").

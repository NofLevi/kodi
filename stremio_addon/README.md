# Katan for Stremio

A Stremio add-on that brings Katan's Israeli parts to Stremio:

- **Israeli VOD**: Kan, Keshet 12, Reshet 13, sport5, Now 14, Sport 1, with
  seasons, episodes and search, plus radio.
- **Live Israeli TV channels.**
- **Hebrew subtitles** for any film or episode. Katan's own Hebrew search is
  used when it finds a good match. Otherwise an English subtitle is translated
  with Gemini: for anime from AnimeTosho (made for the exact file) or
  Kitsunekko, and for everything else from Katan's own English match. Each
  translation is kept, and the next episode is prepared while you watch.

It reuses the Kodi add-on's code (`plugin.video.katan/resources/lib`) without
changing it.

## Run it

```
python stremio_addon/server.py
```

The first run creates `stremio_addon/config.json`, which is gitignored, with
a random `access_token`. Put your Gemini key in it (free from
https://aistudio.google.com) and start the server again.

The server prints an address like
`http://127.0.0.1:7000/<token>/manifest.json`. In Stremio on this PC, paste
it into the add-on search box to install.

The broadcasters only answer Israeli connections, so the server has to run
at home.

## The projector

Stremio only installs add-ons over https, except on the same machine. To use
it on the projector, give the server an https address with Cloudflare Tunnel:

```
cloudflared tunnel --url http://127.0.0.1:7000
```

It prints an `https://….trycloudflare.com` address. Install
`https://….trycloudflare.com/<token>/manifest.json` on the projector. A quick
tunnel gets a new address every time it starts. A named tunnel on your own
domain keeps the address fixed, so the add-on stays installed.

Video never passes through the PC or the tunnel. Stremio plays it straight
from the broadcaster. Only the small JSON answers go through.

## Tests

```
python -m pytest stremio_addon/tests -q
```

Run these separately from the Kodi suite (`python -m pytest tests`). Each
suite loads its own stand-ins for Kodi.

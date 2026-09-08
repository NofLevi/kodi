# -*- coding: utf-8 -*-
"""Paste a key from your phone instead of typing it on a remote.

Kodi shows a LAN address, you open it on a phone that already has the key in
its clipboard, and it arrives here. Nothing is typed on the television.

The idea is taken from the Kodi POV IL build, which does the same thing for
its Gemini key, and it is the right answer to the one genuinely miserable part
of setting this up: a TorBox key is thirty-two characters and an on-screen
keyboard driven by a remote is about a minute of work to get it wrong.

Deliberately small. One page, one field, one submission, then the server stops.
It listens only while the dialog is open, and only on the local network - this
is plain HTTP carrying a credential, which is fine between a phone and a
projector on the same wifi and would not be fine anywhere else.
"""
import socket
import threading
import unicodedata

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs
except ImportError:      # pragma: no cover - Python 2 safety net
    raise

from . import kodi

# Long enough to walk to the phone and find the key, short enough that a
# forgotten dialog does not leave a socket open all evening.
LIFETIME = 300

PAGE = u"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title><style>
body{font-family:system-ui,-apple-system,sans-serif;background:#111;color:#eee;
margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}
form{width:100%%;max-width:26rem;padding:1.5rem}
h1{font-size:1.1rem;font-weight:600;margin:0 0 1rem}
input{width:100%%;box-sizing:border-box;font-size:1.1rem;padding:.8rem;
border-radius:.5rem;border:1px solid #444;background:#1c1c1c;color:#eee}
button{width:100%%;margin-top:1rem;font-size:1.1rem;padding:.9rem;border:0;
border-radius:.5rem;background:#e50914;color:#fff}
p{color:#999;font-size:.9rem}
</style></head><body><form method="post">
<h1>%(title)s</h1>
<input name="value" autofocus autocomplete="off" autocapitalize="off"
 autocorrect="off" spellcheck="false" placeholder="%(placeholder)s">
<button type="submit">%(send)s</button>
<p>%(hint)s</p>
</form></body></html>"""

DONE = u"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;
display:flex;min-height:100vh;align-items:center;justify-content:center}
</style></head><body><h1>%(done)s</h1></body></html>"""


def sanitise(value):
    """Undo what a phone keyboard does to a pasted key.

    iOS in particular substitutes smart quotes and en-dashes into anything it
    thinks is prose, and a key with a U+2013 in it fails authentication with
    no clue why. NFKC first, so full-width and decorated characters collapse to
    ASCII, then dashes are forced back, then anything left that a key would
    never contain is dropped.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).strip()
    for dash in u"\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        text = text.replace(dash, "-")
    for quote in u"\u2018\u2019\u201c\u201d'\"`":
        text = text.replace(quote, "")
    return "".join(c for c in text if c.isalnum() or c in "-_.:/+=").strip()


def lan_address():
    """This machine's address on the local network.

    The UDP socket sends nothing; connecting it just asks the routing table
    which interface would be used to reach the internet, which is the one the
    phone can see. Reading the hostname instead returns 127.0.0.1 as often as
    anything useful.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def _handler_for(state, labels):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, code=200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(PAGE % labels)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            value = sanitise((parse_qs(raw).get("value") or [""])[0])
            self._send(DONE % labels)
            if value:
                state["value"] = value
                state["done"].set()

        def log_message(self, *args):
            pass        # the Kodi log is not a web server access log

    return Handler


def receive(title, placeholder="", lifetime=LIFETIME, on_ready=None):
    """Serve the page until something is pasted. Returns it, or "".

    `on_ready(url)` is called once the address is known, so the caller can put
    it on screen however it likes - as text, or as a code to scan.
    """
    address = lan_address()
    if not address:
        kodi.log("no LAN address, cannot offer the paste page")
        return ""

    labels = {
        "title": title,
        "placeholder": placeholder or title,
        "send": kodi.localize(32510),
        "hint": kodi.localize(32511),
        "done": kodi.localize(32512),
    }
    state = {"value": "", "done": threading.Event()}

    try:
        # Port 0 asks the operating system for a free one, which is the only
        # way to avoid colliding with whatever else the box is running.
        server = HTTPServer(("0.0.0.0", 0), _handler_for(state, labels))
    except OSError:
        kodi.log_exception("could not open the paste page")
        return ""

    url = "http://%s:%d" % (address, server.server_port)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    kodi.log("paste page waiting on %s" % url)

    try:
        if on_ready is not None:
            on_ready(url)
        state["done"].wait(lifetime)
    finally:
        server.shutdown()
        server.server_close()

    return state["value"]

# -*- coding: utf-8 -*-
"""Set the whole thing up from a computer instead of from a remote.

Everything this add-on needs is a key, and a key is the one thing a remote is
worst at. The phone page in `pastebox` solved that one key at a time; this is
the same idea for all of them at once: one page, every account, filled in
with a real keyboard and saved in one press.

Deliberately the same shape as the paste page, for the same reasons. It
listens only while the screen that started it is open, only on the local
network, and the address carries a short code that is not reused. This is
plain HTTP carrying credentials, which is fine between two machines in one
house and would not be fine anywhere else, so it is never left running.

A field left empty keeps what is already stored, and what is stored is never
written into the page: knowing that a key is set is enough to decide whether
to replace it, and a page that prints every credential in the house is a page
somebody leaves open on a laptop.
"""
import threading
import time

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from . import kodi, pastebox, settings

LIFETIME = 600
MAX_BODY_BYTES = 16384

# What can be set from here: everything that is a key somebody can paste.
# Trakt is deliberately not among them - it signs in through its own device
# flow, which is a code on the television rather than a key to copy.
FIELDS = (
    ("tmdb.apikey", "TMDB", "the catalogue; nothing plays without it"),
    ("torbox.apikey", "TorBox", "debrid"),
    ("realdebrid.token", "Real-Debrid", "debrid (API token)"),
    ("premiumize.apikey", "Premiumize", "debrid"),
    ("alldebrid.apikey", "AllDebrid", "debrid"),
    ("subs.ai.gemini_key", "Gemini", "AI subtitle translation"),
    ("subs.ai.openrouter_key", "OpenRouter", "AI, second engine"),
    ("subs.ai.openai_key", "OpenAI", "AI, compatible endpoint"),
    ("mdblist.apikey", "MDBList", "lists on the home screen"),
    ("subs.opensubtitles.apikey", "OpenSubtitles",
     "optional; shared keys are built in"),
)

PAGE = u"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title><style>
body{font-family:system-ui,-apple-system,sans-serif;background:#111;color:#eee;
margin:0;padding:2rem 1rem}
form{max-width:34rem;margin:0 auto}
h1{font-size:1.2rem;margin:0 0 .3rem}
p.hint{color:#999;font-size:.9rem;margin:0 0 1.5rem}
label{display:block;margin:1rem 0 .3rem;font-size:.95rem}
span.note{color:#888;font-weight:400}
span.set{color:#4caf50}
input{width:100%%;box-sizing:border-box;font-size:1.05rem;padding:.7rem;
border-radius:.5rem;border:1px solid #444;background:#1c1c1c;color:#eee}
button{width:100%%;margin-top:1.5rem;font-size:1.1rem;padding:.9rem;border:0;
border-radius:.5rem;background:#e50914;color:#fff}
</style></head><body><form method="post">
<h1>%(title)s</h1><p class="hint">%(hint)s</p>
%(fields)s
<button type="submit">%(save)s</button>
</form></body></html>"""

FIELD = u"""<label for="%(key)s">%(label)s <span class="note">%(note)s</span>
 %(state)s</label>
<input id="%(key)s" name="%(key)s" autocomplete="off" autocapitalize="off"
 autocorrect="off" spellcheck="false" placeholder="%(placeholder)s">"""

DONE = u"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;background:#111;color:#eee;
display:flex;min-height:100vh;align-items:center;justify-content:center}
</style></head><body><h1>%(done)s</h1></body></html>"""


def _labels():
    return {
        "title": kodi.localize(32573),
        "hint": kodi.localize(32574),
        "save": kodi.localize(32575),
        "done": kodi.localize(32576),
        "stored": kodi.localize(32577),
        "empty": kodi.localize(32578),
    }


def page(labels=None):
    """The form: every field empty, and no stored key written into it."""
    labels = labels or _labels()
    rows = []
    for key, label, note in FIELDS:
        stored = bool((settings.get(key) or "").strip())
        rows.append(FIELD % {
            "key": key,
            "label": label,
            "note": note,
            "state": ('<span class="set">%s</span>' % labels["stored"]) if stored
                     else ('<span class="note">%s</span>' % labels["empty"]),
            "placeholder": labels["stored"] if stored else "",
        })
    return PAGE % dict(labels, fields="\n".join(rows))


def apply_form(form):
    """Store what was filled in. Returns how many settings changed.

    An empty box means "leave it alone" rather than "clear it": the page
    cannot show what is stored, so empty is the normal state of a field
    nobody came here to change.
    """
    changed = 0
    for key, _label, _note in FIELDS:
        value = pastebox.sanitise((form.get(key) or [""])[0])
        if not value or value == (settings.get(key) or "").strip():
            continue
        settings.set(key, value)
        changed += 1
    if any(settings.get(key) for key in ("subs.ai.gemini_key",
                                         "subs.ai.openrouter_key",
                                         "subs.ai.openai_key")):
        # The same rule as entering a key on the television: a key that was
        # typed in is a key that is meant to be used.
        settings.set("subs.ai.enabled", "true")
    return changed


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
            if self.path != state["path"] or state.get("used"):
                self._send("Not found", 404)
                return
            self._send(page(labels))

        def do_POST(self):
            if self.path != state["path"] or state.get("used"):
                self._send("Not found", 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError, OverflowError):
                self._send("Bad request", 400)
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send("Request too large", 413)
                return
            raw = self.rfile.read(length).decode("utf-8", "replace")
            state["changed"] = apply_form(parse_qs(raw))
            state["used"] = True
            self._send(DONE % labels)
            state["done"].set()

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(lifetime=LIFETIME, on_ready=None, cancelled=None):
    """Run the page until it is saved or the screen closes.

    Returns how many settings changed, so the caller can say so rather than
    leaving somebody to guess whether the press landed.
    """
    address = pastebox.lan_address()
    if not address:
        kodi.log("no LAN address, cannot offer the setup page")
        return 0

    labels = _labels()
    state = {"done": threading.Event(), "used": False, "changed": 0,
             "path": "/%s" % pastebox.code()}
    server = pastebox.listen(_handler_for(state, labels))
    if server is None:
        kodi.log_exception("could not open the setup page")
        return 0

    url = "http://%s:%d%s" % (address, server.server_port, state["path"])
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    kodi.log("setup page is ready on the local network")

    try:
        if on_ready is not None:
            on_ready(url)
        deadline = time.time() + max(0.0, float(lifetime))
        while not state["done"].is_set():
            if cancelled is not None and cancelled.is_set():
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            state["done"].wait(min(0.2, remaining))
    finally:
        server.shutdown()
        server.server_close()
    return state["changed"]

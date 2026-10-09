# -*- coding: utf-8 -*-
"""Take a look inside the box from a computer, while sitting at the computer.

Everything that has gone wrong on the projector this far was diagnosed the
same way: pull the log over FTP, read it here, put a file back. That works,
but it needs an FTP server running on the box, the right path typed by hand,
and nobody minding that it exposes the whole of internal storage to anyone on
the network.

This is the same job done by the add-on itself: the log on screen, the
add-on's own folders to look through and fix, and the handful of repairs that
are otherwise four menus deep. Opened from the television, closed from the
television, and listening only while that screen is up - the same contract as
the key page, because the contract is the point.

What it may touch is deliberately narrow:

* **the log** - read only, the tail on screen and the whole thing to download
* **the add-on's data folder** - settings, caches, prepared subtitles; this
  is the one that gets edited when something is wedged, so files here can be
  viewed, replaced and deleted
* **the add-on's own folder** - read only. Being able to read a shipped file
  answers "is the box running what I think it is"; being able to delete one
  answers nothing and breaks the add-on.

Everything else on the device is not addressable from here at all: paths are
resolved and then checked against those roots, so `../../` lands outside and
is refused rather than followed.
"""
import io
import os
import shutil
import threading
import time

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import kodi, pastebox

LIFETIME = 1800
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
TAIL_BYTES = 60 * 1024
TEXT_SUFFIXES = (".log", ".txt", ".xml", ".json", ".srt", ".md", ".py", ".db-journal")

PAGE = u"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title><style>
body{font-family:system-ui,-apple-system,sans-serif;background:#111;color:#eee;
margin:0;padding:1.5rem 1rem}
main{max-width:60rem;margin:0 auto}
h1{font-size:1.2rem;margin:0 0 .2rem}
p.hint{color:#999;font-size:.9rem;margin:0 0 1rem}
nav button{font-size:1rem;padding:.5rem .9rem;margin-right:.4rem;border:0;
border-radius:.4rem;background:#272727;color:#eee;cursor:pointer}
nav button.on{background:#e50914;color:#fff}
section{display:none;margin-top:1rem}section.on{display:block}
pre{background:#000;border:1px solid #333;border-radius:.4rem;padding:.7rem;
overflow:auto;max-height:65vh;font-size:.8rem;line-height:1.35;white-space:pre-wrap}
table{width:100%%;border-collapse:collapse;font-size:.9rem}
td,th{text-align:left;padding:.35rem .4rem;border-bottom:1px solid #272727}
td.size{color:#999;white-space:nowrap}
a{color:#7ab7ff}
button.act{font-size:.95rem;padding:.6rem .8rem;margin:.2rem .3rem .2rem 0;
border:0;border-radius:.4rem;background:#272727;color:#eee;cursor:pointer}
button.danger{background:#5c1f22}
#said{color:#4caf50;min-height:1.2rem;margin-top:.6rem}
</style></head><body><main>
<h1>%(title)s</h1><p class="hint">%(hint)s</p>
<nav>
 <button class="on" onclick="show('log',this)">%(tab_log)s</button>
 <button onclick="show('files',this)">%(tab_files)s</button>
 <button onclick="show('fix',this)">%(tab_fix)s</button>
</nav>
<section id="log" class="on">
 <button class="act" onclick="loadLog()">%(refresh)s</button>
 <a class="act" href="log?whole=1" download="kodi.log">%(download)s</a>
 <pre id="logtext">...</pre>
</section>
<section id="files">
 <div id="tree">...</div>
</section>
<section id="fix">
 %(actions)s
 <div id="said"></div>
</section>
</main><script>
function show(name, button) {
  document.querySelectorAll('section').forEach(s => s.classList.toggle('on', s.id === name));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b === button));
  if (name === 'log') loadLog(); else if (name === 'files') browse('');
}
function loadLog() {
  fetch('log').then(r => r.text()).then(t => {
    const box = document.getElementById('logtext');
    box.textContent = t; box.scrollTop = box.scrollHeight;
  });
}
function browse(path) {
  fetch('files?path=' + encodeURIComponent(path)).then(r => r.text())
    .then(html => { document.getElementById('tree').innerHTML = html; });
}
function wipe(path) {
  if (!confirm(path)) return;
  const body = new URLSearchParams({path: path});
  fetch('delete', {method: 'POST', body: body}).then(r => r.text()).then(where => browse(where));
}
function run(name) {
  const said = document.getElementById('said');
  said.textContent = '...';
  fetch('run?what=' + name, {method: 'POST'}).then(r => r.text()).then(t => { said.textContent = t; });
}
loadLog();
</script></body></html>"""


def _labels():
    return {
        "title": kodi.localize(32588),
        "hint": kodi.localize(32589),
        "tab_log": kodi.localize(32590),
        "tab_files": kodi.localize(32591),
        "tab_fix": kodi.localize(32592),
        "refresh": kodi.localize(32593),
        "download": kodi.localize(32594),
    }


# The repairs that are otherwise four menus deep on a television.
ACTIONS = (
    ("cache", 32595),
    ("rows", 32596),
    ("keymap", 32597),
    ("update", 32598),
)


def roots():
    """{name: (path, writable)} - everything this page may address.

    A dict rather than a prefix check on one folder, because the two useful
    places are nowhere near each other: Kodi's log lives outside the add-on
    entirely, and the add-on's data is a sibling of its code rather than a
    child.
    """
    import xbmcvfs

    try:
        logs = xbmcvfs.translatePath("special://logpath")
    except Exception:
        logs = ""
    found = {"data": (kodi.profile_path(), True),
             "addon": (kodi.addon_path(), False)}
    if logs and os.path.isdir(logs):
        found["logs"] = (logs, False)
    return found


def log_path():
    for folder in (roots().get("logs", ("", False))[0], kodi.temp_dir()):
        candidate = os.path.join(folder or "", "kodi.log")
        if os.path.isfile(candidate):
            return candidate
    return ""


def resolve(relative):
    """An absolute path inside one of the roots, or ("", False).

    `..` is not forbidden by spelling but by result: the path is resolved
    first and then has to still be inside the root it claimed, so anything
    that climbs out simply is not found.
    """
    relative = (relative or "").replace("\\", "/").strip("/")
    name, _, rest = relative.partition("/")
    entry = roots().get(name)
    if entry is None:
        return "", False
    root, writable = entry
    full = os.path.realpath(os.path.join(root, rest))
    if full != os.path.realpath(root) and not full.startswith(
            os.path.realpath(root) + os.sep):
        return "", False
    return full, writable


def listing(relative):
    """The folder as rows of HTML: what is there, how big, and what may be
    done with it."""
    if not relative:
        rows = ['<table><tr><th>%s</th><th></th></tr>' % kodi.localize(32591)]
        for name in sorted(roots()):
            rows.append('<tr><td><a href="#" onclick="browse(\'%s\');return false">%s/</a>'
                        '</td><td class="size"></td></tr>' % (name, name))
        return "".join(rows) + "</table>"

    full, writable = resolve(relative)
    if not full or not os.path.isdir(full):
        return "<p>%s</p>" % kodi.localize(32599)

    up = relative.rstrip("/").rpartition("/")[0]
    rows = ['<table><tr><th>%s</th><th></th></tr>' % relative,
            '<tr><td><a href="#" onclick="browse(\'%s\');return false">..</a></td>'
            '<td class="size"></td></tr>' % up]
    for name in sorted(os.listdir(full)):
        child = os.path.join(full, name)
        where = "%s/%s" % (relative.rstrip("/"), name)
        if os.path.isdir(child):
            rows.append('<tr><td><a href="#" onclick="browse(\'%s\');return false">%s/</a>'
                        '</td><td class="size"></td></tr>' % (where, name))
            continue
        size = os.path.getsize(child)
        actions = ['<a href="file?path=%s" download="%s">%s</a>'
                   % (quote(where), name, kodi.localize(32594))]
        if writable:
            actions.append('<a href="#" onclick="wipe(\'%s\');return false">%s</a>'
                           % (where, kodi.localize(32600)))
        rows.append('<tr><td>%s<br><small>%s</small></td><td class="size">%s</td></tr>'
                    % (name, " &middot; ".join(actions), _readable(size)))
    return "".join(rows) + "</table>"


def _readable(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%d %s" % (size, unit)
        size //= 1024
    return "%d B" % size


def tail(path, limit=TAIL_BYTES):
    """The end of a file, which is the part worth reading."""
    if not path or not os.path.isfile(path):
        return ""
    size = os.path.getsize(path)
    with io.open(path, "rb") as handle:
        if size > limit:
            handle.seek(size - limit)
        return handle.read().decode("utf-8", "replace")


def run_action(name):
    """One repair. Returns what to say about it."""
    if name == "cache":
        from . import cache
        cache.clear()
        return kodi.localize(32601)
    if name == "rows":
        from . import catalog
        catalog.invalidate()
        catalog.warm(force=True)
        return kodi.localize(32602)
    if name == "keymap":
        from . import remotekeys
        return kodi.localize(32603) if remotekeys.install() else kodi.localize(32319)
    if name == "update":
        from . import updater
        found = updater.check()
        if not found:
            return kodi.localize(32500, updater.installed_version())
        return kodi.localize(32604, found[0])
    return kodi.localize(32599)


def _handler_for(state, labels):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, code=200, kind="text/html; charset=utf-8",
                  filename=""):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            if filename:
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s"' % filename)
            self.end_headers()
            self.wfile.write(data)

        def _route(self):
            """(what was asked for, its query), or (None, {}) when the request
            did not come through this page's own address."""
            split = urlsplit(self.path)
            path = unquote(split.path)
            if not path.startswith(state["path"]):
                return None, {}
            return path[len(state["path"]):].strip("/"), parse_qs(split.query)

        def do_GET(self):
            what, query = self._route()
            if what is None:
                return self._send("Not found", 404, "text/plain")
            if what == "":
                return self._send(page(labels))
            if what == "log":
                path = log_path()
                if query.get("whole"):
                    with io.open(path, "rb") as handle:
                        return self._send(handle.read(), kind="text/plain; charset=utf-8",
                                          filename="kodi.log")
                return self._send(tail(path), kind="text/plain; charset=utf-8")
            if what == "files":
                return self._send(listing((query.get("path") or [""])[0]))
            if what == "file":
                full, _writable = resolve((query.get("path") or [""])[0])
                if not full or not os.path.isfile(full):
                    return self._send("Not found", 404, "text/plain")
                with io.open(full, "rb") as handle:
                    return self._send(handle.read(),
                                      kind="application/octet-stream",
                                      filename=os.path.basename(full))
            return self._send("Not found", 404, "text/plain")

        def do_POST(self):
            what, query = self._route()
            if what is None:
                return self._send("Not found", 404, "text/plain")
            if what == "run":
                name = (query.get("what") or [""])[0]
                try:
                    return self._send(run_action(name), kind="text/plain; charset=utf-8")
                except Exception:
                    kodi.log_exception("remote action %s failed" % name)
                    return self._send(kodi.localize(32319), kind="text/plain; charset=utf-8")
            if what == "delete":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError, OverflowError):
                    return self._send("Bad request", 400, "text/plain")
                if length <= 0 or length > 4096:
                    return self._send("Bad request", 400, "text/plain")
                form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                where = (form.get("path") or [""])[0]
                full, writable = resolve(where)
                if full and writable and os.path.exists(full):
                    try:
                        if os.path.isdir(full):
                            shutil.rmtree(full)
                        else:
                            os.remove(full)
                        kodi.log("removed from the console: %s" % where)
                    except OSError:
                        kodi.log_exception("could not remove %s" % where)
                parent = where.rstrip("/").rpartition("/")[0]
                return self._send(parent, kind="text/plain; charset=utf-8")
            return self._send("Not found", 404, "text/plain")

        def log_message(self, fmt, *args):
            pass

    return Handler


def page(labels=None):
    labels = labels or _labels()
    actions = "".join(
        '<button class="act%s" onclick="run(\'%s\')">%s</button>'
        % (" danger" if name == "cache" else "", name, kodi.localize(string_id))
        for name, string_id in ACTIONS)
    return PAGE % dict(labels, actions=actions)


def serve(lifetime=LIFETIME, on_ready=None, cancelled=None):
    """Run the console until the screen that opened it closes."""
    address = pastebox.lan_address()
    if not address:
        kodi.log("no LAN address, cannot open the console")
        return False

    labels = _labels()
    state = {"path": "/%s/" % pastebox.code(), "done": threading.Event()}
    server = pastebox.listen(_handler_for(state, labels))
    if server is None:
        kodi.log_exception("could not open the console")
        return False

    url = "http://%s:%d%s" % (address, server.server_port, state["path"])
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    kodi.log("console is open on the local network")

    try:
        if on_ready is not None:
            on_ready(url)
        deadline = time.time() + max(0.0, float(lifetime))
        while time.time() < deadline:
            if cancelled is not None and cancelled.is_set():
                break
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
        kodi.log("console closed")
    return True

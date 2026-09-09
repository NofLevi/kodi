# -*- coding: utf-8 -*-
"""One way of signing in, used by every service.

Typing a thirty-two character key on a television with a remote is the worst
thing this add-on ever asks anybody to do, and until now each service asked
differently: TorBox wanted a typed key, Real-Debrid ran a device flow,
Premiumize offered a choice in wording of its own, AllDebrid showed a PIN. Same
job, four screens.

There is one screen now, and it offers whatever the service actually supports,
in the order of how little work it is:

    1. Scan a code with your phone   - nothing to type at all
    2. Paste it from your phone      - for a key, without the remote
    3. Type the key here             - the fallback that always works

An option is only offered when the service has it. Nothing here pretends: a
service with no device flow does not get a "scan a code" entry that opens a
page and then asks you to type the key anyway.

There used to be a fourth, "open a link and type a code", and it was the same
flow as the first one with the QR code switched off - the screen shows the
link and the code either way, which is exactly what somebody without a camera
needs. So it was never a different way in, only a worse presentation of the
same one, and offering it made the viewer answer a question about themselves
that the screen had already answered.
"""
import threading
import time

from .. import kodi, qr

# How often to look while waiting for somebody to finish on their phone, and
# how long to leave the window up if the service does not say.
POLL_SECONDS = 5
DEFAULT_LIFETIME = 600

KEY = "key"
SCAN = "scan"
PASTE = "paste"


def choose_method(title, methods, can_paste=True, overrides=None):
    """Ask how the viewer wants to sign in. Returns a method or None.

    Each entry is named for what it makes the viewer *do*, because that is the
    only thing they are choosing between. "Scan a code with your phone" was
    the name of an entry whose own screen says "scan the code, or open the
    address and enter the code below" - so the way in that needs no camera was
    there, drawn beside the QR, and advertised nowhere.

    `overrides` lets one service rename an entry that means something
    different for it. Trakt's second way in is not a key, it is your own
    registered application, and calling it "type your key" would be a lie.
    """
    labels = {
        # Marked, not chosen for them. It is the least work by a distance -
        # nothing typed and nothing copied - and saying so is more useful than
        # silently running it, which leaves somebody looking at a QR code with
        # no idea the other ways in exist.
        SCAN: "%s   (%s)" % (kodi.localize(32460), kodi.localize(32515)),
        PASTE: kodi.localize(32513),
        KEY: kodi.localize(32462),
    }
    for method, string_id in (overrides or {}).items():
        labels[method] = kodi.localize(string_id)

    # A key that can be typed can be pasted from a phone instead, so PASTE is
    # offered wherever KEY is rather than being declared by every client. It
    # sits above KEY because typing thirty-two characters on a remote is the
    # worst thing this add-on asks anybody to do.
    #
    # Only where the client can actually take one, though: the paste path ends
    # at `authorize_with_key`, and offering it to a service without that is an
    # entry that crashes rather than one that signs you in.
    methods = tuple(methods)
    if can_paste and KEY in methods and PASTE not in methods:
        methods = methods + (PASTE,)
    offered = [m for m in (SCAN, PASTE, KEY) if m in methods]
    if not offered:
        return None
    if len(offered) == 1:
        return offered[0]
    choice = kodi.select([labels[m] for m in offered], title)
    return offered[choice] if 0 <= choice < len(offered) else None


def ask_for_key(title, current="", help_url=""):
    """The typed path, with the option of putting the page on a phone first.

    `help_url` is where the key lives on the service's website. Showing it as
    a code first is the difference between "find your API key" and a link you
    can actually open, and it costs one extra button.
    """
    if help_url:
        show_url(title, help_url, kodi.localize(32463))
    entered = kodi.keyboard(current, title)
    return None if entered is None else entered.strip()


def show_url(title, url, message=""):
    """Put a URL on screen as a scannable code. Returns when it is dismissed.

    Used for pages that are not a device flow - "your API key is here" - so
    there is nothing to poll for and nothing to wait on.
    """
    from .auth_window import open_auth
    open_auth(title=title, url=url, code="", message=message, poll=None)


def run_device(title, url, code, poll, lifetime=None, interval=POLL_SECONDS):
    """Show a device flow and wait for it to finish.

    `poll` is called every few seconds and returns True when the viewer has
    finished on their phone, False while they have not, and None if the
    attempt has failed for good. Returning None rather than False matters:
    an expired code should say so rather than sit there until the clock runs
    out.

    Returns True only on a real success.
    """
    from .auth_window import open_auth

    deadline = time.time() + float(lifetime or DEFAULT_LIFETIME)
    state = {"done": False}

    def tick():
        """Called by the window. Returns the fraction still to run, or None
        to close: True means signed in, False means give up."""
        if time.time() >= deadline:
            return None
        try:
            answer = poll()
        except Exception:
            kodi.log_exception("sign-in poll failed")
            return None
        if answer is None:
            return None
        if answer:
            state["done"] = True
            return None
        return max(0.0, (deadline - time.time()) / float(lifetime or
                                                         DEFAULT_LIFETIME))

    open_auth(title=title, url=url, code=code,
              message=kodi.localize(32464) if code else "",
              poll=tick, interval=interval)
    return state["done"]


def code_image(url):
    """A QR image path for a URL, or "" if one could not be made."""
    try:
        qr.cleanup()
    except Exception:
        pass
    return qr.image_for(url)


def receive_key(title, placeholder="", lifetime=None):
    """Take a key from a phone on the same network. Returns it, or None.

    The address is served by `pastebox`, and shown here the same way a device
    code is - as something to scan - because the whole point is that nothing
    gets typed on the television.
    """
    from .. import pastebox
    from .auth_window import open_auth

    state = {"value": "", "url": ""}
    ready = threading.Event()
    span = float(lifetime or pastebox.LIFETIME)

    def serve():
        state["value"] = pastebox.receive(
            title, placeholder, lifetime=span,
            on_ready=lambda url: (state.__setitem__("url", url), ready.set()))

    thread = threading.Thread(target=serve)
    thread.daemon = True
    thread.start()

    # No LAN address, or the socket would not open. Say so by returning None,
    # which sends the caller back to the chooser rather than to a blank wait.
    if not ready.wait(5) or not state["url"]:
        return None

    deadline = time.time() + span

    def tick():
        if state["value"]:
            return None
        if time.time() >= deadline:
            return None
        return max(0.0, (deadline - time.time()) / span)

    open_auth(title=title, url=state["url"], code="",
              message=kodi.localize(32514), poll=tick, interval=1)
    return state["value"] or None

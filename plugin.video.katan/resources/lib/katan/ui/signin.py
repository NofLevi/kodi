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
    2. Open a link and type a code   - six characters, on the phone
    3. Type the key here             - the fallback that always works

An option is only offered when the service has it. Nothing here pretends: a
service with no device flow does not get a "scan a code" entry that opens a
page and then asks you to type the key anyway.
"""
import time

from .. import kodi, qr

# How often to look while waiting for somebody to finish on their phone, and
# how long to leave the window up if the service does not say.
POLL_SECONDS = 5
DEFAULT_LIFETIME = 600

KEY = "key"
LINK = "link"
SCAN = "scan"


def choose_method(title, methods):
    """Ask how the viewer wants to sign in. Returns a method or None."""
    labels = {
        SCAN: kodi.localize(32460),
        LINK: kodi.localize(32461),
        KEY: kodi.localize(32462),
    }
    offered = [m for m in (SCAN, LINK, KEY) if m in methods]
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


def run_device(title, url, code, poll, lifetime=None, interval=POLL_SECONDS,
               scan=True):
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
              poll=tick, interval=interval, scan=scan)
    return state["done"]


def code_image(url):
    """A QR image path for a URL, or "" if one could not be made."""
    try:
        qr.cleanup()
    except Exception:
        pass
    return qr.image_for(url)

"""Update the add-on from inside the add-on.

Kodi's own repository already does this on a schedule and preserves settings
while it does. This exists for the case the repository cannot cover: the first
update after a manual zip install, and a viewer who wants the new version now
rather than whenever Kodi next looks.

Nothing here touches `userdata/addon_data`, which is where every key and every
setting lives, so an update cannot lose them. That is not a promise this module
keeps by being careful - it is true because the add-on writes nothing inside
its own folder in the first place, and `test_upgrade.py` holds it to that.

The one dangerous step is replacing the folder, so it is the one with care
taken: the download is unpacked to a temporary directory and checked for a
parseable `addon.xml` before anything installed is touched.
"""
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from . import http, kodi, settings

ADDON_ID = "plugin.video.katan"

# Where the repository index lives. Overridable in settings so the host can
# move without waiting for a release from the host that moved.
DEFAULT_INDEX = "https://kodi-katan.pages.dev/addons.xml"

# The test channel: whatever `development` was built into last, rather than
# the last release. A second Pages project rather than a folder or a branch
# alias on the first, for two reasons - a direct upload replaces the whole
# site, so the two would overwrite each other, and Pages puts branch aliases
# behind Cloudflare Access, which answers a 302 to a login page and is
# therefore invisible to Kodi.
TEST_INDEX = "https://kodi-katan-dev.pages.dev/addons.xml"

VERSION = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def on_test_channel():
    return (settings.get("update.channel") or "stable").strip() == "test"


def index_url():
    """An explicit url wins, then the channel, then the released one.

    `update.url` stays the final override so the host can move without
    waiting for a release from the host that moved.
    """
    explicit = (settings.get("update.url") or "").strip()
    if explicit:
        return explicit
    return TEST_INDEX if on_test_channel() else DEFAULT_INDEX


def parse_version(text):
    """A comparable tuple. Anything unparseable sorts lowest."""
    match = VERSION.match((text or "").strip())
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def installed_version():
    return kodi.addon_version()


def check():
    """Return (latest_version, zip_url) when an update exists, else None."""
    url = index_url()
    response = http.get(url, timeout=(5, 12), retries=1)
    if response is None or response.status_code >= 400:
        kodi.log("update check failed: %s"
                 % (response.status_code if response else "no response"))
        return None

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        kodi.log_error("the update index is not valid XML")
        return None

    latest = ""
    for node in root.findall("addon"):
        if node.get("id") == ADDON_ID:
            latest = node.get("version") or ""
            break
    if not latest:
        kodi.log("the update index does not list %s" % ADDON_ID)
        return None

    installed = installed_version()
    if on_test_channel():
        # A test build is "what the branch is now", not "an upgrade". Its
        # version sorts as a pre-release - 0.0.5~dev.12 - so two of them in a
        # row compare equal under parse_version, and going back to stable
        # means installing something numerically *older*. Neither would ever
        # be offered by a greater-than. Any difference is the answer here.
        if latest == installed:
            return None
    elif parse_version(latest) <= parse_version(installed):
        return None

    base = url.rsplit("/", 1)[0]
    return latest, "%s/zips/%s/%s-%s.zip" % (base, ADDON_ID, ADDON_ID, latest)


def download(zip_url, progress=None):
    """Fetch the release to a temporary file. Returns its path, or ''."""
    response = http.get(zip_url, timeout=(5, 60), retries=1, stream=True)
    if response is None or response.status_code >= 400:
        kodi.log_error("could not download %s" % zip_url)
        return ""

    handle, path = tempfile.mkstemp(suffix=".zip", prefix="katan-update-")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(response.content)
    except Exception:
        kodi.log_exception("writing the update failed")
        _remove(path)
        return ""

    if not _is_sane_zip(path):
        _remove(path)
        return ""
    return path


def _is_sane_zip(path):
    """A release must be a readable zip that carries a parseable addon.xml.

    A truncated download is a perfectly ordinary outcome on a projector on
    wifi, and installing one would leave an add-on that cannot start.
    """
    member = "%s/addon.xml" % ADDON_ID
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                kodi.log_error("the downloaded update is corrupt")
                return False
            if member not in archive.namelist():
                kodi.log_error("the downloaded update has no %s" % member)
                return False
            ET.fromstring(archive.read(member))
    except (zipfile.BadZipfile, ET.ParseError, KeyError, OSError):
        kodi.log_exception("the downloaded update is not usable")
        return False
    return True


def apply(zip_path):
    """Replace the installed add-on with the downloaded one.

    Unpacked beside the add-ons folder first, so a failure half way through
    cannot leave the installed copy deleted and unreplaced.
    """
    # normpath, because real Kodi hands back the add-on path with a trailing
    # separator and the stub does not. With one, `target + ".old"` names a
    # file *inside* the folder being replaced rather than a sibling of it, and
    # os.path.dirname returns the add-on folder itself - so the staging
    # directory was unpacked inside the thing it was meant to replace, and the
    # rename failed with WinError 87 on a projector while passing every test
    # here.
    target = os.path.normpath(kodi.addon_path())
    addons_dir = os.path.dirname(target)
    staging = tempfile.mkdtemp(prefix="katan-staging-", dir=addons_dir)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(staging)
        unpacked = os.path.join(staging, ADDON_ID)
        if not os.path.isfile(os.path.join(unpacked, "addon.xml")):
            kodi.log_error("the update unpacked without an addon.xml")
            return False

        backup = target + ".old"
        _remove_tree(backup)
        if os.path.isdir(target):
            os.rename(target, backup)
        try:
            os.rename(unpacked, target)
        except OSError:
            if os.path.isdir(backup):        # put it back rather than leave none
                os.rename(backup, target)
            raise
        _remove_tree(backup)
        return True
    except Exception:
        kodi.log_exception("installing the update failed")
        return False
    finally:
        _remove_tree(staging)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _remove_tree(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def update_now(silent=False):
    """The whole flow, as the Tools entry runs it."""
    found = check()
    if not found:
        if not silent:
            kodi.ok_dialog(kodi.localize(32500, installed_version()))
        return False

    latest, zip_url = found
    if not kodi.yes_no(kodi.localize(32501, installed_version(), latest)):
        return False

    import xbmcgui
    path = ""
    progress = xbmcgui.DialogProgressBG()
    progress.create("Katan", kodi.localize(32502))
    try:
        path = download(zip_url)
        if not path:
            kodi.ok_dialog(kodi.localize(32503))
            return False
        progress.update(70, message=kodi.localize(32504))
        ok = apply(path)
    finally:
        progress.close()
        if path:
            _remove(path)

    if not ok:
        kodi.ok_dialog(kodi.localize(32503))
        return False

    kodi.run_builtin("UpdateLocalAddons")
    kodi.log("updated to %s" % latest, kodi.LOG_INFO)
    if kodi.yes_no(kodi.localize(32505, latest)):
        kodi.run_builtin("RestartApp")
    return True

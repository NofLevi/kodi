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
DEFAULT_INDEX = "https://noflevi.github.io/kodi/addons.xml"

# The test channel: whatever `development` was built into last, rather than
# the last release. It is a branch served by raw.githubusercontent.com rather
# than a second folder on the Pages site, because a Pages deployment replaces
# the whole site - the two channels would overwrite each other every time
# either published. A branch is independent by construction, and raw serves
# ranged reads, which is the thing the previous host did not.
TEST_INDEX = "https://raw.githubusercontent.com/NofLevi/kodi/test-channel/addons.xml"

VERSION = re.compile(r"(\d{1,9})(?:\.(\d{1,9}))?(?:\.(\d{1,9}))?")
MAX_VERSION_LENGTH = 29
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_ZIP_MEMBERS = 1024
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
REQUIRED_MEMBERS = ("addon.xml", "main.py")


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
    clean = (text or "").strip()
    if len(clean) > MAX_VERSION_LENGTH:
        return (0, 0, 0)
    match = VERSION.fullmatch(clean)
    if not match:
        return (0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


def installed_version():
    return kodi.addon_version()


def published_version():
    """What the current channel is publishing: (version, zip_url).

    Separate from `check` because "what is out there" and "should this box
    install it" are different questions, and the second is the only one with
    an opinion in it. Returns ("", "") when the index cannot be read.
    """
    url = index_url()
    response = http.get(url, timeout=(5, 12), retries=1,
                        max_bytes=1024 * 1024)
    if response is None or response.status_code >= 400:
        kodi.log("update check failed: %s"
                 % (response.status_code if response else "no response"))
        return "", ""

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        kodi.log_error("the update index is not valid XML")
        return "", ""

    latest = ""
    for node in root.findall("addon"):
        if node.get("id") == ADDON_ID:
            latest = node.get("version") or ""
            break
    if not latest:
        kodi.log("the update index does not list %s" % ADDON_ID)
        return "", ""

    base = url.rsplit("/", 1)[0]
    return latest, "%s/zips/%s/%s-%s.zip" % (base, ADDON_ID, ADDON_ID, latest)


def check():
    """Return (latest_version, zip_url) when an update exists, else None."""
    latest, zip_url = published_version()
    if not latest:
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

    return latest, zip_url


def download(zip_url, progress=None, expected_version=None):
    """Fetch the release to a temporary file. Returns its path, or ''."""
    response = http.get(zip_url, timeout=(5, 60), retries=1, stream=True)
    if response is None or response.status_code >= 400:
        kodi.log_error("could not download update from %s" % http._host(zip_url))
        return ""

    encoding = (response.headers.get("Content-Encoding") or "identity").lower()
    try:
        declared = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError, OverflowError):
        declared = -1
    if encoding not in ("", "identity") or declared < 0 or declared > MAX_ZIP_BYTES:
        response.close()
        kodi.log_error("the update download has unsafe transport metadata")
        return ""

    handle, path = tempfile.mkstemp(suffix=".zip", prefix="katan-update-")
    try:
        with os.fdopen(handle, "wb") as out:
            total = 0
            while True:
                chunk = response.raw.read(64 * 1024, decode_content=False)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise ValueError("update archive exceeds size limit")
                out.write(chunk)
    except Exception:
        kodi.log_exception("writing the update failed")
        _remove(path)
        return ""
    finally:
        response.close()

    if not _is_sane_zip(path, expected_version=expected_version):
        _remove(path)
        return ""
    return path


def _is_sane_zip(path, expected_version=None):
    """Validate identity, completeness, canonical paths and resource bounds."""
    member = "%s/addon.xml" % ADDON_ID
    try:
        if os.path.getsize(path) > MAX_ZIP_BYTES:
            return False
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_MEMBERS:
                kodi.log_error("the downloaded update has an unsafe member count")
                return False
            approved = {}
            expanded = 0
            for info in infos:
                raw_name = info.filename
                parts = raw_name.split("/")
                if ("\\" in raw_name or raw_name.startswith("/")
                        or any(part in ("", ".", "..") for part in parts[:-1])):
                    kodi.log_error("the downloaded update has an unsafe path")
                    return False
                canonical = "/".join(part for part in parts if part)
                folded = canonical.casefold()
                if folded in approved:
                    kodi.log_error("the downloaded update has duplicate paths")
                    return False
                approved[folded] = canonical
                if info.is_dir():
                    continue
                expanded += info.file_size
                ratio = info.file_size / float(max(1, info.compress_size))
                if (info.file_size > MAX_MEMBER_BYTES
                        or expanded > MAX_EXPANDED_BYTES
                        or ratio > MAX_COMPRESSION_RATIO):
                    kodi.log_error("the downloaded update exceeds safe archive limits")
                    return False
                mode = (info.external_attr >> 16) & 0o170000
                if mode and mode != 0o100000:
                    kodi.log_error("the downloaded update contains a special file")
                    return False
            if archive.testzip() is not None:
                kodi.log_error("the downloaded update is corrupt")
                return False
            if member not in archive.namelist():
                kodi.log_error("the downloaded update has no %s" % member)
                return False
            for required in REQUIRED_MEMBERS:
                name = ("%s/%s" % (ADDON_ID, required)).casefold()
                if name not in approved:
                    kodi.log_error("the downloaded update is incomplete")
                    return False
            root = ET.fromstring(archive.read(member))
            if root.tag != "addon" or root.get("id") != ADDON_ID:
                kodi.log_error("the downloaded update has the wrong add-on identity")
                return False
            version = root.get("version") or ""
            if expected_version is not None and version != expected_version:
                kodi.log_error("the downloaded update version does not match the index")
                return False
    except (zipfile.BadZipfile, ET.ParseError, KeyError, OSError, OverflowError):
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
    if not _is_sane_zip(zip_path):
        return False
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
        sweep(addons_dir, keep=zip_path)
        return True
    except Exception:
        kodi.log_exception("installing the update failed")
        return False
    finally:
        _remove_tree(staging)


# Files left lying around by an update, none of which anything reads again.
_LEFTOVERS = (ADDON_ID + ".old", ADDON_ID + ".old.old")
_STAGING = "katan-staging-"
_DOWNLOAD = "katan-update-"


def sweep(addons_dir, keep=""):
    """Delete what an update leaves behind. Returns what it removed.

    The add-on folder itself is replaced wholesale, so the previous version's
    *source* is gone the moment the rename succeeds. What survives is
    everything around it, and none of it is small:

    - `plugin.video.katan.old`, when the removal above lost a race with a file
      still open. It is 1.8 MB of a version nobody will run again, and Kodi
      scans every folder under `addons/`.
    - `katan-staging-*`, left by a process killed mid-update rather than by a
      failure - the `finally` cannot run if there is no longer a process.
    - `addons/packages/plugin.video.katan-*.zip`. This is the one that
      actually accumulates: Kodi's own repository update downloads there and
      keeps it, one zip per release forever, and it is the copy people find
      months later and install by hand.
    - `katan-update-*.zip` in the temporary directory, from runs that died
      between downloading and installing.

    Only ever this add-on's own files. A zip in `packages/` belonging to
    somebody else's add-on is somebody else's business, and `keep` is the zip
    being installed right now - deleting that mid-install would be removing
    the thing under our own feet.
    """
    removed = []

    for name in _LEFTOVERS:
        path = os.path.join(addons_dir, name)
        if os.path.isdir(path):
            _remove_tree(path)
            if not os.path.exists(path):
                removed.append(path)

    for name in _listing(addons_dir):
        if name.startswith(_STAGING):
            path = os.path.join(addons_dir, name)
            _remove_tree(path)
            if not os.path.exists(path):
                removed.append(path)

    packages = os.path.join(addons_dir, "packages")
    for name in _listing(packages):
        if name.startswith(ADDON_ID + "-") and name.endswith(".zip"):
            path = os.path.join(packages, name)
            if os.path.abspath(path) == os.path.abspath(keep or ""):
                continue
            _remove(path)
            if not os.path.exists(path):
                removed.append(path)

    temporary = tempfile.gettempdir()
    for name in _listing(temporary):
        if name.startswith(_DOWNLOAD) and name.endswith(".zip"):
            path = os.path.join(temporary, name)
            if os.path.abspath(path) == os.path.abspath(keep or ""):
                continue
            _remove(path)
            if not os.path.exists(path):
                removed.append(path)

    if removed:
        kodi.log("update tidy-up removed %d leftover(s)" % len(removed))
    return removed


def _listing(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


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
        path = download(zip_url, expected_version=latest)
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

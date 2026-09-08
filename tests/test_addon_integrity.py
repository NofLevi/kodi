"""Checks that catch the mistakes that are invisible until Kodi loads the add-on."""
import os
import re
import xml.etree.ElementTree as ET

import pytest

from conftest import ADDON_DIR, ROOT, TESTS_DIR

LANG_DIR = os.path.join(ADDON_DIR, "resources", "language")
SKIN_DIR = os.path.join(ADDON_DIR, "resources", "skins", "default", "1080i")
PACKAGE_ROOT = os.path.join(ADDON_DIR, "resources", "lib", "katan")


def test_addon_xml_is_valid_and_declares_three_extension_points():
    tree = ET.parse(os.path.join(ADDON_DIR, "addon.xml"))
    root = tree.getroot()
    assert root.get("id") == "plugin.video.katan"
    points = {e.get("point") for e in root.findall("extension")}
    assert {"xbmc.python.pluginsource", "xbmc.subtitle.module",
            "xbmc.service"} <= points


def test_declared_entry_points_exist():
    root = ET.parse(os.path.join(ADDON_DIR, "addon.xml")).getroot()
    for extension in root.findall("extension"):
        library = extension.get("library")
        if library:
            assert os.path.isfile(os.path.join(ADDON_DIR, library)), library


def test_declared_assets_exist():
    root = ET.parse(os.path.join(ADDON_DIR, "addon.xml")).getroot()
    for asset in root.iter():
        if asset.tag in ("icon", "fanart"):
            assert os.path.isfile(os.path.join(ADDON_DIR, asset.text)), asset.text


def test_an_empty_string_default_says_it_allows_empty():
    """Kodi drops a string setting whose empty default is not declared.

    It logs "error reading the default value" and then "requested setting was
    not found", and the setting simply does not exist: it cannot be shown and
    it cannot be written. Every API key, every debrid token and the kids PIN
    were in that state, so the settings dialog showed almost nothing and
    nothing the user typed could have been saved.

    The form Kodi accepts is <default/> plus an allowempty constraint.
    """
    tree = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml"))

    offenders = []
    for setting in tree.iter("setting"):
        if setting.get("type") != "string":
            continue
        default = setting.find("default")
        if default is None or (default.text or "").strip():
            continue
        constraints = setting.find("constraints")
        allowed = (constraints is not None
                   and constraints.find("allowempty") is not None
                   and (constraints.find("allowempty").text or "").strip()
                   == "true")
        if not allowed:
            offenders.append(setting.get("id"))

    assert not offenders, (
        "these string settings default to empty without allowempty, so Kodi "
        "will drop them: %s" % offenders)


def test_every_setting_the_code_uses_is_declared():
    """A setting settings.xml never names cannot be written, only read.

    settings.get falls back to DEFAULTS so reading looks fine, which is why
    this hid: the wizard's OAuth tokens and the chosen device profile were
    being written to a setting Kodi did not have and silently discarded.
    """
    from katan import settings

    tree = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml"))
    declared = {node.get("id") for node in tree.iter("setting")}

    missing = sorted(key for key in settings.DEFAULTS if key not in declared)
    assert not missing, \
        "settings used in code but not declared in settings.xml: %s" % missing


def test_settings_labels_are_string_ids():
    """Kodi takes a numeric string id for label, help and heading.

    Plain text is not a fallback: it renders as nothing, so every label in the
    settings dialog was blank and none of them could ever be translated, which
    matters rather a lot in a Hebrew-first add-on. Found by opening the dialog
    in a real Kodi, because nothing here could see it.

    <option> is exempt: its label really is the displayed value.
    """
    path = os.path.join(ADDON_DIR, "resources", "settings.xml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    offenders = []
    for line in text.splitlines():
        if "<option" in line:
            continue
        for name, value in re.findall(r'\b(label|help)="([^"]*)"', line):
            if value and not value.isdigit():
                offenders.append("%s=%r" % (name, value))
    for value in re.findall(r"<heading>([^<]*)</heading>", text):
        if value.strip() and not value.strip().isdigit():
            offenders.append("<heading>%s</heading>" % value.strip())

    assert not offenders, \
        "settings.xml has plain text where Kodi wants a string id: %s" \
        % offenders[:5]

    # <help> is an attribute in this format, never an element.
    assert "<help>" not in text, \
        "<help> is not an element in Kodi's settings format; it is ignored"


def test_every_settings_string_id_exists():
    """A label pointing at a missing string renders blank, same as plain text."""
    path = os.path.join(ADDON_DIR, "resources", "settings.xml")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    used = set(re.findall(r'\b(?:label|help)="(\d+)"', text))
    used |= set(re.findall(r"<heading>(\d+)</heading>", text))

    po = os.path.join(LANG_DIR, "resource.language.en_gb", "strings.po")
    with open(po, encoding="utf-8") as handle:
        available = set(re.findall(r'msgctxt "#(\d+)"', handle.read()))

    missing = sorted(used - available, key=int)
    assert not missing, "settings.xml uses string ids with no string: %s" % missing


def test_no_string_id_is_defined_twice():
    """A duplicate msgctxt silently shadows one of the two labels."""
    for folder in os.listdir(LANG_DIR):
        po = os.path.join(LANG_DIR, folder, "strings.po")
        if not os.path.isfile(po):
            continue
        with open(po, encoding="utf-8") as handle:
            ids = re.findall(r'msgctxt "#(\d+)"', handle.read())
        duplicates = sorted({i for i in ids if ids.count(i) > 1}, key=int)
        assert not duplicates, "%s defines %s more than once" % (folder, duplicates)


def test_the_home_rows_fit_on_screen():
    """The row area has to hold a whole number of rows.

    This has now gone wrong twice. A grouplist does not simply crop what does
    not fit: it scrolls to keep the focused control visible, so a few pixels
    short slides the whole column up, takes the focused row's heading off the
    top and clips the next row against the bottom of the screen. The first row
    then looks like it has no title, which is not an obvious symptom of a
    height being wrong. The arithmetic is cheap to check, so it is checked.
    """
    tree = ET.parse(os.path.join(SKIN_DIR, "katan-home.xml"))

    grouplist = None
    for control in tree.getroot().iter("control"):
        if control.get("id") == "8000":
            grouplist = control
            break
    assert grouplist is not None, "the row grouplist should have id 8000"

    def value(node, tag, default=0):
        found = node.find(tag)
        return int(found.text) if found is not None and found.text else default

    top = value(grouplist, "top")
    height = value(grouplist, "height")
    gap = value(grouplist, "itemgap")

    rows = [c for c in grouplist.findall("control") if c.get("type") == "group"]
    assert rows, "expected the row groups"
    row_height = value(rows[0], "height")

    assert top + height <= 1080, \
        "the row area runs past the bottom of a 1080 screen"

    fit = (height + gap) // (row_height + gap)
    assert fit >= 2, (
        "only %d whole rows fit in %d px: %d rows of %d plus a %d gap need %d"
        % (fit, height, 2, row_height, gap, 2 * row_height + gap))

    # And every row group must be tall enough for its own heading and list.
    for row in rows:
        label = row.find("control")
        listing = [c for c in row.findall("control") if c.get("type") == "list"]
        assert listing, "a row group with no list"
        needed = value(listing[0], "top") + value(listing[0], "height")
        assert needed <= value(row, "height"), \
            "row %s is %d px shorter than its own contents" % (
                row.get("id"), needed - value(row, "height"))


def test_the_home_screen_has_a_slot_for_every_row_on_by_default():
    """ROW_SLOTS must match the skin, and cover the rows shipped switched on.

    The window draws `catalog.enabled_rows()[:ROW_SLOTS]`. When ROW_SLOTS was
    ten and fifteen rows were on by default, the last five were dropped -
    among them the Israeli live channels, which is the thing the add-on is
    for. Nothing failed, nothing was logged, and the rows were present and
    correct everywhere except on the screen.

    Two separate claims, because they break separately: the constant has to
    match the number of list controls that actually exist in the XML, and
    there have to be enough of them for a fresh install to show its own
    default rows without any being cut.
    """
    from katan import catalog
    from katan.ui import home_window

    with open(os.path.join(SKIN_DIR, "katan-home.xml"),
              encoding="utf-8") as handle:
        body = handle.read()
    in_skin = sorted(int(i) for i in
                     re.findall(r'type="list" id="(50\d\d)"', body))

    assert in_skin == list(range(home_window.LIST_BASE,
                                 home_window.LIST_BASE + len(in_skin))), \
        "the row list ids must run consecutively from LIST_BASE: %s" % in_skin
    assert len(in_skin) == home_window.ROW_SLOTS, (
        "ROW_SLOTS is %d but katan-home.xml has %d row lists, so %s"
        % (home_window.ROW_SLOTS, len(in_skin),
           "rows would be dropped" if home_window.ROW_SLOTS > len(in_skin)
           else "controls exist that nothing can fill"))

    on_by_default = [row["id"] for row in catalog.rows() if row.get("default")]
    assert len(on_by_default) <= home_window.ROW_SLOTS, (
        "%d rows ship switched on but only %d can be drawn, so a fully "
        "configured install would never see %s"
        % (len(on_by_default), home_window.ROW_SLOTS,
           on_by_default[home_window.ROW_SLOTS:]))

    # And the same for every tab, which is the bigger number now: the Films
    # tab is the one that decides how many row controls the skin needs.
    for section in catalog.section_ids():
        ids = catalog.enabled_row_ids(section)
        assert len(ids) <= home_window.ROW_SLOTS, (
            "the %s section has %d rows and only %d fit, so %s would never "
            "be drawn" % (section, len(ids), home_window.ROW_SLOTS,
                          ids[home_window.ROW_SLOTS:]))


def test_the_section_rail_matches_the_catalog():
    """The rail's control ids run in catalog.SECTIONS order.

    The window maps a control id to a section by subtracting SECTION_BASE, so
    a button in the wrong place does not fail - it quietly switches to the
    wrong section, which is the sort of thing that gets shipped.
    """
    from katan import catalog
    from katan.ui import home_window

    with open(os.path.join(SKIN_DIR, "katan-home.xml"),
              encoding="utf-8") as handle:
        body = handle.read()

    ids = [home_window.SECTION_BASE + n
           for n in range(len(catalog.SECTIONS))]
    for n, control_id in enumerate(ids):
        assert 'id="%d"' % control_id in body, (
            "section %s has no button in the skin"
            % catalog.SECTIONS[n]["id"])
        assert 'Window.Property(katan.rail%d.title)' % n in body, (
            "section %s has a button with no label bound to it"
            % catalog.SECTIONS[n]["id"])

    # Settings is the entry after the last section, and it is on the rail
    # rather than in the top bar, which is where people look for it.
    assert 'id="%d"' % home_window.BUTTON_RAIL_SETTINGS in body, \
        "the rail has no settings entry"
    assert 'Window.Property(katan.rail%d.title)' % len(catalog.SECTIONS) in body

    # No "Home" tab. A button called Home beside Films, Series and Live TV
    # does not say what it would show, and the viewer said so.
    assert catalog.HOME not in [s["id"] for s in catalog.SECTIONS]

    # The accent bar for each section tests the property the window writes.
    for section in catalog.SECTIONS:
        assert 'katan.section),%s)' % section["id"] in body, (
            "no active marker for the %s section" % section["id"])

    # And every section has a title string that actually exists.
    po = os.path.join(LANG_DIR, "resource.language.en_gb", "strings.po")
    with open(po, encoding="utf-8") as handle:
        defined = set(re.findall(r'msgctxt "#(\d+)"', handle.read()))
    for section in catalog.SECTIONS:
        assert str(section["title_id"]) in defined, \
            "section %s names string %s, which is not defined" % (
                section["id"], section["title_id"])


def test_the_rail_is_tall_enough_for_every_entry():
    """A grouplist that is short scrolls rather than crops.

    The row area was caught by that once - sixty pixels short, and it slid
    the whole column up and took a heading off the top of the screen. The
    rail is the other vertical grouplist in this window and it was sized to
    the pixel, so the same arithmetic is checked here before it has a chance
    to go wrong. The settings entry is the last one, and it is the only way
    into the settings from an interface somebody has locked down.
    """
    from katan import catalog
    from katan.ui import home_window

    tree = ET.parse(os.path.join(SKIN_DIR, "katan-home.xml"))
    rail = None
    for control in tree.getroot().iter("control"):
        if control.get("id") == str(home_window.RAIL):
            rail = control
            break
    assert rail is not None, "the rail should have id %d" % home_window.RAIL

    def value(node, tag, default=0):
        found = node.find(tag)
        return int(found.text) if found is not None and found.text else default

    buttons = [c for c in rail.findall("control") if c.get("type") == "button"]
    expected = len(catalog.SECTIONS) + 1        # the sections, plus settings
    assert len(buttons) == expected, (
        "the rail has %d buttons but there are %d sections plus settings"
        % (len(buttons), len(catalog.SECTIONS)))

    gap = value(rail, "itemgap")
    needed = sum(value(b, "height") for b in buttons) + gap * len(buttons)
    assert value(rail, "height") >= needed, (
        "the rail is %d px tall and needs at least %d, so Kodi will refuse "
        "focus to the last entry" % (value(rail, "height"), needed))
    assert value(rail, "top") + value(rail, "height") <= 1080, \
        "the rail runs off the bottom of a 1080 screen"


def test_the_israeli_rows_are_near_the_top_of_the_home_screen():
    """The two rows this add-on exists for should not be below the fold.

    They were twelfth and thirteenth, past a ten-slot window, so they were
    never drawn at all. Keeping them inside the first handful is a deliberate
    editorial choice for an Israeli add-on, not an accident of list order, so
    it is asserted rather than left to whoever next adds a row.
    """
    from katan import catalog
    order = [row["id"] for row in catalog.rows() if row.get("default")]
    for row_id in ("israel_live", "israel_vod"):
        assert row_id in order, "%s should be on by default" % row_id
        assert order.index(row_id) < 6, (
            "%s is row %d of the default set; the Israeli rows belong near "
            "the top" % (row_id, order.index(row_id) + 1))


def test_the_tmdb_helper_player_points_at_real_routes():
    """A player file that names a dead route fails inside somebody else's skin.

    TMDb Helper builds these URLs itself, so nothing in this add-on would ever
    notice that a route had been renamed. Checking the file against the
    registered actions is the only place that mistake can be caught.
    """
    import json

    from katan import router
    from katan.ui import handlers      # noqa: F401  (registers the routes)

    path = os.path.join(ADDON_DIR, "resources", "players", "katan.json")
    assert os.path.isfile(path), "the TMDb Helper player file is missing"

    with open(path, encoding="utf-8") as handle:
        player = json.load(handle)

    assert player.get("plugin") == "plugin.video.katan"

    known = set(router.registered_actions())
    checked = 0
    for key, url in player.items():
        if not key.startswith(("play_", "search_")):
            continue
        assert url.startswith(router.BASE_URL), "%s: %s" % (key, url)
        action = re.search(r"[?&]action=([a-z_]+)", url)
        assert action, "%s has no action: %s" % (key, url)
        assert action.group(1) in known, \
            "%s points at the unknown action %r" % (key, action.group(1))
        checked += 1

    assert checked >= 4, "expected play and search entries for films and episodes"


def _value_settings(root):
    """Settings that hold a value.

    An `action` setting is a button - it runs a builtin and stores nothing -
    so asking what its default is, or who reads it, is asking the wrong
    question about the wrong kind of thing.
    """
    return [node for node in root.iter("setting")
            if node.get("id") and node.get("type") != "action"]


def test_every_settings_id_has_a_default():
    """settings.xml and settings.DEFAULTS drift apart silently otherwise."""
    from katan import settings

    root = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml")).getroot()
    declared = {s.get("id") for s in _value_settings(root)}
    missing = sorted(declared - set(settings.DEFAULTS))
    assert not missing, "settings.xml ids with no default: %s" % missing


def test_every_action_setting_runs_a_route_that_exists():
    """A button in the settings dialog is a promise like any other.

    These carry a builtin rather than a value, so the default and read-by
    checks skip them - which would leave a button naming a route that does
    not exist as the one kind of settings entry nothing checked.
    """
    from katan import router
    router._load_handlers()

    root = ET.parse(os.path.join(ADDON_DIR, "resources",
                                 "settings.xml")).getroot()
    actions = [n for n in root.iter("setting") if n.get("type") == "action"]
    assert actions, "expected the connect buttons"

    for node in actions:
        data = node.find("data")
        assert data is not None and data.text, \
            "%s is a button that does nothing" % node.get("id")
        found = re.search(r"action=(\w+)", data.text)
        assert found, "%s runs %r, which names no action" % (node.get("id"),
                                                             data.text)
        assert found.group(1) in router._ROUTES, \
            "%s runs action=%s, which is not a route" % (node.get("id"),
                                                         found.group(1))


def test_settings_xml_defaults_match_the_python_defaults():
    from katan import settings

    root = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml")).getroot()
    mismatched = []
    for node in root.iter("setting"):
        key = node.get("id")
        if key not in settings.DEFAULTS:
            continue
        default_node = node.find("default")
        xml_default = (default_node.text or "") if default_node is not None else ""
        if xml_default != settings.DEFAULTS[key]:
            mismatched.append((key, xml_default, settings.DEFAULTS[key]))
    assert not mismatched, "default drift: %s" % mismatched


def test_skin_xml_files_parse():
    files = [f for f in os.listdir(SKIN_DIR) if f.endswith(".xml")]
    assert files, "no window XML found"
    for name in files:
        ET.parse(os.path.join(SKIN_DIR, name))


def test_skin_texture_references_exist():
    """A missing texture shows as an invisible control, which is hard to debug."""
    media = os.path.join(ADDON_DIR, "resources", "media")
    pattern = re.compile(r"resources/media/([\w.-]+)")
    missing = set()
    for name in os.listdir(SKIN_DIR):
        if not name.endswith(".xml"):
            continue
        text = open(os.path.join(SKIN_DIR, name), encoding="utf-8").read()
        for asset in pattern.findall(text):
            if not os.path.isfile(os.path.join(media, asset)):
                missing.add(asset)
    assert not missing, "textures referenced but not present: %s" % sorted(missing)


@pytest.mark.parametrize("language", ["resource.language.en_gb", "resource.language.he_il"])
def test_string_files_are_well_formed(language):
    path = os.path.join(LANG_DIR, language, "strings.po")
    text = open(path, encoding="utf-8").read()
    ids = re.findall(r'msgctxt "#(\d+)"', text)
    assert ids, "no strings in %s" % language
    assert len(ids) == len(set(ids)), "duplicate ids in %s" % language
    # Kodi reserves 30000-30999 for an add-on's settings labels and
    # 32000-32999 for the strings its own code asks for by number. Both are
    # used here, and nothing should fall outside them.
    outside = [i for i in ids
               if not (30000 <= int(i) <= 30999 or 32000 <= int(i) <= 32999)]
    assert not outside, "ids outside the add-on ranges: %s" % sorted(outside)[:5]


def test_every_localize_call_has_a_string():
    """kodi.localize(32xxx) with no matching entry renders as a bare number."""
    text = open(os.path.join(LANG_DIR, "resource.language.en_gb", "strings.po"),
                encoding="utf-8").read()
    known = set(re.findall(r'msgctxt "#(\d+)"', text))

    used = set()
    lib = os.path.join(ADDON_DIR, "resources", "lib")
    for folder, _, files in os.walk(lib):
        for name in files:
            if not name.endswith(".py"):
                continue
            source = open(os.path.join(folder, name), encoding="utf-8").read()
            used.update(re.findall(r"localize\((\d{5})", source))

    missing = sorted(used - known)
    assert not missing, "localize() ids with no string: %s" % missing


def test_every_provider_setting_has_a_module_behind_it():
    """A switch the user can turn on must do something when they do.

    Settings that promise a provider with no code behind them are worse than
    an absent feature: the user enables them, nothing changes, and there is no
    way to tell whether the provider is broken or imaginary.
    """
    from katan import settings

    lib = os.path.join(ADDON_DIR, "resources", "lib", "katan")
    missing = []

    for key in settings.DEFAULTS:
        if key.startswith("sources.provider."):
            name = key.rsplit(".", 1)[-1]
            path = os.path.join(lib, "sources", "providers", "%s.py" % name)
        elif key.startswith("subs.provider."):
            name = key.rsplit(".", 1)[-1]
            path = os.path.join(lib, "subs", "providers", "%s.py" % name)
        else:
            continue
        if not os.path.isfile(path):
            missing.append(key)

    assert not missing, "settings with no provider module: %s" % missing


def test_every_route_referenced_in_the_ui_exists():
    """A url_for() to a route that was never registered is a dead button."""
    import re

    from katan import router
    router._load_handlers()
    known = set(router.registered_actions())

    lib = os.path.join(ADDON_DIR, "resources", "lib", "katan")
    referenced = set()
    for folder, dirs, files in os.walk(lib):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            source = open(os.path.join(folder, name), encoding="utf-8").read()
            referenced.update(re.findall(r'url_for\(\s*"([a-z_]+)"', source))

    missing = sorted(referenced - known)
    assert not missing, "routes used but never registered: %s" % missing


def test_every_window_python_module_has_its_skin_file():
    """A WindowXML class without its XML fails only when opened."""
    import re

    lib = os.path.join(ADDON_DIR, "resources", "lib", "katan", "ui")
    missing = []
    for name in os.listdir(lib):
        if not name.endswith("_window.py"):
            continue
        source = open(os.path.join(lib, name), encoding="utf-8").read()
        for xml_name in re.findall(r'"(katan-[a-z]+\.xml)"', source):
            if not os.path.isfile(os.path.join(SKIN_DIR, xml_name)):
                missing.append("%s wants %s" % (name, xml_name))
    assert not missing, missing


# Kodi calls these itself, or they are entry points rather than helpers.
_CALLED_BY_KODI = {
    "onInit", "onClick", "onAction", "onFocus", "onSettingsChanged",
    "onPlayBackStarted", "onPlayBackStopped", "onPlayBackEnded",
    "onPlayBackPaused", "onPlayBackResumed", "onPlayBackSeek",
    "onAVStarted", "onNotification", "run", "dispatch", "main",
}


def test_no_public_function_is_defined_and_never_called():
    """A function nothing calls is dead weight, or a check somebody meant to
    make and did not.

    Three defects tonight were exactly this shape, and the third is the reason
    for the test rather than a tidy-up:

      registry.forget_cache_status  existed, and a torrent that became cached
                                    still read as uncached for an hour
      sources_window.quick_pick     a fallback picker nothing could reach
      upnext.installed              said the right thing and was never asked,
                                    so every episode spent two TMDB requests
                                    on a card that was never going to be drawn

    The rule this defends is the project's own: nothing here is a stub
    pretending to work.
    """
    import ast
    import io
    import re

    defined = {}
    for folder, dirs, files in os.walk(PACKAGE_ROOT):
        if "__pycache__" in folder:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with io.open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            # Module level only. A method can be an override a framework
            # calls by name - urlsession's redirect_request is urllib's own
            # API - and counting mentions cannot tell that apart from dead
            # code.
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name.startswith("_"):
                    continue
                # A route handler is called through the decorator's registry,
                # never by name. test_addon_integrity already checks that
                # every registered action has a handler and that every
                # url_for names a real route, so they are covered elsewhere.
                if any("route" in ast.dump(d) for d in node.decorator_list):
                    continue
                # Kodi's own callbacks: onInit, onPlayBackError and friends.
                if re.match(r"^on[A-Z]", node.name):
                    continue
                defined.setdefault(node.name, []).append(
                    "%s:%d" % (os.path.relpath(path, PACKAGE_ROOT),
                               node.lineno))

    # Count every identifier in one pass rather than searching the whole tree
    # once per name. Four hundred regex scans over a few megabytes took six
    # seconds, which is a third of the suite for one check.
    import collections

    mentions = collections.Counter()
    word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for base in (ADDON_DIR, TESTS_DIR, os.path.join(ROOT, "tools")):
        for folder, dirs, files in os.walk(base):
            if "__pycache__" in folder or ".kodi-test" in folder:
                continue
            for name in files:
                if not name.endswith((".py", ".xml")):
                    continue
                with io.open(os.path.join(folder, name), encoding="utf-8",
                             errors="replace") as handle:
                    mentions.update(word.findall(handle.read()))

    orphans = []
    for name, places in sorted(defined.items()):
        if name in _CALLED_BY_KODI:
            continue
        # The definition itself is one mention; more means somebody calls it.
        if mentions[name] <= len(places):
            orphans.append("%s (%s)" % (name, ", ".join(places)))

    assert not orphans, (
        "defined and referenced nowhere - wire it up or delete it:\n  "
        + "\n  ".join(orphans))


# Read through a prefix loop rather than by name.
_PREFIX_READ = ("sources.provider.", "subs.provider.")


def test_every_declared_setting_is_read_by_something():
    """The mirror of the check that every setting the code uses is declared.

    A setting nobody reads is a switch that does nothing while sitting in the
    dialog looking like a feature. Two were found this way: "Verbose logging",
    which was never consulted at all, and "Metadata cache (hours)", whose
    default of 6 sat next to a hard-coded constant of exactly six hours.
    """
    import io

    root = ET.parse(os.path.join(ADDON_DIR, "resources",
                                 "settings.xml")).getroot()
    declared = [node.get("id") for node in _value_settings(root)]
    assert declared, "expected some settings"

    source = []
    for folder, dirs, files in os.walk(PACKAGE_ROOT):
        if "__pycache__" in folder:
            continue
        for name in files:
            if name.endswith(".py"):
                with io.open(os.path.join(folder, name), encoding="utf-8",
                             errors="replace") as handle:
                    source.append(handle.read())
    blob = "\n".join(source)

    unread = []
    for key in declared:
        if key.startswith(_PREFIX_READ):
            continue
        # One mention is the DEFAULTS table; a second means somebody reads it.
        if len(re.findall(re.escape('"%s"' % key), blob)) <= 1:
            unread.append(key)

    assert not unread, (
        "declared and read by nothing - wire it up or remove it:\n  "
        + "\n  ".join(unread))


def test_every_debrid_service_has_its_own_connect_button():
    """One generic "connect a debrid service" made the viewer pick twice, and
    hid the fact that the four services sign in differently - Real-Debrid has
    no key to paste, TorBox has no link to open."""
    import xml.etree.ElementTree as ET

    from katan.debrid import registry

    root = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml")).getroot()
    actions = {node.get("id") for node in root.iter("setting")
               if (node.get("id") or "").startswith("action.connect.")}

    missing = ["action.connect.%s" % name for name in registry.names()
               if "action.connect.%s" % name not in actions]
    assert not missing, missing


def test_each_connect_button_names_a_service_that_exists():
    """A button wired to a typo is a button that does nothing."""
    import re
    import xml.etree.ElementTree as ET

    from katan.debrid import registry
    from katan import router
    router._load_handlers()

    known = set(registry.names()) | {"trakt", "tmdb", "debrid", "opensubtitles",
                                     "mdblist", "gemini", "ktuvit"}
    root = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml")).getroot()

    unknown = []
    for node in root.iter("setting"):
        data = node.find("data")
        if data is None or not data.text:
            continue
        found = re.search(r"service=([a-z]+)", data.text)
        if found and found.group(1) not in known:
            unknown.append((node.get("id"), found.group(1)))
    assert not unknown, unknown

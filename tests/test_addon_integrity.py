"""Checks that catch the mistakes that are invisible until Kodi loads the add-on."""
import os
import re
import xml.etree.ElementTree as ET

import pytest

from conftest import ADDON_DIR

LANG_DIR = os.path.join(ADDON_DIR, "resources", "language")
SKIN_DIR = os.path.join(ADDON_DIR, "resources", "skins", "default", "1080i")


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


def test_every_settings_id_has_a_default():
    """settings.xml and settings.DEFAULTS drift apart silently otherwise."""
    from katan import settings

    root = ET.parse(os.path.join(ADDON_DIR, "resources", "settings.xml")).getroot()
    declared = {s.get("id") for s in root.iter("setting") if s.get("id")}
    missing = sorted(declared - set(settings.DEFAULTS))
    assert not missing, "settings.xml ids with no default: %s" % missing


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
    assert all(32000 <= int(i) <= 32999 for i in ids), "ids outside the add-on range"


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

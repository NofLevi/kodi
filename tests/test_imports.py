"""Import every module in the add-on.

Kodi only surfaces an import error at runtime, usually as a blank screen, so
importing everything here is the cheapest bug-catcher in the suite.
"""
import importlib
import os
import pkgutil

import pytest

from conftest import LIB_DIR

PACKAGE_ROOT = os.path.join(LIB_DIR, "katan")


def _module_names():
    names = ["katan"]
    for finder, name, is_package in pkgutil.walk_packages([PACKAGE_ROOT], "katan."):
        names.append(name)
    return sorted(names)


@pytest.mark.parametrize("module_name", _module_names())
def test_module_imports(module_name):
    importlib.import_module(module_name)


def test_every_python_file_is_reachable_as_a_module():
    """A .py file in a folder without __init__.py is dead code Kodi cannot load."""
    orphans = []
    for folder, dirs, files in os.walk(PACKAGE_ROOT):
        if "__pycache__" in folder:
            continue
        has_python = any(f.endswith(".py") and f != "__init__.py" for f in files)
        if has_python and "__init__.py" not in files:
            orphans.append(os.path.relpath(folder, PACKAGE_ROOT))
    assert not orphans, "packages missing __init__.py: %s" % orphans


def test_no_source_file_has_syntax_warnings():
    """Invalid escape sequences are warnings today and errors tomorrow.

    They also indicate a regex that silently stopped meaning what it says, so
    the whole package is compiled with warnings promoted to errors.
    """
    import io
    import warnings

    problems = []
    for folder, dirs, files in os.walk(PACKAGE_ROOT):
        if "__pycache__" in folder:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with io.open(path, encoding="utf-8") as handle:
                source = handle.read()
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    compile(source, path, "exec")
                except (SyntaxWarning, DeprecationWarning, SyntaxError) as error:
                    problems.append("%s: %s" % (os.path.relpath(path, PACKAGE_ROOT),
                                                error))
    assert not problems, "\n".join(problems)


def test_no_stray_control_characters_in_source():
    """A patch gone wrong can leave an invisible control character behind."""
    import io

    problems = []
    for folder, dirs, files in os.walk(PACKAGE_ROOT):
        if "__pycache__" in folder:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with io.open(path, encoding="utf-8") as handle:
                text = handle.read()
            for code in (8, 12, 13, 27):
                if chr(code) in text:
                    problems.append("%s contains chr(%d)"
                                    % (os.path.relpath(path, PACKAGE_ROOT), code))
    assert not problems, "\n".join(problems)

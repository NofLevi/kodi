"""Katan - a lightweight Netflix-style streaming add-on for Kodi.

The version lives in addon.xml and nowhere else. There used to be a
__version__ here as well; nothing read it, release.py did not bump it,
and it sat at 0.1.0 saying so - a second source of truth that could
only ever be wrong. `kodi.addon_version()` asks Kodi.
"""

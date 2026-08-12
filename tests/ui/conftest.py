"""Headless Qt setup: must run before any Qt module is imported."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

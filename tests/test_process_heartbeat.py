"""Tests for process heartbeat helpers."""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from process_heartbeat import (  # noqa: E402
    is_heartbeat_stale,
    write_heartbeat,
)
from platform_runtime import app_support_dir  # noqa: E402


class ProcessHeartbeatTests(unittest.TestCase):
    def setUp(self):
        os.environ["WIN_REC_APP_DATA_DIR"] = os.path.join(
            app_support_dir(), "heartbeat_test"
        )
        os.makedirs(os.environ["WIN_REC_APP_DATA_DIR"], exist_ok=True)

    def test_stale_after_age(self):
        write_heartbeat(session_id="t")
        time.sleep(0.05)
        self.assertFalse(is_heartbeat_stale(max_age=60.0))


class DesktopProbeFactoryTests(unittest.TestCase):
    def test_default_probe_is_uia_unless_disabled(self):
        from platform_runtime import is_windows

        if not is_windows():
            self.skipTest("Windows-only probe factory")
        os.environ.pop("WINREC_DISABLE_UIA", None)
        from windows_desktop_call_uia_probe import create_desktop_probe

        probe = create_desktop_probe()
        self.assertEqual(probe.__class__.__name__, "WindowsDesktopCallUiaProbe")

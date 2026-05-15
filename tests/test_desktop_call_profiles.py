"""Tests for desktop call app profiles."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from desktop_call_profiles import (  # noqa: E402
    DESKTOP_CALL_PROFILES,
    PROFILE_BY_APP_ID,
    TEAMS_PROFILE,
    resolve_logical_app_id,
)


class DesktopCallProfilesTests(unittest.TestCase):
    def test_seven_profiles_registered(self):
        self.assertEqual(len(DESKTOP_CALL_PROFILES), 7)
        self.assertIn("teams", PROFILE_BY_APP_ID)
        self.assertIn("google_meet_pwa", PROFILE_BY_APP_ID)

    def test_teams_root_processes(self):
        self.assertIn("ms-teams.exe", TEAMS_PROFILE.root_processes)
        self.assertIn("msteams.exe", TEAMS_PROFILE.root_processes)
        self.assertIn("msedgewebview2.exe", TEAMS_PROFILE.child_processes)

    def test_resolve_teams_root(self):
        self.assertEqual(resolve_logical_app_id(1, "ms-teams.exe"), "teams")

    def test_webview2_without_psutil_returns_none(self):
        # pid 0 should not resolve without real parent chain
        self.assertIsNone(resolve_logical_app_id(0, "msedgewebview2.exe"))


if __name__ == "__main__":
    unittest.main()

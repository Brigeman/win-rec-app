"""Tests for generic UIA scoring (no uiautomation import)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from desktop_call_profiles import (  # noqa: E402
    DISCORD_PROFILE,
    SLACK_PROFILE,
    TEAMS_PROFILE,
    ZOOM_PROFILE,
    score_uia_text,
)


class DesktopUiaScoringTests(unittest.TestCase):
    def test_teams_leave_mute_high_score(self):
        text = "leave mute unmute share participants raise hand camera"
        score, matched = score_uia_text(TEAMS_PROFILE, text, False, False)
        self.assertGreaterEqual(score, 70)
        self.assertTrue(any("strong:leave" in m for m in matched))

    def test_teams_join_only_capped(self):
        text = "join meeting присоединиться chat"
        score, _ = score_uia_text(TEAMS_PROFILE, text, False, False)
        self.assertLessEqual(score, 40)

    def test_zoom_leave_meeting(self):
        text = "leave meeting mute unmute share screen participants"
        score, _ = score_uia_text(ZOOM_PROFILE, text, True, False)
        self.assertGreaterEqual(score, 70)

    def test_discord_disconnect_voice(self):
        text = "disconnect voice connected mute undeafen"
        score, _ = score_uia_text(DISCORD_PROFILE, text, False, True)
        self.assertGreaterEqual(score, 70)

    def test_slack_start_huddle_only_capped(self):
        text = "присоединиться"
        score, matched = score_uia_text(SLACK_PROFILE, text, False, False)
        self.assertLessEqual(score, 40)
        self.assertIn("pre_call_only_cap", matched)

    def test_audio_bonuses(self):
        text = "camera chat"
        base, _ = score_uia_text(TEAMS_PROFILE, text, False, False)
        boosted, matched = score_uia_text(TEAMS_PROFILE, text, True, True)
        self.assertGreater(boosted, base)
        self.assertIn("loopback_active", matched)
        self.assertIn("capture_active", matched)


if __name__ == "__main__":
    unittest.main()

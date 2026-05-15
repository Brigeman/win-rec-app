import unittest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from meeting_detection import AudioActivity, LegacyMeetingDetector
from presence_probe import ForegroundWindowInfo, PresenceSnapshot


class StubPresenceProbe:
    def __init__(self, process_name: str, title: str, running: set[str]):
        self._snapshot = PresenceSnapshot(
            running_processes=running,
            foreground=ForegroundWindowInfo(process_name=process_name, title=title),
        )

    def snapshot(self) -> PresenceSnapshot:
        return self._snapshot


class StubAudioProbe:
    def __init__(self, activity: AudioActivity):
        self._activity = activity

    def start(self):
        return

    def stop(self):
        return

    def get_activity(self) -> AudioActivity:
        return self._activity


class DetectionCoreTests(unittest.TestCase):
    def test_google_meet_browser_context_prompts_instantly(self):
        detector = LegacyMeetingDetector(
            presence_probe=StubPresenceProbe(
                process_name="chrome.exe",
                title="Meet - abc-defg-hij - Google Chrome",
                running={"chrome.exe"},
            ),
        )
        detector.audio_probe = StubAudioProbe(AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0))

        decision = detector.evaluate(is_recording=False, mic_rms=0.0)

        self.assertTrue(decision.should_prompt_start)
        self.assertEqual(decision.reason, "instant_context")

    def test_context_cooldown_blocks_only_same_context(self):
        detector = LegacyMeetingDetector(
            presence_probe=StubPresenceProbe(
                process_name="chrome.exe",
                title="Meet - abc-defg-hij - Google Chrome",
                running={"chrome.exe"},
            ),
        )
        detector.audio_probe = StubAudioProbe(AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0))
        first = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertTrue(first.should_prompt_start)

        detector.set_cooldown_dismiss()
        blocked = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertFalse(blocked.should_prompt_start)
        self.assertEqual(blocked.reason, "cooldown_active")

        detector.presence_probe = StubPresenceProbe(
            process_name="msedge.exe",
            title="Join from Zoom Workplace app - Zoom",
            running={"msedge.exe"},
        )
        unblocked = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertIn(unblocked.reason, {"instant_context", "threshold_met", "threshold_not_met"})
        self.assertNotEqual(unblocked.reason, "cooldown_active")

    def test_search_results_page_does_not_trigger(self):
        """Typing 'zoom meeting' into Google must not produce a prompt.

        The suppress_title_patterns path short-circuits all scoring so
        even strong/instant rules cannot fire on search engine pages.
        """
        for title in [
            "zoom meeting - Google Search",
            "zoom meeting - Google \u041f\u043e\u0438\u0441\u043a",
            "google meet \u2014 \u041f\u043e\u0438\u0441\u043a Google",
            "join a meeting \u2014 \u042f\u043d\u0434\u0435\u043a\u0441",
            "New Tab",
            "chrome://newtab",
        ]:
            with self.subTest(title=title):
                detector = LegacyMeetingDetector(
                    presence_probe=StubPresenceProbe(
                        process_name="chrome.exe",
                        title=title,
                        running={"chrome.exe", "ms-teams.exe"},
                    ),
                )
                detector.audio_probe = StubAudioProbe(
                    AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0)
                )
                decision = detector.evaluate(is_recording=False, mic_rms=0.0)
                self.assertFalse(
                    decision.should_prompt_start,
                    f"Search/new-tab title leaked through: {title!r}",
                )
                if decision.reason != "cooldown_active":
                    self.assertEqual(decision.reason, "suppress_title")

    def test_same_context_promptable_again_after_cooldown_expires(self):
        detector = LegacyMeetingDetector(
            presence_probe=StubPresenceProbe(
                process_name="chrome.exe",
                title="Meet - abc-defg-hij - Google Chrome",
                running={"chrome.exe"},
            ),
        )
        detector.audio_probe = StubAudioProbe(AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0))

        first = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertTrue(first.should_prompt_start)
        same_context = first.context_key

        detector.set_cooldown_dismiss()
        blocked = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertEqual(blocked.reason, "cooldown_active")

        # Simulate the cooldown elapsing for this exact context.
        detector.context_cooldown_until[same_context] = 0.0

        revived = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertTrue(
            revived.should_prompt_start,
            f"Expected same context to be promptable again after cooldown expiry, got {revived}",
        )
        self.assertEqual(revived.context_key, same_context)
        self.assertNotEqual(revived.reason, "cooldown_active")

    def test_teams_desktop_active_call_title_instant_prompt(self):
        detector = LegacyMeetingDetector(
            presence_probe=StubPresenceProbe(
                process_name="ms-teams.exe",
                title="Call with Alex | Microsoft Teams",
                running={"ms-teams.exe"},
            ),
        )
        detector.audio_probe = StubAudioProbe(
            AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0)
        )
        decision = detector.evaluate(is_recording=False, mic_rms=0.0)
        self.assertTrue(decision.should_prompt_start)
        self.assertEqual(decision.reason, "instant_context")


if __name__ == "__main__":
    unittest.main()

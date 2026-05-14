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


if __name__ == "__main__":
    unittest.main()

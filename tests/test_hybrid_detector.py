"""Direct-runnable unittest coverage for HybridDetector.

Run with:
    WIN_REC_APP_DATA_DIR=/tmp/win-rec-app-test \
    PYTHONPATH=. .venv/bin/python tests/test_hybrid_detector.py -v
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from call_probe import CallSessionSnapshot  # noqa: E402
import meeting_detection as md_mod  # noqa: E402
from hybrid_detector import HybridDetector  # noqa: E402
from meeting_detection import (  # noqa: E402
    AudioActivity,
    DetectionDecision,
    LegacyMeetingDetector,
    LoopbackAudioProbe,
)
from presence_probe import ForegroundWindowInfo, PresenceSnapshot  # noqa: E402
from universal_call_detector import UniversalCallDetector  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Stub:
    """Minimal stub implementing the detector surface HybridDetector needs."""

    def __init__(self, decision: DetectionDecision):
        self.next_decision = decision
        self.eval_calls: list[tuple[bool, float]] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.dismiss_calls = 0
        self.post_stop_calls = 0
        self.log_calls = 0

    def evaluate(self, is_recording: bool, mic_rms: float = 0.0) -> DetectionDecision:
        self.eval_calls.append((is_recording, mic_rms))
        return self.next_decision

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def set_cooldown_dismiss(self) -> None:
        self.dismiss_calls += 1

    def set_cooldown_post_stop(self) -> None:
        self.post_stop_calls += 1

    def should_log_decision(self, decision: DetectionDecision) -> bool:
        self.log_calls += 1
        return True


class StubPresenceProbe:
    def __init__(self, process_name: str = "", title: str = "", running=None):
        self._snapshot = PresenceSnapshot(
            running_processes=set(running or set()),
            foreground=ForegroundWindowInfo(process_name=process_name, title=title),
        )

    def snapshot(self) -> PresenceSnapshot:
        return self._snapshot


class StubCallProbeUnavailable:
    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def snapshot(self) -> CallSessionSnapshot:
        return CallSessionSnapshot(
            active_call_pids={}, self_pids=set(), timestamp=time.time(), available=False
        )


class StubLoopbackProbe:
    def __init__(self, activity: AudioActivity | None = None):
        self._activity = activity or AudioActivity()

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def get_activity(self) -> AudioActivity:
        return self._activity


class FakeAudioBackend:
    """Returns no loopback device so the real probe's run-loop is idle."""

    def get_default_loopback(self):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(reason: str, prompt_start=False, prompt_stop=False, auto_stop=False, **kw) -> DetectionDecision:
    return DetectionDecision(
        should_prompt_start=prompt_start,
        score=kw.pop("score", 0),
        reason=reason,
        should_prompt_stop=prompt_stop,
        auto_stop=auto_stop,
        **kw,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class HybridDetectorTests(unittest.TestCase):
    def test_universal_prompt_start_short_circuits_legacy(self):
        universal = _Stub(_ok("call_sustained", prompt_start=True, call_pid=4242))
        legacy = _Stub(_ok("threshold_not_met"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        decision = hybrid.evaluate(is_recording=False)

        self.assertTrue(decision.should_prompt_start)
        self.assertEqual(decision.reason, "call_sustained")
        self.assertEqual(decision.call_pid, 4242)
        self.assertEqual(len(universal.eval_calls), 1)
        self.assertEqual(legacy.eval_calls, [], "legacy must not run after universal wins")

    def test_universal_probe_unavailable_falls_through_to_legacy(self):
        universal = _Stub(_ok("probe_unavailable"))
        legacy = _Stub(_ok("instant_context", prompt_start=True, context_key="chrome.exe|meet"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        with self.assertLogs("quick_audio_recorder", level="INFO") as captured:
            decision = hybrid.evaluate(is_recording=False)

        self.assertTrue(decision.should_prompt_start)
        self.assertEqual(decision.reason, "instant_context")
        self.assertEqual(decision.context_key, "chrome.exe|meet")
        self.assertEqual(len(legacy.eval_calls), 1)
        self.assertTrue(
            any("hybrid_path | active=legacy" in line for line in captured.output),
            f"expected hybrid_path active=legacy transition log, got {captured.output}",
        )

    def test_universal_idle_with_legacy_idle_returns_universal_reason(self):
        universal = _Stub(_ok("no_call_pid"))
        legacy = _Stub(_ok("threshold_not_met"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        decision = hybrid.evaluate(is_recording=False)

        self.assertFalse(decision.should_prompt_start)
        self.assertEqual(decision.reason, "no_call_pid")
        self.assertEqual(len(universal.eval_calls), 1)
        self.assertEqual(len(legacy.eval_calls), 1)

    def test_during_recording_universal_stop_propagates_without_legacy(self):
        universal = _Stub(
            _ok("call_ended", prompt_stop=True, call_pid=4242, call_process_name="ms-teams.exe")
        )
        legacy = _Stub(_ok("threshold_not_met"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        decision = hybrid.evaluate(is_recording=True)

        self.assertTrue(decision.should_prompt_stop)
        self.assertEqual(decision.reason, "call_ended")
        self.assertEqual(decision.call_pid, 4242)
        self.assertEqual(legacy.eval_calls, [], "legacy must not run while recording")

    def test_cooldown_forwarders_invoke_both_detectors(self):
        universal = _Stub(_ok("no_call_pid"))
        legacy = _Stub(_ok("threshold_not_met"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        hybrid.set_cooldown_dismiss()
        hybrid.set_cooldown_post_stop()

        self.assertEqual(universal.dismiss_calls, 1)
        self.assertEqual(legacy.dismiss_calls, 1)
        self.assertEqual(universal.post_stop_calls, 1)
        self.assertEqual(legacy.post_stop_calls, 1)

    def test_start_stop_forwarders_invoke_both_detectors(self):
        universal = _Stub(_ok("no_call_pid"))
        legacy = _Stub(_ok("threshold_not_met"))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        hybrid.start()
        hybrid.stop()

        self.assertEqual(universal.start_calls, 1)
        self.assertEqual(legacy.start_calls, 1)
        self.assertEqual(universal.stop_calls, 1)
        self.assertEqual(legacy.stop_calls, 1)

    def test_should_log_decision_delegates_to_active_backend(self):
        universal = _Stub(_ok("probe_unavailable"))
        legacy = _Stub(_ok("instant_context", prompt_start=True))
        hybrid = HybridDetector(universal=universal, legacy=legacy)

        legacy_decision = hybrid.evaluate(is_recording=False)
        hybrid.should_log_decision(legacy_decision)
        self.assertEqual(legacy.log_calls, 1)
        self.assertEqual(universal.log_calls, 0)

        universal.next_decision = _ok("no_call_pid")
        legacy.next_decision = _ok("threshold_not_met")
        universal_idle_decision = hybrid.evaluate(is_recording=False)
        hybrid.should_log_decision(universal_idle_decision)
        self.assertEqual(universal.log_calls, 1)

    def test_shared_loopback_probe_is_only_started_once(self):
        # Force the threaded code path so the macOS short-circuit
        # doesn't hide the dedup guard we want to verify.
        original_is_macos = md_mod.is_macos
        md_mod.is_macos = lambda: False
        try:
            shared = LoopbackAudioProbe(audio_backend=FakeAudioBackend())
            universal = UniversalCallDetector(
                call_probe=StubCallProbeUnavailable(),
                loopback_probe=shared,
            )
            legacy = LegacyMeetingDetector(
                presence_probe=StubPresenceProbe(),
                loopback_probe=shared,
            )
            # Sanity: both detectors observe the same probe instance.
            self.assertIs(universal.audio_probe, shared)
            self.assertIs(legacy.audio_probe, shared)

            hybrid = HybridDetector(universal=universal, legacy=legacy)
            self.assertIsNone(shared._thread)
            hybrid.start()
            try:
                first_thread = shared._thread
                self.assertIsNotNone(first_thread, "expected a probe thread after start")
                self.assertTrue(first_thread.is_alive())
                # The second start() (via legacy) must NOT spawn a new thread.
                self.assertIs(shared._thread, first_thread)
            finally:
                hybrid.stop()
        finally:
            md_mod.is_macos = original_is_macos


if __name__ == "__main__":
    unittest.main()

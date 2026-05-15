"""Direct-runnable unittest coverage for UniversalCallDetector.

Run with:
    WIN_REC_APP_DATA_DIR=/tmp/win-rec-app-test \
    PYTHONPATH=. .venv/bin/python tests/test_universal_detector.py -v
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from call_probe import CallPidState, CallSessionSnapshot  # noqa: E402
from detection_rules import DEFAULT_UNIVERSAL_RULES, UniversalCallRules  # noqa: E402
from meeting_detection import AudioActivity  # noqa: E402
from universal_call_detector import UniversalCallDetector  # noqa: E402
from windows_desktop_call_uia_probe import DesktopCallPresence, NullDesktopCallUiaProbe  # noqa: E402


class StubCallProbe:
    """Replay a fixed sequence of CallSessionSnapshot for each call.

    If the sequence is exhausted, the last snapshot is repeated. Use
    ``set_snapshot`` to swap at any time.
    """

    def __init__(self, snapshot: CallSessionSnapshot):
        self._snapshot = snapshot

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def snapshot(self) -> CallSessionSnapshot:
        return self._snapshot

    def set_snapshot(self, snapshot: CallSessionSnapshot) -> None:
        self._snapshot = snapshot


class StubLoopbackProbe:
    def __init__(self, activity: AudioActivity):
        self._activity = activity

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def get_activity(self) -> AudioActivity:
        return self._activity

    def set_activity(self, activity: AudioActivity) -> None:
        self._activity = activity


def _empty_snapshot(self_pids=None, available=True) -> CallSessionSnapshot:
    return CallSessionSnapshot(
        active_call_pids={},
        self_pids=set(self_pids or set()),
        timestamp=time.time(),
        available=available,
    )


def _snapshot_with(pid_states, self_pids=None) -> CallSessionSnapshot:
    return CallSessionSnapshot(
        active_call_pids={s.pid: s for s in pid_states},
        self_pids=set(self_pids or set()),
        timestamp=time.time(),
        available=True,
    )


def _quiet_audio() -> AudioActivity:
    return AudioActivity(rms=0.0, peak=0.0, sustained_seconds=0.0)


class StubDesktopUiaProbe:
    def __init__(self, presence: DesktopCallPresence | None = None):
        self._presence = presence or DesktopCallPresence(active=False)

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def snapshot(self) -> DesktopCallPresence:
        return self._presence

    def set_presence(self, presence: DesktopCallPresence) -> None:
        self._presence = presence


def _build_detector(probe, loopback=None, rules=None, uia_probe=None):
    rules = rules or DEFAULT_UNIVERSAL_RULES
    detector = UniversalCallDetector(
        call_probe=probe,
        universal_rules=rules,
        loopback_probe=loopback or StubLoopbackProbe(_quiet_audio()),
        desktop_uia_probe=uia_probe if uia_probe is not None else NullDesktopCallUiaProbe(),
    )
    return detector


class UniversalDetectorTests(unittest.TestCase):
    def test_no_capture_no_render_no_prompt(self):
        probe = StubCallProbe(_empty_snapshot())
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)
        self.assertFalse(decision.should_prompt_stop)
        self.assertEqual(decision.reason, "no_call_signal")

    def test_probe_unavailable_returns_probe_unavailable(self):
        probe = StubCallProbe(_empty_snapshot(available=False))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertEqual(decision.reason, "probe_unavailable")
        self.assertFalse(decision.should_prompt_start)

    def test_sustained_call_prompts_after_threshold(self):
        # Simulate a Teams-like PID with render + capture active.
        old_since = time.time() - 10.0
        state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=old_since,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertTrue(decision.should_prompt_start, decision)
        self.assertEqual(decision.call_pid, 4242)
        self.assertEqual(decision.call_process_name, "ms-teams.exe")
        self.assertIn("universal_call", decision.matched_rules)

    def test_short_lived_pid_does_not_prompt_yet(self):
        state = CallPidState(
            pid=9001,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time(),  # just appeared
        )
        probe = StubCallProbe(_snapshot_with([state]))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)
        self.assertEqual(decision.reason, "awaiting_sustain")

    def test_same_pid_does_not_re_prompt(self):
        state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        detector = _build_detector(probe)
        first = detector.evaluate(is_recording=False)
        self.assertTrue(first.should_prompt_start)
        second = detector.evaluate(is_recording=False)
        self.assertFalse(second.should_prompt_start)
        self.assertEqual(second.reason, "already_prompted")

    def test_self_pid_alone_does_not_prompt(self):
        my_pid = os.getpid()
        state = CallPidState(
            pid=my_pid,
            process_name="python.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state], self_pids={my_pid}))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)
        self.assertEqual(decision.reason, "no_call_signal")

    def test_negative_service_pid_does_not_prompt(self):
        state = CallPidState(
            pid=1010,
            process_name="searchhost.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.05,
            capture_peak=0.04,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)
        self.assertEqual(decision.reason, "no_call_signal")

    def test_low_capture_peak_does_not_prompt(self):
        state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.0,  # below min_capture_peak
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        detector = _build_detector(probe)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)
        self.assertEqual(decision.reason, "no_call_signal")

    def test_pid_disappearance_during_recording_prompts_stop(self):
        # First tick: call is active and sustained.
        active_state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([active_state]))
        detector = _build_detector(probe)
        # Establish state outside of recording first.
        first = detector.evaluate(is_recording=False)
        self.assertTrue(first.should_prompt_start)

        # Now the PID vanishes and we're recording.
        probe.set_snapshot(_empty_snapshot())
        # Backdate call_last_seen_ts to clearly exceed end-sustain.
        detector.call_last_seen_ts = (
            time.time() - DEFAULT_UNIVERSAL_RULES.call_end_sustain_seconds - 5.0
        )
        decision = detector.evaluate(is_recording=True)
        self.assertTrue(decision.should_prompt_stop)
        self.assertEqual(decision.reason, "call_ended")
        self.assertEqual(decision.call_pid, 4242)

    def test_pid_disappearance_within_sustain_does_not_prompt_stop(self):
        active_state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([active_state]))
        detector = _build_detector(probe)
        first = detector.evaluate(is_recording=False)
        self.assertTrue(first.should_prompt_start)

        # The PID drops, but only a moment ago.
        probe.set_snapshot(_empty_snapshot())
        detector.call_last_seen_ts = time.time()
        decision = detector.evaluate(is_recording=True)
        self.assertFalse(decision.should_prompt_stop)
        self.assertIn(decision.reason, {"recording_call_dropping"})

    def test_split_pid_listener_relax(self):
        # Capture-only on PID A, render-only on PID B, loopback sustained.
        capture_state = CallPidState(
            pid=7001,
            process_name="discord.exe",
            render_active=False,
            capture_active=True,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        render_state = CallPidState(
            pid=7002,
            process_name="discord.exe",
            render_active=True,
            capture_active=False,
            render_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([capture_state, render_state]))
        loopback = StubLoopbackProbe(
            AudioActivity(rms=0.05, peak=0.2, sustained_seconds=15.0)
        )
        detector = _build_detector(probe, loopback=loopback)
        decision = detector.evaluate(is_recording=False)
        self.assertTrue(decision.should_prompt_start, decision)
        self.assertEqual(decision.call_pid, 7001)
        self.assertTrue(
            "universal_split_pid_call" in decision.matched_rules
            or "known_app_capture_plus_loopback" in decision.matched_rules,
            decision.matched_rules,
        )

    def test_split_pid_without_sustained_loopback_does_not_prompt(self):
        capture_state = CallPidState(
            pid=7001,
            process_name="discord.exe",
            render_active=False,
            capture_active=True,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        render_state = CallPidState(
            pid=7002,
            process_name="discord.exe",
            render_active=True,
            capture_active=False,
            render_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([capture_state, render_state]))
        loopback = StubLoopbackProbe(_quiet_audio())
        detector = _build_detector(probe, loopback=loopback)
        decision = detector.evaluate(is_recording=False)
        self.assertFalse(decision.should_prompt_start)

    def test_pid_cooldown_after_dismiss(self):
        state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=True,
            capture_active=True,
            render_peak=0.1,
            capture_peak=0.05,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        # Use small cooldown to avoid sleeping.
        rules = UniversalCallRules(dismiss_cooldown_seconds=600.0)
        detector = _build_detector(probe, rules=rules)
        first = detector.evaluate(is_recording=False)
        self.assertTrue(first.should_prompt_start)
        detector.set_cooldown_dismiss()
        # Clear the per-PID "already prompted" gate so we re-enter the
        # cooldown branch (a real dismissal would not flip both flags).
        detector.prompted_keys.clear()
        second = detector.evaluate(is_recording=False)
        self.assertFalse(second.should_prompt_start)
        self.assertEqual(second.reason, "cooldown_active")

    def test_uia_high_score_requires_sustain_before_prompt(self):
        presence = DesktopCallPresence(
            active=True,
            app_id="teams",
            display_name="Microsoft Teams",
            process_name="ms-teams.exe",
            pid=4242,
            score=85,
            matched=["strong:leave", "medium:mute"],
        )
        uia = StubDesktopUiaProbe(presence)
        probe = StubCallProbe(_empty_snapshot())
        detector = _build_detector(probe, uia_probe=uia)
        early = detector.evaluate(is_recording=False)
        self.assertFalse(early.should_prompt_start)
        self.assertEqual(early.reason, "awaiting_sustain")

        detector._best_signal_since_ts = time.time() - 5.0
        late = detector.evaluate(is_recording=False)
        self.assertTrue(late.should_prompt_start)
        self.assertEqual(late.signal_source, "uia")

    def test_known_app_capture_without_render_prompts(self):
        state = CallPidState(
            pid=4242,
            process_name="ms-teams.exe",
            render_active=False,
            capture_active=True,
            capture_peak=0.01,
            since_ts=time.time() - 30.0,
        )
        probe = StubCallProbe(_snapshot_with([state]))
        loopback = StubLoopbackProbe(
            AudioActivity(rms=0.05, peak=0.2, sustained_seconds=5.0)
        )
        detector = _build_detector(probe, loopback=loopback)
        decision = detector.evaluate(is_recording=False)
        self.assertTrue(decision.should_prompt_start, decision)
        self.assertIn("known_app_capture_plus_loopback", decision.matched_rules)


if __name__ == "__main__":
    unittest.main()

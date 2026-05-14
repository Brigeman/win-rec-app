"""Detector strategy selection.

Looks at the ``WIN_REC_DETECTOR`` env var first
(``hybrid|universal|legacy``), then falls back to
``settings["detector_strategy"]``, then defaults to ``HYBRID``.

The hybrid strategy composes the universal call detector (primary)
with the legacy meeting detector (fallback): universal owns the
stop/auto-stop side, legacy provides the browser-title/URL start path
on Windows builds where pycaw is unavailable or returns no sessions.

The strategy is intentionally hidden from the UI for now; see plan
section "Стратегии детекции".
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Dict, Optional

from app_logger import get_logger
from audio_backends import AudioBackend
from hybrid_detector import HybridDetector
from meeting_detection import LegacyMeetingDetector, LoopbackAudioProbe
from platform_factory import create_call_probe, create_presence_probe
from universal_call_detector import UniversalCallDetector

logger = get_logger()


class DetectorStrategy(str, Enum):
    UNIVERSAL = "universal"
    LEGACY = "legacy"
    HYBRID = "hybrid"


_ENV_VAR = "WIN_REC_DETECTOR"
_VALID = {s.value for s in DetectorStrategy}


def resolve_strategy(settings: Optional[Dict] = None) -> DetectorStrategy:
    env = (os.getenv(_ENV_VAR) or "").strip().lower()
    if env in _VALID:
        return DetectorStrategy(env)
    if settings:
        configured = str(settings.get("detector_strategy", "")).strip().lower()
        if configured in _VALID:
            return DetectorStrategy(configured)
    return DetectorStrategy.HYBRID


def create_detector(
    audio_backend: AudioBackend,
    settings: Optional[Dict] = None,
):
    """Instantiate the active detector.

    Returns an object with the shared evaluate-shape interface used by
    ``TrayApplication`` (``start/stop/set_cooldown_dismiss/set_cooldown_post_stop/evaluate``).
    """
    strategy = resolve_strategy(settings)
    if strategy == DetectorStrategy.LEGACY:
        logger.info("Detector strategy: LEGACY")
        return LegacyMeetingDetector(
            audio_backend=audio_backend,
            presence_probe=create_presence_probe(),
        )
    if strategy == DetectorStrategy.UNIVERSAL:
        logger.info("Detector strategy: UNIVERSAL")
        return UniversalCallDetector(
            call_probe=create_call_probe(),
            audio_backend=audio_backend,
        )
    logger.info("Detector strategy: HYBRID")
    # Share a single loopback probe so both detectors observe the same
    # audio stream and only one capture thread is alive at a time.
    shared_loopback = LoopbackAudioProbe(audio_backend=audio_backend)
    universal = UniversalCallDetector(
        call_probe=create_call_probe(),
        audio_backend=audio_backend,
        loopback_probe=shared_loopback,
    )
    legacy = LegacyMeetingDetector(
        audio_backend=audio_backend,
        presence_probe=create_presence_probe(),
        loopback_probe=shared_loopback,
    )
    return HybridDetector(universal=universal, legacy=legacy)

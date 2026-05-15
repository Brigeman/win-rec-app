"""Detector trace logging toggled via environment variables."""

from __future__ import annotations

import os


def detector_trace_enabled() -> bool:
    for key in ("WINREC_DETECTOR_TRACE", "WIN_REC_DETECTOR_TRACE"):
        if os.getenv(key, "").strip() == "1":
            return True
    return False

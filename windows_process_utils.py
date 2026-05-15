"""Lightweight Windows process helpers (avoid psutil during COM work)."""

from __future__ import annotations

import os
from typing import Optional

from platform_runtime import is_windows


def process_name_for_pid(pid: int) -> str:
    if not pid:
        return ""
    if is_windows():
        name = _process_name_win32(pid)
        if name:
            return name
    return _process_name_psutil(pid)


def _process_name_win32(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        access = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel32.OpenProcess(access, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""
    return ""


def _process_name_psutil(pid: int) -> str:
    try:
        import psutil

        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return ""

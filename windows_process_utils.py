"""Lightweight Windows process helpers (avoid psutil during COM work)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from platform_runtime import is_windows

# Populated once per title-scan thread (never during pycaw/COM work).
_PARENT_CACHE: Dict[int, int] = {}


def process_name_for_pid(pid: int) -> str:
    if not pid:
        return ""
    if is_windows():
        return _process_name_win32(pid)
    return _process_name_psutil(pid)


def parent_pid_for_pid(pid: int) -> int:
    """Parent PID via Toolhelp32 (no psutil — safe beside COM)."""
    if not pid or not is_windows():
        return 0
    if pid in _PARENT_CACHE:
        return int(_PARENT_CACHE[pid])
    parent = _parent_pid_toolhelp(int(pid))
    _PARENT_CACHE[pid] = parent
    return parent


def resolve_app_id_from_process_tree(pid: int, process_name: str, root_map: dict) -> str:
    """Walk parents using Win32 only (WebView2 → Teams, etc.)."""
    name = (process_name or "").lower()
    if name in root_map:
        return root_map[name]
    if name != "msedgewebview2.exe" or not pid:
        return ""
    current = int(pid)
    seen = set()
    for _ in range(16):
        if current in seen or current <= 0:
            break
        seen.add(current)
        parent = parent_pid_for_pid(current)
        if parent <= 0:
            break
        parent_name = _process_name_win32(parent)
        if parent_name in root_map:
            return root_map[parent_name]
        current = parent
    return ""


def clear_parent_cache() -> None:
    _PARENT_CACHE.clear()


def _parent_pid_toolhelp(pid: int) -> int:
    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (-1, 0xFFFFFFFF):
            return 0

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        parent = 0
        try:
            if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                while True:
                    if int(entry.th32ProcessID) == pid:
                        parent = int(entry.th32ParentProcessID)
                        break
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        return parent
    except Exception:
        return 0


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

"""Single-instance guard that survives crashed predecessors.

``QSharedMemory`` can outlive the process after a hard exit; the next
launch then attaches and immediately quits. A Windows named mutex is
released by the OS when the owning process dies.
"""

from __future__ import annotations

import os
from typing import Optional

from platform_runtime import app_support_dir, is_windows

_MUTEX_NAME = "Global\\win-rec-app-single-instance-v2"
_PID_LOCK_NAME = "single_instance.pid"


class SingleInstanceGuard:
    """Holds OS resources for the lifetime of one GUI process."""

    def __init__(self):
        self._mutex_handle: Optional[int] = None
        self._pid_lock_path = os.path.join(app_support_dir(), _PID_LOCK_NAME)

    def _write_pid_lock(self) -> None:
        try:
            with open(self._pid_lock_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

    def try_acquire(self) -> bool:
        if os.environ.get("WIN_REC_ALLOW_MULTI_INSTANCE", "").strip() == "1":
            return True
        if is_windows():
            return self._try_acquire_windows_mutex()
        return self._try_acquire_pid_file()

    def _try_acquire_windows_mutex(self, allow_retry: bool = True) -> bool:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
        except Exception:
            return self._try_acquire_pid_file()

        ERROR_ALREADY_EXISTS = 183
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return self._try_acquire_pid_file()
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            if allow_retry and self._mutex_owner_is_dead():
                return self._try_acquire_windows_mutex(allow_retry=False)
            return False
        self._mutex_handle = int(handle)
        self._write_pid_lock()
        return True

    def _mutex_owner_is_dead(self) -> bool:
        """If the pid lock names a process that exited, retry mutex once."""
        try:
            if not os.path.exists(self._pid_lock_path):
                return False
            with open(self._pid_lock_path, "r", encoding="utf-8") as f:
                old_pid = int((f.read() or "").strip() or "0")
        except Exception:
            return False
        if old_pid <= 0 or old_pid == os.getpid():
            return False
        return not self._pid_is_alive(old_pid)

    def _try_acquire_pid_file(self) -> bool:
        try:
            if os.path.exists(self._pid_lock_path):
                try:
                    with open(self._pid_lock_path, "r", encoding="utf-8") as f:
                        old_pid = int((f.read() or "").strip() or "0")
                except Exception:
                    old_pid = 0
                if old_pid and self._pid_is_alive(old_pid):
                    return False
            with open(self._pid_lock_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return True
        except Exception:
            return True

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            import psutil

            return psutil.pid_exists(pid)
        except Exception:
            return False

    def release(self) -> None:
        if self._mutex_handle is not None:
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None
        try:
            if os.path.exists(self._pid_lock_path):
                with open(self._pid_lock_path, "r", encoding="utf-8") as f:
                    if (f.read() or "").strip() == str(os.getpid()):
                        os.remove(self._pid_lock_path)
        except Exception:
            pass

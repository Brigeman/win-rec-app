"""Single-instance guard that survives crashed predecessors.

``QSharedMemory`` can outlive the process after a hard exit; the next
launch then attaches and immediately quits. A Windows named mutex is
released by the OS when the owning process dies. A stale heartbeat
allows replacing a hung instance that still holds the mutex.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from app_logger import get_logger
from platform_runtime import app_support_dir, is_windows
from process_heartbeat import heartbeat_owner_pid, is_heartbeat_stale

logger = get_logger()

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

    def _read_pid_lock(self) -> int:
        try:
            if not os.path.exists(self._pid_lock_path):
                return 0
            with open(self._pid_lock_path, "r", encoding="utf-8") as f:
                return int((f.read() or "").strip() or "0")
        except Exception:
            return 0

    def try_acquire(self) -> bool:
        if os.environ.get("WIN_REC_ALLOW_MULTI_INSTANCE", "").strip() == "1":
            return True
        if is_windows():
            return self._try_acquire_windows_mutex()
        return self._try_acquire_pid_file()

    def _try_acquire_windows_mutex(self, allow_replace: bool = True) -> bool:
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
            if allow_replace and self._should_replace_existing_owner():
                owner = self._existing_owner_pid()
                logger.warning(
                    "single_instance_replace | reason=stale_or_dead | pid=%s",
                    owner,
                )
                exited = self._terminate_and_wait(owner, timeout=5.0)
                if not exited:
                    logger.warning(
                        "single_instance_replace_failed | pid=%s | still_alive=1",
                        owner,
                    )
                return self._try_acquire_windows_mutex(allow_replace=False)
            return False
        self._mutex_handle = int(handle)
        self._write_pid_lock()
        return True

    def _terminate_and_wait(self, pid: int, timeout: float = 5.0) -> bool:
        if pid <= 0:
            return True
        self._terminate_pid(pid)
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if not self._pid_is_alive(pid):
                logger.info("single_instance | owner_exited | pid=%s", pid)
                return True
            time.sleep(0.15)
        alive = self._pid_is_alive(pid)
        if alive:
            logger.warning("single_instance | owner_still_alive | pid=%s", pid)
        return not alive

    def _existing_owner_pid(self) -> int:
        pid = heartbeat_owner_pid()
        if pid > 0:
            return pid
        return self._read_pid_lock()

    def _should_replace_existing_owner(self) -> bool:
        owner = self._existing_owner_pid()
        if owner <= 0 or owner == os.getpid():
            return False
        if not self._pid_is_alive(owner):
            return True
        return is_heartbeat_stale()

    @staticmethod
    def _terminate_pid(pid: int) -> None:
        if pid <= 0:
            return
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            access = 0x0001  # PROCESS_TERMINATE
            handle = kernel32.OpenProcess(access, False, int(pid))
            if not handle:
                return
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _try_acquire_pid_file(self) -> bool:
        try:
            old_pid = self._read_pid_lock()
            if old_pid and self._pid_is_alive(old_pid):
                if not is_heartbeat_stale():
                    return False
                self._terminate_pid(old_pid)
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

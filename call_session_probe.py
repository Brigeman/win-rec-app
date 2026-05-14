"""Windows Core Audio session probe (pycaw + comtypes).

Enumerates render and capture sessions on the default endpoints at ~1 Hz
and aggregates active sessions by PID. Falls back to an empty snapshot
on import failure (e.g. running on macOS during development).

All pycaw / comtypes imports are deferred to runtime so this module
imports cleanly on non-Windows hosts.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Set

from app_logger import get_logger
from call_probe import CallPidState, CallSessionSnapshot

logger = get_logger()

try:  # psutil is already a hard dep on Windows, soft on dev hosts.
    import psutil
except Exception:  # pragma: no cover
    psutil = None


# Core Audio session states (from AudioSessionState enum).
_AUDIO_SESSION_STATE_ACTIVE = 1

# Throttle window for repeated probe_error log lines per endpoint.
_PROBE_ERROR_LOG_INTERVAL_SEC = 30.0


class WindowsCallSessionProbe:
    """Background 1 Hz probe over Core Audio sessions.

    Self-exclusion: snapshot excludes our own PID and any child PIDs we
    can enumerate via psutil. The detector additionally drops names in
    ``UniversalCallRules.self_process_names``.

    Graceful degradation: if pycaw / comtypes cannot be imported (e.g.
    macOS), ``snapshot()`` always returns an empty unavailable snapshot.
    """

    def __init__(self, interval_seconds: float = 1.0):
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._snapshot = CallSessionSnapshot(available=False)
        self._available, unavailable_reason = self._probe_dependencies_available()
        if not self._available:
            logger.info(
                "probe_unavailable | reason=%s", unavailable_reason or "unknown"
            )
        # since_ts persistence across snapshots (PID seen continuously).
        self._pid_first_seen: Dict[int, float] = {}
        # Per-endpoint error throttling and device-change tracking.
        self._error_counts: Dict[str, int] = {"render": 0, "capture": 0}
        self._last_error_log_ts: Dict[str, float] = {"render": 0.0, "capture": 0.0}
        self._last_endpoint_name: Dict[str, str] = {"render": "", "capture": ""}

    @staticmethod
    def _probe_dependencies_available():
        import traceback

        try:
            # PyInstaller onefile unpacks into a read-only ``_MEIPASS``; the
            # ``comtypes.gen`` package tries to write generated type-library
            # wrappers into its own package dir at import time, which raises
            # OSError. Redirect ``comtypes.client.gen_dir`` to a writable
            # location under ``app_support_dir()`` BEFORE pycaw is imported.
            try:
                import os
                from platform_runtime import app_support_dir

                gen_dir = os.path.join(app_support_dir(), "comtypes_gen")
                os.makedirs(gen_dir, exist_ok=True)
                import comtypes.client  # type: ignore

                comtypes.client.gen_dir = gen_dir
            except Exception:
                # Pre-redirect failed; fall through so the real error below
                # is captured with a precise traceback.
                pass

            import pycaw  # noqa: F401
            import comtypes  # noqa: F401

            # Force the COM lookup the probe relies on so init-time failures
            # surface here instead of mid-tick.
            try:
                from pycaw.pycaw import AudioUtilities  # type: ignore

                _ = AudioUtilities.GetSpeakers()
            except Exception:
                # Allow late failures (e.g. no default device on a headless
                # box); the probe iteration will skip empty endpoints anyway.
                pass

            return True, ""
        except Exception as exc:  # pragma: no cover - packaged Windows only
            msg = f"{exc.__class__.__name__}: {exc}"
            tb_lines = traceback.format_exc().strip().splitlines()
            tail = " || ".join(tb_lines[-3:]) if tb_lines else ""
            return False, f"{msg} | tb={tail}"

    def start(self) -> None:
        if not self._available:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="call-session-probe", daemon=True
        )
        self._thread.start()
        logger.info("probe_start | thread=%s", self._thread.name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        logger.info("probe_stop | thread=%s", getattr(self._thread, "name", "?"))

    def snapshot(self) -> CallSessionSnapshot:
        with self._lock:
            return CallSessionSnapshot(
                active_call_pids=dict(self._snapshot.active_call_pids),
                self_pids=set(self._snapshot.self_pids),
                timestamp=self._snapshot.timestamp,
                available=self._snapshot.available,
            )

    # --- internals ----------------------------------------------------

    def _run(self) -> None:
        # COM must be initialized on every thread that talks to it.
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            logger.exception("CallSessionProbe: CoInitialize failed.")
        try:
            while not self._stop_event.is_set():
                try:
                    snap = self._collect_snapshot()
                    with self._lock:
                        self._snapshot = snap
                except Exception:
                    logger.exception("CallSessionProbe iteration failed.")
                    with self._lock:
                        self._snapshot = CallSessionSnapshot(
                            timestamp=time.time(), available=False
                        )
                time.sleep(self.interval_seconds)
        finally:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _self_pids(self) -> Set[int]:
        pids: Set[int] = {os.getpid()}
        if psutil is None:
            return pids
        try:
            me = psutil.Process(os.getpid())
            for child in me.children(recursive=True):
                try:
                    pids.add(child.pid)
                except Exception:
                    continue
        except Exception:
            pass
        return pids

    @staticmethod
    def _process_name(pid: int) -> str:
        if not pid or psutil is None:
            return ""
        try:
            return (psutil.Process(pid).name() or "").lower()
        except Exception:
            return ""

    def _collect_snapshot(self) -> CallSessionSnapshot:
        # Imports kept local so module load doesn't require pycaw.
        from pycaw.pycaw import AudioUtilities  # type: ignore
        from comtypes import CLSCTX_ALL  # type: ignore # noqa: F401
        try:
            from pycaw.constants import EDataFlow  # type: ignore
            render_flow = EDataFlow.eRender.value
            capture_flow = EDataFlow.eCapture.value
        except Exception:
            render_flow = 0
            capture_flow = 1

        now = time.time()
        self_pids = self._self_pids()
        agg: Dict[int, CallPidState] = {}

        self._maybe_log_endpoint_changes()

        for flow_value, is_capture in ((render_flow, False), (capture_flow, True)):
            endpoint_label = "capture" if is_capture else "render"
            try:
                sessions = self._enumerate_sessions(flow_value)
            except Exception as exc:
                self._log_probe_error(endpoint_label, exc)
                continue
            for session in sessions:
                pid = self._session_pid(session)
                if not pid or pid in self_pids:
                    continue
                if not self._session_is_active(session):
                    continue
                peak = self._session_peak(session)
                state = agg.get(pid)
                if state is None:
                    state = CallPidState(
                        pid=pid,
                        process_name=self._process_name(pid),
                    )
                    agg[pid] = state
                if is_capture:
                    state.capture_active = True
                    state.capture_peak = max(state.capture_peak, peak)
                else:
                    state.render_active = True
                    state.render_peak = max(state.render_peak, peak)

        # Maintain since_ts across iterations: keep timestamp for PIDs
        # that remained in the snapshot, drop entries that disappeared.
        new_first_seen: Dict[int, float] = {}
        for pid, state in agg.items():
            first_seen = self._pid_first_seen.get(pid, now)
            new_first_seen[pid] = first_seen
            state.since_ts = first_seen
        self._pid_first_seen = new_first_seen

        return CallSessionSnapshot(
            active_call_pids=agg,
            self_pids=self_pids,
            timestamp=now,
            available=True,
        )

    @staticmethod
    def _enumerate_sessions(flow_value: int):
        """Enumerate sessions on the default endpoint for the given flow.

        Returns a list of `AudioSession` objects (pycaw wrappers). Any
        underlying error is re-raised so the caller can apply throttled
        logging via :meth:`_log_probe_error` instead of emitting a full
        stack trace on every probe iteration.
        """
        from pycaw.pycaw import AudioUtilities  # type: ignore

        # pycaw's `AudioUtilities.GetAllSessions()` enumerates render
        # sessions on the default render endpoint. For capture we ask
        # `IMMDeviceEnumerator` directly via `GetSpeakers/GetMicrophone`.
        if flow_value == 0:  # render
            return AudioUtilities.GetAllSessions()
        # capture flow
        from comtypes import CLSCTX_ALL  # type: ignore
        from pycaw.pycaw import (  # type: ignore
            IAudioSessionManager2,
            AudioSession,
        )

        mic = AudioUtilities.GetMicrophone()
        if mic is None:
            return []
        interface = mic.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
        from comtypes import cast, POINTER  # type: ignore

        manager = cast(interface, POINTER(IAudioSessionManager2))
        enumerator = manager.GetSessionEnumerator()
        count = enumerator.GetCount()
        sessions = []
        for i in range(count):
            ctl = enumerator.GetSession(i)
            sessions.append(AudioSession(ctl))
        return sessions

    @staticmethod
    def _session_pid(session) -> int:
        try:
            return int(getattr(session, "ProcessId", 0) or 0)
        except Exception:
            return 0

    @staticmethod
    def _session_is_active(session) -> bool:
        try:
            state = session.State
            return int(state) == _AUDIO_SESSION_STATE_ACTIVE
        except Exception:
            try:
                ctl = session.SimpleAudioVolume  # touch to validate
                _ = ctl  # noqa: F841
                return False
            except Exception:
                return False

    @staticmethod
    def _session_peak(session) -> float:
        try:
            from pycaw.pycaw import IAudioMeterInformation  # type: ignore
            from comtypes import cast, POINTER  # type: ignore

            ctl = session._ctl  # pycaw exposes raw control on AudioSession
            meter_ptr = ctl.QueryInterface(IAudioMeterInformation)
            return float(meter_ptr.GetPeakValue())
        except Exception:
            return 0.0

    def _log_probe_error(self, endpoint: str, exc: BaseException) -> None:
        """Log enumeration failures with simple per-endpoint throttling.

        We log the first occurrence at WARNING, then at most once every
        30 seconds with a running counter so a broken endpoint doesn't
        flood the log file.
        """
        now = time.monotonic()
        count = self._error_counts.get(endpoint, 0) + 1
        self._error_counts[endpoint] = count
        last = self._last_error_log_ts.get(endpoint, 0.0)
        if count == 1 or (now - last) >= _PROBE_ERROR_LOG_INTERVAL_SEC:
            self._last_error_log_ts[endpoint] = now
            logger.warning(
                "probe_error | endpoint=%s | exc=%s | count=%s",
                endpoint,
                exc.__class__.__name__,
                count,
            )

    def _maybe_log_endpoint_changes(self) -> None:
        """Emit a single line when the default render/capture device changes.

        Uses pycaw's `GetSpeakers/GetMicrophone` friendly name as the
        identity proxy. Wrapped in try/except so probe iteration cannot
        fail because of logging.
        """
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore
        except Exception:
            return

        for endpoint_label, accessor in (
            ("render", AudioUtilities.GetSpeakers),
            ("capture", AudioUtilities.GetMicrophone),
        ):
            try:
                device = accessor()
                name = ""
                if device is not None:
                    try:
                        name = (device.FriendlyName or "").strip()
                    except Exception:
                        name = ""
                previous = self._last_endpoint_name.get(endpoint_label, "")
                if name and name != previous:
                    self._last_endpoint_name[endpoint_label] = name
                    if previous:
                        logger.info(
                            "probe_endpoint_change | render_or_capture=%s | name=%s",
                            endpoint_label,
                            name,
                        )
            except Exception:
                # Endpoint reads can fail mid device-switch; rely on the
                # next iteration to pick up the new device.
                continue

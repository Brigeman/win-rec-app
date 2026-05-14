import soundfile as sf
import threading
import time
import os
import lameenc
import numpy as np
import tempfile
import shutil
from typing import Callable, Dict, Optional, Tuple

from app_logger import _mask_home, get_logger
from audio_backends import AudioBackend, create_audio_backend
from transcription_service import (
    FasterWhisperService,
    TranscriptionConfig,
    TranscriptionService,
)


logger = get_logger()


def _to_2d(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        return data[:, np.newaxis]
    return data


def _to_mono(data: np.ndarray) -> np.ndarray:
    src = _to_2d(data)
    return src.mean(axis=1)


def _level_metrics(data: np.ndarray) -> Tuple[float, float]:
    if data.size == 0:
        return 0.0, 0.0
    src = _to_2d(data)
    peak = float(np.max(np.abs(src)))
    rms = float(np.sqrt(np.mean(np.square(src))))
    return max(0.0, min(1.0, rms)), max(0.0, min(1.0, peak))


def _resample_linear(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return data
    src = _to_2d(data)
    src_len = src.shape[0]
    if src_len < 2:
        return src.copy()
    dst_len = max(1, int(round(src_len * (dst_sr / float(src_sr)))))
    x_old = np.linspace(0.0, 1.0, num=src_len, endpoint=True)
    x_new = np.linspace(0.0, 1.0, num=dst_len, endpoint=True)
    out = np.zeros((dst_len, src.shape[1]), dtype=np.float32)
    for ch in range(src.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, src[:, ch]).astype(np.float32)
    return out


def _coerce_channels(data: np.ndarray, channels: int) -> np.ndarray:
    src = _to_2d(data)
    if src.shape[1] == channels:
        return src
    if channels == 1:
        return _to_mono(src)[:, np.newaxis]
    if src.shape[1] == 1:
        return np.repeat(src, channels, axis=1)
    if src.shape[1] > channels:
        return src[:, :channels]
    pad = np.repeat(src[:, -1][:, np.newaxis], channels - src.shape[1], axis=1)
    return np.concatenate([src, pad], axis=1)


def _detect_onset_sample(data: np.ndarray, threshold: float = 0.01) -> Optional[int]:
    mono = np.abs(_to_mono(data))
    if mono.size == 0:
        return None
    idx = np.where(mono >= threshold)[0]
    if idx.size == 0:
        return None
    return int(idx[0])


def _shift_signal(data: np.ndarray, shift_samples: int) -> np.ndarray:
    src = _to_2d(data)
    if shift_samples == 0:
        return src
    if shift_samples > 0:
        # Positive shift means drop leading samples from source.
        if shift_samples >= src.shape[0]:
            return np.zeros((1, src.shape[1]), dtype=np.float32)
        return src[shift_samples:]
    # Negative shift means pad source at start.
    pad = np.zeros((-shift_samples, src.shape[1]), dtype=np.float32)
    return np.vstack((pad, src))


def _resample_to_length(data: np.ndarray, target_len: int) -> np.ndarray:
    src = _to_2d(data)
    if target_len <= 0:
        return src
    if src.shape[0] == target_len:
        return src
    x_old = np.linspace(0.0, 1.0, num=src.shape[0], endpoint=True)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=True)
    out = np.zeros((target_len, src.shape[1]), dtype=np.float32)
    for ch in range(src.shape[1]):
        out[:, ch] = np.interp(x_new, x_old, src[:, ch]).astype(np.float32)
    return out

class RawRecorder(threading.Thread):
    """
    Helper thread to record a single device to a WAV file.
    """
    def __init__(
        self,
        source_name,
        device,
        filepath,
        samplerate=44100,
        channels=2,
        started_event: Optional[threading.Event] = None,
        level_callback: Optional[Callable[[str, float, float], None]] = None,
    ):
        super().__init__()
        self.source_name = source_name
        self.device = device
        self.filepath = filepath
        self.samplerate = samplerate
        self.channels = channels
        self.stop_event = threading.Event()
        self.started_event = started_event or threading.Event()
        self.level_callback = level_callback
        self.error = None
        self.total_frames = 0

    def run(self):
        try:
            with sf.SoundFile(self.filepath, mode='w', samplerate=self.samplerate, channels=self.channels) as f_wav:
                with self.device.recorder(samplerate=self.samplerate, channels=self.channels) as mic:
                    while not self.stop_event.is_set():
                        data = mic.record(numframes=2048)
                        self.total_frames += len(data)
                        if self.total_frames > 0:
                            self.started_event.set()
                        f_wav.write(data)
                        if self.level_callback:
                            rms, peak = _level_metrics(data)
                            self.level_callback(self.source_name, rms, peak)
        except Exception as e:
            self.error = str(e)
            logger.exception("Raw recorder failed for source=%s", self.source_name)

    def stop(self):
        self.stop_event.set()
        self.join()

class AudioRecorder(threading.Thread):
    """
    Orchestrates recording from Microphone, Loopback, or Both.
    """
    def __init__(
        self,
        mic_id,
        source_mode,
        output_folder,
        output_format="wav",
        normalize=False,
        on_finish_callback=None,
        on_status_callback: Optional[Callable[[str, str], None]] = None,
        on_started_callback: Optional[Callable[[], None]] = None,
        on_level_callback: Optional[Callable[[Dict[str, float]], None]] = None,
        transcription_service: Optional[TranscriptionService] = None,
        transcription_config: Optional[Dict] = None,
        audio_backend: Optional[AudioBackend] = None,
    ):
        super().__init__()
        self.mic_id = mic_id
        self.source_mode = source_mode # "mic", "loopback", "both"
        self.output_folder = output_folder
        self.output_format = output_format.lower()
        self.normalize = normalize
        self.callback = on_finish_callback
        self.on_status_callback = on_status_callback
        self.on_started_callback = on_started_callback
        self.on_level_callback = on_level_callback
        self.transcription_service = transcription_service or FasterWhisperService()
        self.transcription_config = TranscriptionConfig(**(transcription_config or {}))
        self.audio_backend = audio_backend or create_audio_backend()
        
        self.recording = False
        self.stop_event = threading.Event()
        self.error_message = None
        self.final_filepath = None
        self.transcript_filepath = None
        
        # Temp files
        self.temp_files = []
        self.recorders = []
        self.started_events = []
        self.latest_levels: Dict[str, Tuple[float, float]] = {}
        self.level_lock = threading.Lock()
        self.last_level_emit = 0.0
        self.last_non_silent_at = 0.0
        self.signal_warning_emitted = False
        # Periodic progress logging (every 5s) is driven by the same level
        # callback so we don't need a separate thread/timer.
        self._recording_started_monotonic: Optional[float] = None
        self._last_progress_log_ts: float = 0.0
        self._last_combined_rms: float = 0.0
        self._last_combined_peak: float = 0.0

    def _get_device(self, is_loopback):
        if is_loopback:
            return self.audio_backend.get_default_loopback()
        else:
            return self.audio_backend.get_microphone(self.mic_id)

    def _emit_status(self, state: str, message: str):
        if self.on_status_callback:
            self.on_status_callback(state, message)

    def _emit_combined_level(self):
        now = time.time()
        if now - self.last_level_emit < 0.08:
            return

        with self.level_lock:
            if not self.latest_levels:
                rms = 0.0
                peak = 0.0
            else:
                rms = sum(v[0] for v in self.latest_levels.values()) / len(self.latest_levels)
                peak = max(v[1] for v in self.latest_levels.values())
        self.last_level_emit = now
        self._last_combined_rms = rms
        self._last_combined_peak = peak
        if self.on_level_callback:
            self.on_level_callback({"rms": rms, "peak": peak})

        # USB / laptop mics are often below 0.01 peak; avoid false alarms
        # during short pauses. Longer grace for mic / both than loopback-only.
        mic_like = self.source_mode in ("mic", "both")
        if mic_like:
            has_signal = peak > 0.004 or rms > 0.0012
            silence_s = 8.0
        else:
            has_signal = peak > 0.008 or rms > 0.002
            silence_s = 4.5

        if has_signal:
            self.last_non_silent_at = now
            self.signal_warning_emitted = False
        elif self.recording and now - self.last_non_silent_at > silence_s:
            if not self.signal_warning_emitted:
                self.signal_warning_emitted = True
                logger.warning(
                    "recording_warning | warning_kind=no_signal | mode=%s | last_rms=%.4f | last_peak=%.4f",
                    self.source_mode,
                    rms,
                    peak,
                )
                if self.source_mode == "mic":
                    msg = (
                        "Очень тихий сигнал с микрофона. "
                        "Чтобы писать голоса из Teams/Zoom, включите режим Both или Loopback."
                    )
                elif self.source_mode == "both":
                    msg = "Слабый сигнал. Проверьте микрофон и громкость в звонке."
                else:
                    msg = (
                        "Слабый сигнал с компьютера (loopback). "
                        "Увеличьте громкость динамиков или выберите режим Both."
                    )
                self._emit_status("warning", msg)

        self._maybe_log_progress(now)

    def _maybe_log_progress(self, now: float):
        if not self.recording or self._recording_started_monotonic is None:
            return
        mono = time.monotonic()
        # First call after recording_start sets the baseline; subsequent
        # logs fire once every 5 seconds independent of level activity.
        if self._last_progress_log_ts == 0.0:
            self._last_progress_log_ts = mono
            return
        if mono - self._last_progress_log_ts < 5.0:
            return
        self._last_progress_log_ts = mono
        elapsed = mono - self._recording_started_monotonic
        mic_frames = 0
        loop_frames = 0
        for rec in self.recorders:
            if getattr(rec, "source_name", "") == "mic":
                mic_frames = getattr(rec, "total_frames", 0) or 0
            elif getattr(rec, "source_name", "") == "loopback":
                loop_frames = getattr(rec, "total_frames", 0) or 0
        logger.info(
            "recording_progress | elapsed=%.1f | frames_mic=%s | frames_loop=%s | last_rms=%.4f | last_peak=%.4f",
            elapsed,
            mic_frames,
            loop_frames,
            self._last_combined_rms,
            self._last_combined_peak,
        )

    def _on_source_level(self, source_name: str, rms: float, peak: float):
        with self.level_lock:
            self.latest_levels[source_name] = (rms, peak)
        self._emit_combined_level()

    def run(self):
        self.recording = True
        self.error_message = None
        self.temp_files = []
        self.recorders = []
        self.started_events = []
        self.latest_levels = {}
        self.last_level_emit = 0.0
        self.last_non_silent_at = time.time()
        self.signal_warning_emitted = False
        self._recording_started_monotonic = None
        self._last_progress_log_ts = 0.0

        # Stage is updated as the orchestrator walks through its phases so
        # an unexpected exception can be attributed to a concrete step.
        stage = "starting"
        logger.info(
            "recording_start | mode=%s | mic_id=%s | output_folder=%s | format=%s | normalize=%s",
            self.source_mode,
            self.mic_id or "",
            _mask_home(self.output_folder),
            self.output_format,
            self.normalize,
        )
        recording_started_wall = time.time()

        try:
            try:
                self._emit_status("starting", "Initializing capture devices...")
                # 1. Setup Recorders
                if self.source_mode == "both":
                    # Need two recorders
                    dev_mic = self._get_device(is_loopback=False)
                    dev_loop = self._get_device(is_loopback=True)

                    t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                    t2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                    self.temp_files = [t1, t2]
                    e1, e2 = threading.Event(), threading.Event()
                    self.started_events = [e1, e2]

                    self.recorders.append(RawRecorder("mic", dev_mic, t1, started_event=e1, level_callback=self._on_source_level))
                    self.recorders.append(RawRecorder("loopback", dev_loop, t2, started_event=e2, level_callback=self._on_source_level))
                    logger.info(
                        "recording_devices | mic_name=%s | loop_name=%s",
                        getattr(dev_mic, "name", "<unknown>"),
                        getattr(dev_loop, "name", "<unknown>"),
                    )

                elif self.source_mode == "loopback":
                    dev = self._get_device(is_loopback=True)
                    t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                    self.temp_files = [t1]
                    e1 = threading.Event()
                    self.started_events = [e1]
                    self.recorders.append(RawRecorder("loopback", dev, t1, started_event=e1, level_callback=self._on_source_level))
                    logger.info(
                        "recording_devices | mic_name=%s | loop_name=%s",
                        "",
                        getattr(dev, "name", "<unknown>"),
                    )

                else: # mic
                    dev = self._get_device(is_loopback=False)
                    t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                    self.temp_files = [t1]
                    e1 = threading.Event()
                    self.started_events = [e1]
                    self.recorders.append(RawRecorder("mic", dev, t1, started_event=e1, level_callback=self._on_source_level))
                    logger.info(
                        "recording_devices | mic_name=%s | loop_name=%s",
                        getattr(dev, "name", "<unknown>"),
                        "",
                    )

                # 2. Start Recording
                for r in self.recorders:
                    r.start()

                # Wait for first actual frames from each source to avoid fake recording state
                for started_event in self.started_events:
                    if not started_event.wait(timeout=3.0):
                        raise Exception("Capture stream did not start in time.")

                stage = "recording"
                self._recording_started_monotonic = time.monotonic()
                if self.on_started_callback:
                    self.on_started_callback()
                self._emit_status("recording", f"Recording {self.source_mode}...")

                # Wait for stop signal
                self.stop_event.wait()
                stage = "stopping"
                self._emit_status("stopping", "Stopping and finalizing audio...")
            finally:
                # Ensure every started RawRecorder is stopped and joined on any
                # exit path (including started_event timeout) so threads don't leak.
                for r in self.recorders:
                    try:
                        r.stop()
                    except Exception:
                        logger.exception("Failed to stop raw recorder source=%s", getattr(r, "source_name", "?"))

            for r in self.recorders:
                if r.error:
                    raise Exception(f"Recorder error: {r.error}")

            # 4. Mix/Process
            if len(self.temp_files) == 2:
                stage = "mixing"
                mixed_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                self._mix_audio(self.temp_files[0], self.temp_files[1], mixed_wav)
                # Use mixed file as source for next steps
                source_wav = mixed_wav
                self.temp_files.append(mixed_wav) # Mark for cleanup
            else:
                source_wav = self.temp_files[0]

            # 5. Normalization
            stage = "finalizing"
            if self.normalize:
                self._normalize_audio(source_wav)
            
            # 6. Finalize
            if not os.path.exists(self.output_folder):
                os.makedirs(self.output_folder)

            timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            # Append a short PID-derived suffix so concurrent recordings finishing
            # in the same second don't collide on output paths.
            suffix = f"{os.getpid() % 1000:03d}"
            filename = f"{timestamp}_{suffix}.{self.output_format}"
            self.final_filepath = os.path.join(self.output_folder, filename)
            
            if self.output_format == "mp3":
                self._convert_to_mp3(source_wav, self.final_filepath)
            else:
                shutil.copy2(source_wav, self.final_filepath)
            try:
                size_bytes = os.path.getsize(self.final_filepath)
            except Exception:
                size_bytes = -1
            duration_sec = 0.0
            if self._recording_started_monotonic is not None:
                duration_sec = max(0.0, time.monotonic() - self._recording_started_monotonic)
            else:
                duration_sec = max(0.0, time.time() - recording_started_wall)
            logger.info(
                "recording_stop | duration=%.2f | output=%s | size_bytes=%s",
                duration_sec,
                _mask_home(self.final_filepath),
                size_bytes,
            )

            # 7. Transcription
            if self.transcription_config.enabled:
                stage = "transcribing"
                self._emit_status("transcribing", "Running local transcription...")
                txt_path = os.path.splitext(self.final_filepath)[0] + ".txt"
                ok, transcribe_error = self.transcription_service.transcribe_file(
                    self.final_filepath,
                    txt_path,
                    self.transcription_config,
                )
                if ok:
                    self.transcript_filepath = txt_path
                    logger.info(
                        "transcription_done | output=%s",
                        _mask_home(txt_path),
                    )
                else:
                    self.transcript_filepath = None
                    logger.error(
                        "transcription_failed | reason=%s",
                        transcribe_error,
                    )
                    self._emit_status("warning", f"Transcription failed: {transcribe_error}")
            self._emit_status("idle", "Ready")

        except Exception as e:
            self.error_message = str(e)
            logger.exception("Error during recording.")
            logger.error("recording_aborted | stage=%s | error=%s", stage, e)
            self._emit_status("error", self.error_message)
        finally:
            self.recording = False
            self._recording_started_monotonic = None
            # Clean up all temp files
            for t in self.temp_files:
                if os.path.exists(t):
                    try:
                        os.remove(t)
                    except Exception:
                        logger.exception("Failed to remove temp file: %s", t)
                
            if self.callback:
                self.callback(self.final_filepath, self.error_message, self.transcript_filepath)

    def stop(self, wait: bool = False, timeout: float = 8.0):
        """Signal the recorder to stop.

        If ``wait`` is True, block up to ``timeout`` seconds for the orchestrator
        thread to finish so the output file is fully flushed before returning.
        """
        self.stop_event.set()
        if wait:
            try:
                self.join(timeout)
            except RuntimeError:
                # join() before start() raises; nothing to wait on.
                pass
            if self.is_alive():
                logger.warning(
                    "AudioRecorder did not finish within %.1fs; continuing anyway.",
                    timeout,
                )

    def _mix_audio(self, mic_file, loopback_file, out_file):
        mic_data, mic_sr = sf.read(mic_file, dtype="float32")
        loop_data, loop_sr = sf.read(loopback_file, dtype="float32")

        target_sr = int(loop_sr)
        target_channels = max(_to_2d(mic_data).shape[1], _to_2d(loop_data).shape[1], 2)

        mic_data = _coerce_channels(_resample_linear(mic_data, mic_sr, target_sr), target_channels)
        loop_data = _coerce_channels(loop_data, target_channels)

        onset_lag_samples = 0
        mic_onset = _detect_onset_sample(mic_data)
        loop_onset = _detect_onset_sample(loop_data)
        if mic_onset is not None and loop_onset is not None:
            lag = mic_onset - loop_onset
            if abs(lag) > int(0.005 * target_sr):
                onset_lag_samples = lag
                logger.info("Applying onset alignment lag=%s samples", lag)
                mic_data = _shift_signal(mic_data, lag)

        ref_len = loop_data.shape[0]
        mic_len_pre = mic_data.shape[0]
        drift_ratio = 0.0
        if ref_len > 0 and mic_len_pre > 0:
            drift_ratio = abs(mic_len_pre - ref_len) / float(ref_len)
            if drift_ratio > 0.01:
                logger.warning(
                    "Applying drift compensation | mic_len=%s loop_len=%s drift=%.4f",
                    mic_len_pre,
                    ref_len,
                    drift_ratio,
                )
                mic_data = _resample_to_length(mic_data, ref_len)

        logger.info(
            "mix | mic_sr=%s | loop_sr=%s | mic_len=%s | loop_len=%s | drift_ratio=%.4f | onset_lag_samples=%s",
            int(mic_sr),
            int(loop_sr),
            mic_len_pre,
            ref_len,
            drift_ratio,
            onset_lag_samples,
        )

        max_len = max(mic_data.shape[0], loop_data.shape[0])
        if mic_data.shape[0] < max_len:
            mic_data = np.vstack(
                (mic_data, np.zeros((max_len - mic_data.shape[0], mic_data.shape[1]), dtype=np.float32))
            )
        if loop_data.shape[0] < max_len:
            loop_data = np.vstack(
                (loop_data, np.zeros((max_len - loop_data.shape[0], loop_data.shape[1]), dtype=np.float32))
            )

        # Prioritize loopback signal while keeping speech from microphone clear.
        mixed = (loop_data * 0.72) + (mic_data * 0.58)
        mixed = np.clip(mixed, -1.0, 1.0).astype(np.float32)
        sf.write(out_file, mixed, target_sr)

    def _normalize_audio(self, filepath):
        try:
            data, sr = sf.read(filepath)
            max_val = np.max(np.abs(data))
            if max_val > 0:
                target_peak = 0.99 
                factor = target_peak / max_val
                data = data * factor
                sf.write(filepath, data, sr)
                logger.info("normalize_done | factor=%.4f", float(factor))
            else:
                logger.info("normalize_done | factor=0.0")
        except Exception as e:
            logger.exception("Normalization failed.")

    def _convert_to_mp3(self, src_wav, dst_mp3):
        data, sr = sf.read(src_wav)
        channels = data.shape[1] if data.ndim > 1 else 1
        
        pcm_data = (data * 32767).clip(-32768, 32767).astype(np.int16)
        
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(192)
        encoder.set_in_sample_rate(sr)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        
        mp3_data = encoder.encode(pcm_data.tobytes())
        mp3_data += encoder.flush()
        
        with open(dst_mp3, "wb") as f_mp3:
            f_mp3.write(mp3_data)
        logger.info(
            "mp3_done | bitrate=192 | output=%s",
            _mask_home(dst_mp3),
        )

def get_devices(include_loopback=False):
    backend = create_audio_backend()
    try:
        devices = backend.list_microphones(include_loopback=include_loopback)
        return [{"id": d.id, "name": d.name} for d in devices]
    except Exception as e:
        logger.exception("Error fetching devices.")
        return []

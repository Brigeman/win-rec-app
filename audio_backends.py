from dataclasses import dataclass
from typing import List, Optional, Protocol

try:
    import soundcard as sc
except Exception:  # pragma: no cover - exercised in environments without audio deps
    sc = None

from app_logger import get_logger
from platform_runtime import is_macos


logger = get_logger()


@dataclass
class AudioDeviceInfo:
    id: str
    name: str


class AudioBackend(Protocol):
    def list_microphones(self, include_loopback: bool = False) -> List[AudioDeviceInfo]:
        ...

    def default_microphone_id(self) -> str:
        ...

    def get_microphone(self, mic_id: str):
        ...

    def get_default_loopback(self):
        ...


class SoundCardAudioBackend:
    VIRTUAL_LOOPBACK_KEYWORDS = ("blackhole", "loopback", "soundflower", "virtual")

    def list_microphones(self, include_loopback: bool = False) -> List[AudioDeviceInfo]:
        self._require_soundcard()
        devices = sc.all_microphones(include_loopback=include_loopback)
        infos = [AudioDeviceInfo(id=d.id, name=d.name) for d in devices]
        logger.info(
            "audio_list_microphones | include_loopback=%s | count=%s",
            include_loopback,
            len(infos),
        )
        return infos

    def default_microphone_id(self) -> str:
        self._require_soundcard()
        mic = sc.default_microphone()
        mic_id = getattr(mic, "id", "") if mic else ""
        mic_name = getattr(mic, "name", "") if mic else ""
        logger.info(
            "audio_default_microphone | id=%s | name=%s",
            mic_id or "",
            mic_name or "",
        )
        return mic_id

    def get_microphone(self, mic_id: str):
        self._require_soundcard()
        if not mic_id:
            raise Exception("No microphone selected.")
        return sc.get_microphone(mic_id, include_loopback=False)

    def get_default_loopback(self):
        self._require_soundcard()
        default_speaker = sc.default_speaker()
        if not default_speaker:
            raise Exception("No default speaker found.")

        mics = sc.all_microphones(include_loopback=True)
        loopback_mic = next((m for m in mics if m.name == default_speaker.name), None)
        if loopback_mic:
            logger.info(
                "audio_default_loopback | match=exact | speaker=%s | id=%s",
                default_speaker.name,
                getattr(loopback_mic, "id", ""),
            )
            return loopback_mic

        loopback_mic = next((m for m in mics if default_speaker.name in m.name), None)
        if loopback_mic:
            logger.info(
                "audio_default_loopback | match=substring | speaker=%s | id=%s | name=%s",
                default_speaker.name,
                getattr(loopback_mic, "id", ""),
                getattr(loopback_mic, "name", ""),
            )
            return loopback_mic

        # macOS typically requires a virtual loopback device for system capture.
        if is_macos():
            logger.info(
                "audio_default_loopback | match=fallback | speaker=%s | searching=virtual_keyword",
                default_speaker.name,
            )
            virtual = next(
                (
                    m
                    for m in mics
                    if any(keyword in m.name.lower() for keyword in self.VIRTUAL_LOOPBACK_KEYWORDS)
                ),
                None,
            )
            if virtual:
                logger.info(
                    "audio_default_loopback | match=virtual | id=%s | name=%s",
                    getattr(virtual, "id", ""),
                    getattr(virtual, "name", ""),
                )
                return virtual
            raise Exception(
                "No virtual loopback device found. Install/configure BlackHole or similar and set it as output."
            )
        raise Exception("No default system output loopback device found.")

    @staticmethod
    def _require_soundcard():
        if sc is None:
            raise Exception("soundcard dependency is unavailable in this environment.")


def create_audio_backend() -> AudioBackend:
    return SoundCardAudioBackend()

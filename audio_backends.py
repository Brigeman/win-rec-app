from dataclasses import dataclass
from typing import List, Optional, Protocol

try:
    import soundcard as sc
except Exception:  # pragma: no cover - exercised in environments without audio deps
    sc = None

from platform_runtime import is_macos


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
        return [AudioDeviceInfo(id=d.id, name=d.name) for d in devices]

    def default_microphone_id(self) -> str:
        self._require_soundcard()
        mic = sc.default_microphone()
        return getattr(mic, "id", "") if mic else ""

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
        if not loopback_mic:
            loopback_mic = next((m for m in mics if default_speaker.name in m.name), None)
        if loopback_mic:
            return loopback_mic

        # macOS typically requires a virtual loopback device for system capture.
        if is_macos():
            virtual = next(
                (
                    m
                    for m in mics
                    if any(keyword in m.name.lower() for keyword in self.VIRTUAL_LOOPBACK_KEYWORDS)
                ),
                None,
            )
            if virtual:
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

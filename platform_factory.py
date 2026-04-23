from audio_backends import AudioBackend, create_audio_backend
from hotkeys_service import HotkeyService, create_hotkey_service
from macos_presence import MacOSPresenceProbe
from platform_runtime import is_macos
from presence_probe import PresenceProbe
from system_ops import SystemOps, create_system_ops
from windows_presence import WindowsPresenceProbe


def create_presence_probe() -> PresenceProbe:
    if is_macos():
        return MacOSPresenceProbe()
    return WindowsPresenceProbe()


def create_platform_services() -> tuple[AudioBackend, PresenceProbe, HotkeyService, SystemOps]:
    return (
        create_audio_backend(),
        create_presence_probe(),
        create_hotkey_service(),
        create_system_ops(),
    )

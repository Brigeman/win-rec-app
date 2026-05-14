from audio_backends import AudioBackend, create_audio_backend
from call_probe import CallProbe, NullCallProbe
from hotkeys_service import HotkeyService, create_hotkey_service
from macos_presence import MacOSPresenceProbe
from platform_runtime import is_macos, is_windows
from presence_probe import PresenceProbe
from system_ops import SystemOps, create_system_ops
from windows_presence import WindowsPresenceProbe


def create_presence_probe() -> PresenceProbe:
    if is_macos():
        return MacOSPresenceProbe()
    return WindowsPresenceProbe()


def create_call_probe() -> CallProbe:
    """Build the platform-native call session probe.

    On Windows we return :class:`WindowsCallSessionProbe`, which lazily
    imports ``pycaw``/``comtypes`` only inside its background probe
    thread (never in the Qt GUI thread, avoiding ``RPC_E_CHANGED_MODE``).
    If bootstrap fails, snapshots stay ``available=False``.

    On non-Windows hosts we return :class:`NullCallProbe`.
    """
    if not is_windows():
        return NullCallProbe()
    try:
        from call_session_probe import WindowsCallSessionProbe
        return WindowsCallSessionProbe()
    except Exception:  # pragma: no cover - defensive on broken envs
        return NullCallProbe()


def create_platform_services() -> tuple[AudioBackend, PresenceProbe, HotkeyService, SystemOps]:
    return (
        create_audio_backend(),
        create_presence_probe(),
        create_hotkey_service(),
        create_system_ops(),
    )

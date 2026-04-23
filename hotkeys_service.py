from typing import Callable, Dict, Protocol

from app_logger import get_logger
from platform_runtime import is_macos


logger = get_logger()


class HotkeyService(Protocol):
    def clear(self):
        ...

    def register(self, hotkey: str, callback: Callable[[], None]):
        ...


class KeyboardHotkeyService:
    def __init__(self):
        self._keyboard = None
        try:
            import keyboard  # type: ignore

            self._keyboard = keyboard
        except Exception:
            logger.exception("keyboard module unavailable; global hotkeys disabled.")
            self._keyboard = None

    def clear(self):
        if not self._keyboard:
            return
        try:
            self._keyboard.unhook_all_hotkeys()
        except AttributeError:
            pass
        except Exception:
            logger.exception("Failed to clear hotkeys.")

    def register(self, hotkey: str, callback: Callable[[], None]):
        if not self._keyboard or not hotkey:
            return
        try:
            self._keyboard.add_hotkey(hotkey, callback)
        except Exception:
            logger.exception("Failed to register hotkey: %s", hotkey)


class DisabledHotkeyService:
    def clear(self):
        return

    def register(self, hotkey: str, callback: Callable[[], None]):
        return


def create_hotkey_service() -> HotkeyService:
    # keyboard can be unreliable on macOS without accessibility permissions.
    if is_macos():
        return DisabledHotkeyService()
    return KeyboardHotkeyService()

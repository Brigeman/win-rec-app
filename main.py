import sys
from PyQt6.QtWidgets import QApplication
from gui import TrayApplication
from app_logger import setup_logging
from platform_factory import create_platform_services

def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    audio_backend, _, hotkey_service, system_ops = create_platform_services()
    tray = TrayApplication(
        app,
        audio_backend=audio_backend,
        hotkey_service=hotkey_service,
        system_ops=system_ops,
    )
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

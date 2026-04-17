import sys
from PyQt6.QtWidgets import QApplication
from gui import TrayApplication
from app_logger import setup_logging

def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    tray = TrayApplication(app)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

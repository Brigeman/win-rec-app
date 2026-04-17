import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl, QMimeData
from app_logger import get_logger


logger = get_logger()

def copy_file_to_clipboard(filepath):
    """
    Copies a file path to clipboard using Qt APIs only.
    Avoids shell command interpolation risks.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        return False, "File does not exist"

    try:
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(filepath)])
        QApplication.clipboard().setMimeData(data)
        return True, "Copied via Qt file URL"
    except Exception as e:
        logger.exception("Clipboard copy failed.")
        return False, f"All methods failed. Qt Error: {e}"

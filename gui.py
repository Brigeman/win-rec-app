import json
import os
import shutil
import tempfile
from typing import Dict

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QBrush, QIcon, QKeySequence, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QFrame,
    QGraphicsDropShadowEffect,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
import keyboard
import soundcard as sc

from app_logger import get_logger
from audio_recorder import AudioRecorder, get_devices
from clipboard_utils import copy_file_to_clipboard


CONFIG_FILE = "settings.json"
logger = get_logger()


def default_output_folder() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "win-rec-app")
    os.makedirs(out, exist_ok=True)
    return out


def resource_path(relative_path):
    try:
        base_path = __import__("sys")._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class SignalManager(QObject):
    recording_finished = pyqtSignal(str, str, str)
    status_changed = pyqtSignal(str, str)
    level_changed = pyqtSignal(object)
    recording_confirmed = pyqtSignal()


class HotkeyEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Click to set hotkey...")
        self.setReadOnly(True)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear()
            return
        if key == Qt.Key.Key_Escape:
            self.clearFocus()
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("windows")

        key_text = ""
        if 0x20 <= key <= 0x7E:
            key_text = chr(key).lower()
        else:
            key_map = {
                Qt.Key.Key_F1: "f1",
                Qt.Key.Key_F2: "f2",
                Qt.Key.Key_F3: "f3",
                Qt.Key.Key_F4: "f4",
                Qt.Key.Key_F5: "f5",
                Qt.Key.Key_F6: "f6",
                Qt.Key.Key_F7: "f7",
                Qt.Key.Key_F8: "f8",
                Qt.Key.Key_F9: "f9",
                Qt.Key.Key_F10: "f10",
                Qt.Key.Key_F11: "f11",
                Qt.Key.Key_F12: "f12",
                Qt.Key.Key_Space: "space",
            }
            key_text = key_map.get(key, QKeySequence(key).toString().lower())

        if key_text:
            parts.append(key_text)
            self.setText("+".join(parts))
            self.clearFocus()


class RecorderBarWindow(QWidget):
    rec_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    hide_clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.elapsed = 0
        self.drag_start = None
        self._build_ui()
        self._build_timer()
        self.set_state("idle", "Ready")

    def _build_ui(self):
        self.setWindowTitle("win rec app")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(620, 86)

        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)

        panel = QFrame()
        panel.setObjectName("recorderPanel")
        panel_layout = QHBoxLayout()
        panel_layout.setContentsMargins(14, 10, 14, 10)
        panel_layout.setSpacing(10)
        panel.setLayout(panel_layout)

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 120))
        panel.setGraphicsEffect(shadow)

        self.btn_rec = QPushButton("REC")
        self.btn_stop = QPushButton("STOP")
        self.btn_hide = QPushButton("HIDE")
        self.btn_rec.setFixedWidth(74)
        self.btn_stop.setFixedWidth(74)
        self.btn_hide.setFixedWidth(70)
        self.btn_stop.setEnabled(False)
        self.btn_rec.clicked.connect(self.rec_clicked.emit)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        self.btn_hide.clicked.connect(self.hide_clicked.emit)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setMinimumWidth(185)
        self.lbl_timer = QLabel("00:00")
        self.lbl_timer.setMinimumWidth(56)

        meter_col = QVBoxLayout()
        meter_col.setSpacing(3)
        self.lbl_meter = QLabel("RMS 0% | Peak 0%")
        self.lbl_meter.setMinimumWidth(140)

        self.meter_peak = QProgressBar()
        self.meter_peak.setRange(0, 100)
        self.meter_peak.setValue(0)
        self.meter_peak.setTextVisible(False)
        self.meter_peak.setFixedHeight(10)

        self.meter_rms = QProgressBar()
        self.meter_rms.setRange(0, 100)
        self.meter_rms.setValue(0)
        self.meter_rms.setTextVisible(False)
        self.meter_rms.setFixedHeight(8)

        meter_col.addWidget(self.lbl_meter)
        meter_col.addWidget(self.meter_peak)
        meter_col.addWidget(self.meter_rms)

        panel_layout.addWidget(self.btn_rec)
        panel_layout.addWidget(self.btn_stop)
        panel_layout.addWidget(self.btn_hide)
        panel_layout.addWidget(self.lbl_status)
        panel_layout.addWidget(self.lbl_timer)
        panel_layout.addLayout(meter_col, 1)

        root.addWidget(panel)
        self.setLayout(root)
        self.setStyleSheet(
            """
            #recorderPanel {
                background: rgba(26, 30, 36, 230);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 16px;
            }
            QPushButton {
                color: #f3f6fb;
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 10px;
                padding: 5px 8px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 34);
            }
            QPushButton:disabled {
                color: #8d95a3;
                background: rgba(255, 255, 255, 10);
                border: 1px solid rgba(255, 255, 255, 22);
            }
            QLabel {
                color: #e4e8ef;
                font-size: 12px;
            }
            QProgressBar {
                background: rgba(255, 255, 255, 20);
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 5px;
            }
            QProgressBar::chunk {
                border-radius: 4px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #31d17f, stop:0.6 #d6d84f, stop:1 #ec5e63);
            }
            """
        )

    def _build_timer(self):
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.elapsed += 1
        m, s = divmod(self.elapsed, 60)
        self.lbl_timer.setText(f"{m:02d}:{s:02d}")

    def reset_timer(self):
        self.elapsed = 0
        self.lbl_timer.setText("00:00")
        if self.timer.isActive():
            self.timer.stop()

    def update_meter(self, metrics: Dict[str, float]):
        rms = max(0.0, min(1.0, float(metrics.get("rms", 0.0))))
        peak = max(0.0, min(1.0, float(metrics.get("peak", 0.0))))
        self.meter_rms.setValue(int(rms * 100))
        self.meter_peak.setValue(int(peak * 100))
        self.lbl_meter.setText(f"RMS {int(rms * 100)}% | Peak {int(peak * 100)}%")

    def set_state(self, state: str, message: str):
        self.lbl_status.setText(message)
        if state == "recording":
            self.btn_rec.setEnabled(False)
            self.btn_stop.setEnabled(True)
            if not self.timer.isActive():
                self.timer.start()
        elif state in ("starting", "stopping", "transcribing"):
            self.btn_rec.setEnabled(False)
            self.btn_stop.setEnabled(False)
        else:
            self.btn_rec.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.reset_timer()
            if state in ("idle", "error"):
                self.update_meter({"rms": 0.0, "peak": 0.0})

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self.drag_start:
            self.move(event.globalPosition().toPoint() - self.drag_start)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        super().mouseReleaseEvent(event)


class SettingsWindow(QMainWindow):
    settings_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings - win rec app")
        self.setGeometry(120, 120, 520, 700)
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        layout = QVBoxLayout()
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        group_mic = QGroupBox("Input Device")
        layout_mic = QVBoxLayout()
        self.combo_mic = QComboBox()
        btn_refresh = QPushButton("Refresh Devices")
        btn_refresh.clicked.connect(self.refresh_devices)
        layout_mic.addWidget(self.combo_mic)
        layout_mic.addWidget(btn_refresh)
        group_mic.setLayout(layout_mic)
        layout.addWidget(group_mic)

        group_out = QGroupBox("Output Configuration")
        layout_out = QFormLayout()
        folder_row = QHBoxLayout()
        self.lbl_folder = QLabel(default_output_folder())
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse_folder)
        folder_row.addWidget(self.lbl_folder)
        folder_row.addWidget(btn_browse)
        self.combo_fmt = QComboBox()
        self.combo_fmt.addItems(["WAV", "MP3"])
        layout_out.addRow("Folder:", folder_row)
        layout_out.addRow("Format:", self.combo_fmt)
        group_out.setLayout(layout_out)
        layout.addWidget(group_out)

        group_tray = QGroupBox("Tray Icon Behavior")
        layout_tray = QFormLayout()
        self.combo_left_click = QComboBox()
        self.combo_left_click.addItems(["Last Used", "Microphone", "Loopback", "Both"])
        layout_tray.addRow("Left Click Action:", self.combo_left_click)
        group_tray.setLayout(layout_tray)
        layout.addWidget(group_tray)

        group_post = QGroupBox("Post-Processing & Clipboard")
        layout_post = QVBoxLayout()
        self.chk_normalize = QCheckBox("Normalize Audio")
        self.chk_clipboard = QCheckBox("Copy File to Clipboard")
        self.chk_delete = QCheckBox("Delete original after copy (move to temp)")
        self.chk_delete.setEnabled(False)
        self.chk_clipboard.toggled.connect(lambda c: self.chk_delete.setEnabled(c))
        layout_post.addWidget(self.chk_normalize)
        layout_post.addWidget(self.chk_clipboard)
        layout_post.addWidget(self.chk_delete)
        group_post.setLayout(layout_post)
        layout.addWidget(group_post)

        group_transcribe = QGroupBox("Local Transcription (faster-whisper)")
        layout_transcribe = QFormLayout()
        self.chk_transcribe = QCheckBox("Enable local transcription after STOP")
        self.chk_transcribe.setChecked(True)
        self.combo_model = QComboBox()
        self.combo_model.addItems(["tiny", "base", "small", "medium"])
        self.combo_compute = QComboBox()
        self.combo_compute.addItems(["int8", "float16", "float32"])
        self.chk_local_only = QCheckBox("Local files only (no model download)")
        self.chk_local_only.setChecked(True)
        self.input_model_path = QLineEdit()
        self.input_model_path.setPlaceholderText("Optional local model path")
        self.input_language = QLineEdit()
        self.input_language.setPlaceholderText("Auto detect if empty")
        layout_transcribe.addRow(self.chk_transcribe)
        layout_transcribe.addRow("Model:", self.combo_model)
        layout_transcribe.addRow("Compute type:", self.combo_compute)
        layout_transcribe.addRow(self.chk_local_only)
        layout_transcribe.addRow("Model path:", self.input_model_path)
        layout_transcribe.addRow("Language:", self.input_language)
        group_transcribe.setLayout(layout_transcribe)
        layout.addWidget(group_transcribe)

        group_hotkeys = QGroupBox("Global Hotkeys")
        layout_hotkeys = QFormLayout()
        self.hk_mic = HotkeyEdit()
        self.hk_loop = HotkeyEdit()
        self.hk_both = HotkeyEdit()
        self.hk_stop = HotkeyEdit()
        layout_hotkeys.addRow("Record Mic:", self.hk_mic)
        layout_hotkeys.addRow("Record Loopback:", self.hk_loop)
        layout_hotkeys.addRow("Record Both:", self.hk_both)
        layout_hotkeys.addRow("Stop Recording:", self.hk_stop)
        group_hotkeys.setLayout(layout_hotkeys)
        layout.addWidget(group_hotkeys)

        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self.save_settings)
        layout.addWidget(btn_save)
        self.refresh_devices()

    def refresh_devices(self):
        self.combo_mic.clear()
        try:
            mics = get_devices(include_loopback=False)
            if not mics:
                self.combo_mic.addItem("No microphone found", "")
                return
            default_mic = sc.default_microphone()
            default_index = 0
            for i, m in enumerate(mics):
                self.combo_mic.addItem(m["name"], m["id"])
                if default_mic and m["id"] == getattr(default_mic, "id", None):
                    default_index = i
            self.combo_mic.setCurrentIndex(default_index)
        except Exception:
            logger.exception("Error refreshing microphone devices.")
            self.combo_mic.addItem("No microphone found", "")

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.lbl_folder.setText(folder)

    def load_settings(self):
        if not os.path.exists(CONFIG_FILE):
            self.lbl_folder.setText(default_output_folder())
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.lbl_folder.setText(data.get("output_folder", default_output_folder()))
            fmt_idx = self.combo_fmt.findText(data.get("format", "WAV"))
            if fmt_idx >= 0:
                self.combo_fmt.setCurrentIndex(fmt_idx)
            saved_id = data.get("device_id")
            if saved_id:
                idx = self.combo_mic.findData(saved_id)
                if idx >= 0:
                    self.combo_mic.setCurrentIndex(idx)
            mode_idx = self.combo_left_click.findText(data.get("tray_click_mode", "Last Used"))
            if mode_idx >= 0:
                self.combo_left_click.setCurrentIndex(mode_idx)

            self.chk_normalize.setChecked(data.get("normalize", False))
            self.chk_clipboard.setChecked(data.get("clipboard", False))
            self.chk_delete.setChecked(data.get("delete_after", False))
            self.chk_delete.setEnabled(self.chk_clipboard.isChecked())

            self.chk_transcribe.setChecked(data.get("transcription_enabled", True))
            model_idx = self.combo_model.findText(data.get("whisper_model", "small"))
            if model_idx >= 0:
                self.combo_model.setCurrentIndex(model_idx)
            comp_idx = self.combo_compute.findText(data.get("whisper_compute_type", "int8"))
            if comp_idx >= 0:
                self.combo_compute.setCurrentIndex(comp_idx)
            self.chk_local_only.setChecked(data.get("whisper_local_only", True))
            self.input_model_path.setText(data.get("whisper_model_path", ""))
            self.input_language.setText(data.get("whisper_language", ""))

            self.hk_mic.setText(data.get("hk_mic", ""))
            self.hk_loop.setText(data.get("hk_loop", ""))
            self.hk_both.setText(data.get("hk_both", ""))
            self.hk_stop.setText(data.get("hk_stop", ""))
        except Exception:
            logger.exception("Error loading settings.")

    def save_settings(self):
        data = self.get_settings()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.settings_saved.emit()
            QMessageBox.information(self, "Settings", "Settings saved successfully.")
        except Exception as exc:
            logger.exception("Failed to save settings.")
            QMessageBox.critical(self, "Error", f"Failed to save settings: {exc}")

    def get_settings(self) -> Dict:
        return {
            "device_id": self.combo_mic.currentData(),
            "output_folder": self.lbl_folder.text() or default_output_folder(),
            "format": self.combo_fmt.currentText(),
            "tray_click_mode": self.combo_left_click.currentText(),
            "normalize": self.chk_normalize.isChecked(),
            "clipboard": self.chk_clipboard.isChecked(),
            "delete_after": self.chk_delete.isChecked(),
            "transcription_enabled": self.chk_transcribe.isChecked(),
            "whisper_model": self.combo_model.currentText(),
            "whisper_compute_type": self.combo_compute.currentText(),
            "whisper_local_only": self.chk_local_only.isChecked(),
            "whisper_model_path": self.input_model_path.text().strip(),
            "whisper_language": self.input_language.text().strip(),
            "hk_mic": self.hk_mic.text().strip(),
            "hk_loop": self.hk_loop.text().strip(),
            "hk_both": self.hk_both.text().strip(),
            "hk_stop": self.hk_stop.text().strip(),
        }


class TrayApplication(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.recorder = None
        self.last_mode = "loopback"
        self.state = "idle"

        self.signals = SignalManager()
        self.signals.recording_finished.connect(self.on_recording_finished)
        self.signals.status_changed.connect(self.on_status_changed)
        self.signals.level_changed.connect(self.on_level_changed)
        self.signals.recording_confirmed.connect(self.on_recording_confirmed)

        self.icon_idle_path = resource_path("icon_idle.png")
        self.icon_rec_path = resource_path("icon_rec.png")
        self.generate_icons()

        self.bar_window = RecorderBarWindow()
        self.bar_window.rec_clicked.connect(lambda: self.start_recording(self.last_mode))
        self.bar_window.stop_clicked.connect(self.stop_recording)
        self.bar_window.hide_clicked.connect(self.hide_bar)
        self._position_bar()
        self.bar_window.show()

        self.tray_icon = QSystemTrayIcon(QIcon(self.icon_idle_path), self.app)
        self.tray_icon.setToolTip("win rec app (Idle)")
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.build_menu()
        self.tray_icon.show()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_saved.connect(self.register_hotkeys)
        self.register_hotkeys()
        self.tray_icon.showMessage(
            "Ready",
            "Floating panel is visible. Use HIDE to keep it only in tray.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _position_bar(self):
        screen = self.app.primaryScreen().availableGeometry()
        x = screen.center().x() - (self.bar_window.width() // 2)
        y = screen.top() + 24
        self.bar_window.move(max(0, x), max(0, y))

    def _set_state(self, state: str, message: str):
        self.state = state
        self.bar_window.set_state(state, message)
        tooltip = f"win rec app ({state})"
        self.tray_icon.setToolTip(tooltip)
        if state == "recording":
            self.tray_icon.setIcon(QIcon(self.icon_rec_path))
        elif state in ("idle", "error", "warning"):
            self.tray_icon.setIcon(QIcon(self.icon_idle_path))

    def generate_icons(self):
        if not os.path.exists(self.icon_idle_path):
            pix = QPixmap(64, 64)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(QColor(80, 80, 80)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(4, 4, 56, 56)
            painter.end()
            pix.save(self.icon_idle_path)
        if not os.path.exists(self.icon_rec_path):
            pix = QPixmap(64, 64)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(QColor(220, 0, 0)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(4, 4, 56, 56)
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawEllipse(22, 22, 20, 20)
            painter.end()
            pix.save(self.icon_rec_path)

    def build_menu(self):
        self.menu = QMenu()
        self.action_show = QAction("Show Panel", self)
        self.action_show.triggered.connect(self.show_bar)
        self.action_hide = QAction("Hide Panel", self)
        self.action_hide.triggered.connect(self.hide_bar)

        self.action_record_mic = QAction("Start Recording (Mic)", self)
        self.action_record_mic.triggered.connect(lambda: self.start_recording("mic"))
        self.action_record_loop = QAction("Start Recording (Loopback)", self)
        self.action_record_loop.triggered.connect(lambda: self.start_recording("loopback"))
        self.action_record_both = QAction("Start Recording (Both)", self)
        self.action_record_both.triggered.connect(lambda: self.start_recording("both"))
        self.action_stop = QAction("Stop Recording", self)
        self.action_stop.triggered.connect(self.stop_recording)
        self.action_stop.setEnabled(False)

        self.action_settings = QAction("Settings", self)
        self.action_settings.triggered.connect(self.open_settings)
        self.action_exit = QAction("Exit", self)
        self.action_exit.triggered.connect(self.exit_app)

        self.menu.addAction(self.action_show)
        self.menu.addAction(self.action_hide)
        self.menu.addSeparator()
        self.menu.addAction(self.action_record_mic)
        self.menu.addAction(self.action_record_loop)
        self.menu.addAction(self.action_record_both)
        self.menu.addAction(self.action_stop)
        self.menu.addSeparator()
        self.menu.addAction(self.action_settings)
        self.menu.addAction(self.action_exit)
        self.tray_icon.setContextMenu(self.menu)

    def register_hotkeys(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            logger.exception("Failed to unhook old hotkeys.")

        settings = self.settings_window.get_settings()
        try:
            if settings.get("hk_mic"):
                keyboard.add_hotkey(settings["hk_mic"], lambda: self.start_recording("mic"))
            if settings.get("hk_loop"):
                keyboard.add_hotkey(settings["hk_loop"], lambda: self.start_recording("loopback"))
            if settings.get("hk_both"):
                keyboard.add_hotkey(settings["hk_both"], lambda: self.start_recording("both"))
            if settings.get("hk_stop"):
                keyboard.add_hotkey(settings["hk_stop"], self.stop_recording)
        except Exception:
            logger.exception("Failed to register hotkeys.")
            self.tray_icon.showMessage(
                "Hotkeys",
                "Failed to register one or more global hotkeys.",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )

    def show_bar(self):
        self.bar_window.show()
        self.bar_window.raise_()
        self.bar_window.activateWindow()

    def hide_bar(self):
        self.bar_window.hide()
        self.tray_icon.showMessage(
            "Recorder panel hidden",
            "Use tray menu -> Show Panel to restore.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.bar_window.isVisible():
                self.hide_bar()
            else:
                self.show_bar()

    def open_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _set_actions_for_recording(self, recording: bool):
        self.action_record_mic.setEnabled(not recording)
        self.action_record_loop.setEnabled(not recording)
        self.action_record_both.setEnabled(not recording)
        self.action_stop.setEnabled(recording)
        self.bar_window.btn_rec.setEnabled(not recording)
        self.bar_window.btn_stop.setEnabled(recording)

    def start_recording(self, mode="loopback"):
        if self.recorder and self.recorder.is_alive():
            return

        settings = self.settings_window.get_settings()
        self.last_mode = mode
        self._set_actions_for_recording(True)
        self._set_state("starting", "Starting capture...")
        self.bar_window.update_meter({"rms": 0.0, "peak": 0.0})

        def finish_callback(path, error, transcript_path):
            self.signals.recording_finished.emit(path or "", error or "", transcript_path or "")

        def status_callback(state, message):
            self.signals.status_changed.emit(state, message)

        def level_callback(metrics):
            self.signals.level_changed.emit(metrics)

        def started_callback():
            self.signals.recording_confirmed.emit()

        self.recorder = AudioRecorder(
            mic_id=settings.get("device_id"),
            source_mode=mode,
            output_folder=settings.get("output_folder", default_output_folder()),
            output_format=settings.get("format", "WAV"),
            normalize=settings.get("normalize", False),
            on_finish_callback=finish_callback,
            on_status_callback=status_callback,
            on_level_callback=level_callback,
            on_started_callback=started_callback,
            transcription_config={
                "enabled": settings.get("transcription_enabled", True),
                "model_size": settings.get("whisper_model", "small"),
                "model_path": settings.get("whisper_model_path", ""),
                "language": settings.get("whisper_language", ""),
                "compute_type": settings.get("whisper_compute_type", "int8"),
                "local_files_only": settings.get("whisper_local_only", True),
            },
        )
        self.recorder.start()

    def stop_recording(self):
        if self.recorder:
            self._set_state("stopping", "Stopping...")
            self.recorder.stop()

    def on_recording_confirmed(self):
        self._set_state("recording", f"Recording {self.last_mode}...")
        self.tray_icon.showMessage(
            "Recording started",
            f"Recording mode: {self.last_mode}",
            QSystemTrayIcon.MessageIcon.NoIcon,
            1200,
        )

    def on_status_changed(self, state, message):
        self._set_state(state, message)
        if state == "error":
            self.tray_icon.showMessage("Recording error", message, QSystemTrayIcon.MessageIcon.Critical, 4000)
        elif state == "warning":
            self.tray_icon.showMessage("Recording warning", message, QSystemTrayIcon.MessageIcon.Warning, 2500)

    def on_level_changed(self, metrics):
        self.bar_window.update_meter(metrics)

    def on_recording_finished(self, path, error, transcript_path):
        self._set_actions_for_recording(False)
        self.recorder = None

        if error:
            self._set_state("error", error)
            self.tray_icon.showMessage(
                "Error",
                f"Recording failed: {error}",
                QSystemTrayIcon.MessageIcon.Critical,
                4500,
            )
            return

        settings = self.settings_window.get_settings()
        final_path = path
        message_lines = [f"Saved: {os.path.basename(path)}"]
        if transcript_path:
            message_lines.append(f"Transcript: {os.path.basename(transcript_path)}")

        if settings.get("clipboard") and path and os.path.exists(path):
            try:
                if settings.get("delete_after"):
                    temp_dir = tempfile.gettempdir()
                    new_path = os.path.join(temp_dir, os.path.basename(path))
                    if os.path.exists(new_path):
                        base, ext = os.path.splitext(new_path)
                        new_path = f"{base}_{int(__import__('time').time())}{ext}"
                    shutil.move(path, new_path)
                    final_path = new_path
                    message_lines.append("Moved to temp folder before clipboard copy.")
                success, status = copy_file_to_clipboard(final_path)
                if success:
                    message_lines.append("Copied file to clipboard.")
                else:
                    message_lines.append(f"Clipboard error: {status}")
            except Exception as exc:
                logger.exception("Clipboard post-processing failed.")
                message_lines.append(f"Clipboard/move error: {exc}")

        self._set_state("idle", "Ready")
        self.tray_icon.showMessage(
            "Finished",
            "\n".join(message_lines),
            QSystemTrayIcon.MessageIcon.Information,
            3500,
        )

    def exit_app(self):
        if self.recorder:
            self.recorder.stop()
        self.app.quit()

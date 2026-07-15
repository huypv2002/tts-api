# -*- coding: utf-8 -*-
"""
Preview Studio — clone UI OmniVoiceOnly (tool local).

- Login account local (accounts.json)
- Gắn proxyxoay cho từng account
- Generate batch bằng fast_tts (HSW + preview anonymous) — KHÔNG tts-api server
- Bỏ Omni / Colab
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

from app_paths import app_dir, ensure_sys_path  # noqa: E402

ensure_sys_path()
_APP_DIR = app_dir()

import accounts_store as accounts  # noqa: E402
from ui.edit_mp3_tab import EditMp3Tab  # noqa: E402
from ui.multivoice_tab import MultivoiceTab  # noqa: E402
from ui.preview_tab import PreviewTab  # noqa: E402

APP_NAME = "ElevenLabs Unlimited Studio"
LOGIN_TEMP_FILE = os.path.join(_APP_DIR, "login_temp.json")
CONFIG_FILE = os.path.join(_APP_DIR, "preview_studio_config.json")


def load_config() -> dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "output_dir": os.path.join(_APP_DIR, "output"),
        "max_chars": 300,  # chunk size — max ký tự/đoạn
        "workers": 5,  # TTS threads hard max (capped by account max_workers)
        "hsw_workers": 5,
        "voice_id": "NOpBlnGInO9m6vDvFkFC",
        "lang": "en",
        "speed": 1.0,
        "advanced": {
            "gap_enabled": True,
            "gap_seconds": 1.5,
            "gap_every": 1,
            "pause_char_enabled": False,
            "char1": ",",
            "char1_sec": 0.3,
            "char2": ".",
            "char2_sec": 0.5,
        },
        "voices": [
            {
                "name": "Giọng mặc định",
                "voice_id": "NOpBlnGInO9m6vDvFkFC",
                "lang": "en",
            }
        ],
    }


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"config save: {exc}")


def _load_login_temp() -> dict:
    try:
        if os.path.exists(LOGIN_TEMP_FILE):
            with open(LOGIN_TEMP_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            encoded = data.get("password", "")
            if encoded:
                try:
                    password = base64.b64decode(encoded.encode()).decode("utf-8")
                except Exception:
                    password = encoded
            else:
                password = ""
            return {"username": data.get("username", ""), "password": password}
    except Exception as exc:
        print(f"login temp: {exc}")
    return {"username": "", "password": ""}


def _save_login_temp(username: str, password: str) -> None:
    try:
        encoded = base64.b64encode(password.encode("utf-8")).decode("utf-8")
        with open(LOGIN_TEMP_FILE, "w", encoding="utf-8") as file:
            json.dump({"username": username, "password": encoded}, file, indent=2)
    except Exception as exc:
        print(f"login temp save: {exc}")


class LoginDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        accounts.ensure_default_account()
        saved = _load_login_temp()
        self.user = None
        self.setWindowTitle("ElevenLabs Unlimited Studio — Đăng nhập")
        self.setModal(True)
        # Taller card — fixed size was too short / cramped on HiDPI
        self.setFixedSize(480, 640)
        self.setMinimumSize(480, 640)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet(
            """
            QDialog {
                background: #ffffff;
                border-radius: 16px;
                border: 1px solid #cfcfcf;
            }
            QLabel { color: #171717; font-size: 13px; background: transparent; }
            QLineEdit {
                background-color: #ffffff; border: 1px solid #c9c9c9;
                border-radius: 10px; padding: 16px 18px; color: #171717; font-size: 15px;
                min-height: 22px;
            }
            QLineEdit:focus { border: 1.5px solid #171717; }
            QPushButton {
                border-radius: 10px; padding: 14px 30px; font-size: 14px; font-weight: 600;
            }
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(44, 28, 44, 32)
        layout.setSpacing(0)

        close_layout = QtWidgets.QHBoxLayout()
        close_layout.addStretch()
        btn_close = QtWidgets.QPushButton("✕")
        btn_close.setFixedSize(36, 36)
        btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #737373; border: none; font-size: 18px; border-radius: 8px; }"
            "QPushButton:hover { color: #171717; background: #f0f0f0; }"
        )
        btn_close.clicked.connect(self.reject)
        btn_close.setAutoDefault(False)
        close_layout.addWidget(btn_close)
        layout.addLayout(close_layout)
        layout.addSpacing(8)

        icon_label = QtWidgets.QLabel("STUDIO")
        icon_label.setStyleSheet(
            "font-size: 12px; font-weight: 700; letter-spacing: 1px; color: #ffffff; background: #171717;"
            "border-radius: 16px; padding: 10px 16px;"
        )
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        icon_label.setFixedHeight(36)
        icon_label.setFixedWidth(108)
        icon_row = QtWidgets.QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(icon_label)
        icon_row.addStretch()
        layout.addLayout(icon_row)
        layout.addSpacing(18)

        title = QtWidgets.QLabel("ElevenLabs Unlimited STUDIO")
        title.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #171717; background: transparent;"
        )
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        subtitle = QtWidgets.QLabel("Đăng nhập để tiếp tục")
        subtitle.setStyleSheet("color: #737373; font-size: 13px;")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(28)

        self.ed_username = QtWidgets.QLineEdit()
        self.ed_username.setPlaceholderText("👤  Tên đăng nhập")
        self.ed_username.setMinimumHeight(56)
        # Auto-fill last login (login_temp.json) — không hardcode admin
        self.ed_username.setText(saved.get("username") or "")
        self.ed_username.setPlaceholderText("👤  Tên đăng nhập")
        layout.addWidget(self.ed_username)
        layout.addSpacing(16)

        self.ed_password = QtWidgets.QLineEdit()
        self.ed_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_password.setPlaceholderText("🔒  Mật khẩu")
        self.ed_password.setMinimumHeight(56)
        self.ed_password.setText(saved.get("password") or "")
        layout.addWidget(self.ed_password)
        layout.addSpacing(12)

        # Always reserve error line height so layout never "jumps" / clips
        self.lbl_error = QtWidgets.QLabel("")
        self.lbl_error.setStyleSheet("color: #991b1b; font-size: 12px; padding: 4px 2px;")
        self.lbl_error.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setMinimumHeight(36)
        self.lbl_error.setVisible(True)
        layout.addWidget(self.lbl_error)
        layout.addSpacing(10)

        self.bt_login = QtWidgets.QPushButton("ĐĂNG NHẬP")
        self.bt_login.setMinimumHeight(56)
        self.bt_login.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_login.setStyleSheet(
            "QPushButton { background: #171717; color: #ffffff; border: 1px solid #171717; }"
            "QPushButton:hover { background: #333333; }"
            "QPushButton:disabled { background: #e5e5e5; color: #999; }"
        )
        layout.addWidget(self.bt_login)
        self.bt_login.setDefault(True)
        layout.addSpacing(16)

        # Không self-register — chỉ admin tạo account (panel web D1 / admin local)
        self.bt_exit = QtWidgets.QPushButton("THOÁT")
        self.bt_exit.setMinimumHeight(48)
        self.bt_exit.setStyleSheet(
            "QPushButton { background: #ffffff; color: #171717; border: 1px solid #c9c9c9; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        layout.addWidget(self.bt_exit)
        layout.addSpacing(18)
        layout.addStretch(1)

        footer = QtWidgets.QLabel("© 2026 ElevenLabs Unlimited Studio · tài khoản do admin cấp")
        footer.setStyleSheet("color: #a3a3a3; font-size: 11px; padding-top: 4px;")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(footer)

        self.bt_login.clicked.connect(self._login)
        self.bt_exit.clicked.connect(self.reject)
        self.ed_password.returnPressed.connect(self._login)
        self._center()

    def _center(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            return
        g = screen.geometry()
        self.move((g.width() - self.width()) // 2, (g.height() - self.height()) // 2)

    def _show_error(self, message: str):
        self.lbl_error.setStyleSheet("color: #991b1b; font-size: 12px; padding: 4px 2px;")
        self.lbl_error.setText(f"❌ {message}" if message else "")
        self.lbl_error.setVisible(True)

    def _login(self):
        self.lbl_error.setText("")
        u = self.ed_username.text().strip()
        p = self.ed_password.text()
        if not u or not p:
            self._show_error("Hãy nhập tên đăng nhập và mật khẩu!")
            return
        self.bt_login.setEnabled(False)
        self.bt_login.setText("Đang đăng nhập…")
        QtWidgets.QApplication.processEvents()
        try:
            row = accounts.authenticate(u, p)
            if not row:
                self._show_error(
                    "Sai tên đăng nhập hoặc mật khẩu!\n"
                    "(Tài khoản do admin cấp trên web — kiểm tra lại user/pass)"
                )
                return
            self.user = row
            _save_login_temp(u, p)
            self.accept()
        except Exception as exc:
            msg = str(exc)
            if (
                "không kết nối" in msg
                or "auth server" in msg
                or "timed out" in msg.lower()
            ):
                self._show_error(f"Lỗi mạng: {msg[:100]}")
            else:
                self._show_error(msg[:120])
        finally:
            self.bt_login.setEnabled(True)
            self.bt_login.setText("ĐĂNG NHẬP")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, user: dict):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1260, 820)
        self.setMinimumSize(1040, 680)
        self.user = user
        # Tab: TTS generate + Edit MP3 (ffmpeg copy)
        cfg = load_config()
        out_dir = cfg.get("output_dir") or os.path.join(_APP_DIR, "output")
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            """
            QTabWidget::pane { border: 0; background: #f2f2f2; }
            QTabBar::tab {
                background: #e8e8e8; color: #525252; padding: 8px 16px;
                margin-right: 2px; border-top-left-radius: 8px;
                border-top-right-radius: 8px; font-weight: 600;
            }
            QTabBar::tab:selected { background: #ffffff; color: #171717; }
            """
        )
        self._gen = PreviewTab(self, user, load_config, save_config)
        self._multi = MultivoiceTab(self, user, load_config, save_config)
        self._edit = EditMp3Tab(self, default_dir=out_dir)
        self._tabs.addTab(self._gen, "Tạo audio")
        self._tabs.addTab(self._multi, "Hội thoại")
        self._tabs.addTab(self._edit, "Edit MP3")
        self.setCentralWidget(self._tabs)
        self.setStyleSheet("QMainWindow { background: #f2f2f2; }")

    def show_tts_tab(self):
        self._tabs.setCurrentWidget(self._gen)

    def show_multivoice_tab(self):
        self._tabs.setCurrentWidget(self._multi)

    def show_edit_mp3_tab(self, paths: list | None = None):
        if paths:
            self._edit.open_with_files(paths)
        # refresh default out from TTS config
        try:
            cfg = load_config()
            d = cfg.get("output_dir") or ""
            if d:
                self._edit._default_dir = d
        except Exception:
            pass
        self._tabs.setCurrentWidget(self._edit)

    def closeEvent(self, event: QtGui.QCloseEvent):
        try:
            self._gen.cleanup()
        except Exception:
            pass
        try:
            self._multi.cleanup()
        except Exception:
            pass
        event.accept()


def main() -> int:
    try:
        app = QtWidgets.QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        login = LoginDialog()
        if login.exec() != QtWidgets.QDialog.Accepted or not login.user:
            return 0
        window = MainWindow(login.user)
        window.show()
        return app.exec()
    except Exception:
        crash = os.path.join(_APP_DIR, "preview_studio_crash.log")
        with open(crash, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        try:
            QtWidgets.QMessageBox.critical(None, "Lỗi", f"Crash log:\n{crash}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())

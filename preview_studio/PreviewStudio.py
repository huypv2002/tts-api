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

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import accounts_store as accounts  # noqa: E402
from ui.preview_tab import PreviewTab  # noqa: E402

APP_NAME = "HuyViet Preview Studio"
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
        "max_chars": 900,
        "workers": 2,
        "hsw_workers": 2,
        "voice_id": "NOpBlnGInO9m6vDvFkFC",
        "lang": "en",
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
        self.setWindowTitle("HuyViet Preview Studio - Đăng nhập")
        self.setModal(True)
        self.setFixedSize(450, 480)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.FramelessWindowHint)
        self.setStyleSheet(
            """
            QDialog {
                background: #ffffff;
                border-radius: 12px;
                border: 1px solid #cfcfcf;
            }
            QLabel { color: #171717; font-size: 13px; background: transparent; }
            QLineEdit {
                background-color: #ffffff; border: 1px solid #c9c9c9;
                border-radius: 7px; padding: 14px 18px; color: #171717; font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #171717; }
            QPushButton {
                border-radius: 7px; padding: 14px 30px; font-size: 14px; font-weight: 500;
            }
            """
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(40, 25, 40, 30)
        layout.setSpacing(0)

        close_layout = QtWidgets.QHBoxLayout()
        close_layout.addStretch()
        btn_close = QtWidgets.QPushButton("✕")
        btn_close.setFixedSize(32, 32)
        btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #737373; border: none; font-size: 18px; }"
            "QPushButton:hover { color: #171717; background: #f0f0f0; }"
        )
        btn_close.clicked.connect(self.reject)
        btn_close.setAutoDefault(False)
        close_layout.addWidget(btn_close)
        layout.addLayout(close_layout)
        layout.addSpacing(10)

        icon_label = QtWidgets.QLabel("PREVIEW")
        icon_label.setStyleSheet(
            "font-size: 11px; color: #ffffff; background: #171717;"
            "border-radius: 14px; padding: 7px 12px;"
        )
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        icon_label.setFixedWidth(88)
        icon_row = QtWidgets.QHBoxLayout()
        icon_row.addStretch()
        icon_row.addWidget(icon_label)
        icon_row.addStretch()
        layout.addLayout(icon_row)
        layout.addSpacing(12)

        title = QtWidgets.QLabel("HUYVIET PREVIEW STUDIO")
        title.setStyleSheet(
            "font-size: 18px; font-weight: 600; color: #171717; background: transparent;"
        )
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(6)

        subtitle = QtWidgets.QLabel("Đăng nhập để tiếp tục")
        subtitle.setStyleSheet("color: #737373; font-size: 12px;")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        self.ed_username = QtWidgets.QLineEdit()
        self.ed_username.setPlaceholderText("👤  Tên đăng nhập")
        self.ed_username.setMinimumHeight(50)
        self.ed_username.setText(saved.get("username") or "admin")
        layout.addWidget(self.ed_username)
        layout.addSpacing(14)

        self.ed_password = QtWidgets.QLineEdit()
        self.ed_password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_password.setPlaceholderText("🔒  Mật khẩu")
        self.ed_password.setMinimumHeight(50)
        self.ed_password.setText(saved.get("password") or "admin123")
        layout.addWidget(self.ed_password)
        layout.addSpacing(10)

        self.lbl_error = QtWidgets.QLabel("")
        self.lbl_error.setStyleSheet("color: #991b1b; font-size: 12px; padding: 5px;")
        self.lbl_error.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setVisible(False)
        layout.addWidget(self.lbl_error)
        layout.addSpacing(12)

        self.bt_login = QtWidgets.QPushButton("ĐĂNG NHẬP")
        self.bt_login.setMinimumHeight(52)
        self.bt_login.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_login.setStyleSheet(
            "QPushButton { background: #171717; color: #ffffff; border: 1px solid #171717; }"
            "QPushButton:hover { background: #333333; }"
            "QPushButton:disabled { background: #e5e5e5; color: #999; }"
        )
        layout.addWidget(self.bt_login)
        self.bt_login.setDefault(True)
        layout.addSpacing(12)

        self.bt_register = QtWidgets.QPushButton("Tạo account mới")
        self.bt_register.setMinimumHeight(40)
        self.bt_register.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_register.setStyleSheet(
            "QPushButton { background: #ffffff; color: #171717; border: 1px solid #c9c9c9; }"
            "QPushButton:hover { background: #f0f0f0; border: 1px solid #171717; }"
        )
        layout.addWidget(self.bt_register)
        layout.addSpacing(10)

        self.bt_exit = QtWidgets.QPushButton("THOÁT")
        self.bt_exit.setMinimumHeight(42)
        self.bt_exit.setStyleSheet(
            "QPushButton { background: #ffffff; color: #171717; border: 1px solid #c9c9c9; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        layout.addWidget(self.bt_exit)
        layout.addStretch()

        footer = QtWidgets.QLabel("© 2026 Preview Studio · local tool")
        footer.setStyleSheet("color: #a3a3a3; font-size: 11px;")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(footer)

        self.bt_login.clicked.connect(self._login)
        self.bt_register.clicked.connect(self._register)
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
        self.lbl_error.setText(f"❌ {message}")
        self.lbl_error.setVisible(True)

    def _login(self):
        self.lbl_error.setVisible(False)
        u = self.ed_username.text().strip()
        p = self.ed_password.text()
        if not u or not p:
            self._show_error("Nhập username / password!")
            return
        self.bt_login.setEnabled(False)
        self.bt_login.setText("Đang đăng nhập...")
        QtWidgets.QApplication.processEvents()
        try:
            row = accounts.authenticate(u, p)
            if not row:
                self._show_error("Sai tên đăng nhập hoặc mật khẩu!")
                return
            self.user = row
            _save_login_temp(u, p)
            self.accept()
        except Exception as exc:
            self._show_error(str(exc)[:80])
        finally:
            self.bt_login.setEnabled(True)
            self.bt_login.setText("ĐĂNG NHẬP")

    def _register(self):
        u = self.ed_username.text().strip()
        p = self.ed_password.text()
        if not u or not p:
            self._show_error("Nhập username + password để tạo account!")
            return
        try:
            accounts.create_account(u, p)
            self._show_error("")  # clear
            self.lbl_error.setStyleSheet("color: #166534; font-size: 12px;")
            self.lbl_error.setText(f"✅ Đã tạo account '{u}' — bấm ĐĂNG NHẬP")
            self.lbl_error.setVisible(True)
        except Exception as exc:
            self.lbl_error.setStyleSheet("color: #991b1b; font-size: 12px;")
            self._show_error(str(exc)[:80])

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
        # Admin account/proxy/gói ký tự → web https://tts-origin.liveyt.pro/admin/
        # Tool desktop chỉ Generate TTS
        self._gen = PreviewTab(self, user, load_config, save_config)
        self.setCentralWidget(self._gen)
        self.setStyleSheet("QMainWindow { background: #f2f2f2; }")

    def closeEvent(self, event: QtGui.QCloseEvent):
        try:
            self._gen.cleanup()
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

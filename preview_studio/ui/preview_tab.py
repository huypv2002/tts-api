# -*- coding: utf-8 -*-
"""Main tab — UI clone OmniVoiceTab, engine = tts-api preview (no Omni)."""
from __future__ import annotations

import os
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QHeaderView

from client.tts_api_client import TtsApiClient

DEFAULT_VOICE = "NOpBlnGInO9m6vDvFkFC"
DEFAULT_MODEL = "eleven_v3"


def split_chunks(text: str, max_chars: int) -> List[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    out: List[str] = []
    cur: List[str] = []
    for w in words:
        trial = (" ".join(cur + [w])).strip()
        if len(trial) > max_chars and cur:
            out.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


class BatchWorker(QThread):
    log = Signal(str)
    row_started = Signal(int)
    row_done = Signal(int, bool, str, str)  # row, ok, path, err
    file_progress = Signal(int, int, int)
    finished = Signal(int, int)  # ok, fail

    def __init__(
        self,
        client: TtsApiClient,
        chunks: List[dict],
        output_dir: str,
        voice: str,
        lang: str,
        model: str,
        workers: int = 2,
    ):
        super().__init__()
        self.client = client
        self.chunks = chunks
        self.output_dir = output_dir
        self.voice = voice
        self.lang = lang
        self.model = model
        self.workers = max(1, min(6, int(workers or 1)))
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        ok = fail = 0
        os.makedirs(self.output_dir, exist_ok=True)

        def one(row: int, ch: dict):
            if self._stop:
                return row, False, "", "stopped"
            self.row_started.emit(row)
            text = ch.get("text") or ""
            src = Path(ch.get("file") or "chunk").stem
            out = os.path.join(
                self.output_dir, f"{row+1:04d}_{src}_{len(text)}c.mp3"
            )
            try:
                self.client.synthesize_to_file(
                    text=text,
                    out_path=out,
                    lang=self.lang,
                    voice=self.voice or None,
                    model=self.model or None,
                )
                return row, True, out, ""
            except Exception as e:
                return row, False, "", str(e)[:200]

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {
                ex.submit(one, i, self.chunks[i]): i for i in range(len(self.chunks))
            }
            done_n = 0
            for fut in as_completed(futs):
                if self._stop:
                    break
                row, success, path, err = fut.result()
                done_n += 1
                if success:
                    ok += 1
                    self.row_done.emit(row, True, path, "")
                    self.log.emit(f"✅ #{row+1} → {os.path.basename(path)}")
                else:
                    fail += 1
                    self.row_done.emit(row, False, "", err)
                    self.log.emit(f"❌ #{row+1} {err}")
                self.file_progress.emit(0, done_n, len(self.chunks))
        self.finished.emit(ok, fail)


class PreviewTab(QtWidgets.QWidget):
    def __init__(
        self,
        main_window,
        client: TtsApiClient,
        user: dict,
        load_config: Callable,
        save_config: Callable,
    ):
        super().__init__()
        self.main_window = main_window
        self.client = client
        self.user = user
        self.load_config = load_config
        self.save_config = save_config
        self._cfg = load_config()
        self._sources: List[dict] = []
        self._chunks: List[dict] = []
        self._batch: Optional[BatchWorker] = None
        self._setup_ui()
        self._load_cfg()
        self._refresh_account_badge()

    def _setup_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(body)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self.setStyleSheet(
            """
            QWidget { color: #171717; font-size: 12px; }
            QScrollArea { background: #f2f2f2; border: none; }
            QFrame#card {
                background: #ffffff; border: 1px solid #d4d4d4; border-radius: 8px;
            }
            QLabel#cardTitle { color: #171717; font-size: 13px; font-weight: 500; }
            QLabel#badge {
                background: #f2f2f2; color: #262626; border: 1px solid #d4d4d4;
                border-radius: 7px; padding: 4px 8px; font-size: 11px;
            }
            QLabel#muted { color: #737373; font-size: 11px; }
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
                background: #ffffff; border: 1px solid #c9c9c9; border-radius: 6px;
                padding: 5px 8px; color: #171717;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
                border: 1px solid #171717;
            }
            QPushButton {
                min-height: 26px; padding: 4px 10px; color: #171717;
                background: #ffffff; border: 1px solid #c9c9c9; border-radius: 6px;
            }
            QPushButton:hover { background: #f0f0f0; border-color: #171717; }
            QPushButton#primaryButton {
                color: #ffffff; background: #171717; border-color: #171717;
            }
            QPushButton#primaryButton:hover { background: #333333; }
            QPushButton#dangerButton:hover {
                color: #ffffff; background: #171717; border-color: #171717;
            }
            QTableWidget {
                background: #ffffff; alternate-background-color: #f7f7f7;
                border: 1px solid #d4d4d4; border-radius: 7px; gridline-color: #e8e8e8;
            }
            QHeaderView::section {
                background: #eeeeee; color: #404040; border: none;
                border-right: 1px solid #d4d4d4; border-bottom: 1px solid #d4d4d4;
                padding: 6px 5px;
            }
            QProgressBar {
                background: #ededed; border: 1px solid #d0d0d0; border-radius: 4px;
                height: 10px; text-align: center;
            }
            QProgressBar::chunk { background: #171717; border-radius: 3px; }
            QToolButton#settingsButton {
                min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
                color: #404040; background: #ffffff; border: 1px solid #c9c9c9;
                border-radius: 5px; font-size: 15px;
            }
            QToolButton#settingsButton:hover {
                color: #ffffff; background: #171717; border-color: #171717;
            }
            """
        )

        def card(title: str):
            frame = QtWidgets.QFrame()
            frame.setObjectName("card")
            layout = QtWidgets.QVBoxLayout(frame)
            layout.setContentsMargins(12, 10, 12, 12)
            layout.setSpacing(8)
            label = QtWidgets.QLabel(title)
            label.setObjectName("cardTitle")
            layout.addWidget(label)
            return frame, layout

        # ── Settings dialog ──
        self.settings_dialog = QtWidgets.QDialog(self)
        self.settings_dialog.setWindowTitle("Cài đặt · Account + Proxyxoay")
        self.settings_dialog.setModal(False)
        self.settings_dialog.resize(480, 520)
        sl = QtWidgets.QVBoxLayout(self.settings_dialog)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(8)

        t1 = QtWidgets.QLabel("Tài khoản tts-api")
        t1.setObjectName("cardTitle")
        sl.addWidget(t1)
        self.lbl_login_status = QtWidgets.QLabel("—")
        self.lbl_login_status.setObjectName("badge")
        sl.addWidget(self.lbl_login_status)

        sl.addWidget(QtWidgets.QLabel("Server URL"))
        self.ed_base = QtWidgets.QLineEdit()
        sl.addWidget(self.ed_base)
        sl.addWidget(QtWidgets.QLabel("API Key (account)"))
        self.ed_api_key = QtWidgets.QLineEdit()
        self.ed_api_key.setEchoMode(QtWidgets.QLineEdit.Password)
        sl.addWidget(self.ed_api_key)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sl.addWidget(sep)

        t2 = QtWidgets.QLabel("Gắn Proxyxoay cho account (admin)")
        t2.setObjectName("cardTitle")
        sl.addWidget(t2)
        tip = QtWidgets.QLabel(
            "Dùng admin password để ghi proxy vào API key trên server.\n"
            "Worker sẽ ưu tiên proxy riêng của key này."
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        sl.addWidget(tip)

        self.ed_admin_pw = QtWidgets.QLineEdit()
        self.ed_admin_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_admin_pw.setPlaceholderText("Admin password tts-api")
        sl.addWidget(self.ed_admin_pw)

        self.ed_px_key = QtWidgets.QLineEdit()
        self.ed_px_key.setPlaceholderText("Proxyxoay API key")
        sl.addWidget(self.ed_px_key)
        row_u = QtWidgets.QHBoxLayout()
        self.ed_px_user = QtWidgets.QLineEdit()
        self.ed_px_user.setPlaceholderText("username")
        self.ed_px_pass = QtWidgets.QLineEdit()
        self.ed_px_pass.setPlaceholderText("password")
        self.ed_px_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        row_u.addWidget(self.ed_px_user)
        row_u.addWidget(self.ed_px_pass)
        sl.addLayout(row_u)
        row_h = QtWidgets.QHBoxLayout()
        self.ed_px_host = QtWidgets.QLineEdit()
        self.ed_px_host.setPlaceholderText("host e.g. vipvn7.proxyxoay.net")
        self.ed_px_port = QtWidgets.QSpinBox()
        self.ed_px_port.setRange(1, 65535)
        self.ed_px_port.setValue(8978)
        row_h.addWidget(self.ed_px_host, 2)
        row_h.addWidget(self.ed_px_port, 1)
        sl.addLayout(row_h)
        self.ed_px_label = QtWidgets.QLineEdit()
        self.ed_px_label.setPlaceholderText("Nhãn proxy (tuỳ chọn)")
        sl.addWidget(self.ed_px_label)

        btn_row = QtWidgets.QHBoxLayout()
        self.bt_save_account = QtWidgets.QPushButton("Lưu account + proxy")
        self.bt_save_account.setObjectName("primaryButton")
        self.bt_test_me = QtWidgets.QPushButton("Test /me")
        btn_row.addWidget(self.bt_save_account)
        btn_row.addWidget(self.bt_test_me)
        sl.addLayout(btn_row)
        self.lbl_settings_msg = QtWidgets.QLabel("")
        self.lbl_settings_msg.setObjectName("muted")
        self.lbl_settings_msg.setWordWrap(True)
        sl.addWidget(self.lbl_settings_msg)
        sl.addStretch(1)

        # ── Main layout ──
        content = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)

        voice_card, voice_l = card("Giọng nói · Preview ElevenLabs")
        top_bar = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel("Preview TTS (tts-api)")
        badge.setObjectName("badge")
        top_bar.addWidget(badge)
        top_bar.addStretch(1)
        self.bt_settings = QtWidgets.QToolButton()
        self.bt_settings.setObjectName("settingsButton")
        self.bt_settings.setText("⚙")
        self.bt_settings.setToolTip("Cài đặt account / proxyxoay")
        top_bar.addWidget(self.bt_settings)
        voice_l.addLayout(top_bar)

        self.ed_voice_id = QtWidgets.QLineEdit()
        self.ed_voice_id.setPlaceholderText("Voice ID")
        self.ed_voice_id.setText(DEFAULT_VOICE)
        self.ed_lang = QtWidgets.QLineEdit()
        self.ed_lang.setPlaceholderText("lang (en/vi/...)")
        self.ed_lang.setText("en")
        self.ed_lang.setMaximumWidth(80)
        vr = QtWidgets.QHBoxLayout()
        vr.addWidget(self.ed_voice_id, 3)
        vr.addWidget(self.ed_lang, 1)
        voice_l.addLayout(vr)
        self.lbl_account = QtWidgets.QLabel("")
        self.lbl_account.setObjectName("muted")
        voice_l.addWidget(self.lbl_account)
        left.addWidget(voice_card)

        source_card, source_l = card("Nội dung")
        src_top = QtWidgets.QHBoxLayout()
        self.ed_input_path = QtWidgets.QLineEdit()
        self.ed_input_path.setReadOnly(True)
        self.ed_input_path.setPlaceholderText("Chọn TXT / Thư mục / SRT")
        self.bt_txt = QtWidgets.QPushButton("TXT")
        self.bt_folder = QtWidgets.QPushButton("Thư mục")
        self.bt_srt = QtWidgets.QPushButton("SRT")
        src_top.addWidget(self.ed_input_path, 1)
        src_top.addWidget(self.bt_txt)
        src_top.addWidget(self.bt_folder)
        src_top.addWidget(self.bt_srt)
        source_l.addLayout(src_top)
        opt = QtWidgets.QHBoxLayout()
        opt.addWidget(QtWidgets.QLabel("Tối đa mỗi đoạn"))
        self.sb_max_chars = QtWidgets.QSpinBox()
        self.sb_max_chars.setRange(100, 1000)
        self.sb_max_chars.setSingleStep(50)
        self.sb_max_chars.setValue(900)
        opt.addWidget(self.sb_max_chars)
        opt.addWidget(QtWidgets.QLabel("ký tự"))
        opt.addSpacing(10)
        opt.addWidget(QtWidgets.QLabel("Luồng"))
        self.sb_workers = QtWidgets.QSpinBox()
        self.sb_workers.setRange(1, 6)
        self.sb_workers.setValue(2)
        opt.addWidget(self.sb_workers)
        opt.addStretch(1)
        self.lbl_chunk_summary = QtWidgets.QLabel("0 đoạn / 0 chunk")
        self.lbl_chunk_summary.setObjectName("badge")
        opt.addWidget(self.lbl_chunk_summary)
        source_l.addLayout(opt)
        self.ed_text = QtWidgets.QPlainTextEdit()
        self.ed_text.setReadOnly(True)
        self.ed_text.setPlaceholderText("Nội dung file sẽ hiển thị tại đây...")
        self.ed_text.setMinimumHeight(110)
        source_l.addWidget(self.ed_text, 1)
        left.addWidget(source_card, 1)

        out_card, out_l = card("Xuất file")
        orow = QtWidgets.QHBoxLayout()
        self.ed_output_dir = QtWidgets.QLineEdit()
        self.bt_browse_out = QtWidgets.QPushButton("...")
        orow.addWidget(self.ed_output_dir, 1)
        orow.addWidget(self.bt_browse_out)
        out_l.addLayout(orow)
        run = QtWidgets.QHBoxLayout()
        self.bt_start = QtWidgets.QPushButton("Bắt đầu")
        self.bt_stop = QtWidgets.QPushButton("Dừng")
        self.bt_output = QtWidgets.QPushButton("Mở thư mục")
        self.bt_start.setObjectName("primaryButton")
        self.bt_stop.setObjectName("dangerButton")
        self.lbl_result = QtWidgets.QLabel("Kết quả: 0/0")
        self.lbl_result.setObjectName("badge")
        run.addWidget(self.bt_start)
        run.addWidget(self.bt_stop)
        run.addWidget(self.bt_output)
        run.addStretch(1)
        run.addWidget(self.lbl_result)
        out_l.addLayout(run)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        out_l.addWidget(self.progress)
        left.addWidget(out_card)

        queue_card, queue_l = card("File đang chờ")
        self.tbl_queue = QtWidgets.QTableWidget(0, 4)
        self.tbl_queue.setHorizontalHeaderLabels(["STT", "Tệp", "Trạng thái", "Tiến độ"])
        self.tbl_queue.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_queue.verticalHeader().setVisible(False)
        qh = self.tbl_queue.horizontalHeader()
        qh.setSectionResizeMode(0, QHeaderView.Fixed)
        qh.setSectionResizeMode(1, QHeaderView.Stretch)
        qh.setSectionResizeMode(2, QHeaderView.Fixed)
        qh.setSectionResizeMode(3, QHeaderView.Fixed)
        self.tbl_queue.setColumnWidth(0, 52)
        self.tbl_queue.setColumnWidth(2, 105)
        self.tbl_queue.setColumnWidth(3, 90)
        self.tbl_queue.setAlternatingRowColors(True)
        self.tbl_queue.setMaximumHeight(150)
        queue_l.addWidget(self.tbl_queue)
        right.addWidget(queue_card)

        chunks_card, chunks_l = card("Danh sách đoạn")
        self.tbl_sub = QtWidgets.QTableWidget(0, 6)
        self.tbl_sub.setHorizontalHeaderLabels(
            ["STT", "Tệp", "Thời lượng", "Ký tự", "Nội dung", "Trạng thái"]
        )
        self.tbl_sub.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_sub.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_sub.verticalHeader().setVisible(False)
        h = self.tbl_sub.horizontalHeader()
        for c in (0, 1, 2, 3, 5):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_sub.setColumnWidth(0, 44)
        self.tbl_sub.setColumnWidth(1, 100)
        self.tbl_sub.setColumnWidth(2, 78)
        self.tbl_sub.setColumnWidth(3, 50)
        self.tbl_sub.setColumnWidth(5, 82)
        self.tbl_sub.setAlternatingRowColors(True)
        chunks_l.addWidget(self.tbl_sub)
        right.addWidget(chunks_card, 1)

        content.addLayout(left, 38)
        content.addLayout(right, 62)
        root.addLayout(content, 1)
        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("muted")
        root.addWidget(self._status)

        self.bt_stop.setEnabled(False)
        self.bt_settings.clicked.connect(self._open_settings)
        self.bt_save_account.clicked.connect(self._save_account_proxy)
        self.bt_test_me.clicked.connect(self._test_me)
        self.bt_txt.clicked.connect(self._pick_txt)
        self.bt_folder.clicked.connect(self._pick_folder)
        self.bt_srt.clicked.connect(self._pick_srt)
        self.sb_max_chars.valueChanged.connect(self._rebuild_chunks)
        self.bt_browse_out.clicked.connect(self._browse_out)
        self.bt_start.clicked.connect(self._start)
        self.bt_stop.clicked.connect(self._stop)
        self.bt_output.clicked.connect(self._open_out)
        self.tbl_sub.cellDoubleClicked.connect(self._open_chunk)

    def _load_cfg(self):
        c = self._cfg
        self.ed_base.setText(c.get("base_url") or self.client.base_url)
        self.ed_api_key.setText(c.get("api_key") or self.client.api_key)
        self.ed_output_dir.setText(
            c.get("output_dir")
            or os.path.join(os.path.dirname(__file__), "..", "output")
        )
        self.sb_max_chars.setValue(int(c.get("max_chars") or 900))
        self.sb_workers.setValue(int(c.get("workers") or 2))
        self.ed_voice_id.setText(c.get("voice_id") or DEFAULT_VOICE)
        self.ed_lang.setText(c.get("lang") or "en")
        self.ed_admin_pw.setText(c.get("admin_password") or "")
        self.ed_px_key.setText(c.get("proxy_api_key") or "")
        self.ed_px_user.setText(c.get("proxy_username") or "")
        self.ed_px_pass.setText(c.get("proxy_password") or "")
        self.ed_px_host.setText(c.get("proxy_host") or "")
        if c.get("proxy_port"):
            self.ed_px_port.setValue(int(c["proxy_port"]))
        self.ed_px_label.setText(c.get("proxy_label") or "")

    def _persist_cfg(self):
        c = self.load_config()
        c.update(
            {
                "base_url": self.ed_base.text().strip(),
                "api_key": self.ed_api_key.text().strip(),
                "output_dir": self.ed_output_dir.text().strip(),
                "max_chars": self.sb_max_chars.value(),
                "workers": self.sb_workers.value(),
                "voice_id": self.ed_voice_id.text().strip(),
                "lang": self.ed_lang.text().strip() or "en",
                "admin_password": self.ed_admin_pw.text(),
                "proxy_api_key": self.ed_px_key.text().strip(),
                "proxy_username": self.ed_px_user.text().strip(),
                "proxy_password": self.ed_px_pass.text(),
                "proxy_host": self.ed_px_host.text().strip(),
                "proxy_port": self.ed_px_port.value(),
                "proxy_label": self.ed_px_label.text().strip(),
            }
        )
        self.save_config(c)
        self._cfg = c

    def _refresh_account_badge(self):
        me = self.user.get("me") or {}
        name = me.get("name") or self.user.get("username")
        px = "có proxy" if me.get("has_proxy") else "chưa gắn proxy"
        self.lbl_login_status.setText(f"Account: {name} · {px}")
        self.lbl_account.setText(
            f"Logged in · {name} · max_chars={me.get('max_chars')} · "
            f"quota_jobs={me.get('jobs_used_day')}/{me.get('quota_jobs_day')} · {px}"
        )

    def _open_settings(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()

    def _test_me(self):
        try:
            self.client.base_url = self.ed_base.text().strip().rstrip("/")
            self.client.api_key = self.ed_api_key.text().strip()
            me = self.client.me()
            self.user["me"] = me
            self._refresh_account_badge()
            self.lbl_settings_msg.setText(f"OK /me: {me}")
        except Exception as e:
            self.lbl_settings_msg.setText(f"Lỗi: {e}")

    def _save_account_proxy(self):
        """Admin login + PATCH api key with proxyxoay binding."""
        self._persist_cfg()
        base = self.ed_base.text().strip().rstrip("/")
        api_key = self.ed_api_key.text().strip()
        admin_pw = self.ed_admin_pw.text().strip()
        if not admin_pw:
            self.lbl_settings_msg.setText("Cần admin password để gắn proxy lên server.")
            return
        try:
            admin = TtsApiClient(base)
            admin.admin_login(admin_pw)
            keys = admin.list_keys()
            # find key by matching prefix of our api_key
            target = None
            for k in keys:
                pref = (k.get("key_prefix") or "").replace("…", "").replace("...", "")
                if api_key.startswith(pref.rstrip("…")) or pref in api_key:
                    target = k
                    break
            # fallback: match by me id
            if not target and self.user.get("id"):
                for k in keys:
                    if k.get("id") == self.user.get("id"):
                        target = k
                        break
            if not target:
                self.lbl_settings_msg.setText(
                    "Không tìm thấy API key trên server (admin). "
                    "Kiểm tra key / admin password."
                )
                return
            body = {
                "proxy_provider": "proxyxoay_net",
                "proxy_api_key": self.ed_px_key.text().strip(),
                "proxy_username": self.ed_px_user.text().strip(),
                "proxy_password": self.ed_px_pass.text(),
                "proxy_host": self.ed_px_host.text().strip(),
                "proxy_port": self.ed_px_port.value(),
                "proxy_label": self.ed_px_label.text().strip()
                or f"px-{target.get('name')}",
            }
            # also upsert into global pool for redundancy
            if body["proxy_host"] and body["proxy_username"]:
                try:
                    admin.upsert_proxy(
                        {
                            "id": f"key{target['id']}",
                            "label": body["proxy_label"],
                            "enabled": True,
                            "provider": "proxyxoay_net",
                            "api_key": body["proxy_api_key"],
                            "username": body["proxy_username"],
                            "password": body["proxy_password"],
                            "host": body["proxy_host"],
                            "port": body["proxy_port"],
                        }
                    )
                except Exception as e:
                    self.lbl_settings_msg.setText(f"Pool upsert warn: {e}")

            updated = admin.patch_key(int(target["id"]), **body)
            self.client.base_url = base
            self.client.api_key = api_key
            me = self.client.me()
            self.user["me"] = me
            self._refresh_account_badge()
            self.lbl_settings_msg.setText(
                f"✅ Đã gắn proxy cho key id={target['id']} "
                f"host={updated.get('proxy_host')} port={updated.get('proxy_port')}"
            )
        except Exception as e:
            self.lbl_settings_msg.setText(f"Lỗi lưu: {e}\n{traceback.format_exc()[:200]}")

    def _set_status(self, text: str):
        self._status.setText(text)

    def _pick_txt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Chọn TXT", "", "Text (*.txt);;All (*.*)"
        )
        if path:
            self._load_paths([path], "txt")

    def _pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục TXT")
        if d:
            paths = sorted(
                str(p)
                for p in Path(d).rglob("*.txt")
                if p.is_file()
            )
            self._load_paths(paths, "folder")

    def _pick_srt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Chọn SRT", "", "SRT (*.srt);;All (*.*)"
        )
        if path:
            self._load_paths([path], "srt")

    def _load_paths(self, paths: List[str], source_type: str):
        self._sources = []
        for p in paths:
            try:
                if p.lower().endswith(".srt"):
                    text = self._srt_to_text(p)
                else:
                    text = Path(p).read_text(encoding="utf-8", errors="ignore")
                self._sources.append(
                    {"file": os.path.basename(p), "path": p, "text": text}
                )
            except Exception as e:
                self._set_status(f"Lỗi đọc {p}: {e}")
        self.ed_input_path.setText(
            paths[0] if len(paths) == 1 else f"{len(paths)} files ({source_type})"
        )
        preview = "\n\n".join(
            f"--- {s['file']} ---\n{s['text'][:800]}" for s in self._sources[:3]
        )
        self.ed_text.setPlainText(preview)
        self._rebuild_chunks()
        self._rebuild_queue_table()

    def _srt_to_text(self, path: str) -> str:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.isdigit() or "-->" in line:
                continue
            lines.append(line)
        return " ".join(lines)

    def _rebuild_chunks(self):
        max_c = self.sb_max_chars.value()
        self._chunks = []
        for s in self._sources:
            parts = split_chunks(s["text"], max_c)
            for i, t in enumerate(parts):
                self._chunks.append(
                    {
                        "file": s["file"],
                        "part": i + 1,
                        "text": t,
                        "path": None,
                    }
                )
        self.lbl_chunk_summary.setText(
            f"{len(self._sources)} đoạn / {len(self._chunks)} chunk"
        )
        self.tbl_sub.setRowCount(0)
        self.tbl_sub.setRowCount(len(self._chunks))
        for i, ch in enumerate(self._chunks):
            self.tbl_sub.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_sub.setItem(i, 1, QtWidgets.QTableWidgetItem(ch["file"][:40]))
            self.tbl_sub.setItem(i, 2, QtWidgets.QTableWidgetItem("-"))
            self.tbl_sub.setItem(i, 3, QtWidgets.QTableWidgetItem(str(len(ch["text"]))))
            prev = ch["text"][:80] + ("…" if len(ch["text"]) > 80 else "")
            self.tbl_sub.setItem(i, 4, QtWidgets.QTableWidgetItem(prev))
            self.tbl_sub.setItem(i, 5, QtWidgets.QTableWidgetItem("chờ"))
        self._persist_cfg()

    def _rebuild_queue_table(self):
        self.tbl_queue.setRowCount(0)
        self.tbl_queue.setRowCount(len(self._sources))
        for i, s in enumerate(self._sources):
            self.tbl_queue.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_queue.setItem(i, 1, QtWidgets.QTableWidgetItem(s["file"]))
            self.tbl_queue.setItem(i, 2, QtWidgets.QTableWidgetItem("chờ"))
            self.tbl_queue.setItem(i, 3, QtWidgets.QTableWidgetItem("0%"))

    def _browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Thư mục xuất")
        if d:
            self.ed_output_dir.setText(d)
            self._persist_cfg()

    def _start(self):
        if not self._chunks:
            self._set_status("Chưa có chunk — chọn file trước.")
            return
        self._persist_cfg()
        out = self.ed_output_dir.text().strip()
        if not out:
            self._set_status("Chọn thư mục xuất.")
            return
        self.client.base_url = self.ed_base.text().strip().rstrip("/") or self.client.base_url
        self.client.api_key = self.ed_api_key.text().strip() or self.client.api_key
        self.bt_start.setEnabled(False)
        self.bt_stop.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_result.setText(f"Kết quả: 0/{len(self._chunks)}")
        for i in range(self.tbl_sub.rowCount()):
            self.tbl_sub.setItem(i, 5, QtWidgets.QTableWidgetItem("chờ"))
        self._batch = BatchWorker(
            client=self.client,
            chunks=self._chunks,
            output_dir=out,
            voice=self.ed_voice_id.text().strip() or DEFAULT_VOICE,
            lang=self.ed_lang.text().strip() or "en",
            model=DEFAULT_MODEL,
            workers=self.sb_workers.value(),
        )
        self._batch.log.connect(self._set_status)
        self._batch.row_started.connect(self._on_row_started)
        self._batch.row_done.connect(self._on_row_done)
        self._batch.file_progress.connect(self._on_progress)
        self._batch.finished.connect(self._on_finished)
        self._batch.start()
        self._set_status(f"Đang generate {len(self._chunks)} chunk…")

    def _stop(self):
        if self._batch:
            self._batch.stop()
            self._set_status("Đang dừng…")

    def _on_row_started(self, row: int):
        item = QtWidgets.QTableWidgetItem("chạy")
        item.setForeground(QtGui.QColor("#171717"))
        self.tbl_sub.setItem(row, 5, item)

    def _on_row_done(self, row: int, ok: bool, path: str, err: str):
        if ok:
            self._chunks[row]["path"] = path
            item = QtWidgets.QTableWidgetItem("xong")
            item.setForeground(QtGui.QColor("#166534"))
            self.tbl_sub.setItem(row, 5, item)
            if path and os.path.exists(path):
                try:
                    sz = os.path.getsize(path)
                    self.tbl_sub.setItem(
                        row, 2, QtWidgets.QTableWidgetItem(f"{sz//1024}KB")
                    )
                except Exception:
                    pass
        else:
            item = QtWidgets.QTableWidgetItem("lỗi")
            item.setForeground(QtGui.QColor("#991b1b"))
            item.setToolTip(err)
            self.tbl_sub.setItem(row, 5, item)

    def _on_progress(self, _fi: int, cur: int, total: int):
        pct = int(100 * cur / max(1, total))
        self.progress.setValue(pct)
        self.lbl_result.setText(f"Kết quả: {cur}/{total}")

    def _on_finished(self, ok: int, fail: int):
        self.bt_start.setEnabled(True)
        self.bt_stop.setEnabled(False)
        self.lbl_result.setText(f"Kết quả: {ok} ok / {fail} fail")
        self._set_status(f"Xong — ok={ok} fail={fail}")
        self._batch = None

    def _open_out(self):
        d = self.ed_output_dir.text().strip()
        if d and os.path.isdir(d):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))

    def _open_chunk(self, row: int, _col: int):
        if 0 <= row < len(self._chunks):
            p = self._chunks[row].get("path")
            if p and os.path.exists(p):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p))

    def cleanup(self):
        if self._batch:
            self._batch.stop()
            self._batch.wait(3000)

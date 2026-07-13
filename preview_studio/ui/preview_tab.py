# -*- coding: utf-8 -*-
"""Main tab — UI clone OmniVoice, engine = local fast_tts (no server)."""
from __future__ import annotations

import os
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QHeaderView

import accounts_store as accounts
from local_tts import synthesize_one_sync

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
    row_done = Signal(int, bool, str, str)
    file_progress = Signal(int, int, int)
    finished = Signal(int, int)

    def __init__(
        self,
        chunks: List[dict],
        output_dir: str,
        proxy: Optional[str],
        voice: str,
        lang: str,
        model: str,
        workers: int = 2,
        hsw_workers: int = 2,
    ):
        super().__init__()
        self.chunks = chunks
        self.output_dir = output_dir
        self.proxy = proxy
        self.voice = voice
        self.lang = lang
        self.model = model
        self.workers = max(1, min(5, int(workers or 1)))
        self.hsw_workers = max(1, min(4, int(hsw_workers or 2)))
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
                synthesize_one_sync(
                    text=text,
                    out_path=out,
                    proxy=self.proxy,
                    voice=self.voice,
                    model=self.model,
                    lang=self.lang,
                    hsw_workers=self.hsw_workers,
                )
                return row, True, out, ""
            except Exception as e:
                return row, False, "", str(e)[:240]

        # Serial-ish: each synthesize uses shared farm; multi workers still ok with farm lock
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
        user: dict,
        load_config: Callable,
        save_config: Callable,
    ):
        super().__init__()
        self.main_window = main_window
        self.user = user  # full account row with proxy secrets
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

        # Settings dialog — account + proxyxoay (local)
        self.settings_dialog = QtWidgets.QDialog(self)
        self.settings_dialog.setWindowTitle("Cài đặt · Account + Proxyxoay")
        self.settings_dialog.setModal(False)
        self.settings_dialog.resize(480, 480)
        sl = QtWidgets.QVBoxLayout(self.settings_dialog)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(8)

        t1 = QtWidgets.QLabel("Account local")
        t1.setObjectName("cardTitle")
        sl.addWidget(t1)
        self.lbl_login_status = QtWidgets.QLabel("—")
        self.lbl_login_status.setObjectName("badge")
        sl.addWidget(self.lbl_login_status)

        tip = QtWidgets.QLabel(
            "Tool local generate TTS (fast_tts).\n"
            "Admin account / gói ký tự / proxy pool → web:\n"
            "https://tts-origin.liveyt.pro/admin/"
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        sl.addWidget(tip)

        t2 = QtWidgets.QLabel("Proxyxoay gắn account này")
        t2.setObjectName("cardTitle")
        sl.addWidget(t2)
        self.ed_px_key = QtWidgets.QLineEdit()
        self.ed_px_key.setPlaceholderText("Proxyxoay API key (để change-ip, tuỳ chọn)")
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
        self.ed_px_label.setPlaceholderText("Nhãn (tuỳ chọn)")
        sl.addWidget(self.ed_px_label)

        self.bt_save_proxy = QtWidgets.QPushButton("Lưu proxy cho account")
        self.bt_save_proxy.setObjectName("primaryButton")
        sl.addWidget(self.bt_save_proxy)
        self.lbl_settings_msg = QtWidgets.QLabel("")
        self.lbl_settings_msg.setObjectName("muted")
        self.lbl_settings_msg.setWordWrap(True)
        sl.addWidget(self.lbl_settings_msg)
        sl.addStretch(1)

        content = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)

        voice_card, voice_l = card("Giọng nói · Preview local")
        top_bar = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel("fast_tts · HSW preview")
        badge.setObjectName("badge")
        top_bar.addWidget(badge)
        top_bar.addStretch(1)
        self.bt_settings = QtWidgets.QToolButton()
        self.bt_settings.setObjectName("settingsButton")
        self.bt_settings.setText("⚙")
        self.bt_settings.setToolTip("Proxyxoay / account")
        top_bar.addWidget(self.bt_settings)
        voice_l.addLayout(top_bar)

        self.ed_voice_id = QtWidgets.QLineEdit()
        self.ed_voice_id.setPlaceholderText("Voice ID")
        self.ed_voice_id.setText(DEFAULT_VOICE)
        self.ed_lang = QtWidgets.QLineEdit()
        self.ed_lang.setPlaceholderText("lang")
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
        opt.addSpacing(8)
        opt.addWidget(QtWidgets.QLabel("Luồng TTS"))
        self.sb_workers = QtWidgets.QSpinBox()
        # hard max 5; account may lower further in _refresh_account_badge
        self.sb_workers.setRange(1, 5)
        self.sb_workers.setValue(2)
        opt.addWidget(self.sb_workers)
        opt.addWidget(QtWidgets.QLabel("HSW"))
        self.sb_hsw = QtWidgets.QSpinBox()
        self.sb_hsw.setRange(1, 4)
        self.sb_hsw.setValue(2)
        opt.addWidget(self.sb_hsw)
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
            ["STT", "Tệp", "Size", "Ký tự", "Nội dung", "Trạng thái"]
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
        self.bt_save_proxy.clicked.connect(self._save_proxy)
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
        self.ed_output_dir.setText(
            c.get("output_dir") or os.path.join(os.path.dirname(__file__), "..", "output")
        )
        self.sb_max_chars.setValue(int(c.get("max_chars") or 900))
        self.sb_workers.setValue(int(c.get("workers") or 2))
        self.sb_hsw.setValue(int(c.get("hsw_workers") or 2))
        self.ed_voice_id.setText(c.get("voice_id") or DEFAULT_VOICE)
        self.ed_lang.setText(c.get("lang") or "en")
        # proxy from account
        self.ed_px_key.setText(self.user.get("proxy_api_key") or "")
        self.ed_px_user.setText(self.user.get("proxy_username") or "")
        self.ed_px_pass.setText(self.user.get("proxy_password") or "")
        self.ed_px_host.setText(self.user.get("proxy_host") or "")
        if self.user.get("proxy_port"):
            self.ed_px_port.setValue(int(self.user["proxy_port"]))
        self.ed_px_label.setText(self.user.get("proxy_label") or "")

    def _persist_cfg(self):
        c = self.load_config()
        c.update(
            {
                "output_dir": self.ed_output_dir.text().strip(),
                "max_chars": self.sb_max_chars.value(),
                "workers": self.sb_workers.value(),
                "hsw_workers": self.sb_hsw.value(),
                "voice_id": self.ed_voice_id.text().strip(),
                "lang": self.ed_lang.text().strip() or "en",
            }
        )
        self.save_config(c)
        self._cfg = c

    def _refresh_account_badge(self):
        pub = accounts.public_account(self.user)
        u = pub.get("username") or "?"
        px = "có proxy" if accounts.build_proxy_url(self.user) else "CHƯA gắn proxy"
        left = int(pub.get("chars_left") or 0)
        quota = int(pub.get("char_quota") or 0)
        used = int(pub.get("chars_used") or 0)
        mw = min(5, max(1, int(pub.get("max_workers") or 2)))
        self.sb_workers.setMaximum(mw)
        if self.sb_workers.value() > mw:
            self.sb_workers.setValue(mw)
        host = self.user.get("proxy_host") or pub.get("proxy_host") or "—"
        if self.user.get("proxy_id"):
            p = accounts.get_proxy(self.user["proxy_id"])
            if p:
                host = p.get("host") or host
        self.lbl_login_status.setText(
            f"{u} · {px} · {used:,}/{quota:,} ký tự · max {mw} luồng"
        )
        self.lbl_account.setText(
            f"{u} · gói còn {left:,} ký tự · max luồng {mw} · proxy={host}"
        )

    def _open_settings(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()

    def _save_proxy(self):
        """Save inline proxy on this account (or admin: use Quản trị tab for pool)."""
        try:
            accounts.update_account(
                self.user["id"],
                proxy_provider="proxyxoay_net",
                proxy_api_key=self.ed_px_key.text().strip(),
                proxy_username=self.ed_px_user.text().strip(),
                proxy_password=self.ed_px_pass.text(),
                proxy_host=self.ed_px_host.text().strip(),
                proxy_port=self.ed_px_port.value(),
                proxy_label=self.ed_px_label.text().strip(),
            )
            full = accounts.get_account(self.user["id"])
            if full:
                self.user = full
            self._refresh_account_badge()
            self.lbl_settings_msg.setText(
                f"✅ Đã lưu proxy account '{self.user.get('username')}' "
                f"→ {self.user.get('proxy_host')}:{self.user.get('proxy_port')}"
            )
        except Exception as e:
            self.lbl_settings_msg.setText(f"Lỗi: {e}")

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
            paths = sorted(str(p) for p in Path(d).rglob("*.txt") if p.is_file())
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
            for i, t in enumerate(split_chunks(s["text"], max_c)):
                self._chunks.append({"file": s["file"], "part": i + 1, "text": t, "path": None})
        self.lbl_chunk_summary.setText(
            f"{len(self._sources)} đoạn / {len(self._chunks)} chunk"
        )
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
        # refresh account from disk
        full = accounts.get_account(self.user.get("id") or "")
        if full:
            self.user = full
        proxy = accounts.build_proxy_url(self.user)
        if not proxy:
            QtWidgets.QMessageBox.warning(
                self,
                "Thiếu proxy",
                "Account chưa gắn proxyxoay.\n"
                "Admin: tab Quản trị → Proxy + gán cho Account.\n"
                "Hoặc ⚙ → điền proxy inline → Lưu.",
            )
            return
        total_chars = sum(len(c.get("text") or "") for c in self._chunks)
        ok_q, msg_q = accounts.check_chars(self.user, total_chars)
        if not ok_q:
            QtWidgets.QMessageBox.warning(self, "Hết gói ký tự", msg_q)
            return
        mw = min(5, max(1, int(self.user.get("max_workers") or 2)))
        workers = min(self.sb_workers.value(), mw)
        self._persist_cfg()
        out = self.ed_output_dir.text().strip()
        if not out:
            self._set_status("Chọn thư mục xuất.")
            return
        self.bt_start.setEnabled(False)
        self.bt_stop.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_result.setText(f"Kết quả: 0/{len(self._chunks)}")
        for i in range(self.tbl_sub.rowCount()):
            self.tbl_sub.setItem(i, 5, QtWidgets.QTableWidgetItem("chờ"))
        self._batch = BatchWorker(
            chunks=self._chunks,
            output_dir=out,
            proxy=proxy,
            voice=self.ed_voice_id.text().strip() or DEFAULT_VOICE,
            lang=self.ed_lang.text().strip() or "en",
            model=DEFAULT_MODEL,
            workers=workers,
            hsw_workers=self.sb_hsw.value(),
        )
        self._batch.log.connect(self._set_status)
        self._batch.row_started.connect(self._on_row_started)
        self._batch.row_done.connect(self._on_row_done)
        self._batch.file_progress.connect(self._on_progress)
        self._batch.finished.connect(self._on_finished)
        self._batch.start()
        self._set_status(
            f"Đang generate {len(self._chunks)} chunk · {workers} luồng · "
            f"~{total_chars:,} ký tự…"
        )

    def _stop(self):
        if self._batch:
            self._batch.stop()
            self._set_status("Đang dừng…")

    def _on_row_started(self, row: int):
        self.tbl_sub.setItem(row, 5, QtWidgets.QTableWidgetItem("chạy"))

    def _on_row_done(self, row: int, ok: bool, path: str, err: str):
        if ok:
            self._chunks[row]["path"] = path
            # trừ gói ký tự
            n = len(self._chunks[row].get("text") or "")
            accounts.consume_chars(self.user.get("id") or "", n)
            full = accounts.get_account(self.user.get("id") or "")
            if full:
                self.user = full
                self._refresh_account_badge()
            item = QtWidgets.QTableWidgetItem("xong")
            item.setForeground(QtGui.QColor("#166534"))
            self.tbl_sub.setItem(row, 5, item)
            if path and os.path.exists(path):
                try:
                    self.tbl_sub.setItem(
                        row, 2, QtWidgets.QTableWidgetItem(f"{os.path.getsize(path)//1024}KB")
                    )
                except Exception:
                    pass
        else:
            item = QtWidgets.QTableWidgetItem("lỗi")
            item.setForeground(QtGui.QColor("#991b1b"))
            item.setToolTip(err)
            self.tbl_sub.setItem(row, 5, item)

    def _on_progress(self, _fi: int, cur: int, total: int):
        self.progress.setValue(int(100 * cur / max(1, total)))
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
            self._batch.wait(5000)

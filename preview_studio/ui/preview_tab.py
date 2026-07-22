# -*- coding: utf-8 -*-
"""Main tab — UI clone OmniVoice, engine = local fast_tts (no server)."""
from __future__ import annotations

import os
import re
import threading
import traceback
from pathlib import Path
from typing import Callable, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QHeaderView

import accounts_store as accounts
from gen_pipeline import run_jobs
from local_tts import fetch_voice_info
from output_layout import (
    build_chunks_from_sources,
    default_advanced,
    doan_path,
    merge_file_from_chunks,
    normalize_advanced,
    parse_srt_cues,
    smart_split_text,
    srt_to_plain_text,
)

DEFAULT_VOICE = "NOpBlnGInO9m6vDvFkFC"
DEFAULT_MODEL = "eleven_v3"
# model_id, nhãn UI (user chọn)
MODEL_CHOICES = [
    ("eleven_v3", "eleven_v3 — chất lượng / cảm xúc"),
    ("eleven_turbo_v2_5", "eleven_turbo_v2_5 — cân bằng"),
    ("eleven_flash_v2_5", "eleven_flash_v2_5 — nhanh + hỗ trợ vi"),
    ("eleven_multilingual_v2", "eleven_multilingual_v2 — đa ngôn ngữ"),
]
CHUNK_PAGE_SIZE = 40  # rows per page — tránh lag UI
PREVIEW_TEXT_MAX = 80_000  # chars in preview box (có scroll)

try:
    from app_paths import app_dir, resource_dir

    _STUDIO_DIR = app_dir()
    SILENT_1S_PATH = os.path.join(resource_dir(), "silent_1s.mp3")
except Exception:
    _STUDIO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    SILENT_1S_PATH = os.path.join(_STUDIO_DIR, "silent_1s.mp3")


def split_chunks(text: str, max_chars: int) -> List[str]:
    """Backward-compat — dùng smart_split_text (v327-style)."""
    return smart_split_text(text, max_chars)


class AdvancedSettingsDialog(QtWidgets.QDialog):
    """Cài đặt nâng cao — gap merge + pause char (v327)."""

    def __init__(self, parent=None, settings: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt nâng cao")
        self.setModal(True)
        self.setMinimumWidth(380)
        self._settings = normalize_advanced(settings)
        self._setup_ui()

    def _setup_ui(self):
        root = QtWidgets.QGridLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setVerticalSpacing(8)
        r = 0

        self.cb_gap = QtWidgets.QCheckBox("Ngắt âm giữa các đoạn")
        self.cb_gap.setChecked(bool(self._settings.get("gap_enabled")))
        root.addWidget(self.cb_gap, r, 0)

        self.sb_gap = QtWidgets.QDoubleSpinBox()
        self.sb_gap.setRange(0.0, 30.0)
        self.sb_gap.setSingleStep(0.1)
        self.sb_gap.setDecimals(1)
        self.sb_gap.setValue(float(self._settings.get("gap_seconds") or 1.5))
        self.sb_gap.setFixedWidth(70)
        root.addWidget(self.sb_gap, r, 1)
        root.addWidget(QtWidgets.QLabel("(s)"), r, 2)
        r += 1

        root.addWidget(QtWidgets.QLabel("    Cách nhau mỗi"), r, 0)
        self.sb_every = QtWidgets.QSpinBox()
        self.sb_every.setRange(1, 100)
        self.sb_every.setValue(int(self._settings.get("gap_every") or 1))
        self.sb_every.setFixedWidth(70)
        root.addWidget(self.sb_every, r, 1)
        root.addWidget(QtWidgets.QLabel("đoạn"), r, 2)
        r += 1

        tip_gap = QtWidgets.QLabel(
            "SRT: khoảng lặng lấy theo timing phụ đề (ưu tiên hơn gap cố định)."
        )
        tip_gap.setObjectName("muted")
        tip_gap.setWordWrap(True)
        root.addWidget(tip_gap, r, 0, 1, 3)
        r += 1

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        root.addWidget(line, r, 0, 1, 3)
        r += 1

        self.cb_pause = QtWidgets.QCheckBox(
            "Ngắt âm theo ký tự (chèn <break> SSML — như v327)"
        )
        self.cb_pause.setChecked(bool(self._settings.get("pause_char_enabled")))
        root.addWidget(self.cb_pause, r, 0, 1, 3)
        r += 1

        char_grid = QtWidgets.QGridLayout()
        char_grid.setHorizontalSpacing(6)
        char_grid.addWidget(QtWidgets.QLabel("Ký tự:"), 0, 0)
        self.ed_char1 = QtWidgets.QLineEdit(str(self._settings.get("char1") or ","))
        self.ed_char1.setMaxLength(1)
        self.ed_char1.setFixedWidth(40)
        char_grid.addWidget(self.ed_char1, 0, 1)
        self.sb_char1 = QtWidgets.QDoubleSpinBox()
        self.sb_char1.setRange(0.0, 10.0)
        self.sb_char1.setSingleStep(0.1)
        self.sb_char1.setDecimals(1)
        self.sb_char1.setValue(float(self._settings.get("char1_sec") or 0.3))
        self.sb_char1.setFixedWidth(70)
        char_grid.addWidget(self.sb_char1, 0, 2)
        char_grid.addWidget(QtWidgets.QLabel("(s)"), 0, 3)

        char_grid.addWidget(QtWidgets.QLabel("Ký tự:"), 1, 0)
        self.ed_char2 = QtWidgets.QLineEdit(str(self._settings.get("char2") or "."))
        self.ed_char2.setMaxLength(1)
        self.ed_char2.setFixedWidth(40)
        char_grid.addWidget(self.ed_char2, 1, 1)
        self.sb_char2 = QtWidgets.QDoubleSpinBox()
        self.sb_char2.setRange(0.0, 10.0)
        self.sb_char2.setSingleStep(0.1)
        self.sb_char2.setDecimals(1)
        self.sb_char2.setValue(float(self._settings.get("char2_sec") or 0.5))
        self.sb_char2.setFixedWidth(70)
        char_grid.addWidget(self.sb_char2, 1, 2)
        char_grid.addWidget(QtWidgets.QLabel("(s)"), 1, 3)
        root.addLayout(char_grid, r, 0, 1, 3)
        r += 1

        tip_p = QtWidgets.QLabel(
            "Giống v327: chèn <break time=\"…ms\"/> sau ký tự trong cùng request TTS.\n"
            "Không tách file / không silence merge. Paragraph: \\n\\n → doan_{para}."
        )
        tip_p.setWordWrap(True)
        tip_p.setObjectName("muted")
        root.addWidget(tip_p, r, 0, 1, 3)
        r += 1

        root.setRowStretch(r, 1)
        r += 1
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns, r, 0, 1, 3)

    def get_settings(self) -> dict:
        return normalize_advanced(
            {
                "gap_enabled": self.cb_gap.isChecked(),
                "gap_seconds": float(self.sb_gap.value()),
                "gap_every": int(self.sb_every.value()),
                "pause_char_enabled": self.cb_pause.isChecked(),
                "char1": (self.ed_char1.text() or ",")[:1],
                "char1_sec": float(self.sb_char1.value()),
                "char2": (self.ed_char2.text() or ".")[:1],
                "char2_sec": float(self.sb_char2.value()),
            }
        )


class VoiceFetchWorker(QThread):
    """Fetch voice meta off UI thread."""

    done = Signal(object)  # dict info
    failed = Signal(str)

    def __init__(self, voice_id: str):
        super().__init__()
        self.voice_id = voice_id

    def run(self):
        try:
            self.done.emit(fetch_voice_info(self.voice_id))
        except Exception as e:
            self.failed.emit(str(e))


class LoadFilesWorker(QThread):
    """Read files + split chunks off UI thread (paragraph + SRT + pause-char)."""

    progress = Signal(str)
    done = Signal(object, object)  # sources, chunks
    failed = Signal(str)

    def __init__(
        self,
        paths: List[str],
        max_chars: int,
        output_dir: str = "",
        advanced: Optional[dict] = None,
    ):
        super().__init__()
        self.paths = paths
        self.max_chars = max_chars
        self.output_dir = output_dir or ""
        self.advanced = normalize_advanced(advanced)

    def run(self):
        try:
            sources: List[dict] = []
            for i, p in enumerate(self.paths):
                self.progress.emit(
                    f"Đang đọc tệp {i+1}/{len(self.paths)}: {os.path.basename(p)}"
                )
                try:
                    if p.lower().endswith(".srt"):
                        cues = parse_srt_cues(p)
                        text = (
                            "\n\n".join(c["text"] for c in cues)
                            if cues
                            else srt_to_plain_text(p)
                        )
                        sources.append(
                            {
                                "file": os.path.basename(p),
                                "path": p,
                                "text": text,
                                "srt_cues": cues or None,
                            }
                        )
                    else:
                        text = Path(p).read_text(encoding="utf-8", errors="ignore")
                        sources.append(
                            {"file": os.path.basename(p), "path": p, "text": text}
                        )
                except Exception as e:
                    self.progress.emit(f"Lỗi đọc tệp {os.path.basename(p)}: {e}")
            self.progress.emit(
                f"Đang chia đoạn văn + chunk (≤{self.max_chars} ký tự)…"
            )
            out_root = self.output_dir or os.path.join(
                os.path.dirname(__file__), "..", "output"
            )
            os.makedirs(out_root, exist_ok=True)
            chunks = build_chunks_from_sources(
                sources,
                int(self.max_chars or 300),
                out_root,
                advanced=self.advanced,
            )
            for ch in chunks:
                op = ch.get("out_path") or ""
                if op and os.path.isfile(op) and os.path.getsize(op) > 500:
                    ch["path"] = op
            self.done.emit(sources, chunks)
        except Exception as e:
            self.failed.emit(str(e))


class BatchWorker(QThread):
    log = Signal(str)
    row_status = Signal(int, str)  # absolute row, status label
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
        workers: int = 5,
        hsw_workers: int = 5,
        speed: float = 1.0,
        proxy_api_key: str = "",
        proxy_lines: Optional[List[dict]] = None,
        advanced: Optional[dict] = None,
    ):
        super().__init__()
        self.chunks = chunks
        self.output_dir = output_dir
        self.proxy = proxy
        self.voice = voice
        self.lang = lang
        self.model = model
        # N proxy → N TTS lanes (cap 5); pool = 3N tokens
        self.workers = max(1, min(5, int(workers or 5)))
        self.hsw_workers = max(1, min(8, int(hsw_workers or 5)))
        self.speed = float(speed or 1.0)
        self.proxy_api_key = proxy_api_key or ""
        self.proxy_lines = list(proxy_lines or [])
        self.advanced = normalize_advanced(advanced)
        self._stop = False

    def stop(self):
        self._stop = True

    @staticmethod
    def _good_mp3(path: str) -> bool:
        try:
            return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 500
        except Exception:
            return False

    def run(self):
        """Pipeline: multi-lane TTS + outer requeue + merge chỉ khi ĐỦ đoạn."""
        import asyncio

        os.makedirs(self.output_dir, exist_ok=True)
        done_lock = threading.Lock()
        done_n = 0

        file_meta: dict[str, dict] = {}
        for ch in self.chunks:
            fn = ch.get("file") or "doan.txt"
            if fn not in file_meta:
                file_meta[fn] = {
                    "file_dir": ch.get("file_dir")
                    or os.path.dirname(ch.get("out_path") or self.output_dir),
                    "merged_path": ch.get("merged_path")
                    or os.path.join(
                        self.output_dir,
                        Path(fn).stem,
                        f"{Path(fn).stem}.mp3",
                    ),
                    "total_parts": int(ch.get("total_parts") or 0),
                }

        # Flatten jobs (row = index in self.chunks)
        all_jobs: list[dict] = []
        for i, ch in enumerate(self.chunks):
            text = ch.get("text") or ""
            fname = ch.get("file") or "doan.txt"
            part = int(ch.get("part") or (i + 1))
            out = ch.get("out_path") or doan_path(self.output_dir, fname, part)
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            ch["out_path"] = out
            all_jobs.append(
                {
                    "row": i,
                    "text": text,
                    "out_path": out,
                    "file": fname,
                    "part": part,
                }
            )

        total = len(self.chunks)
        OUTER_ROUNDS = 4  # re-queue đoạn thiếu (tránh file tổng ngắn)

        def on_start(row: int):
            if self._stop:
                return
            ch = self.chunks[row] if 0 <= row < len(self.chunks) else {}
            para = ch.get("para_idx")
            sub = ch.get("sub_idx")
            label = f"doan_{para}"
            if int(ch.get("total_subs") or 1) > 1:
                label = f"doan_{para}_{sub}"
            self.log.emit(
                f"▶ {ch.get('file')} · {label}/"
                f"{ch.get('total_parts') or '?'} · "
                f"{len(ch.get('text') or '')} ký tự"
            )

        def on_status(row: int, status: str):
            self.row_status.emit(row, status)

        def file_complete(fname: str) -> tuple[bool, int, int, list[str]]:
            """(đủ hết, n_ok, n_total, missing_labels)."""
            leaves = [c for c in self.chunks if (c.get("file") or "") == fname]
            ok_n = 0
            missing: list[str] = []
            for c in leaves:
                p = c.get("out_path") or c.get("path") or ""
                if self._good_mp3(p):
                    ok_n += 1
                else:
                    para = c.get("para_idx")
                    sub = c.get("sub_idx")
                    if para is not None and int(c.get("total_subs") or 1) > 1:
                        missing.append(f"doan_{para}_{sub}")
                    elif para is not None:
                        missing.append(f"doan_{para}")
                    else:
                        missing.append(f"part_{c.get('part') or '?'}")
            return ok_n == len(leaves) and len(leaves) > 0, ok_n, len(leaves), missing

        def try_merge_file(fname: str, *, force_log: bool = True) -> bool:
            meta = file_meta.get(fname) or {}
            mout = meta.get("merged_path") or ""
            if not mout:
                return False
            complete, ok_n, n_tot, missing = file_complete(fname)
            if not complete:
                if force_log:
                    miss_s = ", ".join(missing[:8])
                    if len(missing) > 8:
                        miss_s += f" …(+{len(missing)-8})"
                    self.log.emit(
                        f"⚠ {fname}: chỉ có {ok_n}/{n_tot} đoạn trên disk — "
                        f"KHÔNG merge (thiếu sẽ làm file ngắn/sai nội dung). "
                        f"Thiếu: {miss_s or '?'}"
                    )
                return False
            leaves = [c for c in self.chunks if (c.get("file") or "") == fname]
            ok_m, msg = merge_file_from_chunks(
                leaves, mout, advanced=self.advanced
            )
            if ok_m:
                self.log.emit(f"📦 {fname}: {msg} · đủ {ok_n}/{n_tot} đoạn")
            else:
                self.log.emit(f"⚠ Merge {fname}: {msg}")
            return ok_m

        def on_done(row: int, success: bool, path: str, err: str):
            nonlocal done_n
            with done_lock:
                done_n += 1
                cur = done_n
            ch = self.chunks[row] if 0 <= row < len(self.chunks) else {}
            fname = ch.get("file") or ""
            if success:
                if path:
                    ch["path"] = path
                self.row_status.emit(row, "Xong")
                self.row_done.emit(row, True, path, "")
                rel = path
                try:
                    rel = os.path.relpath(path, self.output_dir) if path else ""
                except Exception:
                    rel = os.path.basename(path) if path else ""
                self.log.emit(
                    f"✅ {fname} doan_{ch.get('part') or row+1} → {rel}"
                )
            else:
                self.row_status.emit(row, "Chờ thử lại…" if not self._stop else "Lỗi")
                self.row_done.emit(row, False, "", err)
                self.log.emit(
                    f"❌ {fname} doan_{ch.get('part') or row+1}: {err}"
                )
            # progress theo file thật trên disk
            disk_ok = sum(
                1
                for c in self.chunks
                if self._good_mp3(c.get("out_path") or c.get("path") or "")
            )
            self.file_progress.emit(0, disk_ok, total)

        try:
            n_workers = max(1, min(5, int(self.workers or 1)))
            ok = fail = 0

            async def _run_all():
                nonlocal ok, fail, done_n
                for outer in range(1, OUTER_ROUNDS + 1):
                    if self._stop:
                        break
                    pending = [
                        dict(j)
                        for j in all_jobs
                        if not self._good_mp3(j.get("out_path") or "")
                    ]
                    # purge tiny broken files
                    for j in pending:
                        p = j.get("out_path") or ""
                        try:
                            if p and os.path.isfile(p) and os.path.getsize(p) <= 500:
                                os.remove(p)
                        except Exception:
                            pass
                    if not pending:
                        self.log.emit("Tất cả đoạn đã có audio trên disk.")
                        break
                    self.log.emit(
                        f"── Vòng {outer}/{OUTER_ROUNDS}: "
                        f"còn {len(pending)}/{total} đoạn · {n_workers} luồng ──"
                    )
                    done_n = total - len(pending)  # baseline for this pass UI
                    o_ok, o_fail = await run_jobs(
                        pending,
                        proxy_url=self.proxy or "",
                        proxy_api_key=self.proxy_api_key,
                        proxy_lines=self.proxy_lines or None,
                        voice=self.voice,
                        model=self.model,
                        lang=self.lang,
                        speed=self.speed,
                        hsw_workers=self.hsw_workers,
                        workers=n_workers,
                        tokens_per_lane=max(3, n_workers),
                        max_attempts=40,
                        should_stop=lambda: self._stop,
                        on_start=on_start,
                        on_status=on_status,
                        on_done=on_done,
                    )
                    ok, fail = o_ok, o_fail
                    still = sum(
                        1
                        for j in all_jobs
                        if not self._good_mp3(j.get("out_path") or "")
                    )
                    if still == 0:
                        break
                    if outer < OUTER_ROUNDS and not self._stop:
                        wait = min(10.0, 1.5 * outer)
                        self.log.emit(
                            f"Còn {still} đoạn thiếu — chờ {wait:.0f}s rồi gen lại "
                            f"(chỉ đoạn thiếu, đoạn OK giữ nguyên)…"
                        )
                        await asyncio.sleep(wait)

            asyncio.run(_run_all())

            # Merge per file — chỉ khi ĐỦ mọi leaf trên disk
            for fn in file_meta:
                if self._stop:
                    break
                try_merge_file(fn, force_log=True)

            # Final counts from disk truth (không tin counter pipeline)
            disk_ok = sum(
                1
                for c in self.chunks
                if self._good_mp3(c.get("out_path") or c.get("path") or "")
            )
            disk_fail = total - disk_ok
            ok, fail = disk_ok, disk_fail
            if disk_fail > 0:
                self.log.emit(
                    f"⚠ KẾT THÚC: {disk_ok}/{total} đoạn OK · thiếu {disk_fail} đoạn. "
                    f"File MP3 tổng CHỈ merge khi đủ 100% đoạn — "
                    f"nếu ghép tay các doan_* thiếu sẽ RA NGẮN / SÓT NỘI DUNG."
                )
            else:
                self.log.emit(f"✅ KẾT THÚC: đủ {disk_ok}/{total} đoạn trên disk.")
        except Exception as e:
            self.log.emit(f"❌ Lỗi hệ thống: {e}")
            ok, fail = 0, total
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
        self._chunk_status: dict[int, str] = {}  # absolute index → status
        self._chunk_page = 0
        self._batch: Optional[BatchWorker] = None
        self._load_worker: Optional[LoadFilesWorker] = None
        self._voice_worker: Optional[VoiceFetchWorker] = None
        self._advanced = normalize_advanced((self._cfg or {}).get("advanced"))
        self._loaded_paths: List[str] = []
        self._loaded_kind = "txt"
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

        # Settings dialog — quản lý giọng đọc + account info (proxy do admin web)
        self.settings_dialog = QtWidgets.QDialog(self)
        self.settings_dialog.setWindowTitle("Cài đặt · Giọng đọc")
        self.settings_dialog.setModal(False)
        self.settings_dialog.resize(520, 560)
        sl = QtWidgets.QVBoxLayout(self.settings_dialog)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(8)

        t1 = QtWidgets.QLabel("Tài khoản đang dùng")
        t1.setObjectName("cardTitle")
        sl.addWidget(t1)
        self.lbl_login_status = QtWidgets.QLabel("—")
        self.lbl_login_status.setObjectName("badge")
        sl.addWidget(self.lbl_login_status)

        tip = QtWidgets.QLabel(
            "Proxy, gói ký tự và số luồng do admin cấp trên web.\n"
            "Tool chỉ chọn giọng đọc và cài đặt tạo audio (tự lưu).\n"
            "Trang quản trị: https://tts-origin.liveyt.pro/admin/"
        )
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        sl.addWidget(tip)

        t2 = QtWidgets.QLabel("Quản lý giọng đọc")
        t2.setObjectName("cardTitle")
        sl.addWidget(t2)

        self.tbl_voices = QtWidgets.QTableWidget(0, 3)
        self.tbl_voices.setHorizontalHeaderLabels(["Tên giọng", "Mã giọng (ID)", "Ngôn ngữ"])
        self.tbl_voices.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_voices.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl_voices.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_voices.verticalHeader().setVisible(False)
        self.tbl_voices.setAlternatingRowColors(True)
        vh = self.tbl_voices.horizontalHeader()
        vh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        vh.setSectionResizeMode(1, QHeaderView.Stretch)
        vh.setSectionResizeMode(2, QHeaderView.Fixed)
        self.tbl_voices.setColumnWidth(2, 72)
        self.tbl_voices.setMinimumHeight(160)
        sl.addWidget(self.tbl_voices, 1)

        vf = QtWidgets.QGridLayout()
        self.ed_v_name = QtWidgets.QLineEdit()
        self.ed_v_name.setPlaceholderText("Tên hiển thị (tự điền sau khi lấy thông tin)")
        self.ed_v_id = QtWidgets.QLineEdit()
        self.ed_v_id.setPlaceholderText("Dán mã giọng (vd: NOpBlnGInO9m6vDvFkFC)")
        self.ed_v_lang = QtWidgets.QLineEdit()
        self.ed_v_lang.setPlaceholderText("ngôn ngữ")
        self.ed_v_lang.setText("en")
        self.ed_v_lang.setMaximumWidth(72)
        self.bt_voice_fetch = QtWidgets.QPushButton("Lấy thông tin")
        self.bt_voice_fetch.setToolTip(
            "Lấy tên, ngôn ngữ, mô tả giọng từ thư viện ElevenLabs (public API)"
        )
        vf.addWidget(QtWidgets.QLabel("Mã giọng"), 0, 0)
        vf.addWidget(self.ed_v_id, 0, 1, 1, 2)
        vf.addWidget(self.bt_voice_fetch, 0, 3)
        vf.addWidget(QtWidgets.QLabel("Tên"), 1, 0)
        vf.addWidget(self.ed_v_name, 1, 1, 1, 2)
        vf.addWidget(self.ed_v_lang, 1, 3)
        sl.addLayout(vf)

        self.lbl_voice_info = QtWidgets.QLabel(
            "Nhập mã giọng → bấm «Lấy thông tin» để điền tên / ngôn ngữ / mô tả"
        )
        self.lbl_voice_info.setObjectName("muted")
        self.lbl_voice_info.setWordWrap(True)
        self.lbl_voice_info.setMinimumHeight(48)
        sl.addWidget(self.lbl_voice_info)

        vbtn = QtWidgets.QHBoxLayout()
        self.bt_voice_add = QtWidgets.QPushButton("Thêm giọng")
        self.bt_voice_add.setObjectName("primaryButton")
        self.bt_voice_upd = QtWidgets.QPushButton("Cập nhật dòng chọn")
        self.bt_voice_del = QtWidgets.QPushButton("Xóa dòng chọn")
        self.bt_voice_use = QtWidgets.QPushButton("Dùng giọng này")
        vbtn.addWidget(self.bt_voice_add)
        vbtn.addWidget(self.bt_voice_upd)
        vbtn.addWidget(self.bt_voice_del)
        vbtn.addWidget(self.bt_voice_use)
        sl.addLayout(vbtn)

        self.lbl_settings_msg = QtWidgets.QLabel("")
        self.lbl_settings_msg.setObjectName("muted")
        self.lbl_settings_msg.setWordWrap(True)
        sl.addWidget(self.lbl_settings_msg)

        content = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)

        voice_card, voice_l = card("Giọng đọc")
        top_bar = QtWidgets.QHBoxLayout()
        badge = QtWidgets.QLabel("Tạo audio · tuần tự · giữ IP đến 401")
        badge.setObjectName("badge")
        top_bar.addWidget(badge)
        top_bar.addStretch(1)
        self.bt_settings = QtWidgets.QToolButton()
        self.bt_settings.setObjectName("settingsButton")
        self.bt_settings.setText("⚙")
        self.bt_settings.setToolTip("Cài đặt giọng đọc")
        top_bar.addWidget(self.bt_settings)
        voice_l.addLayout(top_bar)

        # Chọn giọng đã lưu (lang ẩn — lấy từ giọng đã lưu)
        self.cb_voice = QtWidgets.QComboBox()
        self.cb_voice.setMinimumHeight(30)
        voice_l.addWidget(self.cb_voice)
        self.ed_lang = QtWidgets.QLineEdit()
        self.ed_lang.setVisible(False)
        self.ed_lang.setText("en")
        voice_l.addWidget(self.ed_lang)
        self.ed_voice_id = QtWidgets.QLineEdit()
        self.ed_voice_id.setVisible(False)
        self.ed_voice_id.setText(DEFAULT_VOICE)
        voice_l.addWidget(self.ed_voice_id)
        self.lbl_account = QtWidgets.QLabel("")
        self.lbl_account.setObjectName("muted")
        voice_l.addWidget(self.lbl_account)
        left.addWidget(voice_card)

        source_card, source_l = card("Nội dung")
        src_top = QtWidgets.QHBoxLayout()
        self.ed_input_path = QtWidgets.QLineEdit()
        self.ed_input_path.setReadOnly(True)
        self.ed_input_path.setPlaceholderText("Chọn tệp TXT / thư mục / SRT…")
        self.bt_txt = QtWidgets.QPushButton("Tệp TXT")
        self.bt_folder = QtWidgets.QPushButton("Thư mục")
        self.bt_srt = QtWidgets.QPushButton("Tệp SRT")
        src_top.addWidget(self.ed_input_path, 1)
        src_top.addWidget(self.bt_txt)
        src_top.addWidget(self.bt_folder)
        src_top.addWidget(self.bt_srt)
        source_l.addLayout(src_top)
        # voice_settings (payload TTS) — không show luồng/HSW/max đoạn (admin web + fixed 5)
        opt = QtWidgets.QHBoxLayout()
        opt.addWidget(QtWidgets.QLabel("Model"))
        self.cb_model = QtWidgets.QComboBox()
        self.cb_model.setMinimumHeight(30)
        self.cb_model.setMinimumWidth(260)
        for mid, label in MODEL_CHOICES:
            self.cb_model.addItem(label, mid)
        self.cb_model.setToolTip(
            "Model ElevenLabs (anonymous).\n"
            "• v3 / turbo_v2_5 / flash_v2_5: gửi language_code từ giọng đã lưu\n"
            "• multilingual_v2: không ép language_code (tránh 400 với vi)"
        )
        opt.addWidget(self.cb_model)
        opt.addWidget(QtWidgets.QLabel("Tốc độ đọc"))
        self.sb_speed = QtWidgets.QDoubleSpinBox()
        self.sb_speed.setRange(0.70, 1.20)
        self.sb_speed.setSingleStep(0.05)
        self.sb_speed.setDecimals(2)
        self.sb_speed.setValue(1.00)
        self.sb_speed.setToolTip("Tốc độ giọng nói (0.70 chậm – 1.20 nhanh)")
        opt.addWidget(self.sb_speed)
        self.bt_advanced = QtWidgets.QPushButton("Cài đặt nâng cao")
        self.bt_advanced.setToolTip(
            "Ngắt âm giữa đoạn / silence / gap_every / ngắt theo ký tự"
        )
        opt.addWidget(self.bt_advanced)
        opt.addStretch(1)
        self.lbl_chunk_summary = QtWidgets.QLabel("0 tệp / 0 đoạn")
        self.lbl_chunk_summary.setObjectName("badge")
        opt.addWidget(self.lbl_chunk_summary)
        source_l.addLayout(opt)
        # Hidden runtime knobs (not shown — admin / fixed)
        self._max_chars = 300
        self._workers = 5
        self._hsw_workers = 5
        self._advanced = default_advanced()
        self.ed_text = QtWidgets.QPlainTextEdit()
        self.ed_text.setReadOnly(True)
        self.ed_text.setPlaceholderText("Nội dung file sẽ hiển thị tại đây…")
        self.ed_text.setMinimumHeight(140)
        self.ed_text.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.ed_text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        self.ed_text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.ed_text.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 1px solid #c9c9c9; "
            "border-radius: 6px; padding: 6px; }"
        )
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
        self.bt_edit_mp3 = QtWidgets.QPushButton("Edit MP3")
        self.bt_edit_mp3.setToolTip(
            "Mở tab Edit MP3 — cắt / ghép / nối (ffmpeg copy mode)"
        )
        self.bt_edit_mp3.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_edit_mp3.setStyleSheet(
            "QPushButton { background: #ffffff; color: #171717; border: 1px solid #d4d4d4; "
            "border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #f5f5f5; }"
        )
        self.bt_start.setObjectName("primaryButton")
        self.bt_stop.setObjectName("dangerButton")
        self.lbl_result = QtWidgets.QLabel("Kết quả: 0/0")
        self.lbl_result.setObjectName("badge")
        run.addWidget(self.bt_start)
        run.addWidget(self.bt_stop)
        run.addWidget(self.bt_output)
        run.addWidget(self.bt_edit_mp3)
        run.addStretch(1)
        run.addWidget(self.lbl_result)
        out_l.addLayout(run)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        out_l.addWidget(self.progress)
        left.addWidget(out_card)

        queue_card, queue_l = card("Hàng đợi tệp")
        self.tbl_queue = QtWidgets.QTableWidget(0, 4)
        self.tbl_queue.setHorizontalHeaderLabels(["STT", "Tên tệp", "Trạng thái", "Tiến độ"])
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

        chunks_card, chunks_l = card("Danh sách đoạn cần tạo")
        self.tbl_sub = QtWidgets.QTableWidget(0, 6)
        self.tbl_sub.setHorizontalHeaderLabels(
            ["Đoạn", "Tệp", "Dung lượng", "Ký tự", "Nội dung", "Trạng thái"]
        )
        self.tbl_sub.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_sub.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_sub.verticalHeader().setVisible(False)
        h = self.tbl_sub.horizontalHeader()
        for c in (0, 1, 2, 3, 5):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_sub.setColumnWidth(0, 88)
        self.tbl_sub.setColumnWidth(1, 110)
        self.tbl_sub.setColumnWidth(2, 78)
        self.tbl_sub.setColumnWidth(3, 50)
        self.tbl_sub.setColumnWidth(5, 100)
        self.tbl_sub.setAlternatingRowColors(True)
        self.tbl_sub.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        chunks_l.addWidget(self.tbl_sub, 1)
        # Pagination — không render cả nghìn dòng cùng lúc
        page_row = QtWidgets.QHBoxLayout()
        self.bt_page_prev = QtWidgets.QPushButton("◀")
        self.bt_page_next = QtWidgets.QPushButton("▶")
        self.bt_page_prev.setFixedWidth(36)
        self.bt_page_next.setFixedWidth(36)
        self.lbl_page = QtWidgets.QLabel("Trang 0/0")
        self.lbl_page.setObjectName("muted")
        page_row.addWidget(self.bt_page_prev)
        page_row.addWidget(self.bt_page_next)
        page_row.addWidget(self.lbl_page, 1)
        chunks_l.addLayout(page_row)
        right.addWidget(chunks_card, 1)

        content.addLayout(left, 38)
        content.addLayout(right, 62)
        root.addLayout(content, 1)
        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("muted")
        root.addWidget(self._status)

        self.bt_stop.setEnabled(False)
        self.bt_settings.clicked.connect(self._open_settings)
        self.bt_voice_fetch.clicked.connect(self._voice_fetch_info)
        self.bt_voice_add.clicked.connect(self._voice_add)
        self.bt_voice_upd.clicked.connect(self._voice_update)
        self.bt_voice_del.clicked.connect(self._voice_delete)
        self.bt_voice_use.clicked.connect(self._voice_use_selected)
        self.tbl_voices.itemSelectionChanged.connect(self._voice_row_selected)
        self.ed_v_id.returnPressed.connect(self._voice_fetch_info)
        self.cb_voice.currentIndexChanged.connect(self._on_voice_combo)
        self.bt_txt.clicked.connect(self._pick_txt)
        self.bt_folder.clicked.connect(self._pick_folder)
        self.bt_srt.clicked.connect(self._pick_srt)
        self.sb_speed.valueChanged.connect(self._on_setting_changed)
        self.cb_model.currentIndexChanged.connect(self._on_setting_changed)
        self.bt_advanced.clicked.connect(self._open_advanced)
        self.ed_output_dir.editingFinished.connect(self._persist_cfg)
        self.bt_browse_out.clicked.connect(self._browse_out)
        self.bt_start.clicked.connect(self._start)
        self.bt_stop.clicked.connect(self._stop)
        self.bt_output.clicked.connect(self._open_out)
        self.bt_edit_mp3.clicked.connect(self._open_edit_mp3)
        self.tbl_sub.cellDoubleClicked.connect(self._open_chunk)
        self.bt_page_prev.clicked.connect(self._page_prev)
        self.bt_page_next.clicked.connect(self._page_next)

    def _voices_list(self) -> list:
        voices = list(self._cfg.get("voices") or [])
        if not voices:
            voices = [
                {
                    "name": "Giọng mặc định",
                    "voice_id": self._cfg.get("voice_id") or DEFAULT_VOICE,
                    "lang": self._cfg.get("lang") or "en",
                }
            ]
            self._cfg["voices"] = voices
        return voices

    def _reload_voice_combo(self, select_id: Optional[str] = None):
        voices = self._voices_list()
        want = select_id or self.ed_voice_id.text().strip() or DEFAULT_VOICE
        self.cb_voice.blockSignals(True)
        self.cb_voice.clear()
        sel = 0
        for i, v in enumerate(voices):
            vid = (v.get("voice_id") or "").strip()
            name = (v.get("name") or vid or f"Giọng {i+1}").strip()
            lang = (v.get("lang") or "en").strip()
            self.cb_voice.addItem(f"{name} · ngôn ngữ {lang}", v)
            if vid == want:
                sel = i
        self.cb_voice.setCurrentIndex(sel)
        self.cb_voice.blockSignals(False)
        self._apply_combo_voice()

    def _reload_voice_table(self):
        voices = self._voices_list()
        self.tbl_voices.setRowCount(0)
        for v in voices:
            r = self.tbl_voices.rowCount()
            self.tbl_voices.insertRow(r)
            self.tbl_voices.setItem(r, 0, QtWidgets.QTableWidgetItem(v.get("name") or ""))
            self.tbl_voices.setItem(
                r, 1, QtWidgets.QTableWidgetItem(v.get("voice_id") or "")
            )
            self.tbl_voices.setItem(r, 2, QtWidgets.QTableWidgetItem(v.get("lang") or "en"))

    def _apply_combo_voice(self):
        data = self.cb_voice.currentData()
        if isinstance(data, dict):
            self.ed_voice_id.setText((data.get("voice_id") or DEFAULT_VOICE).strip())
            self.ed_lang.setText((data.get("lang") or "en").strip())
        elif self.cb_voice.count() == 0:
            self.ed_voice_id.setText(DEFAULT_VOICE)

    def _on_voice_combo(self, _idx: int = 0):
        self._apply_combo_voice()
        self._persist_cfg()

    def _on_setting_changed(self, *_args):
        self._persist_cfg()

    def _selected_model(self) -> str:
        mid = (self.cb_model.currentData() or "").strip()
        if mid:
            return mid
        return DEFAULT_MODEL

    def _set_model_combo(self, model_id: str) -> None:
        want = (model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        idx = self.cb_model.findData(want)
        if idx < 0:
            idx = self.cb_model.findData(DEFAULT_MODEL)
        if idx < 0:
            idx = 0
        self.cb_model.blockSignals(True)
        self.cb_model.setCurrentIndex(max(0, idx))
        self.cb_model.blockSignals(False)

    def _load_cfg(self):
        c = self._cfg
        self.ed_output_dir.setText(
            c.get("output_dir") or os.path.join(os.path.dirname(__file__), "..", "output")
        )
        # fixed runtime (not shown)
        self._max_chars = int(c.get("max_chars") or 300)
        self._workers = 5
        self._hsw_workers = 5
        self.sb_speed.blockSignals(True)
        self.sb_speed.setValue(float(c.get("speed") if c.get("speed") is not None else 1.0))
        self.sb_speed.blockSignals(False)
        self._set_model_combo(c.get("model") or DEFAULT_MODEL)
        self._advanced = normalize_advanced(c.get("advanced") or {})
        self.ed_voice_id.setText(c.get("voice_id") or DEFAULT_VOICE)
        self.ed_lang.setText(c.get("lang") or "en")
        self._reload_voice_combo(c.get("voice_id"))
        self._reload_voice_table()

    def _persist_cfg(self):
        c = self.load_config()
        voices = self._voices_list()
        c.update(
            {
                "output_dir": self.ed_output_dir.text().strip(),
                "max_chars": int(self._max_chars or 300),
                "workers": 5,
                "hsw_workers": 5,
                "voice_id": self.ed_voice_id.text().strip() or DEFAULT_VOICE,
                "lang": self.ed_lang.text().strip() or "en",
                "model": self._selected_model(),
                "speed": float(self.sb_speed.value()),
                "advanced": normalize_advanced(self._advanced),
                "voices": voices,
            }
        )
        self.save_config(c)
        self._cfg = c

    def _open_advanced(self):
        dlg = AdvancedSettingsDialog(self, self._advanced)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self._advanced = dlg.get_settings()
        self._persist_cfg()
        # re-split if files already loaded so gap/pause/paragraph apply
        paths = list(getattr(self, "_loaded_paths", None) or [])
        if paths:
            self._set_status("Đã lưu cài đặt nâng cao — đang chia lại đoạn…")
            self._load_paths(paths, getattr(self, "_loaded_kind", "txt"))
        else:
            self._set_status("Đã lưu cài đặt nâng cao (gap / pause / SRT).")

    def _refresh_account_badge(self):
        pub = accounts.public_account(self.user)
        u = pub.get("username") or "?"
        left = int(pub.get("chars_left") or 0)
        quota = int(pub.get("char_quota") or 0)
        used = int(pub.get("chars_used") or 0)
        unlimited = bool(pub.get("unlimited")) or accounts.is_unlimited_quota(quota)
        # account max_workers from admin (≤5); runtime workers = min(5, mw)
        mw = min(5, max(1, int(pub.get("max_workers") or 5)))
        self._workers = mw
        self._hsw_workers = 5
        # max_chars: CF account >0 wins, else config default (300)
        acc_mc = int(self.user.get("max_chars") or pub.get("max_chars") or 0)
        if acc_mc > 0:
            self._max_chars = max(40, min(5000, acc_mc))
        else:
            try:
                self._max_chars = int(self.load_config().get("max_chars") or 300)
            except Exception:
                self._max_chars = 300
        mc = int(self._max_chars or 300)
        if unlimited:
            self.lbl_login_status.setText(
                f"{u} · Unlimited · đã gen {used:,} ký tự · {mw} luồng · chunk ≤{mc}"
            )
            self.lbl_account.setText(
                f"{u} · gói Unlimited · tối đa {mw} luồng · max {mc} ký tự/đoạn"
            )
        else:
            self.lbl_login_status.setText(
                f"{u} · {used:,}/{quota:,} ký tự · {mw} luồng · chunk ≤{mc}"
            )
            self.lbl_account.setText(
                f"{u} · còn {left:,} ký tự · tối đa {mw} luồng · max {mc} ký tự/đoạn"
            )

    def _open_settings(self):
        self._reload_voice_table()
        self.lbl_settings_msg.setText("")
        self.settings_dialog.show()
        self.settings_dialog.raise_()

    def _voice_row_selected(self):
        rows = self.tbl_voices.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        voices = self._voices_list()
        if r < 0 or r >= len(voices):
            return
        v = voices[r]
        self.ed_v_name.setText(v.get("name") or "")
        self.ed_v_id.setText(v.get("voice_id") or "")
        self.ed_v_lang.setText(v.get("lang") or "en")
        desc = (v.get("description") or "").strip()
        if desc:
            self.lbl_voice_info.setText(desc[:280] + ("…" if len(desc) > 280 else ""))

    def _voice_fetch_info(self):
        """Nhập voice_id → fetch off UI thread (shared-voices API)."""
        vid = self.ed_v_id.text().strip()
        if not vid:
            self.lbl_settings_msg.setText("Hãy nhập mã giọng trước")
            return
        if self._voice_worker and self._voice_worker.isRunning():
            self.lbl_settings_msg.setText("Đang lấy thông tin… vui lòng chờ")
            return
        self.bt_voice_fetch.setEnabled(False)
        self.bt_voice_fetch.setText("Đang lấy…")
        self.lbl_settings_msg.setText("Đang tải thông tin giọng (chạy nền)…")
        w = VoiceFetchWorker(vid)
        self._voice_worker = w
        w.done.connect(self._on_voice_fetched)
        w.failed.connect(self._on_voice_fetch_failed)
        w.finished.connect(lambda: self.bt_voice_fetch.setEnabled(True))
        w.finished.connect(lambda: self.bt_voice_fetch.setText("Lấy thông tin"))
        w.start()

    def _on_voice_fetched(self, info: object):
        if not isinstance(info, dict):
            return
        self.ed_v_id.setText(info.get("voice_id") or "")
        self.ed_v_name.setText(info.get("name") or "")
        self.ed_v_lang.setText(info.get("language") or "en")
        _vn_gender = {
            "male": "nam",
            "female": "nữ",
            "neutral": "trung tính",
        }
        _vn_age = {
            "young": "trẻ",
            "middle_aged": "trung niên",
            "old": "già",
        }
        bits = [
            info.get("name") or "",
            f"mã={info.get('voice_id')}",
            f"ngôn ngữ={info.get('language') or '?'}",
        ]
        if info.get("gender"):
            bits.append(
                f"giới tính={_vn_gender.get(str(info['gender']).lower(), info['gender'])}"
            )
        if info.get("age"):
            bits.append(f"độ tuổi={_vn_age.get(str(info['age']).lower(), info['age'])}")
        if info.get("accent"):
            bits.append(f"giọng vùng={info['accent']}")
        if info.get("category"):
            bits.append(f"loại={info['category']}")
        if info.get("use_case"):
            bits.append(f"dùng cho={info['use_case']}")
        if info.get("verified_languages"):
            bits.append(
                "hỗ trợ: " + ", ".join(info["verified_languages"][:8])
            )
        desc = (info.get("description") or "").strip()
        text = " · ".join(b for b in bits if b)
        if desc:
            text += "\n" + desc[:320] + ("…" if len(desc) > 320 else "")
        self.lbl_voice_info.setText(text)
        self._last_voice_meta = info
        self.lbl_settings_msg.setText(
            f"✅ Đã lấy «{info.get('name')}» — bấm «Thêm giọng» hoặc «Dùng giọng này»"
        )

    def _on_voice_fetch_failed(self, err: str):
        self._last_voice_meta = None
        self.lbl_voice_info.setText("")
        self.lbl_settings_msg.setText(f"❌ {err}")

    def _voice_add(self):
        name = self.ed_v_name.text().strip()
        vid = self.ed_v_id.text().strip()
        lang = (self.ed_v_lang.text().strip() or "en")
        if not vid:
            self.lbl_settings_msg.setText("Hãy nhập mã giọng")
            return
        if not name:
            # Prefer last fetched meta (async); never block UI with network here
            meta0 = getattr(self, "_last_voice_meta", None) or {}
            if meta0.get("voice_id") == vid or not meta0.get("voice_id"):
                name = (meta0.get("name") or "").strip() or (vid[:12] + "…")
                if meta0.get("language"):
                    lang = meta0.get("language") or lang
            else:
                name = vid[:12] + "…"
            self.ed_v_name.setText(name)
        voices = self._voices_list()
        for v in voices:
            if v.get("voice_id") == vid:
                self.lbl_settings_msg.setText("Mã giọng này đã có trong danh sách")
                return
        meta = getattr(self, "_last_voice_meta", None) or {}
        if meta.get("voice_id") != vid:
            meta = {}
        voices.append(
            {
                "name": name,
                "voice_id": vid,
                "lang": lang,
                "description": (meta.get("description") or "")[:400],
                "gender": meta.get("gender") or "",
                "accent": meta.get("accent") or "",
            }
        )
        self._cfg["voices"] = voices
        self._persist_cfg()
        self._reload_voice_table()
        self._reload_voice_combo(vid)
        self.lbl_settings_msg.setText(f"✅ Đã thêm giọng «{name}»")

    def _voice_update(self):
        rows = self.tbl_voices.selectionModel().selectedRows()
        if not rows:
            self.lbl_settings_msg.setText("Hãy chọn một giọng trong bảng")
            return
        r = rows[0].row()
        voices = self._voices_list()
        if r < 0 or r >= len(voices):
            return
        name = self.ed_v_name.text().strip()
        vid = self.ed_v_id.text().strip()
        lang = self.ed_v_lang.text().strip() or "en"
        if not vid:
            self.lbl_settings_msg.setText("Mã giọng đang trống")
            return
        if not name:
            name = vid[:12] + "…"
        voices[r] = {"name": name, "voice_id": vid, "lang": lang}
        self._cfg["voices"] = voices
        self._persist_cfg()
        self._reload_voice_table()
        self._reload_voice_combo(vid)
        self.lbl_settings_msg.setText(f"✅ Đã cập nhật «{name}»")

    def _voice_delete(self):
        rows = self.tbl_voices.selectionModel().selectedRows()
        if not rows:
            self.lbl_settings_msg.setText("Hãy chọn một giọng trong bảng để xóa")
            return
        r = rows[0].row()
        voices = self._voices_list()
        if len(voices) <= 1:
            self.lbl_settings_msg.setText("Cần giữ lại ít nhất 1 giọng")
            return
        if r < 0 or r >= len(voices):
            return
        removed = voices.pop(r)
        self._cfg["voices"] = voices
        self._persist_cfg()
        self._reload_voice_table()
        self._reload_voice_combo(voices[0].get("voice_id"))
        self.lbl_settings_msg.setText(
            f"Đã xóa «{removed.get('name') or removed.get('voice_id')}»"
        )

    def _voice_use_selected(self):
        rows = self.tbl_voices.selectionModel().selectedRows()
        if not rows:
            # use form fields
            vid = self.ed_v_id.text().strip()
            if not vid:
                self.lbl_settings_msg.setText(
                    "Hãy chọn giọng trong bảng hoặc nhập mã giọng"
                )
                return
            lang = self.ed_v_lang.text().strip() or "en"
            self.ed_voice_id.setText(vid)
            self.ed_lang.setText(lang)
            self._reload_voice_combo(vid)
            self._persist_cfg()
            self.lbl_settings_msg.setText("✅ Đã chọn giọng từ ô nhập")
            return
        r = rows[0].row()
        voices = self._voices_list()
        if r < 0 or r >= len(voices):
            return
        v = voices[r]
        self.ed_voice_id.setText(v.get("voice_id") or DEFAULT_VOICE)
        self.ed_lang.setText(v.get("lang") or "en")
        self._reload_voice_combo(v.get("voice_id"))
        self._persist_cfg()
        self.lbl_settings_msg.setText(
            f"✅ Đang dùng «{v.get('name') or v.get('voice_id')}»"
        )

    def _set_status(self, text: str):
        self._status.setText(text)

    def _pick_txt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Chọn tệp văn bản TXT",
            "",
            "Tệp văn bản (*.txt);;Tất cả tệp (*.*)",
        )
        if path:
            self._load_paths([path], "txt")

    def _pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Chọn thư mục chứa tệp TXT"
        )
        if d:
            paths = sorted(str(p) for p in Path(d).rglob("*.txt") if p.is_file())
            self._load_paths(paths, "thư mục")

    def _pick_srt(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Chọn tệp phụ đề SRT",
            "",
            "Phụ đề SRT (*.srt);;Tất cả tệp (*.*)",
        )
        if path:
            self._load_paths([path], "srt")

    def _load_paths(self, paths: List[str], source_type: str):
        if not paths:
            return
        if self._load_worker and self._load_worker.isRunning():
            self._set_status("Đang đọc tệp khác — vui lòng chờ xong…")
            return
        if self._batch and self._batch.isRunning():
            self._set_status(
                "Đang tạo audio — hãy bấm Dừng trước khi mở tệp mới"
            )
            return
        self._loaded_paths = list(paths)
        self._loaded_kind = source_type
        self.ed_input_path.setText(
            paths[0]
            if len(paths) == 1
            else f"{len(paths)} tệp ({source_type})"
        )
        self.ed_text.setPlainText("Đang đọc tệp và chia đoạn…")
        self.bt_start.setEnabled(False)
        self._set_status(f"Đang tải {len(paths)} tệp (chạy nền)…")
        out_dir = self.ed_output_dir.text().strip() or os.path.join(
            os.path.dirname(__file__), "..", "output"
        )
        w = LoadFilesWorker(
            paths,
            int(self._max_chars or 300),
            output_dir=out_dir,
            advanced=self._advanced,
        )
        self._load_worker = w
        w.progress.connect(self._set_status)
        w.done.connect(self._on_files_loaded)
        w.failed.connect(self._on_files_load_failed)
        w.start()

    def _on_files_loaded(self, sources: object, chunks: object):
        self._sources = list(sources or [])
        self._chunks = list(chunks or [])
        self._chunk_status = {i: "Chờ" for i in range(len(self._chunks))}
        self._chunk_page = 0
        # preview text with scroll (cap size)
        parts = []
        used = 0
        for s in self._sources:
            block = f"--- {s['file']} ---\n{s.get('text') or ''}\n\n"
            if used + len(block) > PREVIEW_TEXT_MAX:
                remain = PREVIEW_TEXT_MAX - used
                if remain > 80:
                    parts.append(
                        block[:remain] + "\n… (đã cắt phần xem trước — tệp còn dài hơn)"
                    )
                break
            parts.append(block)
            used += len(block)
        self.ed_text.setPlainText("".join(parts) or "(trống)")
        self.ed_text.verticalScrollBar().setValue(0)
        self.lbl_chunk_summary.setText(
            f"{len(self._sources)} tệp / {len(self._chunks)} đoạn"
        )
        self._rebuild_queue_table()
        self._render_chunk_page()
        self.bt_start.setEnabled(True)
        out_root = self.ed_output_dir.text().strip() or "output"
        self._set_status(
            f"Sẵn sàng · {len(self._sources)} tệp · {len(self._chunks)} đoạn "
            f"· layout {{output}}/{{stem}}/doan_N.mp3 · {out_root}"
        )
        self._persist_cfg()

    def _on_files_load_failed(self, err: str):
        self.bt_start.setEnabled(True)
        self.ed_text.setPlainText("")
        self._set_status(f"Lỗi khi tải tệp: {err}")

    def _total_chunk_pages(self) -> int:
        n = len(self._chunks)
        if n <= 0:
            return 0
        return (n + CHUNK_PAGE_SIZE - 1) // CHUNK_PAGE_SIZE

    def _page_prev(self):
        if self._chunk_page > 0:
            self._chunk_page -= 1
            self._render_chunk_page()

    def _page_next(self):
        if self._chunk_page + 1 < self._total_chunk_pages():
            self._chunk_page += 1
            self._render_chunk_page()

    def _render_chunk_page(self):
        """Chỉ vẽ 1 trang chunk — tránh lag main thread."""
        total = len(self._chunks)
        pages = self._total_chunk_pages()
        if pages == 0:
            self.tbl_sub.setRowCount(0)
            self.lbl_page.setText("Trang 0/0")
            self.bt_page_prev.setEnabled(False)
            self.bt_page_next.setEnabled(False)
            return
        if self._chunk_page >= pages:
            self._chunk_page = pages - 1
        start = self._chunk_page * CHUNK_PAGE_SIZE
        end = min(start + CHUNK_PAGE_SIZE, total)
        self.tbl_sub.setUpdatesEnabled(False)
        self.tbl_sub.setRowCount(end - start)
        for local, abs_i in enumerate(range(start, end)):
            ch = self._chunks[abs_i]
            st = self._chunk_status.get(abs_i, "Chờ")
            para = ch.get("para_idx")
            sub = ch.get("sub_idx")
            total_p = ch.get("total_parts") or "?"
            if para is not None:
                if int(ch.get("total_subs") or 1) > 1:
                    label = f"doan_{para}_{sub}/{total_p}"
                else:
                    label = f"doan_{para}/{total_p}"
            else:
                part = ch.get("part") or (abs_i + 1)
                label = f"doan_{part}/{total_p}"
            self.tbl_sub.setItem(
                local,
                0,
                QtWidgets.QTableWidgetItem(label),
            )
            self.tbl_sub.setItem(
                local, 1, QtWidgets.QTableWidgetItem((ch.get("file") or "")[:40])
            )
            size = "—"
            p = ch.get("path") or ch.get("out_path")
            if p and os.path.exists(p) and os.path.getsize(p) > 500:
                try:
                    size = f"{os.path.getsize(p) // 1024} KB"
                except Exception:
                    size = "đã có"
            self.tbl_sub.setItem(local, 2, QtWidgets.QTableWidgetItem(size))
            # Ký tự = plain content (không tính thẻ <break>); tooltip = payload API
            try:
                from output_layout import plain_char_count

                plain_n = int(
                    ch.get("plain_chars")
                    or plain_char_count(ch.get("text") or "")
                )
            except Exception:
                plain_n = len(ch.get("text") or "")
            payload_n = int(ch.get("payload_chars") or len(ch.get("text") or ""))
            cell_chars = QtWidgets.QTableWidgetItem(str(plain_n))
            if payload_n != plain_n:
                cell_chars.setToolTip(
                    f"Nội dung: {plain_n} · payload TTS (kèm break): {payload_n}"
                    f" · max {int(self._max_chars or 0)}"
                )
            else:
                cell_chars.setToolTip(f"≤ max {int(self._max_chars or 0)} ký tự/đoạn")
            self.tbl_sub.setItem(local, 3, cell_chars)
            # preview: strip break tags cho dễ đọc
            try:
                from output_layout import strip_ssml_breaks

                prev_src = strip_ssml_breaks(ch.get("text") or "")
            except Exception:
                prev_src = ch.get("text") or ""
            prev = prev_src[:80]
            if len(prev_src) > 80:
                prev += "…"
            self.tbl_sub.setItem(local, 4, QtWidgets.QTableWidgetItem(prev))
            item = QtWidgets.QTableWidgetItem(st)
            st_l = st.lower()
            if st == "Xong" or st_l == "xong" or "đã merge" in st_l:
                item.setForeground(QtGui.QColor("#166534"))
            elif st_l.startswith("lỗi") and "thử lại" not in st_l:
                item.setForeground(QtGui.QColor("#991b1b"))
            elif any(
                k in st_l
                for k in (
                    "đang",
                    "chạy",
                    "chờ",
                    "chuẩn bị",
                    "đổi",
                    "thử lại",
                    "xếp lại",
                )
            ):
                item.setForeground(QtGui.QColor("#b45309"))
            elif "lỗi" in st_l:
                item.setForeground(QtGui.QColor("#b45309"))  # retrying
            self.tbl_sub.setItem(local, 5, item)
        self.tbl_sub.setUpdatesEnabled(True)
        self.lbl_page.setText(
            f"Trang {self._chunk_page + 1}/{pages} · đoạn {start + 1}–{end}/{total}"
        )
        self.bt_page_prev.setEnabled(self._chunk_page > 0)
        self.bt_page_next.setEnabled(self._chunk_page + 1 < pages)

    def _set_chunk_status(self, abs_row: int, status: str):
        self._chunk_status[abs_row] = status
        start = self._chunk_page * CHUNK_PAGE_SIZE
        end = start + CHUNK_PAGE_SIZE
        if start <= abs_row < end:
            local = abs_row - start
            if 0 <= local < self.tbl_sub.rowCount():
                item = QtWidgets.QTableWidgetItem(status)
                st_l = status.lower()
                if status == "Xong" or st_l == "xong" or "đã merge" in st_l:
                    item.setForeground(QtGui.QColor("#166534"))
                elif st_l.startswith("lỗi") and "thử lại" not in st_l:
                    item.setForeground(QtGui.QColor("#991b1b"))
                elif any(
                    k in st_l
                    for k in (
                        "đang",
                        "chạy",
                        "chờ",
                        "chuẩn bị",
                        "đổi",
                        "thử lại",
                        "xếp lại",
                    )
                ):
                    item.setForeground(QtGui.QColor("#b45309"))
                elif "lỗi" in st_l:
                    item.setForeground(QtGui.QColor("#b45309"))
                self.tbl_sub.setItem(local, 5, item)

    def _rebuild_queue_table(self):
        # queue files: cap display to 200 rows to avoid lag
        show = self._sources[:200]
        # totals per file for % progress
        self._file_total: dict[str, int] = {}
        self._file_done: dict[str, int] = {}
        self._file_row: dict[str, int] = {}
        for ch in self._chunks:
            fn = ch.get("file") or ""
            if not fn:
                continue
            self._file_total[fn] = self._file_total.get(fn, 0) + 1
            # already-have on disk count as done for display
            p = ch.get("path") or ch.get("out_path") or ""
            if p and os.path.isfile(p) and os.path.getsize(p) > 500:
                self._file_done[fn] = self._file_done.get(fn, 0) + 1
        self.tbl_queue.setUpdatesEnabled(False)
        self.tbl_queue.setRowCount(len(show))
        for i, s in enumerate(show):
            fn = s.get("file") or ""
            self._file_row[fn] = i
            total = max(1, self._file_total.get(fn, 1))
            done = min(total, self._file_done.get(fn, 0))
            pct = int(100 * done / total)
            if done >= total:
                st, st_color = "Xong", "#166534"
            elif done > 0:
                st, st_color = "Đang chạy", "#b45309"
            else:
                st, st_color = "Chờ", None
            self.tbl_queue.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_queue.setItem(i, 1, QtWidgets.QTableWidgetItem(fn))
            it_st = QtWidgets.QTableWidgetItem(st)
            if st_color:
                it_st.setForeground(QtGui.QColor(st_color))
            self.tbl_queue.setItem(i, 2, it_st)
            self.tbl_queue.setItem(i, 3, QtWidgets.QTableWidgetItem(f"{pct}%"))
        self.tbl_queue.setUpdatesEnabled(True)
        if len(self._sources) > 200:
            self._set_status(
                f"Hàng đợi chỉ hiện 200/{len(self._sources)} tệp "
                f"(vẫn tạo audio đủ tất cả đoạn)"
            )

    def _bump_queue_file(self, fname: str, ok: bool = True):
        """Cập nhật % + trạng thái 1 dòng hàng đợi tệp."""
        if not fname:
            return
        if not hasattr(self, "_file_total"):
            return
        total = max(1, int(self._file_total.get(fname) or 1))
        done = min(total, int(self._file_done.get(fname) or 0) + (1 if ok else 0))
        if ok:
            self._file_done[fname] = done
        pct = int(100 * done / total)
        row = self._file_row.get(fname)
        if row is None or row >= self.tbl_queue.rowCount():
            return
        if done >= total:
            st, color = "Xong", "#166534"
            # show merge file if present
            merged = ""
            for ch in self._chunks:
                if ch.get("file") == fname and ch.get("merged_path"):
                    merged = ch.get("merged_path") or ""
                    break
            if merged and os.path.isfile(merged) and os.path.getsize(merged) > 500:
                st = "Xong · đã merge"
        elif done > 0:
            st, color = "Đang chạy", "#b45309"
        else:
            st, color = "Chờ", None
        it_st = QtWidgets.QTableWidgetItem(st)
        if color:
            it_st.setForeground(QtGui.QColor(color))
        self.tbl_queue.setItem(row, 2, it_st)
        self.tbl_queue.setItem(row, 3, QtWidgets.QTableWidgetItem(f"{pct}%"))

    def _browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục xuất audio")
        if d:
            self.ed_output_dir.setText(d)
            self._persist_cfg()

    def _start(self):
        if not self._chunks:
            self._set_status("Chưa có đoạn nào — hãy chọn tệp TXT/SRT trước.")
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
                "Tài khoản chưa được admin gắn proxy.\n"
                "Liên hệ admin cấp proxy tại:\n"
                "https://tts-origin.liveyt.pro/admin/",
            )
            return
        total_chars = sum(len(c.get("text") or "") for c in self._chunks)
        ok_q, msg_q = accounts.check_chars(self.user, total_chars)
        if not ok_q:
            QtWidgets.QMessageBox.warning(self, "Hết gói ký tự", msg_q)
            return
        # max_workers admin = số TTS đồng thời (1 proxy vẫn chạy đủ N luồng)
        mw = min(5, max(1, int(self.user.get("max_workers") or 5)))
        proxy_lines = accounts.list_proxy_lines_for_gen(self.user, max_lanes=5)
        if not proxy_lines:
            QtWidgets.QMessageBox.warning(
                self,
                "Thiếu proxy",
                "Không có proxy enabled trong pool.\n"
                "Admin thêm proxy (proxyxoay.net hoặc proxyxoay.shop) tại:\n"
                "https://tts-origin.liveyt.pro/admin/",
            )
            return
        workers = mw  # 1 proxy + max_workers=3 → 3 luồng TTS
        hsw = min(8, max(3, workers * 2))
        self._workers = workers
        self._hsw_workers = hsw
        self._persist_cfg()
        out = self.ed_output_dir.text().strip()
        if not out:
            self._set_status("Hãy chọn thư mục xuất audio.")
            return
        self.bt_start.setEnabled(False)
        self.bt_stop.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_result.setText(f"Kết quả: 0/{len(self._chunks)}")
        self._chunk_status = {i: "Chờ" for i in range(len(self._chunks))}
        self._chunk_page = 0
        # reset queue % (chỉ đếm lại từ 0 khi gen mới; skip-on-disk vẫn cập nhật sau)
        self._file_done = {fn: 0 for fn in (getattr(self, "_file_total", {}) or {})}
        self._rebuild_queue_table()
        # zero done for fresh run display (rebuild counted existing files)
        for fn in list(self._file_done.keys()):
            # keep pre-existing as progress so skip looks correct
            pass
        self._render_chunk_page()
        px_key = self.user.get("proxy_api_key") or ""
        if not px_key and proxy_lines:
            px_key = proxy_lines[0].get("api_key") or ""
        labels = ", ".join(
            (p.get("label") or p.get("id") or "?") for p in proxy_lines
        )
        model = self._selected_model()
        self._persist_cfg()
        self._batch = BatchWorker(
            chunks=self._chunks,
            output_dir=out,
            proxy=proxy,
            voice=self.ed_voice_id.text().strip() or DEFAULT_VOICE,
            lang=self.ed_lang.text().strip() or "en",
            model=model,
            workers=workers,
            hsw_workers=hsw,
            speed=float(self.sb_speed.value()),
            proxy_api_key=str(px_key or ""),
            proxy_lines=proxy_lines,
            advanced=self._advanced,
        )
        self._batch.log.connect(self._set_status)
        self._batch.row_status.connect(self._on_row_status)
        self._batch.row_done.connect(self._on_row_done)
        self._batch.file_progress.connect(self._on_progress)
        self._batch.finished.connect(self._on_finished)
        self._batch.start()
        # presence: online = đang gen (admin)
        import time as _time
        import uuid as _uuid

        self._gen_session_id = _uuid.uuid4().hex[:16]
        self._gen_ok = 0
        self._gen_fail = 0
        self._gen_total = len(self._chunks)
        self._gen_workers = workers
        self._presence_last = 0.0
        try:
            accounts.report_gen_start(
                self.user,
                kind="preview",
                workers=workers,
                total=len(self._chunks),
                label=f"{model} · {len(self._chunks)} đoạn",
                session_id=self._gen_session_id,
            )
            self._presence_last = _time.time()
        except Exception:
            pass
        self._set_status(
            f"Đang tạo {len(self._chunks)} đoạn · model {model} · "
            f"{workers} luồng · {len(proxy_lines)} proxy · ~{total_chars:,} ký tự…"
        )

    def _stop(self):
        if self._batch:
            self._batch.stop()
            self._set_status("Đang dừng…")

    def _on_row_status(self, row: int, status: str):
        self._set_chunk_status(row, status)
        # jump view to active row page (optional, gentle)
        page = row // CHUNK_PAGE_SIZE
        st_l = (status or "").lower()
        if page != self._chunk_page and "đang" in st_l:
            # only auto-follow first few running to avoid thrashing
            if (
                sum(1 for s in self._chunk_status.values() if "đang" in str(s).lower())
                <= 2
            ):
                self._chunk_page = page
                self._render_chunk_page()

    def _on_row_done(self, row: int, ok: bool, path: str, err: str):
        if 0 <= row < len(self._chunks):
            fname = self._chunks[row].get("file") or ""
            if ok:
                self._chunks[row]["path"] = path
                # trừ gói theo nội dung plain (không tính thẻ <break>)
                ch = self._chunks[row]
                try:
                    from output_layout import plain_char_count

                    n = int(ch.get("plain_chars") or plain_char_count(ch.get("text") or ""))
                except Exception:
                    n = len(ch.get("text") or "")
                try:
                    accounts.consume_chars(self.user.get("id") or "", n)
                    full = accounts.get_account(self.user.get("id") or "")
                    if full:
                        self.user = full
                        self._refresh_account_badge()
                except Exception:
                    pass
                self._set_chunk_status(row, "Xong")
                self._bump_queue_file(fname, ok=True)
                self._gen_ok = int(getattr(self, "_gen_ok", 0) or 0) + 1
                # refresh size cell if visible
                start = self._chunk_page * CHUNK_PAGE_SIZE
                if start <= row < start + CHUNK_PAGE_SIZE:
                    local = row - start
                    if path and os.path.exists(path) and local < self.tbl_sub.rowCount():
                        try:
                            self.tbl_sub.setItem(
                                local,
                                2,
                                QtWidgets.QTableWidgetItem(
                                    f"{os.path.getsize(path) // 1024} KB"
                                ),
                            )
                        except Exception:
                            pass
            else:
                self._gen_fail = int(getattr(self, "_gen_fail", 0) or 0) + 1
                self._set_chunk_status(row, "Lỗi")
                self._bump_queue_file(fname, ok=False)
                start = self._chunk_page * CHUNK_PAGE_SIZE
                if start <= row < start + CHUNK_PAGE_SIZE:
                    local = row - start
                    if local < self.tbl_sub.rowCount():
                        item = self.tbl_sub.item(local, 5)
                        if item:
                            item.setToolTip(err or "Lỗi không rõ")

            # throttle presence heartbeat ~20s (online = đang gen)
            try:
                import time as _time

                now = _time.time()
                if now - float(getattr(self, "_presence_last", 0) or 0) >= 20:
                    self._presence_last = now
                    accounts.report_gen_heartbeat(
                        self.user,
                        kind="preview",
                        workers=int(getattr(self, "_gen_workers", 0) or 0),
                        ok=int(getattr(self, "_gen_ok", 0) or 0),
                        fail=int(getattr(self, "_gen_fail", 0) or 0),
                        total=int(getattr(self, "_gen_total", 0) or 0),
                        session_id=str(getattr(self, "_gen_session_id", "") or ""),
                    )
            except Exception:
                pass

    def _on_progress(self, _fi: int, cur: int, total: int):
        self.progress.setValue(int(100 * cur / max(1, total)))
        self.lbl_result.setText(f"Tiến độ: {cur}/{total} đoạn")

    def _on_finished(self, ok: int, fail: int):
        self.bt_start.setEnabled(True)
        self.bt_stop.setEnabled(False)
        self.lbl_result.setText(f"Xong: {ok} thành công / {fail} lỗi")
        self.progress.setValue(100 if ok > 0 else self.progress.value())
        try:
            accounts.report_gen_stop(
                self.user,
                kind="preview",
                ok=int(ok or 0),
                fail=int(fail or 0),
                total=int(getattr(self, "_gen_total", 0) or 0),
                session_id=str(getattr(self, "_gen_session_id", "") or ""),
            )
        except Exception:
            pass

        # refresh queue 100% + merge badge
        merged_paths: list[str] = []
        seen_m: set[str] = set()
        for fn in list(getattr(self, "_file_total", {}) or {}):
            total = max(1, int(self._file_total.get(fn) or 1))
            self._file_done[fn] = total
            row = self._file_row.get(fn)
            merged = ""
            for ch in self._chunks:
                if ch.get("file") == fn:
                    merged = ch.get("merged_path") or ""
                    break
            if merged and os.path.isfile(merged) and os.path.getsize(merged) > 500:
                if merged not in seen_m:
                    seen_m.add(merged)
                    merged_paths.append(merged)
                st_txt = "Xong · đã merge"
            else:
                st_txt = "Xong" if fail == 0 or ok > 0 else "Lỗi"
            if row is not None and row < self.tbl_queue.rowCount():
                it = QtWidgets.QTableWidgetItem(st_txt)
                it.setForeground(
                    QtGui.QColor("#166534" if "Xong" in st_txt else "#991b1b")
                )
                self.tbl_queue.setItem(row, 2, it)
                self.tbl_queue.setItem(row, 3, QtWidgets.QTableWidgetItem("100%"))

        extra = f" · {len(merged_paths)} file tổng đã merge" if merged_paths else ""
        status = f"Hoàn tất — thành công {ok}, lỗi {fail}{extra}"
        self._set_status(status)
        self._render_chunk_page()
        self._batch = None

        # Popup thông báo gen thành công
        if ok > 0 and fail == 0:
            lines = [
                f"Tạo audio thành công: {ok} đoạn.",
            ]
            if merged_paths:
                lines.append("")
                lines.append("File tổng (merge):")
                for mp in merged_paths[:8]:
                    try:
                        rel = os.path.relpath(
                            mp, self.ed_output_dir.text().strip() or mp
                        )
                    except Exception:
                        rel = os.path.basename(mp)
                    kb = os.path.getsize(mp) // 1024
                    lines.append(f"  • {rel} ({kb} KB)")
                if len(merged_paths) > 8:
                    lines.append(f"  … và {len(merged_paths) - 8} file khác")
            else:
                lines.append("(Chưa tạo được file merge — kiểm tra doan_*.mp3)")
            lines.append("")
            lines.append(f"Thư mục: {self.ed_output_dir.text().strip()}")
            QtWidgets.QMessageBox.information(
                self,
                "Gen thành công",
                "\n".join(lines),
            )
        elif ok > 0 and fail > 0:
            QtWidgets.QMessageBox.warning(
                self,
                "Gen hoàn tất (có lỗi)",
                f"Thành công {ok} đoạn, lỗi {fail} đoạn.\n"
                f"File merge: {len(merged_paths)}.\n"
                f"Thư mục: {self.ed_output_dir.text().strip()}",
            )
        elif fail > 0:
            QtWidgets.QMessageBox.critical(
                self,
                "Gen thất bại",
                f"Không tạo được đoạn nào thành công (lỗi {fail}).",
            )

    def _open_out(self):
        d = self.ed_output_dir.text().strip()
        if d and os.path.isdir(d):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))

    def _open_edit_mp3(self):
        """Nút nhỏ → tab Edit MP3; nạp file merge/doan nếu có."""
        paths = []
        # ưu tiên file merge + doan đã gen
        for ch in self._chunks:
            mp = ch.get("merged_path") or ""
            if mp and os.path.isfile(mp) and mp not in paths:
                paths.append(mp)
            op = ch.get("path") or ch.get("out_path") or ""
            if op and os.path.isfile(op) and op not in paths:
                paths.append(op)
        if not paths:
            d = self.ed_output_dir.text().strip()
            if d and os.path.isdir(d):
                paths = sorted(
                    str(p) for p in Path(d).rglob("*.mp3") if p.is_file()
                )[:50]
        if hasattr(self.main_window, "show_edit_mp3_tab"):
            self.main_window.show_edit_mp3_tab(paths or None)
        else:
            self._set_status("Không mở được tab Edit MP3")

    def _open_chunk(self, local_row: int, _col: int):
        abs_row = self._chunk_page * CHUNK_PAGE_SIZE + local_row
        if 0 <= abs_row < len(self._chunks):
            ch = self._chunks[abs_row]
            p = ch.get("path") or ch.get("out_path")
            if p and os.path.exists(p):
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p))
            else:
                # mở folder file nếu chưa có mp3
                d = ch.get("file_dir") or ""
                if d and os.path.isdir(d):
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))

    def cleanup(self):
        if self._batch:
            self._batch.stop()
            self._batch.wait(5000)
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.wait(3000)
        if self._voice_worker and self._voice_worker.isRunning():
            self._voice_worker.wait(3000)

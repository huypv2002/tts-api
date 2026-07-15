# -*- coding: utf-8 -*-
"""
Tab Edit MP3 — cắt / ghép / nối / chia (FFmpeg COPY MODE only).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QHeaderView

from ffmpeg_tools import (
    concat_copy,
    cut_copy,
    find_ffmpeg,
    find_ffprobe,
    format_ts,
    parse_ts,
    probe_info,
    remove_segment_copy,
    split_at_timestamps_copy,
    split_equal_copy,
)


class EditMp3Tab(QtWidgets.QWidget):
    def __init__(self, main_window, default_dir: str = ""):
        super().__init__()
        self.main = main_window
        self._files: List[str] = []
        self._default_dir = default_dir or ""
        self._setup_ui()
        self._refresh_tool_status()

    def _setup_ui(self):
        self.setStyleSheet(
            """
            QWidget { background: #f2f2f2; color: #171717; font-size: 13px; }
            QFrame#card {
                background: #ffffff; border: 1px solid #e5e5e5; border-radius: 12px;
            }
            QLabel#cardTitle { color: #171717; font-size: 13px; font-weight: 600; }
            QLabel#muted { color: #737373; font-size: 12px; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {
                background: #fff; border: 1px solid #d4d4d4; border-radius: 8px;
                padding: 8px 10px; min-height: 20px;
            }
            QPushButton {
                background: #171717; color: #fff; border: 0; border-radius: 8px;
                padding: 10px 14px; font-weight: 600; min-height: 20px;
            }
            QPushButton:hover { background: #333; }
            QPushButton#ghost {
                background: #fff; color: #171717; border: 1px solid #d4d4d4;
            }
            QPushButton#ghost:hover { background: #f5f5f5; }
            QPushButton#danger { background: #991b1b; }
            QGroupBox {
                background: #fff; border: 1px solid #e5e5e5; border-radius: 10px;
                margin-top: 12px; padding: 14px 12px 12px; font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QScrollArea { border: 0; background: transparent; }
            QWidget#scrollInner { background: #f2f2f2; }
            QTableWidget {
                background: #fff; border: 1px solid #e5e5e5; border-radius: 8px;
                gridline-color: #f0f0f0;
            }
            QHeaderView::section {
                background: #f5f5f5; border: 0; border-bottom: 1px solid #e5e5e5;
                padding: 6px; font-weight: 600;
            }
            """
        )
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        outer.addWidget(scroll)

        inner = QtWidgets.QWidget()
        inner.setObjectName("scrollInner")
        inner.setMinimumWidth(980)
        scroll.setWidget(inner)

        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(14, 12, 14, 16)
        root.setSpacing(12)

        # header (sticky-feel: top of scroll content)
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Edit MP3 · FFmpeg copy mode (−c copy)")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_ff = QtWidgets.QLabel("")
        self.lbl_ff.setObjectName("muted")
        head.addWidget(self.lbl_ff)
        self.bt_back = QtWidgets.QPushButton("← TTS")
        self.bt_back.setObjectName("ghost")
        self.bt_back.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_back.clicked.connect(self._go_tts)
        head.addWidget(self.bt_back)
        root.addLayout(head)

        hint = QtWidgets.QLabel(
            "Chỉ stream copy — nhanh, không re-encode. Cắt MP3 có thể lệch nhẹ keyframe. "
            "Cuộn dọc nếu màn hình thấp."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 0)

        # ── left: file list ──
        left = QtWidgets.QFrame()
        left.setObjectName("card")
        left.setMinimumWidth(420)
        left.setMinimumHeight(360)
        left_l = QtWidgets.QVBoxLayout(left)
        left_l.setContentsMargins(12, 12, 12, 12)
        left_l.setSpacing(8)
        lt = QtWidgets.QLabel("Danh sách file (thứ tự = thứ tự nối)")
        lt.setObjectName("cardTitle")
        left_l.addWidget(lt)

        row_bt = QtWidgets.QHBoxLayout()
        self.bt_add = QtWidgets.QPushButton("Thêm MP3")
        self.bt_add.setObjectName("ghost")
        self.bt_add_folder = QtWidgets.QPushButton("Thêm folder")
        self.bt_add_folder.setObjectName("ghost")
        self.bt_rm = QtWidgets.QPushButton("Xóa chọn")
        self.bt_rm.setObjectName("ghost")
        self.bt_clear = QtWidgets.QPushButton("Xóa hết")
        self.bt_clear.setObjectName("ghost")
        for b in (self.bt_add, self.bt_add_folder, self.bt_rm, self.bt_clear):
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setMinimumHeight(34)
            row_bt.addWidget(b)
        left_l.addLayout(row_bt)

        row_ord = QtWidgets.QHBoxLayout()
        self.bt_up = QtWidgets.QPushButton("↑ Lên")
        self.bt_up.setObjectName("ghost")
        self.bt_down = QtWidgets.QPushButton("↓ Xuống")
        self.bt_down.setObjectName("ghost")
        self.bt_probe = QtWidgets.QPushButton("Probe duration")
        self.bt_probe.setObjectName("ghost")
        for b in (self.bt_up, self.bt_down, self.bt_probe):
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setMinimumHeight(34)
            row_ord.addWidget(b)
        left_l.addLayout(row_ord)

        self.tbl = QtWidgets.QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["#", "Tên", "Thời lượng", "KB"])
        self.tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setMinimumHeight(260)
        h = self.tbl.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl.setColumnWidth(0, 36)
        self.tbl.setColumnWidth(2, 90)
        self.tbl.setColumnWidth(3, 64)
        left_l.addWidget(self.tbl, 1)
        body.addWidget(left, 3)

        # ── right: tools (vertical scroll via outer scroll) ──
        right_w = QtWidgets.QWidget()
        right_w.setMinimumWidth(360)
        right = QtWidgets.QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        body.addWidget(right_w, 2)

        def _min_fields(*widgets):
            for w in widgets:
                w.setMinimumHeight(34)

        # output
        out_box = QtWidgets.QGroupBox("File xuất")
        out_l = QtWidgets.QVBoxLayout(out_box)
        out_l.setSpacing(8)
        row_o = QtWidgets.QHBoxLayout()
        self.ed_out = QtWidgets.QLineEdit()
        self.ed_out.setPlaceholderText("Đường dẫn file / folder xuất…")
        self.ed_out.setMinimumHeight(36)
        self.bt_out = QtWidgets.QPushButton("…")
        self.bt_out.setObjectName("ghost")
        self.bt_out.setFixedSize(40, 36)
        row_o.addWidget(self.ed_out, 1)
        row_o.addWidget(self.bt_out)
        out_l.addLayout(row_o)
        right.addWidget(out_box)

        # join
        join_box = QtWidgets.QGroupBox("Nối / Ghép (concat · copy)")
        jl = QtWidgets.QVBoxLayout(join_box)
        jl.setSpacing(8)
        jl_lbl = QtWidgets.QLabel(
            "Nối tất cả file trong danh sách theo thứ tự → 1 file."
        )
        jl_lbl.setWordWrap(True)
        jl_lbl.setObjectName("muted")
        jl.addWidget(jl_lbl)
        self.bt_join = QtWidgets.QPushButton("Nối tất cả → file xuất")
        self.bt_join.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_join.setMinimumHeight(40)
        jl.addWidget(self.bt_join)
        right.addWidget(join_box)

        # cut
        cut_box = QtWidgets.QGroupBox("Cắt đoạn (copy)")
        cl = QtWidgets.QFormLayout(cut_box)
        cl.setSpacing(10)
        cl.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        cl.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self.ed_cut_start = QtWidgets.QLineEdit("0")
        self.ed_cut_start.setPlaceholderText("0 hoặc 0:00.000")
        self.ed_cut_end = QtWidgets.QLineEdit("")
        self.ed_cut_end.setPlaceholderText("trống = đến hết · VD 1:30.5")
        _min_fields(self.ed_cut_start, self.ed_cut_end)
        cl.addRow("Bắt đầu", self.ed_cut_start)
        cl.addRow("Kết thúc", self.ed_cut_end)
        self.bt_cut = QtWidgets.QPushButton("Cắt file đang chọn")
        self.bt_cut.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_cut.setMinimumHeight(40)
        cl.addRow(self.bt_cut)
        right.addWidget(cut_box)

        # remove segment
        rm_box = QtWidgets.QGroupBox("Xóa đoạn giữa (copy)")
        rl = QtWidgets.QFormLayout(rm_box)
        rl.setSpacing(10)
        rl.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self.ed_rm_start = QtWidgets.QLineEdit("10")
        self.ed_rm_end = QtWidgets.QLineEdit("20")
        _min_fields(self.ed_rm_start, self.ed_rm_end)
        rl.addRow("Xóa từ", self.ed_rm_start)
        rl.addRow("đến", self.ed_rm_end)
        self.bt_rm_seg = QtWidgets.QPushButton("Xóa đoạn → file xuất")
        self.bt_rm_seg.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_rm_seg.setMinimumHeight(40)
        rl.addRow(self.bt_rm_seg)
        right.addWidget(rm_box)

        # split
        sp_box = QtWidgets.QGroupBox("Chia file (copy)")
        sl = QtWidgets.QFormLayout(sp_box)
        sl.setSpacing(10)
        sl.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self.sb_parts = QtWidgets.QSpinBox()
        self.sb_parts.setRange(2, 50)
        self.sb_parts.setValue(2)
        self.sb_parts.setMinimumHeight(34)
        sl.addRow("Chia đều N phần", self.sb_parts)
        self.bt_split_eq = QtWidgets.QPushButton("Chia đều → folder xuất")
        self.bt_split_eq.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_split_eq.setMinimumHeight(40)
        sl.addRow(self.bt_split_eq)
        self.ed_cuts = QtWidgets.QLineEdit()
        self.ed_cuts.setPlaceholderText("mốc cắt giây, VD: 30, 90, 2:00")
        self.ed_cuts.setMinimumHeight(34)
        sl.addRow("Mốc cắt", self.ed_cuts)
        self.bt_split_at = QtWidgets.QPushButton("Chia tại mốc → folder xuất")
        self.bt_split_at.setCursor(QtCore.Qt.PointingHandCursor)
        self.bt_split_at.setMinimumHeight(40)
        sl.addRow(self.bt_split_at)
        right.addWidget(sp_box)

        right.addStretch(1)

        # log
        log_box = QtWidgets.QGroupBox("Log")
        ll = QtWidgets.QVBoxLayout(log_box)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(140)
        self.log.setMaximumHeight(220)
        ll.addWidget(self.log)
        root.addWidget(log_box)

        # signals
        self.bt_add.clicked.connect(self._add_files)
        self.bt_add_folder.clicked.connect(self._add_folder)
        self.bt_rm.clicked.connect(self._remove_selected)
        self.bt_clear.clicked.connect(self._clear)
        self.bt_up.clicked.connect(lambda: self._move(-1))
        self.bt_down.clicked.connect(lambda: self._move(1))
        self.bt_probe.clicked.connect(self._probe_all)
        self.bt_out.clicked.connect(self._pick_out)
        self.bt_join.clicked.connect(self._do_join)
        self.bt_cut.clicked.connect(self._do_cut)
        self.bt_rm_seg.clicked.connect(self._do_remove_seg)
        self.bt_split_eq.clicked.connect(self._do_split_eq)
        self.bt_split_at.clicked.connect(self._do_split_at)

        if self._default_dir and os.path.isdir(self._default_dir):
            self.ed_out.setText(
                os.path.join(self._default_dir, "edit_out.mp3")
            )

    # ── helpers ─────────────────────────────────────────────
    def _go_tts(self):
        if hasattr(self.main, "show_tts_tab"):
            self.main.show_tts_tab()

    def _refresh_tool_status(self):
        ff = find_ffmpeg()
        fp = find_ffprobe()
        if ff and fp:
            self.lbl_ff.setText(f"ffmpeg ✓ · ffprobe ✓")
            self.lbl_ff.setStyleSheet("color: #166534; font-size: 12px;")
        else:
            self.lbl_ff.setText("⚠ Cần cài ffmpeg + ffprobe trong PATH")
            self.lbl_ff.setStyleSheet("color: #991b1b; font-size: 12px;")

    def _log(self, msg: str):
        self.log.appendPlainText(msg)

    def _selected_path(self) -> Optional[str]:
        rows = self.tbl.selectionModel().selectedRows()
        if not rows:
            if len(self._files) == 1:
                return self._files[0]
            return None
        i = rows[0].row()
        if 0 <= i < len(self._files):
            return self._files[i]
        return None

    def _reload_table(self):
        self.tbl.setRowCount(len(self._files))
        for i, p in enumerate(self._files):
            info = probe_info(p) if os.path.isfile(p) else {
                "name": os.path.basename(p),
                "duration_str": "?",
                "size_kb": 0,
            }
            self.tbl.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl.setItem(i, 1, QtWidgets.QTableWidgetItem(info.get("name") or ""))
            self.tbl.setItem(
                i, 2, QtWidgets.QTableWidgetItem(info.get("duration_str") or "?")
            )
            self.tbl.setItem(
                i, 3, QtWidgets.QTableWidgetItem(str(info.get("size_kb") or 0))
            )

    def _add_paths(self, paths: List[str]):
        n = 0
        for p in paths:
            p = str(p)
            if not p.lower().endswith((".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg")):
                continue
            if p not in self._files and os.path.isfile(p):
                self._files.append(p)
                n += 1
        self._reload_table()
        if n:
            self._log(f"+ {n} file")
            if not self.ed_out.text().strip() and self._files:
                stem = Path(self._files[0]).stem
                base = self._default_dir or str(Path(self._files[0]).parent)
                self.ed_out.setText(os.path.join(base, f"{stem}_edit.mp3"))

    def _add_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Chọn file audio",
            self._default_dir or "",
            "Audio (*.mp3 *.m4a *.wav *.aac *.flac *.ogg);;All (*.*)",
        )
        if paths:
            self._add_paths(paths)

    def _add_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder chứa MP3", self._default_dir or ""
        )
        if not d:
            return
        paths = sorted(
            str(p)
            for p in Path(d).rglob("*")
            if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
        )
        self._add_paths(paths)

    def _remove_selected(self):
        rows = sorted(
            {i.row() for i in self.tbl.selectionModel().selectedRows()}, reverse=True
        )
        for i in rows:
            if 0 <= i < len(self._files):
                del self._files[i]
        self._reload_table()

    def _clear(self):
        self._files.clear()
        self._reload_table()

    def _move(self, delta: int):
        rows = self.tbl.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        j = i + delta
        if j < 0 or j >= len(self._files):
            return
        self._files[i], self._files[j] = self._files[j], self._files[i]
        self._reload_table()
        self.tbl.selectRow(j)

    def _probe_all(self):
        self._reload_table()
        total = 0.0
        for p in self._files:
            total += probe_info(p).get("duration") or 0
        self._log(f"Probe xong · tổng ~ {format_ts(total)} ({len(self._files)} file)")

    def _pick_out(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "File xuất",
            self.ed_out.text().strip() or self._default_dir or "out.mp3",
            "MP3 (*.mp3);;All (*.*)",
        )
        if path:
            if not path.lower().endswith(".mp3"):
                path += ".mp3"
            self.ed_out.setText(path)

    def _out_path(self, default_name: str = "edit_out.mp3") -> Optional[str]:
        p = self.ed_out.text().strip()
        if not p:
            base = self._default_dir or os.getcwd()
            p = os.path.join(base, default_name)
            self.ed_out.setText(p)
        # if user picked a directory-like path without extension for split
        return p

    def _out_dir_for_split(self) -> str:
        p = self.ed_out.text().strip()
        if p.lower().endswith((".mp3", ".m4a", ".wav")):
            d = os.path.join(str(Path(p).parent), Path(p).stem + "_parts")
        elif p and not os.path.splitext(p)[1]:
            d = p
        else:
            d = os.path.join(self._default_dir or os.getcwd(), "mp3_parts")
        os.makedirs(d, exist_ok=True)
        return d

    # ── actions ─────────────────────────────────────────────
    def _do_join(self):
        if not self._files:
            self._log("⚠ Chưa có file")
            return
        out = self._out_path("joined.mp3")
        if not out:
            return
        self._log(f"Nối {len(self._files)} file…")
        ok, msg = concat_copy(self._files, out)
        self._log(("✅ " if ok else "❌ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Xong", msg + f"\n{out}")

    def _do_cut(self):
        src = self._selected_path()
        if not src:
            self._log("⚠ Chọn 1 file trong bảng")
            return
        try:
            start = parse_ts(self.ed_cut_start.text())
            end_raw = self.ed_cut_end.text().strip()
            end = parse_ts(end_raw) if end_raw else None
        except ValueError as e:
            self._log(f"❌ {e}")
            return
        stem = Path(src).stem
        out = self._out_path(f"{stem}_cut.mp3")
        self._log(f"Cắt {os.path.basename(src)}…")
        ok, msg = cut_copy(src, out, start, end)
        self._log(("✅ " if ok else "❌ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Xong", msg + f"\n{out}")

    def _do_remove_seg(self):
        src = self._selected_path()
        if not src:
            self._log("⚠ Chọn 1 file")
            return
        try:
            a = parse_ts(self.ed_rm_start.text())
            b = parse_ts(self.ed_rm_end.text())
        except ValueError as e:
            self._log(f"❌ {e}")
            return
        out = self._out_path(f"{Path(src).stem}_rmseg.mp3")
        self._log(f"Xóa đoạn {format_ts(a)}–{format_ts(b)}…")
        ok, msg = remove_segment_copy(src, out, a, b)
        self._log(("✅ " if ok else "❌ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Xong", msg + f"\n{out}")

    def _do_split_eq(self):
        src = self._selected_path()
        if not src:
            self._log("⚠ Chọn 1 file")
            return
        d = self._out_dir_for_split()
        n = self.sb_parts.value()
        self._log(f"Chia đều {n} phần → {d}")
        ok, msg, outs = split_equal_copy(src, d, n, prefix=Path(src).stem)
        self._log(("✅ " if ok else "❌ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Xong", f"{msg}\n{len(outs)} file trong:\n{d}"
            )

    def _do_split_at(self):
        src = self._selected_path()
        if not src:
            self._log("⚠ Chọn 1 file")
            return
        raw = self.ed_cuts.text().strip()
        if not raw:
            self._log("⚠ Nhập mốc cắt")
            return
        try:
            marks = [parse_ts(x.strip()) for x in raw.replace(";", ",").split(",") if x.strip()]
        except ValueError as e:
            self._log(f"❌ {e}")
            return
        d = self._out_dir_for_split()
        self._log(f"Chia tại {marks} → {d}")
        ok, msg, outs = split_at_timestamps_copy(
            src, d, marks, prefix=Path(src).stem
        )
        self._log(("✅ " if ok else "❌ ") + msg)
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Xong", f"{msg}\n{len(outs)} file:\n{d}"
            )

    def open_with_files(self, paths: Optional[List[str]] = None):
        """Gọi từ tab TTS — nạp file sẵn."""
        if paths:
            self._add_paths(list(paths))
        self._refresh_tool_status()

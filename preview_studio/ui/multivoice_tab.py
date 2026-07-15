# -*- coding: utf-8 -*-
"""
Tab Hội thoại (multi-voice) — UI tối giản:

  1) Bảng gán giọng: Tên nhân vật → voice đã lưu
  2) Ô script: mỗi dòng «Tên: nội dung»
  3) Bắt đầu → từng lượt TTS anonymous → merge 1 file MP3
"""
from __future__ import annotations

import os
import re
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QThread, Signal

import accounts_store as accounts
from multivoice import default_script, parse_script, speakers_in_script
from output_layout import merge_audio_with_gaps, safe_stem

DEFAULT_MODEL = "eleven_v3"
DEFAULT_VOICE = "NOpBlnGInO9m6vDvFkFC"


class MultivoiceWorker(QThread):
    """
    Gen hội thoại SONG SONG theo số luồng admin (max_workers / proxy lanes).

    Độ tin cậy (xử lý lỗi gần như tuần tự):
      1) Mỗi lượt = 1 job độc lập, voice riêng, retry riêng ≤ max_attempts
      2) Lỗi 401/mạng/captcha → pipeline đổi IP / soft-fail (không poison batch)
      3) File OK ghi atomic; lượt fail chỉ re-queue bản thân, không ảnh hưởng lượt khác
      4) Vòng ngoài (outer): chỉ gen lại lượt còn thiếu file trên disk
      5) Vòng cuối “cứu hộ” 1 luồng (tuần tự) — retry dễ như gen tuần tự cũ
      6) Merge chỉ khi ĐỦ lượt theo đúng thứ tự (không theo completion order)
    """

    log = Signal(str)
    turn_status = Signal(int, str, str)  # global row, status, out_path
    file_status = Signal(int, str)  # file queue row, status
    progress = Signal(int, int)  # done_ok, total
    finished_ok = Signal(int, int, str)  # ok_turns, fail_turns, last_merged
    failed = Signal(str)

    # Vòng song song re-queue (mỗi vòng = run_jobs mới, attempts reset)
    OUTER_ROUNDS = 5
    # Mỗi job trong 1 vòng pipeline (giống Tạo audio)
    MAX_ATTEMPTS = 40
    # Vòng cứu hộ tuần tự sau khi song song vẫn thiếu
    RESCUE_ROUNDS = 2
    RESCUE_ATTEMPTS = 40

    def __init__(
        self,
        jobs: List[dict],
        output_dir: str,
        proxy: str,
        proxy_api_key: str = "",
        proxy_lines: Optional[List[dict]] = None,
        lang: str = "vi",
        speed: float = 1.0,
        gap_seconds: float = 0.35,
        workers: int = 1,
        hsw_workers: int = 0,
        max_attempts: int = 0,
    ):
        super().__init__()
        self.jobs = list(jobs or [])
        self.output_dir = output_dir
        self.proxy = proxy or ""
        self.proxy_api_key = proxy_api_key or ""
        self.proxy_lines = list(proxy_lines or [])
        self.lang = lang or "vi"
        self.speed = float(speed or 1.0)
        self.gap_seconds = max(0.0, float(gap_seconds or 0))
        self.workers = max(1, min(5, int(workers or 1)))
        self.hsw_workers = int(hsw_workers or 0)
        self.max_attempts = int(max_attempts or self.MAX_ATTEMPTS)
        self._stop = False

    def stop(self):
        self._stop = True

    @staticmethod
    def _good_mp3(path: str) -> bool:
        try:
            return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 500
        except Exception:
            return False

    @staticmethod
    def _purge_bad(path: str) -> None:
        """Xóa file rỗng/dở để vòng sau không skip nhầm."""
        try:
            if not path or not os.path.isfile(path):
                return
            if os.path.getsize(path) <= 500:
                os.remove(path)
        except Exception:
            pass

    def run(self):
        try:
            import asyncio
            import random
            from pathlib import Path as P

            from gen_pipeline import run_jobs

            # ── Flatten turns → pipeline jobs (giữ meta thứ tự merge) ──
            flat: List[dict] = []
            file_groups: List[dict] = []
            global_i = 0
            for ji, job in enumerate(self.jobs):
                turns = list(job.get("turns") or [])
                pname = job.get("project_name") or f"dialogue_{ji+1}"
                out_root = P(self.output_dir) / safe_stem(pname)
                out_root.mkdir(parents=True, exist_ok=True)
                merged = str(out_root / f"{safe_stem(pname)}.mp3")
                group_rows: List[int] = []
                group_paths: List[str] = []
                for ti, turn in enumerate(turns):
                    speaker = turn.get("speaker") or f"S{ti+1}"
                    voice = (turn.get("voice_id") or DEFAULT_VOICE).strip()
                    text = (turn.get("text") or "").strip()
                    safe_sp = re.sub(r"[^\w\-]+", "_", speaker, flags=re.U)[:24]
                    path = str(out_root / f"turn_{ti+1:02d}_{safe_sp}.mp3")
                    turn["out_path"] = path
                    turn["file_dir"] = str(out_root)
                    turn["merged_path"] = merged
                    # Xóa file dở dang cũ trước khi gen
                    self._purge_bad(path)
                    flat.append(
                        {
                            "row": global_i,
                            "text": text,
                            "out_path": path,
                            "voice": voice,
                            "voice_id": voice,
                            "speaker": speaker,
                            "file_idx": ji,
                            "turn_idx": ti,
                        }
                    )
                    group_rows.append(global_i)
                    group_paths.append(path)
                    global_i += 1
                file_groups.append(
                    {
                        "file_idx": ji,
                        "project_name": pname,
                        "out_root": str(out_root),
                        "merged": merged,
                        "rows": group_rows,
                        "paths": group_paths,  # ordered for merge
                    }
                )

            total = len(flat)
            if total == 0:
                self.finished_ok.emit(0, 0, "")
                return

            n_proxy = max(1, len(self.proxy_lines) if self.proxy_lines else 1)
            # workers = admin max_workers = số TTS đồng thời (1 proxy có thể 3 luồng)
            n_workers = max(1, min(5, int(self.workers or 1)))
            hsw = self.hsw_workers or min(8, max(3, n_workers * 2))
            self.log.emit(
                f"Song song {n_workers} luồng TTS · {n_proxy} proxy · "
                f"{total} lượt · retry/lượt ≤{self.max_attempts} · "
                f"vòng song song ≤{self.OUTER_ROUNDS} · "
                f"cứu hộ tuần tự ≤{self.RESCUE_ROUNDS}"
            )

            async def _run():
                def count_done() -> int:
                    return sum(1 for j in flat if self._good_mp3(j["out_path"]))

                def pending_jobs() -> List[dict]:
                    """Chỉ lượt thiếu file — mỗi vòng attempts reset trong run_jobs."""
                    out: List[dict] = []
                    for j in flat:
                        p = j["out_path"]
                        if self._good_mp3(p):
                            continue
                        self._purge_bad(p)
                        # copy sạch — không mang attempts cũ / state lane
                        out.append(
                            {
                                "row": j["row"],
                                "text": j["text"],
                                "out_path": p,
                                "voice": j["voice"],
                                "voice_id": j.get("voice_id") or j["voice"],
                                "speaker": j.get("speaker") or "",
                            }
                        )
                    return out

                def on_status(row: int, status: str):
                    if self._stop:
                        return
                    path = ""
                    for j in flat:
                        if j["row"] == row:
                            path = j.get("out_path") or ""
                            break
                    self.turn_status.emit(row, status, path)

                def on_start(row: int):
                    if self._stop:
                        return
                    j = next((x for x in flat if x["row"] == row), None)
                    if not j:
                        return
                    self.log.emit(
                        f"▶ Lượt {row+1}/{total} «{j.get('speaker')}» · "
                        f"{len(j.get('text') or '')} ký tự"
                    )

                def on_done(row: int, success: bool, path: str, err: str):
                    if success:
                        self.turn_status.emit(row, "Xong", path or "")
                        self.log.emit(
                            f"✅ Lượt {row+1}: {os.path.basename(path or '')}"
                        )
                    else:
                        # Chưa final — outer/rescue còn retry
                        self.turn_status.emit(row, "Chờ thử lại…", path or "")
                        self.log.emit(
                            f"⚠ Lượt {row+1} tạm fail: {(err or '')[:140]}"
                        )
                    self.progress.emit(count_done(), total)

                async def pass_jobs(
                    jobs_batch: List[dict],
                    *,
                    n_workers: int,
                    attempts: int,
                    hsw_n: int,
                    label: str,
                ) -> None:
                    if not jobs_batch or self._stop:
                        return
                    for j in jobs_batch:
                        self.turn_status.emit(
                            j["row"], "Xếp hàng…", j.get("out_path") or ""
                        )
                    self.log.emit(
                        f"── {label}: {len(jobs_batch)} lượt · "
                        f"{n_workers} luồng TTS · retry ≤{attempts} ──"
                    )
                    try:
                        await run_jobs(
                            jobs_batch,
                            proxy_url=self.proxy,
                            proxy_api_key=self.proxy_api_key,
                            proxy_lines=self.proxy_lines or None,
                            voice=DEFAULT_VOICE,  # overridden per-job voice
                            model=DEFAULT_MODEL,
                            lang=self.lang,
                            speed=self.speed,
                            hsw_workers=hsw_n,
                            workers=n_workers,  # admin max_workers
                            max_attempts=attempts,
                            tokens_per_lane=max(3, n_workers),
                            should_stop=lambda: self._stop,
                            on_start=on_start,
                            on_status=on_status,
                            on_done=on_done,
                        )
                    except Exception as e:
                        self.log.emit(f"⚠ Pipeline {label}: {e}")
                        await asyncio.sleep(1.5)

                # Skip lượt đã có sẵn
                for j in flat:
                    if self._good_mp3(j["out_path"]):
                        self.turn_status.emit(j["row"], "Xong", j["out_path"])
                self.progress.emit(count_done(), total)

                for fi, _g in enumerate(file_groups):
                    self.file_status.emit(fi, "Đang chạy")

                # ── Phase 1: song song theo n_lanes (admin) ──
                for outer in range(1, self.OUTER_ROUNDS + 1):
                    if self._stop:
                        self.log.emit("Đã dừng.")
                        break
                    pending = pending_jobs()
                    if not pending:
                        self.log.emit("Tất cả lượt đã có audio.")
                        break
                    await pass_jobs(
                        pending,
                        n_workers=n_workers,
                        attempts=self.max_attempts,
                        hsw_n=hsw,
                        label=f"Song song {outer}/{self.OUTER_ROUNDS}",
                    )
                    still = total - count_done()
                    self.progress.emit(count_done(), total)
                    if still == 0:
                        break
                    if outer < self.OUTER_ROUNDS and not self._stop:
                        # backoff tăng dần — cho proxy/IP nguội
                        wait = min(12.0, 1.5 * outer + random.uniform(0.3, 1.2))
                        self.log.emit(
                            f"Còn {still} lượt — chờ {wait:.0f}s rồi thử lại "
                            f"(chỉ lượt thiếu, lượt OK giữ nguyên)…"
                        )
                        await asyncio.sleep(wait)

                # ── Phase 2: cứu hộ tuần tự (1 luồng) — dễ retry như gen cũ ──
                for rescue in range(1, self.RESCUE_ROUNDS + 1):
                    if self._stop:
                        break
                    pending = pending_jobs()
                    if not pending:
                        break
                    wait = min(15.0, 3.0 * rescue)
                    self.log.emit(
                        f"🛟 Cứu hộ tuần tự {rescue}/{self.RESCUE_ROUNDS}: "
                        f"{len(pending)} lượt còn lại · 1 luồng · "
                        f"chờ {wait:.0f}s…"
                    )
                    await asyncio.sleep(wait)
                    # 1 lane + HSW nhỏ — ổn định hơn khi proxy mệt
                    await pass_jobs(
                        pending,
                        n_workers=1,  # tuần tự 1 luồng — dễ retry
                        attempts=self.RESCUE_ATTEMPTS,
                        hsw_n=min(4, max(2, hsw)),
                        label=f"Cứu hộ tuần tự {rescue}/{self.RESCUE_ROUNDS}",
                    )
                    self.progress.emit(count_done(), total)

                # ── Merge từng file (đúng thứ tự paths) ──
                last_merged = ""
                for g in file_groups:
                    fi = g["file_idx"]
                    paths = list(g["paths"])
                    missing = [p for p in paths if not self._good_mp3(p)]
                    if missing:
                        self.file_status.emit(fi, f"Thiếu {len(missing)} lượt")
                        self.log.emit(
                            f"❌ «{g['project_name']}»: thiếu {len(missing)}/"
                            f"{len(paths)} lượt — không merge"
                        )
                        for row, p in zip(g["rows"], paths):
                            if not self._good_mp3(p):
                                self.turn_status.emit(row, "Lỗi", p)
                        continue
                    if self._stop:
                        self.file_status.emit(fi, "Dừng")
                        continue
                    gaps = (
                        [self.gap_seconds] * (len(paths) - 1)
                        if len(paths) > 1
                        else []
                    )
                    mok, mmsg = merge_audio_with_gaps(paths, g["merged"], gaps)
                    if mok:
                        last_merged = g["merged"]
                        self.file_status.emit(fi, "Xong · đã merge")
                        self.log.emit(f"📦 {g['project_name']}: {mmsg}")
                    else:
                        self.file_status.emit(fi, "Xong (merge lỗi)")
                        self.log.emit(f"⚠ Merge {g['project_name']}: {mmsg}")

                ok = sum(1 for j in flat if self._good_mp3(j["out_path"]))
                fail = total - ok
                self.progress.emit(ok, total)
                self.finished_ok.emit(ok, fail, last_merged)

            asyncio.run(_run())
        except Exception as e:
            self.failed.emit(f"{e}\n{traceback.format_exc()[:400]}")


class MultivoiceTab(QtWidgets.QWidget):
    def __init__(
        self,
        main_window,
        user: dict,
        load_config: Callable,
        save_config: Callable,
    ):
        super().__init__()
        self.main_window = main_window
        self.user = user
        self.load_config = load_config
        self.save_config = save_config
        self._cfg = load_config()
        self._worker: Optional[MultivoiceWorker] = None
        self._turns: List[dict] = []
        self._setup_ui()
        self._load_state()

    # ── UI ──────────────────────────────────────────────────────────────
    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self.setStyleSheet(
            """
            QWidget { color: #171717; font-size: 12px; }
            QFrame#card {
                background: #ffffff; border: 1px solid #d4d4d4; border-radius: 8px;
            }
            QLabel#cardTitle { font-size: 13px; font-weight: 600; }
            QLabel#muted { color: #737373; font-size: 11px; }
            QLabel#badge {
                background: #f2f2f2; border: 1px solid #d4d4d4; border-radius: 7px;
                padding: 4px 8px; font-size: 11px;
            }
            QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox {
                background: #ffffff; border: 1px solid #c9c9c9; border-radius: 6px;
                padding: 5px 8px;
            }
            QPushButton {
                min-height: 28px; padding: 4px 12px; background: #fff;
                border: 1px solid #c9c9c9; border-radius: 6px;
            }
            QPushButton:hover { background: #f0f0f0; }
            QPushButton#primary {
                background: #171717; color: #fff; border-color: #171717; font-weight: 600;
            }
            QPushButton#primary:hover { background: #333; }
            QPushButton#danger:hover { background: #171717; color: #fff; }
            QTableWidget {
                background: #fff; border: 1px solid #d4d4d4; border-radius: 7px;
                gridline-color: #e8e8e8;
            }
            QHeaderView::section {
                background: #f5f5f5; padding: 6px; border: none;
                border-bottom: 1px solid #e5e5e5; font-weight: 600;
            }
            QScrollArea { background: transparent; border: none; }
            """
        )

        # ══ 2 cột giống tab Tạo audio: trái control · phải bảng ═══════
        def card(title: str):
            frame = QtWidgets.QFrame()
            frame.setObjectName("card")
            lay = QtWidgets.QVBoxLayout(frame)
            lay.setContentsMargins(12, 10, 12, 12)
            lay.setSpacing(8)
            lab = QtWidgets.QLabel(title)
            lab.setObjectName("cardTitle")
            lay.addWidget(lab)
            return frame, lay

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(10)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        left.setContentsMargins(0, 0, 6, 0)
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)

        # ── TRÁI: nhân vật (cao hơn) ──
        cast_card, cl = card("Nhân vật & giọng")
        cast_tip = QtWidgets.QLabel("Gán giọng đã lưu cho từng tên trong kịch bản.")
        cast_tip.setObjectName("muted")
        cl.addWidget(cast_tip)
        self.tbl_cast = QtWidgets.QTableWidget(0, 2)
        self.tbl_cast.setHorizontalHeaderLabels(["Tên nhân vật", "Giọng đọc"])
        self.tbl_cast.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.tbl_cast.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.Stretch
        )
        self.tbl_cast.verticalHeader().setVisible(False)
        self.tbl_cast.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_cast.setMinimumHeight(160)
        self.tbl_cast.setMaximumHeight(220)
        cl.addWidget(self.tbl_cast)
        crow = QtWidgets.QHBoxLayout()
        self.bt_cast_add = QtWidgets.QPushButton("＋ Thêm")
        self.bt_cast_del = QtWidgets.QPushButton("Xóa dòng")
        self.lbl_account = QtWidgets.QLabel("—")
        self.lbl_account.setObjectName("badge")
        crow.addWidget(self.bt_cast_add)
        crow.addWidget(self.bt_cast_del)
        crow.addStretch(1)
        crow.addWidget(self.lbl_account)
        cl.addLayout(crow)
        left.addWidget(cast_card, 0)

        # ── TRÁI: nội dung ──
        src_card, src_l = card("Nội dung")
        src_top = QtWidgets.QHBoxLayout()
        self.ed_input_path = QtWidgets.QLineEdit()
        self.ed_input_path.setReadOnly(True)
        self.ed_input_path.setPlaceholderText("Chọn tệp TXT / thư mục…")
        self.bt_txt = QtWidgets.QPushButton("Tệp TXT")
        self.bt_folder = QtWidgets.QPushButton("Thư mục")
        self.bt_sample = QtWidgets.QPushButton("Chèn mẫu")
        self.bt_clear_queue = QtWidgets.QPushButton("Xóa hàng đợi")
        src_top.addWidget(self.ed_input_path, 1)
        src_top.addWidget(self.bt_txt)
        src_top.addWidget(self.bt_folder)
        src_top.addWidget(self.bt_sample)
        src_top.addWidget(self.bt_clear_queue)
        src_l.addLayout(src_top)

        opt = QtWidgets.QHBoxLayout()
        opt.addWidget(QtWidgets.QLabel("Gap lượt"))
        self.sb_gap = QtWidgets.QDoubleSpinBox()
        self.sb_gap.setRange(0.0, 5.0)
        self.sb_gap.setSingleStep(0.05)
        self.sb_gap.setDecimals(2)
        self.sb_gap.setValue(0.35)
        self.sb_gap.setSuffix(" s")
        self.sb_gap.setMaximumWidth(90)
        opt.addWidget(self.sb_gap)
        opt.addStretch(1)
        self.lbl_preview = QtWidgets.QLabel("0 lượt · 0 tệp")
        self.lbl_preview.setObjectName("badge")
        opt.addWidget(self.lbl_preview)
        src_l.addLayout(opt)

        tip_src = QtWidgets.QLabel(
            "Import TXT → hàng đợi (phải). Ô dưới chỉ mẫu/gõ tay — "
            "format «Tên: câu» · [happy] · break."
        )
        tip_src.setObjectName("muted")
        tip_src.setWordWrap(True)
        src_l.addWidget(tip_src)

        self.ed_script = QtWidgets.QPlainTextEdit()
        self.ed_script.setPlaceholderText(default_script())
        self.ed_script.setMinimumHeight(100)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setPointSize(11)
        self.ed_script.setFont(font)
        self.ed_script.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 1px solid #c9c9c9; "
            "border-radius: 6px; padding: 6px; }"
        )
        src_l.addWidget(self.ed_script, 1)
        left.addWidget(src_card, 1)

        # ── TRÁI: xuất + chạy ──
        run_card, rl = card("Xuất file")
        row1 = QtWidgets.QHBoxLayout()
        self.ed_out = QtWidgets.QLineEdit()
        self.bt_browse = QtWidgets.QPushButton("…")
        row1.addWidget(self.ed_out, 1)
        row1.addWidget(self.bt_browse)
        rl.addLayout(row1)
        row_name = QtWidgets.QHBoxLayout()
        row_name.addWidget(QtWidgets.QLabel("Tên project"))
        self.ed_name = QtWidgets.QLineEdit("dialogue")
        row_name.addWidget(self.ed_name, 1)
        rl.addLayout(row_name)
        row2 = QtWidgets.QHBoxLayout()
        self.bt_start = QtWidgets.QPushButton("Bắt đầu")
        self.bt_start.setObjectName("primary")
        self.bt_stop = QtWidgets.QPushButton("Dừng")
        self.bt_stop.setObjectName("danger")
        self.bt_stop.setEnabled(False)
        self.bt_open = QtWidgets.QPushButton("Mở thư mục")
        self.bt_edit = QtWidgets.QPushButton("Edit MP3")
        self.lbl_result = QtWidgets.QLabel("Kết quả: 0/0")
        self.lbl_result.setObjectName("badge")
        row2.addWidget(self.bt_start)
        row2.addWidget(self.bt_stop)
        row2.addWidget(self.bt_open)
        row2.addWidget(self.bt_edit)
        row2.addStretch(1)
        row2.addWidget(self.lbl_result)
        rl.addLayout(row2)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        rl.addWidget(self.progress)
        left.addWidget(run_card, 0)

        # Cột trái bọc scroll (khi màn thấp / panel cast cao)
        left_host = QtWidgets.QWidget()
        left_host.setLayout(left)
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidget(left_host)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(360)
        left_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { width: 10px; background: transparent; }"
            "QScrollBar::handle:vertical {"
            "  background: #d4d4d4; border-radius: 4px; min-height: 28px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height: 0; }"
        )

        # ── PHẢI: hàng đợi tệp (giảm ~50% chiều cao) ──
        queue_card, ql = card("Hàng đợi tệp")
        self.tbl_files = QtWidgets.QTableWidget(0, 4)
        self.tbl_files.setHorizontalHeaderLabels(
            ["STT", "Tên tệp", "Lượt", "Trạng thái"]
        )
        self.tbl_files.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        fhh = self.tbl_files.horizontalHeader()
        fhh.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        fhh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        fhh.setSectionResizeMode(2, QtWidgets.QHeaderView.Fixed)
        fhh.setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)
        self.tbl_files.setColumnWidth(0, 48)
        self.tbl_files.setColumnWidth(2, 56)
        self.tbl_files.setColumnWidth(3, 110)
        self.tbl_files.verticalHeader().setVisible(False)
        self.tbl_files.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # ~50% so với trước (min 100): gọn, còn thấy header + 1–2 dòng
        self.tbl_files.setMinimumHeight(52)
        self.tbl_files.setMaximumHeight(68)
        self.tbl_files.setToolTip("Double-click để mở file TXT nguồn")
        ql.addWidget(self.tbl_files)
        right.addWidget(queue_card, 0)

        # ── PHẢI: danh sách lượt (status) — chính ──
        turns_card, tl = card("Danh sách lượt cần tạo")
        self.tbl_turns = QtWidgets.QTableWidget(0, 5)
        self.tbl_turns.setHorizontalHeaderLabels(
            ["#", "Nhân vật", "Giọng", "Nội dung", "Trạng thái"]
        )
        self.tbl_turns.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_turns.setAlternatingRowColors(True)
        hh = self.tbl_turns.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.Fixed)
        self.tbl_turns.setColumnWidth(0, 40)
        self.tbl_turns.setColumnWidth(4, 100)
        self.tbl_turns.verticalHeader().setVisible(False)
        self.tbl_turns.setMinimumHeight(220)
        self.tbl_turns.setToolTip("Double-click để mở MP3 lượt (sau khi gen xong)")
        tl.addWidget(self.tbl_turns, 1)
        right.addWidget(turns_card, 1)

        content.addWidget(left_scroll, 2)
        content.addLayout(right, 3)
        root.addLayout(content, 1)

        self.lbl_status = QtWidgets.QLabel("Sẵn sàng.")
        self.lbl_status.setObjectName("muted")
        root.addWidget(self.lbl_status)

        self._files: List[dict] = []  # {path, name, text}
        self._active_file_idx = -1
        self._script_timer = QtCore.QTimer(self)
        self._script_timer.setSingleShot(True)
        self._script_timer.setInterval(280)

        # signals
        self.bt_cast_add.clicked.connect(lambda: self._add_cast_row("", ""))
        self.bt_cast_del.clicked.connect(self._del_cast_row)
        self.bt_sample.clicked.connect(self._insert_sample)
        self.bt_clear_queue.clicked.connect(self._clear_queue)
        self.bt_txt.clicked.connect(self._pick_txt)
        self.bt_folder.clicked.connect(self._pick_folder)
        self.bt_browse.clicked.connect(self._browse_out)
        self.bt_start.clicked.connect(self._start)
        self.bt_stop.clicked.connect(self._stop)
        self.bt_open.clicked.connect(self._open_out)
        self.bt_edit.clicked.connect(self._open_edit)
        self.ed_script.textChanged.connect(self._on_script_changed)
        self.tbl_files.itemSelectionChanged.connect(self._on_file_selected)
        self.tbl_files.cellDoubleClicked.connect(self._open_queue_file)
        self.tbl_turns.cellDoubleClicked.connect(self._open_turn_audio)
        self._script_timer.timeout.connect(lambda: self._preview_turns(silent=True))

    # ── State ───────────────────────────────────────────────────────────
    def _voices_list(self) -> List[dict]:
        voices = list((self._cfg or {}).get("voices") or [])
        if not voices:
            voices = [
                {
                    "name": "Giọng mặc định",
                    "voice_id": DEFAULT_VOICE,
                    "lang": "vi",
                }
            ]
        return voices

    def _voice_combo(self, selected_id: str = "") -> QtWidgets.QComboBox:
        cb = QtWidgets.QComboBox()
        voices = self._voices_list()
        sel = 0
        for i, v in enumerate(voices):
            vid = (v.get("voice_id") or "").strip()
            name = (v.get("name") or vid or f"Giọng {i+1}").strip()
            cb.addItem(f"{name}", vid)
            if vid and vid == selected_id:
                sel = i
        if selected_id and cb.findData(selected_id) < 0:
            cb.addItem(f"(khác) {selected_id[:12]}…", selected_id)
            sel = cb.count() - 1
        cb.setCurrentIndex(sel)
        cb.currentIndexChanged.connect(lambda *_: self._preview_turns(silent=True))
        return cb

    def _load_state(self):
        c = self._cfg or {}
        out = c.get("output_dir") or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "output"
        )
        self.ed_out.setText(out)
        mv = c.get("multivoice") or {}
        script = mv.get("script") or default_script()
        self.ed_script.setPlainText(script)
        self.ed_name.setText(mv.get("project_name") or "dialogue")
        try:
            self.sb_gap.setValue(float(mv.get("gap_seconds", 0.35)))
        except Exception:
            self.sb_gap.setValue(0.35)
        cast = mv.get("cast") or []
        self.tbl_cast.setRowCount(0)
        if cast:
            for row in cast:
                self._add_cast_row(row.get("name") or "", row.get("voice_id") or "")
        else:
            self._add_cast_row("Nam", DEFAULT_VOICE)
            # second voice if available
            voices = self._voices_list()
            v2 = voices[1]["voice_id"] if len(voices) > 1 else DEFAULT_VOICE
            self._add_cast_row("Nữ", v2 or DEFAULT_VOICE)
        self._refresh_account()
        self._preview_turns(silent=True)

    def _persist(self):
        c = self.load_config()
        c["output_dir"] = self.ed_out.text().strip() or c.get("output_dir") or ""
        cast = []
        for r in range(self.tbl_cast.rowCount()):
            name_item = self.tbl_cast.item(r, 0)
            name = (name_item.text() if name_item else "").strip()
            w = self.tbl_cast.cellWidget(r, 1)
            vid = ""
            if isinstance(w, QtWidgets.QComboBox):
                vid = (w.currentData() or "").strip()
            if name:
                cast.append({"name": name, "voice_id": vid})
        c["multivoice"] = {
            "script": self.ed_script.toPlainText(),
            "cast": cast,
            "project_name": self.ed_name.text().strip() or "dialogue",
            "gap_seconds": float(self.sb_gap.value()),
        }
        self.save_config(c)
        self._cfg = c

    def _refresh_account(self):
        try:
            pub = accounts.public_account(self.user)
            u = pub.get("username") or "?"
            left = int(pub.get("chars_left") or 0)
            unlimited = bool(pub.get("unlimited"))
            if unlimited:
                self.lbl_account.setText(f"{u} · Unlimited")
            else:
                self.lbl_account.setText(f"{u} · còn {left:,} ký tự")
        except Exception:
            self.lbl_account.setText("—")

    # ── Cast table ──────────────────────────────────────────────────────
    def _add_cast_row(self, name: str = "", voice_id: str = ""):
        r = self.tbl_cast.rowCount()
        self.tbl_cast.insertRow(r)
        self.tbl_cast.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
        self.tbl_cast.setCellWidget(r, 1, self._voice_combo(voice_id))

    def _del_cast_row(self):
        rows = sorted(
            {i.row() for i in self.tbl_cast.selectionModel().selectedRows()},
            reverse=True,
        )
        for r in rows:
            self.tbl_cast.removeRow(r)
        self._persist()

    def _voice_map_from_table(self) -> Dict[str, str]:
        m: Dict[str, str] = {}
        for r in range(self.tbl_cast.rowCount()):
            name_item = self.tbl_cast.item(r, 0)
            name = (name_item.text() if name_item else "").strip()
            w = self.tbl_cast.cellWidget(r, 1)
            vid = ""
            if isinstance(w, QtWidgets.QComboBox):
                vid = (w.currentData() or "").strip()
            if name and vid:
                m[name] = vid
        return m

    def _ensure_cast_for_names(self, names: List[str]):
        """Tự thêm dòng cast nếu script có tên mới (không cần nút quét)."""
        existing = {k.lower(): k for k in self._voice_map_from_table()}
        # also names currently in table even without voice
        for r in range(self.tbl_cast.rowCount()):
            it = self.tbl_cast.item(r, 0)
            if it and it.text().strip():
                existing[it.text().strip().lower()] = it.text().strip()
        voices = self._voices_list()
        vi = 0
        for name in names:
            if name.lower() in existing:
                continue
            vid = DEFAULT_VOICE
            if voices:
                vid = (voices[min(vi, len(voices) - 1)].get("voice_id") or DEFAULT_VOICE)
                vi += 1
            self._add_cast_row(name, vid)
            existing[name.lower()] = name

    # ── Load TXT / folder ───────────────────────────────────────────────
    def _read_txt(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="ignore")

    def _pick_txt(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Chọn tệp kịch bản TXT",
            "",
            "Text (*.txt);;Tất cả (*.*)",
        )
        if paths:
            self._load_paths(paths)

    def _pick_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa TXT")
        if not d:
            return
        paths = sorted(
            str(p)
            for p in Path(d).rglob("*.txt")
            if p.is_file() and not p.name.startswith(".")
        )
        if not paths:
            QtWidgets.QMessageBox.information(
                self, "Trống", "Thư mục không có file .txt"
            )
            return
        self._load_paths(paths)

    def _load_paths(self, paths: List[str]):
        """Import TXT → hàng đợi. KHÔNG đổ full text vào ô mẫu."""
        files: List[dict] = []
        for p in paths:
            try:
                text = self._read_txt(p)
            except Exception as e:
                self._set_status(f"Lỗi đọc {os.path.basename(p)}: {e}")
                continue
            files.append(
                {
                    "path": p,
                    "name": os.path.basename(p),
                    "text": text,  # stored in memory only
                }
            )
        if not files:
            return
        self._files = files
        self._active_file_idx = 0
        self._rebuild_file_table()
        # Editor: keep sample UI — do NOT dump file content
        self.ed_script.blockSignals(True)
        self.ed_script.clear()
        self.ed_script.setPlaceholderText(
            default_script()
            + "\n# (Đang dùng hàng đợi TXT — nội dung lấy từ file, không hiện full ở đây)"
        )
        self.ed_script.blockSignals(False)
        self.ed_input_path.setText(
            files[0]["path"]
            if len(files) == 1
            else f"{len(files)} tệp TXT"
        )
        if len(files) == 1:
            self.ed_name.setText(Path(files[0]["name"]).stem or "dialogue")
        self.tbl_files.blockSignals(True)
        self.tbl_files.selectRow(0)
        self.tbl_files.blockSignals(False)
        self._preview_turns()
        self._set_status(
            f"Đã tải {len(files)} tệp TXT → hàng đợi (ô trên chỉ để mẫu/gõ tay)"
        )

    def _clear_queue(self):
        self._files = []
        self._active_file_idx = -1
        self.tbl_files.setRowCount(0)
        self.ed_input_path.clear()
        self.ed_script.setPlaceholderText(default_script())
        self._preview_turns(silent=True)
        self._set_status("Đã xóa hàng đợi — dùng ô mẫu / gõ tay.")

    def _rebuild_file_table(self):
        vmap = self._voice_map_from_table()
        self.tbl_files.setRowCount(0)
        for i, f in enumerate(self._files):
            pr = parse_script(f.get("text") or "", vmap)
            self.tbl_files.insertRow(i)
            self.tbl_files.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_files.setItem(i, 1, QtWidgets.QTableWidgetItem(f.get("name") or ""))
            self.tbl_files.setItem(i, 2, QtWidgets.QTableWidgetItem(str(len(pr.turns))))
            self.tbl_files.setItem(i, 3, QtWidgets.QTableWidgetItem("Chờ"))

    def _on_file_selected(self):
        """Chọn file trong hàng đợi — chỉ cập nhật bảng lượt, không đổ full text."""
        rows = self.tbl_files.selectionModel().selectedRows()
        if not rows or not self._files:
            return
        idx = rows[0].row()
        if idx < 0 or idx >= len(self._files):
            return
        self._active_file_idx = idx
        self.ed_name.setText(Path(self._files[idx].get("name") or "dialogue").stem)
        # show turns of selected file in status table
        self._fill_turns_table_from_text(
            self._files[idx].get("text") or "",
            silent=True,
        )
        n = self.tbl_turns.rowCount()
        self._set_status(
            f"File: {self._files[idx].get('name')} · {n} lượt (nội dung từ file)"
        )

    # ── Script ──────────────────────────────────────────────────────────
    def _insert_sample(self):
        """Chế độ gõ tay / mẫu — xóa hàng đợi, hiện mẫu trong editor."""
        self._files = []
        self.tbl_files.setRowCount(0)
        self._active_file_idx = -1
        self.ed_input_path.setText("(mẫu trong editor)")
        self.ed_script.setPlainText(default_script())
        self.ed_name.setText("dialogue_mau")
        self._preview_turns()

    def _on_script_changed(self):
        # Debounce refresh bảng lượt (không cần nút «Xem lượt»)
        if self._files:
            n_files = len(self._files)
            total = 0
            vmap = self._voice_map_from_table()
            for f in self._files:
                total += len(parse_script(f.get("text") or "", vmap).turns)
            self.lbl_preview.setText(f"{total} lượt · {n_files} tệp")
            return
        self._script_timer.start()

    def _fill_turns_table_from_text(self, text: str, silent: bool = False):
        """Parse text → bảng lượt (status)."""
        names = speakers_in_script(text)
        self._ensure_cast_for_names(names)
        vmap = self._voice_map_from_table()
        pr = parse_script(text, vmap)
        turns = [
            {
                "speaker": t.speaker,
                "text": t.text,
                "voice_id": t.voice_id,
                "line_no": t.line_no,
            }
            for t in pr.turns
        ]
        self._turns = turns
        self.tbl_turns.setRowCount(0)
        for i, t in enumerate(turns):
            self.tbl_turns.insertRow(i)
            self.tbl_turns.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_turns.setItem(i, 1, QtWidgets.QTableWidgetItem(t["speaker"]))
            label = (t["voice_id"] or "")[:14]
            if t["voice_id"] and len(t["voice_id"]) > 14:
                label += "…"
            for v in self._voices_list():
                if (v.get("voice_id") or "") == t["voice_id"]:
                    label = v.get("name") or label
                    break
            self.tbl_turns.setItem(
                i,
                2,
                QtWidgets.QTableWidgetItem(label if t["voice_id"] else "⚠ chưa gán"),
            )
            prev = (t["text"] or "")[:90]
            if len(t["text"] or "") > 90:
                prev += "…"
            self.tbl_turns.setItem(i, 3, QtWidgets.QTableWidgetItem(prev))
            st = "Chờ" if t["voice_id"] else "Thiếu giọng"
            item = QtWidgets.QTableWidgetItem(st)
            if not t["voice_id"]:
                item.setForeground(QtGui.QColor("#991b1b"))
            self.tbl_turns.setItem(i, 4, item)
        return pr

    def _preview_turns(self, silent: bool = False):
        """
        - Có hàng đợi: parse từ file (selected hoặc gộp tất cả khi gen)
        - Không queue: parse từ editor (mẫu / gõ tay)
        """
        vmap = self._voice_map_from_table()

        if self._files:
            # cast from all files
            all_names: List[str] = []
            seen = set()
            for f in self._files:
                for name in speakers_in_script(f.get("text") or ""):
                    if name not in seen:
                        seen.add(name)
                        all_names.append(name)
            self._ensure_cast_for_names(all_names)
            vmap = self._voice_map_from_table()

            # table 4 = selected file turns (or first)
            idx = self._active_file_idx if 0 <= self._active_file_idx < len(self._files) else 0
            self._active_file_idx = idx
            text = self._files[idx].get("text") or ""
            pr = self._fill_turns_table_from_text(text, silent=silent)
            total = sum(
                len(parse_script(f.get("text") or "", vmap).turns) for f in self._files
            )
            self.lbl_preview.setText(f"{total} lượt · {len(self._files)} tệp")
            self._rebuild_file_table_status_only(vmap)
            if not silent:
                if pr.errors:
                    self._set_status(
                        f"{self._files[idx].get('name')}: " + " · ".join(pr.errors[:2])
                    )
                else:
                    self._set_status(
                        f"Hàng đợi {len(self._files)} tệp · "
                        f"đang xem «{self._files[idx].get('name')}» "
                        f"({len(self._turns)} lượt) · gen sẽ chạy cả hàng đợi"
                    )
        else:
            text = self.ed_script.toPlainText()
            self._ensure_cast_for_names(speakers_in_script(text))
            pr = self._fill_turns_table_from_text(text, silent=silent)
            self.lbl_preview.setText(f"{len(self._turns)} lượt · 0 tệp")
            if not silent:
                if pr.errors:
                    self._set_status(" · ".join(pr.errors[:3]))
                else:
                    self._set_status(f"Sẵn sàng · mẫu/editor {len(self._turns)} lượt")

        self._persist()
        return pr

    def _rebuild_file_table_status_only(self, vmap: Dict[str, str]):
        """Refresh turn counts without wiping status mid-run."""
        for i, f in enumerate(self._files):
            if i >= self.tbl_files.rowCount():
                break
            pr = parse_script(f.get("text") or "", vmap)
            self.tbl_files.setItem(i, 2, QtWidgets.QTableWidgetItem(str(len(pr.turns))))

    # ── Run ─────────────────────────────────────────────────────────────
    def _browse_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Thư mục xuất")
        if d:
            self.ed_out.setText(d)
            self._persist()

    def _set_status(self, t: str):
        self.lbl_status.setText(t or "")

    def _build_jobs(self) -> tuple[List[dict], List[str]]:
        """Build gen jobs from file queue (preferred) or editor sample."""
        self._preview_turns(silent=True)
        vmap = self._voice_map_from_table()
        errors: List[str] = []
        jobs: List[dict] = []

        if self._files:
            sources = list(self._files)
        else:
            sources = [
                {
                    "path": "",
                    "name": (self.ed_name.text().strip() or "dialogue") + ".txt",
                    "text": self.ed_script.toPlainText(),
                }
            ]

        flat_turns: List[dict] = []
        for src in sources:
            name = src.get("name") or "dialogue.txt"
            text = src.get("text") or ""
            pr = parse_script(text, vmap)
            if pr.errors:
                errors.extend([f"{name}: {e}" for e in pr.errors])
                continue
            if not pr.turns:
                errors.append(f"{name}: không có lượt thoại")
                continue
            proj = Path(name).stem or "dialogue"
            out_root = os.path.join(self.ed_out.text().strip() or ".", safe_stem(proj))
            turns = []
            for ti, t in enumerate(pr.turns, 1):
                safe_sp = re.sub(r"[^\w\-]+", "_", t.speaker, flags=re.U)[:24]
                turns.append(
                    {
                        "speaker": t.speaker,
                        "text": t.text,
                        "voice_id": t.voice_id,
                        "line_no": t.line_no,
                        "source_path": src.get("path") or "",
                        "file_dir": out_root,
                        "out_path": os.path.join(
                            out_root, f"turn_{ti:02d}_{safe_sp}.mp3"
                        ),
                        "merged_path": os.path.join(
                            out_root, f"{safe_stem(proj)}.mp3"
                        ),
                    }
                )
            jobs.append(
                {
                    "project_name": proj,
                    "turns": turns,
                    "source": src.get("path") or name,
                }
            )
            flat_turns.extend(turns)

        self._turns = flat_turns  # full status table when gen starts
        return jobs, errors

    def _start(self):
        full = accounts.get_account(self.user.get("id") or "")
        if full:
            self.user = full
            self._refresh_account()

        jobs, errors = self._build_jobs()
        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Script chưa sẵn sàng", "\n".join(errors[:10])
            )
            return
        if not jobs:
            QtWidgets.QMessageBox.warning(
                self,
                "Trống",
                "Chưa có lượt thoại.\nMở TXT / thư mục TXT hoặc viết kịch bản.",
            )
            return

        proxy = accounts.build_proxy_url(self.user)
        # max_workers admin = số TTS đồng thời (1 proxy vẫn chạy đủ N luồng)
        mw = min(5, max(1, int(self.user.get("max_workers") or 5)))
        proxy_lines = accounts.list_proxy_lines_for_gen(self.user, max_lanes=5)
        if not proxy and not proxy_lines:
            QtWidgets.QMessageBox.warning(
                self,
                "Thiếu proxy",
                "Tài khoản chưa được gắn proxy.\n"
                "Admin cấp proxy tại https://tts-origin.liveyt.pro/admin/",
            )
            return
        if not proxy_lines:
            QtWidgets.QMessageBox.warning(
                self,
                "Thiếu proxy",
                "Không có proxy enabled trong pool.\n"
                "Admin thêm proxy tại https://tts-origin.liveyt.pro/admin/",
            )
            return

        n_workers = mw  # 1 proxy + max_workers=3 → 3 luồng TTS
        hsw = min(8, max(3, n_workers * 2))

        total_chars = sum(
            len(t.get("text") or "")
            for j in jobs
            for t in (j.get("turns") or [])
        )
        ok_q, msg_q = accounts.check_chars(self.user, total_chars)
        if not ok_q:
            QtWidgets.QMessageBox.warning(self, "Hết gói ký tự", msg_q)
            return

        out = self.ed_out.text().strip()
        if not out:
            QtWidgets.QMessageBox.warning(self, "Thiếu thư mục", "Chọn thư mục xuất.")
            return

        self._persist()
        # refresh turn table for all flat turns
        self.tbl_turns.setRowCount(0)
        for i, t in enumerate(self._turns):
            self.tbl_turns.insertRow(i)
            self.tbl_turns.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.tbl_turns.setItem(i, 1, QtWidgets.QTableWidgetItem(t.get("speaker") or ""))
            self.tbl_turns.setItem(i, 2, QtWidgets.QTableWidgetItem((t.get("voice_id") or "")[:12]))
            prev = (t.get("text") or "")[:80]
            self.tbl_turns.setItem(i, 3, QtWidgets.QTableWidgetItem(prev))
            self.tbl_turns.setItem(i, 4, QtWidgets.QTableWidgetItem("Chờ"))
        for i in range(self.tbl_files.rowCount()):
            self.tbl_files.setItem(i, 3, QtWidgets.QTableWidgetItem("Chờ"))

        self.bt_start.setEnabled(False)
        self.bt_stop.setEnabled(True)
        self.progress.setValue(0)
        n_turns = len(self._turns)
        self.lbl_result.setText(f"0/{n_turns}")

        px_key = self.user.get("proxy_api_key") or ""
        if not px_key and proxy_lines:
            px_key = proxy_lines[0].get("api_key") or ""

        self._worker = MultivoiceWorker(
            jobs=jobs,
            output_dir=out,
            proxy=proxy or "",
            proxy_api_key=str(px_key or ""),
            proxy_lines=proxy_lines,
            lang="vi",
            speed=float((self._cfg or {}).get("speed") or 1.0),
            gap_seconds=float(self.sb_gap.value()),
            workers=n_workers,
            hsw_workers=hsw,
            max_attempts=40,
        )
        self._worker.log.connect(self._set_status)
        self._worker.turn_status.connect(self._on_turn_status)
        self._worker.file_status.connect(self._on_file_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()
        labels = ", ".join(
            (p.get("label") or p.get("id") or "?") for p in proxy_lines[:3]
        )
        self._set_status(
            f"{n_workers} luồng TTS · {len(proxy_lines)} proxy · "
            f"{len(jobs)} tệp · {n_turns} lượt · proxy: {labels}"
        )

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self._set_status("Đang dừng…")

    def _on_turn_status(self, row: int, status: str, path: str = ""):
        if 0 <= row < len(self._turns) and path:
            self._turns[row]["out_path"] = path
            if not self._turns[row].get("file_dir") and path:
                self._turns[row]["file_dir"] = os.path.dirname(path)
        if 0 <= row < self.tbl_turns.rowCount():
            item = QtWidgets.QTableWidgetItem(status)
            st = status.lower()
            if "xong" in st:
                item.setForeground(QtGui.QColor("#166534"))
            elif "lỗi" in st:
                item.setForeground(QtGui.QColor("#991b1b"))
            elif "đang" in st:
                item.setForeground(QtGui.QColor("#b45309"))
            if path:
                item.setToolTip(path)
            self.tbl_turns.setItem(row, 4, item)

    def _on_file_status(self, row: int, status: str):
        if 0 <= row < self.tbl_files.rowCount():
            item = QtWidgets.QTableWidgetItem(status)
            st = status.lower()
            if "xong" in st:
                item.setForeground(QtGui.QColor("#166534"))
            elif "lỗi" in st or "dừng" in st:
                item.setForeground(QtGui.QColor("#991b1b"))
            elif "chạy" in st:
                item.setForeground(QtGui.QColor("#b45309"))
            self.tbl_files.setItem(row, 3, item)

    def _on_progress(self, cur: int, total: int):
        self.progress.setValue(int(100 * cur / max(1, total)))
        self.lbl_result.setText(f"{cur}/{total}")

    def _on_finished(self, ok: int, fail: int, merged: str):
        self.bt_start.setEnabled(True)
        self.bt_stop.setEnabled(False)
        self.progress.setValue(100 if ok else self.progress.value())
        self.lbl_result.setText(f"Xong: {ok} OK / {fail} lỗi")
        try:
            n = sum(len(t.get("text") or "") for t in self._turns)
            # charge successful-ish total script length proportionally
            if ok > 0 and n > 0:
                charge = int(n * ok / max(1, ok + fail))
                accounts.consume_chars(self.user.get("id") or "", max(0, charge))
                full = accounts.get_account(self.user.get("id") or "")
                if full:
                    self.user = full
                    self._refresh_account()
        except Exception:
            pass
        if merged and os.path.isfile(merged):
            self._last_merged = merged
            self._set_status(f"Hoàn tất · file: {merged}")
            QtWidgets.QMessageBox.information(
                self,
                "Xong",
                f"Tạo xong hội thoại.\n\nLượt OK: {ok}\nLượt lỗi: {fail}\n\n"
                f"File (gần nhất):\n{merged}",
            )
        else:
            self._set_status(f"Xong (ok={ok}, fail={fail})")

    def _on_failed(self, err: str):
        self.bt_start.setEnabled(True)
        self.bt_stop.setEnabled(False)
        self._set_status(f"Lỗi: {err[:200]}")
        QtWidgets.QMessageBox.critical(self, "Lỗi", err[:800])

    def _open_out(self):
        d = self.ed_out.text().strip()
        if d and os.path.isdir(d):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))
        else:
            self._set_status("Thư mục xuất không tồn tại.")

    def _open_path(self, path: str) -> bool:
        """Mở file/folder bằng app hệ thống (Finder / player)."""
        p = (path or "").strip()
        if not p:
            return False
        if os.path.isfile(p) or os.path.isdir(p):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(p))
            return True
        return False

    def _open_queue_file(self, row: int, _col: int = 0):
        """Double-click hàng đợi → mở TXT nguồn (giống Tạo audio)."""
        if row < 0 or row >= len(self._files):
            return
        f = self._files[row]
        src = f.get("path") or ""
        if self._open_path(src):
            self._set_status(f"Mở: {f.get('name') or src}")
            return
        # fallback: folder xuất project
        out = self.ed_out.text().strip()
        stem = Path(f.get("name") or "dialogue").stem
        proj = os.path.join(out, safe_stem(stem)) if out else ""
        if self._open_path(proj):
            self._set_status(f"Mở thư mục xuất: {stem}")
        else:
            self._set_status("Chưa có file để mở.")

    def _open_turn_audio(self, row: int, _col: int = 0):
        """Double-click lượt → mở MP3 đã gen; chưa có thì mở folder / TXT nguồn."""
        if 0 <= row < len(self._turns):
            t = self._turns[row]
            p = t.get("out_path") or t.get("path") or ""
            if p and os.path.isfile(p):
                self._open_path(p)
                self._set_status(f"Mở audio: {os.path.basename(p)}")
                return
            # merged full dialogue if turn file missing
            m = t.get("merged_path") or ""
            if m and os.path.isfile(m):
                self._open_path(m)
                self._set_status(f"Mở file gộp: {os.path.basename(m)}")
                return
            d = t.get("file_dir") or ""
            if d and os.path.isdir(d):
                self._open_path(d)
                self._set_status("Chưa có MP3 — mở thư mục xuất lượt.")
                return
            src = t.get("source_path") or ""
            if self._open_path(src):
                self._set_status("Mở kịch bản TXT nguồn.")
                return
        if self._files and 0 <= self._active_file_idx < len(self._files):
            src = self._files[self._active_file_idx].get("path") or ""
            if self._open_path(src):
                self._set_status("Mở kịch bản TXT nguồn.")
                return
        self._set_status("Chưa có audio — hãy Bắt đầu tạo trước.")

    def _open_edit(self):
        paths = []
        if getattr(self, "_last_merged", None) and os.path.isfile(self._last_merged):
            paths = [self._last_merged]
        if hasattr(self.main_window, "show_edit_mp3_tab"):
            self.main_window.show_edit_mp3_tab(paths or None)

    def cleanup(self):
        try:
            if self._worker and self._worker.isRunning():
                self._worker.stop()
                self._worker.wait(2000)
        except Exception:
            pass
        try:
            self._persist()
        except Exception:
            pass

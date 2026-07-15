# -*- coding: utf-8 -*-
"""
FFmpeg helpers — COPY MODE only (-c copy / -c:a copy).
Không re-encode (nhanh, lossless stream).
Cắt MP3 theo keyframe (có thể lệch nhẹ so với timestamp).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def find_ffmpeg() -> Optional[str]:
    try:
        from app_paths import find_portable_ffmpeg

        p = find_portable_ffmpeg()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def find_ffprobe() -> Optional[str]:
    try:
        from app_paths import find_portable_ffprobe

        p = find_portable_ffprobe()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe")


def _run(cmd: List[str], timeout: float = 600) -> Tuple[int, str, str]:
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, r.stdout or "", r.stderr or ""


def probe_duration(path: str) -> float:
    """Seconds (float). 0 if fail."""
    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.isfile(path):
        return 0.0
    code, out, err = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        timeout=30,
    )
    if code != 0:
        return 0.0
    try:
        return max(0.0, float((out or "").strip()))
    except ValueError:
        return 0.0


def probe_info(path: str) -> dict:
    """Basic info for UI."""
    d = probe_duration(path)
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    return {
        "path": path,
        "name": os.path.basename(path),
        "duration": d,
        "duration_str": format_ts(d),
        "size": size,
        "size_kb": size // 1024,
    }


def format_ts(seconds: float) -> str:
    """H:MM:SS.mmm or M:SS.mmm"""
    if seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"


def parse_ts(text: str) -> float:
    """
    Parse time: SS, M:SS, H:MM:SS, with optional .mmm
    """
    t = (text or "").strip().replace(",", ".")
    if not t:
        return 0.0
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t)
    parts = t.split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise ValueError(f"thời gian không hợp lệ: {text!r}")


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def concat_copy(paths: Sequence[str], out_path: str) -> Tuple[bool, str]:
    """Nối nhiều file theo thứ tự — stream copy."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "không tìm thấy ffmpeg trong PATH"
    paths = [p for p in paths if p and os.path.isfile(p)]
    if not paths:
        return False, "không có file đầu vào"
    if len(paths) == 1:
        _ensure_parent(out_path)
        shutil.copy2(paths[0], out_path)
        return True, f"copy 1 file → {os.path.basename(out_path)}"

    _ensure_parent(out_path)
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="ff_concat_")
    os.close(fd)
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in paths:
                ep = os.path.abspath(p).replace("'", r"'\''")
                f.write(f"file '{ep}'\n")
        code, _, err = _run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c",
                "copy",
                out_path,
            ]
        )
        if code != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
            return False, f"concat fail: {(err or '')[-300:]}"
        return True, f"nối {len(paths)} file (copy) → {os.path.basename(out_path)}"
    finally:
        try:
            os.remove(list_path)
        except Exception:
            pass


def cut_copy(
    path: str,
    out_path: str,
    start: float = 0.0,
    end: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Cắt đoạn [start, end) — copy mode.
    -ss sau -i: chính xác hơn nhưng chậm hơn; copy mode vẫn keyframe-aligned.
    Dùng -ss before -i for speed with copy (seek).
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "không tìm thấy ffmpeg"
    if not os.path.isfile(path):
        return False, "file không tồn tại"
    if start < 0:
        start = 0.0
    _ensure_parent(out_path)
    cmd = [ffmpeg, "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if end is not None and end > start:
        # duration after seek
        cmd += ["-t", f"{(end - start):.3f}"]
    cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero", out_path]
    code, _, err = _run(cmd)
    if code != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 100:
        return False, f"cắt fail: {(err or '')[-300:]}"
    return True, f"cắt {format_ts(start)}→{format_ts(end or 0)} (copy) → {os.path.basename(out_path)}"


def remove_segment_copy(
    path: str,
    out_path: str,
    cut_start: float,
    cut_end: float,
) -> Tuple[bool, str]:
    """Xóa đoạn [cut_start, cut_end] bằng cách nối phần trước + sau (copy)."""
    if cut_end <= cut_start:
        return False, "cut_end phải > cut_start"
    dur = probe_duration(path)
    if dur <= 0:
        return False, "không đọc được duration"
    parts: List[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="ff_rmseg_")
    try:
        if cut_start > 0.05:
            a = os.path.join(tmp_dir, "a.mp3")
            ok, msg = cut_copy(path, a, 0.0, cut_start)
            if not ok:
                return False, msg
            parts.append(a)
        if cut_end < dur - 0.05:
            b = os.path.join(tmp_dir, "b.mp3")
            ok, msg = cut_copy(path, b, cut_end, None)
            if not ok:
                return False, msg
            parts.append(b)
        if not parts:
            return False, "sau khi cắt không còn audio"
        return concat_copy(parts, out_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def split_equal_copy(
    path: str,
    out_dir: str,
    n_parts: int,
    prefix: str = "part",
) -> Tuple[bool, str, List[str]]:
    """Chia đều n phần (copy). Trả list file tạo ra."""
    n_parts = max(2, min(50, int(n_parts)))
    dur = probe_duration(path)
    if dur <= 0:
        return False, "không đọc được duration", []
    os.makedirs(out_dir, exist_ok=True)
    step = dur / n_parts
    outs: List[str] = []
    for i in range(n_parts):
        start = i * step
        end = dur if i == n_parts - 1 else (i + 1) * step
        out = os.path.join(out_dir, f"{prefix}_{i+1:02d}.mp3")
        ok, msg = cut_copy(path, out, start, end)
        if not ok:
            return False, msg, outs
        outs.append(out)
    return True, f"chia {n_parts} phần (copy) → {out_dir}", outs


def split_at_timestamps_copy(
    path: str,
    out_dir: str,
    timestamps: Sequence[float],
    prefix: str = "seg",
) -> Tuple[bool, str, List[str]]:
    """
    Chia tại các mốc thời gian (giây).
    timestamps = điểm cắt giữa (không gồm 0 và end).
    """
    dur = probe_duration(path)
    if dur <= 0:
        return False, "không đọc được duration", []
    cuts = sorted({0.0, float(dur), *[float(t) for t in timestamps if 0 < t < dur]})
    os.makedirs(out_dir, exist_ok=True)
    outs: List[str] = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if b - a < 0.05:
            continue
        out = os.path.join(out_dir, f"{prefix}_{i+1:02d}.mp3")
        ok, msg = cut_copy(path, out, a, b)
        if not ok:
            return False, msg, outs
        outs.append(out)
    return True, f"chia {len(outs)} đoạn tại mốc (copy)", outs

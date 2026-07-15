# -*- coding: utf-8 -*-
"""
Output layout giống appTTs-clean-v327:

  {output_root}/
    {stem}/
      doan_1.mp3
      doan_2.mp3
      {stem}.mp3          ← merge khi đủ đoạn

  Nhiều TXT folder → mỗi file 1 subfolder.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional


def safe_stem(name: str) -> str:
    """Tên folder an toàn từ basename file."""
    s = Path(name or "doan").stem
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s).strip(" .")
    return s or "doan"


def smart_split_text(text: str, max_chars: int = 300) -> List[str]:
    """
    Chia đoạn thông minh (gần v327):
    1) Cắt theo . ! ? 。！？ …
    2) Gói câu vào chunk ≤ max_chars
    3) Câu quá dài → cắt theo khoảng trắng
    """
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []
    max_chars = max(40, int(max_chars or 300))
    if len(text) <= max_chars:
        return [text]

    # Tách câu (giữ dấu câu cuối)
    parts = re.split(r"(?<=[.!?…。！？])\s+", text)
    sentences = [p.strip() for p in parts if p and p.strip()]
    if not sentences:
        return _split_by_words(text, max_chars)

    chunks: List[str] = []
    cur = ""

    def flush():
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for sent in sentences:
        if len(sent) > max_chars:
            flush()
            chunks.extend(_split_by_words(sent, max_chars))
            continue
        trial = (cur + " " + sent).strip() if cur else sent
        if len(trial) > max_chars and cur:
            flush()
            cur = sent
        else:
            cur = trial
    flush()
    return chunks if chunks else [text]


def _split_by_words(text: str, max_chars: int) -> List[str]:
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


def file_output_dir(output_root: str, file_name: str) -> str:
    """{output_root}/{stem}/"""
    stem = safe_stem(file_name)
    d = os.path.join(output_root, stem)
    os.makedirs(d, exist_ok=True)
    return d


def doan_path(output_root: str, file_name: str, part: int) -> str:
    """…/{stem}/doan_{part}.mp3"""
    d = file_output_dir(output_root, file_name)
    return os.path.join(d, f"doan_{int(part)}.mp3")


def merged_path(output_root: str, file_name: str) -> str:
    """…/{stem}/{stem}.mp3"""
    stem = safe_stem(file_name)
    d = file_output_dir(output_root, file_name)
    return os.path.join(d, f"{stem}.mp3")


def list_doan_files(file_dir: str) -> List[str]:
    """doan_*.mp3 sort theo số."""
    p = Path(file_dir)
    if not p.is_dir():
        return []
    files = []
    for f in p.glob("doan_*.mp3"):
        m = re.match(r"doan_(\d+)\.mp3$", f.name, re.I)
        if m and f.stat().st_size > 500:
            files.append((int(m.group(1)), str(f)))
    files.sort(key=lambda x: x[0])
    return [path for _, path in files]


def find_ffmpeg() -> Optional[str]:
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def insert_punctuation_silence(
    mp3_path: str,
    text: str,
    silent_path: str,
) -> bool:
    """
    Split MP3 at punctuation positions ("," and ".") using ratio-based timestamps,
    then concatenate parts with silence between them.
    
    Example: "con chó, đang cười. Con mèo, đang khóc"
    → split at timestamps of "," and "."
    → concat: [con chó] + 0.5s + [đang cười] + 0.5s + [Con mèo] + 0.5s + [đang khóc]
    """
    if not silent_path or not os.path.isfile(silent_path):
        return False
    if not mp3_path or not os.path.isfile(mp3_path):
        return False
    
    # Find punctuation positions in text
    punct_positions = []  # list of (char_index, punct_char)
    for i, ch in enumerate(text):
        if ch in ",.":
            punct_positions.append((i, ch))
    
    if not punct_positions:
        return False  # No punctuation, nothing to do
    
    # Get MP3 duration using ffprobe
    ffprobe = find_ffprobe()
    if not ffprobe:
        return False
    
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", mp3_path],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            return False
        duration = float(r.stdout.strip())
        if duration <= 0:
            return False
    except Exception:
        return False
    
    # Calculate timestamps based on character ratios
    total_chars = len(text)
    timestamps = []  # list of (start, end) for each segment
    prev_pos = 0
    
    for pos, punct in punct_positions:
        # Segment from prev_pos to pos+1 (including punctuation)
        seg_start_ratio = prev_pos / total_chars
        seg_end_ratio = (pos + 1) / total_chars
        timestamps.append((seg_start_ratio * duration, seg_end_ratio * duration))
        prev_pos = pos + 1
    
    # Last segment (if any text after last punctuation)
    if prev_pos < total_chars:
        seg_start_ratio = prev_pos / total_chars
        timestamps.append((seg_start_ratio * duration, duration))
    
    if not timestamps:
        return False
    
    # If only 1 segment, no need to split
    if len(timestamps) == 1:
        return False
    
    # Split MP3 at timestamps using ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    
    tmp_dir = tempfile.mkdtemp(prefix="tts_punct_")
    part_paths = []
    
    try:
        for i, (start, end) in enumerate(timestamps):
            part_path = os.path.join(tmp_dir, f"part_{i:03d}.mp3")
            cmd = [
                ffmpeg, "-y",
                "-ss", f"{start:.3f}",
                "-to", f"{end:.3f}",
                "-i", mp3_path,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                part_path
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.isfile(part_path) and os.path.getsize(part_path) > 500:
                part_paths.append(part_path)
        
        if len(part_paths) < 2:
            return False  # Need at least 2 parts to insert silence
        
        # Concat parts with silence between them
        ok, msg = concat_with_silence(part_paths, mp3_path, silent_path)
        return ok
    
    except Exception:
        return False
    finally:
        # Cleanup temp files
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def concat_with_silence(
    paths: list[str],
    out_mp3: str,
    silent_path: str,
) -> tuple[bool, str]:
    """
    Concatenate multiple MP3 files with silence between them.
    paths: list of MP3 file paths
    out_mp3: output MP3 path
    silent_path: path to silent MP3 to insert between files
    Returns (ok, message)
    """
    if not paths:
        return False, "no input files"
    
    if not silent_path or not os.path.isfile(silent_path):
        # No silent file, just concat without silence
        return _binary_concat_list(paths, out_mp3)
    
    # Build concat list with silence between each file
    concat_list = []
    for i, p in enumerate(paths):
        concat_list.append(p)
        if i < len(paths) - 1:  # Add silence between files (not after last)
            concat_list.append(silent_path)
    
    return _binary_concat_list(concat_list, out_mp3)


def _binary_concat_list(
    paths: list[str],
    out_mp3: str,
) -> tuple[bool, str]:
    """
    Concatenate list of MP3 files using ffmpeg concat demuxer or binary fallback.
    """
    ffmpeg = find_ffmpeg()
    
    if ffmpeg:
        list_path = ""
        try:
            fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_silence_")
            os.close(fd)
            with open(list_path, "w", encoding="utf-8") as f:
                for p in paths:
                    ep = os.path.abspath(p).replace("'", "'\\''")
                    f.write(f"file '{ep}'\n")
            
            cmd = [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", list_path, "-c", "copy", out_mp3
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and os.path.isfile(out_mp3) and os.path.getsize(out_mp3) > 500:
                return True, f"concat {len(paths)} files"
        except Exception as e:
            pass
        finally:
            if list_path:
                try:
                    os.remove(list_path)
                except Exception:
                    pass
    
    # Binary fallback
    try:
        with open(out_mp3, "wb") as out:
            for p in paths:
                if os.path.isfile(p):
                    with open(p, "rb") as inp:
                        out.write(inp.read())
        if os.path.isfile(out_mp3) and os.path.getsize(out_mp3) > 500:
            return True, f"concat {len(paths)} files (binary)"
        return False, "output too small"
    except Exception as e:
        return False, str(e)


def merge_doan_mp3s(
    file_dir: str,
    out_mp3: str,
    *,
    expected_parts: int = 0,
    silent_between: str = "",
) -> tuple[bool, str]:
    """
    Ghép doan_1..N → out_mp3.
    silent_between: path to silent MP3 to insert between parts (e.g. silent_1s.mp3)
    Returns (ok, message).
    """
    parts = list_doan_files(file_dir)
    if not parts:
        return False, "không có doan_*.mp3"
    if expected_parts > 0 and len(parts) < expected_parts:
        return False, f"chưa đủ đoạn ({len(parts)}/{expected_parts})"

    # Build concat list with optional silence between
    has_silent = silent_between and os.path.isfile(silent_between)
    concat_list: list[str] = []
    for i, p in enumerate(parts):
        concat_list.append(p)
        if has_silent and i < len(parts) - 1:
            concat_list.append(silent_between)

    if len(concat_list) == 1 and not has_silent:
        try:
            shutil.copy2(parts[0], out_mp3)
            return True, f"copy 1 đoạn → {os.path.basename(out_mp3)}"
        except Exception as e:
            return False, str(e)

    def _binary_concat() -> tuple[bool, str]:
        try:
            with open(out_mp3, "wb") as out:
                for p in concat_list:
                    with open(p, "rb") as inp:
                        out.write(inp.read())
            if not os.path.isfile(out_mp3) or os.path.getsize(out_mp3) < 500:
                return False, "concat empty"
            return (
                True,
                f"concat {len(parts)} đoạn → {os.path.basename(out_mp3)}",
            )
        except Exception as e:
            return False, str(e)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return _binary_concat()

    # ffmpeg concat demuxer (re-encode fallback if copy fails)
    list_path = ""
    try:
        fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_concat_")
        os.close(fd)
        with open(list_path, "w", encoding="utf-8") as f:
            for p in concat_list:
                ep = os.path.abspath(p).replace("'", "'\\''")
                f.write(f"file '{ep}'\n")
        for extra in (
            ["-c", "copy"],
            ["-c:a", "libmp3lame", "-q:a", "2"],
        ):
            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                *extra,
                out_mp3,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if (
                r.returncode == 0
                and os.path.isfile(out_mp3)
                and os.path.getsize(out_mp3) > 500
            ):
                msg = f"merge {len(parts)} đoạn"
                if has_silent:
                    msg += " (+silence)"
                msg += f" → {os.path.basename(out_mp3)}"
                return True, msg
        # last resort
        ok_b, msg_b = _binary_concat()
        if ok_b:
            return True, msg_b + " (ffmpeg fallback)"
        err = (r.stderr or r.stdout or "")[-180:]
        return False, f"ffmpeg fail: {err}"
    except Exception as e:
        ok_b, msg_b = _binary_concat()
        if ok_b:
            return True, msg_b + " (error fallback)"
        return False, str(e)
    finally:
        if list_path:
            try:
                os.remove(list_path)
            except Exception:
                pass


def append_silence_to_mp3(
    mp3_path: str,
    silent_path: str,
    out_path: str = "",
) -> bool:
    """
    Append silent MP3 to end of mp3_path.
    If out_path empty, overwrite mp3_path in-place.
    Returns True on success.
    """
    if not silent_path or not os.path.isfile(silent_path):
        return False
    if not mp3_path or not os.path.isfile(mp3_path):
        return False
    
    target = out_path or mp3_path
    ffmpeg = find_ffmpeg()
    
    if ffmpeg:
        # Use ffmpeg concat
        list_path = ""
        try:
            fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_sil_")
            os.close(fd)
            with open(list_path, "w", encoding="utf-8") as f:
                ep1 = os.path.abspath(mp3_path).replace("'", "'\\''")
                ep2 = os.path.abspath(silent_path).replace("'", "'\\''")
                f.write(f"file '{ep1}'\n")
                f.write(f"file '{ep2}'\n")
            
            tmp_out = target + ".tmp.mp3"
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                   "-c", "copy", tmp_out]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 500:
                if target != mp3_path or tmp_out != target:
                    shutil.move(tmp_out, target)
                else:
                    os.replace(tmp_out, target)
                return True
            # fallback to binary
            if os.path.isfile(tmp_out):
                os.remove(tmp_out)
        except Exception:
            pass
        finally:
            if list_path:
                try:
                    os.remove(list_path)
                except Exception:
                    pass
    
    # Binary fallback
    try:
        tmp_out = target + ".tmp.mp3"
        with open(tmp_out, "wb") as out:
            with open(mp3_path, "rb") as inp:
                out.write(inp.read())
            with open(silent_path, "rb") as inp:
                out.write(inp.read())
        if os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 500:
            os.replace(tmp_out, target)
            return True
        if os.path.isfile(tmp_out):
            os.remove(tmp_out)
    except Exception:
        pass
    return False


def build_chunks_from_sources(
    sources: Iterable[dict],
    max_chars: int,
    output_root: str,
) -> List[dict]:
    """
    sources: [{file, path, text}, ...]
    → chunks: [{file, path_src, stem, file_idx, part, text, out_path, total_parts}, ...]
    """
    chunks: List[dict] = []
    for fi, s in enumerate(sources):
        fname = s.get("file") or f"file_{fi+1}.txt"
        stem = safe_stem(fname)
        parts = smart_split_text(s.get("text") or "", max_chars)
        total = len(parts)
        file_dir = file_output_dir(output_root, fname)
        for j, t in enumerate(parts):
            part = j + 1
            out_p = os.path.join(file_dir, f"doan_{part}.mp3")
            chunks.append(
                {
                    "file": fname,
                    "path_src": s.get("path") or "",
                    "stem": stem,
                    "file_idx": fi,
                    "part": part,
                    "total_parts": total,
                    "text": t,
                    "out_path": out_p,
                    "file_dir": file_dir,
                    "merged_path": os.path.join(file_dir, f"{stem}.mp3"),
                    "path": out_p if os.path.isfile(out_p) and os.path.getsize(out_p) > 500 else None,
                }
            )
    return chunks

# -*- coding: utf-8 -*-
"""
Output layout giống appTTs-clean-v327:

  {output_root}/
    {stem}/
      doan_{para}.mp3            ← 1 chunk / paragraph
      doan_{para}_{sub}.mp3      ← nhiều sub-chunk trong paragraph
      para_{para}.mp3            ← merge sub (nếu multi)
      {stem}.mp3                 ← merge full

Tính năng:
  • Split paragraph (\\n\\n / dòng) → chunk ≤ max_chars
  • Ngắt âm theo ký tự khi split (silence khi merge)
  • Gap giữa đoạn: bật/tắt, giây, mỗi N đoạn
  • SRT đúng nhịp (gap = start_next − end_cur)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Defaults (v327-style)
DEFAULT_GAP_ENABLED = True
DEFAULT_GAP_SECONDS = 1.5
DEFAULT_GAP_EVERY = 1
DEFAULT_PAUSE_CHAR_ENABLED = False
DEFAULT_CHAR1 = ","
DEFAULT_CHAR1_SEC = 0.3
DEFAULT_CHAR2 = "."
DEFAULT_CHAR2_SEC = 0.5


def default_advanced() -> Dict[str, Any]:
    return {
        "gap_enabled": DEFAULT_GAP_ENABLED,
        "gap_seconds": DEFAULT_GAP_SECONDS,
        "gap_every": DEFAULT_GAP_EVERY,
        "pause_char_enabled": DEFAULT_PAUSE_CHAR_ENABLED,
        "char1": DEFAULT_CHAR1,
        "char1_sec": DEFAULT_CHAR1_SEC,
        "char2": DEFAULT_CHAR2,
        "char2_sec": DEFAULT_CHAR2_SEC,
    }


def normalize_advanced(raw: Optional[dict] = None) -> Dict[str, Any]:
    d = default_advanced()
    if not raw or not isinstance(raw, dict):
        return d
    d["gap_enabled"] = bool(raw.get("gap_enabled", d["gap_enabled"]))
    try:
        d["gap_seconds"] = max(0.0, min(30.0, float(raw.get("gap_seconds", d["gap_seconds"]))))
    except (TypeError, ValueError):
        pass
    try:
        d["gap_every"] = max(1, min(100, int(raw.get("gap_every", d["gap_every"]))))
    except (TypeError, ValueError):
        pass
    d["pause_char_enabled"] = bool(raw.get("pause_char_enabled", d["pause_char_enabled"]))
    c1 = str(raw.get("char1", d["char1"]) or ",")
    c2 = str(raw.get("char2", d["char2"]) or ".")
    d["char1"] = (c1[:1] if c1 else ",")
    d["char2"] = (c2[:1] if c2 else ".")
    try:
        d["char1_sec"] = max(0.0, min(10.0, float(raw.get("char1_sec", d["char1_sec"]))))
    except (TypeError, ValueError):
        pass
    try:
        d["char2_sec"] = max(0.0, min(10.0, float(raw.get("char2_sec", d["char2_sec"]))))
    except (TypeError, ValueError):
        pass
    return d


def safe_stem(name: str) -> str:
    """Tên folder an toàn từ basename file."""
    s = Path(name or "doan").stem
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s).strip(" .")
    return s or "doan"


_SSML_BREAK_RE = re.compile(r"<break\s+time=\"\d+ms\"\s*/>", re.I)


def strip_ssml_breaks(text: str) -> str:
    """Bỏ thẻ <break …/> để đếm ký tự nội dung (plain)."""
    return _SSML_BREAK_RE.sub("", text or "")


def plain_char_count(text: str) -> int:
    return len(strip_ssml_breaks(text or ""))


def smart_split_text(text: str, max_chars: int = 300) -> List[str]:
    """
    Chia đoạn thông minh (gần v327):
    1) Cắt theo . ! ? 。！？ …
    2) Gói câu vào chunk ≤ max_chars
    3) Câu quá dài → cắt theo khoảng trắng
    4) Từ quá dài → hard-cut
    """
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []
    max_chars = max(40, int(max_chars or 300))
    if len(text) <= max_chars:
        return [text]

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
        if len(w) > max_chars:
            # hard-cut từ/khối không khoảng trắng
            if cur:
                out.append(" ".join(cur))
                cur = []
            for i in range(0, len(w), max_chars):
                piece = w[i : i + max_chars]
                if piece:
                    out.append(piece)
            continue
        trial = (" ".join(cur + [w])).strip()
        if len(trial) > max_chars and cur:
            out.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _bisect_plain(plain: str) -> Tuple[str, str]:
    """Cắt plain gần giữa tại khoảng trắng (fallback)."""
    plain = (plain or "").strip()
    if len(plain) < 2:
        return plain, ""
    mid = len(plain) // 2
    window = plain[:mid]
    sp = window.rfind(" ")
    if sp > max(10, mid // 4):
        return plain[:sp].strip(), plain[sp:].strip()
    # thử tìm space sau mid
    sp2 = plain.find(" ", mid)
    if sp2 > 0 and sp2 < len(plain) - 1:
        return plain[:sp2].strip(), plain[sp2:].strip()
    return plain[:mid].strip(), plain[mid:].strip()


def split_paragraphs(text: str) -> List[str]:
    """
    Split theo paragraph (v327):
    - Ưu tiên \\n\\n
    - Không có → mỗi dòng non-empty là 1 paragraph
    - Không có line break → 1 block
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if "\n\n" in text:
        return [p.strip() for p in re.split(r"\n\s*\n+", text) if p and p.strip()]
    if "\n" in text:
        return [ln.strip() for ln in text.split("\n") if ln.strip()]
    return [text]


def _ts_to_seconds(ts: str) -> float:
    ts = (ts or "").strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_srt_cues(path: str) -> List[dict]:
    """
    Parse SRT → [{start, end, text, gap_after}, ...].
    gap_after = start(next) − end(cur) (0 for last).
    """
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    pattern = (
        r"(\d+)\s*\n"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
        r"((?:(?!\n\n|\n\d+\s*\n).)*)"
    )
    matches = re.findall(pattern, content, re.DOTALL)
    entries: List[dict] = []
    for _idx, start_ts, end_ts, text in matches:
        t = re.sub(r"\s+", " ", (text or "").strip())
        if not t:
            continue
        entries.append(
            {
                "start": _ts_to_seconds(start_ts),
                "end": _ts_to_seconds(end_ts),
                "text": t,
                "gap_after": 0.0,
            }
        )
    for i in range(len(entries) - 1):
        gap = entries[i + 1]["start"] - entries[i]["end"]
        entries[i]["gap_after"] = max(0.0, float(gap))
    return entries


def srt_to_plain_text(path: str) -> str:
    cues = parse_srt_cues(path)
    if cues:
        return "\n\n".join(c["text"] for c in cues)
    # fallback strip like before
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return " ".join(lines)


def insert_ssml_breaks(
    txt: str,
    char1: str,
    char1_sec: float,
    char2: str,
    char2_sec: float,
) -> str:
    """
    Chèn SSML/HTML <break> sau ký tự — logic giống v327 (11Labs0811.insert_ssml_breaks).

    VD: "Hello, world." + char1=',' 0.3s, char2='.' 0.5s
      → Hello,<break time="300ms"/> world.<break time="500ms"/>

    Không tách request TTS; break nằm trong cùng payload (model v3 / API tự nghỉ).
    """
    if not txt:
        return txt
    result = txt
    c1 = (char1 or "")[:1]
    c2 = (char2 or "")[:1]
    if c1 and char1_sec and float(char1_sec) > 0:
        ms1 = int(min(3000, max(0, float(char1_sec) * 1000)))
        if ms1 > 0:
            tag1 = f'<break time="{ms1}ms"/>'
            esc1 = re.escape(c1)
            # tránh chèn trùng nếu đã có break ngay sau ký tự
            result = re.sub(rf"({esc1})(?!<break)", rf"\1{tag1}", result)
    if c2 and char2_sec and float(char2_sec) > 0:
        ms2 = int(min(3000, max(0, float(char2_sec) * 1000)))
        if ms2 > 0:
            tag2 = f'<break time="{ms2}ms"/>'
            esc2 = re.escape(c2)
            result = re.sub(rf"({esc2})(?!<break)", rf"\1{tag2}", result)
    return result


def expand_paragraph_to_units(
    para_text: str,
    max_chars: int,
    advanced: Optional[dict] = None,
) -> List[Tuple[str, float]]:
    """
    1 paragraph → list (text, silence_after=0) ready for TTS.

    max_chars = trần ĐỘ DÀI PAYLOAD gửi TTS (kể cả thẻ <break>).

    Trước đây chỉ split text sạch ≤ max_chars rồi chèn SSML → payload phình
    (vd admin 900 → cột Ký tự 1000+). Giờ re-pack đến khi len(payload) ≤ max_chars.
    """
    adv = normalize_advanced(advanced)
    limit = max(40, int(max_chars or 300))
    para_text = (para_text or "").strip()
    if not para_text:
        return []

    pause_on = bool(adv.get("pause_char_enabled"))

    def with_breaks(plain: str) -> str:
        plain = (plain or "").strip()
        if not plain:
            return ""
        if not pause_on:
            return plain
        return insert_ssml_breaks(
            plain,
            adv["char1"],
            adv["char1_sec"],
            adv["char2"],
            adv["char2_sec"],
        )

    def pack_plain(plain: str, depth: int = 0) -> List[str]:
        """Trả về list payload TTS, mỗi phần len ≤ limit."""
        plain = (plain or "").strip()
        if not plain:
            return []
        payload = with_breaks(plain)
        if len(payload) <= limit:
            return [payload]

        # Quá dài sau SSML → chia nhỏ text sạch rồi pack lại
        if depth > 14:
            # an toàn: hard-cut plain theo tỉ lệ overhead
            ratio = len(payload) / max(len(plain), 1)
            cut = max(20, int(limit / max(ratio, 1.05)) - 2)
            cut = min(cut, max(20, len(plain) - 1))
            left, right = plain[:cut].strip(), plain[cut:].strip()
            if not right:
                # không cắt được — trả nguyên (hiếm, từ siêu dài)
                return [payload]
            return pack_plain(left, depth + 1) + pack_plain(right, depth + 1)

        # Ước plain budget từ overhead SSML
        ratio = len(payload) / max(len(plain), 1)
        plain_budget = max(40, int(limit / max(ratio, 1.05) * 0.90))
        # Không để budget ≥ plain (sẽ loop)
        if plain_budget >= len(plain):
            plain_budget = max(40, len(plain) // 2)

        parts = smart_split_text(plain, plain_budget)
        if len(parts) <= 1:
            left, right = _bisect_plain(plain)
            if not right or left == plain:
                # hard cut
                mid = max(20, len(plain) // 2)
                left, right = plain[:mid].strip(), plain[mid:].strip()
            if not right:
                return [payload]
            parts = [left, right]

        out: List[str] = []
        for p in parts:
            out.extend(pack_plain(p, depth + 1))
        return out

    payloads = pack_plain(para_text)
    # silence_after luôn 0 — ngắt do <break> trong audio
    return [(t, 0.0) for t in payloads if t]


def file_output_dir(output_root: str, file_name: str) -> str:
    """{output_root}/{stem}/"""
    stem = safe_stem(file_name)
    d = os.path.join(output_root, stem)
    os.makedirs(d, exist_ok=True)
    return d


def doan_path(output_root: str, file_name: str, part: int) -> str:
    """…/{stem}/doan_{part}.mp3 — legacy flat numbering."""
    d = file_output_dir(output_root, file_name)
    return os.path.join(d, f"doan_{int(part)}.mp3")


def leaf_out_path(file_dir: str, para_idx: int, sub_idx: int, total_subs: int) -> str:
    if total_subs <= 1:
        return os.path.join(file_dir, f"doan_{para_idx}.mp3")
    return os.path.join(file_dir, f"doan_{para_idx}_{sub_idx}.mp3")


def para_merged_path(file_dir: str, para_idx: int) -> str:
    return os.path.join(file_dir, f"para_{para_idx}.mp3")


def merged_path(output_root: str, file_name: str) -> str:
    """…/{stem}/{stem}.mp3"""
    stem = safe_stem(file_name)
    d = file_output_dir(output_root, file_name)
    return os.path.join(d, f"{stem}.mp3")


def list_doan_files(file_dir: str) -> List[str]:
    """doan_*.mp3 sort — chỉ leaf doan_N hoặc doan_N_M (không para_)."""
    p = Path(file_dir)
    if not p.is_dir():
        return []
    files = []
    for f in p.glob("doan_*.mp3"):
        m = re.match(r"doan_(\d+)(?:_(\d+))?\.mp3$", f.name, re.I)
        if m and f.stat().st_size > 500:
            para = int(m.group(1))
            sub = int(m.group(2) or 0)
            files.append((para, sub, str(f)))
    files.sort(key=lambda x: (x[0], x[1]))
    return [path for _, _, path in files]


def find_ffmpeg() -> Optional[str]:
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def ensure_silence_mp3(seconds: float = 1.5, studio_dir: str = "") -> str:
    """Create/cache silent MP3; return path or ''."""
    from app_paths import app_dir, resource_dir

    seconds = float(seconds or 0)
    if seconds <= 0:
        return ""
    # quantize to 1 decimal for cache name stability
    seconds = round(seconds, 1)
    if seconds <= 0:
        return ""
    res = studio_dir or resource_dir()
    cache = studio_dir or app_dir()
    name = f"silent_{str(seconds).replace('.', '_')}s.mp3"
    for root in (res, cache):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 200:
            return candidate
    out = os.path.join(cache, name)
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        for root in (res, cache):
            fallback = os.path.join(root, "silent_1s.mp3")
            if os.path.isfile(fallback):
                return fallback
        return ""
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=44100",
            "-t",
            str(seconds),
            "-acodec",
            "libmp3lame",
            "-ar",
            "44100",
            "-b:a",
            "128k",
            out,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 200:
            return out
    except Exception:
        pass
    for root in (res, cache):
        fallback = os.path.join(root, "silent_1s.mp3")
        if os.path.isfile(fallback):
            return fallback
    return ""


def _concat_files(paths: Sequence[str], out_mp3: str) -> tuple[bool, str]:
    """Concat ordered audio files (ffmpeg demuxer, binary fallback)."""
    paths = [p for p in paths if p and os.path.isfile(p) and os.path.getsize(p) > 200]
    if not paths:
        return False, "không có file để ghép"
    if len(paths) == 1:
        try:
            shutil.copy2(paths[0], out_mp3)
            return True, f"copy → {os.path.basename(out_mp3)}"
        except Exception as e:
            return False, str(e)

    def _binary_concat() -> tuple[bool, str]:
        try:
            with open(out_mp3, "wb") as out:
                for p in paths:
                    with open(p, "rb") as inp:
                        out.write(inp.read())
            if not os.path.isfile(out_mp3) or os.path.getsize(out_mp3) < 500:
                return False, "concat empty"
            return True, f"concat {len(paths)} files → {os.path.basename(out_mp3)}"
        except Exception as e:
            return False, str(e)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return _binary_concat()

    list_path = ""
    try:
        fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_concat_")
        os.close(fd)
        with open(list_path, "w", encoding="utf-8") as f:
            for p in paths:
                ep = os.path.abspath(p).replace("'", r"'\''")
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
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if (
                r.returncode == 0
                and os.path.isfile(out_mp3)
                and os.path.getsize(out_mp3) > 500
            ):
                return True, f"merge {len(paths)} files → {os.path.basename(out_mp3)}"
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


def build_concat_list_with_gaps(
    audio_files: Sequence[str],
    gaps: Sequence[float],
) -> List[str]:
    """
    audio_files: N paths; gaps: N-1 silence seconds between them.
    Returns flat list of paths (audio + generated silence files).
    """
    files = list(audio_files)
    if not files:
        return []
    if len(files) == 1:
        return files
    # pad gaps
    g = list(gaps or [])
    while len(g) < len(files) - 1:
        g.append(0.0)
    g = g[: len(files) - 1]

    out: List[str] = []
    parent = os.path.dirname(os.path.abspath(files[0])) or "."
    for i, f in enumerate(files):
        out.append(f)
        if i < len(g):
            sec = float(g[i] or 0)
            if sec > 0.05:
                # cache silence beside source (or app cache)
                sil = ensure_silence_mp3(sec)
                if not sil:
                    # try write into parent
                    name = f"_silence_{str(round(sec, 1)).replace('.', '_')}s.mp3"
                    cand = os.path.join(parent, name)
                    sil = ensure_silence_mp3(sec)
                    if sil and sil != cand:
                        try:
                            shutil.copy2(sil, cand)
                            sil = cand
                        except Exception:
                            pass
                if sil and os.path.isfile(sil):
                    out.append(sil)
    return out


def gaps_from_every(
    n_files: int,
    *,
    gap_enabled: bool,
    gap_seconds: float,
    gap_every: int,
) -> List[float]:
    """v327: chèn silence sau mỗi gap_every file (1-based index)."""
    if n_files <= 1:
        return []
    if not gap_enabled or gap_seconds <= 0 or gap_every <= 0:
        return [0.0] * (n_files - 1)
    gaps = []
    for i in range(1, n_files):  # after file i (1-based)
        if i % gap_every == 0:
            gaps.append(float(gap_seconds))
        else:
            gaps.append(0.0)
    return gaps


def merge_audio_with_gaps(
    audio_files: Sequence[str],
    out_mp3: str,
    gaps: Sequence[float],
) -> tuple[bool, str]:
    concat = build_concat_list_with_gaps(audio_files, gaps)
    n_audio = len([p for p in audio_files if p])
    ok, msg = _concat_files(concat, out_mp3)
    if ok and any(float(g or 0) > 0.05 for g in (gaps or [])):
        msg = msg.replace("→", "(+silence) →", 1) if "(+silence)" not in msg else msg
    return ok, msg


def merge_doan_mp3s(
    file_dir: str,
    out_mp3: str,
    *,
    expected_parts: int = 0,
    silent_between: str = "",
    silence_seconds: float = 1.5,
    gap_enabled: bool = True,
    gap_every: int = 1,
    gaps: Optional[Sequence[float]] = None,
) -> tuple[bool, str]:
    """
    Ghép doan_* → out_mp3.
    - gaps: explicit per-boundary seconds (ưu tiên)
    - else gap_enabled + silence_seconds + gap_every
    """
    parts = list_doan_files(file_dir)
    if not parts:
        return False, "không có doan_*.mp3"
    if expected_parts > 0 and len(parts) < expected_parts:
        return False, f"chưa đủ đoạn ({len(parts)}/{expected_parts})"

    if gaps is not None:
        g = list(gaps)
    else:
        g = gaps_from_every(
            len(parts),
            gap_enabled=gap_enabled and silence_seconds > 0,
            gap_seconds=silence_seconds,
            gap_every=max(1, int(gap_every or 1)),
        )
        # optional fixed silent file for uniform gap
        if silent_between and os.path.isfile(silent_between) and gap_enabled:
            # rebuild concat with that single silence file
            sec = float(silence_seconds or 0)
            if sec > 0:
                for i in range(len(g)):
                    if g[i] > 0:
                        g[i] = sec

    return merge_audio_with_gaps(parts, out_mp3, g)


def build_chunks_from_sources(
    sources: Iterable[dict],
    max_chars: int,
    output_root: str,
    advanced: Optional[dict] = None,
) -> List[dict]:
    """
    sources: [{file, path, text, srt_cues?}, ...]
    → leaf chunks (TTS units) with paragraph hierarchy + silence metadata.
    """
    adv = normalize_advanced(advanced)
    chunks: List[dict] = []

    for fi, s in enumerate(sources):
        fname = s.get("file") or f"file_{fi+1}.txt"
        stem = safe_stem(fname)
        file_dir = file_output_dir(output_root, fname)
        src_path = s.get("path") or ""

        # paragraphs: SRT cues or text paragraphs
        srt_cues = s.get("srt_cues")
        if srt_cues is None and src_path.lower().endswith(".srt"):
            srt_cues = parse_srt_cues(src_path)

        paras: List[dict] = []
        if srt_cues:
            for ci, cue in enumerate(srt_cues):
                paras.append(
                    {
                        "text": cue.get("text") or "",
                        "srt_gap_after": float(cue.get("gap_after") or 0),
                        "srt_start": float(cue.get("start") or 0),
                        "srt_end": float(cue.get("end") or 0),
                    }
                )
        else:
            for ptext in split_paragraphs(s.get("text") or ""):
                paras.append({"text": ptext, "srt_gap_after": None})

        if not paras:
            continue

        # expand each paragraph → units
        para_units: List[List[Tuple[str, float]]] = []
        for para in paras:
            units = expand_paragraph_to_units(para.get("text") or "", max_chars, adv)
            if not units:
                units = [(" ", 0.0)]  # skip empty? better skip
                units = []
            para_units.append(units)

        # drop empty paras
        cleaned_paras = []
        cleaned_units = []
        for para, units in zip(paras, para_units):
            if units:
                cleaned_paras.append(para)
                cleaned_units.append(units)
        paras, para_units = cleaned_paras, cleaned_units
        if not paras:
            continue

        total_paras = len(paras)
        total_parts = sum(len(u) for u in para_units)
        leaf_i = 0

        for pi, (para, units) in enumerate(zip(paras, para_units), start=1):
            n_sub = len(units)
            srt_gap = para.get("srt_gap_after")
            for si, (utext, sil_after) in enumerate(units, start=1):
                leaf_i += 1
                out_p = leaf_out_path(file_dir, pi, si, n_sub)
                # silence after this leaf when merging:
                # - within para: pause-char silence
                # - last sub of para: SRT gap to next cue (if any), else 0 here
                #   (inter-para gap_every applied later if no srt)
                if si < n_sub:
                    silence_after = float(sil_after or 0)
                    srt_after = None
                else:
                    silence_after = float(sil_after or 0)
                    srt_after = (
                        float(srt_gap)
                        if srt_gap is not None
                        else None
                    )

                chunks.append(
                    {
                        "file": fname,
                        "path_src": src_path,
                        "stem": stem,
                        "file_idx": fi,
                        "para_idx": pi,
                        "sub_idx": si,
                        "total_subs": n_sub,
                        "total_paras": total_paras,
                        "part": leaf_i,  # flat leaf index 1..N
                        "total_parts": total_parts,
                        "text": utext,
                        # plain = nội dung (không tính thẻ <break>); payload = len gửi API
                        "plain_chars": plain_char_count(utext),
                        "payload_chars": len(utext or ""),
                        "out_path": out_p,
                        "file_dir": file_dir,
                        "para_path": para_merged_path(file_dir, pi)
                        if n_sub > 1
                        else out_p,
                        "merged_path": os.path.join(file_dir, f"{stem}.mp3"),
                        "silence_after": silence_after,
                        "srt_gap_after": srt_after,
                        "is_srt": srt_cues is not None,
                        "path": out_p
                        if os.path.isfile(out_p) and os.path.getsize(out_p) > 500
                        else None,
                    }
                )
    return chunks


def compute_merge_gaps_for_leaves(
    leaves: List[dict],
    advanced: Optional[dict] = None,
) -> List[float]:
    """
    gaps[i] = silence between leaves[i] and leaves[i+1].
    Priority:
      1) SRT gap when crossing paragraph (on last sub)
      2) pause-char silence_after within paragraph
      3) gap_enabled / gap_every / gap_seconds on leaf index
    """
    adv = normalize_advanced(advanced)
    n = len(leaves)
    if n <= 1:
        return []

    # base from gap_every on leaf order
    base = gaps_from_every(
        n,
        gap_enabled=bool(adv["gap_enabled"]),
        gap_seconds=float(adv["gap_seconds"]),
        gap_every=int(adv["gap_every"]),
    )
    gaps = list(base)

    for i in range(n - 1):
        a = leaves[i]
        b = leaves[i + 1]
        same_para = int(a.get("para_idx") or 0) == int(b.get("para_idx") or 0)

        if same_para:
            sil = float(a.get("silence_after") or 0)
            if sil > 0.05:
                gaps[i] = sil
            # else keep base gap_every (or 0)
        else:
            # paragraph boundary
            srt_g = a.get("srt_gap_after")
            if srt_g is not None:
                gaps[i] = max(0.0, float(srt_g))
            else:
                sil = float(a.get("silence_after") or 0)
                if sil > 0.05:
                    gaps[i] = max(gaps[i], sil)
                # else keep base (gap_every between paragraphs as segments)
    return gaps


def merge_file_from_chunks(
    leaves: List[dict],
    out_mp3: str,
    advanced: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Hierarchical merge:
      1) each paragraph multi-sub → para_{n}.mp3 (pause-char silences)
      2) all para outputs → full file (SRT / gap_every)
    """
    if not leaves:
        return False, "không có đoạn"
    adv = normalize_advanced(advanced)
    # sort by para, sub
    leaves = sorted(
        leaves,
        key=lambda c: (int(c.get("para_idx") or 0), int(c.get("sub_idx") or 0)),
    )
    file_dir = leaves[0].get("file_dir") or os.path.dirname(leaves[0].get("out_path") or ".")

    # group by para
    by_para: Dict[int, List[dict]] = {}
    for ch in leaves:
        pi = int(ch.get("para_idx") or 0)
        by_para.setdefault(pi, []).append(ch)

    para_files: List[str] = []
    para_boundary_meta: List[dict] = []  # last leaf of each para (for srt gap)

    for pi in sorted(by_para.keys()):
        group = sorted(by_para[pi], key=lambda c: int(c.get("sub_idx") or 0))
        paths = []
        for ch in group:
            p = ch.get("out_path") or ch.get("path") or ""
            if not p or not os.path.isfile(p) or os.path.getsize(p) < 500:
                return False, f"thiếu doan para {pi}: {os.path.basename(p) or '?'}"
            paths.append(p)

        if len(paths) == 1:
            para_out = paths[0]
        else:
            # internal gaps = silence_after of each sub except last uses silence_after too
            inner_gaps = []
            for j in range(len(group) - 1):
                inner_gaps.append(float(group[j].get("silence_after") or 0))
            para_out = para_merged_path(file_dir, pi)
            ok_p, msg_p = merge_audio_with_gaps(paths, para_out, inner_gaps)
            if not ok_p:
                return False, f"merge para {pi}: {msg_p}"
        para_files.append(para_out)
        para_boundary_meta.append(group[-1])

    if len(para_files) == 1:
        try:
            if os.path.abspath(para_files[0]) != os.path.abspath(out_mp3):
                shutil.copy2(para_files[0], out_mp3)
            return True, f"1 đoạn văn → {os.path.basename(out_mp3)}"
        except Exception as e:
            return False, str(e)

    # gaps between paragraphs
    n = len(para_files)
    # start from gap_every on paragraph units
    p_gaps = gaps_from_every(
        n,
        gap_enabled=bool(adv["gap_enabled"]),
        gap_seconds=float(adv["gap_seconds"]),
        gap_every=int(adv["gap_every"]),
    )
    for i in range(n - 1):
        last = para_boundary_meta[i]
        srt_g = last.get("srt_gap_after")
        if srt_g is not None:
            p_gaps[i] = max(0.0, float(srt_g))
        else:
            # also apply residual silence_after on last sub if any
            sil = float(last.get("silence_after") or 0)
            if sil > 0.05:
                p_gaps[i] = max(p_gaps[i], sil)

    return merge_audio_with_gaps(para_files, out_mp3, p_gaps)

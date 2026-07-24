# -*- coding: utf-8 -*-
"""
Multi-voice dialogue script parser:

  1) Map nhân vật → voice_id (UI table)
  2) Script dạng:
        Nam: [happy] Chào cậu!
        Nữ: [curious] Chào!
  3) Mỗi dòng = 1 turn → 1 call TTS (voice riêng) → merge MP3
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# "Nam: hello" or "@Nam: hello"  — name then first colon, rest is text
_LINE_RE = re.compile(
    r"^\s*@?(?P<name>[^:=\s][^:=]*?)\s*:\s*(?P<text>.+?)\s*$"
)
# Header map: "Nam = voiceid" or "Nam:voiceid" (voice-like token, no spaces)
_MAP_EQ_RE = re.compile(
    r"^\s*@?(?P<name>[^=:]+?)\s*=\s*(?P<voice>[A-Za-z0-9_-]{10,})\s*$"
)
_MAP_COLON_RE = re.compile(
    r"^\s*@?(?P<name>[^:]+?)\s*:\s*(?P<voice>[A-Za-z0-9_-]{10,})\s*$"
)


@dataclass
class Turn:
    speaker: str
    text: str
    voice_id: str = ""
    line_no: int = 0


@dataclass
class ParseResult:
    turns: List[Turn] = field(default_factory=list)
    maps_from_script: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def default_script() -> str:
    return (
        "# Hội thoại 2 giọng — mỗi dòng: Tên: nội dung\n"
        "# Gán giọng ở bảng bên trên (hoặc dòng: Tên = voice_id)\n"
        "# Emo + break: [happy] [laughs] <break time=\"200ms\"/>\n"
        "\n"
        "Nam: [happy] Chào cậu!<break time=\"300ms\"/> Lâu rồi không gặp.\n"
        "Nữ: [curious] Chào!<break time=\"200ms\"/> Dạo này cậu khỏe không?\n"
        "Nam: [laughs] Khỏe lắm.<break time=\"300ms\"/> Nhìn cậu vui ghê.\n"
        "Nữ: [friendly] Thôi đi cà phê đi.<break time=\"200ms\"/> Mình kể chuyện cười lắm.\n"
        "Nam: [excited] Được luôn!<break time=\"200ms\"/> Đi thôi.\n"
    )


def _looks_like_voice_id(s: str) -> bool:
    s = (s or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{10,}", s))


def parse_script(
    script: str,
    voice_map: Optional[Dict[str, str]] = None,
) -> ParseResult:
    """
    Parse script + voice map (UI).
    voice_map keys are speaker names (case-sensitive as typed).
    """
    res = ParseResult()
    ui_map = {str(k).strip(): str(v).strip() for k, v in (voice_map or {}).items() if str(k).strip()}
    script_map: Dict[str, str] = {}

    lines = (script or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        m_eq = _MAP_EQ_RE.match(line)
        if m_eq:
            name = m_eq.group("name").strip()
            vid = m_eq.group("voice").strip()
            script_map[name] = vid
            continue

        # Colon map only if entire RHS is voice_id (no spaces / no emo)
        m_col = _MAP_COLON_RE.match(line)
        if m_col and _looks_like_voice_id(m_col.group("voice")):
            # Avoid treating "Nam: Hello world" as map — voice has no spaces and looks like id
            name = m_col.group("name").strip()
            vid = m_col.group("voice").strip()
            # If RHS is pure voice id and name has no trailing text intent
            script_map[name] = vid
            continue

        m = _LINE_RE.match(line)
        if not m:
            res.warnings.append(f"Dòng {i}: bỏ qua (không khớp «Tên: nội dung») — {line[:60]}")
            continue
        name = m.group("name").strip()
        text = m.group("text").strip()
        if _looks_like_voice_id(text) and " " not in text and "[" not in text:
            # treated as map already above; if here, still map
            script_map[name] = text
            continue
        if not text:
            res.warnings.append(f"Dòng {i}: trống — bỏ qua")
            continue
        res.turns.append(Turn(speaker=name, text=text, line_no=i))

    res.maps_from_script = dict(script_map)
    # Resolve voice: UI map overrides script map for same name? Prefer UI if set, else script
    merged: Dict[str, str] = dict(script_map)
    merged.update({k: v for k, v in ui_map.items() if v})

    for t in res.turns:
        vid = merged.get(t.speaker) or ""
        # case-insensitive fallback
        if not vid:
            for k, v in merged.items():
                if k.lower() == t.speaker.lower():
                    vid = v
                    break
        t.voice_id = (vid or "").strip()
        if not t.voice_id:
            res.errors.append(
                f"Dòng {t.line_no}: nhân vật «{t.speaker}» chưa gán voice_id"
            )

    if not res.turns:
        res.errors.append("Chưa có lượt thoại nào — viết dạng: Nam: xin chào")

    return res


def speakers_in_script(script: str) -> List[str]:
    """Unique speaker names in order of first appearance (for auto-fill table)."""
    seen = set()
    order: List[str] = []
    pr = parse_script(script, {})
    for t in pr.turns:
        if t.speaker not in seen:
            seen.add(t.speaker)
            order.append(t.speaker)
    for name in pr.maps_from_script:
        if name not in seen:
            seen.add(name)
            order.append(name)
    return order


def validate_ready(
    script: str,
    voice_map: Dict[str, str],
) -> Tuple[bool, ParseResult]:
    pr = parse_script(script, voice_map)
    ok = not pr.errors and bool(pr.turns)
    return ok, pr

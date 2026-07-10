from __future__ import annotations

import re

DEFAULT_CHUNK_MAX = 950
HARD_MAX = 1000


def split_long_on_words(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_chars:
            out.append(rest.strip())
            break
        window = rest[: max_chars + 1]
        sp = window.rfind(" ", 0, max_chars + 1)
        if sp <= 0:
            piece, rest = rest[:max_chars], rest[max_chars:].lstrip()
        else:
            piece, rest = rest[:sp].rstrip(), rest[sp + 1 :].lstrip()
        if piece:
            out.append(piece)
    return out


def load_text_chunks(raw: str, max_chars: int = DEFAULT_CHUNK_MAX) -> list[str]:
    max_chars = min(max_chars, HARD_MAX)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
    if not raw:
        return []
    parts = re.split(r"(?<=[.!?])\s+", raw.replace("\n", " "))
    sents = [s.strip() for s in parts if s and s.strip()] or [raw]
    chunks: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        b = buf.strip()
        buf = ""
        if not b:
            return
        if len(b) <= max_chars:
            chunks.append(b)
        else:
            chunks.extend(split_long_on_words(b, max_chars))

    for s in sents:
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        if len(s) > max_chars:
            flush()
            chunks.extend(split_long_on_words(s, max_chars))
            continue
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_chars:
            buf = f"{buf} {s}"
        else:
            flush()
            buf = s
    flush()
    return [c for c in chunks if c.strip()]

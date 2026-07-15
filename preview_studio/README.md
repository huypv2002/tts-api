# Bubble TTS Elevenlabs Unlimited Preview Studio (desktop tool LOCAL)

GUI clone OmniVoice — **generate TTS local** (`fast_tts`).

## Phân tách (BẮT BUỘC)

| Lớp | Nơi | Việc |
|-----|-----|------|
| **Tool local** | `preview_studio/` | Login local, generate MP3 trên máy |
| **Web admin** | `cloudflare-admin/` (Workers + **D1**) | Account, gói ký tự, proxy, max luồng |
| **Windows server** | `tts-api/` (optional) | Chỉ worker TTS nặng nếu scale — **không** là admin DB |

Xem `../ARCHITECTURE.md`.

## Login tool

Chỉ **username + password** (không endpoint).

Default: `admin` / `admin123`

## Generate

- Gắn proxy trên account local (⚙) **hoặc** dùng proxy đã cấu hình khi chạy local
- TXT / folder / SRT → chunk → fast_tts → MP3
- Max **5 luồng**

## Chạy

```bash
cd preview_studio
pip3 install -r requirements.txt
python3 PreviewStudio.py
```

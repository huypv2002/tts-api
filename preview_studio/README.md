# HuyViet Preview Studio

Clone UI **OmniVoiceOnly** (PySide6), backend **tts-api** (không Omni/Colab).

## Tính năng

| | |
|--|--|
| Login | API Key `tts_…` + Server URL |
| Generate | Batch TXT / folder / SRT → chunk → `POST /v1/tts` → MP3 |
| Settings | Gắn **proxyxoay** cho account (API key) qua admin |
| Voice | Voice ID ElevenLabs preview (default server) |

## Cài

```bash
cd preview_studio
pip install -r requirements.txt
# cần tts-api đang chạy (Windows + tunnel hoặc local)
python PreviewStudio.py
```

## Luồng gắn proxy cho account

1. Admin tạo API key trên server (hoặc bootstrap key).
2. Mở ⚙ **Cài đặt** trong app.
3. Điền:
   - Admin password (`30102002` hoặc password server)
   - Proxyxoay: api_key / user / pass / host / port
4. **Lưu account + proxy** → server `PATCH /admin/api/keys/{id}`  
   Worker ưu tiên proxy riêng của key; fallback pool chung.

## Server cần

```bash
cd C:\TTS\tts-api
git pull
# restart start_all.bat  (có migration cột proxy_* trên api_keys)
```

## File

```
preview_studio/
  PreviewStudio.py      # entry (login + main window)
  ui/preview_tab.py     # UI clone OmniVoice tab
  client/tts_api_client.py
  requirements.txt
```

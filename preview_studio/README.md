# HuyViet Preview Studio (desktop tool)

GUI clone OmniVoice — **generate TTS local** (`fast_tts`).

## Phân tách rõ

| | |
|--|--|
| **Admin web** | https://tts-origin.liveyt.pro/admin/ — account/API key, gói ký tự, max luồng ≤5, proxyxoay |
| **Tool desktop** | `PreviewStudio.py` — login user/pass local, chọn file, generate MP3 |

**Không** quản lý account/proxy/gói trên desktop. Admin chỉ trên web.

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

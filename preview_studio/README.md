# HuyViet Preview Studio (tool local)

Clone UI OmniVoice — **không** tts-api server.

## Login

Chỉ **username / password** (không endpoint).

Mặc định lần đầu: `admin` / `admin123` (role admin).

## Tab Generate TTS

- TXT / folder / SRT → chunk → `fast_tts` (HSW preview)
- Proxy theo account
- Luồng TTS **tối đa 5** (giới hạn theo gói account)

## Tab Quản trị (admin only)

| Tab | Chức năng |
|-----|-----------|
| **Account** | Tạo/sửa/xóa user, role, gói ký tự, max luồng (1–5), gán proxy |
| **Proxy** | Pool proxyxoay (host/port/user/pass/api_key) |
| **Gói ký tự** | 1M / 5M / 10M / 50M… tùy chỉnh |

## Chạy

```bash
cd preview_studio
pip3 install -r requirements.txt
python3 PreviewStudio.py
```

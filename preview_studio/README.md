# HuyViet Preview Studio (TOOL local)

Clone UI **OmniVoiceOnly**, **không** dùng tts-api server.

| | |
|--|--|
| Login | Account local (`accounts.json`) |
| Proxy | Gắn **proxyxoay** theo từng account |
| TTS | `fast_tts` (HSW + anonymous preview) trên máy |
| Omni | Đã bỏ |

## Chạy

```bash
cd /Users/phamvanhuy/Downloads/tts-preview/preview_studio
pip3 install -r requirements.txt
# cần camoufox + tls-client (đã có nếu từng chạy fast_tts)
python3 PreviewStudio.py
```

Lần đầu: user `admin` / pass `admin123` (đổi sau trong account).

## Gắn proxyxoay

1. ⚙ Cài đặt  
2. Điền host / port / user / pass (và api_key nếu có)  
3. **Lưu proxy cho account**  
4. Chọn TXT → Bắt đầu  

MP3 → thư mục `preview_studio/output/` (hoặc path bạn chọn).

## Phụ thuộc repo

Cần `fast_tts.py` ở thư mục cha (`tts-preview/`).

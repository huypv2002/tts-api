# TTS Studio (desktop tool LOCAL)

GUI desktop — generate TTS local.

## Login

Chỉ **username + password**. Tài khoản do quản trị viên cấp.

## Generate

- Đường truyền / gói ký tự do admin cấp
- TXT / folder / SRT / hội thoại → MP3
- Max **5 luồng**

## Chạy (dev)

```bash
cd preview_studio
pip3 install -r requirements.txt
python3 PreviewStudio.py
```

Ship: Nuitka standalone + runtime folders (xem `BUILD_NUITKA.md`).

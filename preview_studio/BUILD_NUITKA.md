# Build Preview Studio — Nuitka only

**Chỉ dùng Nuitka** (không PyInstaller).

Nuitka `--mode=app`:
- **Windows** → 1 file `.exe` (onefile)
- **macOS** → `PreviewStudio.app` (app bundle; bắt buộc cho PySide6/Foundation)

## Local (macOS)

```bash
cd /path/to/tts-preview
OUT_DIR="/Users/you/Documents/New project 4" bash preview_studio/build_nuitka.sh
# → …/TTS-Preview-Studio-Nuitka-darwin-arm64.zip
```

Cần: Python 3.11+, deps trong `requirements.txt` + `nuitka ordered-set zstandard`.

## Windows (GitHub Actions)

1. Push repo → Actions → **Build Preview Studio Nuitka**
2. `workflow_dispatch` → chạy
3. Tải artifact `TTS-Preview-Studio-Nuitka-windows.zip`

Hoặc tag `v*`.

## Nội dung ZIP

| File | Mô tả |
|------|--------|
| `PreviewStudio.app` (Mac) / `TTS Preview Studio.exe` (Win) | Nuitka build |
| `silent_*.mp3` | Gap merge 0.5s / 1s / 1.5s |
| `run-preview-studio.bat` | Launcher Windows |
| `proxyxoay.example.json` | Mẫu proxy |
| `HUONG_DAN.txt` | Hướng dẫn nhanh |

## Lưu ý

- **ffmpeg** trên PATH (merge MP3).
- **Camoufox browser** tải riêng (`camoufox fetch`) — không gói binary browser trong zip.
- Config / `accounts.json` / `output/` ghi **cạnh** `.app`/`.exe` (không ghi vào trong bundle).

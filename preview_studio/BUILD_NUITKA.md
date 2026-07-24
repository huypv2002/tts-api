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

## Nội dung ZIP (user-facing)

| File | Mô tả |
|------|--------|
| `TTS Studio.exe` / app | Nuitka standalone |
| `silent_*.mp3` | Gap merge |
| `bin/ffmpeg` | Merge audio |
| `camoufox-browser/` | Runtime bắt buộc |
| `HUONG_DAN.txt` | Hướng dẫn ngắn (không debug) |

## Lưu ý ship

- **Không** kèm `requirements.txt`, `BUILD_*.md`, `proxyxoay.example.json`, `CHAY-DEBUG.bat`.
- Console Windows: **disable** (không dump stdout pipeline).
- Config / `accounts.json` / `output/` ghi **cạnh** EXE (không bundle secrets).

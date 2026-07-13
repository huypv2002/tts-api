# TTS Local Tool — chạy trên Windows

Tool máy local: **HSW farm + token pool + multi-worker TTS** (không cần server API).

Repo private: `https://github.com/huypv2002/tts-api`

---

## 1) Máy Windows — cài 1 lần

### Yêu cầu
- Windows 10/11 hoặc Windows Server
- Python **3.11+** ([python.org](https://www.python.org/downloads/) — tick **Add to PATH**)
- Git + đã `gh auth login` hoặc HTTPS clone private repo

### Clone (nếu chưa có)

```powershell
# Cách A — GitHub CLI (private, nhanh)
gh repo clone huypv2002/tts-api C:\tts-api
cd C:\tts-api

# Cách B — đã clone sẵn
cd C:\tts-api
git pull
```

### Cài tool

```powershell
cd C:\tts-api
powershell -ExecutionPolicy Bypass -File .\install_tool.ps1
```

Script sẽ:
1. Tạo `.venv`
2. `pip install -r requirements-tool.txt`
3. Pin Playwright &lt; 1.61 (tránh lỗi Camoufox Windows)
4. `camoufox fetch` (tải browser)
5. Tạo `.proxyxoay.json` từ example nếu chưa có

### Điền proxy

Sửa `C:\tts-api\.proxyxoay.json` (file này **không** commit lên git):

```json
{
  "api_key": "YOUR_KEY",
  "username": "USER",
  "password": "PASS",
  "host": "vipvn7.proxyxoay.net",
  "http_port": 8978
}
```

---

## 2) Chạy

### Nhanh — double-click hoặc CMD

```bat
run_loop.bat
rem count workers hsw:
run_loop.bat 20 6 3
```

### 1 lần (1 câu)

```bat
run_once.bat "Xin chao the gioi"
```

### Đầy đủ (PowerShell / CMD)

```bat
.\.venv\Scripts\python.exe -u fast_tts_loop.py ^
  --count 100 ^
  --workers 6 ^
  --hsw-workers 3 ^
  --token-target 6 ^
  --outdir tts_loop_out ^
  --text-file long_text.txt ^
  --lang en
```

MP3 nằm trong `tts_loop_out\`.

---

## 3) Cập nhật code từ GitHub

```powershell
cd C:\tts-api
git pull
powershell -ExecutionPolicy Bypass -File .\install_tool.ps1
```

(Chỉ cần reinstall nếu `requirements-tool.txt` đổi.)

---

## 4) Lỗi thường gặp

| Lỗi | Cách xử lý |
|-----|------------|
| `isMobile` / `setDefaultViewport` | Chạy `fix_playwright.bat` hoặc `install_tool.ps1` lại |
| `missing api_key` | Sửa `.proxyxoay.json` |
| `connection refused` proxy | Key hết hạn / host:port sai — check dashboard proxyxoay |
| Camoufox chậm lần đầu | Bình thường — farm warm ~2–5s |
| `image_challenge` | IP bẩn — đợi rotate hoặc đổi gói |

### Fix Playwright thủ công

```bat
.\.venv\Scripts\python.exe -m pip install -U "playwright>=1.48.0,<1.61.0" camoufox tls-client
.\.venv\Scripts\python.exe -m camoufox fetch
```

---

## 5) Kiến trúc nhanh

```
HSW farm (K pages, no-proxy) → mint token (1 token = 1 TTS)
TTS workers (N) → take token → call_tts → mp3
Block IP → rotate proxyxoay → mint lại
```

| Flag | Ý nghĩa | Gợi ý |
|------|---------|--------|
| `--workers` | TTS song song | 4–6 |
| `--hsw-workers` | Page HSW | 3 |
| `--token-target` | Token sẵn (= workers) | 0 = auto |
| `--count` | Số file mp3 | — |

---

## 6) File quan trọng

| File | Vai trò |
|------|---------|
| `fast_tts.py` | HSW farm + token + TTS |
| `fast_tts_loop.py` | Multi-worker loop |
| `install_tool.ps1` | Cài Windows |
| `run_loop.bat` | Chạy loop |
| `run_once.bat` | 1 câu |
| `requirements-tool.txt` | Python deps |
| `.proxyxoay.json` | Secret local (gitignore) |

# Quick start (private repo)

## Windows Server — 3 bước

### 1) Cài tool (1 lần)

```powershell
winget install Git.Git GitHub.cli Python.Python.3.12 Cloudflare.cloudflared
```

### 2) Login GitHub (1 lần — repo **private**)

```powershell
gh auth login
# GitHub.com → HTTPS → Login with browser
```

### 3) Cài + chạy API

```powershell
# Cách A — clone rồi bootstrap
gh repo clone huypv2002/tts-api C:\apps\tts-api
cd C:\apps\tts-api
powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

Script sẽ:

- `git pull` nếu đã có repo  
- tạo venv + cài deps + camoufox  
- tạo `.env` (in **admin password** ra màn hình)  
- mở `proxies.json` để dán proxy  
- chạy API port **8787**

### Tunnel (terminal 2)

Copy file từ Mac (1 lần):

```text
~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
→ C:\Users\<you>\.cloudflared\
```

```powershell
# C:\apps\tts-api\tts-api\cloudflared-config.yml
@"
tunnel: 09b4bc94-43c3-4b4a-919b-16d680f927fd
credentials-file: C:\Users\$env:USERNAME\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json
ingress:
  - hostname: tts-origin.liveyt.pro
    service: http://127.0.0.1:8787
  - service: http_status:404
"@ | Set-Content C:\apps\tts-api\tts-api\cloudflared-config.yml

cloudflared tunnel --config C:\apps\tts-api\tts-api\cloudflared-config.yml run 09b4bc94-43c3-4b4a-919b-16d680f927fd
```

---

## Vì sao private vẫn clone nhanh?

| Cách | Ghi chú |
|------|---------|
| **`gh repo clone`** | Dùng token sau `gh auth login` — **không** cần dán PAT tay |
| `git clone https://...` | Hỏi user/pass → dùng PAT, lâu hơn |
| SSH deploy key | Setup lâu, chỉ cần nếu CI |

Repo: https://github.com/huypv2002/tts-api (private)

---

## URL

| | |
|---|---|
| Admin | https://tts-origin.liveyt.pro/admin/ |
| Health | https://tts-origin.liveyt.pro/v1/health |
| API | `POST /v1/tts` + header `X-API-Key` |

---

## Update sau này (10 giây)

```powershell
cd C:\apps\tts-api
git pull
cd tts-api
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -q
# restart uvicorn
```

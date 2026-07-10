# Deploy checklist (GitHub + Cloudflare + Windows US)

## Already done (local / Cloudflare account)

| Item | Value |
|------|--------|
| GitHub (private) | https://github.com/huypv2002/tts-api |
| Tunnel name | `tts-api` |
| Tunnel UUID | `09b4bc94-43c3-4b4a-919b-16d680f927fd` |
| Public hostname | `https://tts-origin.liveyt.pro` |
| Local port | `8787` |

Local Mac currently runs API + tunnel for testing. For production, run the same on **Windows Server US**.

---

## Windows Server (US) — clone & run

```powershell
# Git + Python 3.11+ installed
cd C:\apps
git clone https://github.com/huypv2002/tts-api.git
cd tts-api\tts-api
# repo root is monorepo: fast_tts.py at parent of tts-api/
# structure after clone:
#   C:\apps\tts-api\fast_tts.py
#   C:\apps\tts-api\tts-api\...

cd C:\apps\tts-api\tts-api
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1

# secrets
copy .env.example .env
notepad .env
# TTS_ADMIN_PASSWORD=<strong>
# TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
# TTS_PORT=8787

copy config\proxies.example.json config\proxies.json
notepad config\proxies.json
# paste proxyxoay accounts (5-10)

.\.venv\Scripts\Activate.ps1
pip install camoufox tls-client
camoufox fetch

powershell -ExecutionPolicy Bypass -File .\scripts\run_server.ps1
```

### Cloudflare Tunnel on Windows

Copy from your Mac (securely — USB/scp, not git):

```text
~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
~/.cloudflared/cert.pem   (optional, for management)
```

On Windows, write `C:\apps\tts-api\tts-api\cloudflared-config.yml`:

```yaml
tunnel: 09b4bc94-43c3-4b4a-919b-16d680f927fd
credentials-file: C:\Users\Administrator\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json

ingress:
  - hostname: tts-origin.liveyt.pro
    service: http://127.0.0.1:8787
  - service: http_status:404
```

```powershell
# stop tunnel on Mac first (only one connector needed ideally)
cloudflared tunnel --config C:\apps\tts-api\tts-api\cloudflared-config.yml run 09b4bc94-43c3-4b4a-919b-16d680f927fd
```

Or install as service:

```powershell
cloudflared service install --config C:\apps\tts-api\tts-api\cloudflared-config.yml
```

---

## Endpoints

| URL | Use |
|-----|-----|
| https://tts-origin.liveyt.pro/admin/ | Admin dashboard |
| https://tts-origin.liveyt.pro/v1/health | Health |
| https://tts-origin.liveyt.pro/v1/tts | Create TTS (API key) |

### Admin

- Password: set in `.env` (`TTS_ADMIN_PASSWORD`)
- Create customer API keys in GUI → API Keys

### Public API

```bash
curl -X POST https://tts-origin.liveyt.pro/v1/tts \
  -H "X-API-Key: tts_xxxx" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Hello\",\"lang\":\"en\"}"
```

---

## Update from GitHub

```powershell
cd C:\apps\tts-api
git pull
cd tts-api
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# restart uvicorn / NSSM service
```

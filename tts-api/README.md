# TTS API — Multi-tenant + Admin Dashboard + Cloudflare Tunnel

Production-oriented TTS API for **Windows Server (US)** with:

- Public API (`X-API-Key`) — queue jobs, poll status, download MP3  
- **Proxy pool** (5–10 rotating residential lines) — lease per job, rotate only the blocked slot  
- **Admin GUI** — API keys, max chars, quota, usage, proxies, jobs  
- **Cloudflare Tunnel** — public HTTPS endpoint without opening firewall ports  
- Reuses `../fast_tts.py` (HSW + anonymous ElevenLabs pipeline)

```text
Client ──HTTPS──► Cloudflare Tunnel ──► localhost:8787 (FastAPI)
                                           │
                         ┌─────────────────┼─────────────────┐
                         │ workers         │  SQLite + audio │
                         │ lease ProxySlot │  Admin /static  │
                         └─────────────────┴─────────────────┘
```

---

## 1. Repo layout

```text
tts-api/
  server/           FastAPI app, DB, proxy pool, workers
  static/admin/     Admin dashboard (no build step)
  config/           settings + proxies (local, gitignored after copy)
  data/             SQLite + MP3 output
  scripts/          Windows install / run / tunnel
  requirements.txt
  README.md
```

Parent folder must contain **`fast_tts.py`** (this monorepo: `tts-preview/fast_tts.py`).

---

## 2. GitHub workflow (local + US Windows Server)

### Local (Mac/dev)

```bash
cd tts-preview
git init   # if needed
git add tts-api fast_tts.py
git commit -m "Add multi-tenant TTS API"
# create GitHub repo, then:
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

**Do not commit secrets.** Already gitignored:

- `.env`, `config/settings.json`, `config/proxies.json`, `data/db/*`, `data/audio/*`

### Windows Server (US)

```powershell
# install Git + Python 3.11+ first
cd C:\apps
git clone https://github.com/<you>/<repo>.git tts-preview
cd tts-preview\tts-api
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1

# edit secrets
notepad .env
notepad config\proxies.json

# run
powershell -ExecutionPolicy Bypass -File .\scripts\run_server.ps1
```

Update later:

```powershell
cd C:\apps\tts-preview
git pull
cd tts-api
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# restart server process
```

---

## 3. Configure

### `.env`

```env
TTS_ADMIN_PASSWORD=your-strong-admin-password
TTS_ADMIN_SECRET=long-random-secret
TTS_PORT=8787
TTS_PUBLIC_BASE_URL=https://tts.yourdomain.com
```

### `config/proxies.json` (5–10 lines)

```json
{
  "proxies": [
    {
      "id": "px1",
      "label": "EU VIP 1",
      "enabled": true,
      "provider": "proxyxoay_net",
      "api_key": "uuid-key",
      "username": "user",
      "password": "pass",
      "host": "x.x.x.x",
      "port": 8570
    }
  ]
}
```

Or manage proxies in **Admin → Proxies**.

On first boot a bootstrap API key is written to:

```text
data/bootstrap_key.txt
```

---

## 4. Cloudflare Tunnel (endpoint)

1. Create a hostname in Cloudflare DNS (zone you own), e.g. `tts.yourdomain.com`.
2. On Windows Server install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/).
3. Login (browser once):

```powershell
cloudflared tunnel login
```

4. Helper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_tunnel.ps1
cloudflared tunnel route dns tts-api tts.yourdomain.com
cloudflared tunnel --config .\cloudflared-config.yml run tts-api
```

5. Optional Windows service:

```powershell
# Admin PowerShell
.\scripts\install_tunnel_service.ps1
```

6. Set public URL in Admin → Settings → `public_base_url`  
   or `TTS_PUBLIC_BASE_URL=https://tts.yourdomain.com`

Local test without tunnel: open `http://127.0.0.1:8787/admin/`

---

## 5. Admin dashboard

| URL | Purpose |
|-----|---------|
| `/admin/` | GUI login (admin password) |
| Overview | capacity, proxies, recent jobs |
| API Keys | create keys, max chars, daily quota, concurrent |
| Proxies | add 5–10 lines, rotate IP, enable/disable |
| Settings | global defaults, public URL, workers |
| Jobs / Usage | history + daily usage |

Login: password from `.env` / `settings.json` (`TTS_ADMIN_PASSWORD`).

---

## 6. Public API

### Auth

```http
X-API-Key: tts_xxxx
# or
Authorization: Bearer tts_xxxx
```

### Create speech

```bash
curl -X POST https://tts.yourdomain.com/v1/tts \
  -H "X-API-Key: tts_xxxx" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Hello from the API\",\"lang\":\"en\"}"
```

Response:

```json
{
  "id": "job_...",
  "status": "queued",
  "chars": 18,
  "poll_url": "https://tts.yourdomain.com/v1/tts/job_...",
  "audio_url": "https://tts.yourdomain.com/v1/tts/job_.../audio"
}
```

### Poll / download

```bash
curl -H "X-API-Key: tts_xxxx" https://tts.yourdomain.com/v1/tts/job_...
curl -H "X-API-Key: tts_xxxx" -o out.mp3 https://tts.yourdomain.com/v1/tts/job_.../audio
```

### Me / health

```bash
curl -H "X-API-Key: tts_xxxx" https://tts.yourdomain.com/v1/me
curl https://tts.yourdomain.com/v1/health
```

**Limits:** each key has `max_chars` (word-boundary chunk ≤950 default, hard 1000), daily char/job quota, max concurrent jobs.

---

## 7. Architecture (5–10 proxies)

```text
Job queue (SQLite)
    → Worker leases ONE ProxySlot (max inflight per slot, default 3)
    → HSW + TTS via that proxy
    → 401 block → rotate ONLY that slot (cooldown ~4m)
    → other slots keep serving other users
```

| Setting | Meaning |
|---------|---------|
| `inflight_per_proxy` | Parallel jobs per proxy line |
| `worker_count` | Total async workers |
| ready slots × inflight | Peak concurrency |

---

## 8. Run as Windows service (optional)

Use [NSSM](https://nssm.cc/):

```powershell
nssm install TtsApi "C:\apps\tts-preview\tts-api\.venv\Scripts\python.exe" "-m" "uvicorn" "server.main:app" "--host" "0.0.0.0" "--port" "8787"
nssm set TtsApi AppDirectory "C:\apps\tts-preview\tts-api"
nssm set TtsApi AppEnvironmentExtra "PYTHONPATH=C:\apps\tts-preview\tts-api;C:\apps\tts-preview"
nssm start TtsApi
```

---

## 9. Local Mac quick test

```bash
cd tts-api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env admin password
export PYTHONPATH="$(pwd):$(pwd)/.."
python -m uvicorn server.main:app --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787/admin/

---

## 10. Security checklist

- [ ] Strong `TTS_ADMIN_PASSWORD`
- [ ] Never commit `proxies.json` / `.env`
- [ ] Cloudflare Access optional on `/admin` for extra lock
- [ ] Rotate bootstrap API key after first login
- [ ] Keep tunnel credentials only on server

---

## License / note

Internal tool. Anonymous TTS depends on upstream availability and proxy quality.

# TTS Admin Web — Cloudflare Workers + D1 + Assets

**Web admin** (Cloudflare). **Không** phải tool local, **không** phải SQLite Windows.

| Lớp | Công nghệ |
|-----|-----------|
| API | Cloudflare Worker (`src/index.js`) |
| DB | Cloudflare D1 (`schema.sql`) |
| UI | Worker assets (`public/`) |

## Deploy

```bash
cd cloudflare-admin
npm i -g wrangler   # nếu chưa có
wrangler login

# 1) Tạo D1
wrangler d1 create tts-admin-db
# Copy database_id vào wrangler.toml → database_id = "..."

# 2) Schema
wrangler d1 execute tts-admin-db --remote --file=./schema.sql

# 3) Secrets
wrangler secret put ADMIN_PASSWORD
# nhập: 30102002

wrangler secret put API_SECRET
# nhập chuỗi random dài

# 4) Deploy
wrangler deploy
```

Worker URL dạng: `https://tts-admin-web.<subdomain>.workers.dev`

Gắn domain custom (optional) trong Cloudflare dashboard.

## Tính năng UI

- **Accounts**: user/pass, gói ký tự (1M–50M+), max luồng 1–5, gán proxy, api_key sinh sẵn
- **Proxies**: pool proxyxoay
- **Gói ký tự**: CRUD gói triệu ký tự

## Tool local

```bash
cd ../preview_studio
python3 PreviewStudio.py
```

Hoàn toàn độc lập — DB local `accounts.json`, TTS `fast_tts`.

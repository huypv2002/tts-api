# Architecture — 3 lớp TÁCH BIỆT

```
┌─────────────────────────────────────────────────────────────┐
│  1) TOOL LOCAL (máy user / Mac / PC)                        │
│     preview_studio/  +  fast_tts.py                         │
│     - Login local (accounts.json)                           │
│     - Proxyxoay gắn account local                           │
│     - Generate HSW + preview TTS trên máy                   │
│     - KHÔNG gọi tts-api Windows, KHÔNG bắt buộc Cloudflare  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  2) WEB ADMIN (Cloudflare)                                  │
│     cloudflare-admin/  Workers + D1 + static admin          │
│     - Domain admin (Pages / Worker assets)                  │
│     - Database: Cloudflare D1                               │
│     - Accounts, gói ký tự, proxy, max luồng                 │
│     - KHÔNG phải SQLite trên Windows                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  3) TTS WORKER SERVER (Windows, optional)                   │
│     tts-api/  uvicorn + camoufox + tunnel                   │
│     - Chỉ chạy job TTS nặng nếu cần scale                   │
│     - Tunnel tts-origin.liveyt.pro (nếu dùng)               │
│     - Không phải nguồn admin DB chính                       │
└─────────────────────────────────────────────────────────────┘
```

## Quy tắc

| Không làm | Phải làm |
|-----------|----------|
| Trộn admin Windows SQLite với tool local | Admin = D1 + Workers |
| Bắt desktop tool gọi endpoint admin Windows | Tool = local only |
| Deploy admin bằng `git pull` Windows | Deploy admin = `wrangler deploy` |

## Deploy web admin (Cloudflare)

```bash
cd cloudflare-admin
npm i
npx wrangler login
npx wrangler d1 create tts-admin-db
# paste database_id vào wrangler.toml
npx wrangler d1 execute tts-admin-db --file=./schema.sql
npx wrangler secret put ADMIN_PASSWORD   # 30102002
npx wrangler secret put API_SECRET
npx wrangler deploy
```

## Tool local

```bash
cd preview_studio
python3 PreviewStudio.py
```

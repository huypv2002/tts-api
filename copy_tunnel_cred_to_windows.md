# Copy Cloudflare Tunnel credential → Windows

File **không** commit git (secret). Cần copy tay 1 lần.

## Trên Mac

File nguồn:
```text
~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
```

### Cách A — USB / AirDrop / chat file
Copy file đó sang Windows.

### Cách B — từ Mac scp (Windows bật OpenSSH)
```bash
scp ~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json luk_sms@IP_WINDOWS:C:/Users/luk_sms/.cloudflared/
```

### Cách C — PowerShell trên Windows (nếu Mac share tạm)
Dán nội dung file vào:
```text
C:\Users\luk_sms\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json
```

## Trên Windows

```bat
mkdir %USERPROFILE%\.cloudflared
REM dat file json vao do, dung ten:
REM 09b4bc94-43c3-4b4a-919b-16d680f927fd.json

cd C:\TTS\tts-api
git pull
start_all.bat
```

**Lưu ý:** Chỉ **1 máy** chạy tunnel cùng lúc. Nếu Mac đang tunnel → tắt Mac trước khi bật Windows.

## Chỉ cần API local (không domain)

```bat
start_api_only.bat
```
→ http://127.0.0.1:8787/admin/

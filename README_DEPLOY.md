# Deploy 1‑Click (Railway / Render / Docker)

## Railway
1. Push code lên GitHub.
2. Railway → New Project → Deploy from GitHub.
3. Thêm biến `BOT_TOKEN` trong Variables.
4. Deploy (service chạy kiểu worker).

## Render
1. New + → Blueprint → nhập repo URL.
2. Render đọc `render.yaml` và tạo Worker.
3. Thêm `BOT_TOKEN` vào Environment → Save → Deploy.

## Docker
```bash
docker build -t tiktok-checker-bot .
docker run -e BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN --name tiktok_checker --restart unless-stopped tiktok-checker-bot
```
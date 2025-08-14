# TikTok Checker Telegram Bot (with billing + VietQR + webhook)

## Install
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_with_bot.txt
```

## Run (local)
```bash
export TELEGRAM_BOT_TOKEN=YOUR_TOKEN
export BOT_DATA_DIR=/tmp/botdata     # writable dir
mkdir -p $BOT_DATA_DIR

# optional
export PRICE_PER_CHECK=200
export PRICE_MODE=per_check
export TRANSFER_CODE_PREFIX=NAP

python bot_telegram.py
```

## Run webhook
```bash
export BOT_WEBHOOK_SECRET=your-very-strong-secret
export TELEGRAM_BOT_TOKEN=YOUR_TOKEN
python wallet_webhook.py
```

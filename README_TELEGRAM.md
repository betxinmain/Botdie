# TikTok Checker + Telegram Bot (billing + VietQR topup)

## Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_with_bot.txt

export TELEGRAM_BOT_TOKEN=YOUR_TOKEN
export ADMIN_CHAT_IDS=123456789
export PRICE_PER_CHECK=200
export PRICE_MODE=per_check          # hoặc per_live
export TRANSFER_CODE_PREFIX=NAP      # trùng với siteValue('transfer_code') của web

# QR cấu hình (chọn 1 trong 2)
export TOPUP_BANK=ACB
export TOPUP_ACCOUNT=123456789
export TOPUP_ACCOUNT_NAME=NGUYEN VAN A
# hoặc
# export TOPUP_QR_TEMPLATE="https://img.vietqr.io/image/ACB-123456789-qr_only.png?amount={amount}&addInfo={addinfo}&accountName=NGUYEN%20VAN%20A"

python bot_telegram.py
```

## Webhook nạp tiền tự động
```bash
export BOT_WEBHOOK_SECRET="your-very-strong-secret"
export TELEGRAM_BOT_TOKEN=YOUR_TOKEN
python wallet_webhook.py
```

Laravel gọi sau khi nạp thành công:
```php
$botUrl  = rtrim(env('TT_CHECKER_BOT_URL'),'/').'/api/credit';
$secret  = env('TT_CHECKER_BOT_SECRET');
$chat_id = (string) $user->telegram_id;
$amount  = (int) $creditAmount;
$ts      = time();
$sig     = hash_hmac('sha256', $chat_id.'|'.$amount.'|'.$ts, $secret);
// POST JSON {'chat_id','amount','ts','sig'}
```
Bot sẽ **cộng tiền** và **gửi tin nhắn số dư mới** cho người dùng ngay trên Telegram.

## Lệnh trong Telegram
- `/bind <user_id>`: liên kết user_id (để tạo nội dung CK chuẩn)
- `/topup [amount]`: tạo QR VietQR (Nội dung CK = `TRANSFER_CODE_PREFIX + user_id`)
- `/balance`, `/me`
- `/check <username>`
- Dán nhiều dòng username hoặc gửi file `.txt` để kiểm tra hàng loạt
- Admin: `/credit <chat_id> <amount>`, `/setprice <amount> <per_check|per_live>`

# TikTok Checker + Telegram Bot

## Cách chạy nhanh (local/server)
```bash
python -V         # >= 3.10
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements_with_bot.txt

export TELEGRAM_BOT_TOKEN=YOUR_TOKEN_HERE
# tùy chọn: chỉ cho phép chat id này dùng bot (có thể nhiều id, ngăn cách dấu phẩy)
export ALLOWED_CHAT_ID=123456789

python bot_telegram.py
```

## Dùng lệnh
- `/check @username` hoặc `/check username` → trả kết quả LIVE/BANNED/ERROR.
- Dán **nhiều dòng** username → bot tự hiểu là **batch**.
- Gửi **file .txt** (mỗi dòng 1 username) → bot xử lý song song (tối đa 5 luồng) và gửi lại `live.txt`, `banned.txt`, `error.txt`.

> Logic phân loại tái sử dụng từ `check.py` và endpoint `https://www.tiktok.com/@{username}` với các header trình duyệt.

# -*- coding: utf-8 -*-
"""
Telegram bot tích hợp kiểm tra TikTok live/banned dựa trên check.py
Yêu cầu:
  - TELEGRAM_BOT_TOKEN: token bot
  - (tùy chọn) ALLOWED_CHAT_ID: chỉ cho phép chat id này dùng bot (số, có thể nhiều id cách nhau bằng dấu phẩy)
Lệnh hỗ trợ:
  /start, /help
  /check <username hoặc @username>
  Gửi file .txt (mỗi dòng 1 username) để chạy batch
"""

import os
import io
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
import requests

# Tái dùng logic từ check.py
try:
    from check import classify, TIKTOK_ENDPOINT, HEADERS  # type: ignore
except Exception:
    # fallback phòng trường hợp file đổi tên
    TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
    }
    def classify(username: str, status: int, text: str) -> str:
        if status == 200:
            if f'"uniqueId":"{username}"' in text or f"/@{username}" in text:
                return "live"
            return "banned"
        if status in (404, 451):
            return "banned"
        if status == 429:
            return "error"
        return "error"

from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from usage_limit import allowed as usage_allowed, inc_use, remaining, FREE_USES
PURCHASE_MSG = os.getenv("PURCHASE_MSG", "Bạn đã sử dụng hết {free} lượt miễn phí. Mua tool tại MuaTuongTac.Com để dùng thêm.")


TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED = [x.strip() for x in os.getenv("ALLOWED_CHAT_ID", "").split(",") if x.strip().isdigit()]
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
MAX_WORKERS = min(MAX_WORKERS, 5)  # giữ an toàn tránh 429


async def _gate_or_count(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    if not usage_allowed(chat_id):
        msg = PURCHASE_MSG.format(free=FREE_USES)
        try:
            awPhí\n"
        "• Gửi file .txt (mỗi dòng 1 username) để kiểm tra hàng loạt\n"
        "• Giới hạn song song: tối đa 5 để tránh 429\n"
        "• Lưu Ý Chỉ Cung Cấp User Để Chúng Tôi Check, Tránh Ảnh Hưởng Tới Khi Bị Mất Thông Tin\n"
      "Hệ Thống Cung Cấp Clone Tiktok - Gmail EDu - Dịch Vụ MXH 24/7 (Tham Khảo)\n\n"
      
    )
    await update.message.reply_text(msg)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    r = remaining(chat_id)
    await update.message.reply_text(f"Bạn còn {r} / {FREE_USES} lượt miễn phí.")

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate_or_count(update):
        return
    if not _is_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Cú pháp: /check <username hoặc @username>")
        return
    username = normalize_username(" ".join(context.args))
    if not username:
        await update.message.reply_text("Username không hợp lệ.")
        return
    await update.message.chat.send_action("typing")
    # chạy trong thread tránh block
    loop = asyncio.get_running_loop()
    def _run():
        with requests.Session() as s:
            return quick_check(username, s)
    res = await loop.run_in_executor(None, _run)
    badge = "✅ LIVE" if res == "live" else "❌ BANNED" if res == "banned" else "⚠️ ERROR"
    await update.message.reply_text(f"{badge} — @{username}\nhttps://www.tiktok.com/@{username}")

async def handle_text_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate_or_count(update):
        return
    """Cho phép người dùng dán nhiều username mỗi dòng."""
    if not _is_allowed(update):
        return
    text = update.message.text or ""
    lines = [normalize_username(x) for x in text.splitlines()]
    lines = [x for x in lines if x]
    if len(lines) < 2:
        return  # để dành cho /check handler/khác
    await update.message.chat.send_action("typing")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: batch_check(lines))
    total = sum(len(v) for v in res.values())
    summary = (
        f"🔎 Đã kiểm tra {total} username:\n"
        f"  ✅ Tài Khoản Có Thể Sử Dụng: {len(res['live'])}\n"
        f"  ❌ Tài Khoản Bị Khóa: {len(res['banned'])}\n"
        f"  ⚠️ ERROR: {len(res['error'])}\n"
    )
    await update.message.reply_text(summary)
    # gửi file kết quả (nếu có)
    for key in ("live", "banned", "error"):
        lst = res.get(key, [])
        if lst:
            bio = io.BytesIO(("\n".join(lst)).encode("utf-8"))
            bio.name = f"{key}.txt"
            await update.message.reply_document(document=InputFile(bio))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _gate_or_count(update):
        return
    if not _is_allowed(update):
        return
    doc = update.message.document
    if not doc:
        return
    # chỉ chấp nhận text/plain hoặc .txt
    fname = (doc.file_name or "").lower()
    if doc.mime_type not in ("text/plain", None) and not fname.endswith(".txt"):
        await update.message.reply_text("Vui lòng gửi file .txt (mỗi dòng 1 username).")
        return
    await update.message.chat.send_action("upload_document")
    f = await context.bot.get_file(doc.file_id)
    bio = io.BytesIO()
    await f.download_to_memory(out= bio)
    bio.seek(0)
    try:
        content = bio.read().decode("utf-8", errors="ignore")
    except Exception:
        await update.message.reply_text("Không đọc được nội dung file.")
        return
    usernames = [normalize_username(x) for x in content.splitlines() if x.strip()]
    if not usernames:
        await update.message.reply_text("File rỗng hoặc không có username hợp lệ.")
        return
    await update.message.reply_text(f"⏳ Đang kiểm tra {len(usernames)} username...")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: batch_check(usernames))
    total = sum(len(v) for v in res.values())
    summary = (
        f"🔎 Xong! Đã kiểm tra {total} username:\n"
        f"  ✅ Tài Khoản Có Thể Sử Dụng: {len(res['live'])}\n"
        f"  ❌ Tài Khoản Bị Khóa: {len(res['banned'])}\n"
        f"  ⚠️ ERROR: {len(res['error'])}\n"
    )
    await update.message.reply_text(summary)
    # gửi file kết quả (nếu có)
    for key in ("live", "banned", "error"):
        lst = res.get(key, [])
        if lst:
            b = io.BytesIO(("\n".join(lst)).encode("utf-8"))
            b.name = f"{key}.txt"
            await update.message.reply_document(document=InputFile(b))

def main():
    if not TOKEN:
        raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN trong biến môi trường.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("uses", cmd_uses))
    app.add_handler(CommandHandler("check", cmd_check))
    # text batch (>=2 dòng)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_batch))
    # file .txt
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    print("🤖 Bot đang chạy. Nhấn Ctrl+C để thoát.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

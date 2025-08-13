# -*- coding: utf-8 -*-
import os
import io
import asyncio
from typing import List
from pathlib import Path

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

from checker import check_many, LIVE, DIE, ERROR

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

def _read_env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")

def get_config():
    return {
        "token": os.getenv("BOT_TOKEN", "").strip(),
        "allowed_ids": {int(i) for i in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if i.isdigit()},
        "max_bulk": int(float(os.getenv("MAX_BULK", "200"))),
        "timeout": float(os.getenv("TIMEOUT", "10.0")),
        "threads": int(float(os.getenv("THREADS", "5"))),
    }

def check_auth(update: Update, cfg) -> bool:
    if not cfg["allowed_ids"]:
        return True
    uid = update.effective_user.id if update.effective_user else None
    return uid in cfg["allowed_ids"]

HELP_TEXT = (
    "🤖 *TikTok Checker Bot*\n\n"
    "• /check `<username ...>` — kiểm tra 1 hoặc nhiều username (cách nhau bởi khoảng trắng)\n"
    "• Gửi *file .txt* chứa danh sách username (mỗi dòng 1 username) để kiểm tra hàng loạt.\n"
    "• /help — hướng dẫn sử dụng\n\n"
    "_Gợi ý_: Nên giới hạn mỗi lượt <= 200 username để tránh bị chặn tạm thời (429)."
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

def chunk_list(lst: List[str], size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

async def _respond_with_results(update: Update, results: dict):
    live_list = results.get(LIVE, [])
    die_list = results.get(DIE, [])
    err_list = results.get(ERROR, [])

    # Prepare in-memory files
    files = []
    if live_list:
        files.append(("live.txt", "\n".join(live_list)))
    if die_list:
        files.append(("banned.txt", "\n".join(die_list)))
    if err_list:
        files.append(("errors.txt", "\n".join(err_list)))

    # Summary message
    summary = (
        f"✅ *Xong!*\n\n"
        f"• Live: *{len(live_list)}*\n"
        f"• Die/Banned: *{len(die_list)}*\n"
        f"• Lỗi: *{len(err_list)}*"
    )
    await update.message.reply_text(summary, parse_mode="Markdown")

    # Send files as documents
    for name, text in files:
        bio = io.BytesIO(text.encode("utf-8"))
        bio.name = name
        await update.message.reply_document(document=InputFile(bio))

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = get_config()
    if not check_auth(update, cfg):
        return await update.message.reply_text("⛔️ Bạn không có quyền dùng bot này.")
    if not cfg["token"]:
        return await update.message.reply_text("⚠️ BOT_TOKEN chưa được cấu hình.")
    usernames = []
    for arg in context.args:
        # split by comma or whitespace
        usernames += [x for x in arg.replace(",", " ").split() if x.strip()]
    if not usernames:
        return await update.message.reply_text("Cách dùng: /check username1 username2 ...")
    if len(usernames) > cfg["max_bulk"]:
        usernames = usernames[:cfg["max_bulk"]]
        await update.message.reply_text(f"⚠️ Đã giới hạn còn {cfg['max_bulk']} username để tránh bị chặn.")
    await update.message.reply_text("🕒 Đang kiểm tra, vui lòng đợi...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, check_many, usernames, cfg["threads"], cfg["timeout"])
    await _respond_with_results(update, results)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = get_config()
    if not check_auth(update, cfg):
        return await update.message.reply_text("⛔️ Bạn không có quyền dùng bot này.")
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    if not doc.file_name.lower().endswith(".txt"):
        return await update.message.reply_text("Vui lòng gửi file .txt (mỗi dòng 1 username).")
    # download to memory
    f = await doc.get_file()
    byts = await f.download_as_bytearray()
    text = byts.decode("utf-8", errors="ignore")
    usernames = [line.strip() for line in text.splitlines() if line.strip()]
    if not usernames:
        return await update.message.reply_text("File rỗng.")
    if len(usernames) > cfg["max_bulk"]:
        usernames = usernames[:cfg["max_bulk"]]
        await update.message.reply_text(f"⚠️ Đã giới hạn còn {cfg['max_bulk']} username để tránh bị chặn.")
    await update.message.reply_text(f"🕒 Nhận {len(usernames)} username. Đang kiểm tra...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, check_many, usernames, cfg["threads"], cfg["timeout"])
    await _respond_with_results(update, results)

def main():
    cfg = get_config()
    if not cfg["token"]:
        raise SystemExit("BOT_TOKEN chưa cấu hình.")
    app = ApplicationBuilder().token(cfg["token"]).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    print("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

import os
import io
import asyncio
from typing import List
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from checker import check_usernames

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

HELP_TEXT = (
    "🤖 TikTok Live/Die Checker\n"
    "• /start — thông tin bot\n"
    "• /help — hướng dẫn\n"
    "• /check <username...> — kiểm nhanh 1 hoặc nhiều username (cách nhau bởi khoảng trắng). Ví dụ:\n"
    "  /check vuthanh_99 tiktok @sontungmtp\n"
    "• Gửi file .txt chứa danh sách username (mỗi dòng 1 username) để kiểm hàng loạt.\n"
    "Kết quả gồm 3 nhóm: live, banned, error."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Chào bạn! Gửi lệnh /help để xem hướng dẫn.\n"
        "Bạn có thể /check ngay hoặc upload file .txt (mỗi dòng 1 username)."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

def _chunk_list(lst: List[str], size: int = 1000):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

async def _send_results(update: Update, buckets, filename_prefix="results"):
    live, banned, error = buckets.get("live", []), buckets.get("banned", []), buckets.get("error", [])
    summary = (
        f"✅ Kết quả:\n"
        f"• Live: {len(live)}\n"
        f"• Banned: {len(banned)}\n"
        f"• Error: {len(error)}"
    )
    await update.message.reply_text(summary)

    files = []
    for name, data in [("live", live), ("banned", banned), ("error", error)]:
        if data:
            buf = io.StringIO("\\n".join(data))
            files.append(InputFile(
                io.BytesIO(buf.getvalue().encode("utf-8")), filename=f"{filename_prefix}_{name}.txt"
            ))

    for f in files:
        await update.message.reply_document(document=f)

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập ít nhất 1 username. Ví dụ: /check tiktok @sontungmtp")
        return
    usernames = context.args
    await update.message.reply_text("⏳ Đang kiểm tra, vui lòng đợi...")
    buckets = await asyncio.to_thread(check_usernames, usernames, 5, 10.0)
    await _send_results(update, buckets, filename_prefix="check")

async def handle_text_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        return
    await update.message.reply_text("📥 Đang tải file và xử lý...")
    file = await doc.get_file()
    b = await file.download_as_bytearray()
    content = b.decode("utf-8", errors="ignore")
    usernames = [line.strip() for line in content.splitlines() if line.strip()]
    if not usernames:
        await update.message.reply_text("File trống hoặc không hợp lệ.")
        return
    buckets = await asyncio.to_thread(check_usernames, usernames, 5, 10.0)
    await _send_results(update, buckets, filename_prefix=os.path.splitext(doc.file_name)[0])

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Chưa cấu hình BOT_TOKEN trong biến môi trường.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_text_file))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

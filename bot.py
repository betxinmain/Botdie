import os, io, asyncio
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from checker import check_usernames, debug_username

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

HELP_TEXT = (
    "🤖 TikTok Live/Die Checker\n"
    "/check <username...> — kiểm nhanh.\n"
    "/debug <username> — xem đường quyết định.\n"
    "Gửi file .txt (mỗi dòng 1 username) để kiểm hàng loạt."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Chào bạn! Dùng /help để xem hướng dẫn. Bạn có thể /check hoặc gửi file .txt.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def _send_results(update: Update, buckets, filename_prefix="results"):
    live, banned, error = buckets.get("live", []), buckets.get("banned", []), buckets.get("error", [])
    await update.message.reply_text(f"✅ Kết quả:\n• Live: {len(live)}\n• Banned: {len(banned)}\n• Error: {len(error)}")
    for name, data in [("live", live), ("banned", banned), ("error", error)]:
        if data:
            buf = io.StringIO("\n".join(data))
            await update.message.reply_document(InputFile(io.BytesIO(buf.getvalue().encode("utf-8")), filename=f"{filename_prefix}_{name}.txt"))

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /check <username...>")
        return
    await update.message.reply_text("⏳ Đang kiểm tra, vui lòng đợi...")
    buckets = await asyncio.to_thread(check_usernames, context.args, 5, 10.0)
    await _send_results(update, buckets, filename_prefix="check")

async def handle_text_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".txt"):
        return
    await update.message.reply_text("📥 Đang tải file và xử lý...")
    f = await doc.get_file()
    b = await f.download_as_bytearray()
    content = b.decode("utf-8", errors="ignore")
    usernames = [line.strip() for line in content.splitlines() if line.strip()]
    if not usernames:
        await update.message.reply_text("File trống hoặc không hợp lệ.")
        return
    buckets = await asyncio.to_thread(check_usernames, usernames, 5, 10.0)
    await _send_results(update, buckets, filename_prefix=os.path.splitext(doc.file_name)[0])

async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /debug <username>")
        return
    await update.message.reply_text("🧪 Debug đang chạy...")
    name, status, dbg = await asyncio.to_thread(lambda: debug_username(context.args[0]))
    lines = [f"Kết luận: {status}"]
    for kind, url, code, extra in dbg:
        lines.append(f"{kind} {url} -> {code} {extra}")
    text = "\n".join(lines)
    if len(text) > 3500: text = text[:3500] + "\n..."
    await update.message.reply_text(text)

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Chưa cấu hình BOT_TOKEN.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_text_file))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

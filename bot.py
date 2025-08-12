import os
import io
import json
import asyncio
from typing import List
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from checker_precise import check_usernames, check_one

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

HELP_TEXT = (
    "🤖 TikTok Live/Die Checker (precise)\n"
    "• /start — thông tin bot\n"
    "• /help — hướng dẫn\n"
    "• /check <username...> — kiểm nhanh 1 hoặc nhiều username. Ví dụ:\n"
    "  /check vuthanh_99 tiktok @sontungmtp\n"
    "• Gửi file .txt (mỗi dòng 1 username) để kiểm hàng loạt.\n"
    "• /debug <username> — hiển thị đường quyết định chi tiết."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Chào bạn! Dùng /help để xem hướng dẫn. Bạn có thể /check hoặc gửi file .txt."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def _send_results(update: Update, buckets, filename_prefix="results"):
    live, banned, error = buckets.get("live", []), buckets.get("banned", []), buckets.get("error", [])
    summary = (
        f"✅ Kết quả:\n"
        f"• Live: {len(live)}\n"
        f"• Banned: {len(banned)}\n"
        f"• Error: {len(error)}"
    )
    await update.message.reply_text(summary)
    for name, data in [("live", live), ("banned", banned), ("error", error)]:
        if data:
            buf = io.StringIO("\\n".join(sorted(set(data))))
            await update.message.reply_document(
                document=InputFile(io.BytesIO(buf.getvalue().encode("utf-8")), filename=f"{filename_prefix}_{name}.txt")
            )

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập ít nhất 1 username. Ví dụ: /check tiktok @sontungmtp")
        return
    usernames = context.args
    await update.message.reply_text("⏳ Đang kiểm tra, vui lòng đợi...")
    buckets = await asyncio.to_thread(check_usernames, usernames, 5, 12.0)
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
    buckets = await asyncio.to_thread(check_usernames, usernames, 5, 12.0)
    await _send_results(update, buckets, filename_prefix=os.path.splitext(doc.file_name)[0])

async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Dùng: /debug <username>")
        return
    username = context.args[0]
    await update.message.reply_text("🔍 Debug đang chạy...")
    u, status, meta = await asyncio.to_thread(check_one, username, 12.0, True)
    # Compact debug output
    lines = [f"Kết luận: {status}"]
    for st in meta.get("steps", []):
        if st.get("kind") == "api":
            lines.append(f"api {st.get('url')} -> {st.get('code')} {'json' if st.get('has_json') else st.get('err')}")
        else:
            lines.append(f"html {st.get('html_url')} -> {st.get('status_code')} {st.get('reason')}")
    txt = "\\n".join(lines)
    # Telegram message length limit handling
    if len(txt) > 3800:
        txt = txt[:3800] + "..."
    await update.message.reply_text(txt)

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Chưa cấu hình BOT_TOKEN trong biến môi trường.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("debug", debug_cmd))
    app.add_handler(MessageHandler(filters.Document.TEXT, handle_text_file))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
import os
import logging
from dotenv import load_dotenv
from telegram import Update, FSInputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from checker import check_many, write_results, LIVE, BANNED, ERROR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("checker-bot")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ALLOWED_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(",", " ").split() if x.isdigit()}

def _allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    u = update.effective_user.id if update.effective_user else None
    return u in ALLOWED_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    await update.message.reply_text(
        "📟 TikTok Checker Bot\n"
        "/check <username ...> – kiểm tra nhanh\n"
        "• Gửi file .txt (mỗi dòng 1 username) để kiểm tra hàng loạt.\n"
        "Kết quả: live.txt, banned.txt, errors.txt"
    )

help_cmd = start

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Dùng: /check username1 username2 ...")
        return
    await update.message.reply_text(f"Đang kiểm tra {len(args)} username...")
    results = check_many(args, threads=5)
    counts = {k: len(v) for k, v in results.items()}
    await update.message.reply_text(f"✅ LIVE: {counts.get(LIVE,0)} | 🔒 BANNED: {counts.get(BANNED,0)} | ⚠️ ERROR: {counts.get(ERROR,0)}")
    paths = write_results(results, "results")
    for p in paths.values():
        if os.path.exists(p):
            await update.message.reply_document(FSInputFile(p), filename=os.path.basename(p))

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    doc = update.message.document
    if not doc:
        return
    if not doc.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Vui lòng gửi file .txt (mỗi dòng 1 username).")
        return
    os.makedirs("results", exist_ok=True)
    path = os.path.join("results", f"upload_{doc.file_unique_id}.txt")
    f = await doc.get_file()
    await f.download_to_drive(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
        names = [line.strip() for line in fp if line.strip()]
    await update.message.reply_text(f"Đang kiểm tra {len(names)} username...")
    results = check_many(names, threads=5)
    counts = {k: len(v) for k, v in results.items()}
    await update.message.reply_text(f"✅ LIVE: {counts.get(LIVE,0)} | 🔒 BANNED: {counts.get(BANNED,0)} | ⚠️ ERROR: {counts.get(ERROR,0)}")
    paths = write_results(results, "results")
    for p in paths.values():
        if os.path.exists(p):
            await update.message.reply_document(FSInputFile(p), filename=os.path.basename(p))

def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, file_handler))
    log.info("Bot started.")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

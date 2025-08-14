# -*- coding: utf-8 -*-
import os, io, re, asyncio, urllib.parse, hmac, hashlib, base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

import requests
from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from billing import ensure_user, get_balance, can_afford, charge, credit
from link_store import bind as link_bind, unbind as link_unbind, get_user_id as link_get_user_id

# TikTok checker fallback
try:
    from check import classify, TIKTOK_ENDPOINT, HEADERS  # type: ignore
except Exception:
    TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    }
    def classify(username: str, status: int, text: str) -> str:
        if status == 200 and (f'/"@{username}"' in text or f'"uniqueId":"{username}"' in text):
            return "live"
        if status in (404, 451): return "banned"
        return "error"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED = [x.strip() for x in os.getenv("ALLOWED_CHAT_ID", "").split(",") if x.strip().isdigit()]
ADMINS = [x.strip() for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip().isdigit()]
PRICE = int(os.getenv("PRICE_PER_CHECK", "200"))
PRICE_MODE = os.getenv("PRICE_MODE", "per_check").lower()  # per_check | per_live
MAX_WORKERS = min(int(os.getenv("MAX_WORKERS", "5")), 5)

# Topup config
TRANSFER_PREFIX = os.getenv("TRANSFER_CODE_PREFIX", "NAP")
TOPUP_QR_TEMPLATE = os.getenv("TOPUP_QR_TEMPLATE", "")
TOPUP_BANK = os.getenv("TOPUP_BANK", "")
TOPUP_ACCOUNT = os.getenv("TOPUP_ACCOUNT", "")
TOPUP_ACCOUNT_NAME = os.getenv("TOPUP_ACCOUNT_NAME", "")
DEFAULT_TOPUP_AMOUNT = int(os.getenv("DEFAULT_TOPUP_AMOUNT", "50000"))

# Deep link
BOT_LINK_SECRET = os.getenv("BOT_LINK_SECRET", "").encode()
WEB_CONFIRM_URL = os.getenv("WEB_CONFIRM_URL", "").strip()

def _is_allowed(update: Update) -> bool:
    if not ALLOWED:
        return True
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return chat_id in ALLOWED

def _is_admin(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return chat_id in ADMINS

def normalize_username(u: str) -> str:
    u = u.strip()
    if u.startswith("@"):
        u = u[1:]
    return re.sub(r"[^a-zA-Z0-9_.]", "", u)

def quick_check(username: str, session: requests.Session, timeout: float = 10.0) -> str:
    url = TIKTOK_ENDPOINT.format(username)
    try:
        r = session.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return classify(username, r.status_code, r.text or "")
    except requests.RequestException:
        return "error"

def batch_check(usernames: List[str], timeout: float = 10.0) -> Dict[str, List[str]]:
    usernames = [normalize_username(u) for u in usernames if u.strip()]
    usernames = [u for u in usernames if u]
    results = {"live": [], "banned": [], "error": []}
    if not usernames: return results
    with requests.Session() as s, ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut2name = {ex.submit(quick_check, u, s, timeout): u for u in usernames}
        for fut in as_completed(fut2name):
            u = fut2name[fut]
            try: res = fut.result()
            except Exception: res = "error"
            results.setdefault(res, []).append(u)
    return results

def _build_qr_url(addinfo: str, amount: int) -> str:
    if TOPUP_QR_TEMPLATE:
        return TOPUP_QR_TEMPLATE.format(amount=amount, addinfo=urllib.parse.quote(addinfo, safe=""))
    if TOPUP_BANK and TOPUP_ACCOUNT and TOPUP_ACCOUNT_NAME:
        accname = urllib.parse.quote(TOPUP_ACCOUNT_NAME, safe="")
        return f"https://img.vietqr.io/image/{TOPUP_BANK}-{TOPUP_ACCOUNT}-qr_only.png?amount={amount}&addInfo={urllib.parse.quote(addinfo, safe='')}&accountName={accname}"
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(addinfo)}"

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)

    # Deep-link: /start <token>.<sig>
    if context.args:
        start_param = context.args[0]
        try:
            token, sig = start_param.split(".", 1)
            raw = base64.urlsafe_b64decode(token + "==")
            want = hmac.new(BOT_LINK_SECRET, raw, hashlib.sha256).hexdigest() if BOT_LINK_SECRET else None
            if want and hmac.compare_digest(want, sig):
                parts = (raw.decode("utf-8")).split("|", 1)
                uid = int(parts[0]) if parts and parts[0].isdigit() else 0
                if WEB_CONFIRM_URL:
                    try:
                        resp = requests.post(WEB_CONFIRM_URL, json={
                            "start": start_param,
                            "chat_id": chat_id
                        }, timeout=8)
                        if resp.ok and uid:
                            link_bind(chat_id, uid)
                            await update.message.reply_text("✅ Đã liên kết Telegram với tài khoản website. Dùng /topup để nạp, /balance để xem số dư.")
                            # continue to normal welcome
                    except Exception:
                        pass
        except Exception:
            pass

    bal = get_balance(chat_id)
    msg = (
        "👋 Xin chào!\n"
        f"Phí: {PRICE:,} VND / username — chế độ: **{PRICE_MODE}**\n"
        f"Số dư hiện tại: **{bal:,} VND**\n\n"
        "• /bind <user_id> — liên kết user id\n"
        "• /topup [amount] — tạo QR nạp tiền VietQR\n"
        "• /check <username>\n"
        "• /balance, /me\n"
    )
    if _is_admin(update):
        msg += "• /credit <chat_id> <amount>\n• /setprice <amount> <per_check|per_live>\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(f"💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ **{PRICE_MODE}**", parse_mode=ParseMode.MARKDOWN)

async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    chat_id = str(update.effective_chat.id)
    try:
        if not context.args:
            await update.message.reply_text("Cú pháp: /bind <user_id>")
            return
        try:
            uid = int(context.args[0])
        except Exception:
            await update.message.reply_text("User ID không hợp lệ.")
            return
        link_bind(chat_id, uid)
        await update.message.reply_text(f"✅ Đã liên kết với user_id = {uid}. Dùng /topup để nhận QR.")
    except Exception as e:
        try:
            await update.message.reply_text("⚠️ Lỗi khi liên kết: " + str(e))
        except Exception:
            pass
        print("/bind error:", e); traceback.print_exc()

async def cmd_unbind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    link_unbind(chat_id)
    await update.message.reply_text("✅ Đã huỷ liên kết với user_id.")

async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(f"👤 Chat ID: {chat_id}\n🔗 user_id(link): {uid or 'chưa liên kết'}\n💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ {PRICE_MODE}")

async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    if not uid:
        await update.message.reply_text("Bạn chưa liên kết user_id. Dùng: /bind <user_id> hoặc bấm 'Kết nối Telegram' trên web.")
        return
    try:
        amount = int(context.args[0]) if context.args else DEFAULT_TOPUP_AMOUNT
        if amount <= 0: amount = DEFAULT_TOPUP_AMOUNT
    except Exception:
        amount = DEFAULT_TOPUP_AMOUNT
    addinfo = f\"{TRANSFER_PREFIX}{uid}\"
    qr_url = _build_qr_url(addinfo, amount)
    caption = (
        "🔌 Nạp tiền via VietQR\\n"
        f"🏦 Ngân hàng: {TOPUP_BANK or '...'}\\n"
        f"👤 Chủ TK: {TOPUP_ACCOUNT_NAME or '...'}\\n"
        f"🔢 STK: {TOPUP_ACCOUNT or '...'}\\n"
        f"💵 Số tiền: {amount:,} VND\\n"
        f"📝 Nội dung CK: <code>{addinfo}</code>\\n\\n"
        "Sau khi chuyển, số dư sẽ cộng tự động. Dùng /balance để kiểm tra."
    )
    try:
        await update.message.reply_photo(photo=qr_url, caption=caption, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(caption + f"\\n\\nQR: {qr_url}", parse_mode=ParseMode.HTML)

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update): return
    global PRICE, PRICE_MODE
    try:
        amount = int(context.args[0])
        mode = context.args[1].lower() if len(context.args) > 1 else PRICE_MODE
        if mode not in ("per_check", "per_live"): raise ValueError
        PRICE, PRICE_MODE = amount, mode
        await update.message.reply_text(f"✅ Đã đặt giá: {PRICE:,} VND — chế độ {PRICE_MODE}")
    except Exception:
        await update.message.reply_text("Cú pháp: /setprice <amount> <per_check|per_live>")

async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update): return
    try:
        target = context.args[0]; amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Cú pháp: /credit <chat_id> <amount>"); return
    ensure_user(target); new_bal = credit(target, amount)
    await update.message.reply_text(f"✅ Đã cộng {amount:,} VND cho {target}. Số dư mới: {new_bal:,} VND.")

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    if not context.args:
        await update.message.reply_text("Cú pháp: /check <username hoặc @username>"); return
    username = normalize_username(" ".join(context.args))
    if not username:
        await update.message.reply_text("Username không hợp lệ."); return
    # Cost handling
    if PRICE_MODE == "per_check":
        cost = PRICE
        if not can_afford(chat_id, cost):
            bal = get_balance(chat_id); await update.message.reply_text(f"❗ Số dư không đủ. Cần ≥ {cost:,} VND. Số dư: {bal:,} VND."); return
        if not charge(chat_id, cost, checks=1):
            await update.message.reply_text("❗ Trừ tiền thất bại."); return
    await update.message.chat.send_action("typing")
    def _run():
        with requests.Session() as s: return quick_check(username, s)
    loop = asyncio.get_running_loop(); res = await loop.run_in_executor(None, _run)
    if PRICE_MODE == "per_live" and res == "live":
        charge(chat_id, PRICE, checks=1)
    bal = get_balance(chat_id)
    badge = "✅ LIVE" if res == "live" else "❌ BANNED" if res == "banned" else "⚠️ ERROR"
    await update.message.reply_text(f"{badge} — @{username}\\nhttps://www.tiktok.com/@{username}\\n\\n💰 Số dư: {bal:,} VND")

def main():
    if not TOKEN: raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start","help"], cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("unbind", cmd_unbind))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("credit", cmd_credit))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), lambda u,c: None))
    print(f"🤖 Bot chạy — PRICE={PRICE} — MODE={PRICE_MODE}")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

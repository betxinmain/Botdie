# -*- coding: utf-8 -*-
"""
Bot Telegram: check TikTok live/banned + tính phí + QR nạp VietQR + liên kết user_id
"""
import os, io, re, asyncio, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

import requests
from telegram import Update, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from billing import ensure_user, get_balance, can_afford, charge, credit
from link_store import bind as link_bind, unbind as link_unbind, get_user_id as link_get_user_id

# ---- logic checker ----
try:
    from check import classify, TIKTOK_ENDPOINT, HEADERS  # type: ignore
except Exception:
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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED = [x.strip() for x in os.getenv("ALLOWED_CHAT_ID", "").split(",") if x.strip().isdigit()]
ADMINS = [x.strip() for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip().isdigit()]
PRICE = int(os.getenv("PRICE_PER_CHECK", "200"))
PRICE_MODE = os.getenv("PRICE_MODE", "per_check").lower()  # per_check | per_live
BOT_LINK_SECRET = os.getenv("BOT_LINK_SECRET", "").encode()
WEB_CONFIRM_URL = os.getenv("WEB_CONFIRM_URL", "").strip()

MAX_WORKERS = min(int(os.getenv("MAX_WORKERS", "5")), 5)

# Topup config
TRANSFER_PREFIX = os.getenv("TRANSFER_CODE_PREFIX", "NAP")
TOPUP_QR_TEMPLATE = os.getenv("TOPUP_QR_TEMPLATE", "")
TOPUP_BANK = os.getenv("TOPUP_BANK", "")
TOPUP_ACCOUNT = os.getenv("TOPUP_ACCOUNT", "")
TOPUP_ACCOUNT_NAME = os.getenv("TOPUP_ACCOUNT_NAME", "")
DEFAULT_TOPUP_AMOUNT = int(os.getenv("DEFAULT_TOPUP_AMOUNT", "50000"))

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
    if not usernames:
        return results
    with requests.Session() as s, ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut2name = {ex.submit(quick_check, u, s, timeout): u for u in usernames}
        for fut in as_completed(fut2name):
            u = fut2name[fut]
            try:
                res = fut.result()
            except Exception:
                res = "error"
            results.setdefault(res, []).append(u)
    return results

def _build_qr_url(addinfo: str, amount: int) -> str:
    if TOPUP_QR_TEMPLATE:
        return TOPUP_QR_TEMPLATE.format(amount=amount, addinfo=urllib.parse.quote(addinfo, safe=""))
    if TOPUP_BANK and TOPUP_ACCOUNT and TOPUP_ACCOUNT_NAME:
        accname = urllib.parse.quote(TOPUP_ACCOUNT_NAME, safe="")
        return f"https://img.vietqr.io/image/{TOPUP_BANK}-{TOPUP_ACCOUNT}-qr_only.png?amount={amount}&addInfo={urllib.parse.quote(addinfo, safe='')}&accountName={accname}"
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(addinfo)}"

# ---------- Commands ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    # Deep-link: /start <token> (1-click link)
    if context.args:
        start_param = context.args[0]
        try:
            token, sig = start_param.split(".", 1)
            import base64, hmac, hashlib
            raw = base64.urlsafe_b64decode(token + "==")
            want = hmac.new(BOT_LINK_SECRET, raw, hashlib.sha256).hexdigest() if BOT_LINK_SECRET else None
            if want and hmac.compare_digest(want, sig):
                # token valid: raw = f"{user_id}|{ts}"
                parts = (raw.decode("utf-8")).split("|", 1)
                uid = int(parts[0]) if parts and parts[0].isdigit() else 0
                # call website to confirm mapping
                if WEB_CONFIRM_URL:
                    try:
                        import requests, time as _time
                        resp = requests.post(WEB_CONFIRM_URL, json={
                            "start": start_param,
                            "chat_id": str(update.effective_chat.id)
                        }, timeout=8)
                        if resp.ok:
                            # lưu local mapping để tạo QR /topup
                            try:
                                from link_store import bind as _bind
                                _bind(str(update.effective_chat.id), uid)
                            except Exception:
                                pass
                            await update.message.reply_text("✅ Đã liên kết Telegram với tài khoản website. Dùng /topup để nạp, /balance để xem số dư.")
                            return
                    except Exception:
                        pass
        except Exception:
            pass
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    bal = get_balance(chat_id)
    msg = (
        "👋 Xin chào!\n"
        f"Phí: {PRICE:,} VND / username — chế độ: **{PRICE_MODE}**\n"
        f"Số dư hiện tại: **{bal:,} VND**\n\n"
        "• /bind <user_id> — liên kết user id (để tạo QR đúng nội dung CK)\n"
        "• /topup [amount] — tạo QR nạp tiền VietQR\n"
        "• /check <username> — kiểm tra 1 tài khoản\n"
        "• Dán nhiều dòng username — kiểm tra hàng loạt\n"
        "• Gửi file .txt — kiểm tra hàng loạt\n"
        "• /balance — xem số dư, /me — thông tin\n"
    )
    if _is_admin(update):
        msg += "• /credit <chat_id> <amount>\n• /setprice <amount> <per_check|per_live>\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(f"💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ **{PRICE_MODE}**", parse_mode=ParseMode.MARKDOWN)

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update): return
    global PRICE, PRICE_MODE
    try:
        amount = int(context.args[0])
        mode = context.args[1].lower() if len(context.args) > 1 else PRICE_MODE
        if mode not in ("per_check", "per_live"):
            raise ValueError
        PRICE = amount
        PRICE_MODE = mode
        await update.message.reply_text(f"✅ Đã đặt giá: {PRICE:,} VND — chế độ {PRICE_MODE}")
    except Exception:
        await update.message.reply_text("Cú pháp: /setprice <amount> <per_check|per_live>")

async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update): return
    try:
        target = context.args[0]
        amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Cú pháp: /credit <chat_id> <amount>")
        return
    ensure_user(target)
    new_bal = credit(target, amount)
    await update.message.reply_text(f"✅ Đã cộng {amount:,} VND cho {target}. Số dư mới: {new_bal:,} VND.")

async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
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

async def cmd_unbind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    link_unbind(chat_id)
    await update.message.reply_text("✅ Đã huỷ liên kết user_id.")

async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(f"👤 Chat ID: {chat_id}\n🔗 user_id(link): {uid or 'chưa liên kết'}\n💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ {PRICE_MODE}")

async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    if not uid:
        await update.message.reply_text("Bạn chưa liên kết user_id. Dùng: /bind <user_id>")
        return
    try:
        amount = int(context.args[0]) if context.args else DEFAULT_TOPUP_AMOUNT
        if amount <= 0: amount = DEFAULT_TOPUP_AMOUNT
    except Exception:
        amount = DEFAULT_TOPUP_AMOUNT
    addinfo = f"{TRANSFER_PREFIX}{uid}"
    qr_url = _build_qr_url(addinfo, amount)
    caption = (
        "🔌 Nạp tiền via VietQR\n"
        f"🏦 Ngân hàng: {TOPUP_BANK or '...'}\n"
        f"👤 Chủ TK: {TOPUP_ACCOUNT_NAME or '...'}\n"
        f"🔢 STK: {TOPUP_ACCOUNT or '...'}\n"
        f"💵 Số tiền: {amount:,} VND\n"
        f"📝 Nội dung CK: <code>{addinfo}</code>\n\n"
        "Sau khi chuyển, số dư sẽ cộng tự động. Dùng /balance để kiểm tra."
    )
    try:
        await update.message.reply_photo(photo=qr_url, caption=caption, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(caption + f"\n\nQR: {qr_url}", parse_mode=ParseMode.HTML)

# ---- Billing helpers ----
def _precheck_cost(chat_id: str, units: int) -> (bool, int):
    if PRICE_MODE == "per_check":
        cost = units * PRICE
        if can_afford(chat_id, cost):
            return True, cost
        return False, cost
    else:
        if can_afford(chat_id, PRICE):
            return True, 0
        return False, 0

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    if not context.args:
        await update.message.reply_text("Cú pháp: /check <username hoặc @username>")
        return
    username = normalize_username(" ".join(context.args))
    if not username:
        await update.message.reply_text("Username không hợp lệ.")
        return
    ok, pre_cost = _precheck_cost(chat_id, 1)
    if not ok:
        bal = get_balance(chat_id)
        await update.message.reply_text(f"❗ Số dư không đủ. Cần ≥ {max(pre_cost, PRICE):,} VND. Số dư: {bal:,} VND.")
        return
    if pre_cost and not charge(chat_id, pre_cost, checks=1):
        await update.message.reply_text("❗ Trừ tiền thất bại, vui lòng thử lại.")
        return
    await update.message.chat.send_action("typing")
    loop = asyncio.get_running_loop()
    def _run():
        with requests.Session() as s:
            return quick_check(username, s)
    res = await loop.run_in_executor(None, _run)
    if PRICE_MODE == "per_live" and res == "live":
        charge(chat_id, PRICE, checks=1)
    bal = get_balance(chat_id)
    badge = "✅ LIVE" if res == "live" else "❌ BANNED" if res == "banned" else "⚠️ ERROR"
    await update.message.reply_text(f"{badge} — @{username}\nhttps://www.tiktok.com/@{username}\n\n💰 Số dư: {bal:,} VND")

async def handle_text_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    text = update.message.text or ""
    usernames = [normalize_username(x) for x in text.splitlines() if x.strip()]
    if len(usernames) < 2: return
    ok, pre_cost = _precheck_cost(chat_id, len(usernames))
    if not ok:
        if PRICE_MODE == "per_check":
            bal = get_balance(chat_id)
            can = bal // PRICE
            if can <= 0:
                await update.message.reply_text(f"❗ Số dư không đủ. Cần ≥ {pre_cost:,} VND. Số dư: {bal:,} VND.")
                return
            usernames = usernames[:can]
            await update.message.reply_text(f"⚠️ Số dư chỉ đủ {can} username. Đang xử lý {can} user đầu.")
            pre_cost = can * PRICE
        else:
            bal = get_balance(chat_id)
            await update.message.reply_text(f"❗ Cần tối thiểu {PRICE:,} VND để chạy chế độ per_live. Số dư: {bal:,} VND.")
            return
    if pre_cost and not charge(chat_id, pre_cost, checks=len(usernames)):
        await update.message.reply_text("❗ Trừ tiền thất bại, vui lòng thử lại.")
        return
    await update.message.chat.send_action("typing")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: batch_check(usernames))
    live_count = len(res.get("live", []))
    if PRICE_MODE == "per_live" and live_count > 0:
        if not charge(chat_id, live_count * PRICE, checks=live_count):
            await update.message.reply_text(f"❗ Thiếu tiền để trừ {live_count*PRICE:,} VND cho {live_count} LIVE.")
    total = sum(len(v) for v in res.values())
    summary = (
        f"🔎 Đã kiểm tra {total} username:\n"
        f"  ✅ LIVE: {len(res['live'])}\n"
        f"  ❌ BANNED: {len(res['banned'])}\n"
        f"  ⚠️ ERROR: {len(res['error'])}\n"
        f"💳 Chế độ: {PRICE_MODE} — Đơn giá: {PRICE:,} VND\n"
        f"💰 Số dư còn: {get_balance(chat_id):,} VND"
    )
    await update.message.reply_text(summary)
    for key in ("live", "banned", "error"):
        lst = res.get(key, [])
        if lst:
            bio = io.BytesIO(("\n".join(lst)).encode("utf-8"))
            bio.name = f"{key}.txt"
            await update.message.reply_document(document=InputFile(bio))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update): return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    doc = update.message.document
    if not doc: return
    fname = (doc.file_name or "").lower()
    if doc.mime_type not in ("text/plain", None) and not fname.endswith(".txt"):
        await update.message.reply_text("Vui lòng gửi file .txt (mỗi dòng 1 username).")
        return
    await update.message.chat.send_action("upload_document")
    f = await context.bot.get_file(doc.file_id)
    bio = io.BytesIO()
    await f.download_to_memory(out=bio)
    try:
        bio.seek(0); content = bio.read().decode("utf-8", errors="ignore")
    except Exception:
        await update.message.reply_text("Không đọc được nội dung file.")
        return
    usernames = [normalize_username(x) for x in content.splitlines() if x.strip()]
    if not usernames:
        await update.message.reply_text("File rỗng hoặc không có username hợp lệ.")
        return
    ok, pre_cost = _precheck_cost(chat_id, len(usernames))
    if not ok:
        if PRICE_MODE == "per_check":
            bal = get_balance(chat_id)
            can = bal // PRICE
            if can <= 0:
                await update.message.reply_text(f"❗ Số dư không đủ. Cần ≥ {pre_cost:,} VND. Số dư: {bal:,} VND.")
                return
            usernames = usernames[:can]
            await update.message.reply_text(f"⚠️ Số dư chỉ đủ {can} username. Đang xử lý {can} user đầu.")
            pre_cost = can * PRICE
        else:
            bal = get_balance(chat_id)
            await update.message.reply_text(f"❗ Cần tối thiểu {PRICE:,} VND để chạy chế độ per_live. Số dư: {bal:,} VND.")
            return
    if pre_cost and not charge(chat_id, pre_cost, checks=len(usernames)):
        await update.message.reply_text("❗ Trừ tiền thất bại, vui lòng thử lại.")
        return
    await update.message.reply_text(f"⏳ Đang kiểm tra {len(usernames)} username...")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: batch_check(usernames))
    live_count = len(res.get("live", []))
    if PRICE_MODE == "per_live" and live_count > 0:
        if not charge(chat_id, live_count * PRICE, checks=live_count):
            await update.message.reply_text(f"❗ Thiếu tiền để trừ {live_count*PRICE:,} VND cho {live_count} LIVE.")
    total = sum(len(v) for v in res.values())
    summary = (
        f"🔎 Xong! Đã kiểm tra {total} username:\n"
        f"  ✅ LIVE: {len(res['live'])}\n"
        f"  ❌ BANNED: {len(res['banned'])}\n"
        f"  ⚠️ ERROR: {len(res['error'])}\n"
        f"💳 Chế độ: {PRICE_MODE} — Đơn giá: {PRICE:,} VND\n"
        f"💰 Số dư còn: {get_balance(chat_id):,} VND"
    )
    await update.message.reply_text(summary)
    for key in ("live", "banned", "error"):
        lst = res.get(key, [])
        if lst:
            b = io.BytesIO(("\n".join(lst)).encode("utf-8"))
            b.name = f"{key}.txt"
            await update.message.reply_document(document=InputFile(b))

def main():
    if not TOKEN:
        raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("credit", cmd_credit))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("unbind", cmd_unbind))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_batch))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    print(f"🤖 Bot chạy — PRICE={PRICE} — MODE={PRICE_MODE}")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

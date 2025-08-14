
# -*- coding: utf-8 -*-
import os, re, io, base64, hmac, hashlib, urllib.parse, asyncio, time
from typing import List, Dict

import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters, CallbackQueryHandler
)

# ====== Optional local modules ======
try:
    from billing import ensure_user, get_balance, can_afford, charge, credit
except Exception:
    # minimal in-memory fallback (dev only)
    _MEM = {"users": {}}
    def ensure_user(chat_id: str): _MEM["users"].setdefault(chat_id, {"balance": 0, "checks": 0, "spent": 0})
    def get_balance(chat_id: str) -> int: return int(_MEM["users"].get(chat_id, {}).get("balance", 0))
    def can_afford(chat_id: str, amount: int) -> bool: return get_balance(chat_id) >= int(amount)
    def charge(chat_id: str, amount: int, checks: int = 1) -> bool:
        ensure_user(chat_id); u = _MEM["users"][chat_id]
        if u["balance"] < int(amount): return False
        u["balance"] -= int(amount); u["spent"] += int(amount); u["checks"] += int(checks); return True
    def credit(chat_id: str, amount: int) -> int:
        ensure_user(chat_id); u = _MEM["users"][chat_id]
        u["balance"] += int(amount); return u["balance"]

try:
    from link_store import bind as link_bind, unbind as link_unbind, get_user_id as link_get_user_id
except Exception:
    _LINKS = {}
    def link_bind(chat_id: str, user_id: int): _LINKS[str(chat_id)] = {"user_id": int(user_id)}
    def link_unbind(chat_id: str): _LINKS.pop(str(chat_id), None)
    def link_get_user_id(chat_id: str): return int(_LINKS.get(str(chat_id), {}).get("user_id") or 0)

try:
    from usage_limit import (
        FREE_USES, remaining, inc_use, get_uses,
        limit_for_today, grant_bonus
    )
except Exception:
    FREE_USES = int(os.getenv("FREE_USES", "3"))
    def remaining(chat_id: str) -> int: return max(FREE_USES - get_uses(chat_id), 0)
    _USES = {}
    def _today(): return time.strftime("%Y-%m-%d", time.gmtime())
    def get_uses(chat_id: str) -> int: return _USES.get((_today(), str(chat_id)), 0)
    def inc_use(chat_id: str) -> int:
        k = (_today(), str(chat_id)); _USES[k] = _USES.get(k, 0) + 1; return _USES[k]
    def limit_for_today(chat_id: str) -> int: return FREE_USES
    def grant_bonus(chat_id: str, slots: int) -> int: return 0

# ====== Env config ======
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip()

ALLOWED = [x.strip() for x in os.getenv("ALLOWED_CHAT_ID", "").split(",") if x.strip().isdigit()]
ADMINS  = [x.strip() for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip().isdigit()]

PRICE = int(os.getenv("PRICE_PER_CHECK", "200"))
PRICE_MODE = os.getenv("PRICE_MODE", "per_check").lower()  # per_check | per_live

TRANSFER_PREFIX = os.getenv("TRANSFER_CODE_PREFIX", "NAP")
TOPUP_QR_TEMPLATE = os.getenv("TOPUP_QR_TEMPLATE", "")
TOPUP_BANK = os.getenv("TOPUP_BANK", "")
TOPUP_ACCOUNT = os.getenv("TOPUP_ACCOUNT", "")
TOPUP_ACCOUNT_NAME = os.getenv("TOPUP_ACCOUNT_NAME", "")
DEFAULT_TOPUP_AMOUNT = int(os.getenv("DEFAULT_TOPUP_AMOUNT", "50000"))

BOT_LINK_SECRET = os.getenv("BOT_LINK_SECRET", "").encode()
WEB_CONFIRM_URL = os.getenv("WEB_CONFIRM_URL", "").strip()

# Yeumoney
YEUMONEY_API = os.getenv("YEUMONEY_API", "https://yeumoney.com/QL_api.php")
YEUMONEY_TOKEN = os.getenv("YEUMONEY_TOKEN", "").strip()
YEUMONEY_FORMAT = os.getenv("YEUMONEY_FORMAT", "json")
FREE_BONUS_SLOTS = int(os.getenv("FREE_BONUS_SLOTS", "1"))
FREE_WAIT_SECONDS = int(os.getenv("FREE_WAIT_SECONDS", "15"))

PURCHASE_MSG = os.getenv(
    "PURCHASE_MSG",
    "Bạn đã sử dụng hết {free} lượt miễn phí trong ngày. Nhấn 'Nhận thêm lượt' hoặc mua tool tại MuaTuongTac.Com."
)

# ====== TikTok check (simple) ======
TIKTOK_ENDPOINT = "https://www.tiktok.com/@{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
}
def normalize_username(u: str) -> str:
    u = u.strip()
    if u.startswith("@"): u = u[1:]
    return re.sub(r"[^a-zA-Z0-9_.]", "", u)

def classify(username: str, status: int, text: str) -> str:
    if status == 200 and (f'/"@{username}"' in text or f'"uniqueId":"{username}"' in text):
        return "live"
    if status in (404, 451):
        return "banned"
    return "error"

def quick_check(username: str, timeout: float = 10.0) -> str:
    url = TIKTOK_ENDPOINT.format(username)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return classify(username, r.status_code, r.text or "")
    except requests.RequestException:
        return "error"

# ====== Helpers ======
def _is_allowed(update: Update) -> bool:
    if not ALLOWED: return True
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return chat_id in ALLOWED

def _is_admin(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    return chat_id in ADMINS

def _build_qr_url(addinfo: str, amount: int) -> str:
    if TOPUP_QR_TEMPLATE:
        return TOPUP_QR_TEMPLATE.format(amount=amount, addinfo=urllib.parse.quote(addinfo, safe=""))
    if TOPUP_BANK and TOPUP_ACCOUNT and TOPUP_ACCOUNT_NAME:
        accname = urllib.parse.quote(TOPUP_ACCOUNT_NAME, safe="")
        return (
            f"https://img.vietqr.io/image/{TOPUP_BANK}-{TOPUP_ACCOUNT}-qr_only.png"
            f"?amount={amount}&addInfo={urllib.parse.quote(addinfo, safe='')}&accountName={accname}"
        )
    return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(addinfo)}"

def create_yeumoney_link(chat_id: str) -> str:
    """Return a yeumoney-shortened link that redirects back to Telegram deep link for bonus."""
    payload = f"free-{chat_id}-{int(time.time())}"
    sig = hmac.new(BOT_LINK_SECRET, payload.encode(), hashlib.sha256).hexdigest() if BOT_LINK_SECRET else "0"
    target = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={urllib.parse.quote(payload + '.' + sig)}"
    if not YEUMONEY_TOKEN:
        return target
    try:
        url = f"{YEUMONEY_API}?token={YEUMONEY_TOKEN}&format={YEUMONEY_FORMAT}&url={urllib.parse.quote(target, safe='')}"
        r = requests.get(url, timeout=10)
        # Try JSON first
        try:
            data = r.json()
            for k in ("shortenedUrl","short","short_url","url","result"):
                if isinstance(data, dict) and isinstance(data.get(k), str) and data[k].startswith("http"):
                    return data[k]
        except Exception:
            pass
        if r.text.strip().startswith("http"):
            return r.text.strip()
    except Exception:
        pass
    return target

# ====== Gate: count usage or suggest purchase/free ======
async def _gate_or_count(update: Update) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    cur = get_uses(chat_id)
    limit = limit_for_today(chat_id)
    if cur >= limit:
        msg = PURCHASE_MSG.format(free=FREE_USES)
        try:
            short = create_yeumoney_link(chat_id)
            kb = [
                [InlineKeyboardButton("🔗 Nhận thêm lượt (vượt link)", url=short)],
                [InlineKeyboardButton("🛒 Mua tool tại MuaTuongTac.Com", url="https://MuaTuongTac.Com")]
            ]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.message.reply_text(msg + "\nTruy cập: https://MuaTuongTac.Com")
        return False
    inc_use(chat_id)
    return True

# ====== Commands ======
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)

    # Deep-link: 1) free-<chat>-<ts>.<sig>  2) base64(uid|ts).<sig>
    if context.args:
        start_param = context.args[0]
        try:
            if start_param.startswith("free-"):
                payload, sig = start_param.split(".", 1) if "." in start_param else (start_param, "0")
                want = hmac.new(BOT_LINK_SECRET, payload.encode(), hashlib.sha256).hexdigest() if BOT_LINK_SECRET else None
                if want and hmac.compare_digest(want, sig):
                    parts = payload.split("-", 2)  # ["free", chat, ts]
                    if len(parts) == 3:
                        ts = int(parts[2]) if parts[2].isdigit() else 0
                        if ts and (int(time.time()) - ts) >= max(0, FREE_WAIT_SECONDS // 2):
                            new_extra = grant_bonus(chat_id, FREE_BONUS_SLOTS)
                            await update.message.reply_text(
                                f"✅ Đã cộng +{FREE_BONUS_SLOTS} lượt miễn phí cho hôm nay. "
                                f"Tổng hạn mức: {FREE_USES + new_extra} lượt/ngày."
                            )
            else:
                token, sig = start_param.split(".", 1)
                raw = base64.urlsafe_b64decode(token + "==")
                want = hmac.new(BOT_LINK_SECRET, raw, hashlib.sha256).hexdigest() if BOT_LINK_SECRET else None
                if want and hmac.compare_digest(want, sig):
                    parts = (raw.decode("utf-8")).split("|", 1)
                    uid = int(parts[0]) if parts and parts[0].isdigit() else 0
                    if WEB_CONFIRM_URL:
                        try:
                            resp = requests.post(WEB_CONFIRM_URL, json={"start": start_param, "chat_id": chat_id}, timeout=8)
                            if resp.ok and uid:
                                link_bind(chat_id, uid)
                                await update.message.reply_text(
                                    "✅ Đã liên kết Telegram với tài khoản website. "
                                    "Dùng /topup để nạp, /balance để xem số dư."
                                )
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
        "• /topup [amount] — tạo QR nạp tiền\n"
        "• /check <username>\n"
        "• /balance, /me\n"
        "• /free — nhận thêm lượt (Yeumoney)\n"
        "• /uses — xem lượt miễn phí còn lại hôm nay\n"
    )
    if _is_admin(update):
        msg += (
            "\nQuản trị:\n"
            "• /credit <chat_id> <amount>\n"
            "• /setprice <amount> <per_check|per_live>\n"
        )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(
        f"💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ **{PRICE_MODE}**",
        parse_mode=ParseMode.MARKDOWN
    )

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
    await update.message.reply_text("✅ Đã huỷ liên kết với user_id.")

async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    bal = get_balance(chat_id)
    await update.message.reply_text(
        f"👤 Chat ID: {chat_id}\n🔗 user_id(link): {uid or 'chưa liên kết'}\n"
        f"💰 Số dư: {bal:,} VND\nPhí: {PRICE:,} VND — chế độ {PRICE_MODE}"
    )

async def cmd_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    short = create_yeumoney_link(chat_id)
    txt = (
        "🎁 Nhận **lượt dùng miễn phí** hôm nay.\n"
        f"Mỗi lần vượt link sẽ cộng **+{FREE_BONUS_SLOTS}** lượt vào hạn mức ngày.\n"
        "_Mở link, hoàn thành yêu cầu, hệ thống sẽ tự quay lại Telegram._"
    )
    kb = [[InlineKeyboardButton("🔗 Mở link nhận lượt", url=short)]]
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    uid = link_get_user_id(chat_id)
    if not uid:
        await update.message.reply_text("Bạn chưa liên kết user_id. Dùng: /bind <user_id> hoặc bấm 'Kết nối Telegram' trên web.")
        return
    try:
        amount = int(context.args[0]) if context.args else DEFAULT_TOPUP_AMOUNT
        if amount <= 0:
            amount = DEFAULT_TOPUP_AMOUNT
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

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    global PRICE, PRICE_MODE
    try:
        amount = int(context.args[0])
        mode = context.args[1].lower() if len(context.args) > 1 else PRICE_MODE
        if mode not in ("per_check", "per_live"):
            raise ValueError
        PRICE, PRICE_MODE = amount, mode
        await update.message.reply_text(f"✅ Đã đặt giá: {PRICE:,} VND — chế độ {PRICE_MODE}")
    except Exception:
        await update.message.reply_text("Cú pháp: /setprice <amount> <per_check|per_live>")

async def cmd_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    try:
        target = context.args[0]; amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Cú pháp: /credit <chat_id> <amount>")
        return
    ensure_user(target); new_bal = credit(target, amount)
    await update.message.reply_text(f"✅ Đã cộng {amount:,} VND cho {target}. Số dư mới: {new_bal:,} VND.")

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    chat_id = str(update.effective_chat.id)
    ensure_user(chat_id)
    if not context.args:
        await update.message.reply_text("Cú pháp: /check <username hoặc @username>")
        return
    username = normalize_username(" ".join(context.args))
    if not username:
        await update.message.reply_text("Username không hợp lệ.")
        return
    # usage gate: 3 free/day (+ bonus), rồi mới cho dùng tiếp
    if not await _gate_or_count(update):
        return
    # Pricing
    if PRICE_MODE == "per_check":
        cost = PRICE
        if not can_afford(chat_id, cost):
            bal = get_balance(chat_id)
            await update.message.reply_text(f"❗ Số dư không đủ. Cần ≥ {cost:,} VND. Số dư: {bal:,} VND.")
            return
        if not charge(chat_id, cost, checks=1):
            await update.message.reply_text("❗ Trừ tiền thất bại.")
            return
    # Do check
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, lambda: quick_check(username))
    if PRICE_MODE == "per_live" and res == "live":
        charge(chat_id, PRICE, checks=1)
    bal = get_balance(chat_id)
    badge = "✅ LIVE" if res == "live" else "❌ BANNED" if res == "banned" else "⚠️ ERROR"
    await update.message.reply_text(f"{badge} — @{username}\nhttps://www.tiktok.com/@{username}\n\n💰 Số dư: {bal:,} VND")

async def ignore_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return

def main():
    if not TOKEN:
        raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start","help"], cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("unbind", cmd_unbind))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("free", cmd_free))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("credit", cmd_credit))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(lambda u,c: None, pattern=r"^free:claim$"))  # kept for backward compat, no-op
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), ignore_text))
    print(f"🤖 Bot chạy — PRICE={PRICE} — MODE={PRICE_MODE}")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

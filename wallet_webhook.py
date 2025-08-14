# -*- coding: utf-8 -*-
import os, time, hmac, hashlib, requests
from flask import Flask, request, jsonify
from billing import ensure_user, credit, get_balance

SECRET = os.getenv("BOT_WEBHOOK_SECRET", "").encode()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
app = Flask(__name__)

def chk_sig_credit(chat_id: str, amount: int, ts: int, sig: str) -> bool:
    if not SECRET: return False
    base = f"{chat_id}|{amount}|{ts}".encode()
    want = hmac.new(SECRET, base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig)

@app.post("/api/credit")
def api_credit():
    try: data = request.get_json(force=True)
    except Exception: return jsonify({"ok": False, "error": "invalid_json"}), 400
    chat_id = str(data.get("chat_id","")).strip()
    amount  = int(data.get("amount",0))
    ts      = int(data.get("ts",0))
    sig     = str(data.get("sig",""))
    if not chat_id or amount <= 0 or not ts or not sig:
        return jsonify({"ok": False, "error": "missing"}), 400
    if abs(int(time.time()) - ts) > 600:
        return jsonify({"ok": False, "error": "expired"}), 400
    if not chk_sig_credit(chat_id, amount, ts, sig):
        return jsonify({"ok": False, "error": "bad_sig"}), 403
    ensure_user(chat_id); bal = credit(chat_id, amount)
    try:
        if BOT_TOKEN:
            msg = f"💳 Nạp tiền thành công: +{amount:,} VND\n💰 Số dư mới: {bal:,} VND"
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": msg}, timeout=8)
    except Exception: pass
    return jsonify({"ok": True, "balance": bal})

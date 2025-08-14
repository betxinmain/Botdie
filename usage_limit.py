# -*- coding: utf-8 -*-
import os, json, threading
from datetime import datetime, timedelta

DATA_DIR = os.getenv("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
USAGE_FILE = os.path.join(DATA_DIR, "usage.json")
FREE_USES = int(os.getenv("FREE_USES", "3"))
# Múi giờ cho reset theo ngày: "+07:00" (mặc định VN)
DAILY_RESET_UTC_OFFSET = os.getenv("DAILY_RESET_UTC_OFFSET", "+07:00")

_LOCK = threading.Lock()

def _ensure():
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "w", encoding="utf-8") as f:
            json.dump({"uses": {}}, f, ensure_ascii=False)

def _load():
    _ensure()
    with open(USAGE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {"uses": {}}

def _save(data):
    _ensure()
    tmp = USAGE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USAGE_FILE)

def _today_str():
    # Lấy ngày theo offset UTC chỉ định (VD +07:00 cho VN)
    off = DAILY_RESET_UTC_OFFSET.strip()
    sign = 1
    if off.startswith("-"):
        sign = -1
        off = off[1:]
    elif off.startswith("+"):
        off = off[1:]
    try:
        hh, mm = off.split(":")
        delta = timedelta(hours=int(hh), minutes=int(mm)) * sign
    except Exception:
        # fallback: +07:00
        delta = timedelta(hours=7)
    now = datetime.utcnow() + delta
    return now.strftime("%Y-%m-%d")

def _get_entry(data, chat_id: str):
    uses = data.setdefault("uses", {})
    ent = uses.get(chat_id)
    if not ent or not isinstance(ent, dict):
        ent = {"count": 0, "date": _today_str()}
        uses[chat_id] = ent
    # reset nếu qua ngày mới
    today = _today_str()
    if ent.get("date") != today:
        ent["count"] = 0
        ent["date"] = today
    return ent

def get_uses(chat_id: str) -> int:
    data = _load()
    ent = _get_entry(data, chat_id)
    return int(ent.get("count", 0))

def inc_use(chat_id: str) -> int:
    with _LOCK:
        data = _load()
        ent = _get_entry(data, chat_id)
        ent["count"] = int(ent.get("count", 0)) + 1
        _save(data)
        return ent["count"]

def remaining(chat_id: str) -> int:
    return max(FREE_USES - get_uses(chat_id), 0)

def allowed(chat_id: str) -> bool:
    return get_uses(chat_id) < FREE_USES

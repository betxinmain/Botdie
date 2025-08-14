# -*- coding: utf-8 -*-
import os, json, threading
from typing import Dict

DATA_DIR = os.getenv("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
LEDGER = os.path.join(DATA_DIR, "billing.json")
_LOCK = threading.Lock()

def _ensure_storage():
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(LEDGER):
        with open(LEDGER, "w", encoding="utf-8") as f:
            json.dump({"users": {}}, f, ensure_ascii=False)

def _load() -> Dict:
    _ensure_storage()
    with open(LEDGER, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {"users": {}}

def _save(data: Dict):
    _ensure_storage()
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEDGER)

def ensure_user(chat_id: str):
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        if chat_id not in users:
            users[chat_id] = {"balance": 0, "checks": 0, "spent": 0}
            _save(data)

def get_balance(chat_id: str) -> int:
    with _LOCK:
        data = _load()
        return int(data.get("users", {}).get(chat_id, {}).get("balance", 0))

def can_afford(chat_id: str, amount: int) -> bool:
    return get_balance(chat_id) >= int(amount)

def charge(chat_id: str, amount: int, checks: int = 1) -> bool:
    amount = int(amount)
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        u = users.setdefault(chat_id, {"balance": 0, "checks": 0, "spent": 0})
        if u["balance"] < amount:
            return False
        u["balance"] -= amount
        u["spent"] += amount
        u["checks"] += int(checks)
        _save(data)
        return True

def credit(chat_id: str, amount: int) -> int:
    amount = int(amount)
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        u = users.setdefault(chat_id, {"balance": 0, "checks": 0, "spent": 0})
        u["balance"] += amount
        _save(data)
        return u["balance"]

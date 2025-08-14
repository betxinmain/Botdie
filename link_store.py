# -*- coding: utf-8 -*-
import os, json, threading

DATA_DIR = os.getenv("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
LINK_FILE = os.path.join(DATA_DIR, "link.json")
_LOCK = threading.Lock()

def _ensure():
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(LINK_FILE):
        with open(LINK_FILE, "w", encoding="utf-8") as f:
            json.dump({"links": {}}, f, ensure_ascii=False)

def _load():
    _ensure()
    with open(LINK_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {"links": {}}

def _save(data):
    _ensure()
    tmp = LINK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LINK_FILE)

def bind(chat_id: str, user_id: int):
    with _LOCK:
        data = _load()
        data.setdefault("links", {})[chat_id] = {"user_id": int(user_id)}
        _save(data)

def unbind(chat_id: str):
    with _LOCK:
        data = _load()
        data.setdefault("links", {}).pop(chat_id, None)
        _save(data)

def get_user_id(chat_id: str):
    data = _load()
    return int(data.get("links", {}).get(chat_id, {}).get("user_id") or 0)

import re
import time
import json
import random
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

TIKTOK_WWW = "https://www.tiktok.com/@{username}?lang=en"
TIKTOK_M = "https://m.tiktok.com/@{username}?lang=en"
# Unofficial JSON endpoints (may change). We'll try but fall back to HTML heuristics.
TT_NODE_SHARE = "https://m.tiktok.com/node/share/user/@{username}"
TT_USER_DETAIL = "https://m.tiktok.com/api/user/detail/?uniqueId={username}"

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

NOT_FOUND_PATTERNS = [
    "couldn't find this account",
    "couldnât find this account",
    "khÃ´ng tÃ¬m tháº¥y tÃ i khoáº£n nÃ y",
    "khÃ´ng thá» tÃ¬m tháº¥y tÃ i khoáº£n nÃ y",
    "khÃ´ng tháº¥y tÃ i khoáº£n nÃ y",
    "ãã®ã¢ã«ã¦ã³ãã¯è¦ã¤ããã¾ããã§ãã",
    "no se pudo encontrar esta cuenta",
    "account not found",
    "user not found",
    "page not available",
    "not found",
]

LIVE_HINTS = [
    'property="og:type" content="profile"',
    'name="og:type" content="profile"',
    '"seoProps"',
    '"uniqueId"',
    '"secUid"',
    '"followerCount"',
    '"followingCount"',
]

def _headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
        "Referer": "https://www.tiktok.com/",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    }

def _normalize(u: str) -> str:
    u = u.strip()
    if not u:
        return ""
    # URL forms
    if u.startswith("http"):
        m = re.search(r"/@([A-Za-z0-9._]+)", u)
        if m:
            return m.group(1)
        u = u.split("/")[-1]
    # Leading @
    if u.startswith("@"):
        u = u[1:]
    # Valid chars for TikTok usernames: letters, digits, underscore, dot
    if not re.fullmatch(r"[A-Za-z0-9._]{1,24}", u):
        return ""
    return u

def _classify_from_html(text: str, username: str) -> Optional[str]:
    low = text.lower()
    for pat in NOT_FOUND_PATTERNS:
        if pat in low:
            return "banned"
    # If we see strong live hints and canonical/og url points to the username, assume live
    if any(hint.lower() in low for hint in LIVE_HINTS):
        # Try to confirm canonical/@username
        m = re.search(r'property=["\']og:url["\']\s+content=["\']https?://www\.tiktok\.com/@([^"\'/?#]+)', text, re.I)
        if m:
            # canonical might be case-sensitive; compare lower
            if m.group(1).lower() == username.lower():
                return "live"
        # Also check uniqueId JSON fragment
        m2 = re.search(r'"uniqueId"\s*:\s*"([A-Za-z0-9._]+)"', text)
        if m2 and m2.group(1).lower() == username.lower():
            return "live"
    # If title explicitly says Not found
    if re.search(r"<title>[^<]*not\s+found[^<]*</title>", low):
        return "banned"
    return None  # unknown

def _classify_from_json(js: dict) -> Optional[str]:
    # Try to recognize structures from node/share or api/user/detail
    # node/share: {"userInfo":{"user":{"uniqueId":"..." ...}},"statusCode":0}
    try:
        if "userInfo" in js:
            user = js.get("userInfo", {}).get("user", {})
            if isinstance(user, dict) and user.get("uniqueId"):
                return "live"
        # api/user/detail: {"userInfo": {...}, "user": {...}} or {"statusCode":10221} etc.
        if js.get("user") or js.get("userInfo"):
            return "live"
        # Some endpoints return {"statusCode": 10202, "statusMsg":"user not found"}
        sc = js.get("statusCode")
        if sc and int(sc) != 0:
            # Non-zero often indicates not found; be conservative:
            if str(js).lower().find("not found") != -1:
                return "banned"
    except Exception:
        pass
    return None

def _try_get_json(url: str, session: requests.Session, timeout: float) -> Optional[dict]:
    try:
        r = session.get(url, headers=_headers(), timeout=timeout)
        if r.status_code == 200 and r.headers.get("content-type","").startswith(("application/json","text/json")):
            return r.json()
    except Exception:
        return None
    return None

def _get(url: str, session: requests.Session, timeout: float) -> Tuple[int, str]:
    r = session.get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
    return r.status_code, r.text

def check_one(username: str, timeout: float = 10.0, session: Optional[requests.Session] = None) -> Tuple[str, str]:
    u = _normalize(username)
    if not u:
        return (username, "error")

    s = session or requests.Session()

    # 1) Try unofficial JSON endpoints (these may change)
    for api_url in (TT_NODE_SHARE.format(username=u), TT_USER_DETAIL.format(username=u)):
        js = _try_get_json(api_url, s, timeout)
        if js is not None:
            cls = _classify_from_json(js)
            if cls:
                return (u, cls)
            # If JSON clearly says not found
            if "user" not in js and "userInfo" not in js and "secUid" not in str(js):
                # fall through to HTML, don't conclude "banned" yet
                pass

    # 2) Try mobile HTML first (less JS heavy)
    try:
        code, text = _get(TIKTOK_M.format(username=u), s, timeout)
        if code == 404:
            return (u, "banned")
        if code == 200:
            cls = _classify_from_html(text, u)
            if cls:
                return (u, cls)
    except requests.RequestException:
        pass

    # 3) Try desktop HTML
    try:
        code, text = _get(TIKTOK_WWW.format(username=u), s, timeout)
        if code == 404:
            return (u, "banned")
        if code == 200:
            cls = _classify_from_html(text, u)
            if cls:
                return (u, cls)
        # Some regions may return 302 â /login or a consent page â treat as unknown error
        if code in (401, 403):
            return (u, "error")
    except requests.RequestException:
        return (u, "error")

    return (u, "error")

def check_usernames(usernames: Iterable[str], threads: int = 5, timeout: float = 10.0) -> Dict[str, List[str]]:
    usernames = [u for u in map(_normalize, usernames) if u]
    buckets = {"live": [], "banned": [], "error": []}
    if not usernames:
        return buckets
    threads = max(1, min(threads, 5))
    with requests.Session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(check_one, u, timeout, s): u for u in usernames}
        for f in as_completed(futures):
            u, status = f.result()
            buckets.setdefault(status, []).append(u)
            # jitter to reduce hammering
            time.sleep(random.uniform(0.05, 0.12))
    return buckets

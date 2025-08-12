import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Tuple, Dict, Optional
import requests

TIKTOK_HTML = "https://www.tiktok.com/@{username}"
TIKTOK_M_HTML = "https://m.tiktok.com/@{username}"
API_CANDIDATES = [
    "https://m.tiktok.com/api/user/detail/?uniqueId={username}",
    "https://www.tiktok.com/api/user/detail/?uniqueId={username}",
    "https://m.tiktok.com/node/share/user/@{username}",
]

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

NOT_FOUND_MARKERS = [
    "couldn't find this account",
    "khÃ´ng tÃ¬m tháº¥y tÃ i khoáº£n nÃ y",
    "this account couldn't be found",
    "tÃ i khoáº£n nÃ y khÃ´ng tá»n táº¡i",
    "page not found",
]

def _headers(extra: Optional[dict]=None):
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
        "Referer": "https://www.tiktok.com/",
    }
    if extra:
        h.update(extra)
    return h

def _normalize(u: str) -> str:
    u = u.strip()
    if not u:
        return ""
    if u.startswith("http"):
        m = re.search(r"/@([A-Za-z0-9._]+)", u)
        u = m.group(1) if m else u.split("/")[-1]
    if u.startswith("@"):
        u = u[1:]
    # keep allowed chars only and trim length
    u = re.sub(r"[^A-Za-z0-9._]", "", u)[:24]
    return u

def _try_user_json(session: requests.Session, username: str, timeout: float, debug: Optional[list]=None) -> Optional[dict]:
    for api in API_CANDIDATES:
        url = api.format(username=username)
        try:
            r = session.get(url, headers=_headers({"Accept": "application/json"}), timeout=timeout, allow_redirects=True)
            code = r.status_code
            if debug is not None:
                debug.append(f"api {url} -> {code}")
            if code == 200 and r.headers.get("content-type","").startswith("application/json"):
                data = r.json()
                # different shapes across endpoints
                if isinstance(data, dict):
                    # m.tiktok.com/api/user/detail
                    if "userInfo" in data and isinstance(data["userInfo"], dict):
                        user_info = data["userInfo"].get("user", {}) or data["userInfo"]
                        unique = (user_info.get("uniqueId") or "").lower()
                        if unique:
                            return {"live": True, "data": data}
                    # node/share/user
                    if "userInfo" in data and "user" in data["userInfo"]:
                        unique = (data["userInfo"]["user"].get("uniqueId") or "").lower()
                        if unique:
                            return {"live": True, "data": data}
                    # explicit "not found" code seen commonly
                    status_code = data.get("statusCode") or data.get("status_code")
                    if status_code in (10000, 10221, 10222, 10223):
                        return {"live": False, "not_found": True, "data": data}
                # if JSON not match â continue
            elif code == 404:
                return {"live": False, "not_found": True}
            elif code in (401, 403, 429):
                # throttled; don't call it banned
                return {"live": None, "error": "blocked"}
        except Exception as e:
            if debug is not None:
                debug.append(f"api err {url}: {e}")
            continue
    return None

def _html_live_or_not(text: str, username: str) -> Optional[bool]:
    low = text.lower()
    # strong positive signals
    if f'"uniqueid":"{username.lower()}"' in low:
        return True
    if f"@{username.lower()}" in low and ('property="og:type" content="profile"' in low or 'rel="canonical"' in low):
        return True
    # strong negative
    for m in NOT_FOUND_MARKERS:
        if m in low:
            return False
    return None

def check_one(username: str, timeout: float = 10.0, session: Optional[requests.Session] = None, debug: Optional[list]=None) -> Tuple[str, str]:
    """Return (username, status) where status in {'live','banned','error'}"""
    u = _normalize(username)
    if not u:
        return (username, "error")
    S = session or requests.Session()

    # 1) Try JSON endpoints first
    js = _try_user_json(S, u, timeout, debug=debug)
    if js:
        if js.get("live") is True:
            return (u, "live")
        if js.get("not_found"):
            return (u, "banned")
        if js.get("live") is None:
            # blocked/429
            return (u, "error")

    # 2) Try m.tiktok.com HTML (lighter)
    try:
        r = S.get(TIKTOK_M_HTML.format(username=u), headers=_headers(), timeout=timeout, allow_redirects=True)
        if debug is not None:
            debug.append(f"m.html -> {r.status_code}")
        if r.status_code == 200:
            verdict = _html_live_or_not(r.text, u)
            if verdict is True:
                return (u, "live")
            if verdict is False:
                return (u, "banned")
        elif r.status_code == 404:
            return (u, "banned")
        elif r.status_code in (401, 403, 429):
            return (u, "error")
    except Exception as e:
        if debug is not None:
            debug.append(f"m.html err: {e}")

    # 3) Try www.tiktok.com HTML (as last resort)
    try:
        r = S.get(TIKTOK_HTML.format(username=u), headers=_headers(), timeout=timeout, allow_redirects=True)
        if debug is not None:
            debug.append(f"www.html -> {r.status_code}")
        if r.status_code == 200:
            verdict = _html_live_or_not(r.text, u)
            if verdict is True:
                return (u, "live")
            if verdict is False:
                return (u, "banned")
        elif r.status_code == 404:
            return (u, "banned")
        elif r.status_code in (401, 403, 429):
            return (u, "error")
    except Exception as e:
        if debug is not None:
            debug.append(f"www.html err: {e}")

    # 4) Unable to conclude
    return (u, "error")

def check_usernames(usernames: Iterable[str], threads: int = 5, timeout: float = 10.0) -> Dict[str, List[str]]:
    usernames = [u for u in map(_normalize, usernames) if u]
    buckets = {"live": [], "banned": [], "error": []}
    if not usernames:
        return buckets

    threads = max(1, min(threads, 5))
    with requests.Session() as s, ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(check_one, u, timeout, s, []): u for u in usernames}
        for f in as_completed(futures):
            u, status = f.result()
            buckets.setdefault(status, []).append(u)
            # jitter
            time.sleep(random.uniform(0.05, 0.12))
    return buckets

import re
import json
import time
import random
from typing import Iterable, Tuple, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

def _rand_tt_webid() -> str:
    # random 32-digit webid-like cookie (not exact but good enough to avoid some blocks)
    return "".join(random.choice(string.digits) for _ in range(32))

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Connection": "keep-alive",
    })
    # set a webid cookie to look more browser-like
    s.cookies.set("tt_webid_v2", _rand_tt_webid(), domain=".tiktok.com")
    return s

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,24}$")

def normalize_username(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("http"):
        m = re.search(r"/@([A-Za-z0-9._]+)", u)
        if m:
            return m.group(1)
    if u.startswith("@"):
        u = u[1:]
    # keep only allowed chars
    u = re.sub(r"[^A-Za-z0-9._]", "", u)
    return u

def _try_json(s: requests.Session, url: str, timeout: float) -> Tuple[Optional[dict], Optional[int], Optional[str]]:
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True)
        code = r.status_code
        ctype = r.headers.get("content-type","")
        if "application/json" in ctype:
            return r.json(), code, None
        # sometimes returns text but JSON inside
        txt = r.text.strip()
        if txt.startswith("{") or txt.startswith("["):
            return json.loads(txt), code, None
        return None, code, "non_json"
    except requests.RequestException as e:
        return None, None, f"req_exc:{type(e).__name__}"
    except json.JSONDecodeError as e:
        return None, None, f"json_exc:{type(e).__name__}"

def _extract_sigi_state(html: str) -> Optional[dict]:
    # find <script id="SIGI_STATE" type="application/json"> ... </script>
    m = re.search(r'<script[^>]*id=["\']SIGI_STATE["\'][^>]*>(.*?)</script>', html, re.S | re.I)
    if not m: 
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

def _check_html_profile(s: requests.Session, base: str, username: str, timeout: float) -> Tuple[str, str, Dict]:
    """Return (status, reason, details) where status in {'live','banned','error'}"""
    url = f"{base}/@{username}"
    details = {"html_url": url}
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True)
        details["status_code"] = r.status_code
        if r.status_code in (401,403,429):
            return "error", f"http_{r.status_code}", details
        txt = r.text

        # try SIGI_STATE JSON
        state = _extract_sigi_state(txt)
        if state:
            details["sigi_state"] = True
            # UserModule has two shapes: { users: {username:{...}}, userNotFound: {username: True} }
            um = state.get("UserModule") or {}
            users = um.get("users") or {}
            uobj = users.get(username) or users.get(username.lower())
            if uobj and (uobj.get("secUid") or uobj.get("id")):
                return "live", "sigi_state_user", details
            unf = um.get("userNotFound") or {}
            # sometimes boolean or dict
            if (isinstance(unf, dict) and (unf.get(username) or unf.get(username.lower()))) or unf is True:
                return "banned", "sigi_state_notfound", details
        else:
            details["sigi_state"] = False

        # fallback markers
        low = txt.lower()
        if ("couldn't find this account" in low) or ("không tìm thấy tài khoản" in low) or ("user not found" in low):
            return "banned", "html_not_found_text", details

        # og tags and canonical pointing to the exact username
        if f'@{username.lower()}' in low and ('og:type" content="profile' in low or "og:type\" content=\"profile" in low):
            return "live", "html_og_profile", details

        # otherwise uncertain
        if r.status_code == 404:
            return "banned", "http_404", details
        if r.status_code == 200:
            return "error", "html_uncertain_200", details
        return "error", f"http_{r.status_code}", details
    except requests.RequestException as e:
        details["exc"] = type(e).__name__
        return "error", "req_exc", details

def check_one(username: str, timeout: float = 10.0, debug: bool=False) -> Tuple[str, str, Dict]:
    """Return (username, status, meta)"""
    u = normalize_username(username)
    meta = {"input": username, "username": u, "steps": []}
    if not u:
        meta["err"] = "empty"
        return username, "error", meta

    s = _session()

    # 1) Try JSON endpoints
    json_urls = [
        f"https://m.tiktok.com/api/user/detail/?uniqueId={u}",
        f"https://www.tiktok.com/api/user/detail/?uniqueId={u}",
        f"https://m.tiktok.com/node/share/user/@{u}",
    ]
    for ju in json_urls:
        data, code, err = _try_json(s, ju, timeout)
        meta["steps"].append({"kind":"api","url":ju,"code":code,"err":err,"has_json":data is not None})
        if data:
            # various shapes: {userInfo: {user: {...}}} or {statusCode: ..., userInfo: {...}}
            userInfo = data.get("userInfo") or data.get("user") or data.get("userData")
            if isinstance(userInfo, dict):
                user = userInfo.get("user") if isinstance(userInfo.get("user"), dict) else userInfo
            else:
                user = None
            if isinstance(user, dict) and (user.get("uniqueId") or user.get("secUid") or user.get("id")):
                return u, "live", meta
            # explicit not found codes (observed historically)
            for key in ("statusCode","status"):
                codev = data.get(key)
                if codev in (10000, 10221, 10222, 10223, 10204, 10101):
                    return u, "banned", meta
        # non JSON 200 might be anti-bot, keep trying

    # 2) HTML m.tiktok.com then www
    for base in ("https://m.tiktok.com", "https://www.tiktok.com"):
        status, reason, details = _check_html_profile(s, base, u, timeout)
        meta["steps"].append({"kind":"html","base":base,"reason":reason, **details})
        if status in ("live","banned"):
            return u, status, meta

    # 3) give up as error, let caller retry
    return u, "error", meta

def check_usernames(usernames: Iterable[str], threads: int = 5, timeout: float = 10.0) -> Dict[str, List[str]]:
    buckets = {"live": [], "banned": [], "error": []}
    items = [normalize_username(u) for u in usernames if normalize_username(u)]
    if not items:
        return buckets
    threads = max(1, min(threads, 5))
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(check_one, u, timeout, False): u for u in items}
        for f in as_completed(futs):
            u, status, _ = f.result()
            buckets.setdefault(status, []).append(u)
            time.sleep(random.uniform(0.05, 0.15))
    return buckets
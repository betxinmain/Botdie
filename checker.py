import os
import re
import json
import html
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

LIVE = "live"
BANNED = "banned"
ERROR = "errors"  # keep folder/file name consistent with provided zip

TIKTOK_ENDPOINT = "https://www.tiktok.com/@{username}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.tiktok.com/",
    "Connection": "keep-alive",
}

def _normalize(u: str) -> str:
    u = (u or "").strip()
    if u.startswith("@"):
        u = u[1:]
    # Only characters allowed in TikTok handles
    return re.sub(r"[^A-Za-z0-9._]", "", u)

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS.copy())
    return s

def _extract_sigi_state(html_text: str):
    # Newer TikTok renders JSON in <script id="SIGI_STATE" type="application/json">...</script>
    start_token = '<script id="SIGI_STATE" type="application/json">'
    end_token = '</script>'
    i = html_text.find(start_token)
    if i == -1:
        return None
    i += len(start_token)
    j = html_text.find(end_token, i)
    if j == -1:
        return None
    payload = html_text[i:j].strip()
    try:
        return json.loads(payload)
    except Exception:
        return None

def _looks_like_live(username: str, html_text: str) -> bool:
    if not html_text:
        return False
    js = _extract_sigi_state(html_text)
    if js:
        # Prefer authoritative data from UserModule
        um = js.get("UserModule") or {}
        users = um.get("users") or {}
        # In many pages, the dict is keyed by uniqueId, or userId. Check both.
        for obj in users.values():
            # Some pages structure nested user objects
            user_obj = obj.get("user") if isinstance(obj, dict) and "user" in obj else obj
            if not isinstance(user_obj, dict):
                continue
            uid = (user_obj.get("uniqueId") or "").lower()
            if uid == username.lower():
                sec_uid = user_obj.get("secUid") or ""
                if sec_uid:
                    return True
        # Some variants include "userNotFound": true or specific status codes
        if um.get("userNotFound") is True:
            return False
        if um.get("statusCode") in (10221, 10222):
            return False

    # Heuristics as fallback (non-JSON or blocked locales)
    t = html_text
    if 'property="og:url"' in t and ("https://www.tiktok.com/@" + username.lower()) in t.lower():
        return True
    # Typical tokens appearing on profile pages
    if '"followers"' in t or '"following"' in t or '"videoCount"' in t:
        return True
    if "Followers" in t or "Following" in t:
        return True
    return False

def _looks_like_banned_or_missing(html_text: str) -> bool:
    if not html_text:
        return False
    t = html.unescape(html_text).lower()
    signals = [
        "couldn't find this account",
        "this account is unavailable",
        "account suspended",
        "account banned",
        "page not available",
        "page not found",
        "không thể tìm thấy tài khoản",
        "tài khoản này không tồn tại",
    ]
    return any(sig in t for sig in signals)

def check_username(username: str, session: requests.Session = None, timeout: int = 12) -> Tuple[str, str]:
    u = _normalize(username)
    if not u:
        return username, ERROR

    sess = session or _make_session()
    url = TIKTOK_ENDPOINT.format(username=u)
    try:
        resp = sess.get(url, allow_redirects=True, timeout=timeout)
    except requests.RequestException:
        return u, ERROR

    status = resp.status_code
    text = resp.text or ""
    final = (resp.url or "").lower()

    # 404 is almost certainly non-existing/banned
    if status == 404:
        return u, BANNED
    # Explicit error codes indicate transient/blocked
    if status in (429, 430, 500, 502, 503, 504):
        return u, ERROR
    # Redirect to search or discover often means missing
    if "/search/" in final or "/discover" in final:
        return u, BANNED
    # Content-based checks
    if _looks_like_banned_or_missing(text):
        return u, BANNED
    if _looks_like_live(u, text):
        return u, LIVE

    # If we reach here, we couldn't confidently decide
    return u, ERROR

def check_many(usernames: List[str], threads: int = 5, delay_between: float = 0.0) -> Dict[str, List[str]]:
    names = [x for x in map(_normalize, usernames) if x]
    out = {LIVE: [], BANNED: [], ERROR: []}
    if not names:
        return out
    sess = _make_session()
    with ThreadPoolExecutor(max_workers=max(1, min(threads, 16))) as ex:
        futs = [ex.submit(check_username, n, sess) for n in names]
        for fut in as_completed(futs):
            uname, state = fut.result()
            out[state].append(uname)
    return out

def write_results(results: Dict[str, List[str]], folder: str = "results") -> Dict[str, str]:
    os.makedirs(folder, exist_ok=True)
    mapping = {LIVE: "live.txt", BANNED: "banned.txt", ERROR: "errors.txt"}
    paths = {}
    for key, fname in mapping.items():
        path = os.path.join(folder, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(results.get(key, [])))
        paths[key] = path
    return paths
